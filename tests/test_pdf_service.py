import copy
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.analysis_engine import analyze_deal
from app.models import AnalyzeRequest
from app.services import pdf_service
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


class PdfInputDisplayTests(unittest.TestCase):
    def test_purchase_price_is_never_replaced_with_total_project_cost(self):
        result = analyze_deal(AnalyzeRequest(
            purchase_price=150000, arv=270000, rehab_budget=67000,
        ))
        self.assertEqual(result.total_project_cost, 252865)
        cases = [({}, "—"), ({"purchase_price": None}, "—"),
                 ({"purchase_price": 0}, "$0"),
                 ({"purchase_price": 150000}, "$150,000")]
        for price_meta, expected in cases:
            with self.subTest(meta=price_meta):
                meta = {"arv": 270000, "rehab_budget": 67000, **price_meta}
                self.assert_report_rows(result, meta, {
                    "Purchase Price": expected,
                    "Total Project Cost": "$252,865",
                    "After-Repair Value (ARV)": "$270,000",
                    "Rehab Budget": "$67,000",
                })

    def test_rehab_zero_is_preserved_and_missing_rehab_shows_dash(self):
        result = analyze_deal(AnalyzeRequest(
            purchase_price=150000, arv=270000, rehab_budget=0,
        ))
        total = _fmt_usd(result.total_project_cost)
        for rehab_meta, expected in [({}, "—"), ({"rehab_budget": None}, "—"),
                                     ({"rehab_budget": 0}, "$0")]:
            with self.subTest(meta=rehab_meta):
                meta = {"purchase_price": 150000, "arv": 270000, **rehab_meta}
                self.assert_report_rows(result, meta, {
                    "Purchase Price": "$150,000",
                    "Total Project Cost": total,
                    "Rehab Budget": expected,
                })

    def assert_report_rows(self, result, meta, expected):
        original_result = copy.deepcopy(result.model_dump())
        original_meta = copy.deepcopy(meta)
        with patch.object(pdf_service, "_kv_table", wraps=pdf_service._kv_table) as table:
            pdf = generate_lender_report(result, meta)
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b"%PDF"))
        rows = [row for call in table.call_args_list for row in call.args[0]]
        for label, value in expected.items():
            self.assertEqual([v for k, v in rows if k == label], [value])
        self.assertEqual(result.model_dump(), original_result)
        self.assertEqual(meta, original_meta)


if __name__ == "__main__":
    unittest.main()
