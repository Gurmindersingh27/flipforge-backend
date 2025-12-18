from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    RiskFlag,
    StressTestScenario,
    Verdict,
    Strategy,
)

# -----------------------
# Helpers / core math
# -----------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def verdict_from_score(score: int) -> Verdict:
    if score >= 75:
        return "BUY"
    if score >= 55:
        return "CONDITIONAL"
    return "PASS"

@dataclass(frozen=True)
class BaseMetrics:
    purchase_price: float
    arv: float
    rehab_budget: float
    closing_cost: float
    selling_cost: float
    holding_cost: float
    total_project_cost: float
    gross_profit: float
    net_profit: float
    profit_pct: float
    annualized_roi: float


def compute_base_metrics(req: AnalyzeRequest, purchase_override: float | None = None) -> BaseMetrics:
    purchase = float(purchase_override) if purchase_override is not None else float(req.purchase_price)
    arv = float(req.arv)
    rehab = float(req.rehab_budget)

    closing_pct = float(req.closing_cost_pct or 0.0)
    selling_pct = float(req.selling_cost_pct or 0.0)
    holding_months = int(req.holding_months or 0)

    interest = float(req.annual_interest_rate or 0.0)
    ltc = float(req.loan_to_cost_pct or 0.0)

    closing_cost = purchase * closing_pct
    selling_cost = arv * selling_pct

    financed_amount = ltc * (purchase + rehab)
    monthly_interest = (financed_amount * interest) / 12.0
    holding_cost = monthly_interest * holding_months

    total_project_cost = purchase + rehab + closing_cost + selling_cost + holding_cost
    gross_profit = arv - (purchase + rehab)
    net_profit = arv - total_project_cost

    profit_pct = (net_profit / total_project_cost) if total_project_cost > 0 else 0.0

    # Simple annualized ROI proxy: net_profit / total_cost scaled by 12/holding_months
    if holding_months > 0:
        annualized_roi = profit_pct * (12.0 / holding_months)
    else:
        annualized_roi = profit_pct

    return BaseMetrics(
        purchase_price=purchase,
        arv=arv,
        rehab_budget=rehab,
        closing_cost=closing_cost,
        selling_cost=selling_cost,
        holding_cost=holding_cost,
        total_project_cost=total_project_cost,
        gross_profit=gross_profit,
        net_profit=net_profit,
        profit_pct=profit_pct,
        annualized_roi=annualized_roi,
    )


def compute_max_safe_offer(req: AnalyzeRequest) -> float:
    """
    Finds max purchase price such that profit margin >= required_profit_margin_pct
    and net_profit >= 0.
    """
    required_margin = float(req.required_profit_margin_pct or 0.0)

    # Upper bound can't exceed ARV, but even ARV could be too high.
    hi = float(req.arv)
    lo = 0.0

    # If even at purchase=0 we can't meet margin, return 0.
    m0 = compute_base_metrics(req, purchase_override=0.0)
    if m0.total_project_cost <= 0:
        return 0.0
    if m0.net_profit < 0 or m0.profit_pct < required_margin:
        return 0.0

    # Binary search
    for _ in range(45):
        mid = (lo + hi) / 2.0
        m = compute_base_metrics(req, purchase_override=mid)
        ok = (m.net_profit >= 0.0) and (m.profit_pct >= required_margin)
        if ok:
            lo = mid
        else:
            hi = mid

    # Round to nearest $100 for sanity
    return round(lo / 100.0) * 100.0


# -----------------------
# Scoring + flags
# -----------------------

def build_risk_flags(req: AnalyzeRequest, m: BaseMetrics, max_safe_offer: float) -> Tuple[List[str], List[RiskFlag]]:
    flags: List[RiskFlag] = []

    # Thin spread / low margin
    if m.profit_pct < 0.06:
        flags.append(RiskFlag(code="thin_spread", label="Thin profit spread", severity="critical"))
    elif m.profit_pct < 0.10:
        flags.append(RiskFlag(code="thin_spread", label="Thin profit spread", severity="moderate"))

    # Low absolute profit
    if m.net_profit < 10000:
        flags.append(RiskFlag(code="low_profit", label="Low net profit", severity="critical"))
    elif m.net_profit < 25000:
        flags.append(RiskFlag(code="low_profit", label="Low net profit", severity="moderate"))

    # Heavy rehab vs purchase
    if req.purchase_price > 0:
        rehab_ratio = req.rehab_budget / req.purchase_price
        if rehab_ratio >= 0.60:
            flags.append(RiskFlag(code="heavy_rehab", label="Heavy rehab vs purchase price", severity="critical"))
        elif rehab_ratio >= 0.35:
            flags.append(RiskFlag(code="heavy_rehab", label="Heavy rehab vs purchase price", severity="moderate"))

    # Over ask vs MAO (proxy using purchase_price as "offer")
    if req.purchase_price > max_safe_offer and max_safe_offer > 0:
        diff = req.purchase_price - max_safe_offer
        if diff >= 15000:
            flags.append(RiskFlag(code="over_mao", label="Offer is far above Max Safe Offer", severity="critical"))
        else:
            flags.append(RiskFlag(code="over_mao", label="Offer above Max Safe Offer", severity="moderate"))

    # Rent checks (if provided)
    if req.est_monthly_rent is not None:
        all_in = m.total_project_cost
        annual_rent = float(req.est_monthly_rent) * 12.0
        if all_in > 0:
            rent_to_cost = annual_rent / all_in
            if rent_to_cost < 0.08:
                flags.append(RiskFlag(code="weak_rent", label="Weak rent-to-cost for BRRRR", severity="moderate"))

    return [f.code for f in flags], flags


