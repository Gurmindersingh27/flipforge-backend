import json
import random
import unittest
from decimal import Decimal
from fractions import Fraction
from itertools import combinations, permutations

from app.item_assessment_models import ProviderAssessment
from app.services.item_evidence_service import quartile, select_evidence
from app.services.item_repair_catalog import suggestions
from test_item_assessment import provider_response


class EvidenceTests(unittest.TestCase):
    def repair_report(self, *jobs):
        report = json.loads(provider_response()["content"][0]["text"])
        report["repairs"] = [dict(job_id=job, reason=f"Observed {job}") for job in jobs]
        return ProviderAssessment.model_validate(report)

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

    def test_draft2_allowances_and_scopes_are_visible_without_rewriting_ai_reasons(self):
        expected = {
            "clean": (5, "Routine cleaning"),
            "scratch_touchup": (8, "Small cosmetic marks"),
            "sand_seat": (15, "This surface only"),
            "refinish_top": (30, "This surface only"),
            "paint_chair": (25, "Whole chair"),
            "paint_dresser": (50, "Whole small dresser"),
            "replace_knobs": (16, "Up to four basic knobs"),
            "glue_joint": (8, "One straightforward loose joint"),
            "seat_fabric": (25, "reuses sound foam and base"),
            "replace_glides": (6, "excludes replacement wooden feet"),
        }
        for job, (amount, scope) in expected.items():
            with self.subTest(job=job):
                repairs, unknowns = suggestions(self.repair_report(job))
                self.assertEqual(unknowns, [])
                self.assertEqual(repairs[0]["materials_cost"], amount)
                self.assertIn(scope, repairs[0]["label"])
                self.assertEqual(repairs[0]["reason"], f"Observed {job}")
                self.assertFalse(repairs[0]["confirmed"])

    def test_every_pair_in_both_orders_absorbs_only_four_whole_piece_paint_pairs(self):
        prices = dict(clean=5, scratch_touchup=8, sand_seat=15, refinish_top=30,
                      paint_chair=25, paint_dresser=50, replace_knobs=16,
                      glue_joint=8, seat_fabric=25, replace_glides=6)
        included = {
            frozenset(("clean", "paint_chair")): "clean",
            frozenset(("clean", "paint_dresser")): "clean",
            frozenset(("scratch_touchup", "paint_chair")): "scratch_touchup",
            frozenset(("scratch_touchup", "paint_dresser")): "scratch_touchup",
        }
        for pair in combinations(prices, 2):
            for jobs in permutations(pair):
                with self.subTest(jobs=jobs):
                    repairs, unknowns = suggestions(self.repair_report(*jobs))
                    self.assertEqual({r["job_id"] for r in repairs}, set(jobs))
                    absorbed = included.get(frozenset(jobs))
                    self.assertEqual(sum(r["materials_cost"] for r in repairs),
                                     sum(prices[job] for job in jobs) - (prices[absorbed] if absorbed else 0))
                    for row in repairs:
                        self.assertEqual(row["materials_cost"], 0 if row["job_id"] == absorbed else prices[row["job_id"]])
                        self.assertEqual("Included in" in row["label"], row["job_id"] == absorbed)
                        self.assertEqual(row["reason"], f'Observed {row["job_id"]}')
                    self.assertEqual(bool(unknowns), set(jobs) == {"paint_dresser", "refinish_top"})

    def test_two_paint_hosts_always_choose_catalog_order_and_keep_both_reasons(self):
        for jobs in permutations(("clean", "scratch_touchup", "paint_dresser", "paint_chair")):
            with self.subTest(jobs=jobs):
                report = self.repair_report(*jobs)
                before = report.model_dump()
                repairs, unknowns = suggestions(report)
                self.assertEqual(report.model_dump(), before)
                self.assertEqual(unknowns, [])
                self.assertEqual(sum(r["materials_cost"] for r in repairs), 75)
                for row in repairs:
                    self.assertEqual(row["reason"], f'Observed {row["job_id"]}')
                    if row["job_id"] in ("clean", "scratch_touchup"):
                        self.assertIn("Included in Prep and paint one chair", row["label"])
                        self.assertNotIn("Included in Prep and paint a small dresser", row["label"])
                        self.assertEqual(row["materials_cost"], 0)

    def test_painted_dresser_and_refinished_top_preserve_work_and_require_custom_total(self):
        for jobs in permutations(("clean", "paint_dresser", "refinish_top")):
            with self.subTest(jobs=jobs):
                report = self.repair_report(*jobs)
                report.repair_unknowns = ["Check the drawer runners"]
                repairs, unknowns = suggestions(report)
                self.assertEqual({r["job_id"]: r["materials_cost"] for r in repairs},
                                 dict(clean=0, paint_dresser=50, refinish_top=30))
                self.assertEqual(unknowns[0], "Check the drawer runners")
                self.assertEqual(len(unknowns), 2)
                self.assertIn("different surfaces or overlap", unknowns[1])
                self.assertIn("enter one total repair budget", unknowns[1])

    def test_duplicate_jobs_do_not_charge_twice_and_unsupported_work_stays_unknown(self):
        report = self.repair_report("clean", "paint_chair", "clean", "paint_chair", "new_legs")
        before = report.model_dump()
        repairs, unknowns = suggestions(report)
        self.assertEqual([(r["job_id"], r["materials_cost"]) for r in repairs],
                         [("clean", 0), ("paint_chair", 25)])
        self.assertEqual(unknowns, ["Observed new_legs"])
        self.assertEqual(report.model_dump(), before)

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
