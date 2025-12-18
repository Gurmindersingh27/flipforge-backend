from sqlalchemy.orm import Session

from app.schemas.analysis import (
    DealInput,
    DealMetrics,
    DealScore,
    AnalyzeDealResponse,
)
from app.schemas.investor_profile import InvestorProfile
from app.core.scoring import analyze_score
from app.db.models.deal import Deal
from app.db.models.analysis import DealAnalysis


def compute_metrics(payload: DealInput) -> DealMetrics:
    """
    Pure math: take raw deal input and compute basic financial metrics.
    No DB, no side effects.
    """

    total_cost = (
        payload.purchase_price
        + payload.rehab_cost
        + payload.closing_costs
        + payload.holding_costs
        + payload.selling_costs
        + payload.misc_costs
    )

    profit = payload.arv - total_cost if payload.arv is not None else 0

    margin_pct = (profit / payload.arv * 100) if payload.arv else 0
    roi_pct = (profit / total_cost * 100) if total_cost else 0

    # Annualized ROI (simple version): adjust by timeline
    if payload.timeline_months and payload.timeline_months > 0:
        annualized_roi_pct = roi_pct * (12 / payload.timeline_months)
    else:
        annualized_roi_pct = roi_pct

    # Very simple MAO for now: 70% rule
    # MAO = (ARV * 0.70) - rehab - other costs
    if payload.arv:
        mao_base = payload.arv * 0.70
        other_costs = (
            payload.rehab_cost
            + payload.closing_costs
            + payload.holding_costs
            + payload.selling_costs
            + payload.misc_costs
        )
        max_offer_price_mao = mao_base - other_costs
    else:
        max_offer_price_mao = 0

    # Breakeven ARV = total_cost (what ARV you need to not lose money)
    breakeven_arv = total_cost

    return DealMetrics(
        total_cost=total_cost,
        profit=profit,
        margin_pct=margin_pct,
        roi_pct=roi_pct,
        annualized_roi_pct=annualized_roi_pct,
        max_offer_price_mao=max_offer_price_mao,
        breakeven_arv=breakeven_arv,
    )


def create_deal_if_needed(db: Session, payload: DealInput) -> Deal:
    """
    For now, we create a new Deal row every time we analyze.
    Later we can re-use existing deals or pass a deal_id from the frontend.
    """
    deal = Deal(
        address=payload.address,
        strategy="flip",
        status="inbox",
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return deal


def create_analysis_record(
    db: Session,
    deal: Deal,
    payload: DealInput,
    metrics: DealMetrics,
    score: DealScore,
) -> DealAnalysis:
    """
    Persist a snapshot of the analysis linked to a deal.
    """
    analysis = DealAnalysis(
        deal_id=deal.id,
        purchase_price=payload.purchase_price,
        arv=payload.arv,
        rehab_cost=payload.rehab_cost,
        total_cost=metrics.total_cost,
        profit=metrics.profit,
        margin_pct=metrics.margin_pct,
        roi_pct=metrics.roi_pct,
        annualized_roi_pct=metrics.annualized_roi_pct,
        max_offer_price_mao=metrics.max_offer_price_mao,
        breakeven_arv=metrics.breakeven_arv,
        score=score.score,
        grade=score.grade,
        verdict=score.verdict,
        risk_level=score.risk_level,
        subscores=score.subscores,
        flags=score.flags,
        risk_notes=score.risk_notes,
        profile_fit_score=score.profile_fit_score,
        profile_fit_notes=score.profile_fit_notes,
    )

    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def analyze_deal_service(payload: DealInput, db: Session | None = None) -> AnalyzeDealResponse:
    """
    - compute metrics
    - compute score with scoring engine
    - if db is provided, save Deal + DealAnalysis
    """
    metrics = compute_metrics(payload)
    profile = InvestorProfile()
    score = analyze_score(metrics, payload, profile)

    if db is not None:
        deal = create_deal_if_needed(db, payload)
        create_analysis_record(db, deal, payload, metrics, score)

    return AnalyzeDealResponse(metrics=metrics, score=score)
