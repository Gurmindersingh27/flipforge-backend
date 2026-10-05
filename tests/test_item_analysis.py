"""Items contract, hand-calculated fixtures and deterministic property checks."""
import copy
import json
import random
import unittest
from contextlib import ExitStack
from decimal import Decimal, localcontext
from fractions import Fraction
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.item_analysis_engine import analyze_item
from app.item_models import ItemAnalyzeRequest, ItemPersonalDefaults


D = Decimal
FIELDS = (
    "purchase_price", "resale_low", "resale_high", "repairs", "pickup",
    "delivery", "storage", "fee_fixed", "hours", "hourly_value",
    "target_profit", "contingency_pct", "fee_pct",
)
PREFERENCES = ("hourly_value", "target_profit", "contingency_pct", "fee_pct")


def dresser(**changes):
    return dict(purchase_price=0, resale_low=300, resale_high=450, repairs=60,
                pickup=40, delivery=0, storage=0, fee_fixed=0, hours=5,
                hourly_value=20, target_profit=150, contingency_pct=0, fee_pct=0) | changes


def fractional(**changes):
    return dresser(resale_high=300, repairs=40, pickup=15, contingency_pct=0.10,
                   fee_pct=0.075, fee_fixed=1.10, hours=2, target_profit=100) | changes


def result(body):
    return analyze_item(ItemAnalyzeRequest.model_validate(body))


class ItemFormulaTests(unittest.TestCase):
    def test_dresser_worked_examples(self):
        cases = [
            (0, "200", "100", "250", "50", "0", "stretch"),
            (40, "160", "60", "210", "90", "0", "stretch"),
            (100, "100", "0", "150", "150", "0", "stretch"),
            (100.01, "99.99", "-0.01", "149.99", "150.01", "0.01", "skip"),
        ]
        for price, cash, low, high, short_low, short_high, status in cases:
            with self.subTest(price=price):
                answer = result(dresser(purchase_price=price))
                self.assertEqual(answer.status, status)
                self.assertEqual(answer.low.cash_left, D(cash))
                self.assertEqual(answer.low.profit_after_time, D(low))
                self.assertEqual(answer.high.profit_after_time, D(high))
                self.assertEqual(answer.low.target_shortfall, D(short_low))
                self.assertEqual(answer.high.target_shortfall, D(short_high))
                self.assertEqual((answer.low.raw_max_offer, answer.high.raw_max_offer), (D(-50), D(100)))
                self.assertEqual((answer.low.max_offer, answer.high.max_offer), (-50, 100))

    def test_fractional_ceiling_and_equal_resale_boundaries(self):
        for price, status in [(77, "within_budget"), (77.40, "within_budget"), (77.41, "skip")]:
            with self.subTest(price=price):
                answer = result(fractional(purchase_price=price))
                self.assertEqual(answer.status, status)
                self.assertEqual(answer.low, answer.high)
                self.assertEqual(answer.low.raw_max_offer, D("77.40"))
                self.assertEqual(answer.low.max_offer, 77)
        self.assertEqual(result(fractional(purchase_price=77)).low.profit_after_time, D("100.40"))

    def test_both_raw_boundaries_with_nonempty_stretch_interval(self):
        for price, status in [(77.40, "within_budget"), (77.41, "stretch"),
                              (354.90, "stretch"), (354.91, "skip")]:
            with self.subTest(price=price):
                self.assertEqual(result(fractional(resale_high=600, purchase_price=price)).status, status)

    def test_fee_and_contingency_bases_and_no_double_counting(self):
        answer = result(fractional(purchase_price=10, resale_high=400, delivery=7, storage=3))
        self.assertEqual(answer.low.contingency, D(4))
        self.assertEqual(answer.low.selling_fees, D("23.60"))
        self.assertEqual(answer.high.selling_fees, D("31.10"))
        self.assertEqual(answer.low.own_time_value, D(40))
        self.assertEqual(answer.low.cash_left, D("197.40"))
        self.assertEqual(answer.low.profit_after_time, D("157.40"))
        self.assertEqual(answer.high.profit_after_time, D("249.90"))
        changed = result(fractional(purchase_price=10, resale_high=400, delivery=7, storage=3, target_profit=200))
        self.assertEqual(changed.low.cash_left, answer.low.cash_left)
        self.assertEqual(changed.low.profit_after_time, answer.low.profit_after_time)
        self.assertEqual(changed.low.raw_max_offer, answer.low.raw_max_offer - 100)

    def test_negative_floor_and_free_acquisition_missing_target(self):
        answer = result(dresser(target_profit=149.20))
        self.assertEqual(answer.low.raw_max_offer, D("-49.20"))
        self.assertEqual(answer.low.max_offer, -50)
        self.assertGreater(answer.low.cash_left, 0)
        self.assertGreater(answer.low.profit_after_time, 0)
        self.assertEqual(answer.low.target_shortfall, D("49.20"))
        self.assertEqual(result(dresser(target_profit=1000)).status, "skip")

    def test_zero_hourly_value_and_cash_independent_of_own_time(self):
        zero = result(fractional(hourly_value=0))
        for rate in [0, 0.01, 20, 999.99, 1000]:
            with self.subTest(rate=rate):
                answer = result(fractional(hourly_value=rate))
                for name in ("low", "high"):
                    self.assertEqual(getattr(answer, name).cash_left, getattr(zero, name).cash_left)
        self.assertEqual(zero.low.cash_left, zero.low.profit_after_time)

    def test_bounded_extremes_do_not_clip_derived_values(self):
        body = {field: 1_000_000 for field in FIELDS}
        body.update(hours=10_000, hourly_value=1000, fee_pct=1, contingency_pct=1)
        answer = result(body)
        self.assertEqual(answer.low.profit_after_time, D(-17_000_000))
        self.assertEqual(answer.low.raw_max_offer, D(-17_000_000))
        self.assertEqual(answer.low.target_shortfall, D(18_000_000))