def compute_flip_score(req: AnalyzeRequest, m: BaseMetrics) -> int:
    # Weighted simple score: margin, profit, hold time
    score = 50

    # Profit pct contribution
    score += int(clamp(m.profit_pct * 400, 0, 35))  # 0.10 => +40 but capped

    # Net profit contribution
    score += int(clamp(m.net_profit / 1000.0, -30, 25))  # +25 at 25k

    # Holding months penalty
    hm = int(req.holding_months or 0)
    score -= int(clamp((hm - 6) * 2, 0, 15))

    return int(clamp(score, 0, 100))


def compute_brrrr_score(req: AnalyzeRequest, m: BaseMetrics) -> int:
    # Requires rent input for a real BRRRR read
    if req.est_monthly_rent is None:
        return 40

    score = 45
    all_in = m.total_project_cost
    annual_rent = float(req.est_monthly_rent) * 12.0
    rent_to_cost = (annual_rent / all_in) if all_in > 0 else 0.0

    # Rent-to-cost is big for BRRRR
    score += int(clamp(rent_to_cost * 300, 0, 40))  # 0.10 => +30
    # Still needs to not be a dog financially
    score += int(clamp(m.net_profit / 2000.0, -20, 15))
    return int(clamp(score, 0, 100))


def compute_wholesale_score(req: AnalyzeRequest, m: BaseMetrics, max_safe_offer: float) -> int:
    # Wholesale is about spread between MAO and purchase
    score = 45
    spread = max_safe_offer - req.purchase_price
    score += int(clamp(spread / 1000.0, -30, 35))
    # Penalize huge rehab because harder to assign
    if req.purchase_price > 0:
        rehab_ratio = req.rehab_budget / req.purchase_price
        score -= int(clamp(rehab_ratio * 40, 0, 20))
    return int(clamp(score, 0, 100))


def pick_best_strategy(flip: int, brrrr: int, wholesale: int) -> Strategy:
    if flip >= brrrr and flip >= wholesale:
        return "flip"
    if brrrr >= flip and brrrr >= wholesale:
        return "brrrr"
    return "wholesale"


def compute_confidence_score(req: AnalyzeRequest, m: BaseMetrics, flags: List[RiskFlag], stress: List[StressTestScenario]) -> int:
    # R1 margin strength
    r1 = clamp(m.profit_pct * 500, 0, 100)  # 0.20 => 100

    # R2 stress robustness: count how many stress verdicts are not PASS
    if not stress:
        r2 = 50
    else:
        ok = sum(1 for s in stress if s.verdict != "PASS")
        r2 = (ok / len(stress)) * 100.0

    # R4 risk penalty
    penalty = 0.0
    for f in flags:
        if f.severity == "critical":
            penalty += 18
        elif f.severity == "moderate":
            penalty += 10
        else:
            penalty += 4
    r4 = clamp(100.0 - penalty, 0, 100)

    # Combine (simple weighted avg)
    confidence = (0.45 * r1) + (0.30 * r2) + (0.25 * r4)
    return int(round(clamp(confidence, 0, 100)))


