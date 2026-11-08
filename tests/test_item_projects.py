"""Projects use existing snapshots; all examples and database writes are local."""
import random
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from unittest.mock import patch
from uuid import uuid4

from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.models.item_assessment import ItemAssessment, SavedItemAssessment
from app.db.models.item_outcome import ItemOutcomeRevision
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.item_analysis_engine import analyze_item
from app.item_models import ItemAnalyzeRequest
from app.item_outcome_models import CASH_FIELDS, ItemOutcomeFacts
from app.main import app
from app.saved_item_models import SavedItemResponse
from app.services.item_project_service import compare_outcome
from app.services.item_outcome_service import _latest
from app.services.saved_item_service import save_item
from app.saved_item_models import SaveItemRequest


def estimate_inputs(**changes):
    return dict(item_name="Dresser", purchase_price=60, resale_low=180, resale_high=240,
                repairs=30, pickup=10, delivery=0, storage=0, fee_pct=0, fee_fixed=0,
                hours=4, hourly_value=20, target_profit=50) | changes


def actual_inputs(**changes):
    return dict(status="sold", purchase_price=45, materials=38, hired_labor=0,
                pickup=12, delivery=0, storage=0, selling_fees=0, other_expenses=5,
                sale_price=160, hours=6, hourly_value=20,
                purchased_on="2026-10-01", listed_on="2026-10-04", sold_on="2026-10-09") | changes


def snapshot(**changes):
    inputs = estimate_inputs(**changes)
    result = analyze_item(ItemAnalyzeRequest.model_validate(inputs)).model_dump(mode="json")
    return SavedItemResponse(id=1, root_item_id=1, parent_item_id=None, schema_version=1,
                             created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), inputs=inputs,
                             analysis_result=result, listing_url=None, notes="Original notes")


def comparison(estimate=None, **changes):
    return compare_outcome(estimate or snapshot(), ItemOutcomeFacts.model_validate(actual_inputs(**changes)))


