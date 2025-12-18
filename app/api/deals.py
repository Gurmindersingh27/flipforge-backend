from fastapi import APIRouter
from app.schemas.deal import DealAnalyzeRequest, DealAnalysisResponse
from app.core.analysis_engine import DealInputs, analyze_deal

router = APIRouter(prefix="/api/v1/deals", tags=["deals"])


@router.post("/analyze", response_model=DealAnalysisResponse)
def analyze_deal_endpoint(payload: DealAnalyzeRequest) -> DealAnalysisResponse:
    inputs = DealInputs(
        purchase_price=payload.purchase_price,
        arv=payload.arv,
        rehab_budget=payload.rehab_budget,
        closing_cost_pct=payload.closing_cost_pct,
        selling_cost_pct=payload.selling_cost_pct,
        holding_months=payload.holding_months,
        annual_interest_rate=payload.annual_interest_rate,
        loan_to_cost_pct=payload.loan_to_cost_pct,
        required_profit_margin_pct=payload.required_profit_margin_pct,
        est_monthly_rent=payload.est_monthly_rent,
    )

    result = analyze_deal(inputs)

    return DealAnalysisResponse(
        total_project_cost=result.total_project_cost,
        gross_profit=result.gross_profit,
        net_profit=result.net_profit,
        profit_pct=result.profit_pct,
        annualized_roi=result.annualized_roi,
        max_safe_offer=result.max_safe_offer,
        risk_flags=result.risk_flags,
        flip_score=result.flip_score,
        brrrr_score=result.brrrr_score,
        wholesale_score=result.wholesale_score,
    )
