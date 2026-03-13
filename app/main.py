from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DraftDeal,
    DraftFromUrlResponse,
    LenderReportRequest,
)
from .analysis_engine import analyze_deal
from .services.url_service import draft_from_url
from .services.pdf_service import generate_lender_report

app = FastAPI(title="FlipForge API", version="0.1.0")

# IMPORTANT: CORS so your frontend can call it.
# In production, restrict this to your Vercel domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://flipforge-frontend.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    return analyze_deal(req)


# ---------------------------------------------------------------------------
# Phase 2 — URL extraction + draft flow
# ---------------------------------------------------------------------------

@app.post("/api/draft-from-url", response_model=DraftFromUrlResponse)
def draft_from_url_endpoint(body: dict):
    """
    Scrape a listing URL and return a DraftDeal with extracted data and
    confidence metadata. Always returns — never raises. Callers check
    draft.source for SOURCE_BLOCKED / EXTRACTION_ERROR.
    """
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="url is required")
    draft = draft_from_url(url)
    return DraftFromUrlResponse(draft=draft)


@app.post("/api/finalize-and-analyze", response_model=AnalyzeResponse)
def finalize_and_analyze(draft: DraftDeal):
    """
    Validate a completed DraftDeal and run the full analysis engine.
    Returns 422 with missing_fields if required DataPoints are null.
    """
    missing = []
    if draft.purchase_price.value is None:
        missing.append("purchase_price")
    if draft.arv.value is None:
        missing.append("arv")
    if draft.rehab_budget.value is None:
        missing.append("rehab_budget")

    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error": "MISSING_REQUIRED_FIELDS", "missing_fields": missing},
        )

    req = AnalyzeRequest(
        purchase_price=draft.purchase_price.value,
        arv=draft.arv.value,
        rehab_budget=draft.rehab_budget.value,
        closing_cost_pct=draft.closing_cost_pct,
        selling_cost_pct=draft.selling_cost_pct,
        holding_months=draft.holding_months,
        annual_interest_rate=draft.annual_interest_rate,
        loan_to_cost_pct=draft.loan_to_cost_pct,
        required_profit_margin_pct=draft.required_profit_margin_pct,
        est_monthly_rent=(
            draft.est_monthly_rent.value
            if draft.est_monthly_rent.value is not None
            else None
        ),
        region=draft.region,
    )
    return analyze_deal(req)


# ---------------------------------------------------------------------------
# Lender report export
# ---------------------------------------------------------------------------

@app.post("/api/export/lender-report")
def export_lender_report(body: LenderReportRequest):
    """
    Generate a lender-ready PDF from the analysis result.
    Returns raw PDF bytes with Content-Disposition: attachment.
    Integrity Gate: if overall_verdict is PASS the PDF is still generated
    but the report itself contains a risk warning (see pdf_service).
    """
    pdf_bytes = generate_lender_report(body.result, body.meta)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="flipforge_lender_report.pdf"'
        },
    )
