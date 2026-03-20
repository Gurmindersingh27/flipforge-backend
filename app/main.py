from __future__ import annotations

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DraftDeal,
    DraftFromUrlResponse,
    LenderReportRequest,
    NegotiationScriptRequest,
    NegotiationScriptResponse,
    SaveDealRequest,
    SavedDealResponse,
)
from .analysis_engine import analyze_deal
from .services.url_service import draft_from_url
from .services.pdf_service import generate_lender_report
from .services.script_service import generate_negotiation_script
from .db.init_db import init_db
from .db.session import get_db
from .db.models.saved_deal import SavedDeal
from .auth import get_current_user_id, preload_jwks

app = FastAPI(title="FlipForge API", version="0.1.0")


@app.on_event("startup")
def on_startup():
    """Initialize DB tables and preload Clerk JWKS on app boot."""
    init_db()
    preload_jwks()


# IMPORTANT: CORS so your frontend can call it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://flipforge-frontend.vercel.app",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
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


# ---------------------------------------------------------------------------
# Negotiation Script generation
# ---------------------------------------------------------------------------

@app.post("/api/generate/negotiation-script", response_model=NegotiationScriptResponse)
def generate_negotiation_script_endpoint(body: NegotiationScriptRequest):
    """
    Deterministic negotiation script from AnalyzeResponse + optional deal context.
    No LLM. Uses max_safe_offer, net_profit, total_project_cost, typed_flags.
    Integrity Gate: caller should only invoke when allowed_outputs.negotiation_script is True.
    """
    script = generate_negotiation_script(body)
    return NegotiationScriptResponse(negotiation_script=script)


# ---------------------------------------------------------------------------
# Saved Deals — auth-gated persistence (Clerk JWT required)
# All three routes require a valid Clerk session token.
# Existing analysis routes above are NOT touched and remain fully public.
# ---------------------------------------------------------------------------

@app.post("/api/deals/save", response_model=SavedDealResponse)
def save_deal(
    body: SaveDealRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Save an analyzed deal for the authenticated user."""
    record = SavedDeal(
        user_id=user_id,
        address=body.address,
        draft_input=body.draft_input,
        analysis_result=body.analysis_result,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return SavedDealResponse(
        id=record.id,
        user_id=record.user_id,
        address=record.address,
        draft_input=record.draft_input,
        analysis_result=record.analysis_result,
        created_at=record.created_at.isoformat(),
    )


@app.get("/api/deals", response_model=list[SavedDealResponse])
def list_deals(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return all saved deals for the authenticated user, newest first."""
    records = (
        db.query(SavedDeal)
        .filter(SavedDeal.user_id == user_id)
        .order_by(SavedDeal.created_at.desc())
        .all()
    )
    return [
        SavedDealResponse(
            id=r.id,
            user_id=r.user_id,
            address=r.address,
            draft_input=r.draft_input,
            analysis_result=r.analysis_result,
            created_at=r.created_at.isoformat(),
        )
        for r in records
    ]


@app.get("/api/deals/{deal_id}", response_model=SavedDealResponse)
def get_deal(
    deal_id: int,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return a single saved deal by ID. Returns 404 if not found or not owned by user."""
    record = (
        db.query(SavedDeal)
        .filter(SavedDeal.id == deal_id, SavedDeal.user_id == user_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Deal not found.")
    return SavedDealResponse(
        id=record.id,
        user_id=record.user_id,
        address=record.address,
        draft_input=record.draft_input,
        analysis_result=record.analysis_result,
        created_at=record.created_at.isoformat(),
    )
