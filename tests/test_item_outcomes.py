"""Outcome contracts and transactions on isolated SQLite; races need the PG probe."""
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.init_db import init_db
from app.db.models.item_outcome import ItemOutcomeRevision
from app.db.models.saved_deal import SavedDeal
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.item_outcome_models import CASH_FIELDS, SaveItemOutcomeRequest
from app.main import app
from app.saved_item_models import SaveItemRequest
from app.services import item_outcome_service as outcomes
from app.services.saved_item_service import save_item, item_response


def outcome_body(**changes):
    return {"request_id": str(uuid4()), "expected_previous_id": None, "status": "bought", **changes}


def sold_facts(**changes):
    return {**dict.fromkeys(CASH_FIELDS, 0), "status": "sold", "purchase_price": 40,
            "materials": 15, "pickup": 10, "selling_fees": 5, "sale_price": 150,
            "hours": 2, "hourly_value": 20, "sale_channel": "facebook_marketplace",
            "purchased_on": "2026-10-01", "listed_on": "2026-10-04", "sold_on": "2026-10-09", **changes}


class ItemOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.old_overrides = app.dependency_overrides.copy()
        def database():
            with self.Session() as db:
                yield db
        def owner(x_test_user: str = Header(default="alice")):
            return x_test_user
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user_id] = owner
        self.client = TestClient(app)
        self.root = self.save_estimate()
        self.child = self.save_estimate(parent_item_id=self.root["id"])
        self.other = self.save_estimate()
        self.foreign = self.save_estimate(user="bob")
        self.body = outcome_body()

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.old_overrides)
        self.engine.dispose()

    def save_estimate(self, user="alice", **fields):
        response = self.client.post("/api/items/save", headers={"X-Test-User": user}, json={
            "inputs": {"purchase_price": 999, "repairs": 888, "personal_defaults": {"hourly_value": 77}}, **fields})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def post(self, body=None, root=None, user="alice"):
        return self.client.post(f'/api/items/{root or self.root["id"]}/outcomes',
                                headers={"X-Test-User": user}, json=self.body if body is None else body)

    def latest(self, root=None, user="alice"):
        return self.client.get(f'/api/items/{root or self.root["id"]}/outcomes', headers={"X-Test-User": user})

    def count(self):
        with self.Session() as db:
            return db.query(ItemOutcomeRevision).count()

    def test_untracked_and_incomplete_do_not_infer_actuals_or_estimate_basis(self):
        self.assertIsNone(self.latest().json())
        first = self.post()
        self.assertEqual(first.status_code, 201, first.text)
        data = first.json()
        self.assertEqual(data["id"], self.body["request_id"])
        self.assertEqual(data["revision_number"], 1)
        self.assertIsNone(data["previous_id"])
        self.assertIsNone(data["based_on_item_id"])
        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(data["created_at"].endswith("+00:00"))
        self.assertNotIn("user_id", data)
        self.assertNotIn("request_hash", data)
        for key, value in data["inputs"].items():
            if key != "status":
                self.assertIsNone(value, key)
        for key, value in data["calculated"].items():
            if key != "missing_fields":
                self.assertIsNone(value, key)
        self.assertEqual(data["calculated"]["missing_fields"]["cash_invested"], list(CASH_FIELDS))
        self.assertEqual(self.latest().json(), data)

    def test_complete_sale_figures_are_server_calculated_and_numbers(self):
        body = {**self.body, **sold_facts(), "based_on_item_id": self.child["id"]}
        response = self.post(body)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["based_on_item_id"], self.child["id"])
        self.assertEqual(data["calculated"], dict(cash_invested=70, cash_profit=80, profit_after_time=40,
            days_to_sell=5, days_held=8, missing_fields={name: [] for name in
            ("cash_invested", "cash_profit", "profit_after_time", "days_to_sell", "days_held")}))
        self.assertIsInstance(data["inputs"]["purchase_price"], (int, float))
        self.assertIsInstance(data["calculated"]["cash_profit"], (int, float))
        self.assertEqual(self.latest().json(), data)

    def test_zero_loss_rounding_and_same_day_sale(self):
        cases = [
            (sold_facts(purchase_price=0, sale_price=0, materials=0, pickup=0, selling_fees=0,
                        hours=0, hourly_value=0, listed_on="2026-10-01", sold_on="2026-10-01"), (0, 0, 0, 0, 0)),
            (sold_facts(sale_price=20), (70, -50, -90, 5, 8)),
            (sold_facts(purchase_price=40.005, materials=15.004, hours=1.25, hourly_value=10.01),
             (70.01, 79.99, 67.48, 5, 8)),
            (sold_facts(hours=0.5, hourly_value=25.01), (70, 80, 67.50, 5, 8)),
            (sold_facts(sale_price=70, hours=0.5, hourly_value=25.01), (70, 0, -12.51, 5, 8)),
        ]
        for facts, expected in cases:
            with self.subTest(facts=facts):
                root = self.save_estimate()["id"]
                response = self.post({**outcome_body(), **facts}, root=root)
                self.assertEqual(response.status_code, 201, response.text)
                figures = response.json()["calculated"]
                self.assertEqual(tuple(figures[name] for name in
                    ("cash_invested", "cash_profit", "profit_after_time", "days_to_sell", "days_held")), expected)

    def test_each_unknown_cash_input_blocks_total_and_both_profits(self):
        for field in CASH_FIELDS:
            for explicit_null in (False, True):
                with self.subTest(field=field, explicit_null=explicit_null):
                    facts = sold_facts()
                    facts.pop(field)
                    if explicit_null:
                        facts[field] = None
                    response = self.post({**outcome_body(), **facts}, root=self.save_estimate()["id"])
                    self.assertEqual(response.status_code, 201, response.text)
                    figures = response.json()["calculated"]
                    for name in ("cash_invested", "cash_profit", "profit_after_time"):
                        self.assertIsNone(figures[name])
                        self.assertIn(field, figures["missing_fields"][name])

    def test_unknown_sale_time_and_dates_do_not_block_unrelated_figures(self):
        for field in ("sale_price", "hours", "hourly_value", "listed_on", "purchased_on", "sold_on"):
            with self.subTest(field=field):
                response = self.post({**outcome_body(), **sold_facts(**{field: None})}, root=self.save_estimate()["id"])
                self.assertEqual(response.status_code, 201, response.text)
                figures = response.json()["calculated"]
                self.assertEqual(figures["cash_invested"], 70)
                self.assertEqual(figures["cash_profit"], None if field == "sale_price" else 80)
                self.assertEqual(figures["profit_after_time"], None if field in ("sale_price", "hours", "hourly_value") else 40)
                self.assertEqual(figures["days_to_sell"], None if field in ("listed_on", "sold_on") else 5)
                self.assertEqual(figures["days_held"], None if field in ("purchased_on", "sold_on") else 8)

    def test_bought_and_listed_cash_is_not_profit_and_all_initial_statuses_allowed(self):
        for status in ("bought", "listed", "sold"):
            body = {**outcome_body(), **dict.fromkeys(CASH_FIELDS, 0), "status": status}
            response = self.post(body, root=self.save_estimate()["id"])
            self.assertEqual(response.status_code, 201, response.text)
            figures = response.json()["calculated"]
            self.assertEqual(figures["cash_invested"], 0)
            self.assertIsNone(figures["cash_profit"])
            self.assertEqual("status" in figures["missing_fields"]["cash_profit"], status != "sold")

    def test_append_correction_preserves_history_estimates_and_does_not_carry_values(self):
        first = self.post({**self.body, **sold_facts(), "based_on_item_id": self.child["id"]}).json()
        body = {**outcome_body(), "expected_previous_id": first["id"]}
        second = self.post(body)
        self.assertEqual(second.status_code, 201, second.text)
        data = second.json()
        self.assertEqual(data["revision_number"], 2)
        self.assertEqual(data["previous_id"], first["id"])
        self.assertIsNone(data["based_on_item_id"])
        self.assertIsNone(data["inputs"]["sale_price"])
        self.assertIsNone(data["inputs"]["purchase_price"])
        with self.Session() as db:
            old = outcomes.outcome_response(db.get(ItemOutcomeRevision, first["id"])).model_dump(mode="json")
            self.assertEqual(old, first)
        self.assertEqual(self.latest().json(), data)
        for original in (self.root, self.child):
            self.assertEqual(self.client.get(f'/api/items/{original["id"]}').json(), original)

    def test_basis_must_be_owned_in_same_family_and_is_never_inferred(self):
        for basis in (self.root["id"], self.child["id"]):
            first = self.latest().json()
            body = {**outcome_body(), "based_on_item_id": basis, "expected_previous_id": first["id"] if first else None}
            self.assertEqual(self.post(body).status_code, 201)
        before = self.count()
        for basis in (self.foreign["id"], self.other["id"], 987654):
            response = self.post({**outcome_body(), "based_on_item_id": basis,
                                  "expected_previous_id": self.latest().json()["id"]})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "Item not found."})
        self.assertEqual(self.count(), before)

    def test_roots_are_owner_filtered_and_child_is_not_a_root(self):
        self.post()
        for root, user in ((self.root["id"], "bob"), (self.foreign["id"], "alice"),
                           (self.child["id"], "alice"), (987654, "alice")):
            for response in (self.latest(root=root, user=user), self.post(outcome_body(), root=root, user=user)):
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Item not found."})
        self.assertEqual(self.count(), 1)

    def test_routes_use_existing_auth_and_do_not_add_update_delete_or_history(self):
        app.dependency_overrides.pop(get_current_user_id)
        for method in ("GET", "POST"):
            response = self.client.request(method, f'/api/items/{self.root["id"]}/outcomes',
                                           **({"json": self.body} if method == "POST" else {}))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.count(), 0)
        schema = self.client.get("/openapi.json").json()
        path = schema["paths"]["/api/items/{root_item_id}/outcomes"]
        self.assertEqual(set(path), {"get", "post"})
        for method in path.values():
            self.assertIn("security", method)
        self.assertFalse(any("outcomes/history" in path for path in schema["paths"]))
        required = schema["components"]["schemas"]["SaveItemOutcomeRequest"]["required"]
        self.assertEqual(set(required), {"status", "request_id", "expected_previous_id"})

    def test_enum_status_dates_and_extra_fields_reject_without_writes(self):
        bad = [dict(status="unknown"), dict(status="sold", sale_channel="FB"),
               dict(status="sold", sale_channel="buyer\x00name"), dict(status="sold", sale_channel="x" * 101),
               dict(sale_price=0), dict(sale_channel="other"), dict(sold_on="2026-10-01"),
               dict(listed_on="2026-10-01"), dict(purchased_on="2026-02-30"),
               dict(purchased_on=1790812800), dict(purchased_on="2026-10-01T00:00:00Z"),
               dict(status="listed", purchased_on="2026-10-03", listed_on="2026-10-01"),
               dict(status="sold", purchased_on="2026-10-03", sold_on="2026-10-01"),
               dict(status="sold", listed_on="2026-10-03", sold_on="2026-10-01"),
               dict(based_on_item_id=True), dict(based_on_item_id=0), dict(based_on_item_id="1"),
               dict(user_id="bob"), dict(root_item_id=1), dict(cash_profit=200), dict(notes="no free text"),
               dict(request_id=None), dict(request_id="bad"), dict(expected_previous_id="bad")]
        for changes in bad:
            with self.subTest(changes=changes):
                response = self.post({**self.body, **changes})
                self.assertEqual(response.status_code, 422, response.text)
        for field in ("request_id", "expected_previous_id", "status"):
            response = self.post({k: v for k, v in self.body.items() if k != field})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_every_channel_is_accepted(self):
        for channel in ("facebook_marketplace", "craigslist", "offerup", "ebay", "in_person", "other"):
            response = self.post({**outcome_body(), "status": "sold", "sale_channel": channel}, root=self.save_estimate()["id"])
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["inputs"]["sale_channel"], channel)

    def test_numeric_boundaries_nonfinite_and_boolean_validation(self):
        for field in (*CASH_FIELDS, "sale_price", "hours", "hourly_value"):
            maximum = 10000 if field == "hours" else 1000 if field == "hourly_value" else 1000000
            bad = [-0.001, maximum + 1, "1", True, float("nan"), float("inf"), -float("inf")]
            if field == "hours":
                bad.append(0.001)
            for value in bad:
                with self.subTest(field=field, value=value):
                    response = self.client.post(f'/api/items/{self.root["id"]}/outcomes',
                        content=json.dumps({**self.body, "status": "sold", field: value}), headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(), 0)
        facts = {**dict.fromkeys(CASH_FIELDS, 1000000), "sale_price": 1000000, "hours": 10000, "hourly_value": 1000}
        response = self.post({**self.body, "status": "sold", **facts})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["calculated"]["profit_after_time"], -17000000)

    def test_stale_edits_reject_without_changing_latest_or_claiming_uuid(self):
        first = self.post().json()
        next_body = {**outcome_body(), "expected_previous_id": first["id"], "status": "listed"}
        second = self.post(next_body).json()
        stale = {**outcome_body(), "expected_previous_id": first["id"]}
        for expected in (None, first["id"], str(uuid4())):
            response = self.post({**stale, "expected_previous_id": expected})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.latest().json(), second)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.post({**stale, "expected_previous_id": second["id"]}).status_code, 201)

    def test_replay_returns_original_after_later_revision_without_checks_or_calculation(self):
        first = self.post().json()
        self.post({**outcome_body(), "expected_previous_id": first["id"], "status": "listed"})
        with patch.object(outcomes, "calculate_outcome", side_effect=AssertionError("no recalculation")), \
                patch.object(outcomes, "_owned_root", side_effect=AssertionError("no revalidation")):
            replay = self.post()
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), first)
        self.assertEqual(self.count(), 2)
        with patch.object(outcomes, "calculate_outcome", side_effect=AssertionError("no read recalculation")):
            self.assertEqual(self.latest().json()["revision_number"], 2)

    def test_conflicting_uuid_has_same_generic_response_without_ids(self):
        self.post()
        conflicts = [self.post(user="bob"), self.post(root=self.other["id"]),
                     self.post({**self.body, "purchase_price": 0}),
                     self.post({**self.body, "based_on_item_id": self.child["id"]})]
        for response in conflicts:
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), conflicts[0].json())
            self.assertEqual(set(response.json()), {"detail"})
            self.assertNotIn(self.body["request_id"], response.text)
        self.assertEqual(self.count(), 1)

    def test_fingerprint_validated_order_rounding_and_omitted_null_zero(self):
        first_body = {**self.body, "purchase_price": 1.004}
        first = self.post(first_body).json()
        replay = self.post(dict(reversed(list({**first_body, "purchase_price": 1}.items()))))
        self.assertEqual(replay.json(), first)
        for changes in ({}, {"purchase_price": None}, {"purchase_price": 0}):
            root = self.save_estimate()["id"]
            body = {**outcome_body(), **changes}
            self.assertEqual(self.post(body, root=root).status_code, 201)
            for other in ({}, {"purchase_price": None}, {"purchase_price": 0}):
                if other != changes:
                    retry = {k: v for k, v in body.items() if k != "purchase_price"}
                    self.assertEqual(self.post({**retry, **other}, root=root).status_code, 409)

    def test_lost_response_after_commit_replays_original(self):
        real_save = outcomes.save_outcome
        committed = []
        def lose_response(*args):
            committed.append(real_save(*args).model_dump(mode="json"))
            raise ConnectionError("simulated response loss")
        with patch("app.main.save_outcome", side_effect=lose_response), self.assertRaises(ConnectionError):
            self.post()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.post().json(), committed[0])

    def test_commit_failure_rolls_back_and_same_session_can_save(self):
        body = SaveItemOutcomeRequest.model_validate(self.body)
        with self.Session() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("commit failed")), self.assertRaises(RuntimeError):
                outcomes.save_outcome(db, "alice", self.root["id"], body)
            self.assertEqual(db.scalar(text("SELECT 1")), 1)
            self.assertEqual(db.query(ItemOutcomeRevision).count(), 0)
            result = outcomes.save_outcome(db, "alice", self.root["id"], body)
        self.assertEqual(self.post().json(), result.model_dump(mode="json"))

    def test_second_replay_check_after_lock_skips_stale_check_and_releases_lock(self):
        first = self.post().json()
        with self.Session() as db:
            real_get = db.get
            calls = 0
            def miss_first(model, key):
                nonlocal calls
                calls += 1
                return None if calls == 1 else real_get(model, key)
            with patch.object(db, "get", side_effect=miss_first), \
                    patch.object(outcomes, "_latest", side_effect=AssertionError("replay before stale check")):
                replay = outcomes.save_outcome(db, "alice", self.root["id"], SaveItemOutcomeRequest.model_validate(self.body))
            self.assertFalse(db.in_transaction())
            self.assertEqual(replay.model_dump(mode="json"), first)

    def test_cross_root_key_collision_rolls_back_and_compares_winner(self):
        self.post()
        with self.Session() as db:
            real_get = db.get
            calls = 0
            def miss_initial_reads(model, key):
                nonlocal calls
                calls += 1
                return None if calls <= 2 else real_get(model, key)
            with patch.object(db, "get", side_effect=miss_initial_reads), self.assertRaises(HTTPException) as error:
                outcomes.save_outcome(db, "alice", self.other["id"], SaveItemOutcomeRequest.model_validate(self.body))
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(db.scalar(text("SELECT 1")), 1)
            self.assertEqual(db.query(ItemOutcomeRevision).count(), 1)

    def test_unrelated_integrity_errors_are_not_replayed(self):
        def bad_row(_, __, row):
            row.request_hash = None
        event.listen(ItemOutcomeRevision, "before_insert", bad_row)
        try:
            with self.Session() as db:
                with self.assertRaises(IntegrityError):
                    outcomes.save_outcome(db, "alice", self.root["id"], SaveItemOutcomeRequest.model_validate(self.body))
                self.assertEqual(db.scalar(text("SELECT 1")), 1)
                self.assertEqual(db.query(ItemOutcomeRevision).count(), 0)
        finally:
            event.remove(ItemOutcomeRevision, "before_insert", bad_row)

    def test_conflict_classifier_accepts_only_request_primary_key(self):
        for code, name, expected in (("23505", "pk_item_outcome_revisions", True),
                ("23505", "uq_item_outcome_revision_number", False), ("23502", "pk_item_outcome_revisions", False)):
            error = IntegrityError("", {}, SimpleNamespace(pgcode=code, diag=SimpleNamespace(constraint_name=name)))
            self.assertEqual(outcomes._request_key_collision(error, "postgresql"), expected)
        error = sqlite3.IntegrityError("UNIQUE constraint failed: item_outcome_revisions.root_item_id, item_outcome_revisions.revision_number")
        error.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT_UNIQUE
        self.assertFalse(outcomes._request_key_collision(IntegrityError("", {}, error), "sqlite"))

    def test_upgrade_adds_only_outcomes_and_preserves_existing_data_and_schema(self):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "item_outcome_revisions"])
            def schema():
                return {name: [(c["name"], str(c["type"]), c["nullable"]) for c in inspect(engine).get_columns(name)]
                        for name in inspect(engine).get_table_names()}
            before = schema()
            Session = sessionmaker(bind=engine)
            with Session() as db:
                db.add(SavedDeal(user_id="alice", address="Existing house", analysis_result={"net_profit": 123}))
                db.commit()
                saved = save_item(db, "alice", SaveItemRequest.model_validate({"inputs": {}, "notes": "Existing item"}))
                original = saved.model_dump(mode="json")
            with patch("app.db.init_db.engine", engine):
                init_db()
                init_db()
            after = schema()
            self.assertEqual(set(after) - set(before), {"item_outcome_revisions"})
            for name, columns in before.items():
                self.assertEqual(after[name], columns)
            with Session() as db:
                self.assertEqual(db.query(SavedDeal).one().analysis_result, {"net_profit": 123})
                self.assertEqual(item_response(db.get(SavedItem, saved.id)).model_dump(mode="json"), original)
                self.assertEqual(db.query(ItemOutcomeRevision).count(), 0)
                outcomes.save_outcome(db, "alice", saved.id, SaveItemOutcomeRequest.model_validate(outcome_body()))
                self.assertEqual(item_response(db.get(SavedItem, saved.id)).model_dump(mode="json"), original)
        finally:
            engine.dispose()
        ddl = str(CreateTable(ItemOutcomeRevision.__table__).compile(dialect=postgresql.dialect()))
        for expected in ("JSONB", "pk_item_outcome_revisions", "uq_item_outcome_revision_number",
                         "FOREIGN KEY(root_item_id) REFERENCES saved_items (id)",
                         "FOREIGN KEY(based_on_item_id) REFERENCES saved_items (id)"):
            self.assertIn(expected, ddl)
