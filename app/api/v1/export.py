from io import BytesIO
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

# IMPORTANT:
# - Do NOT put a prefix here.
# - Prefix is set in main.py via app.include_router(..., prefix="/api/v1/export")
router = APIRouter()

# ----------------------------
# Meta key normalization layer
# ----------------------------
def _meta_get(meta: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in meta and meta.get(k) is not None:
            return meta.get(k)
    return default

def _meta_rate_decimal(meta: Dict[str, Any]) -> float:
    # accept either annual_interest_rate OR interest_rate_pct
    raw = _meta_get(meta, "annual_interest_rate", "interest_rate_pct", default=0.0)
    return _to_decimal_rate(raw)

def _meta_ltc_decimal(meta: Dict[str, Any]) -> float:
    # accept either loan_to_cost_pct OR ltc_pct
    raw = _meta_get(meta, "loan_to_cost_pct", "ltc_pct", default=0.0)
    return _to_decimal_pct(raw)

class LenderReportRequest(BaseModel):
    # Keep loose to avoid schema friction:
    # Frontend POSTs { result: AnalyzeResponse, meta?: {...} }
    result: Dict[str, Any]
    meta: Optional[Dict[str, Any]] = None


def _money(n: Any) -> str:
    try:
        x = float(n)
    except Exception:
        return "—"
    return f"${x:,.0f}"


def _pct_decimal(n: Any) -> str:
    # UI treats profit_pct and annualized_roi as decimals (e.g., 0.15 => 15.0%)
    try:
        x = float(n)
    except Exception:
        return "—"
    return f"{x * 100:.1f}%"


def _safe_float(n: Any, default: float = 0.0) -> float:
    try:
        return float(n)
    except Exception:
        return default


def _to_decimal_rate(x: Any) -> float:
    """
    Accepts either:
      - 0.10 (10% as decimal)
      - 10   (10% as percent)
    Returns decimal (0.10).
    """
    v = _safe_float(x, 0.0)
    if v <= 0:
        return 0.0
    return v / 100.0 if v > 1.0 else v


def _to_decimal_pct(x: Any) -> float:
    """
    Accepts either:
      - 0.80 (80% as decimal)
      - 80   (80% as percent)
    Returns decimal (0.80).
    """
    v = _safe_float(x, 0.0)
    if v <= 0:
        return 0.0
    return v / 100.0 if v > 1.0 else v


# ----------------------------
# Meta key normalization layer
# ----------------------------
def _meta_get(meta: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in meta and meta.get(k) is not None:
            return meta.get(k)
    return default

def _derive_monthly_interest_carry(result: Dict[str, Any], meta: Dict[str, Any]) -> float:
    total_cost = _safe_float(result.get("total_project_cost"), 0.0)

    annual_rate_dec = _meta_rate_decimal(meta)
    ltc_dec = _meta_ltc_decimal(meta)

    if total_cost <= 0 or annual_rate_dec <= 0 or ltc_dec <= 0:
        return 0.0

    loan_amount = total_cost * ltc_dec
    return loan_amount * (annual_rate_dec / 12.0)

def _draw_kv(
    c: canvas.Canvas, x: float, y: float, k: str, v: str, k_w: float = 160
) -> float:
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, k)
    c.setFont("Helvetica", 10)
    c.drawString(x + k_w, y, v)
    return y - 14

@router.post("/lender-report")
def export_lender_report(payload: LenderReportRequest):
    result = payload.result or {}
    meta = payload.meta or {}

    # Respect Integrity Gate (if present)
    allowed = result.get("allowed_outputs") or {}
    can_report = bool(allowed.get("lender_report", True))  # default True for backward-compat
    if not can_report:
        raise HTTPException(status_code=403, detail="Suppressed by Integrity Gate")

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER  # width unused in v0, but fine

    # ---------- Page 1: Executive Summary ----------
    margin_x = 0.8 * inch
    y = height - 0.9 * inch

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin_x, y, "FlipForge — Lender-Style Report (V0)")
    y -= 18

    c.setFont("Helvetica", 9)
    c.drawString(margin_x, y, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 14

    # ✅ accept both new UI keys and legacy keys
    address = _meta_get(meta, "address", "property_address", default="—") or "—"
    url = _meta_get(meta, "url", "listing_url", default="—") or "—"

    c.drawString(margin_x, y, f"Property: {address}")
    y -= 12
    c.drawString(margin_x, y, f"Listing URL: {url}")
    y -= 18

    verdict = result.get("overall_verdict", "—")
    confidence = result.get("confidence_score", "—")
    best_strategy = result.get("best_strategy", "—")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, f"OVERALL VERDICT: {verdict}")
    y -= 18

    c.setFont("Helvetica", 10)
    y = _draw_kv(c, margin_x, y, "Confidence Score:", f"{confidence}/100")
    y = _draw_kv(c, margin_x, y, "Primary Strategy:", str(best_strategy))

    y -= 8
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, y, "Deal Snapshot")
    y -= 18

    purchase_price = meta.get("purchase_price", "—")
    arv = meta.get("arv", "—")
    rehab = meta.get("rehab_budget", "—")

    y = _draw_kv(c, margin_x, y, "Purchase Price:", _money(purchase_price))
    y = _draw_kv(c, margin_x, y, "ARV:", _money(arv))
    y = _draw_kv(c, margin_x, y, "Rehab Budget:", _money(rehab))

    y -= 6
    y = _draw_kv(c, margin_x, y, "Total Project Cost:", _money(result.get("total_project_cost")))
    y = _draw_kv(c, margin_x, y, "Max Safe Offer:", _money(result.get("max_safe_offer")))
    y -= 10

    verdict_reason = result.get("verdict_reason") or ""
    if verdict_reason:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin_x, y, "Verdict Reason:")
        y -= 14
        c.setFont("Helvetica", 10)
        c.drawString(margin_x, y, verdict_reason[:1200])

    c.showPage()

    # ---------- Page 2: Profitability & Returns ----------
    y = height - 0.9 * inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, "Profitability & Returns")
    y -= 22

    y = _draw_kv(c, margin_x, y, "Gross Profit:", _money(result.get("gross_profit")))
    y = _draw_kv(c, margin_x, y, "Net Profit:", _money(result.get("net_profit")))
    y = _draw_kv(c, margin_x, y, "Profit %:", _pct_decimal(result.get("profit_pct")))
    y = _draw_kv(c, margin_x, y, "Annualized ROI:", _pct_decimal(result.get("annualized_roi")))

    hold_mo = _meta_get(meta, "holding_months", default=result.get("holding_months", "—"))
    y = _draw_kv(c, margin_x, y, "Hold Duration (mo):", str(hold_mo))

    y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_x, y, "Interpretation (auto)")
    y -= 14
    c.setFont("Helvetica", 10)
    net_profit = _safe_float(result.get("net_profit"), 0.0)
    if net_profit <= 0:
        c.drawString(
            margin_x,
            y,
            "Base case is negative. Proceed only with corrected inputs or a lower basis.",
        )
    else:
        c.drawString(
            margin_x,
            y,
            "Base case is positive assuming inputs are accurate. Review stress and execution risk.",
        )
    c.showPage()

    # ---------- Page 3: Time & Burn Rate (V0 interest-only) ----------
    y = height - 0.9 * inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, "Time & Burn Rate (V0)")
    y -= 22

    hold_months = _safe_float(_meta_get(meta, "holding_months", default=result.get("holding_months", 0)), 0.0)
    monthly_carry = _derive_monthly_interest_carry(result, meta)
    total_carry = monthly_carry * hold_months

    y = _draw_kv(
        c, margin_x, y, "Estimated Hold Time (mo):", f"{hold_months:.0f}" if hold_months else "—"
    )
    y = _draw_kv(
        c,
        margin_x,
        y,
        "Monthly Carry (interest-only est.):",
        _money(monthly_carry) if monthly_carry else "—",
    )
    y = _draw_kv(c, margin_x, y, "Total Carry Over Hold:", _money(total_carry) if total_carry else "—")

    y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_x, y, "Note")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(
        margin_x,
        y,
        "V0 carry uses interest-only estimate. Taxes/insurance/utilities can be added later without changing the engine.",
    )

    c.showPage()

    # ---------- Page 4: Lender Cushion View (LTV/LTC) ----------
    y = height - 0.9 * inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, "Lender Cushion View (LTV / LTC)")
    y -= 22

    total_cost = _safe_float(result.get("total_project_cost"), 0.0)
    pp = _safe_float(_meta_get(meta, "purchase_price", default=0.0), 0.0)
    arv_val = _safe_float(_meta_get(meta, "arv", default=0.0), 0.0)

    ltc_dec = _to_decimal_pct(_meta_get(meta, "loan_to_cost_pct", "ltc_pct", default=0.0))
    loan_amount = total_cost * ltc_dec if (total_cost > 0 and ltc_dec > 0) else 0.0

    as_is_ltv = (loan_amount / pp) if (loan_amount > 0 and pp > 0) else 0.0
    as_is_ltc = (loan_amount / total_cost) if (loan_amount > 0 and total_cost > 0) else 0.0
    post_rehab_ltv = (loan_amount / arv_val) if (loan_amount > 0 and arv_val > 0) else 0.0

    haircut_arv = arv_val * 0.95 if arv_val > 0 else 0.0
    post_rehab_ltv_haircut = (loan_amount / haircut_arv) if (loan_amount > 0 and haircut_arv > 0) else 0.0

    # Show assumptions used
    y = _draw_kv(c, margin_x, y, "Loan-to-Cost (input):", f"{ltc_dec*100:.1f}%" if ltc_dec else "—")
    y = _draw_kv(c, margin_x, y, "Implied Loan Amount:", _money(loan_amount) if loan_amount else "—")

    y -= 8
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, y, "Ratios")
    y -= 18

    y = _draw_kv(c, margin_x, y, "As-Is LTV (Loan / Purchase):", f"{as_is_ltv*100:.1f}%" if as_is_ltv else "—")
    y = _draw_kv(c, margin_x, y, "As-Is LTC (Loan / Total Cost):", f"{as_is_ltc*100:.1f}%" if as_is_ltc else "—")
    y = _draw_kv(c, margin_x, y, "Post-Rehab LTV (Loan / ARV):", f"{post_rehab_ltv*100:.1f}%" if post_rehab_ltv else "—")
    y = _draw_kv(
        c,
        margin_x,
        y,
        "Post-Rehab LTV (ARV - 5%):",
        f"{post_rehab_ltv_haircut*100:.1f}%" if post_rehab_ltv_haircut else "—",
    )

    y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_x, y, "Note")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(
        margin_x,
        y,
        "Ratios use UI inputs (Purchase/ARV/LTC). ARV haircut is a simple valuation stress, not a full scenario engine.",
    )

    c.showPage()
    c.save()

    pdf_bytes = buf.getvalue()
    buf.close()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="flipforge_lender_report_v0.pdf"'},
    )
