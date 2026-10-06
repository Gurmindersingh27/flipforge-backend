import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.db.init_db import init_db
from app.db.models.item_assessment import ItemAssessment, SavedItemAssessment
from app.db.models.item_ai_budget import ItemAIGate, ItemAIMonth
from app.services import item_ai_budget_service as budget


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = "sqlite:///" + os.path.join(self.tmp.name, "budget.db")
        self.engine = create_engine(self.url, connect_args={"timeout": 15})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def reserve(self, request_id=None):
        key = request_id or str(uuid4())
        with self.Session() as db:
            row, fresh = budget.reserve(db, "alice", key, "hash")
            return row.id, fresh

    def test_reservation_is_durable_and_actual_releases_unused_hold(self):
        key, fresh = self.reserve()
        self.assertTrue(fresh)
        # New engine/session represents another process or restart.
        engine = create_engine(self.url)
        try:
            with sessionmaker(bind=engine)() as db:
                self.assertEqual(budget.budget_status(db).reserved, .5)
                with self.assertRaises(HTTPException) as exc:
                    budget.reserve(db, "bob", str(uuid4()), "hash")
                self.assertEqual(exc.exception.status_code, 409)
                budget.settle(db, key, 71000, {}, result={"schema_version": 1})
                status = budget.budget_status(db)
                self.assertEqual(status.reserved, 0)
                self.assertEqual(str(status.spent), "0.071")
                self.assertTrue(status.available)
        finally:
            engine.dispose()

    def test_two_workers_starting_together_allow_exactly_one_run(self):
        barrier = Barrier(8)
        def worker(_):
            with self.Session() as db:
                barrier.wait()
                try:
                    _, fresh = budget.reserve(db, "alice", str(uuid4()), "hash")
                    return fresh
                except HTTPException as exc:
                    self.assertEqual(exc.status_code, 409)
                    return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(worker, range(8))), 1)
        with self.Session() as db:
            self.assertEqual(db.query(ItemAssessment).count(), 1)
            self.assertEqual(db.get(ItemAIMonth, budget.month_key()).held_micros, budget.HOLD)

    def test_same_key_concurrent_calls_create_one_assessment(self):
        key, barrier = str(uuid4()), Barrier(6)
        def worker(_):
            barrier.wait()
            return self.reserve(key)[1]
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(worker, range(6))), 1)

    def test_boundary_fits_exactly_and_warning_includes_hold(self):
        with self.Session() as db:
            db.add(ItemAIMonth(month=budget.month_key(), spent_micros=19_500_000, held_micros=0))
            db.commit()
        key, _ = self.reserve()
        with self.Session() as db:
            status = budget.budget_status(db)
            self.assertTrue(status.warning)
            budget.settle(db, key, 1, {})
            self.assertEqual(budget.budget_status(db).reason, "budget_paused")
            with self.assertRaises(HTTPException) as exc:
                budget.reserve(db, "alice", str(uuid4()), "hash")
            self.assertEqual(exc.exception.status_code, 429)
            self.assertEqual(exc.exception.detail["message"], budget.PAUSED)

    def test_warning_at_sixteen_not_fifteen(self):
        with self.Session() as db:
            row = ItemAIMonth(month=budget.month_key(), spent_micros=15_499_999, held_micros=500_000)
            db.add(row)
            db.commit()
            self.assertFalse(budget.budget_status(db).warning)
            row.spent_micros += 1
            db.commit()
            self.assertTrue(budget.budget_status(db).warning)

    def test_month_rollover_does_not_bypass_global_gate_or_move_charge(self):
        with patch.object(budget, "utc_now", return_value=datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)):
            key, _ = self.reserve()
        with patch.object(budget, "utc_now", return_value=datetime(2027, 1, 1, tzinfo=timezone.utc)):
            with self.Session() as db:
                status = budget.budget_status(db)
                self.assertEqual(status.reason, "assessment_busy")
                self.assertEqual(status.resets_at, "2027-02-01T00:00:00+00:00")
                with self.assertRaises(HTTPException):
                    budget.reserve(db, "alice", str(uuid4()), "hash")
                budget.settle(db, key, 100_000, {})
                self.assertEqual(db.get(ItemAIMonth, "2026-12").spent_micros, 100_000)
                self.assertEqual(budget.budget_status(db).spent, 0)
            self.reserve()

    def test_uncertain_never_expires_and_known_usage_can_be_reconciled_once(self):
        key, _ = self.reserve()
        with self.Session() as db:
            budget.uncertain(db, key)
        with patch.object(budget, "utc_now", return_value=datetime(2030, 1, 1, tzinfo=timezone.utc)):
            with self.Session() as db:
                self.assertEqual(budget.budget_status(db).reason, "assessment_busy")
                budget.settle(db, key, 700_000, {}, failure_code="reconciled")
                budget.settle(db, key, 900_000, {})
                row = db.get(ItemAssessment, key)
                self.assertEqual(row.actual_micros, 700_000)
                self.assertEqual(db.get(ItemAIMonth, row.month).held_micros, 0)
                self.assertIsNone(db.get(ItemAIGate, 1).active_id)

    def test_failed_reservation_commit_makes_no_partial_hold(self):
        with self.Session() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("database unavailable")):
                with self.assertRaises(RuntimeError):
                    budget.reserve(db, "alice", str(uuid4()), "hash")
        with self.Session() as db:
            self.assertEqual(db.query(ItemAssessment).count(), 0)
            self.assertEqual(budget.budget_status(db).reserved, 0)

    def test_upgrade_adds_only_companion_tables_and_postgres_ddl(self):
        engine = create_engine("sqlite://")
        additions = {"item_assessments", "saved_item_assessments", "item_ai_gate", "item_ai_months"}
        try:
            Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in additions])
            old = {name: inspect(engine).get_columns(name) for name in inspect(engine).get_table_names()}
            with patch("app.db.init_db.engine", engine):
                init_db()
                init_db()
            self.assertEqual(set(inspect(engine).get_table_names()) - set(old), additions)
            for name, columns in old.items():
                self.assertEqual([(c["name"], str(c["type"])) for c in columns],
                                 [(c["name"], str(c["type"])) for c in inspect(engine).get_columns(name)])
        finally:
            engine.dispose()
        for model in [ItemAssessment, SavedItemAssessment]:
            ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
            self.assertIn("JSONB", ddl)
        self.assertIn("TIMESTAMP WITH TIME ZONE", str(CreateTable(ItemAssessment.__table__).compile(dialect=postgresql.dialect())))


if __name__ == "__main__":
    unittest.main()
