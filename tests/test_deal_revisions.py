import copy
import unittest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.auth import get_current_user_id
from app.db.base import Base
from app.db.session import get_db
from app.db.models.saved_deal import SavedDeal
from app.models import RehabScope
from app.services.deal_revision_service import scope_total


def payload(amount=50000, months=6):
    def point(value):
        return {"value": value, "confidence": "HIGH", "source": "manual"}
    return {
        "address": "Pilot fixture", "draft_input": {
            "source": "manual", "purchase_price": point(150000), "arv": point(270000),
            "rehab_budget": point(amount), "est_monthly_rent": point(None),
            "holding_months": months,
        },
        "analysis_result": {"net_profit": 999999999},
        "rehab_scope": {"version": 1, "items": [{
            "id": "scope-1", "category": "General", "quantity": 1, "unit_cost": amount,
            "basis": "allowance", "source": "Owner allowance",
        }], "contingency_pct": 0},
    }


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        # Simulate an existing deployment, then add the companion table.
        SavedDeal.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(SavedDeal(user_id="alice", address="Legacy", analysis_result={"net_profit": 123}))
            db.commit()
        Base.metadata.create_all(self.engine)
        def db_override():
            with self.Session() as db:
                yield db
        def user_override(x_test_user: str = Header(default="alice")):
            return x_test_user
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user_id] = user_override
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def save(self, body):
        response = self.client.post("/api/deals/save", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_full_save_reopen_quote_revise(self):
        original = self.save(payload())
        self.assertNotEqual(original["analysis_result"]["net_profit"], 999999999)
        body = payload(67000, 8)
        body["parent_deal_id"] = original["id"]
        body["revision_note"] = "Walkthrough quotes + two months"
        body["rehab_scope"]["items"][0].update(basis="quote", source="Contractor A", quote_date="2026-09-10")
        revised = self.save(body)
        self.assertEqual(self.client.get(f'/api/deals/{original["id"]}').json(), original)
        self.assertEqual(self.client.get(f'/api/deals/{revised["id"]}').json(), revised)
        self.assertEqual(revised["parent_deal_id"], original["id"])
        self.assertLess(revised["analysis_result"]["max_safe_offer"], original["analysis_result"]["max_safe_offer"])
        self.assertLess(revised["analysis_result"]["net_profit"], original["analysis_result"]["net_profit"])

    def test_legacy_survives_and_can_be_revised(self):
        legacy = self.client.get("/api/deals/1").json()
        self.assertEqual(legacy["analysis_result"], {"net_profit": 123})
        self.assertIsNone(legacy["rehab_scope"])
        body = payload()
        body["parent_deal_id"] = 1
        self.save(body)
        self.assertEqual(self.client.get("/api/deals/1").json(), legacy)

    def test_cross_user_reads_and_parent_links_are_denied(self):
        record = self.save(payload())
        self.assertEqual(self.client.get(f'/api/deals/{record["id"]}', headers={"X-Test-User": "bob"}).status_code, 404)
        body = payload()
        body["parent_deal_id"] = record["id"]
        self.assertEqual(self.client.post("/api/deals/save", json=body, headers={"X-Test-User": "bob"}).status_code, 404)
        self.assertEqual(self.client.get("/api/deals", headers={"X-Test-User": "bob"}).json(), [])

    def test_scope_mismatch_rejected_without_partial_save(self):
        body = payload()
        body["rehab_scope"]["items"][0]["unit_cost"] = 1
        self.assertEqual(self.client.post("/api/deals/save", json=body).status_code, 422)
        self.assertEqual(len(self.client.get("/api/deals").json()), 1)

    def test_quote_requires_provenance_and_valid_date(self):
        for changes in ({"basis": "quote"}, {"basis": "quote", "source": "A", "quote_date": "2026-02-30"}):
            body = payload()
            body["rehab_scope"]["items"][0].update(changes)
            self.assertEqual(self.client.post("/api/deals/save", json=body).status_code, 422)

    def test_invalid_scope_and_incomplete_draft_rejected(self):
        for field, value in [("quantity", -1), ("unit_cost", -1)]:
            body = payload()
            body["rehab_scope"]["items"][0][field] = value
            self.assertEqual(self.client.post("/api/deals/save", json=body).status_code, 422)
        body = payload()
        body["draft_input"] = None
        self.assertEqual(self.client.post("/api/deals/save", json=body).status_code, 422)

    def test_duplicate_items_rejected(self):
        body = payload()
        body["rehab_scope"]["items"].append(copy.deepcopy(body["rehab_scope"]["items"][0]))
        self.assertEqual(self.client.post("/api/deals/save", json=body).status_code, 422)

    def test_contingency_applied_once_and_decimal_rounding(self):
        body = payload()
        body["rehab_scope"]["contingency_pct"] = 0.15
        body["draft_input"]["rehab_budget"]["value"] = 57500
        saved = self.save(body)
        self.assertEqual(saved["draft_input"]["rehab_budget"]["value"], 57500)
        body["rehab_scope"]["items"][0].update(quantity=3, unit_cost=0.335)
        body["rehab_scope"]["contingency_pct"] = 0.5
        self.assertEqual(scope_total(RehabScope.model_validate(body["rehab_scope"])), 2)

    def test_missing_parent_rejected(self):
        body = payload()
        body["parent_deal_id"] = 9999
        self.assertEqual(self.client.post("/api/deals/save", json=body).status_code, 404)

    def test_auth_still_required(self):
        del app.dependency_overrides[get_current_user_id]
        self.assertEqual(self.client.post("/api/deals/save", json=payload()).status_code, 403)

    def test_locked_engine_scenarios(self):
        for price, arv, rehab, verdict, offer, profit in [
            (185000, 240000, 45000, "PASS", 137700, -25100),
            (135000, 240000, 45000, "BUY", 137700, 28650),
            (200000, 345000, 50000, "BUY", 212300, 50150),
        ]:
            result = self.client.post("/api/analyze", json={"purchase_price": price, "arv": arv, "rehab_budget": rehab}).json()
            self.assertEqual((result["overall_verdict"], result["max_safe_offer"], result["net_profit"]), (verdict, offer, profit))


if __name__ == "__main__":
    unittest.main()
