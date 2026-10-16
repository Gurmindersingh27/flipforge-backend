"""Save/replay behavior against an isolated database; no paid provider calls."""
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.models.item_assessment import ItemAssessment, SavedItemAssessment
from app.db.models.item_save_request import ItemSaveRequest
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.main import app
from app.saved_item_models import SaveItemRequest
from app.services import saved_item_service as saves
from app.services.item_assessment_service import build_result
from app.item_assessment_models import ItemAssessmentRequest
from test_item_assessment import provider_response, request_body


class ItemSaveIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        def database():
            with self.Session() as db:
                yield db
        def owner(x_test_user: str = Header(default="alice")):
            return x_test_user
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user_id] = owner
        self.client = TestClient(app)
        self.body = dict(request_id=str(uuid4()), inputs={"item_name": "Chair", "repairs": 0}, notes="Original")

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def post(self, body=None, user="alice"):
        return self.client.post("/api/items/save", json=self.body if body is None else body,
                                headers={"X-Test-User": user})

    def counts(self):
        with self.Session() as db:
            return tuple(db.query(model).count() for model in (SavedItem, ItemSaveRequest, SavedItemAssessment))

    def test_replay_returns_stored_snapshot_before_math_parent_or_assessment_checks(self):
        parent = self.post({"inputs": {}}).json()
        self.body["parent_item_id"] = parent["id"]
        first = self.post()
        self.assertEqual(first.status_code, 201)
        with patch.object(saves, "analyze_item", side_effect=AssertionError("must not recalculate")), \
                patch.object(saves, "owned_item", side_effect=AssertionError("must not revisit parent")), \
                patch("app.services.item_assessment_service.saved_context", side_effect=AssertionError("must not revalidate")):
            replay = self.post()
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(self.counts(), (2, 1, 0))

    def test_lost_response_after_commit_replays_original_record(self):
        real_save = saves.save_item
        committed = []
        def lose_response(*args):
            committed.append(real_save(*args).model_dump(mode="json"))
            raise ConnectionError("simulated lost response after commit")
        with patch("app.main.save_item", side_effect=lose_response), self.assertRaises(ConnectionError):
            self.post()
        self.assertEqual(self.counts(), (1, 1, 0))
        replay = self.post()
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), committed[0])
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_owner_and_changed_payload_have_same_conflict_without_item_id(self):
        first = self.post().json()
        conflict = self.post(user="bob")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(set(conflict.json()), {"detail"})
        self.assertNotIn(str(first["id"]), conflict.text)
        for change in [dict(notes="Changed"), dict(listing_url="https://example.com/chair"),
                       dict(inputs={**self.body["inputs"], "repairs": 1}), dict(parent_item_id=987654),
                       dict(assessment_id=str(uuid4()))]:
            with self.subTest(change=change):
                changed = self.post({**self.body, **change})
                self.assertEqual(changed.status_code, 409)
                self.assertEqual(changed.json(), conflict.json())
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_fingerprint_preserves_omitted_null_zero_and_nested_default_presence(self):
        variants = [{}, {"repairs": None}, {"repairs": 0}, {"personal_defaults": None},
                    {"personal_defaults": {}}, {"personal_defaults": {"fee_pct": None}},
                    {"personal_defaults": {"fee_pct": 0}}]
        for inputs in variants:
            body = dict(request_id=str(uuid4()), inputs=inputs)
            first = self.post(body)
            self.assertEqual(first.status_code, 201)
            self.assertEqual(first.json()["inputs"], inputs)
            for other in variants:
                if other != inputs:
                    self.assertEqual(self.post({**body, "inputs": other}).status_code, 409)

    def test_fingerprint_uses_validated_values_and_stable_field_order(self):
        body = dict(request_id=str(uuid4()), inputs={"repairs": 1.004, "item_name": "Chair"},
                    listing_url=" https://example.com/chair ")
        first = self.post(body).json()
        replay = self.post(dict(listing_url="https://example.com/chair", inputs={"item_name": "Chair", "repairs": 1},
                                request_id=body["request_id"].upper()))
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), first)
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_legacy_absent_and_null_ids_still_create_separate_versions(self):
        for extra in ({}, {"request_id": None}):
            body = dict(inputs={"repairs": None}, **extra)
            first, second = self.post(body), self.post(body)
            self.assertEqual((first.status_code, second.status_code), (201, 201))
            self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.counts(), (4, 0, 0))

    def test_new_request_id_creates_new_immutable_linked_version(self):
        first = self.post().json()
        next_body = {**self.body, "request_id": str(uuid4()), "notes": "Next", "parent_item_id": first["id"]}
        child = self.post(next_body).json()
        self.assertNotEqual(child["id"], first["id"])
        self.assertEqual(child["parent_item_id"], first["id"])
        self.assertEqual(child["root_item_id"], first["root_item_id"])
        self.assertEqual(self.post(next_body).json(), child)
        self.assertEqual(self.client.get(f'/api/items/{first["id"]}').json(), first)

    def test_rejections_do_not_claim_id_or_write_rows(self):
        for bad_id in ("bad", "", 123, True):
            self.assertEqual(self.post({**self.body, "request_id": bad_id}).status_code, 422)
        for bad in (dict(parent_item_id=123), dict(inputs={"repairs": -1}), dict(notes="bad\x00"),
                    dict(assessment_confirmation={"repairs": [], "resale_source": "user_estimate", "preset_acknowledged": True})):
            self.assertIn(self.post({**self.body, **bad}).status_code, (404, 422))
            self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.post().status_code, 201)

    def test_failure_before_commit_rolls_back_mapping_snapshot_and_allows_same_key(self):
        body = SaveItemRequest.model_validate(self.body)
        with self.Session() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("before commit")), self.assertRaises(RuntimeError):
                saves.save_item(db, "alice", body)
            self.assertEqual(self.counts(), (0, 0, 0))
            saved = saves.save_item(db, "alice", body)
        self.assertEqual(self.post().json(), saved.model_dump(mode="json"))
        self.assertEqual(self.counts(), (1, 1, 0))

    def miss_first_mapping_read(self, db):
        # Force the race-loser path with an already committed winner. This is a
        # deterministic SQLite branch test, NOT PostgreSQL concurrency evidence.
        original = db.get
        missed = False
        def get(model, key, **kwargs):
            nonlocal missed
            if model is ItemSaveRequest and not missed:
                missed = True
                return None
            return original(model, key, **kwargs)
        return patch.object(db, "get", side_effect=get)

    def test_mapping_key_collision_rolls_back_loser_before_replay_or_conflict(self):
        first = self.post().json()
        for user, changes in [("alice", {}), ("bob", {}), ("alice", {"notes": "other"})]:
            with self.Session() as db, self.miss_first_mapping_read(db):
                body = SaveItemRequest.model_validate({**self.body, **changes})
                if user == "alice" and not changes:
                    self.assertEqual(saves.save_item(db, user, body).model_dump(mode="json"), first)
                else:
                    from fastapi import HTTPException
                    with self.assertRaises(HTTPException) as error:
                        saves.save_item(db, user, body)
                    self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(self.counts(), (1, 1, 0))

    def test_unrelated_mapping_integrity_error_is_not_a_replay_even_with_winner(self):
        self.post()
        def invalid_mapping(_, __, target):
            target.request_hash = None
        event.listen(ItemSaveRequest, "before_insert", invalid_mapping)
        try:
            with self.Session() as db, self.miss_first_mapping_read(db), self.assertRaises(IntegrityError):
                saves.save_item(db, "alice", SaveItemRequest.model_validate(self.body))
        finally:
            event.remove(ItemSaveRequest, "before_insert", invalid_mapping)
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_photo_replay_preserves_saved_evidence_without_revalidating_assessment(self):
        assessment_id = str(uuid4())
        evidence = build_result(ItemAssessmentRequest.model_validate(request_body()), provider_response())
        with self.Session() as db:
            db.add(ItemAssessment(id=assessment_id, user_id="alice", request_hash="fixture", month="2026-10",
                                  status="completed", reserved_micros=0, actual_micros=0, result=evidence))
            db.commit()
        body = dict(request_id=str(uuid4()), inputs={**evidence["inputs"], "repairs": 15}, assessment_id=assessment_id,
                    assessment_confirmation=dict(repairs=[dict(job_id="sand_seat", materials_cost=15)],
                                                 resale_source="assessment", preset_acknowledged=True))
        first = self.post(body)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["assessment"]["evidence"], evidence)
        with self.Session() as db, self.miss_first_mapping_read(db):
            self.assertEqual(saves.save_item(db, "alice", SaveItemRequest.model_validate(body)).model_dump(mode="json"), first.json())
        self.assertEqual(self.counts(), (1, 1, 1))
        with self.Session() as db:
            db.get(ItemAssessment, assessment_id).status = "failed"
            db.commit()
        with patch("app.services.item_assessment_service.saved_context", side_effect=AssertionError("must not revalidate")):
            self.assertEqual(self.post(body).json(), first.json())
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_openapi_request_id_is_optional_uuid_and_response_is_unchanged(self):
        schemas = self.client.get("/openapi.json").json()["components"]["schemas"]
        schema = schemas["SaveItemRequest"]
        self.assertNotIn("request_id", schema.get("required", []))
        self.assertIn({"type": "string", "format": "uuid"}, schema["properties"]["request_id"]["anyOf"])
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("request_id", schemas["SavedItemResponse"]["properties"])


if __name__ == "__main__":
    unittest.main()
