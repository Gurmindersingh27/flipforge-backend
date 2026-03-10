"""
Lender report PDF generator for FlipForge.

Uses reportlab (pure Python, no system dependencies).
Generates a clean investment memo matching the lender report spec:
  1. Property Summary
  2. Deal Overview
  3. Financial Assumptions
  4. Analysis Output (profit, ROI, MAO, verdict, confidence)
  5. Risk Notes (breakpoints, stress tests, risk flags)
  6. Exit Strategy notes

Returns raw bytes — caller sets Content-Type: application/pdf.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import AnalyzeResponse

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_PAGE_W, _PAGE_H = letter
_MARGIN = 0.75 * inch

_DARK = colors.HexColor("#0f172a")
_MID  = colors.HexColor("#334155")
_LIGHT = colors.HexColor("#94a3b8")
_WHITE = colors.white

# Verdict colors as plain hex strings (used in Paragraph markup)
_VERDICT_COLORS: dict[str, str] = {
    "BUY":         "#10b981",
    "CONDITIONAL": "#f59e0b",
    "PASS":        "#ef4444",
}

# Verdict colors as reportlab Color objects (used in TableStyle)
_VERDICT_RL_COLORS: dict[str, Any] = {
    "BUY":         colors.HexColor("#10b981"),
    "CONDITIONAL": colors.HexColor("#f59e0b"),
    "PASS":        colors.HexColor("#ef4444"),
}


def _verdict_color_str(v: str) -> str:
    """Returns a plain CSS-style hex string for use in Paragraph markup."""
    return _VERDICT_COLORS.get(v, "#94a3b8")


def _verdict_color_rl(v: str) -> Any:
    """Returns a reportlab Color object for use in TableStyle commands."""
    return _VERDICT_RL_COLORS.get(v, _LIGHT)


def _fmt_usd(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: Any, decimals: int = 1) -> str:
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# Paragraph style factory
# ---------------------------------------------------------------------------

def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ff_title",
            parent=base["Title"],
            fontSize=18,
            textColor=_DARK,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "ff_subtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor=_LIGHT,
            spaceAfter=12,
        ),
        "section": ParagraphStyle(
            "ff_section",
            parent=base["Heading2"],
            fontSize=11,
            textColor=_DARK,
            spaceBefore=14,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "ff_body",
            parent=base["Normal"],
            fontSize=9,
            textColor=_MID,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "ff_small",
            parent=base["Normal"],
            fontSize=8,
            textColor=_LIGHT,
            spaceAfter=2,
        ),
        "verdict": ParagraphStyle(
            "ff_verdict",
            parent=base["Normal"],
            fontSize=22,
            fontName="Helvetica-Bold",
            spaceAfter=4,
        ),
        "flag": ParagraphStyle(
            "ff_flag",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#ef4444"),
            spaceAfter=2,
        ),
    }


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

_TABLE_STYLE = TableStyle([
    ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#f1f5f9")),
    ("TEXTCOLOR",     (0, 0), (-1, 0),  _DARK),
    ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
    ("FONTSIZE",      (0, 0), (-1, -1), 9),
    ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_WHITE, colors.HexColor("#f8fafc")]),
    ("GRID",          (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
    ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ("TOPPADDING",    (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
])


def _kv_table(rows: list, col_widths=None) -> Table:
    if col_widths is None:
        usable = _PAGE_W - 2 * _MARGIN
        col_widths = [usable * 0.40, usable * 0.60]
    t = Table(rows, colWidths=col_widths)
    t.setStyle(_TABLE_STYLE)
    return t


def _section_hr(story: list) -> None:
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 2))


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_lender_report(result: AnalyzeResponse, meta: Dict[str, Any]) -> bytes:
    """
    Build lender report PDF and return as raw bytes.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title="FlipForge Lender Report",
    )

    s = _styles()
    story = []

    # -----------------------------------------------------------------------
    # 1. Header
    # -----------------------------------------------------------------------
    story.append(Paragraph("FlipForge", s["title"]))
    story.append(Paragraph("Deal Analysis &amp; Lender Report", s["subtitle"]))
    _section_hr(story)

    # -----------------------------------------------------------------------
    # 2. Property Summary
    # -----------------------------------------------------------------------
    story.append(Paragraph("Property Summary", s["section"]))

    address = meta.get("property_address") or "—"
    url     = meta.get("listing_url") or "—"

    story.append(_kv_table([
        ("Property Address", address),
        ("Listing URL", url if len(url) <= 80 else url[:77] + "…"),
        ("Analysis Date", _today()),
    ]))
    story.append(Spacer(1, 8))

    # -----------------------------------------------------------------------
    # 3. Deal Overview
    # -----------------------------------------------------------------------
    story.append(Paragraph("Deal Overview", s["section"]))

    pp    = meta.get("purchase_price") or result.total_project_cost
    arv   = meta.get("arv") or "—"
    rehab = meta.get("rehab_budget") or "—"
    rent  = meta.get("est_monthly_rent")

    story.append(_kv_table([
        ("Purchase Price",            _fmt_usd(pp)),
        ("After-Repair Value (ARV)",  _fmt_usd(arv)),
        ("Rehab Budget",              _fmt_usd(rehab)),
        ("Est. Monthly Rent",         _fmt_usd(rent) if rent else "Not provided"),
        ("Best Strategy",             str(result.best_strategy or "—").upper()),
    ]))
    story.append(Spacer(1, 8))

    # -----------------------------------------------------------------------
    # 4. Financial Assumptions
    # -----------------------------------------------------------------------
    story.append(Paragraph("Financial Assumptions", s["section"]))

    story.append(_kv_table([
        ("Holding Months",       str(meta.get("holding_months", "—"))),
        ("Annual Interest Rate", f"{meta.get('interest_rate_pct', '—')}%"),
        ("Loan-to-Cost (LTC)",   f"{meta.get('ltc_pct', '—')}%"),
    ]))
    story.append(Spacer(1, 8))

    # -----------------------------------------------------------------------
    # 5. Analysis Output
    # -----------------------------------------------------------------------
    story.append(Paragraph("Analysis Output", s["section"]))

    verdict    = result.overall_verdict or "—"
    vc_str     = _verdict_color_str(verdict)   # plain "#10b981" etc.

    # Verdict as prominent colored text — use plain hex string in markup
    vp = Paragraph(
        f'<font color="{vc_str}"><b>{verdict}</b></font>',
        s["verdict"],
    )
    story.append(vp)

    story.append(_kv_table([
        ("Total Project Cost",    _fmt_usd(result.total_project_cost)),
        ("Net Profit",            _fmt_usd(result.net_profit)),
        ("Profit Margin",         _fmt_pct(result.profit_pct)),
        ("Annualized ROI",        _fmt_pct(result.annualized_roi)),
        ("Max Safe Offer (MAO)",  _fmt_usd(result.max_safe_offer)),
        ("Confidence Score",      f"{result.confidence_score}/100"),
        ("Flip Score",            f"{result.flip_score}/100"),
        ("BRRRR Score",           f"{result.brrrr_score}/100"),
        ("Wholesale Score",       f"{result.wholesale_score}/100"),
    ]))
    story.append(Spacer(1, 8))

    # Rehab Reality
    if result.rehab_reality:
        rr = result.rehab_reality
        story.append(Paragraph("Rehab Reality", s["section"]))
        story.append(_kv_table([
            ("Rehab / Purchase Ratio",    f"{rr.rehab_ratio * 100:.1f}%"),
            ("Severity Classification",   rr.severity),
            ("Suggested Contingency",     _fmt_pct(rr.contingency_pct)),
            ("Est. Additional Hold",      f"{rr.added_holding_months} month(s)"),
        ]))
        story.append(Spacer(1, 8))

    # -----------------------------------------------------------------------
    # 6. Risk Notes
    # -----------------------------------------------------------------------
    story.append(Paragraph("Risk Notes", s["section"]))

    # Breakpoints
    if result.breakpoints:
        bp = result.breakpoints
        if bp.first_break_scenario:
            story.append(Paragraph(
                f"<b>Deal Fragility:</b> First break at scenario "
                f"<b>{bp.first_break_scenario}</b> ({bp.break_reason or 'verdict fail'}).",
                s["flag"],
            ))
        else:
            story.append(Paragraph(
                "<b>Deal Durability:</b> Holds under all built-in stress scenarios.",
                s["body"],
            ))
        story.append(Spacer(1, 4))

    # Risk flags
    if result.typed_flags:
        story.append(Paragraph("<b>Risk Flags:</b>", s["body"]))
        for flag in result.typed_flags:
            sev_color = "#ef4444" if flag.severity == "critical" else "#f59e0b"
            story.append(Paragraph(
                f'<font color="{sev_color}">▸ {flag.label} ({flag.severity})</font>',
                s["body"],
            ))
        story.append(Spacer(1, 4))

    # Stress tests table
    if result.stress_tests:
        story.append(Paragraph("<b>Stress Test Scenarios:</b>", s["body"]))
        usable  = _PAGE_W - 2 * _MARGIN
        st_cols = [
            usable * 0.22, usable * 0.15, usable * 0.15,
            usable * 0.15, usable * 0.15, usable * 0.18,
        ]
        st_rows = [["Scenario", "ARV", "Rehab", "Net Profit", "Margin", "Verdict"]]
        for sc in result.stress_tests:
            st_rows.append([
                sc.name,
                _fmt_usd(sc.arv),
                _fmt_usd(sc.rehab_budget),
                _fmt_usd(sc.net_profit),
                _fmt_pct(sc.profit_pct),
                sc.verdict,
            ])
        st = Table(st_rows, colWidths=st_cols)
        st_style = list(_TABLE_STYLE._cmds)
        for i, sc in enumerate(result.stress_tests, start=1):
            c = _verdict_color_rl(sc.verdict)   # reportlab Color for TableStyle
            st_style.append(("TEXTCOLOR", (5, i), (5, i), c))
            st_style.append(("FONTNAME",  (5, i), (5, i), "Helvetica-Bold"))
        st.setStyle(TableStyle(st_style))
        story.append(st)
        story.append(Spacer(1, 8))

    # Notes
    if result.notes:
        story.append(Paragraph("<b>Analysis Notes:</b>", s["body"]))
        for note in result.notes:
            story.append(Paragraph(f"• {note}", s["body"]))
        story.append(Spacer(1, 8))

    # -----------------------------------------------------------------------
    # 7. Exit Strategy
    # -----------------------------------------------------------------------
    story.append(Paragraph("Exit Strategy", s["section"]))

    best = (result.best_strategy or "flip").lower()
    strategy_notes = {
        "flip": (
            "Recommended exit: Fix-and-Flip. "
            "Focus on ARV accuracy and rehab timeline. "
            "Selling costs and carry are the primary margin risks."
        ),
        "brrrr": (
            "Recommended exit: BRRRR (Buy, Rehab, Rent, Refinance, Repeat). "
            "Verify rent-to-cost ratio supports DSCR on the refi. "
            "Long-term hold — confirm rental market depth."
        ),
        "wholesale": (
            "Recommended exit: Wholesale / Assignment. "
            "Spread between purchase price and MAO is the assignable fee. "
            "Buyer must be identified before earnest money risk grows."
        ),
    }
    story.append(Paragraph(strategy_notes.get(best, "See analysis output."), s["body"]))

    # -----------------------------------------------------------------------
    # Footer disclaimer
    # -----------------------------------------------------------------------
    story.append(Spacer(1, 16))
    _section_hr(story)
    story.append(Paragraph(
        "Generated by FlipForge. For informational purposes only. "
        "Verify all inputs independently before making investment decisions.",
        s["small"],
    ))

    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _today() -> str:
    from datetime import date
    return date.today().strftime("%B %d, %Y")
