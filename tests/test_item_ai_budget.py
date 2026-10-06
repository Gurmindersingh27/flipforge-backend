import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
        self.Session = sessionmaker(bind=self.engine, autoflush=False)

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

    def test_uncertain_recovers_at_ten_minutes_and_duplicate_never_spends_again(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            key, _ = self.reserve()
            with self.Session() as db:
                budget.uncertain(db, key)
        with patch.object(budget, "utc_now", return_value=start + timedelta(seconds=599)):
            with self.Session() as db:
                self.assertEqual(budget.budget_status(db).reason, "assessment_busy")
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            with self.Session() as db:
                status = budget.budget_status(db)
                self.assertEqual((status.spent, status.reserved, status.available), (.5, 0, True))
                row = db.get(ItemAssessment, key)
                self.assertEqual(row.status, "failed")
                self.assertIsNone(row.actual_micros)
                self.assertEqual(row.usage, {"cost_basis": "reservation_allowance", "accounted_micros": 500_000})
                self.assertIsNone(db.get(ItemAIGate, 1).active_id)
                budget.uncertain(db, key)  # A late exception cannot undo recovery.
                self.assertEqual(db.get(ItemAssessment, key).status, "failed")
            self.assertEqual(self.reserve(key), (key, False))
            self.assertTrue(self.reserve()[1])

    def test_abandoned_processing_recovers_after_restart_and_charges_original_month(self):
        start = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            key, _ = self.reserve()
        engine = create_engine(self.url)
        try:
            with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
                with sessionmaker(bind=engine)() as db:
                    status = budget.budget_status(db)
                    self.assertTrue(status.available)
                    self.assertEqual((status.month, status.spent, status.reserved), ("2027-01", 0, 0))
                    december = db.get(ItemAIMonth, "2026-12")
                    self.assertEqual((december.spent_micros, december.held_micros), (500_000, 0))
                    self.assertEqual(db.get(ItemAssessment, key).failure_code, "cost_assumed_after_grace")
        finally:
            engine.dispose()

    def test_late_known_cost_replaces_allowance_once_without_releasing_new_run(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            key, _ = self.reserve()
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            next_key, _ = self.reserve()  # Recovery also happens without a budget poll.
            with self.Session() as db:
                budget.settle(db, key, 71000, {"input_tokens": 12000}, result={"schema_version": 1})
                budget.settle(db, key, 900000, {})
                row = db.get(ItemAIMonth, "2026-10")
                self.assertEqual((row.spent_micros, row.held_micros), (71000, 500000))
                self.assertEqual(db.get(ItemAIGate, 1).active_id, next_key)
                self.assertEqual(db.get(ItemAssessment, key).actual_micros, 71000)
                self.assertEqual(db.get(ItemAssessment, key).status, "completed")

    def test_concurrent_expiry_and_above_hold_settlement_do_not_double_account(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            key, _ = self.reserve()
        barrier = Barrier(8)
        def worker(i):
            with self.Session() as db:
                barrier.wait()
                if i % 2:
                    budget.settle(db, key, 700000, {}, failure_code="reconciled")
                else:
                    budget.recover_expired(db)
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(worker, range(8)))
        with self.Session() as db:
            row = db.get(ItemAIMonth, "2026-10")
            self.assertEqual((row.spent_micros, row.held_micros), (700000, 0))
            self.assertIsNone(db.get(ItemAIGate, 1).active_id)

    def test_concurrent_recovery_charges_once_and_allows_only_one_new_run(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            self.reserve()
        barrier = Barrier(8)
        def worker(_):
            barrier.wait()
            try:
                return self.reserve()[1]
            except HTTPException as exc:
                self.assertEqual(exc.status_code, 409)
                return False
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(worker, range(8))), 1)
        with self.Session() as db:
            row = db.get(ItemAIMonth, "2026-10")
            self.assertEqual((row.spent_micros, row.held_micros), (500000, 500000))
            self.assertEqual(db.query(ItemAssessment).count(), 2)

    def test_expiry_accounting_survives_rejected_next_reservation(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            with self.Session() as db:
                db.add(ItemAIMonth(month="2026-10", spent_micros=19_500_000, held_micros=0))
                db.commit()
            key, _ = self.reserve()
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            with self.assertRaises(HTTPException) as exc:
                self.reserve()
            self.assertEqual(exc.exception.status_code, 429)
            with self.Session() as db:
                status = budget.budget_status(db)
                self.assertEqual((status.spent, status.reserved, status.reason), (20, 0, "budget_paused"))
                self.assertIsNone(db.get(ItemAIGate, 1).active_id)
                self.assertEqual(db.get(ItemAssessment, key).status, "failed")

    def test_failed_reservation_commit_makes_no_partial_hold(self):
        with self.Session() as db:
            commit, calls = db.commit, 0
            def fail_reservation_commit():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("database unavailable")
                commit()  # Let the preceding recovery transaction complete.
            with patch.object(db, "commit", side_effect=fail_reservation_commit):
                with self.assertRaises(RuntimeError):
                    budget.reserve(db, "alice", str(uuid4()), "hash")
            self.assertEqual(calls, 2)
        with self.Session() as db:
            self.assertEqual(db.query(ItemAssessment).count(), 0)
            self.assertEqual(budget.budget_status(db).reserved, 0)

    def test_failed_recovery_commit_keeps_hold_until_successful_recovery(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with patch.object(budget, "utc_now", return_value=start):
            key, _ = self.reserve()
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            with self.Session() as db:
                with patch.object(db, "commit", side_effect=RuntimeError("database unavailable")):
                    with self.assertRaises(RuntimeError):
                        budget.recover_expired(db)
            with self.Session() as db:
                row = db.get(ItemAIMonth, "2026-10")
                self.assertEqual((row.spent_micros, row.held_micros), (0, 500000))
                self.assertEqual(db.get(ItemAIGate, 1).active_id, key)
                self.assertEqual(db.get(ItemAssessment, key).status, "processing")
                self.assertTrue(budget.budget_status(db).available)
                self.assertEqual(budget.budget_status(db).spent, .5)

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
