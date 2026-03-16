from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field


Severity = Literal["critical", "moderate", "mild"]
Verdict = Literal["BUY", "CONDITIONAL", "PASS"]
Strategy = Literal["flip", "brrrr", "wholesale"]
Confidence = Literal["HIGH", "MEDIUM", "LOW", "MISSING"]
RehabSeverity = Literal["LIGHT", "MEDIUM", "HEAVY", "EXTREME"]
BreakpointReason = Literal["NEGATIVE_PROFIT", "BELOW_MARGIN", "VERDICT_FAIL"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class DataPoint(BaseModel):
    """A scraped or user-supplied value with extraction confidence metadata."""
    value: Optional[float] = None
    confidence: Confidence = "MISSING"
    source: Optional[str] = None
    evidence: Optional[str] = None


class RehabReality(BaseModel):
    """Derived rehab risk classification based on rehab/purchase ratio."""
    rehab_ratio: float
    severity: RehabSeverity
    contingency_pct: float        # suggested cost buffer (e.g. 0.15 = 15%)
    added_holding_months: int     # expected timeline slip
    confidence_penalty: int       # score penalty points


class Breakpoints(BaseModel):
    """
    Derived from stress tests: identifies the first scenario that kills the deal.
    Labeled as preliminary — based on the 5 built-in stress scenarios.
    """
    first_break_scenario: Optional[str]   # e.g. "ARV -10%" or None if deal holds
    break_reason: Optional[BreakpointReason]
    is_fragile: bool                       # True if ANY stress scenario = PASS


class AnalyzeRequest(BaseModel):
    purchase_price: float = Field(..., gt=0)
    arv: float = Field(..., gt=0)
    rehab_budget: float = Field(..., ge=0)

    closing_cost_pct: Optional[float] = Field(default=0.03, ge=0, le=0.20)   # decimal (0.03 = 3%)
    selling_cost_pct: Optional[float] = Field(default=0.08, ge=0, le=0.25)   # decimal
    holding_months: Optional[int] = Field(default=6, ge=0, le=60)

    annual_interest_rate: Optional[float] = Field(default=0.10, ge=0, le=1.0)  # decimal
    loan_to_cost_pct: Optional[float] = Field(default=0.90, ge=0, le=1.0)      # decimal
    required_profit_margin_pct: Optional[float] = Field(default=0.12, ge=0, le=1.0)  # decimal

    est_monthly_rent: Optional[float] = Field(default=None, ge=0)

    region: Optional[str] = None


class RiskFlag(BaseModel):
    code: str
    label: str
    severity: Severity


class StressTestScenario(BaseModel):
    name: str
    arv: float
    rehab_budget: float
    holding_months: int
    net_profit: float
    profit_pct: float
    annualized_roi: float
    verdict: Verdict


class AnalyzeResponse(BaseModel):
    # Core numbers
    total_project_cost: float
    gross_profit: float
    net_profit: float
    profit_pct: float
    annualized_roi: float
    max_safe_offer: float

    # Strategy + verdict
    flip_score: int
    brrrr_score: int
    wholesale_score: int
    best_strategy: Strategy
    overall_verdict: Verdict
    flip_verdict: Verdict
    brrrr_verdict: Verdict
    wholesale_verdict: Verdict

    # Extras
    confidence_score: int
    risk_flags: List[str]
    typed_flags: List[RiskFlag]
    stress_tests: List[StressTestScenario]
    notes: List[str]

    # Helpful BRRRR / wholesale helpers (optional but nice)
    rent_to_cost_ratio: Optional[float] = None
    assignment_spread: Optional[float] = None

    # Derived enrichment (always populated by analyze_deal)
    rehab_reality: Optional[RehabReality] = None
    breakpoints: Optional[Breakpoints] = None

    # Integrity Gate — which institutional outputs are allowed for this verdict
    allowed_outputs: Optional[Dict[str, bool]] = None


# ---------------------------------------------------------------------------
# Phase 2 — URL extraction + draft flow
# ---------------------------------------------------------------------------

class DraftDeal(BaseModel):
    """
    Best-effort extraction from a listing URL.
    purchase_price / arv / rehab_budget carry confidence metadata so the UI
    can highlight what needs manual completion.
    """
    source: str                   # "opengraph" | "SOURCE_BLOCKED" | "manual" | etc.
    url: Optional[str] = None

    address: Optional[str] = None
    zip_code: Optional[str] = None
    region: Optional[str] = None

    # Required to analyze — wrapped in DataPoint for confidence visibility
    purchase_price: DataPoint = DataPoint()
    arv: DataPoint = DataPoint()
    rehab_budget: DataPoint = DataPoint()

    # Optional helper
    est_monthly_rent: DataPoint = DataPoint()

    # Assumptions with safe defaults (match AnalyzeRequest defaults)
    closing_cost_pct: float = 0.03
    selling_cost_pct: float = 0.08
    holding_months: int = 6
    annual_interest_rate: float = 0.10
    loan_to_cost_pct: float = 0.90
    required_profit_margin_pct: float = 0.12

    notes: List[str] = []
    signals: List[str] = []


class DraftFromUrlResponse(BaseModel):
    draft: DraftDeal


# ---------------------------------------------------------------------------
# Lender report export
# ---------------------------------------------------------------------------

class LenderReportRequest(BaseModel):
    result: AnalyzeResponse
    meta: Dict[str, Any] = {}


class NegotiationScriptRequest(BaseModel):
    result: AnalyzeResponse
    seller_ask_price: Optional[float] = None
    property_address: Optional[str] = None
    buyer_name: Optional[str] = None
    seller_name: Optional[str] = None


class NegotiationScriptResponse(BaseModel):
    negotiation_script: str
