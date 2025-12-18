from pydantic import BaseModel, Field
from typing import List, Optional


class DealAnalyzeRequest(BaseModel):
    purchase_price: float = Field(gt=0)
    arv: float = Field(gt=0)
    rehab_budget: float = Field(ge=0)
    closing_cost_pct: float = Field(ge=0, le=0.1, default=0.04)
    selling_cost_pct: float = Field(ge=0, le=0.1, default=0.06)
    holding_months: float = Field(gt=0, default=6.0)
    annual_interest_rate: float = Field(ge=0, le=0.3, default=0.1)
    loan_to_cost_pct: float = Field(ge=0, le=1.0, default=0.9)
    required_profit_margin_pct: float = Field(ge=0, le=0.5, default=0.15)
    est_monthly_rent: Optional[float] = Field(default=None, ge=0)


class DealAnalysisResponse(BaseModel):
    total_project_cost: float
    gross_profit: float
    net_profit: float
    profit_pct: float
    annualized_roi: float
    max_safe_offer: float
    risk_flags: List[str]
    flip_score: float
    brrrr_score: float
    wholesale_score: float