class OutcomeComparisonTests(unittest.TestCase):
    def test_dresser_original_adjusted_actual_and_explanations(self):
        saved = snapshot()
        original = saved.model_dump(mode="json")
        result = comparison(saved).model_dump(mode="json")
        self.assertEqual(result["original_estimate"], original)
        self.assertEqual(original["analysis_result"]["status"], "stretch")
        self.assertEqual([original["analysis_result"][key]["cash_left"] for key in ("low", "high")], [75.5, 135.5])
        expected = result["expected_at_purchase_price"]
        self.assertEqual([expected[key]["cash_left"] for key in ("low", "high")], [90.5, 150.5])
        self.assertEqual([expected[key]["profit_after_time"] for key in ("low", "high")], [10.5, 70.5])
        self.assertEqual([result["actual"][key] for key in ("cash_invested", "cash_profit", "profit_after_time")], [100, 60, -60])
        cash = result["cash_explanation"]
        effects = {effect["key"]: effect["amount"] for effect in cash["effects"]}
        self.assertEqual(effects, dict(sale_price=-20, repairs=-3.5, pickup=-2, delivery=0,
                                      storage=0, selling_fees=0, other_expenses=-5))
        self.assertEqual((cash["baseline_profit"], cash["total_effect"], cash["actual_profit"]), (90.5, -30.5, 60))
        self.assertEqual(cash["headline"], "Most of the gap came from selling $20.00 under the low estimate.")
        time = result["after_time_explanation"]
        self.assertEqual((time["baseline_profit"], time["total_effect"], time["actual_profit"]), (10.5, -70.5, -60))
        self.assertEqual(time["dominant_effect"], "time")
        self.assertEqual(time["effects"][-1]["amount"], -40)
        self.assertEqual(saved.model_dump(mode="json"), original)

    def test_each_missing_cash_field_withholds_totals_but_keeps_recorded_lines(self):
        for field in CASH_FIELDS:
            with self.subTest(field=field):
                result = comparison(**{field: None})
                self.assertIsNone(result.cash_explanation)
                self.assertIsNone(result.after_time_explanation)
                self.assertIsNone(result.actual.cash_profit)
                self.assertIn(field, result.actual.missing_fields["cash_profit"])
                self.assertTrue(any(field in line.missing_fields and line.actual is None for line in result.lines))
                self.assertEqual(next(line for line in result.lines if line.key == "sale_price").actual, 160)
                if field != "purchase_price":
                    self.assertIsNotNone(result.expected_at_purchase_price)
                    self.assertEqual(next(line for line in result.lines if line.key == "purchase_price").actual, 45)

    def test_missing_purchase_does_not_fall_back_to_estimate_price(self):
        result = comparison(purchase_price=None)
        self.assertIsNone(result.expected_at_purchase_price)
        self.assertEqual(result.expected_missing_fields, ["purchase_price"])
        self.assertIsNone(result.cash_explanation)
        self.assertEqual(result.original_estimate.analysis_result.assumptions.purchase_price.value, 60)

    def test_missing_time_does_not_block_cash_and_dates_have_no_forecast(self):
        for field in ("hours", "hourly_value"):
            result = comparison(**{field: None})
            self.assertIsNotNone(result.cash_explanation)
            self.assertIsNone(result.after_time_explanation)
        result = comparison(purchased_on=None)
        self.assertIsNone(result.actual.days_held)
        self.assertEqual(result.actual.days_to_sell, 5)
        self.assertFalse(any("days" in line.key for line in result.lines))

    def test_bought_listed_and_unknown_sale_do_not_claim_profit(self):
        for status in ("bought", "listed", "sold"):
            result = comparison(status=status, sale_price=None, sold_on=None,
                                listed_on=None if status == "bought" else "2026-10-04")
            self.assertIsNone(result.cash_explanation)
            self.assertIsNone(result.after_time_explanation)
            self.assertEqual(result.actual.cash_invested, 100)

    def test_in_range_and_boundaries_use_actual_sale_for_baseline(self):
        for sale in (180, 200, 240):
            with self.subTest(sale=sale):
                explanation = comparison(sale_price=sale).cash_explanation
                self.assertEqual(explanation.baseline_sale_price, sale)
                self.assertEqual(explanation.effects[0].amount, 0)
                self.assertEqual(explanation.baseline_profit, Decimal(sale) - Decimal("89.5"))

    def test_percentage_and_fixed_fees_reconcile_below_inside_above_range(self):
        saved = snapshot(fee_pct=0.1, fee_fixed=2, delivery=7, storage=3)
        for sale, baseline, sale_effect, fee_effect, profit in (
                (160, 60.5, -18, 3, 40), (200, 78.5, 0, 7, 80), (260, 114.5, 18, 13, 140)):
            with self.subTest(sale=sale):
                result = comparison(saved, sale_price=sale, selling_fees=15, delivery=4, storage=1)
                explanation = result.cash_explanation
                effects = {effect.key: effect.amount for effect in explanation.effects}
                self.assertEqual(explanation.baseline_profit, Decimal(str(baseline)))
                self.assertEqual(explanation.actual_profit, profit)
                self.assertEqual(effects["sale_price"], sale_effect)
                self.assertEqual(effects["selling_fees"], fee_effect)
                self.assertEqual(effects["delivery"], 3)
                self.assertEqual(effects["storage"], 2)
                self.assertEqual(explanation.baseline_profit + sum(effects.values()), profit)

    def test_purchase_savings_are_not_an_estimate_error(self):
        expensive = comparison(purchase_price=45)
        free = comparison(purchase_price=0)
        self.assertEqual(free.cash_explanation.total_effect, expensive.cash_explanation.total_effect)
        self.assertEqual(free.actual.cash_profit - expensive.actual.cash_profit, 45)
        self.assertFalse(any(effect.key == "purchase_price" for effect in free.cash_explanation.effects))
        purchase = next(line for line in free.lines if line.key == "purchase_price")
        self.assertEqual(purchase.actual, 0)
        self.assertIsNone(purchase.difference_low)

    def test_resolved_defaults_and_original_history_survive_default_change(self):
        saved = snapshot()
        before = comparison(saved).model_dump(mode="json")
        with patch("app.item_analysis_engine.APPLICATION_DEFAULTS", {"contingency_pct": Decimal("0.99"), "hourly_value": Decimal("999")}):
            self.assertEqual(comparison(saved).model_dump(mode="json"), before)
        self.assertEqual(before["original_estimate"]["analysis_result"]["assumptions"]["contingency_pct"]["source"], "default")

    def test_offer_only_stays_original_and_actual_purchase_enables_comparison(self):
        result = comparison(snapshot(purchase_price=None))
        self.assertEqual(result.original_estimate.analysis_result.status, "offer_only")
        self.assertIsNone(result.original_estimate.analysis_result.low.cash_left)
        self.assertEqual(result.expected_at_purchase_price.low.cash_left, Decimal("90.5"))

    def test_resale_sources_are_preserved_without_upgrading_manual_estimates(self):
        self.assertEqual(comparison().resale_source, "user_estimate")
        for source in ("assessment", "national_reference", "user_estimate", "future_source"):
            saved = snapshot().model_copy(update={"assessment": {"confirmation": {"resale_source": source}}})
            self.assertEqual(comparison(saved).resale_source, source if source != "future_source" else "unknown")
        self.assertEqual(comparison(snapshot().model_copy(update={"assessment": {}})).resale_source, "unknown")

    def test_headline_requires_dominant_nonzero_effect_and_excludes_ties(self):
        # Opposing effects cancel: no net gap to explain.
        zero = comparison(materials=14.5, pickup=10, other_expenses=0).cash_explanation
        self.assertEqual(zero.total_effect, 0)
        self.assertIsNone(zero.headline)
        # Equal losses must not be ranked by list order.
        tie = comparison(materials=54.5, pickup=10, other_expenses=0).cash_explanation
        self.assertIsNone(tie.dominant_effect)
        # No single effect reaches half of the absolute changes.
        spread = comparison(materials=54.5, pickup=30, other_expenses=20).cash_explanation
        self.assertIsNone(spread.headline)
        # Above-range sales can explain gains too.
        gain = comparison(sale_price=300, materials=34.5, pickup=10, other_expenses=0).cash_explanation
        self.assertEqual(gain.dominant_effect, "sale_price")

    def test_negative_and_zero_actual_profits_are_values_not_missing(self):
        for sale, expected in ((0, -100), (100, 0)):
            result = comparison(sale_price=sale)
            self.assertEqual(result.actual.cash_profit, expected)
            self.assertIsNotNone(result.cash_explanation)

    def test_cent_rounding_reconciles_serialized_totals_even_with_offsets(self):
        rng = random.Random(112)
        saw_rounding = False
        for _ in range(200):
            saved = snapshot(repairs=rng.randrange(0, 10000) / 100,
                             fee_pct=rng.choice([0, 0.1, 0.123456, 1]), fee_fixed=0.13,
                             contingency_pct=0.15, hours=1.25, hourly_value=10.01)
            result = comparison(saved, sale_price=rng.randrange(0, 40000) / 100,
                                materials=rng.randrange(0, 10000) / 100,
                                selling_fees=12.13, hours=0.25, hourly_value=20.03).model_dump(mode="json")
            for key in ("cash_explanation", "after_time_explanation"):
                explanation = result[key]
                baseline = Decimal(str(explanation["baseline_profit"]))
                actual = Decimal(str(explanation["actual_profit"]))
                effects = sum((Decimal(str(e["amount"])) for e in explanation["effects"]), Decimal(0))
                self.assertEqual(baseline + effects, actual)
                self.assertEqual(effects, Decimal(str(explanation["total_effect"])))
                saw_rounding |= any(effect["key"] == "rounding" for effect in explanation["effects"])
                self.assertNotEqual(explanation["dominant_effect"], "rounding")
        self.assertTrue(saw_rounding)

    def test_ambient_decimal_precision_cannot_change_comparison(self):
        saved = snapshot()
        facts = ItemOutcomeFacts.model_validate(actual_inputs())
        expected = compare_outcome(saved, facts).model_dump(mode="json")
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(compare_outcome(saved, facts).model_dump(mode="json"), expected)


