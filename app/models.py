from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


Severity = Literal["critical", "moderate", "mild"]
Verdict = Literal["BUY", "CONDITIONAL", "PASS"]
Strategy = Literal["flip", "brrrr", "wholesale"]


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
