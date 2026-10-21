"""Account preference storage; calculator and saved snapshots remain unchanged."""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.init_db import init_db
from app.db.models.item_preference import ItemPreference
from app.db.models.saved_deal import SavedDeal
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.item_models import ItemAnalyzeRequest, ItemPersonalDefaults
from app.item_analysis_engine import analyze_item
from app.item_preference_models import SaveItemPreferencesRequest
from app.main import app
from app.saved_item_models import SaveItemRequest
from app.services.saved_item_service import item_response, save_item


PATH = "/api/items/preferences"
FIELDS = ("hourly_value", "target_profit", "contingency_pct", "fee_pct", "pickup", "fee_fixed")


class ItemPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.old_overrides = app.dependency_overrides.copy()

        def database():
            with self.Session() as db:
                yield db

        def owner(x_test_user: str = Header(default="alice")):
            return x_test_user

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user_id] = owner
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.old_overrides)
        self.engine.dispose()

    def get(self, user="alice"):
        return self.client.get(PATH, headers={"X-Test-User": user})

    def put(self, body, user="alice"):
        return self.client.put(PATH, json=body, headers={"X-Test-User": user})

    def test_missing_profile_and_literal_route_before_item_id(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json())
        paths = [route.path for route in app.routes]
        self.assertLess(paths.index(PATH), paths.index("/api/items/{item_id}"))
        with self.Session() as db:
            self.assertEqual(db.query(ItemPreference).count(), 0)

    def test_both_routes_require_sign_in(self):
        del app.dependency_overrides[get_current_user_id]
        for method, kwargs in (("get", {}), ("put", {"json": {}})):
            with self.subTest(method=method):
                response = getattr(self.client, method)(PATH, **kwargs)
                self.assertEqual(response.status_code, 403, response.text)
        with self.Session() as db:
            self.assertEqual(db.query(ItemPreference).count(), 0)

    def test_roundtrip_numeric_values_and_utc_timestamp(self):
        body = dict(hourly_value=25.555, target_profit=100.005, contingency_pct=.143,
                    fee_pct=.123456, pickup=12.345, fee_fixed=0)
        now = datetime(2026, 10, 21, 12, 34, 56, tzinfo=timezone.utc)
        with patch("app.services.item_preference_service.datetime") as clock:
            clock.now.return_value = now
            response = self.put(body)
        self.assertEqual(response.status_code, 200, response.text)
        expected = dict(hourly_value=25.56, target_profit=100.01, contingency_pct=.143,
                        fee_pct=.123456, pickup=12.35, fee_fixed=0, updated_at=now.isoformat())
        self.assertEqual(response.json(), expected)
        self.assertEqual(self.get().json(), expected)
        self.assertNotIn("user_id", response.json())

    def test_full_replacement_and_blank_profile_distinct_from_missing(self):
        self.assertEqual(self.put(dict.fromkeys(FIELDS, 0)).status_code, 200)
        zeros = self.get().json()
        for name in FIELDS:
            self.assertEqual(zeros[name], 0)
        replacement = self.put({"pickup": 8, "fee_fixed": None}).json()
        for name in FIELDS:
            self.assertEqual(replacement[name], 8 if name == "pickup" else None)
        empty = self.put({}).json()
        self.assertIsNotNone(empty)
        for name in FIELDS:
            self.assertIsNone(empty[name])
        self.assertEqual(self.get().json(), empty)
        self.assertIsNone(self.get("bob").json())

    def test_owner_scope_and_no_owner_supplied_in_body(self):
        alice = self.put({"pickup": 11}).json()
        bob = self.put({"pickup": 22}, "bob").json()
        self.assertEqual(self.get().json(), alice)
        self.assertEqual(self.get("bob").json(), bob)
        self.assertEqual(self.put({"user_id": "bob", "pickup": 999}).status_code, 422)
        self.assertEqual(self.get("bob").json(), bob)
        with self.Session() as db:
            self.assertEqual(db.query(ItemPreference).count(), 2)

    def test_last_write_wins_and_lost_response_retry_is_full_replacement(self):
        first = {"target_profit": 30, "pickup": 5}
        self.assertEqual(self.put(first).status_code, 200)  # Ignore the committed response.
        retry = self.put(first)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["target_profit"], 30)
        self.assertEqual(self.put({"target_profit": 60}).status_code, 200)
        # There is deliberately no history/request-ID conflict protection:
        # repeating an older PUT after another device writes makes it latest.
        self.assertEqual(self.put(first).status_code, 200)
        self.assertEqual(self.get().json()["target_profit"], 30)
        self.assertEqual(self.get().json()["pickup"], 5)
        with self.Session() as db:
            self.assertEqual(db.query(ItemPreference).count(), 1)

    def test_validation_rejects_bad_values_without_changing_saved_preferences(self):
        original = self.put({"target_profit": 50}).json()
        for field in FIELDS:
            maximum = 1 if field.endswith("_pct") else 1000 if field == "hourly_value" else 1_000_000
            bad = [True, "12", [], {}, -0.001, maximum + 1, float("nan"), float("inf")]
            if field.endswith("_pct"):
                bad.append(.1234567)
            for value in bad:
                with self.subTest(field=field, value=value):
                    response = self.client.put(PATH, content=json.dumps({field: value}),
                                               headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.get().json(), original)
        for extra in ("request_id", "revision", "updated_at", "purchase_price"):
            with self.subTest(extra=extra):
                self.assertEqual(self.put({extra: None}).status_code, 422)
        self.assertEqual(self.get().json(), original)

    def test_inclusive_numeric_limits(self):
        body = dict(hourly_value=1000, target_profit=1_000_000, contingency_pct=1,
                    fee_pct=1, pickup=1_000_000, fee_fixed=1_000_000)
        response = self.put(body)
        self.assertEqual(response.status_code, 200, response.text)
        for name, value in body.items():
            self.assertEqual(response.json()[name], value)

    def test_preferences_do_not_change_calculator_contract_or_load_implicitly(self):
        fields = {"hourly_value", "target_profit", "contingency_pct", "fee_pct"}
        self.assertEqual(set(ItemPersonalDefaults.model_fields), fields)
        before = analyze_item(ItemAnalyzeRequest()).model_dump(mode="json")
        self.put(dict.fromkeys(FIELDS, 0))
        response = self.client.post("/api/items/analyze", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), before)
        for field in ("pickup", "fee_fixed"):
            self.assertEqual(self.client.post("/api/items/analyze", json={
                "personal_defaults": {field: 0}}).status_code, 422)
        with patch("app.main.analyze_item", side_effect=AssertionError("No calculation on preference writes")), \
             patch("app.main.assess", side_effect=AssertionError("No provider call")):
            self.assertEqual(self.put({}).status_code, 200)
            self.assertEqual(self.get().status_code, 200)

    def test_upgrade_only_adds_preferences_and_keeps_existing_rows_and_columns(self):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "item_preferences"])
            def schema():
                return {name: [(c["name"], str(c["type"]), c["nullable"]) for c in inspect(engine).get_columns(name)]
                        for name in inspect(engine).get_table_names()}
            before = schema()
            Session = sessionmaker(bind=engine)
            with Session() as db:
                db.add(SavedDeal(user_id="alice", address="Existing house", analysis_result={"net_profit": 123}))
                db.commit()
                saved = save_item(db, "alice", SaveItemRequest.model_validate({"inputs": {}, "notes": "Existing find"}))
                original = saved.model_dump(mode="json")
            def rows():
                with engine.connect() as connection:
                    return {name: list(connection.execute(select(Base.metadata.tables[name]))) for name in before}
            original_rows = rows()
            with patch("app.db.init_db.engine", engine):
                init_db()
                init_db()
            after = schema()
            self.assertEqual(set(after) - set(before), {"item_preferences"})
            for name, columns in before.items():
                self.assertEqual(after[name], columns)
            with Session() as db:
                from app.services.item_preference_service import save_preferences
                save_preferences(db, "alice", SaveItemPreferencesRequest(pickup=10))
                self.assertEqual(item_response(db.get(SavedItem, saved.id)).model_dump(mode="json"), original)
            self.assertEqual(rows(), original_rows)
        finally:
            engine.dispose()

    def test_postgres_table_and_atomic_upsert_compile(self):
        # SQL compilation evidence only; not a live PostgreSQL probe.
        table = ItemPreference.__table__
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        self.assertIn("PRIMARY KEY (user_id)", ddl)
        self.assertIn("TIMESTAMP WITH TIME ZONE", ddl)
        statement = insert(table).values(user_id="alice", pickup=5).on_conflict_do_update(
            index_elements=[table.c.user_id], set_={"pickup": 5}).returning(*table.c)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (user_id) DO UPDATE", sql)
        self.assertIn("RETURNING", sql)


if __name__ == "__main__":
    unittest.main()