def build_stress_tests(req: AnalyzeRequest) -> List[StressTestScenario]:
    base = compute_base_metrics(req)

    scenarios = [
        ("Base", 1.00, 1.00, int(req.holding_months or 0)),
        ("ARV -5%", 0.95, 1.00, int(req.holding_months or 0)),
        ("ARV -10%", 0.90, 1.00, int(req.holding_months or 0)),
        ("Rehab +15%", 1.00, 1.15, int(req.holding_months or 0)),
        ("Hold +2 mo", 1.00, 1.00, int(req.holding_months or 0) + 2),
    ]

    out: List[StressTestScenario] = []
    for name, arv_mult, rehab_mult, hold_mo in scenarios:
        stressed = AnalyzeRequest(
            purchase_price=req.purchase_price,
            arv=req.arv * arv_mult,
            rehab_budget=req.rehab_budget * rehab_mult,
            closing_cost_pct=req.closing_cost_pct,
            selling_cost_pct=req.selling_cost_pct,
            holding_months=hold_mo,
            annual_interest_rate=req.annual_interest_rate,
            loan_to_cost_pct=req.loan_to_cost_pct,
            required_profit_margin_pct=req.required_profit_margin_pct,
            est_monthly_rent=req.est_monthly_rent,
            region=req.region,
        )
        m = compute_base_metrics(stressed)
        # Verdict is based on stressed flip score (simple + consistent)
        score = compute_flip_score(stressed, m)
        verdict = verdict_from_score(score)

        out.append(
            StressTestScenario(
                name=name,
                arv=m.arv,
                rehab_budget=m.rehab_budget,
                holding_months=hold_mo,
                net_profit=m.net_profit,
                profit_pct=m.profit_pct,
                annualized_roi=m.annualized_roi,
                verdict=verdict,
            )
        )
    return out


def build_notes(req: AnalyzeRequest, m: BaseMetrics, max_safe_offer: float) -> List[str]:
    notes: List[str] = []

    if m.net_profit <= 0:
        notes.append("Deal is underwater after realistic costs. This is a PASS unless terms change.")
    elif m.profit_pct < float(req.required_profit_margin_pct or 0.0):
        notes.append("Margin is below your required threshold. Consider lowering offer or tightening rehab assumptions.")
    else:
        notes.append("Numbers pencil if assumptions are real. Verify ARV and rehab before moving.")

    if req.purchase_price > max_safe_offer and max_safe_offer > 0:
        notes.append(f"Your purchase price is above Max Safe Offer (~${max_safe_offer:,.0f}).")

    if req.est_monthly_rent is None:
        notes.append("No rent provided — BRRRR score is limited. Add rent to evaluate hold strategy.")
    else:
        notes.append("Rent provided — BRRRR logic includes rent-to-cost check.")

    return notes


def analyze_deal(req: AnalyzeRequest) -> AnalyzeResponse:
    max_safe_offer = compute_max_safe_offer(req)
    base = compute_base_metrics(req)

    stress = build_stress_tests(req)

    flip_score = compute_flip_score(req, base)
    brrrr_score = compute_brrrr_score(req, base)
    wholesale_score = compute_wholesale_score(req, base, max_safe_offer)

    best = pick_best_strategy(flip_score, brrrr_score, wholesale_score)

    risk_codes, typed_flags = build_risk_flags(req, base, max_safe_offer)

    flip_verdict = verdict_from_score(flip_score)
    brrrr_verdict = verdict_from_score(brrrr_score)
    wholesale_verdict = verdict_from_score(wholesale_score)

    # overall verdict based on best strategy score
    best_score = {"flip": flip_score, "brrrr": brrrr_score, "wholesale": wholesale_score}[best]
    overall_verdict = verdict_from_score(best_score)

    confidence = compute_confidence_score(req, base, typed_flags, stress)

    # Helper metrics
    rent_to_cost_ratio = None
    if req.est_monthly_rent is not None and base.total_project_cost > 0:
        rent_to_cost_ratio = (float(req.est_monthly_rent) * 12.0) / base.total_project_cost

    assignment_spread = max_safe_offer - req.purchase_price if max_safe_offer > 0 else None

    notes = build_notes(req, base, max_safe_offer)

    return AnalyzeResponse(
        total_project_cost=base.total_project_cost,
        gross_profit=base.gross_profit,
        net_profit=base.net_profit,
        profit_pct=base.profit_pct,
        annualized_roi=base.annualized_roi,
        max_safe_offer=max_safe_offer,

        flip_score=flip_score,
        brrrr_score=brrrr_score,
        wholesale_score=wholesale_score,
        best_strategy=best,
        overall_verdict=overall_verdict,
        flip_verdict=flip_verdict,
        brrrr_verdict=brrrr_verdict,
        wholesale_verdict=wholesale_verdict,

        confidence_score=confidence,
        risk_flags=risk_codes,
        typed_flags=typed_flags,
        stress_tests=stress,
        notes=notes,

        rent_to_cost_ratio=rent_to_cost_ratio,
        assignment_spread=assignment_spread,
    )
