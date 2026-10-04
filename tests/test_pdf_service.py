import copy
import unittest
from decimal import Decimal

from app.analysis_engine import analyze_deal
from app.models import AnalyzeRequest
from app.services.pdf_service import _fmt_usd, _styles, generate_lender_report


class PdfMoneyFormattingTests(unittest.TestCase):
    def test_whole_dollars_use_grouping_and_round_ties_away_from_zero(self):
        cases = [
            (13880.5, "$13,881"),
            (-13880.5, "-$13,881"),
            (2.5, "$3"),
            (-2.5, "-$3"),
            (1234567.875, "$1,234,568"),
            (-123456, "-$123,456"),
            ("270000", "$270,000"),
            (Decimal("13880.5"), "$13,881"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(_fmt_usd(value), expected)

    def test_rounded_zero_has_no_minus_sign(self):
        for value in (0, -0.0, -0.4, 0.49, Decimal("-0")):
            with self.subTest(value=value):
                self.assertEqual(_fmt_usd(value), "$0")
        self.assertEqual(_fmt_usd(-0.5), "-$1")
        self.assertEqual(_fmt_usd(0.5), "$1")

    def test_missing_invalid_and_nonfinite_values_show_dash(self):
        for value in (None, "—", "abc", float("inf"), float("-inf"),
                      float("nan"), "Infinity", "NaN"):
            with self.subTest(value=value):
                self.assertEqual(_fmt_usd(value), "—")

    def test_report_with_missing_arv_and_rehab_returns_pdf_without_mutation(self):
        result = analyze_deal(AnalyzeRequest(
            purchase_price=150000,
            arv=270000,
            rehab_budget=67000,
            holding_months=8,
        ))
        meta = {"property_address": "PDF regression fixture", "purchase_price": 150000}
        original_result = copy.deepcopy(result.model_dump())
        original_meta = copy.deepcopy(meta)

        pdf = generate_lender_report(result, meta)

        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(result.model_dump(), original_result)
        self.assertEqual(meta, original_meta)


class PdfVerdictLayoutTests(unittest.TestCase):
    def test_verdict_line_height_reserves_room_for_large_text(self):
        style = _styles()["verdict"]
        self.assertGreaterEqual(style.leading, style.fontSize * 1.2)

    def test_all_verdicts_generate_reports_without_mutating_inputs(self):
        cases = [("BUY", 135000, 240000, 45000, 6),
                 ("CONDITIONAL", 150000, 270000, 67000, 8),
                 ("PASS", 185000, 240000, 45000, 6)]
        for verdict, price, arv, rehab, months in cases:
            with self.subTest(verdict=verdict):
                result = analyze_deal(AnalyzeRequest(
                    purchase_price=price, arv=arv,
                    rehab_budget=rehab, holding_months=months,
                ))
                self.assertEqual(result.overall_verdict, verdict)
                meta = {"property_address": "Verdict layout fixture",
                        "purchase_price": price, "arv": arv,
                        "rehab_budget": rehab, "holding_months": months,
                        "interest_rate_pct": 10, "ltc_pct": 90}
                original_result = copy.deepcopy(result.model_dump())
                original_meta = copy.deepcopy(meta)

                pdf = generate_lender_report(result, meta)

                self.assertIsInstance(pdf, bytes)
                self.assertTrue(pdf.startswith(b"%PDF"))
                self.assertEqual(result.model_dump(), original_result)
                self.assertEqual(meta, original_meta)


if __name__ == "__main__":
    unittest.main()
