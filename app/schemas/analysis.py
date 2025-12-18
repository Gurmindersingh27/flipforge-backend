from pydantic import BaseModel
from typing import Optional, List, Dict


class DealInput(BaseModel):
    # What the frontend / you will send in to analyze a deal
    address: Optional[str] = None
    purchase_price: float
    arv: float
    rehab_cost: float
    closing_costs: float = 0
    holding_costs: float = 0
    selling_costs: float = 0
    misc_costs: float = 0
    timeline_months: float = 6

    # extra context for scoring – all optional
    days_on_market: Optional[int] = None
    comp_arv_stdev_pct: Optional[float] = None  # how volatile comps are
    neighborhood_score: Optional[int] = None    # 1–10
    condition_rating: Optional[str] = None      # "light", "medium", "heavy", "structural"

class DealMetrics(BaseModel):
    # Calculated deal numbers
    total_cost: float
    profit: float
    margin_pct: float
    roi_pct: float
    annualized_roi_pct: float
    max_offer_price_mao: float
    breakeven_arv: float


class DealScore(BaseModel):
    # For now we’ll just stub this and fill in later
    score: int
    grade: str
    verdict: str
    risk_level: str
    subscores: Dict[str, int]
    flags: List[str]
    risk_notes: List[str]
    profile_fit_score: int
    profile_fit_notes: List[str]


class AnalyzeDealResponse(BaseModel):
    metrics: DealMetrics
    score: DealScore
