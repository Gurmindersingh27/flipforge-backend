from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime


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

    class Config:
        orm_mode = True


# ✅ ADDED: required by app/api/v1/deals.py
# Keep this lightweight + permissive so it won’t break if your Deal model has different fields.
class DealListItem(BaseModel):
    id: int

    # Common optional fields (won’t error if missing)
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None

    purchase_price: Optional[float] = None
    arv: Optional[float] = None
    rehab_budget: Optional[float] = None

    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True
        extra = "allow"
class DealDetail(DealListItem):
    # Extend list item with extra optional fields that might exist on the Deal model.
    # Using extra="allow" means we won't crash if your model has additional fields.
    description: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True
        extra = "allow"


class DealDashboardResponse(BaseModel):
    # Some services return {deal: {...}, analysis: {...}}.
    # Others return a flattened dict. We allow both.
    deal: Optional[DealListItem] = None
    analysis: Optional[DealAnalysisResponse] = None

    # Catch-all fields so your service can evolve without breaking the response_model
    data: Optional[Dict[str, Any]] = None

    class Config:
        orm_mode = True
        extra = "allow"
