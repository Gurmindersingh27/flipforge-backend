import base64
import copy
import json
import os
import unittest
import httpx
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.session import get_db
from app.db.models.item_assessment import ItemAssessment, SavedItemAssessment
from app.db.models.saved_item import SavedItem
from app.main import app
from app.services.item_assessment_service import MODEL, usage_cost, call_provider
from app.services.item_repair_catalog import VERSION
from app.services import item_ai_budget_service as budget


def request_body(**changes):
    return {"request_id": str(uuid4()), "description": "Wood chair, scratched seat, they want 20",
            "photos": [{"media_type": "image/png", "data": base64.b64encode(
                b"\x89PNG\r\n\x1a\nfixture").decode()}], **changes}


def provider_response(prices=(65, 75, 90)):
    listings = [dict(title="Used wood dining chair", url=f"https://example.com/chair/{i}", price=p,
                     currency="USD", condition="Good used", comparable=True, single_item=True,
                     market="local_pickup", location="Atlanta") for i, p in enumerate(prices)]
    report = dict(item_name="Wood chair", category="Chair", asking_price=20,
                  repairs=[dict(job_id="sand_seat", reason="Visible scratches on seat")],
                  repair_unknowns=[], listings=listings)
    return dict(stop_reason="end_turn", usage=dict(input_tokens=12000, output_tokens=1000,
                server_tool_use=dict(web_search_requests=2)), content=[dict(type="text", text=json.dumps(report),
                citations=[dict(type="web_search_result_location", url=row["url"],
                                cited_text=f'Used wood chair ${row["price"]}, local pickup.') for row in listings])])


