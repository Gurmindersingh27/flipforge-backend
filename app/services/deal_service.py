from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.db.models.deal import Deal
from app.db.models.analysis import DealAnalysis
from app.schemas.deal import DealDashboardResponse, DealDetail
from app.schemas.analysis import DealMetrics, DealScore, AnalyzeDealResponse


def get_deal_dashboard(deal_id: int, db: Session) -> DealDashboardResponse:
    # 1) Load deal
    deal: Deal | None = db.query(Deal).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal {deal_id} not found.",
        )

    # 2) Load latest analysis for this deal
    analysis_row: DealAnalysis | None = (
        db.query(DealAnalysis)
        .filter(DealAnalysis.deal_id == deal.id)
        .order_by(DealAnalysis.created_at.desc())
        .first()
    )

    if analysis_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No analysis found for deal {deal_id}.",
        )

    # 3) Rebuild metrics object from DB row
    metrics = DealMetrics(
        total_cost=analysis_row.total_cost,
        profit=analysis_row.profit,
        margin_pct=analysis_row.margin_pct,
        roi_pct=analysis_row.roi_pct,
        annualized_roi_pct=analysis_row.annualized_roi_pct,
        max_offer_price_mao=analysis_row.max_offer_price_mao,
        breakeven_arv=analysis_row.breakeven_arv,
    )

    # 4) Rebuild score object from DB row
    score = DealScore(
        score=analysis_row.score,
        grade=analysis_row.grade,
        verdict=analysis_row.verdict,
        risk_level=analysis_row.risk_level,
        subscores=analysis_row.subscores or {},
        flags=analysis_row.flags or [],
        risk_notes=analysis_row.risk_notes or [],
        profile_fit_score=analysis_row.profile_fit_score,
        profile_fit_notes=analysis_row.profile_fit_notes or [],
    )

    analysis = AnalyzeDealResponse(metrics=metrics, score=score)
    deal_detail = DealDetail.model_validate(deal)  # from ORM

    return DealDashboardResponse(
        deal=deal_detail,
        analysis=analysis,
    )
