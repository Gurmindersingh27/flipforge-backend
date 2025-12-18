from typing import Dict, List, Tuple
from app.schemas.analysis import DealInput, DealMetrics, DealScore
from app.schemas.investor_profile import InvestorProfile


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _grade_from_score(score: int) -> str:
    if score >= 97:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _profitability_subscore(metrics: DealMetrics, profile: InvestorProfile) -> int:
    profit = metrics.profit
    margin = metrics.margin_pct
    roi = metrics.roi_pct

    # Profit vs target
    if profit <= 0:
        profit_component = 0
    elif profit >= profile.min_profit_flip:
        profit_component = 35
    else:
        ratio = profit / profile.min_profit_flip  # 0–1
        profit_component = 35 * ratio

    # Margin vs target
    if margin <= 0:
        margin_component = 0
    elif margin >= profile.min_margin_pct:
        margin_component = 35
    else:
        ratio = margin / profile.min_margin_pct
        margin_component = 35 * ratio

    # ROI vs target
    if roi <= 0:
        roi_component = 0
    elif roi >= profile.min_roi:
        roi_component = 30
    else:
        ratio = roi / profile.min_roi
        roi_component = 30 * ratio

    return int(_clamp(profit_component + margin_component + roi_component))


def _risk_exposure_subscore(
    metrics: DealMetrics, payload: DealInput, profile: InvestorProfile
) -> int:
    score = 80.0  # start optimistic

    comp_vol = payload.comp_arv_stdev_pct or 0
    dom = payload.days_on_market or 0
    hood = payload.neighborhood_score or 5
    timeline = payload.timeline_months or 6

    # Comps volatility
    if comp_vol > 20:
        score -= 25
    elif comp_vol > 10:
        score -= 10

    # Days on market
    if dom > 120:
        score -= 15
    elif dom > 60:
        score -= 5

    # Neighborhood
    if hood <= 3:
        score -= 15
    elif hood >= 8:
        score += 5

    # Timeline
    if timeline > 9:
        score -= 10
    elif timeline > 6:
        score -= 5
    else:
        score += 2

    # Adjust by risk tolerance
    if profile.risk_tolerance == "conservative":
        score -= 5
    elif profile.risk_tolerance == "aggressive":
        score += 5

    return int(_clamp(score))


def _cost_quality_subscore(payload: DealInput) -> int:
    # For v1, we just look at comp volatility and condition to infer "confidence"
    score = 70.0

    comp_vol = payload.comp_arv_stdev_pct or 0
    condition = (payload.condition_rating or "").lower()

    if comp_vol <= 5:
        score += 10
    elif comp_vol >= 20:
        score -= 10

    if condition in {"heavy", "structural"}:
        score -= 10

    return int(_clamp(score))


def _market_alignment_subscore(
    metrics: DealMetrics, payload: DealInput, profile: InvestorProfile
) -> int:
    score = 70.0
    dom = payload.days_on_market or 0
    hood = payload.neighborhood_score or 5
    price = payload.purchase_price

    # DOM
    if dom < 30:
        score += 5
    elif dom > 90:
        score -= 10

    # Neighborhood
    if hood <= 3:
        score -= 15
    elif hood >= 8:
        score += 5

    # Price vs preferred band
    if profile.preferred_price_min is not None and profile.preferred_price_max is not None:
        if price < profile.preferred_price_min or price > profile.preferred_price_max:
            score -= 10
        else:
            score += 5

    return int(_clamp(score))


def _investor_fit_subscore(
    metrics: DealMetrics, payload: DealInput, profile: InvestorProfile
) -> Tuple[int, List[str], int, List[str]]:
    score = 100.0
    notes: List[str] = []
    profile_fit_notes: List[str] = []

    profit = metrics.profit
    margin = metrics.margin_pct
    roi = metrics.roi_pct

    # Profit vs target
    if profit < profile.min_profit_flip:
        deficit = 1 - (profit / profile.min_profit_flip) if profile.min_profit_flip > 0 else 1
        penalty = min(25, 25 * deficit)
        score -= penalty
        notes.append("Profit is below your target.")
        profile_fit_notes.append(
            f"Profit ${profit:,.0f} vs your target ${profile.min_profit_flip:,.0f}."
        )

    # ROI vs target
    if roi < profile.min_roi:
        deficit = 1 - (roi / profile.min_roi) if profile.min_roi > 0 else 1
        penalty = min(20, 20 * deficit)
        score -= penalty
        notes.append("ROI is below your target.")
        profile_fit_notes.append(
            f"ROI {roi:.1f}% vs your target {profile.min_roi:.1f}%."
        )

    # Margin vs target
    if margin < profile.min_margin_pct:
        deficit = 1 - (margin / profile.min_margin_pct) if profile.min_margin_pct > 0 else 1
        penalty = min(15, 15 * deficit)
        score -= penalty
        notes.append("Margin is thinner than your target.")
        profile_fit_notes.append(
            f"Margin {margin:.1f}% vs your target {profile.min_margin_pct:.1f}%."
        )

    # Budget caps
    if profile.max_purchase_price is not None and payload.purchase_price > profile.max_purchase_price:
        score -= 15
        notes.append("Purchase price is above your max budget.")
        profile_fit_notes.append(
            f"Purchase price ${payload.purchase_price:,.0f} above max ${profile.max_purchase_price:,.0f}."
        )

    if profile.max_rehab_budget is not None and payload.rehab_cost > profile.max_rehab_budget:
        score -= 10
        notes.append("Rehab is above your comfort budget.")
        profile_fit_notes.append(
            f"Rehab ${payload.rehab_cost:,.0f} above max ${profile.max_rehab_budget:,.0f}."
        )

    # Rehab comfort vs condition
    condition = (payload.condition_rating or "").lower()
    if condition:
        if condition == "structural" and "structural" not in [
            lvl.lower() for lvl in profile.rehab_comfort_levels
        ]:
            score -= 20
            notes.append("Rehab scope may exceed your comfort level (structural issues).")
            profile_fit_notes.append(
                "Deal appears to have structural-level rehab but your comfort excludes it."
            )

    fit_score = int(_clamp(score))
    return fit_score, notes, fit_score, profile_fit_notes