class ItemProjectEndpointTests(unittest.TestCase):
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

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.old_overrides)
        self.engine.dispose()

    def save(self, owner="alice", parent=None, inputs=None):
        response = self.client.post("/api/items/save", headers={"X-Test-User": owner}, json={
            "inputs": estimate_inputs() if inputs is None else inputs, "parent_item_id": parent})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def track(self, root, basis=None, previous=None, owner="alice", **facts):
        response = self.client.post(f"/api/items/{root}/outcomes", headers={"X-Test-User": owner}, json={
            "request_id": str(uuid4()), "expected_previous_id": previous,
            "based_on_item_id": basis, **actual_inputs(**facts)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def project(self, root):
        response = self.client.get(f"/api/items/{root}/project")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_routes_empty_list_bounds_and_auth(self):
        response = self.client.get("/api/items/projects")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), dict(items=[], limit=50, offset=0, next_offset=None))
        for query in ("limit=0", "limit=101", "offset=-1"):
            self.assertEqual(self.client.get("/api/items/projects?" + query).status_code, 422)
        self.assertEqual(self.client.get("/api/items/0/project").status_code, 422)
        del app.dependency_overrides[get_current_user_id]
        for path in ("/api/items/projects", "/api/items/1/project"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_three_versions_form_one_project_with_latest_name(self):
        root = self.save()
        child = self.save(parent=root["id"])
        latest = self.save(parent=child["id"], inputs=estimate_inputs(item_name="Confirmed dresser"))
        response = self.client.get("/api/items/projects").json()
        self.assertEqual(len(response["items"]), 1)
        summary = response["items"][0]
        self.assertEqual((summary["root_item_id"], summary["version_count"], summary["stage"]), (root["id"], 3, "considering"))
        self.assertEqual(summary["latest_version"], latest)
        self.assertIsNone(summary["calculated"])
        detail = self.project(root["id"])
        self.assertEqual(detail["summary"], summary)
        self.assertEqual(detail["versions"], [latest, child, root])
        self.assertEqual(detail["comparison_unavailable_reason"], "No outcome recorded")

    def test_root_pagination_keeps_twenty_five_version_families_distinct(self):
        roots = []
        for _ in range(20):
            root = latest = self.save()
            roots.append(root["id"])
            for _ in range(4):
                latest = self.save(parent=latest["id"])
        self.save(owner="bob")
        first = self.client.get("/api/items/projects?limit=10").json()
        second = self.client.get(f'/api/items/projects?limit=10&offset={first["next_offset"]}').json()
        self.assertEqual(first["next_offset"], 10)
        self.assertIsNone(second["next_offset"])
        items = first["items"] + second["items"]
        self.assertEqual([item["root_item_id"] for item in items], list(reversed(roots)))
        self.assertTrue(all(item["version_count"] == 5 for item in items))
        self.assertEqual(self.client.get("/api/items/projects?offset=100").json()["items"], [])

    def test_owner_isolation_and_non_root_id_match_missing_404(self):
        root = self.save()
        child = self.save(parent=root["id"])
        foreign = self.save(owner="bob")
        self.track(foreign["id"], foreign["id"], owner="bob")
        for item in (child["id"], foreign["id"], 999999):
            response = self.client.get(f"/api/items/{item}/project")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "Item not found."})
        summaries = self.client.get("/api/items/projects").json()["items"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["stage"], "considering")

    def test_latest_saved_stage_and_actuals_follow_outcome_revision(self):
        root = self.save()["id"]
        previous = None
        for status in ("bought", "listed", "sold", "bought"):
            facts = dict(status=status)
            if status != "sold":
                facts.update(sale_price=None, sold_on=None)
            if status == "bought":
                facts.update(listed_on=None)
            saved = self.track(root, root, previous, **facts)
            previous = saved["id"]
            detail = self.project(root)
            summary = self.client.get("/api/items/projects").json()["items"][0]
            self.assertEqual(detail["latest_outcome"], saved)
            self.assertEqual(detail["summary"], summary)
            self.assertEqual(summary["stage"], status)
            self.assertEqual(summary["calculated"], saved["calculated"])

    def test_explicit_older_basis_is_used_instead_of_latest_estimate(self):
        root = self.save()
        latest = self.save(parent=root["id"], inputs=estimate_inputs(resale_low=900, resale_high=1000))
        outcome = self.track(root["id"], root["id"])
        detail = self.project(root["id"])
        self.assertEqual(detail["summary"]["latest_version"], latest)
        self.assertEqual(detail["comparison"]["original_estimate"], root)
        self.assertEqual(detail["comparison"]["expected_at_purchase_price"]["low"]["cash_left"], 90.5)
        self.assertEqual(detail["comparison"]["actual"], outcome["calculated"])

    def test_unlinked_and_incomplete_estimate_show_actuals_without_comparison(self):
        root = self.save(inputs={})
        for basis, reason in ((None, "No estimate linked"), (root["id"], "The estimate was incomplete")):
            latest = self.client.get(f'/api/items/{root["id"]}/outcomes').json()
            self.track(root["id"], basis, latest["id"] if latest else None)
            detail = self.project(root["id"])
            self.assertIsNone(detail["comparison"])
            self.assertEqual(detail["comparison_unavailable_reason"], reason)
            self.assertEqual(detail["latest_outcome"]["calculated"]["cash_profit"], 60)

    def test_outcome_read_cannot_outrun_its_linked_estimate_family(self):
        root = self.save()
        def concurrent_save(db, user_id, root_id):
            # Interleave a version + outcome before the latest-outcome read.
            # Reading versions first would incorrectly say "No estimate linked".
            version = save_item(db, user_id, SaveItemRequest.model_validate({
                "inputs": estimate_inputs(), "parent_item_id": root_id}))
            self.track(root_id, version.id)
            return _latest(db, user_id, root_id)
        with patch("app.services.item_project_service._latest", side_effect=concurrent_save):
            detail = self.project(root["id"])
        self.assertIsNone(detail["comparison_unavailable_reason"])
        self.assertEqual(detail["comparison"]["original_estimate"]["id"], detail["latest_outcome"]["based_on_item_id"])
        self.assertEqual(detail["summary"]["version_count"], 2)

    def test_activity_sort_includes_outcomes_and_versions_with_stable_ties(self):
        first = self.save()
        second = self.save()
        outcome = self.track(first["id"], first["id"])
        date = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with self.Session() as db:
            db.query(SavedItem).update({SavedItem.created_at: date})
            db.get(ItemOutcomeRevision, outcome["id"]).created_at = date + timedelta(days=1)
            db.commit()
        summaries = self.client.get("/api/items/projects").json()["items"]
        self.assertEqual([item["root_item_id"] for item in summaries], [first["id"], second["id"]])
        self.assertEqual(summaries[0]["updated_at"], "2026-10-02T00:00:00+00:00")
        child = self.save(parent=second["id"])
        with self.Session() as db:
            db.get(SavedItem, child["id"]).created_at = date + timedelta(days=1)
            db.commit()
        summaries = self.client.get("/api/items/projects").json()["items"]
        self.assertEqual([item["root_item_id"] for item in summaries], [second["id"], first["id"]])

    def test_list_uses_two_bulk_selects_and_preserves_photo_context(self):
        for _ in range(8):
            saved = self.save()
            self.track(saved["id"], saved["id"])
        context = {"confirmation": {"resale_source": "national_reference"}, "evidence": {"listings": []}}
        with self.Session() as db:
            key = str(uuid4())
            db.add(ItemAssessment(id=key, user_id="alice", request_hash="test", month="2026-10",
                                  status="completed", reserved_micros=0))
            db.flush()
            db.add(SavedItemAssessment(item_id=saved["id"], assessment_id=key, context=context))
            db.commit()
        statements = []
        def capture(_, clause, *args):
            statements.append(clause)
        event.listen(self.engine, "before_execute", capture)
        try:
            response = self.client.get("/api/items/projects")
        finally:
            event.remove(self.engine, "before_execute", capture)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.is_select for statement in statements))
        # Compile exactly the executed selects against the production dialect.
        for statement in statements:
            self.assertTrue(str(statement.compile(dialect=postgresql.dialect())))
        self.assertEqual(response.json()["items"][0]["latest_version"]["assessment"], context)
        self.assertEqual(self.project(saved["id"])["comparison"]["resale_source"], "national_reference")

    def test_reads_do_not_mutate_snapshots_schema_or_make_ai_calls(self):
        root = self.save()
        outcome = self.track(root["id"], root["id"])
        before_schema = inspect(self.engine).get_table_names()
        old_items = self.client.get("/api/items").json()
        with patch("app.services.item_assessment_service.assess", side_effect=AssertionError("No AI")):
            self.client.get("/api/items/projects").raise_for_status()
            self.project(root["id"])
        self.assertEqual(self.client.get("/api/items").json(), old_items)
        self.assertEqual(self.client.get(f'/api/items/{root["id"]}').json(), root)
        self.assertEqual(self.client.get(f'/api/items/{root["id"]}/outcomes').json(), outcome)
        self.assertEqual(inspect(self.engine).get_table_names(), before_schema)
        with self.Session() as db:
            self.assertEqual(db.query(SavedItem).count(), 1)
            self.assertEqual(db.query(ItemOutcomeRevision).count(), 1)

    def test_openapi_adds_only_get_routes_and_keeps_protected_contracts(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ("/api/items/projects", "/api/items/{root_item_id}/project"):
            self.assertEqual(set(paths[path]), {"get"})
            self.assertTrue(paths[path]["get"]["security"])
        self.assertNotIn("security", paths["/api/items/analyze"]["post"])
        self.assertEqual(set(paths["/api/items/{root_item_id}/outcomes"]), {"get", "post"})


if __name__ == "__main__":
    unittest.main()
