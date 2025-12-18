from pydantic import BaseModel
from typing import List, Optional


class InvestorProfile(BaseModel):
    # Simple v1 profile – later we can load this from DB instead of hardcoding
    min_profit_flip: float = 25000.0
    min_roi: float = 15.0           # %
    min_margin_pct: float = 10.0    # %
    max_purchase_price: Optional[float] = None
    max_rehab_budget: Optional[float] = None

    # conservative / balanced / aggressive
    risk_tolerance: str = "balanced"

    # which rehab levels user is okay with
    rehab_comfort_levels: List[str] = ["light", "medium", "heavy"]

    # optional preferred price band
    preferred_price_min: Optional[float] = None
    preferred_price_max: Optional[float] = None
