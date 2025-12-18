from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .models import AnalyzeRequest, AnalyzeResponse
from .analysis_engine import analyze_deal

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


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    return analyze_deal(req)
