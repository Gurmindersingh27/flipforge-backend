from __future__ import annotations

import traceback
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DraftFromUrlRequest,
    DraftFromUrlResponse,
    DraftDeal,
    DraftFinalizeError,
    finalize_draft_to_request,
)
from app.analysis_engine import analyze_deal
from app.opengraph_extractor import extract_from_url

# NEW: export router
from app.api.v1.export import router as export_router

# Import modules for debugging
import app.models as models_mod
import app.analysis_engine as engine_mod

app = FastAPI(title="FlipForge API", version="0.1.0")

# IMPORTANT: CORS so your frontend can call it.
# In production, restrict this to your Vercel domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# NEW: register export routes
app.include_router(export_router, prefix="/api/export", tags=["Export"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


# =========================================================
# 🔍 DEBUG: Module paths endpoint
# =========================================================

@app.get("/api/debug/module-paths")
def debug_module_paths():
    """Shows which files Python is actually loading for models and analysis_engine."""
    return {
        "models_file": getattr(models_mod, "__file__", None),
        "analysis_engine_file": getattr(engine_mod, "__file__", None),
    }


# =========================================================
# Main endpoints
# =========================================================

@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    return analyze_deal(req)


@app.post("/api/draft-from-url", response_model=DraftFromUrlResponse)
def draft_from_url(payload: DraftFromUrlRequest):
    """
    Extract property data from URL using Open Graph tags.
    Best-effort extraction - never fails, returns partial data.
    """
    draft = extract_from_url(payload.url)
    return DraftFromUrlResponse(draft=draft)


@app.post("/api/finalize-and-analyze", response_model=AnalyzeResponse)
def finalize_and_analyze(draft: DraftDeal):
    """
    Bridge: DraftDeal -> AnalyzeRequest -> AnalyzeResponse
    Debug-friendly: returns exact exception with full traceback.
    """
    try:
        req = finalize_draft_to_request(draft)
    except DraftFinalizeError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "MISSING_REQUIRED_FIELDS",
                "missing_fields": e.missing_fields,
            },
        )

    try:
        return analyze_deal(req)
    except Exception as e:
        # TEMP DEBUG: surface the real error with FULL TRACEBACK
        raise HTTPException(
            status_code=500,
            detail={
                "error": type(e).__name__,
                "message": str(e),
                "trace": traceback.format_exc(),
            },
        )
