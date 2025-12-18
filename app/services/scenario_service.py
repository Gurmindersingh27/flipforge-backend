from app.schemas.analysis import DealInput
from app.schemas.investor_profile import InvestorProfile
from app.schemas.scenario import ScenarioSet, ScenarioResult
from app.schemas.analysis import DealMetrics, DealScore
from app.services.analyze_service import compute_metrics
from app.core.scoring import analyze_score


def _apply_conservative_adjustments(payload: DealInput, profile: InvestorProfile) -> DealInput:
    # Copy base payload and tweak numbers
    mult_arv = 0.90
    mult_rehab = 1.10
    mult_costs = 1.05
    mult_timeline = 1.20

    if profile.risk_tolerance == "conservative":
        mult_arv = 0.88
        mult_rehab = 1.15
        mult_costs = 1.08
        mult_timeline = 1.30
    elif profile.risk_tolerance == "aggressive":
        mult_arv = 0.93
        mult_rehab = 1.07
        mult_costs = 1.03
        mult_timeline = 1.10

    updated = payload.copy(update={
        "arv": payload.arv * mult_arv,
        "rehab_cost": payload.rehab_cost * mult_rehab,
        "closing_costs": payload.closing_costs * mult_costs,
        "holding_costs": payload.holding_costs * mult_costs,
        "selling_costs": payload.selling_costs * mult_costs,
        "misc_costs": payload.misc_costs * mult_costs,
        "timeline_months": payload.timeline_months * mult_timeline if payload.timeline_months else payload.timeline_months,
    })

    return updated


def _apply_aggressive_adjustments(payload: DealInput, profile: InvestorProfile) -> DealInput:
    mult_arv = 1.05
    mult_rehab = 0.95
    mult_costs = 0.95
    mult_timeline = 0.90

    if profile.risk_tolerance == "conservative":
        mult_arv = 1.03
        mult_rehab = 0.97
        mult_costs = 0.97
        mult_timeline = 0.95
    elif profile.risk_tolerance == "aggressive":
        mult_arv = 1.08
        mult_rehab = 0.92
        mult_costs = 0.93
        mult_timeline = 0.85

    updated = payload.copy(update={
        "arv": payload.arv * mult_arv,
        "rehab_cost": payload.rehab_cost * mult_rehab,
        "closing_costs": payload.closing_costs * mult_costs,
        "holding_costs": payload.holding_costs * mult_costs,
        "selling_costs": payload.selling_costs * mult_costs,
        "misc_costs": payload.misc_costs * mult_costs,
        "timeline_months": payload.timeline_months * mult_timeline if payload.timeline_months else payload.timeline_months,
    })

    return updated


def generate_scenarios(payload: DealInput) -> ScenarioSet:
    """
    Generate Base / Conservative / Aggressive scenarios
    using the same analyzer + scoring engine.
    """
    profile = InvestorProfile()  # later: load real user profile

    # Base = as entered
    base_input = payload
    base_metrics: DealMetrics = compute_metrics(base_input)
    base_score: DealScore = analyze_score(base_metrics, base_input, profile)

    # Conservative
    cons_input = _apply_conservative_adjustments(payload, profile)
    cons_metrics: DealMetrics = compute_metrics(cons_input)
    cons_score: DealScore = analyze_score(cons_metrics, cons_input, profile)

    # Aggressive
    aggr_input = _apply_aggressive_adjustments(payload, profile)
    aggr_metrics: DealMetrics = compute_metrics(aggr_input)
    aggr_score: DealScore = analyze_score(aggr_metrics, aggr_input, profile)

    return ScenarioSet(
        base=ScenarioResult(
            name="base",
            label="Base (as entered)",
            input=base_input,
            metrics=base_metrics,
            score=base_score,
        ),
        conservative=ScenarioResult(
            name="conservative",
            label="Conservative (ARV down, costs up, longer timeline)",
            input=cons_input,
            metrics=cons_metrics,
            score=cons_score,
        ),
        aggressive=ScenarioResult(
            name="aggressive",
            label="Aggressive (ARV up, costs down, shorter timeline)",
            input=aggr_input,
            metrics=aggr_metrics,
            score=aggr_score,
        ),
    )