class ItemMissingAndDefaultsTests(unittest.TestCase):
    def test_unknown_vs_explicit_zero_for_every_required_input(self):
        zero = {field: 0 for field in FIELDS}
        self.assertEqual(result(zero).status, "within_budget")
        for field in FIELDS[1:]:
            with self.subTest(field=field):
                answer = result(zero | {field: None})
                self.assertEqual(answer.status, "needs_info")
                self.assertEqual(answer.missing_inputs, [field])
                self.assertIsNone(answer.low)
                self.assertIsNone(answer.high)
                self.assertIsNone(getattr(answer.assumptions, field).value)
                omitted = zero.copy()
                del omitted[field]
                if field not in ("hourly_value", "contingency_pct"):
                    self.assertEqual(result(omitted).missing_inputs, [field])

    def test_missing_order_and_purchase_always_excluded(self):
        expected = [name for name in FIELDS[1:] if name not in ("hourly_value", "contingency_pct")]
        self.assertEqual(result({}).missing_inputs, expected)
        body = {name: None for name in reversed(FIELDS)}
        self.assertEqual(result(body).missing_inputs, list(FIELDS[1:]))
        for body in [dresser(purchase_price=None, pickup=None), {"purchase_price": None}]:
            answer = result(body)
            self.assertEqual(answer.status, "needs_info")
            self.assertNotIn("purchase_price", answer.missing_inputs)
            self.assertIsNone(answer.low)

    def test_partial_resale_never_fabricates_other_endpoint(self):
        for field in ("resale_low", "resale_high"):
            body = dresser()
            del body[field]
            answer = result(body)
            self.assertEqual(answer.missing_inputs, [field])
            self.assertIsNone(answer.low)
            self.assertIsNone(answer.high)

    def test_offer_only_for_null_and_omitted_purchase(self):
        body = dresser()
        del body["purchase_price"]
        for data in (body, body | {"purchase_price": None}):
            answer = result(data)
            self.assertEqual(answer.status, "offer_only")
            self.assertEqual(answer.missing_inputs, [])
            self.assertEqual((answer.low.max_offer, answer.high.max_offer), (-50, 100))
            for scenario in (answer.low, answer.high):
                self.assertIsNone(scenario.cash_left)
                self.assertIsNone(scenario.profit_after_time)
                self.assertIsNone(scenario.target_shortfall)

    def test_only_hourly_and_contingency_have_application_defaults(self):
        answer = result({})
        for field, value in [("hourly_value", D(20)), ("contingency_pct", D("0.15"))]:
            assumption = getattr(answer.assumptions, field)
            self.assertEqual((assumption.value, assumption.source, assumption.default_origin), (value, "default", "application"))
        for field in ("fee_pct", "target_profit"):
            self.assertIn(field, answer.missing_inputs)
            self.assertIsNone(getattr(answer.assumptions, field).source)

    def test_personal_defaults_precedence_null_zero_and_provenance(self):
        defaults = dict(hourly_value=35, target_profit=80, contingency_pct=0.2, fee_pct=0.075)
        body = {key: value for key, value in dresser().items() if key not in PREFERENCES}
        body["personal_defaults"] = defaults
        for field in PREFERENCES:
            with self.subTest(field=field):
                inherited = getattr(result(body).assumptions, field)
                self.assertEqual(inherited.value, D(str(defaults[field])))
                self.assertEqual((inherited.source, inherited.default_origin), ("default", "personal"))
                for override in [None, 0]:
                    answer = result(body | {field: override})
                    assumption = getattr(answer.assumptions, field)
                    self.assertEqual(assumption.value, None if override is None else D(0))
                    self.assertEqual(assumption.source, None if override is None else "user_entered")
                    self.assertIsNone(assumption.default_origin)
                    self.assertEqual(field in answer.missing_inputs, override is None)
                unknown_default = body | {"personal_defaults": defaults | {field: None}}
                self.assertIn(field, result(unknown_default).missing_inputs)
        self.assertEqual(result(body).missing_inputs, [])
        all_zero = result(body | {"personal_defaults": dict.fromkeys(PREFERENCES, 0)})
        for field in PREFERENCES:
            assumption = getattr(all_zero.assumptions, field)
            self.assertEqual((assumption.value, assumption.source, assumption.default_origin), (D(0), "default", "personal"))

    def test_all_inputs_echo_sources_and_request_is_not_mutated(self):
        body = fractional(item_name="Desk", category="Writing desk", personal_defaults={"hourly_value": 100})
        original = copy.deepcopy(body)
        req = ItemAnalyzeRequest.model_validate(body)
        snapshot, fields = req.model_dump(), req.model_fields_set.copy()
        answer = analyze_item(req)
        self.assertEqual(body, original)
        self.assertEqual(req.model_dump(), snapshot)
        self.assertEqual(req.model_fields_set, fields)
        self.assertEqual(answer, analyze_item(req))
        for field in FIELDS:
            assumption = getattr(answer.assumptions, field)
            self.assertEqual(assumption.value, D(str(body[field])))
            self.assertEqual(assumption.source, "user_entered")
            self.assertIsNone(assumption.default_origin)
        self.assertEqual(answer.assumptions.item_name.value, "Desk")
        self.assertEqual(answer.assumptions.category.source, "user_entered")


