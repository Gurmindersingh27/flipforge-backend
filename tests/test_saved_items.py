import json
import random
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import Header
from fastapi.testclient import TestClient
from jose import ExpiredSignatureError
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.main import app
from app.auth import get_current_user_id
from app.db.base import Base
from app.db.session import get_db
from app.db.init_db import init_db
from app.db.models.saved_deal import SavedDeal
from app.db.models.saved_item import SavedItem


def dresser(price=0):
    return dict(purchase_price=price, resale_low=300, resale_high=450, repairs=60,
                pickup=40, delivery=0, storage=0, fee_fixed=0, fee_pct=0,
                contingency_pct=0, hours=5, hourly_value=20, target_profit=150)


class SavedItemsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        # Simulate the pre-Items database and retain exact schema/data evidence.
        Base.metadata.create_all(self.engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "saved_items"])
        self.before_schema = self.schema()
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(SavedDeal(user_id="alice", address="Existing house", analysis_result={"net_profit": 123}))
            db.commit()
        with patch("app.db.init_db.engine", self.engine):
            init_db()
            init_db()

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
        self.engine.dispose()

    def schema(self):
        inspector = inspect(self.engine)
        return {name: [(c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(name)]
                for name in inspector.get_table_names()}

    def save(self, inputs=None, user="alice", **metadata):
        response = self.client.post("/api/items/save", json={"inputs": inputs if inputs is not None else dresser(), **metadata},
                                    headers={"X-Test-User": user})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def count(self):
        with self.Session() as db:
            return db.query(SavedItem).count()

    def test_nul_in_saved_text_is_rejected_without_creating_rows(self):
        existing = self.save()
        for field in ("notes", "item_name", "category"):
            for value in ("\x00text", "te\x00xt", "text\x00"):
                with self.subTest(field=field, value=value):
                    payload = {"inputs": dresser()}
                    if field == "notes":
                        payload[field] = value
                    else:
                        payload["inputs"][field] = value
                    response = self.client.post("/api/items/save", json=payload)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(field, response.text)
                    self.assertIn("NUL", response.text)
                    self.assertEqual(self.count(), 1)
        self.assertEqual(self.client.get(f"/api/items/{existing['id']}").json(), existing)

    def test_save_text_validation_preserves_allowed_text_and_public_analyze(self):
        text = "Dresser\nwood\tfinish 🪑"
        saved = self.save(inputs={**dresser(), "item_name": text, "category": text}, notes=text)
        self.assertEqual(saved["notes"], text)
        self.assertEqual(saved["inputs"]["item_name"], text)
        self.assertEqual(saved["inputs"]["category"], text)
        response = self.client.post("/api/items/analyze", json={
            **dresser(), "item_name": "name\x00", "category": "category\x00"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_existing_database_adds_only_items_and_preserves_house(self):
        after = self.schema()
        self.assertEqual(set(after) - set(self.before_schema), {"saved_items"})
        for name, columns in self.before_schema.items():
            self.assertEqual(after[name], columns)
        self.save()
        self.assertEqual(self.client.get("/api/deals").json()[0]["analysis_result"], {"net_profit": 123})
        indexes = inspect(self.engine).get_indexes("saved_items")
        self.assertTrue(any(i["column_names"] == ["user_id"] for i in indexes))

    def test_fresh_database_and_postgres_ddl(self):
        fresh = create_engine("sqlite://")
        try:
            with patch("app.db.init_db.engine", fresh):
                init_db()
            self.assertEqual(set(inspect(fresh).get_table_names()), set(Base.metadata.tables))
        finally:
            fresh.dispose()
        ddl = str(CreateTable(SavedItem.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn("JSONB", ddl)
        self.assertIn("root_item_id INTEGER NOT NULL", ddl)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", ddl)

    def test_save_get_and_numbers_are_canonical(self):
        body = dresser(100.01)
        body["repairs"] = 60.004
        record = self.save(body, listing_url=" https://example.com/item/1 ", notes="Condition\nAgreed price")
        self.assertEqual(record["inputs"]["repairs"], 60)
        self.assertIsInstance(record["inputs"]["hours"], (int, float))
        self.assertEqual(record["listing_url"], "https://example.com/item/1")
        self.assertEqual(record["notes"], "Condition\nAgreed price")
        self.assertNotIn("user_id", record)
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["root_item_id"], record["id"])
        self.assertIsNone(record["parent_item_id"])
        self.assertTrue(record["created_at"].endswith("+00:00"))
        self.assertEqual(datetime.fromisoformat(record["created_at"]).utcoffset().total_seconds(), 0)
        expected = self.client.post("/api/items/analyze", json=body).json()
        self.assertEqual(record["analysis_result"], expected)
        self.assertEqual(self.client.get(f'/api/items/{record["id"]}').json(), record)

    def test_unknown_null_zero_and_nested_defaults_round_trip(self):
        for inputs in [{}, {"repairs": None}, {"repairs": 0}, {"personal_defaults": None},
                       {"personal_defaults": {}}, {"personal_defaults": {"fee_pct": None}},
                       {"personal_defaults": {"fee_pct": 0}},
                       {"hourly_value": None, "personal_defaults": {"hourly_value": 12.34, "fee_pct": 0.143}}]:
            with self.subTest(inputs=inputs):
                record = self.save(inputs)
                self.assertEqual(record["inputs"], inputs)
                read = self.client.get(f'/api/items/{record["id"]}').json()
                self.assertEqual(read["inputs"], inputs)
                self.assertEqual(read["analysis_result"]["status"], "needs_info")
                self.assertIsNone(read["analysis_result"]["low"])
                self.assertEqual(self.client.post("/api/items/analyze", json=read["inputs"]).json(), read["analysis_result"])

    def test_all_statuses_and_random_reopened_inputs(self):
        rng = random.Random(20261005)
        cases = [dresser(0), dresser(100), dresser(100.01), {}, {k: v for k, v in dresser().items() if k != "purchase_price"}]
        cases.append({**dresser(0), "target_profit": 0})
        cases += [{**dresser(), "purchase_price": rng.randrange(10000) / 100,
                   "fee_pct": rng.randrange(1000001) / 1000000,
                   "hours": rng.randrange(1000) / 100} for _ in range(75)]
        statuses = set()
        for inputs in cases:
            record = self.save(inputs)
            statuses.add(record["analysis_result"]["status"])
            reopened = self.client.post("/api/items/analyze", json=record["inputs"]).json()
            self.assertEqual(reopened, record["analysis_result"])
        self.assertEqual(statuses, {"needs_info", "offer_only", "within_budget", "stretch", "skip"})

    def test_subcent_status_and_default_sources_survive(self):
        inputs = {**dresser(1), "resale_low": 1, "resale_high": 1, "repairs": 0,
                  "pickup": 0, "hours": 0, "target_profit": 0, "fee_pct": 0.0001}
        record = self.save(inputs)
        self.assertEqual(record["analysis_result"]["status"], "skip")
        self.assertEqual(record["analysis_result"]["low"]["raw_max_offer"], 1)
        self.assertEqual(record["analysis_result"]["low"]["max_offer"], 0)
        inputs = dresser()
        del inputs["hourly_value"]
        del inputs["contingency_pct"]
        del inputs["fee_pct"]
        inputs["personal_defaults"] = {"fee_pct": 0.075}
        result = self.save(inputs)["analysis_result"]
        self.assertEqual(result["assumptions"]["hourly_value"]["default_origin"], "application")
        self.assertEqual(result["assumptions"]["fee_pct"]["default_origin"], "personal")

    def test_linked_resaves_keep_originals_and_one_root(self):
        first = self.save(notes="Original")
        second = self.save(dresser(40), parent_item_id=first["id"], notes="Revision")
        third = self.save(dresser(100), parent_item_id=second["id"])
        sibling = self.save(dresser(10), parent_item_id=first["id"])
        for record in [first, second, third, sibling]:
            self.assertEqual(record["root_item_id"], first["id"])
            self.assertEqual(self.client.get(f'/api/items/{record["id"]}').json(), record)
        self.assertEqual(third["parent_item_id"], second["id"])
        self.assertEqual(self.save()["parent_item_id"], None)

    def test_reads_return_history_without_running_engine(self):
        record = self.save()
        with patch("app.services.saved_item_service.analyze_item", side_effect=AssertionError("must not recalculate")):
            self.assertEqual(self.client.get(f'/api/items/{record["id"]}').json(), record)
            self.assertEqual(self.client.get('/api/items').json()["items"], [record])

    def test_owner_filter_and_foreign_parent_match_missing_404(self):
        own = self.save()
        other = self.save(user="bob")
        self.assertEqual([r["id"] for r in self.client.get('/api/items').json()["items"]], [own["id"]])
        for item_id in [other["id"], 999999]:
            response = self.client.get(f'/api/items/{item_id}')
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "Item not found."})
            response = self.client.post('/api/items/save', json={"inputs": {}, "parent_item_id": item_id})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.count(), 2)

    def test_missing_invalid_and_expired_auth_match_houses(self):
        del app.dependency_overrides[get_current_user_id]
        requests = [("POST", "/api/items/save", {"inputs": {}}), ("GET", "/api/items", None),
                    ("GET", "/api/items/1", None), ("GET", "/api/deals", None)]
        for method, url, body in requests:
            self.assertEqual(self.client.request(method, url, json=body).status_code, 403)
            with patch("app.auth._jwks", {"keys": []}):
                response = self.client.request(method, url, json=body, headers={"Authorization": "Bearer invalid"})
                self.assertEqual(response.status_code, 401)
                with patch("app.auth.jwt.decode", side_effect=ExpiredSignatureError("expired")):
                    self.assertEqual(self.client.request(method, url, json=body, headers={"Authorization": "Bearer expired"}).status_code, 401)
        self.assertEqual(self.client.post('/api/items/analyze', json={}).status_code, 200)
        self.assertEqual(self.count(), 0)

    def test_forged_result_owner_root_and_unknown_keys_rejected(self):
        for extra in [{"analysis_result": {"status": "within_budget"}}, {"user_id": "bob"},
                      {"root_item_id": 1}, {"id": 1}, {"schema_version": 1}, {"extra": 1}]:
            self.assertEqual(self.client.post('/api/items/save', json={"inputs": {}, **extra}).status_code, 422)
        for inputs in [{"vehicle_context": {}}, {"personal_defaults": {"extra": 1}}, {"repairs": True}, {"repairs": "0"}]:
            self.assertEqual(self.client.post('/api/items/save', json={"inputs": inputs}).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_nonfinite_errors_are_strict_json(self):
        for part in ['"inputs":{"repairs":NaN}', '"inputs":{},"extra":Infinity',
                     '"inputs":{"personal_defaults":{"fee_pct":1e400}}']:
            response = self.client.post('/api/items/save', content='{' + part + '}', headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422)
            json.loads(response.text, parse_constant=lambda value: self.fail(value))

    def test_parent_ids_are_strict_positive_numbers(self):
        for value in [0, -1, True, "1", 1.0]:
            self.assertEqual(self.client.post('/api/items/save', json={"inputs": {}, "parent_item_id": value}).status_code, 422)

    def test_link_validation_and_no_network_fetch(self):
        for url in ["javascript:alert(1)", "data:text/html,test", "ftp://example.com", "//example.com", "https:///item",
                    "https://u:p@example.com", "https://example.com/a b", "https://example.com\\evil", "https://example.com:99999",
                    "https://example.com\n", "\thttps://example.com", "", "   "]:
            with self.subTest(url=url):
                self.assertEqual(self.client.post('/api/items/save', json={"inputs": {}, "listing_url": url}).status_code, 422)
        with patch("httpx.get", side_effect=AssertionError("no URL fetching")):
            for url in ["http://example.com/item", "https://example.com/item?q=1#detail", " https://example.com/item "]:
                self.assertEqual(self.save({}, listing_url=url)["listing_url"], url.strip())

    def test_text_limits_and_notes_remain_plain_text(self):
        prefix = "https://example.com/"
        url = prefix + "a" * (2048 - len(prefix))
        self.assertEqual(self.save({}, listing_url=url, notes="x" * 5000)["listing_url"], url)
        note = '<script>alert("text only")</script>\nCondition'
        self.assertEqual(self.save({}, notes=note)["notes"], note)
        for extra in [{"listing_url": url + "x"}, {"notes": "x" * 5001}, {"notes": 1}, {"listing_url": False}]:
            self.assertEqual(self.client.post('/api/items/save', json={"inputs": {}, **extra}).status_code, 422)

    def test_pagination_is_bounded_owner_scoped_and_ties_are_stable(self):
        ids = [self.save({})["id"] for _ in range(5)]
        self.save({}, user="bob")
        with self.Session() as db:
            db.query(SavedItem).update({SavedItem.created_at: datetime(2026, 10, 5, tzinfo=timezone.utc)})
            db.commit()
        page1 = self.client.get('/api/items?limit=2').json()
        page2 = self.client.get('/api/items?limit=2&offset=2').json()
        page3 = self.client.get('/api/items?limit=2&offset=4').json()
        self.assertEqual([r["id"] for page in [page1, page2, page3] for r in page["items"]], ids[::-1])
        self.assertEqual([p["next_offset"] for p in [page1, page2, page3]], [2, 4, None])
        self.assertEqual((page2["limit"], page2["offset"]), (2, 2))
        self.assertEqual(self.client.get('/api/items').json()["limit"], 50)
        self.assertEqual(self.client.get('/api/items?offset=100').json()["items"], [])
        for query in ['limit=0', 'limit=101', 'offset=-1', 'limit=abc']:
            self.assertEqual(self.client.get('/api/items?' + query).status_code, 422)

    def test_no_update_or_delete_routes(self):
        record = self.save()
        for method in ['PUT', 'PATCH', 'DELETE']:
            self.assertEqual(self.client.request(method, f'/api/items/{record["id"]}', json={}).status_code, 405)

    def test_failed_commit_rolls_back_entire_save(self):
        self.save()
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("failed commit")):
            with self.assertRaisesRegex(RuntimeError, "failed commit"):
                self.save()
        self.assertEqual(self.count(), 1)
        record = self.save()
        self.assertEqual(record["root_item_id"], record["id"])

    def test_deferred_root_sentinel_cannot_be_committed(self):
        with self.Session() as db:
            db.add(SavedItem(user_id="alice", inputs={}, analysis_result={}, root_item_id=0))
            db.flush()
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
        self.assertEqual(self.count(), 0)

    def test_openapi_keeps_public_analyze_and_protected_persistence(self):
        schema = app.openapi()
        paths = schema["paths"]
        self.assertIn("ItemAnalyzeRequest", schema["components"]["schemas"])
        self.assertEqual(schema["components"]["schemas"]["SavedItemResponse"]["properties"]["inputs"]["type"], "object")
        self.assertNotIn("security", paths['/api/items/analyze']['post'])
        for path, verb in [('/api/items/save', 'post'), ('/api/items', 'get'), ('/api/items/{item_id}', 'get')]:
            self.assertTrue(paths[path][verb]['security'])


if __name__ == '__main__':
    unittest.main()
