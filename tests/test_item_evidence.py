import json
import random
import unittest
from decimal import Decimal
from fractions import Fraction

from app.item_assessment_models import ProviderAssessment
from app.services.item_evidence_service import quartile, select_evidence
from app.services.item_repair_catalog import suggestions
from test_item_assessment import provider_response


class EvidenceTests(unittest.TestCase):
    def evidence(self, raw):
        report = ProviderAssessment.model_validate_json(raw["content"][0]["text"])
        return select_evidence(report, raw, "2026-10-06T12:00:00+00:00")

    def change_listing(self, raw, **updates):
        report = json.loads(raw["content"][0]["text"])
        report["listings"][0].update(updates)
        raw["content"][0]["text"] = json.dumps(report)

    def test_uncited_price_or_url_cannot_drive_range(self):
        for updates in [dict(price=999), dict(url="https://invented.example/item"), dict(url="javascript:alert(1)")]:
            raw = provider_response()
            self.change_listing(raw, **updates)
            _, resale = self.evidence(raw)
            self.assertIsNone(resale)

    def test_national_unknown_sets_and_unrelated_items_are_not_local_comps(self):
        for updates in [dict(market="national_shipping"), dict(market="unknown"), dict(single_item=False), dict(comparable=False)]:
            raw = provider_response()
            self.change_listing(raw, **updates)
            listings, resale = self.evidence(raw)
            self.assertFalse(listings[0]["eligible"])
            self.assertIsNone(resale)

    def test_duplicate_urls_do_not_supply_three_matches(self):
        raw = provider_response()
        self.change_listing(raw, url="https://example.com/chair/1?tracking=1")
        self.assertIsNone(self.evidence(raw)[1])

    def test_missing_citations_means_manual_estimate(self):
        raw = provider_response()
        raw["content"][0]["citations"] = []
        self.assertIsNone(self.evidence(raw)[1])

    def test_unknown_repairs_stay_unknown_not_zero(self):
        raw = provider_response()
        report = json.loads(raw["content"][0]["text"])
        report["repairs"].append(dict(job_id="structural_rebuild", reason="Possible broken frame"))
        repairs, unknowns = suggestions(ProviderAssessment.model_validate(report))
        self.assertEqual(len(repairs), 1)
        self.assertEqual(unknowns, ["Possible broken frame"])

    def test_generated_quartiles_match_independent_fraction_oracle(self):
        rng = random.Random(20261006)
        for _ in range(1000):
            cents = sorted(rng.randrange(1, 100_000_000) for _ in range(rng.randrange(3, 13)))
            for fraction in [Fraction(1, 4), Fraction(3, 4)]:
                position = (len(cents) - 1) * fraction
                index = position.numerator // position.denominator
                expected = cents[index] + (cents[min(index + 1, len(cents) - 1)] - cents[index]) * (position - index)
                rounded_cents = (expected + Fraction(1, 2)).numerator // (expected + Fraction(1, 2)).denominator
                actual = quartile([Decimal(c) / 100 for c in cents], Decimal(fraction.numerator) / fraction.denominator)
                self.assertEqual(actual, Decimal(rounded_cents) / 100)


if __name__ == "__main__":
    unittest.main()