class ItemValidationTests(unittest.TestCase):
    def test_invalid_numbers_every_top_level_and_personal_default_field(self):
        invalid = [True, False, "0", "1.25", "", "NaN", -1, -0.00001,
                   float("nan"), float("inf"), -float("inf"), [], {}]
        for field in FIELDS:
            for value in invalid:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        ItemAnalyzeRequest.model_validate({field: value})
                    if field in PREFERENCES:
                        with self.assertRaises(ValidationError):
                            ItemAnalyzeRequest.model_validate({"personal_defaults": {field: value}})

    def test_bounds_before_money_normalization(self):
        limits = dict.fromkeys(FIELDS, 1_000_000)
        limits.update(hourly_value=1000, hours=10000, fee_pct=1, contingency_pct=1)
        for field, maximum in limits.items():
            for value in [0, maximum]:
                with self.subTest(field=field, value=value):
                    self.assertEqual(getattr(ItemAnalyzeRequest.model_validate({field: value}), field), D(value))
            for value in [maximum + 0.00001, -0.00001]:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        ItemAnalyzeRequest.model_validate({field: value})
                    if field in PREFERENCES:
                        with self.assertRaises(ValidationError):
                            ItemPersonalDefaults.model_validate({field: value})

    def test_money_half_up_normalization_for_all_money_fields_and_defaults(self):
        fields = [name for name in FIELDS if name not in ("hours", "fee_pct", "contingency_pct")]
        for field in fields:
            for number, normalized in [(0.30000000000000004, "0.30"), (1.005, "1.01"),
                                       (0.0049, "0.00"), (-0.0, "0.00")]:
                with self.subTest(field=field, number=number):
                    self.assertEqual(getattr(ItemAnalyzeRequest.model_validate({field: number}), field), D(normalized))
                    if field in PREFERENCES:
                        req = ItemAnalyzeRequest.model_validate({"personal_defaults": {field: number}})
                        self.assertEqual(getattr(analyze_item(req).assumptions, field).value, D(normalized))

    def test_non_money_precision_is_retained_and_excess_precision_rejected(self):
        for field, allowed, invalid in [("hours", 1.23, 1.234), ("fee_pct", 0.123456, 0.1234567),
                                         ("contingency_pct", 0.075, 0.0000001)]:
            self.assertEqual(getattr(ItemAnalyzeRequest.model_validate({field: allowed}), field), D(str(allowed)))
            with self.assertRaises(ValidationError):
                ItemAnalyzeRequest.model_validate({field: invalid})
            if field in PREFERENCES:
                with self.assertRaises(ValidationError):
                    ItemPersonalDefaults.model_validate({field: invalid})

    def test_reversed_range_even_if_cents_would_coincide(self):
        for low, high in [(301, 300), (0.304, 0.301)]:
            with self.assertRaises(ValidationError):
                ItemAnalyzeRequest(resale_low=low, resale_high=high)

    def test_unknown_fields_and_metadata_types_lengths(self):
        for body in [{"vehicle_context": "truck"}, {"arv": 300}, {"other": 0},
                     {"personal_defaults": {"repairs": 0}}, {"personal_defaults": {"other": 0}},
                     {"personal_defaults": {"vehicle_context": "truck"}}]:
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ItemAnalyzeRequest.model_validate(body)
        for field, limit in [("item_name", 200), ("category", 100)]:
            self.assertEqual(getattr(ItemAnalyzeRequest.model_validate({field: "x" * limit}), field), "x" * limit)
            for invalid in ["x" * (limit + 1), 123, True, {}, []]:
                with self.assertRaises(ValidationError):
                    ItemAnalyzeRequest.model_validate({field: invalid})


