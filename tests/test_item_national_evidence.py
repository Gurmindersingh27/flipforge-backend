"""Synthetic evidence only: no provider calls or claims about real search quality."""
import copy
import json
import unittest

from app.item_assessment_models import ListingEvidence, ProviderAssessment
from app.services.item_evidence_service import evidence_diagnostics, national_reference, select_evidence
from test_item_assessment import national_response, provider_response


class NationalEvidenceTests(unittest.TestCase):
    def select(self, raw):
        report = ProviderAssessment.model_validate_json(raw["content"][0]["text"])
        rows, local = select_evidence(report, raw, "2026-10-28T12:00:00+00:00")
        return rows, local, national_reference(rows, local)

    def change(self, raw, index=0, **updates):
        report = json.loads(raw["content"][0]["text"])
        report["listings"][index].update(updates)
        raw["content"][0]["text"] = json.dumps(report)

    def test_three_used_item_only_shipping_prices_produce_a_separate_reference(self):
        raw = national_response()
        before = copy.deepcopy(raw)
        rows, local, national = self.select(raw)
        self.assertIsNone(local)
        self.assertEqual(national, dict(low=150, high=250, eligible_count=3,
            method="linear_quartiles_v1", source="online_asking_prices",
            label="National used asking prices; shipping excluded. Not confirmed sales or verified local prices."))
        for row in rows:
            self.assertFalse(row["eligible"])
            self.assertEqual(row["exclusion_reason"], "not_local_pickup")
            self.assertTrue(row["national_eligible"])
            self.assertIsNone(row["national_exclusion_reason"])
            self.assertEqual((row["condition_type"], row["price_basis"]), ("used", "item_only"))
            ListingEvidence.model_validate(row)
        self.assertEqual(raw, before)

    def test_minimum_three_and_separate_pools_never_mix(self):
        for prices in [(), (100,), (100, 200)]:
            with self.subTest(prices=prices):
                rows, local, national = self.select(national_response(prices))
                self.assertEqual(len(rows), len(prices))
                self.assertIsNone(local)
                self.assertIsNone(national)
        raw = national_response((100, 200, 300, 400))
        for index in (0, 1):
            self.change(raw, index, market="local_pickup")
        rows, local, national = self.select(raw)
        self.assertEqual(sum(row["eligible"] for row in rows), 2)
        self.assertEqual(sum(row["national_eligible"] for row in rows), 2)
        self.assertIsNone(local)
        self.assertIsNone(national)

    def test_local_range_wins_without_erasing_national_listing_reasons(self):
        raw = national_response((65, 75, 90, 100, 200, 300))
        for index in (0, 1, 2):
            self.change(raw, index, market="local_pickup", condition_type="unknown", price_basis="unknown")
        rows, local, national = self.select(raw)
        self.assertEqual(local, self.select(provider_response())[1])
        self.assertEqual((local["low"], local["high"], local["eligible_count"]), (70, 82.5, 3))
        self.assertIsNone(national)
        self.assertEqual([row["national_exclusion_reason"] for row in rows],
                         ["not_shipped"] * 3 + [None] * 3)
        self.assertEqual([row["national_eligible"] for row in rows], [False] * 3 + [True] * 3)

    def test_each_national_gate_has_a_server_reason(self):
        cases = [
            ({"condition_type": "unknown"}, "condition_unknown"),
            ({"condition_type": "new"}, "condition_new"),
            ({"price_basis": "unknown"}, "price_basis_unknown"),
            ({"price_basis": "shipping_included"}, "shipping_included"),
            ({"market": "unknown"}, "not_shipped"),
            ({"market": "local_pickup"}, "not_shipped"),
            ({"single_item": False}, "not_comparable"),
            ({"comparable": False}, "not_comparable"),
            ({"price": 0}, "zero_price"),
            ({"price": 999}, "unsupported_price"),
        ]
        for updates, reason in cases:
            with self.subTest(updates=updates):
                raw = national_response()
                self.change(raw, **updates)
                if updates.get("price") == 0:
                    raw["content"][0]["citations"][0]["cited_text"] = "Used chair $0, plus shipping."
                rows, _, national = self.select(raw)
                self.assertFalse(rows[0]["national_eligible"])
                self.assertEqual(rows[0]["national_exclusion_reason"], reason)
                self.assertEqual(sum(row["national_eligible"] for row in rows), 2)
                self.assertIsNone(national)

    def test_price_proof_precedes_other_failures_and_url_matching_is_not_loosened(self):
        for mode in ["absent", "wrong_quote", "tracking", "trailing_slash"]:
            with self.subTest(mode=mode):
                raw = national_response()
                self.change(raw, condition_type="unknown", price_basis="unknown")
                citation = raw["content"][0]["citations"][0]
                if mode == "absent":
                    raw["content"][0]["citations"].pop(0)
                elif mode == "wrong_quote":
                    citation["cited_text"] = "Used chair $999, plus shipping."
                elif mode == "tracking":
                    citation["url"] += "?tracking=different"
                else:
                    citation["url"] += "/"
                rows, _, national = self.select(raw)
                self.assertEqual(rows[0]["national_exclusion_reason"], "unsupported_price")
                self.assertIsNone(national)
                counts = evidence_diagnostics(raw, rows)
                self.assertEqual(counts["national_unknown_condition_count"], 0)
                self.assertEqual(counts["national_unknown_price_basis_count"], 0)

    def test_duplicates_invalid_urls_and_blocked_retail_cannot_supply_a_range(self):
        for url in ["https://example.com/chair/1?tracking=duplicate", "javascript:invalid",
                    "https://www.wayfair.com/chair/0", "https://WAYFAIR.COM./chair/0"]:
            with self.subTest(url=url):
                raw = national_response()
                self.change(raw, url=url)
                raw["content"][0]["citations"][0]["url"] = url
                rows, _, national = self.select(raw)
                self.assertEqual(len(rows), 2)
                self.assertIsNone(national)
        raw = national_response()
        self.change(raw, url="https://notwayfair.com/chair/0")
        raw["content"][0]["citations"][0]["url"] = "https://notwayfair.com/chair/0"
        self.assertEqual(self.select(raw)[2]["eligible_count"], 3)

    def test_legacy_missing_classifications_cannot_supply_national_prices(self):
        raw = provider_response()
        for index in range(3):
            self.change(raw, index, market="national_shipping")
        rows, local, national = self.select(raw)
        self.assertIsNone(local)
        self.assertIsNone(national)
        for row in rows:
            self.assertEqual((row["condition_type"], row["price_basis"]), ("unknown", "unknown"))
            self.assertEqual(row["national_exclusion_reason"], "condition_unknown")
        counts = evidence_diagnostics(raw, rows)
        self.assertEqual(counts["national_unknown_condition_count"], 3)
        self.assertEqual(counts["national_unknown_price_basis_count"], 3)

    def test_old_listing_output_defaults_to_unrecorded_not_to_eligibility(self):
        row = self.select(provider_response())[0][0]
        for key in ("condition_type", "price_basis", "national_eligible", "national_exclusion_reason"):
            row.pop(key)
        parsed = ListingEvidence.model_validate(row)
        self.assertFalse(parsed.national_eligible)
        self.assertEqual(parsed.national_exclusion_reason, "not_recorded")
        self.assertEqual((parsed.condition_type, parsed.price_basis), ("unknown", "unknown"))
        self.assertTrue(parsed.eligible)

    def test_unknown_counts_are_independent_and_count_only_retained_national_candidates(self):
        raw = national_response(tuple(range(100, 1200, 100)))
        updates = [
            {"condition_type": "unknown", "price_basis": "unknown"},
            {"price_basis": "unknown"},
            {"condition_type": "unknown"},
            {"condition_type": "new", "price_basis": "unknown"},
            {"condition_type": "unknown", "price_basis": "unknown"},
            {"condition_type": "unknown", "price_basis": "unknown", "comparable": False},
            {"condition_type": "unknown", "price_basis": "unknown", "market": "local_pickup"},
            {"condition_type": "unknown", "price_basis": "unknown", "price": 0},
            {"condition_type": "unknown", "price_basis": "unknown", "url": "https://www.wayfair.com/chair/8"},
            {"condition_type": "unknown", "price_basis": "unknown", "url": "https://example.com/chair/0?duplicate=1"},
            {"condition_type": "unknown", "price_basis": "unknown", "url": "javascript:invalid"},
        ]
        for index, changes in enumerate(updates):
            self.change(raw, index, **changes)
        raw["content"][0]["citations"][4]["cited_text"] = "Used chair, price unavailable."
        raw["content"][0]["citations"][7]["cited_text"] = "Used chair $0, plus shipping."
        rows, _, national = self.select(raw)
        self.assertEqual(len(rows), 8)
        self.assertIsNone(national)
        counts = evidence_diagnostics(raw, rows)
        self.assertEqual(counts["national_unknown_condition_count"], 2)
        self.assertEqual(counts["national_unknown_price_basis_count"], 3)
        self.assertTrue(all(type(value) is int for value in counts.values()))
        self.assertNotIn("https", json.dumps(counts))
        self.assertNotIn("Used chair", json.dumps(counts))

    def test_national_quartiles_reuse_cent_rounding_and_price_boundaries(self):
        national = self.select(national_response((.01, .02, .04)))[2]
        self.assertEqual((national["low"], national["high"]), (.02, .03))
        for quote, price, accepted in [("Asking $450.", 450, True), ("Buy It Now for $259.00.", 259, True),
                                       ("$450, plus shipping", 450, True), ("$12.345", 12.34, False)]:
            with self.subTest(quote=quote):
                raw = national_response((price, 600, 900))
                raw["content"][0]["citations"][0]["cited_text"] = quote
                rows, _, national = self.select(raw)
                self.assertEqual(rows[0]["national_eligible"], accepted)
                self.assertEqual(national is not None, accepted)


if __name__ == "__main__":
    unittest.main()
