import unittest
from math import nextafter, inf

from app.analysis_engine import analyze_deal, cap_verdict_for_required_return, verdict_from_score
from app.models import AnalyzeRequest


class RequiredReturnTests(unittest.TestCase):
    def test_cap_boundaries_and_no_upgrades(self):
        for target in (0.0, 0.12, 1.0):
            with self.subTest(target=target):
                self.assertEqual(cap_verdict_for_required_return("BUY", target, target), "BUY")
                self.assertEqual(cap_verdict_for_required_return("BUY", nextafter(target, inf), target), "BUY")
                self.assertEqual(cap_verdict_for_required_return("BUY", nextafter(target, -inf), target), "CONDITIONAL")
                for verdict in ("PASS", "CONDITIONAL"):
                    for actual in (-0.1, 0.0, 0.5, 1.0):
                        self.assertEqual(cap_verdict_for_required_return(verdict, actual, target), verdict)

    def test_samples_and_locked_scenarios(self):
        # Independent recorded outputs at efe27e0; only the two sample verdicts change.
        cases = [
            (185000, 240000, 45000, 6, "PASS", 137700, -25100, 265100, 12),
            (135000, 240000, 45000, 6, "BUY", 137700, 28650, 211350, 86),
            (200000, 345000, 50000, 6, "BUY", 212300, 50150, 294850, 93),
            (150000, 270000, 50000, 6, "BUY", 155600, 34900, 235100, 88),
            (150000, 270000, 67000, 6, "CONDITIONAL", 139000, 17135, 252865, 54),
            (150000, 270000, 67000, 8, "CONDITIONAL", 136200, 13880, 256120, 37),
        ]
        for price, arv, rehab, months, verdict, offer, profit, cost, confidence in cases:
            with self.subTest(price=price, rehab=rehab, months=months):
                result = analyze_deal(AnalyzeRequest(
                    purchase_price=price, arv=arv, rehab_budget=rehab, holding_months=months,
                ))
                self.assertEqual(result.overall_verdict, verdict)
                self.assertEqual((result.max_safe_offer, result.net_profit, result.total_project_cost,
                                  result.confidence_score), (offer, profit, cost, confidence))
                if verdict == "CONDITIONAL":
                    self.assertEqual(result.flip_verdict, "CONDITIONAL")
                    self.assertTrue(all(result.allowed_outputs.values()))
                for stress in result.stress_tests:
                    if stress.profit_pct < 0.12:
                        self.assertNotEqual(stress.verdict, "BUY")

    def test_target_boundary_honors_rounded_ceiling_but_not_material_shortfall(self):
        request = AnalyzeRequest(purchase_price=150000, arv=270000, rehab_budget=67000, holding_months=8)
        actual = analyze_deal(request).profit_pct
        for target, expected in [(nextafter(actual, -inf), "BUY"), (actual, "BUY"),
                                 (nextafter(actual, inf), "BUY"), (actual + 0.001, "CONDITIONAL")]:
            result = analyze_deal(request.model_copy(update={"required_profit_margin_pct": target}))
            self.assertEqual(result.overall_verdict, expected)
            self.assertEqual(result.flip_verdict, expected)
            self.assertEqual(result.stress_tests[0].verdict, expected)
            self.assertEqual(result.net_profit, 13880)

    def test_brrrr_score_cannot_bypass_overall_cap(self):
        result = analyze_deal(AnalyzeRequest(purchase_price=150000, arv=270000,
                             rehab_budget=67000, holding_months=8, est_monthly_rent=5000))
        self.assertEqual(result.best_strategy, "brrrr")
        self.assertEqual(result.brrrr_verdict, "BUY")  # Strategy score remains independent.
        self.assertEqual(result.overall_verdict, "CONDITIONAL")
        self.assertTrue(any("modeled resale return" in note for note in result.notes))

    def test_zero_target_and_existing_hard_fail(self):
        for target in (0.0, None):
            result = analyze_deal(AnalyzeRequest(purchase_price=150000, arv=270000,
                                 rehab_budget=67000, holding_months=8, required_profit_margin_pct=target))
            self.assertEqual(result.overall_verdict, "BUY")
        result = analyze_deal(AnalyzeRequest(purchase_price=185000, arv=240000,
                             rehab_budget=45000, required_profit_margin_pct=0.0))
        self.assertEqual(result.overall_verdict, "PASS")
        self.assertFalse(any(result.allowed_outputs.values()))

    def test_offers_at_their_own_ceiling_across_assumptions(self):
        for arv, rehab in [(270000, 50000), (345000, 50000), (410000, 85000),
                           (199000, 33000), (240000, 45000), (270000, 67000), (180000, 20000)]:
            for months in (6, 8, 18):
                for rate in (0.0, 0.10, 0.18):
                    for target in (0.05, 0.12, 0.20):
                        request = AnalyzeRequest(purchase_price=100000, arv=arv, rehab_budget=rehab,
                                                 holding_months=months, annual_interest_rate=rate,
                                                 required_profit_margin_pct=target)
                        ceiling = analyze_deal(request).max_safe_offer
                        with self.subTest(arv=arv, rehab=rehab, months=months, rate=rate, target=target):
                            at = analyze_deal(request.model_copy(update={"purchase_price": ceiling}))
                            # The cap must not downgrade at the reported ceiling. A weak score can
                            # still produce CONDITIONAL/PASS; the ceiling does not promise BUY.
                            self.assertEqual(at.flip_verdict, verdict_from_score(at.flip_score))
                            self.assertEqual(at.overall_verdict, verdict_from_score(max(
                                at.flip_score, at.brrrr_score, at.wholesale_score)))
                            above = analyze_deal(request.model_copy(update={"purchase_price": ceiling + 100}))
                            self.assertLess(above.profit_pct, target)
                            self.assertNotEqual(above.overall_verdict, "BUY")
                            if at.profit_pct < target:
                                penny_above = analyze_deal(request.model_copy(update={"purchase_price": ceiling + 0.01}))
                                self.assertNotEqual(penny_above.overall_verdict, "BUY")
                                self.assertTrue(any("rounded to the nearest $100" in note for note in at.notes))

    def test_stress_uses_its_own_ceiling_not_the_base_ceiling(self):
        request = AnalyzeRequest(purchase_price=155600, arv=270000, rehab_budget=50000)
        result = analyze_deal(request)
        self.assertEqual(result.overall_verdict, "BUY")
        self.assertEqual(result.stress_tests[0].verdict, "BUY")
        for stress in result.stress_tests[1:]:
            scenario = request.model_copy(update={"arv": stress.arv, "rehab_budget": stress.rehab_budget,
                                                  "holding_months": stress.holding_months})
            independent = analyze_deal(scenario)
            self.assertEqual(stress.verdict, independent.flip_verdict)
            self.assertNotEqual(stress.verdict, "BUY")

    def test_ceiling_exception_never_approves_negative_profit_or_zero_ceiling(self):
        self.assertEqual(cap_verdict_for_required_return("BUY", -0.00001, 0.0,
                         purchase_price=100, max_safe_offer=100), "CONDITIONAL")
        self.assertEqual(cap_verdict_for_required_return("BUY", 0.0, 0.12,
                         purchase_price=0, max_safe_offer=0), "CONDITIONAL")
        for verdict in ("PASS", "CONDITIONAL"):
            self.assertEqual(cap_verdict_for_required_return(verdict, 0.1199, 0.12,
                             purchase_price=155600, max_safe_offer=155600), verdict)


if __name__ == "__main__":
    unittest.main()