class AssessmentTests(unittest.TestCase):
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
        self.env = patch.dict(os.environ, {"ITEMS_AI_ALLOWED_USER_IDS": "alice,bob",
            "ITEMS_ANTHROPIC_API_KEY": "test-not-real", "ITEMS_ANTHROPIC_WORKSPACE_ID": "wrkspc_test",
            "ITEMS_AI_WORKSPACE_LIMIT_CONFIRMED": "20", "ITEMS_REPAIR_CATALOG_APPROVED": VERSION})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def run_assessment(self, body=None, raw=None):
        with patch("app.services.item_assessment_service.call_provider", return_value=raw or provider_response()) as call:
            response = self.client.post("/api/items/assess", json=body or request_body())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), call

    def test_assessment_has_cited_quartiles_explicit_preset_and_unconfirmed_repairs(self):
        result, call = self.run_assessment()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["actual_cost"], .071)
        evidence = result["result"]
        self.assertEqual((evidence["resale"]["low"], evidence["resale"]["high"]), (70, 82.5))
        self.assertEqual(evidence["analysis_result"]["status"], "needs_info")
        self.assertEqual(evidence["analysis_result"]["missing_inputs"], ["repairs"])
        self.assertEqual(evidence["inputs"]["target_profit"], 30)
        self.assertEqual(evidence["inputs"]["hours"], 0)
        self.assertIn("no charge for your time", evidence["preset"]["label"])
        self.assertFalse(evidence["repair_suggestions"][0]["confirmed"])
        payload = call.call_args.args[0]
        self.assertEqual(payload["model"], MODEL)
        self.assertEqual(payload["tools"], [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}])
        self.assertEqual(payload["max_tokens"], 2500)
        self.assertEqual(call.call_count, 1)

    def test_duplicate_taps_reuse_result_and_changed_payload_conflicts(self):
        body = request_body()
        first, _ = self.run_assessment(body)
        with patch("app.services.item_assessment_service.call_provider") as call:
            again = self.client.post("/api/items/assess", json=body)
            self.assertEqual(again.json(), first)
            self.assertEqual(self.client.post("/api/items/assess", json={**body, "description": "changed"}).status_code, 409)
            self.assertEqual(self.client.post("/api/items/assess", json=body, headers={"X-Test-User": "bob"}).status_code, 409)
            call.assert_not_called()

    def test_not_allowlisted_or_configured_never_calls_or_reserves(self):
        with patch("app.services.item_assessment_service.call_provider") as call:
            self.assertEqual(self.client.post("/api/items/assess", json=request_body(),
                                            headers={"X-Test-User": "stranger"}).status_code, 403)
            with patch.dict(os.environ, {"ITEMS_ANTHROPIC_API_KEY": ""}):
                self.assertEqual(self.client.post("/api/items/assess", json=request_body()).status_code, 503)
                self.assertEqual(self.client.get("/api/items/ai-budget").json()["reason"], "not_configured")
            call.assert_not_called()
        with self.Session() as db:
            self.assertEqual(db.query(ItemAssessment).count(), 0)

    def test_workspace_and_catalog_setup_are_required_before_any_spend(self):
        for setting in ["ITEMS_ANTHROPIC_WORKSPACE_ID", "ITEMS_AI_WORKSPACE_LIMIT_CONFIRMED",
                        "ITEMS_REPAIR_CATALOG_APPROVED"]:
            with patch.dict(os.environ, {setting: ""}), patch("app.services.item_assessment_service.call_provider") as call:
                self.assertEqual(self.client.post('/api/items/assess', json=request_body()).status_code, 503)
                call.assert_not_called()

    def test_draft2_gate_rejects_old_or_near_match_approval_before_reservation(self):
        from app.db.models.item_ai_budget import ItemAIMonth
        self.assertEqual(VERSION, "2026-10-06-draft2")
        for approval in ["2026-10-06-draft1", "2026-10-06-draft2 ", "2026-10-06-DRAFT2"]:
            with self.subTest(approval=approval), patch.dict(os.environ, {"ITEMS_REPAIR_CATALOG_APPROVED": approval}), \
                    patch("app.services.item_assessment_service.call_provider") as call:
                self.assertEqual(self.client.post('/api/items/assess', json=request_body()).status_code, 503)
                self.assertEqual(self.client.get('/api/items/ai-budget').json()["reason"], "not_configured")
                call.assert_not_called()
        with self.Session() as db:
            self.assertEqual(db.query(ItemAssessment).count(), 0)
            self.assertEqual(db.query(ItemAIMonth).count(), 0)

    def test_prompt_contains_every_job_scope_without_prices_or_changed_provider_caps(self):
        _, call = self.run_assessment()
        payload = call.call_args.args[0]
        prompt = payload["system"]
        catalog, _ = json.JSONDecoder().raw_decode(prompt.split("Select repair job IDs from this catalog: ", 1)[1])
        scopes = {job["job_id"]: job["scope"] for job in catalog}
        self.assertEqual(set(scopes), {"clean", "scratch_touchup", "sand_seat", "refinish_top", "paint_chair",
                                     "paint_dresser", "replace_knobs", "glue_joint", "seat_fabric", "replace_glides"})
        for job in catalog:
            self.assertEqual(set(job), {"job_id", "label", "scope"})
            self.assertTrue(job["scope"])
        for job in ("sand_seat", "refinish_top"):
            self.assertIn("This surface only", scopes[job])
            self.assertIn("stripping", scopes[job])
            self.assertIn("veneer damage", scopes[job])
        self.assertIn("veneer damage", scopes["paint_dresser"])
        self.assertIn("routine cleaning", scopes["paint_chair"])
        self.assertIn("reuses sound foam and base", scopes["seat_fabric"])
        self.assertIn("excludes replacement wooden feet", scopes["replace_glides"])
        self.assertIn("Return every applicable job; the server handles included work", prompt)
        self.assertIn("Put work outside these scopes", prompt)
        self.assertIn("Never calculate resale ranges, offers, profits, costs, or buy/pass decisions", prompt)
        self.assertEqual(payload["max_tokens"], 2500)
        self.assertEqual(payload["tools"], [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}])
        self.assertEqual(payload["model"], "claude-sonnet-4-5-20250929")

    def test_overlap_returns_unknown_empty_budget_and_saves_one_custom_total_with_evidence(self):
        raw = provider_response()
        report = json.loads(raw["content"][0]["text"])
        report["repairs"] = [dict(job_id=job, reason=f"Visible {job}")
                             for job in ("clean", "paint_dresser", "refinish_top")]
        raw["content"][0]["text"] = json.dumps(report)
        assessed, _ = self.run_assessment(raw=raw)
        evidence = assessed["result"]
        self.assertEqual(evidence["repair_catalog_version"], "2026-10-06-draft2")
        self.assertEqual(len(evidence["repair_unknowns"]), 1)
        self.assertIn("enter one total repair budget", evidence["repair_unknowns"][0])
        # The frontend restores these inputs and follows repair_unknowns straight
        # to an empty custom-budget field, never the $80 suggestion-sum button.
        self.assertIsNone(evidence["inputs"]["repairs"])
        self.assertEqual(evidence["analysis_result"]["status"], "needs_info")
        self.assertEqual(evidence["analysis_result"]["missing_inputs"], ["repairs"])
        self.assertIsNone(evidence["analysis_result"]["low"])
        rows = evidence["repair_suggestions"]
        self.assertEqual([r["materials_cost"] for r in rows], [0, 50, 30])
        self.assertIn("Included in Prep and paint a small dresser", rows[0]["label"])
        self.assertEqual([r["reason"] for r in rows], ["Visible clean", "Visible paint_dresser", "Visible refinish_top"])
        body = dict(inputs={**evidence["inputs"], "repairs": 72}, assessment_id=assessed["id"],
                    assessment_confirmation=dict(repairs=[dict(job_id="custom", materials_cost=72)],
                                                 resale_source="assessment", preset_acknowledged=True))
        with patch("app.services.item_assessment_service.call_provider") as call:
            saved = self.client.post('/api/items/save', json=body)
            self.assertEqual(saved.status_code, 201, saved.text)
            saved = saved.json()
            self.assertEqual(saved["inputs"]["repairs"], 72)
            self.assertEqual(saved["assessment"]["evidence"], evidence)
            self.assertEqual(saved["assessment"]["confirmation"]["repairs"], [{"job_id": "custom", "materials_cost": 72}])
            self.assertEqual(self.client.get(f'/api/items/{saved["id"]}').json(), saved)
            body["inputs"]["repairs"] = 80
            self.assertEqual(self.client.post('/api/items/save', json=body).status_code, 422)
            call.assert_not_called()

    def test_http_uses_dedicated_key_and_only_one_post_without_provider_retry(self):
        raw = provider_response()
        response = httpx.Response(200, json=raw, headers={"anthropic-workspace-id": "wrkspc_test"},
                                  request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        with patch("app.services.item_assessment_service.httpx.Client") as client:
            post = client.return_value.__enter__.return_value.post
            post.return_value = response
            self.assertEqual(call_provider({"model": MODEL}), raw)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "test-not-real")
            self.assertNotIn("follow_redirects", client.call_args.kwargs)
            post.return_value = httpx.Response(429, request=response.request)
            with self.assertRaises(httpx.HTTPStatusError):
                call_provider({"model": MODEL})
            self.assertEqual(post.call_count, 2)

    def test_workspace_mismatch_is_not_accepted(self):
        response = httpx.Response(200, json=provider_response(), headers={"anthropic-workspace-id": "wrong"},
                                  request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        with patch("app.services.item_assessment_service.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            with self.assertRaisesRegex(ValueError, "workspace mismatch"):
                call_provider({"model": MODEL})

    def test_nonfinite_and_nested_extras_return_strict_json_before_spending(self):
        for changes in [{"asking_price": float("nan")}, {"extra": float("inf")},
                        {"photos": [{**request_body()["photos"][0], "extra": float("inf")}]}]:
            response = self.client.post('/api/items/assess', content=json.dumps(request_body(**changes)),
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422)
            json.loads(response.text, parse_constant=lambda value: self.fail(value))

    def test_public_calculator_and_manual_save_work_when_budget_is_paused(self):
        from app.db.models.item_ai_budget import ItemAIMonth
        from app.services.item_ai_budget_service import month_key
        with self.Session() as db:
            db.add(ItemAIMonth(month=month_key(), spent_micros=20_100_000, held_micros=0))
            db.commit()
        with patch("app.services.item_assessment_service.call_provider") as call:
            self.assertEqual(self.client.post('/api/items/assess', json=request_body()).status_code, 429)
            call.assert_not_called()
        status = self.client.get('/api/items/ai-budget').json()
        self.assertEqual(status["reason"], "budget_paused")
        self.assertIn("Enter what you think", status["message"])
        self.assertEqual(self.client.post('/api/items/analyze', json={}).status_code, 200)
        self.assertEqual(self.client.post('/api/items/save', json={"inputs": {}}).status_code, 201)

    def test_new_routes_have_response_schemas_and_existing_analyze_remains_public(self):
        schema = app.openapi()
        for path in ["/api/items/assess", "/api/items/assessments/{assessment_id}", "/api/items/ai-budget"]:
            operation = next(iter(schema["paths"][path].values()))
            self.assertIn("security", operation)
            self.assertIn("$ref", operation["responses"]["200"]["content"]["application/json"]["schema"])
        self.assertNotIn("security", schema["paths"]["/api/items/analyze"]["post"])

    def test_missing_and_invalid_auth_match_houses(self):
        del app.dependency_overrides[get_current_user_id]
        for method, path, body in [("POST", "/api/items/assess", request_body()),
                                  ("GET", "/api/items/ai-budget", None),
                                  ("GET", f"/api/items/assessments/{uuid4()}", None)]:
            self.assertEqual(self.client.request(method, path, json=body).status_code, 403)
            with patch("app.auth._jwks", {"keys": []}):
                self.assertEqual(self.client.request(method, path, json=body,
                    headers={"Authorization": "Bearer invalid"}).status_code, 401)

    def test_ownership_read_404_and_no_photos_or_description_persisted(self):
        result, _ = self.run_assessment()
        self.assertEqual(self.client.get('/api/items/assessments/' + result["id"]).json(), result)
        missing = self.client.get(f'/api/items/assessments/{uuid4()}')
        other = self.client.get('/api/items/assessments/' + result["id"], headers={"X-Test-User": "bob"})
        self.assertEqual((missing.status_code, missing.json()), (other.status_code, other.json()))
        with self.Session() as db:
            record = db.get(ItemAssessment, result["id"])
            persisted = json.dumps({column.name: str(getattr(record, column.name)) for column in record.__table__.columns})
            self.assertNotIn(request_body()["photos"][0]["data"], persisted)
            self.assertNotIn("Wood chair, scratched seat, they want 20", persisted)

    def test_input_boundaries_unknowns_and_bad_photos_before_spend(self):
        cases = [dict(description="x" * 501), dict(description="x\x00"), dict(description="\ud800"),
                 dict(photos=[]), dict(photos=request_body()["photos"] * 4), dict(location="x" * 101),
                 dict(asking_price=True), dict(asking_price="20"), dict(asking_price=-.001),
                 dict(photos=[{"media_type": "image/png", "data": "oops"}]),
                 dict(photos=[{**request_body()["photos"][0], "extra": 1}]), dict(user_id="bob")]
        with patch("app.services.item_assessment_service.call_provider") as call:
            for changes in cases:
                response = self.client.post('/api/items/assess', content=json.dumps(request_body(**changes)),
                                            headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
            call.assert_not_called()

    def test_maximum_description_and_photo_count_are_accepted(self):
        result, _ = self.run_assessment(request_body(description="x" * 500, photos=request_body()["photos"] * 3))
        self.assertEqual(result["status"], "completed")

    def test_asking_entered_zero_null_and_normalization_override_extraction(self):
        for price, expected in [(0, 0), (None, None), (20.005, 20.01)]:
            result, _ = self.run_assessment(request_body(asking_price=price))
            self.assertEqual(result["result"]["asking_price"], expected)
            self.assertEqual(result["result"]["asking_price_source"], "user_entered")

    def test_short_search_has_manual_fallback_without_invented_prices(self):
        result, _ = self.run_assessment(raw=provider_response((65, 90)))
        self.assertIsNone(result["result"]["resale"])
        self.assertIn("What do you think it'd sell for?", result["result"]["questions"])
        self.assertIsNone(result["result"]["inputs"]["resale_low"])

    def test_timeout_holds_gate_no_retry_and_manual_still_works(self):
        with patch("app.services.item_assessment_service.call_provider", side_effect=TimeoutError) as call:
            response = self.client.post('/api/items/assess', json=request_body()).json()
            self.assertEqual(response["status"], "uncertain")
            self.assertEqual(self.client.post('/api/items/assess', json=request_body()).status_code, 409)
            self.assertEqual(call.call_count, 1)
        status = self.client.get('/api/items/ai-budget').json()
        self.assertEqual((status["reserved"], status["reason"]), (.5, "assessment_busy"))
        self.assertEqual(self.client.post('/api/items/analyze', json={}).status_code, 200)

    def test_provider_http_errors_settle_zero_and_next_request_can_succeed(self):
        for code in (400, 401, 402, 403, 404, 413, 429, 500, 504, 529):
            with self.subTest(http_status=code):
                body = request_body()
                error = httpx.Response(code, json={"error": {"message": "private provider detail"}},
                    request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
                # Exercise raise_for_status in the real HTTP adapter, no paid call.
                with patch("app.services.item_assessment_service.httpx.Client") as client:
                    post = client.return_value.__enter__.return_value.post
                    post.return_value = error
                    response = self.client.post('/api/items/assess', json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertEqual((result["status"], result["actual_cost"]), ("failed", 0))
                    self.assertEqual(result["failure_code"], "provider_error")
                    self.assertIn("AI couldn't finish", result["message"])
                    self.assertNotIn("private provider detail", response.text)
                    self.assertEqual(self.client.post('/api/items/assess', json=body).json(), result)
                    self.assertEqual(post.call_count, 1)  # Failed UUIDs are not retried.
                status = self.client.get('/api/items/ai-budget').json()
                self.assertEqual((status["spent"], status["reserved"], status["available"]), (0, 0, True))
        result, call = self.run_assessment()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(call.call_count, 1)

    def test_timeout_recovers_after_grace_without_paid_retry(self):
        start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        body = request_body()
        with patch.object(budget, "utc_now", return_value=start):
            with patch("app.services.item_assessment_service.call_provider", side_effect=httpx.ReadTimeout("no answer")) as call:
                first = self.client.post('/api/items/assess', json=body).json()
                self.assertEqual(first["status"], "uncertain")
                self.assertEqual(call.call_count, 1)
        with patch("app.services.item_assessment_service.call_provider") as call:
            with patch.object(budget, "utc_now", return_value=start + timedelta(seconds=599)):
                self.assertEqual(self.client.get('/api/items/ai-budget').json()["reason"], "assessment_busy")
            with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
                recovered = self.client.get('/api/items/assessments/' + first["id"]).json()
                self.assertEqual(recovered["status"], "failed")
                self.assertEqual(recovered["failure_code"], "cost_assumed_after_grace")
                self.assertIsNone(recovered["actual_cost"])  # $0.50 is an allowance, not measured usage.
                self.assertEqual(self.client.post('/api/items/assess', json=body).json(), recovered)
                status = self.client.get('/api/items/ai-budget').json()
                self.assertEqual((status["spent"], status["reserved"], status["available"]), (.5, 0, True))
            call.assert_not_called()
        with patch.object(budget, "utc_now", return_value=start + budget.GRACE):
            result, _ = self.run_assessment()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.client.get('/api/items/ai-budget').json()["spent"], .57)

    def test_bad_json_pause_and_token_stop_settle_usage_without_retry(self):
        for stop in ["pause_turn", "max_tokens", "end_turn"]:
            raw = provider_response()
            raw["stop_reason"] = stop
            raw["content"][0]["text"] = "not json"
            result, call = self.run_assessment(raw=raw)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["actual_cost"], .071)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(self.client.get('/api/items/ai-budget').json()["reserved"], 0)

    def test_unknown_usage_retains_reservation(self):
        raw = provider_response()
        del raw["usage"]["input_tokens"]
        result, _ = self.run_assessment(raw=raw)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["actual_cost"])

    def test_cost_accounts_for_real_over_hold_usage(self):
        raw = provider_response()
        raw["usage"]["input_tokens"] = 200000
        result, _ = self.run_assessment(raw=raw)
        self.assertEqual(result["actual_cost"], .635)
        self.assertEqual(self.client.get('/api/items/ai-budget').json()["spent"], .64)

    def test_usage_rejects_invalid_types_and_unexpected_cache_charges(self):
        for change in [{"input_tokens": True}, {"output_tokens": -1}, {"cache_creation_input_tokens": 10}]:
            raw = provider_response()
            raw["usage"].update(change)
            with self.assertRaises(ValueError):
                usage_cost(raw)

    def save_body(self, assessed):
        return dict(inputs={**assessed["result"]["inputs"], "repairs": 15}, assessment_id=assessed["id"],
                    assessment_confirmation=dict(repairs=[dict(job_id="sand_seat", materials_cost=15)],
                        resale_source="assessment", preset_acknowledged=True))

    def test_draft1_assessment_and_saved_versions_keep_original_prices_and_evidence(self):
        # Simulate a pre-upgrade assessment and save. Draft1 charged all three
        # jobs (5 + 35 + 25), had no scope labels and no overlap unknown.
        legacy_rows = [dict(job_id=job, label=label, materials_cost=amount,
                            reason=f"Legacy observation for {job}", confirmed=False)
                       for job, label, amount in [
                           ("clean", "Clean and degrease", 5),
                           ("paint_dresser", "Prep and paint a small dresser", 35),
                           ("refinish_top", "Sand and refinish a small top", 25)]]
        request = request_body()
        with patch("app.services.item_assessment_service.VERSION", "2026-10-06-draft1"), \
                patch.dict(os.environ, {"ITEMS_REPAIR_CATALOG_APPROVED": "2026-10-06-draft1"}), \
                patch("app.services.item_assessment_service.suggestions", return_value=(legacy_rows, [])):
            assessed, _ = self.run_assessment(request)
            body = dict(inputs={**assessed["result"]["inputs"], "repairs": 65}, assessment_id=assessed["id"],
                        assessment_confirmation=dict(
                            repairs=[{key: row[key] for key in ("job_id", "materials_cost")} for row in legacy_rows],
                            resale_source="assessment", preset_acknowledged=True))
            response = self.client.post('/api/items/save', json=body)
            self.assertEqual(response.status_code, 201, response.text)
            original = response.json()
        self.assertEqual(assessed["result"]["repair_catalog_version"], "2026-10-06-draft1")
        with patch("app.services.item_assessment_service.call_provider") as call:
            self.assertEqual(self.client.get('/api/items/assessments/' + assessed["id"]).json(), assessed)
            self.assertEqual(self.client.post('/api/items/assess', json=request).json(), assessed)
            self.assertEqual(self.client.get(f'/api/items/{original["id"]}').json(), original)
            body.update(parent_item_id=original["id"], notes="Notes after catalog upgrade")
            response = self.client.post('/api/items/save', json=body)
            self.assertEqual(response.status_code, 201, response.text)
            child = response.json()
            self.assertEqual(child["root_item_id"], original["id"])
            self.assertEqual(child["inputs"]["repairs"], 65)
            self.assertEqual(child["analysis_result"], original["analysis_result"])
            self.assertEqual(child["assessment"], original["assessment"])
            self.assertEqual(child["assessment"]["evidence"]["repair_suggestions"], legacy_rows)
            self.assertEqual(child["assessment"]["evidence"]["repair_unknowns"], [])
            self.assertEqual(self.client.get(f'/api/items/{original["id"]}').json(), original)
            self.assertEqual(self.client.get(f'/api/items/{child["id"]}').json(), child)
            call.assert_not_called()

    def test_save_reopen_evidence_and_recalculate_without_another_paid_call(self):
        assessed, _ = self.run_assessment()
        body = self.save_body(assessed)
        with patch("app.services.item_assessment_service.call_provider") as call:
            saved = self.client.post('/api/items/save', json=body)
            self.assertEqual(saved.status_code, 201, saved.text)
            saved = saved.json()
            self.assertEqual(saved["assessment"]["assessment_id"], assessed["id"])
            self.assertEqual(saved["assessment"]["evidence"]["listings"], assessed["result"]["listings"])
            self.assertEqual(self.client.get(f'/api/items/{saved["id"]}').json(), saved)
            analysis = self.client.post('/api/items/analyze', json=saved["inputs"]).json()
            self.assertEqual(analysis, saved["analysis_result"])
            self.assertEqual(analysis["status"], "within_budget")
            self.assertEqual(analysis["low"]["max_offer"], 22)
            self.assertEqual(analysis["low"]["cash_left"], 32.75)
            self.assertEqual(analysis["low"]["profit_after_time"], 32.75)
            body.update(parent_item_id=saved["id"], notes="Only notes changed")
            child = self.client.post('/api/items/save', json=body).json()
            self.assertEqual(child["root_item_id"], saved["id"])
            self.assertEqual(child["analysis_result"], saved["analysis_result"])
            call.assert_not_called()

    def test_confirmation_and_price_provenance_cannot_be_forged(self):
        assessed, _ = self.run_assessment()
        body = self.save_body(assessed)
        cases = []
        for key, value in [("preset_acknowledged", False), ("repairs", []),
                           ("repairs", [dict(job_id="invented", materials_cost=15)])]:
            case = copy.deepcopy(body)
            case["assessment_confirmation"][key] = value
            cases.append(case)
        cases.append({**body, "inputs": {**body["inputs"], "resale_low": 71}})
        cases.append({**body, "assessment_confirmation": None})
        cases.append({**body, "assessment_id": None})
        for case in cases:
            self.assertEqual(self.client.post('/api/items/save', json=case).status_code, 422)
        self.assertEqual(self.client.post('/api/items/save', json=body, headers={"X-Test-User": "bob"}).status_code, 404)
        with self.Session() as db:
            self.assertEqual(db.query(SavedItem).count(), 0)

    def test_assessment_save_rolls_back_both_tables(self):
        assessed, _ = self.run_assessment()
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/items/save', json=self.save_body(assessed))
        with self.Session() as db:
            self.assertEqual(db.query(SavedItem).count(), 0)
            self.assertEqual(db.query(SavedItemAssessment).count(), 0)


if __name__ == "__main__":
    unittest.main()