class ItemPropertyTests(unittest.TestCase):
    def test_monotonicity_across_representative_costs_and_preferences(self):
        baseline = fractional(purchase_price=10, resale_high=600)
        before = result(baseline)
        changes = dict(purchase_price=[10, 10.01, 1000], repairs=[40, 40.01, 500],
                       pickup=[15, 15.01, 300], delivery=[0, 0.01, 300], storage=[0, 0.01, 300],
                       fee_fixed=[1.10, 1.11, 300], fee_pct=[0.075, 0.075001, 1],
                       contingency_pct=[0.1, 0.100001, 1], hours=[2, 2.01, 10000],
                       hourly_value=[20, 20.01, 1000], target_profit=[100, 100.01, 1000000])
        for field, increases in changes.items():
            for value in increases:
                with self.subTest(field=field, value=value):
                    after = result(baseline | {field: value})
                    for scenario in ("low", "high"):
                        left, right = getattr(before, scenario), getattr(after, scenario)
                        self.assertLessEqual(right.profit_after_time, left.profit_after_time)
                        self.assertLessEqual(right.raw_max_offer, left.raw_max_offer)
                        self.assertGreaterEqual(right.target_shortfall, left.target_shortfall)
        for low, high in [(300, 600), (300.01, 600.01), (1000, 2000)]:
            after = result(baseline | dict(resale_low=low, resale_high=high))
            for name in ("low", "high"):
                self.assertGreaterEqual(getattr(after, name).profit_after_time, getattr(before, name).profit_after_time)

    def test_fraction_oracle_and_floored_offer_guarantee_over_250_cases(self):
        rng = random.Random(20261005)
        positive = negative = 0
        for _ in range(250):
            # Cent amounts and bounded decimal fractions, generated independently.
            body = {field: rng.randrange(0, 50000) / 100 for field in FIELDS}
            body.update(resale_low=rng.randrange(0, 200000) / 100,
                        hours=rng.randrange(0, 1000) / 100,
                        hourly_value=rng.randrange(0, 10000) / 100,
                        fee_pct=rng.choice([0, 0.075, 0.123456, 1]),
                        contingency_pct=rng.choice([0, 0.15, 0.999999, 1]))
            body["resale_high"] = body["resale_low"] + 1000
            answer = result(body)
            effective = {name: Fraction(getattr(answer.assumptions, name).value) for name in FIELDS}
            for name, resale_name in [("low", "resale_low"), ("high", "resale_high")]:
                scenario = getattr(answer, name)
                v = effective
                expected_cash = (v[resale_name] * (1 - v["fee_pct"]) - v["purchase_price"]
                                 - v["repairs"] * (1 + v["contingency_pct"])
                                 - sum(v[key] for key in ("pickup", "delivery", "storage", "fee_fixed")))
                expected_profit = expected_cash - v["hours"] * v["hourly_value"]
                expected_offer = expected_profit + v["purchase_price"] - v["target_profit"]
                self.assertEqual(Fraction(scenario.cash_left), expected_cash)
                self.assertEqual(Fraction(scenario.profit_after_time), expected_profit)
                self.assertEqual(Fraction(scenario.raw_max_offer), expected_offer)
                self.assertEqual(Fraction(scenario.target_shortfall), max(0, v["target_profit"] - expected_profit))
                self.assertEqual(scenario.max_offer, expected_offer.numerator // expected_offer.denominator)
                if scenario.max_offer >= 0:
                    positive += 1
                    bought = getattr(result(body | {"purchase_price": scenario.max_offer}), name)
                    self.assertGreaterEqual(bought.profit_after_time, D(str(body["target_profit"])))
                else:
                    negative += 1
                    free = getattr(result(body | {"purchase_price": 0}), name)
                    self.assertLess(free.profit_after_time, D(str(body["target_profit"])))
            expected_status = ("within_budget" if answer.low.target_shortfall == 0 else
                               "stretch" if answer.high.target_shortfall == 0 else "skip")
            self.assertEqual(answer.status, expected_status)
        self.assertGreater(positive, 0)
        self.assertGreater(negative, 0)

    def test_ambient_decimal_precision_cannot_change_decisions(self):
        body = fractional(purchase_price=77.40)
        expected = result(body).model_dump_json()
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(result(body).model_dump_json(), expected)


class ItemRouteTests(unittest.TestCase):
    def setUp(self):
        # No lifespan startup, credentials, auth bypass or production DB needed.
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def post(self, body):
        return self.client.post("/api/items/analyze", json=body)

    def test_public_complete_contract_has_json_numbers_and_sources(self):
        response = self.post(fractional(purchase_price=77, item_name="Desk", category="Writing desk"))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(set(data), {"schema_version", "status", "missing_inputs", "assumptions", "low", "high"})
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["status"], "within_budget")
        self.assertEqual(data["low"], dict(resale=300, contingency=4, selling_fees=23.60,
                         own_time_value=40, raw_max_offer=77.40, max_offer=77,
                         cash_left=140.40, profit_after_time=100.40, target_shortfall=0))
        for scenario in (data["low"], data["high"]):
            for value in scenario.values():
                self.assertIn(type(value), (int, float))
            self.assertIs(type(scenario["max_offer"]), int)
        for field in FIELDS:
            self.assertIn(type(data["assumptions"][field]["value"]), (int, float))
        self.assertEqual(data["assumptions"]["fee_pct"]["value"], 0.075)

    def test_dresser_examples_through_http(self):
        for price, status, profit in [(0, "stretch", 100), (40, "stretch", 60),
                                      (100, "stretch", 0), (100.01, "skip", -0.01)]:
            data = self.post(dresser(purchase_price=price)).json()
            self.assertEqual(data["status"], status)
            self.assertEqual(data["low"]["profit_after_time"], profit)
            self.assertEqual(data["low"]["max_offer"], -50)

    def test_incomplete_and_offer_only_http_200(self):
        for body in [{}, dresser(pickup=None, purchase_price=None)]:
            response = self.post(body)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "needs_info")
            self.assertIsNone(data["low"])
            self.assertIsNone(data["high"])
            self.assertNotIn("purchase_price", data["missing_inputs"])
        data = self.post(dresser(purchase_price=None)).json()
        self.assertEqual(data["status"], "offer_only")
        self.assertEqual(data["missing_inputs"], [])
        self.assertEqual(data["high"]["max_offer"], 100)
        for field in ("cash_left", "profit_after_time", "target_shortfall"):
            self.assertIsNone(data["low"][field])

    def test_fee_and_target_required_unless_personally_supplied(self):
        for field in ("fee_pct", "target_profit"):
            body = dresser()
            value = body.pop(field)
            self.assertEqual(self.post(body).json()["missing_inputs"], [field])
            body["personal_defaults"] = {field: value}
            self.assertEqual(self.post(body).json()["missing_inputs"], [])

    def test_status_and_floor_precede_cent_serialization(self):
        # Internal ceiling 0.9999 sends as 1.00; buying for 1 still misses target.
        body = dict.fromkeys(FIELDS, 0)
        body.update(resale_low=1, resale_high=1, fee_pct=0.0001, purchase_price=1)
        data = self.post(body).json()
        self.assertEqual(data["low"]["raw_max_offer"], 1)
        self.assertEqual(data["low"]["max_offer"], 0)
        self.assertEqual(data["status"], "skip")
        self.assertEqual(data["low"]["profit_after_time"], 0)
        self.assertNotIn("-0.0", self.post(body).text)
        body.update(purchase_price=0, target_profit=1)
        data = self.post(body).json()
        self.assertEqual(data["low"]["raw_max_offer"], 0)
        self.assertEqual(data["low"]["max_offer"], -1)
        self.assertEqual(data["status"], "skip")

    def test_money_and_non_money_echo_precision_over_http(self):
        data = self.post(fractional(pickup=0.30000000000000004, hourly_value=20.005, hours=1.23)).json()
        for field, value in [("pickup", 0.30), ("hourly_value", 20.01), ("hours", 1.23), ("fee_pct", 0.075)]:
            self.assertEqual(data["assumptions"][field]["value"], value)
        self.assertEqual(data["low"]["own_time_value"], 24.61)
        # Half-up serialization, not binary bankers' rounding.
        data = self.post(fractional(repairs=0.10, contingency_pct=0.05)).json()
        self.assertEqual(data["low"]["contingency"], 0.01)

    def test_invalid_requests_are_422_including_nonfinite_numbers(self):
        for body in [{"repairs": "0"}, {"hours": True}, {"fee_pct": 1.01},
                     {"purchase_price": -0.00001}, {"vehicle_context": "truck"},
                     {"personal_defaults": {"extra": 0}}, {"personal_defaults": {"fee_pct": "0"}},
                     {"resale_low": 2, "resale_high": 1}, {"hours": 1.234}]:
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 422)
        for token in ("NaN", "Infinity", "-Infinity", "1e309"):
            for template in ('{"repairs": %s}', '{"personal_defaults": {"fee_pct": %s}}',
                             '{"extra": {"nested": [%s]}}', '{"hours": [%s]}',
                             '{"resale_low": 2, "resale_high": 1, "extra": %s}', '%s'):
                response = self.client.post("/api/items/analyze", content=template % token,
                                            headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertTrue(response.json()["detail"])

    def test_stateless_route_has_no_dependencies_or_external_calls(self):
        route = next(route for route in main.app.routes if route.path == "/api/items/analyze")
        self.assertEqual(route.dependant.dependencies, [])
        with ExitStack() as stack:
            for name in ("enrich_address", "analyze_photos", "draft_from_url", "analyze_deal",
                         "init_db", "preload_jwks"):
                stack.enter_context(patch.object(main, name, side_effect=AssertionError("unexpected external work")))
            stack.enter_context(patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("unexpected DB")))
            stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("unexpected network")))
            first = self.post(fractional()).json()
            self.assertEqual(first, self.post(fractional()).json())

    def test_openapi_numeric_wire_schema_and_separate_contract(self):
        schemas = main.app.openapi()["components"]["schemas"]
        request = schemas["ItemAnalyzeRequest"]
        self.assertFalse(request["additionalProperties"])
        self.assertNotIn("arv", request["properties"])
        self.assertNotIn("vehicle_context", request["properties"])
        self.assertNotIn('"string"', json.dumps(request["properties"]["purchase_price"]))
        self.assertNotIn('"string"', json.dumps(schemas["ItemScenario"]["properties"]["raw_max_offer"]))
        self.assertEqual(schemas["ItemScenario"]["properties"]["max_offer"]["type"], "integer")