def compute_subscores(
    metrics: DealMetrics, payload: DealInput, profile: InvestorProfile
) -> Dict[str, int]:
    profitability = _profitability_subscore(metrics, profile)
    risk_exposure = _risk_exposure_subscore(metrics, payload, profile)
    cost_quality = _cost_quality_subscore(payload)
    market_alignment = _market_alignment_subscore(metrics, payload, profile)
    investor_fit, _, _, _ = _investor_fit_subscore(metrics, payload, profile)

    return {
        "profitability": profitability,
        "risk_exposure": risk_exposure,
        "cost_quality": cost_quality,
        "market_alignment": market_alignment,
        "investor_fit": investor_fit,
    }


def combine_score(subscores: Dict[str, int]) -> int:
    score = (
        0.30 * subscores["profitability"]
        + 0.20 * subscores["risk_exposure"]
        + 0.15 * subscores["cost_quality"]
        + 0.15 * subscores["market_alignment"]
        + 0.20 * subscores["investor_fit"]
    )
    return int(_clamp(score))


def verdict_logic(
    score: int, metrics: DealMetrics, payload: DealInput, profile: InvestorProfile
) -> Tuple[str, str, List[str], List[str], int, List[str]]:
    """
    Returns:
    verdict, risk_level, flags, risk_notes, profile_fit_score, profile_fit_notes
    """
    flags: List[str] = []
    risk_notes: List[str] = []

    # Hard fails
    if metrics.profit <= 0 or metrics.roi_pct <= 0:
        return (
            "walk_away",
            "high",
            ["unprofitable"],
            ["Deal loses money or has non-positive ROI."],
            0,
            ["Deal does not meet basic profitability."],
        )

    # Investor fit + notes reuse
    profile_fit_score, fit_notes, profile_fit_score2, profile_fit_notes = _investor_fit_subscore(
        metrics, payload, profile
    )

    # Risk level from score
    if score >= 80:
        risk_level = "low"
    elif score >= 60:
        risk_level = "moderate"
    else:
        risk_level = "high"

    # Verdict tiers
    if (
        score >= 80
        and metrics.profit >= profile.min_profit_flip
        and metrics.roi_pct >= profile.min_roi
        and metrics.margin_pct >= profile.min_margin_pct
    ):
        verdict = "green_light"
    elif score < 60:
        verdict = "walk_away"
    else:
        verdict = "conditional"

    # Flags based on metrics vs profile
    if metrics.profit < profile.min_profit_flip:
        flags.append("below_profit_target")
        risk_notes.append("Profit is under your target.")

    if metrics.roi_pct < profile.min_roi:
        flags.append("low_roi")
        risk_notes.append("ROI is under your target.")

    if metrics.margin_pct < profile.min_margin_pct:
        flags.append("thin_margin")
        risk_notes.append("Margin is thinner than your target.")

    # ARV sensitivity: use comp volatility if provided
    if (payload.comp_arv_stdev_pct or 0) > 10:
        flags.append("arv_sensitive")
        risk_notes.append("Comps are volatile; ARV is sensitive to small changes.")

    # Rehab above comfort
    condition = (payload.condition_rating or "").lower()
    if condition in {"heavy", "structural"} and "heavy" not in [
        lvl.lower() for lvl in profile.rehab_comfort_levels
    ]:
        flags.append("rehab_above_comfort")
        risk_notes.append("Rehab scope may exceed your comfort level.")

    return verdict, risk_level, flags, risk_notes + fit_notes, profile_fit_score2, profile_fit_notes


def analyze_score(
    metrics: DealMetrics, payload: DealInput, profile: InvestorProfile
) -> DealScore:
    subscores = compute_subscores(metrics, payload, profile)
    overall_score = combine_score(subscores)
    verdict, risk_level, flags, risk_notes, profile_fit_score, profile_fit_notes = verdict_logic(
        overall_score, metrics, payload, profile
    )
    grade = _grade_from_score(overall_score)

    return DealScore(
        score=overall_score,
        grade=grade,
        verdict=verdict,
        risk_level=risk_level,
        subscores=subscores,
        flags=flags,
        risk_notes=risk_notes,
        profile_fit_score=profile_fit_score,
        profile_fit_notes=profile_fit_notes,
    )
