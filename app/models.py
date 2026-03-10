from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal
from enum import Enum
from pydantic import BaseModel, Field

# -------------------------
# Type aliases
# -------------------------

Severity = Literal["critical", "moderate", "mild"]
Verdict = Literal["BUY", "CONDITIONAL", "PASS"]
Strategy = Literal["flip", "brrrr", "wholesale"]
RehabSeverity = Literal["LIGHT", "MEDIUM", "HEAVY", "EXTREME"]

BreakpointReason = Literal["NEGATIVE_PROFIT", "BELOW_MARGIN", "VERDICT_FAIL"]

# -------------------------
# Request
# -------------------------

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


# -------------------------
# Core sub-models
# -------------------------

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


class RehabReality(BaseModel):
    rehab_ratio: float = Field(..., ge=0)
    severity: RehabSeverity
    contingency_pct: float = Field(..., ge=0, le=1.0)       # decimal (0.10 = 10%)
    added_holding_months: int = Field(..., ge=0, le=12)
    confidence_penalty: int = Field(..., ge=0, le=50)


class Breakpoints(BaseModel):
    first_break_scenario: Optional[str] = None
    break_reason: Optional[BreakpointReason] = None
    is_fragile: bool = False


# -------------------------
# Response
# -------------------------

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

    # Final verdict + integrity gate (v1)
    overall_verdict: Verdict
    verdict_reason: str
    allowed_outputs: Dict[str, bool]

    flip_verdict: Verdict
    brrrr_verdict: Verdict
    wholesale_verdict: Verdict

    # Rehab Reality (v1)
    rehab_reality: RehabReality

    # Breakpoints (v1)
    breakpoints: Breakpoints

    # Extras
    confidence_score: int
    risk_flags: List[str]
    typed_flags: List[RiskFlag]
    stress_tests: List[StressTestScenario]
    notes: List[str]

    # Helpful BRRRR / wholesale helpers (optional but nice)
    rent_to_cost_ratio: Optional[float] = None
    assignment_spread: Optional[float] = None

    # ✅ NEW: Narratives (backend-first voice)
    narratives: Optional[Dict[str, Any]] = None


# =========================================================
# ✅ ADDITIVE: DraftDeal ingestion models (Pre-Spine object)
# =========================================================

class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    MISSING = "MISSING"


class DataPoint(BaseModel):
    value: Optional[float] = None
    confidence: ConfidenceLevel = ConfidenceLevel.MISSING
    source: Optional[str] = None     # "Zillow" | "Redfin" | "OpenGraph" | "User" | "Heuristic"
    evidence: Optional[str] = None   # short reason string (e.g., "og:title parse")


class DraftDeal(BaseModel):
    """
    DraftDeal sits BEFORE AnalyzeRequest.

    - Adapters/extractors produce DraftDeal (best-effort).
    - User edits DraftDeal in UI.
    - Finalize converts DraftDeal -> AnalyzeRequest (clean).
    """
    source: str = "manual"           # "zillow" | "redfin" | "opengraph" | "manual"
    url: Optional[str] = None

    # Identity (optional)
    address: Optional[str] = None
    zip_code: Optional[str] = None
    region: Optional[str] = None

    # Big Three (required to analyze)
    purchase_price: DataPoint = Field(default_factory=DataPoint)
    arv: DataPoint = Field(default_factory=DataPoint)
    rehab_budget: DataPoint = Field(default_factory=DataPoint)

    # Optional helper
    est_monthly_rent: DataPoint = Field(default_factory=DataPoint)

    # Assumptions (defaults match AnalyzeRequest defaults)
    closing_cost_pct: float = 0.03
    selling_cost_pct: float = 0.08
    holding_months: int = 6
    annual_interest_rate: float = 0.10
    loan_to_cost_pct: float = 0.90
    required_profit_margin_pct: float = 0.12

    # Transparency
    notes: List[str] = Field(default_factory=list)
    signals: List[str] = Field(default_factory=list)


class DraftFromUrlRequest(BaseModel):
    url: str


class DraftFromUrlResponse(BaseModel):
    draft: DraftDeal


class DraftFinalizeError(Exception):
    """Raised when required fields are missing/invalid during draft finalization."""
    def __init__(self, missing_fields: List[str]):
        super().__init__("Missing required fields: " + ", ".join(missing_fields))
        self.missing_fields = missing_fields


def finalize_draft_to_request(d: DraftDeal) -> AnalyzeRequest:
    """
    Gatekeeper bridge:
    - Requires purchase_price, arv, rehab_budget (values must be present and valid)
    - Strips confidence metadata
    - Returns a clean AnalyzeRequest for the underwriting engine
    """
    missing: List[str] = []

    # Defensive: in case any DataPoint object itself is None (shouldn't happen, but safe)
    pp = d.purchase_price.value if d.purchase_price else None
    arv = d.arv.value if d.arv else None
    rehab = d.rehab_budget.value if d.rehab_budget else None

    # Validate core inputs
    if pp is None or pp <= 0:
        missing.append("purchase_price")
    if arv is None or arv <= 0:
        missing.append("arv")
    if rehab is None or rehab < 0:
        missing.append("rehab_budget")

    if missing:
        raise DraftFinalizeError(missing)

    rent = None
    if d.est_monthly_rent and d.est_monthly_rent.value is not None:
        rent = float(d.est_monthly_rent.value)

    return AnalyzeRequest(
        purchase_price=float(pp),
        arv=float(arv),
        rehab_budget=float(rehab),

        closing_cost_pct=float(d.closing_cost_pct),
        selling_cost_pct=float(d.selling_cost_pct),
        holding_months=int(d.holding_months),

        annual_interest_rate=float(d.annual_interest_rate),
        loan_to_cost_pct=float(d.loan_to_cost_pct),
        required_profit_margin_pct=float(d.required_profit_margin_pct),

        est_monthly_rent=rent,
        region=d.region,
    )
