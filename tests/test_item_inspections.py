"""Inspection API, isolation and transaction contracts on an isolated database."""
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
from app.db.models.item_inspection import ItemInspectionRevision
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.item_inspection_models import CHECK_IDS, SaveItemInspectionRequest
from app.main import app
from app.saved_item_models import SaveItemRequest
from app.services import item_inspection_service as inspections
from app.services.saved_item_service import save_item, item_response


def inspection_body(**changes):
    return {"request_id": str(uuid4()), "expected_previous_id": None, "kind": "chair", **changes}


class ItemInspectionTests(unittest.TestCase):
    def make_engine(self):
        return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    def make_upgrade_engine(self):
        return create_engine("sqlite://")

    def setUp(self):
        self.engine = self.make_engine()
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", foreign_keys)
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

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.old_overrides)
        self.engine.dispose()

    def save_estimate(self, user="alice", **fields):
        result = self.client.post("/api/items/save", headers={"X-Test-User": user}, json={
            "inputs": {"item_name": "Seller says Mirra", "purchase_price": 45, "resale_low": 180, "resale_high": 240},
            "notes": "Original saved notes", **fields})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def post(self, body=None, root=None, user="alice"):
        return self.client.post(f'/api/items/{root or self.root["id"]}/inspections',
                                headers={"X-Test-User": user}, json=body if body is not None else inspection_body())

    def latest(self, root=None, user="alice"):
        return self.client.get(f'/api/items/{root or self.root["id"]}/inspections', headers={"X-Test-User": user})

    def count(self):
        with self.Session() as db:
            return db.query(ItemInspectionRevision).count()

    def test_no_record_and_blank_observations_stay_unknown(self):
        self.assertIsNone(self.latest().json())
        self.assertEqual(self.count(), 0)
        body = inspection_body()
        response = self.post(body)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["id"], body["request_id"])
        self.assertEqual(data["root_item_id"], self.root["id"])
        self.assertEqual(data["revision_number"], 1)
        self.assertIsNone(data["previous_id"])
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["source"], "user_reported")
        self.assertTrue(data["created_at"].endswith("+00:00"))
        self.assertEqual(data["inputs"], {"guide_version": "furniture-v1", "kind": "chair", "brand_hint": None,
                         "label_text": None, "notes": None, "inspected_on": None,
                         "answers": dict.fromkeys(CHECK_IDS["chair"], "unchecked")})
        for key in ("user_id", "request_hash", "calculated", "model", "authentic", "condition_score"):
            self.assertNotIn(key, data)
        self.assertEqual(self.latest().json(), data)

    def test_preserves_label_and_notes_exactly_without_paid_calls_or_estimate_changes(self):
        before = self.client.get(f'/api/items/{self.root["id"]}/project').json()
        body = inspection_body(brand_hint="herman_miller", label_text="  MR??\nDate unreadable  ",
                               notes="  Height drops. <b>Arm split</b>  ", inspected_on="2026-11-07",
                               answers={"chair-height": "issue", "chair-frame": "okay"})
        with patch("app.main.assess", side_effect=AssertionError("no assessment")), \
             patch("app.main.analyze_item", side_effect=AssertionError("no calculator")):
            result = self.post(body)
            self.assertEqual(result.status_code, 201, result.text)
            data = self.latest().json()
        for key in ("label_text", "notes", "inspected_on", "brand_hint"):
            self.assertEqual(data["inputs"][key], body[key])
        self.assertEqual(data["inputs"]["answers"], {**dict.fromkeys(CHECK_IDS["chair"], "unchecked"), **body["answers"]})
        self.assertEqual(self.client.get(f'/api/items/{self.root["id"]}/project').json(), before)

    def test_only_owner_and_real_root_can_read_or_save(self):
        for root, user in ((self.foreign["id"], "alice"), (self.child["id"], "alice"), (999999, "alice"), (self.root["id"], "bob")):
            self.assertEqual(self.latest(root=root, user=user).status_code, 404)
            self.assertEqual(self.post(root=root, user=user).status_code, 404)
        self.assertEqual(self.count(), 0)

    def test_both_routes_require_sign_in_and_publish_security(self):
        app.dependency_overrides.pop(get_current_user_id)
        self.assertEqual(self.latest().status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.count(), 0)
        path = self.client.get("/openapi.json").json()["paths"]["/api/items/{root_item_id}/inspections"]
        self.assertTrue(path["get"]["security"])
        self.assertTrue(path["post"]["security"])

    def test_revisions_replace_facts_and_do_not_mutate_history(self):
        first = self.post(inspection_body(label_text="Partial label", notes="Arm cracked", answers={"chair-surfaces": "issue"})).json()
        second = self.post(inspection_body(expected_previous_id=first["id"], kind="storage", notes="Drawer sticks",
                                           answers={"storage-moving": "issue"})).json()
        self.assertEqual(second["revision_number"], 2)
        self.assertEqual(second["previous_id"], first["id"])
        self.assertIsNone(second["inputs"]["label_text"])
        self.assertEqual(set(second["inputs"]["answers"]), set(CHECK_IDS["storage"]))
        self.assertEqual(self.latest().json(), second)
        with self.Session() as db:
            self.assertEqual(inspections.inspection_response(db.get(ItemInspectionRevision, first["id"])).model_dump(mode="json"), first)

    def test_stale_and_wrong_previous_revision_cannot_overwrite(self):
        first = self.post().json()
        foreign = self.post(root=self.other["id"]).json()
        for expected in (None, str(uuid4()), foreign["id"]):
            self.assertEqual(self.post(inspection_body(expected_previous_id=expected)).status_code, 409)
        self.assertEqual(self.latest().json(), first)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.post(inspection_body(expected_previous_id=first["id"])).status_code, 201)

    def test_same_request_replays_original_even_after_later_save(self):
        body = inspection_body(answers={"chair-height": "issue"})
        first = self.post(body).json()
        second = self.post(inspection_body(expected_previous_id=first["id"], notes="New notes")).json()
        self.assertEqual(self.post(body).json(), first)
        self.assertEqual(self.latest().json(), second)
        self.assertEqual(self.count(), 2)

    def test_replay_uses_normalized_facts_and_ignores_json_key_order(self):
        body = inspection_body(answers={"chair-frame": "okay"})
        first = self.post(body).json()
        retry = {**body, **first["inputs"]}
        retry["answers"] = dict(reversed(list(retry["answers"].items())))
        self.assertEqual(self.post(dict(reversed(list(retry.items())))).json(), first)
        self.assertEqual(self.count(), 1)

    def test_reused_request_key_never_discloses_or_changes_another_record(self):
        body = inspection_body(notes="Private observations")
        self.post(body)
        responses = [self.post(body, user="bob"), self.post(body, root=self.other["id"]),
                     self.post({**body, "notes": "Changed"}), self.post({**body, "expected_previous_id": str(uuid4())})]
        for response in responses:
            self.assertEqual(response.status_code, 409)
            self.assertEqual(set(response.json()), {"detail"})
            self.assertNotIn("Private observations", response.text)
        self.assertEqual(self.count(), 1)

    def test_lost_response_retry_returns_committed_record_once(self):
        original = inspections.save_inspection
        committed = []
        def lose_response(*args):
            committed.append(original(*args).model_dump(mode="json"))
            raise ConnectionError("simulated response loss")
        body = inspection_body(notes="Saved before connection dropped")
        with patch("app.main.save_inspection", side_effect=lose_response), self.assertRaises(ConnectionError):
            self.post(body)
        self.assertEqual(self.post(body).json(), committed[0])
        self.assertEqual(self.count(), 1)

    def test_invalid_payloads_do_not_persist(self):
        invalid = [
            {"kind": "sofa"}, {"kind": None}, {"guide_version": "furniture-v2"},
            {"answers": {"storage-frame": "okay"}}, {"answers": {"chair-frame": True}},
            {"answers": {"chair-frame": "passed"}}, {"answers": None},
            {"kind": "general", "brand_hint": "herman_miller"}, {"brand_hint": "authentic"},
            {"notes": "x" * 5001}, {"label_text": "x" * 501}, {"notes": 1}, {"label_text": False},
            {"notes": "has\x00nul"}, {"label_text": "has\x00nul"},
            {"inspected_on": "2026-02-30"}, {"inspected_on": "2026-11-07T12:00:00Z"},
            {"authentic": True}, {"model": "Mirra 2"}, {"repair_cost": 25}, {"source": "ai"}, {"user_id": "bob"},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                self.assertEqual(self.post(inspection_body(**fields)).status_code, 422)
        for field in ("request_id", "expected_previous_id", "kind"):
            body = inspection_body()
            del body[field]
            self.assertEqual(self.post(body).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_note_limits_are_independent_of_assessment_description(self):
        result = self.post(inspection_body(label_text="x" * 500, notes="y" * 5000))
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(len(result.json()["inputs"]["notes"]), 5000)
        self.assertEqual(self.post(inspection_body(kind="general"), root=self.other["id"]).status_code, 201)

    def test_rejected_save_rolls_back_and_same_session_can_continue(self):
        first = self.post().json()
        with self.Session() as db:
            with self.assertRaises(HTTPException):
                inspections.save_inspection(db, "alice", self.root["id"], SaveItemInspectionRequest.model_validate(inspection_body()))
            self.assertEqual(db.scalar(text("SELECT 1")), 1)
            second = inspections.save_inspection(db, "alice", self.root["id"], SaveItemInspectionRequest.model_validate(
                inspection_body(expected_previous_id=first["id"], notes="New edit")))
            self.assertEqual(second.revision_number, 2)

    def test_classifier_recovers_only_the_global_request_uuid_collision(self):
        for code, name, expected in (("23505", "pk_item_inspection_revisions", True),
                ("23505", "uq_item_inspection_revision_number", False), ("23503", "pk_item_inspection_revisions", False)):
            original = SimpleNamespace(pgcode=code, diag=SimpleNamespace(constraint_name=name))
            self.assertEqual(inspections._request_key_collision(IntegrityError("", {}, original), "postgresql"), expected)

    def test_upgrade_adds_one_table_without_rewriting_saved_data(self):
        engine = self.make_upgrade_engine()
        try:
            Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name != "item_inspection_revisions"])
            def schema():
                namespace = engine.get_execution_options().get("schema_translate_map", {}).get(None)
                return {name: [(column["name"], str(column["type"]), column["nullable"])
                               for column in inspect(engine).get_columns(name, schema=namespace)]
                        for name in inspect(engine).get_table_names(schema=namespace)}
            before = schema()
            Session = sessionmaker(bind=engine)
            with Session() as db:
                original = save_item(db, "alice", SaveItemRequest.model_validate({"inputs": {}, "notes": "Untouched"})).model_dump(mode="json")
            with patch("app.db.init_db.engine", engine):
                init_db()
                init_db()
            after = schema()
            self.assertEqual(set(after) - set(before), {"item_inspection_revisions"})
            for name in before:
                self.assertEqual(before[name], after[name])
            with Session() as db:
                self.assertEqual(item_response(db.get(SavedItem, original["id"])).model_dump(mode="json"), original)
        finally:
            engine.dispose()
        ddl = str(CreateTable(ItemInspectionRevision.__table__).compile(dialect=postgresql.dialect()))
        for expected in ("JSONB", "pk_item_inspection_revisions", "uq_item_inspection_revision_number", "FOREIGN KEY(root_item_id)"):
            self.assertIn(expected, ddl)
