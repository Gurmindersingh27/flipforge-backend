import unittest
from math import nextafter, inf

from app.analysis_engine import analyze_deal, cap_verdict_for_required_return
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

    def test_actual_return_boundary_not_rounded_offer(self):
        request = AnalyzeRequest(purchase_price=150000, arv=270000, rehab_budget=67000, holding_months=8)
        actual = analyze_deal(request).profit_pct
        for target, expected in [(nextafter(actual, -inf), "BUY"), (actual, "BUY"),
                                 (nextafter(actual, inf), "CONDITIONAL")]:
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

    def test_zero_target_and_existing_hard_fail(self):
        for target in (0.0, None):
            result = analyze_deal(AnalyzeRequest(purchase_price=150000, arv=270000,
                                 rehab_budget=67000, holding_months=8, required_profit_margin_pct=target))
            self.assertEqual(result.overall_verdict, "BUY")
        result = analyze_deal(AnalyzeRequest(purchase_price=185000, arv=240000,
                             rehab_budget=45000, required_profit_margin_pct=0.0))
        self.assertEqual(result.overall_verdict, "PASS")
        self.assertFalse(any(result.allowed_outputs.values()))


if __name__ == "__main__":
    unittest.main()
