from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

from app.verdict_engine import evaluate_verdict, outputs_allowed

from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    RiskFlag,
    StressTestScenario,
    Verdict,
    Strategy,
    RehabReality,
    Breakpoints,
)

# ✅ NEW: Narrative Generator (backend-first voice)
from app.narratives.narrative_generator import NarrativeGenerator


# -----------------------
# Verdict Engine v1 constants (locked)
# -----------------------
V1_MIN_PROFIT = 30000.0
V1_MIN_MARGIN = 0.15
V1_MILD_STRESS_ORDER = ("Base", "ARV -5%", "Rehab +10%", "Hold +2 mo")

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

    hi = float(req.arv)
    lo = 0.0

    m0 = compute_base_metrics(req, purchase_override=0.0)
    if m0.total_project_cost <= 0:
        return 0.0
    if m0.net_profit < 0 or m0.profit_pct < required_margin:
        return 0.0

    for _ in range(45):
        mid = (lo + hi) / 2.0
        m = compute_base_metrics(req, purchase_override=mid)
        ok = (m.net_profit >= 0.0) and (m.profit_pct >= required_margin)
        if ok:
            lo = mid
        else:
            hi = mid

    return round(lo / 100.0) * 100.0


# -----------------------
# Rehab Reality v1
# -----------------------

def evaluate_rehab_reality(req: AnalyzeRequest) -> RehabReality:
    purchase = float(req.purchase_price)
    rehab = float(req.rehab_budget)

    rehab_ratio = (rehab / purchase) if purchase > 0 else 0.0

    # Buckets
    if rehab_ratio <= 0.15:
        severity = "LIGHT"
        contingency_pct = 0.05
        added_holding = 0
        confidence_penalty = 0
    elif rehab_ratio <= 0.30:
        severity = "MEDIUM"
        contingency_pct = 0.10
        added_holding = 1
        confidence_penalty = 5
    elif rehab_ratio <= 0.50:
        severity = "HEAVY"
        contingency_pct = 0.20
        added_holding = 2
        confidence_penalty = 10
    else:
        severity = "EXTREME"
        contingency_pct = 0.30
        added_holding = 3
        confidence_penalty = 20

    return RehabReality(
        rehab_ratio=float(rehab_ratio),
        severity=severity,
        contingency_pct=float(contingency_pct),
        added_holding_months=int(added_holding),
        confidence_penalty=int(confidence_penalty),
    )


def rehab_reality_flags(rr: RehabReality) -> List[RiskFlag]:
    flags: List[RiskFlag] = []

    if rr.severity == "MEDIUM":
        flags.append(RiskFlag(
            code="rehab_margin_compression",
            label="Rehab level may compress margins (overruns/time risk)",
            severity="moderate",
        ))
    elif rr.severity == "HEAVY":
        flags.append(RiskFlag(
            code="heavy_rehab_vs_purchase",
            label="Heavy rehab relative to purchase price",
            severity="moderate",
        ))
        flags.append(RiskFlag(
            code="rehab_margin_compression",
            label="High overrun/timeline sensitivity at this rehab level",
            severity="moderate",
        ))
    elif rr.severity == "EXTREME":
        flags.append(RiskFlag(
            code="heavy_rehab_vs_purchase",
            label="Heavy rehab relative to purchase price",
            severity="critical",
        ))
        flags.append(RiskFlag(
            code="extreme_rehab_risk",
            label="Extreme rehab execution risk",
            severity="critical",
        ))
        flags.append(RiskFlag(
            code="rehab_margin_compression",
            label="Deal is highly sensitive to rehab overruns and delays",
            severity="critical",
        ))

    return flags


# -----------------------
# Scoring + flags
# -----------------------

def build_risk_flags(req: AnalyzeRequest, m: BaseMetrics, max_safe_offer: float, rr: RehabReality) -> Tuple[List[str], List[RiskFlag]]:
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

    # Existing heavy rehab flag (keep for backward compatibility)
    if req.purchase_price > 0:
        rehab_ratio = req.rehab_budget / req.purchase_price
        if rehab_ratio >= 0.60:
            flags.append(RiskFlag(code="heavy_rehab", label="Heavy rehab vs purchase price", severity="critical"))
        elif rehab_ratio >= 0.35:
            flags.append(RiskFlag(code="heavy_rehab", label="Heavy rehab vs purchase price", severity="moderate"))

    # Rehab Reality (new)
    flags.extend(rehab_reality_flags(rr))

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
    score = 50
    score += int(clamp(m.profit_pct * 400, 0, 35))
    score += int(clamp(m.net_profit / 1000.0, -30, 25))
    hm = int(req.holding_months or 0)
    score -= int(clamp((hm - 6) * 2, 0, 15))
    return int(clamp(score, 0, 100))


def compute_brrrr_score(req: AnalyzeRequest, m: BaseMetrics) -> int:
    if req.est_monthly_rent is None:
        return 40

    score = 45
    all_in = m.total_project_cost
    annual_rent = float(req.est_monthly_rent) * 12.0
    rent_to_cost = (annual_rent / all_in) if all_in > 0 else 0.0

    score += int(clamp(rent_to_cost * 300, 0, 40))
    score += int(clamp(m.net_profit / 2000.0, -20, 15))
    return int(clamp(score, 0, 100))


def compute_wholesale_score(req: AnalyzeRequest, m: BaseMetrics, max_safe_offer: float) -> int:
    score = 45
    spread = max_safe_offer - req.purchase_price
    score += int(clamp(spread / 1000.0, -30, 35))
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


def compute_confidence_score(
    req: AnalyzeRequest,
    m: BaseMetrics,
    flags: List[RiskFlag],
    stress: List[StressTestScenario],
    rr: RehabReality,
) -> int:
    # R1 margin strength
    r1 = clamp(m.profit_pct * 500, 0, 100)

    # R2 stress robustness: count how many stress verdicts are not PASS
    if not stress:
        r2 = 50
    else:
        ok = sum(1 for s in stress if s.verdict != "PASS")
        r2 = (ok / len(stress)) * 100.0

    # R4 risk penalty (flags) + rehab reality penalty
    penalty = 0.0
    for f in flags:
        if f.severity == "critical":
            penalty += 18
        elif f.severity == "moderate":
            penalty += 10
        else:
            penalty += 4

    # Rehab reality confidence penalty (explicit, deterministic)
    penalty += float(rr.confidence_penalty)

    r4 = clamp(100.0 - penalty, 0, 100)

    confidence = (0.45 * r1) + (0.30 * r2) + (0.25 * r4)
    return int(round(clamp(confidence, 0, 100)))


def build_stress_tests(req: AnalyzeRequest, rr: RehabReality) -> List[StressTestScenario]:
    base_hold = int(req.holding_months or 0)

    scenarios = [
        ("Base", 1.00, 1.00, base_hold),
        ("ARV -5%", 0.95, 1.00, base_hold),
        ("ARV -10%", 0.90, 1.00, base_hold),

        # Rehab Reality stress tests (v1)
        ("Rehab +10%", 1.00, 1.10, base_hold),
    ]

    # Only include +20% if heavy/extreme
    if rr.severity in ("HEAVY", "EXTREME"):
        scenarios.append(("Rehab +20%", 1.00, 1.20, base_hold))

    # Timeline risk scenario (added holding months)
    if rr.added_holding_months > 0:
        scenarios.append((
            f"Rehab Reality (Hold +{rr.added_holding_months} mo)",
            1.00,
            1.00,
            base_hold + rr.added_holding_months
        ))

    # Keep your existing generic timeline stress
    scenarios.append(("Hold +2 mo", 1.00, 1.00, base_hold + 2))

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


# -----------------------
# Breakpoints v1
# -----------------------

def _break_reason(
    req: AnalyzeRequest,
    scenario: StressTestScenario,
) -> Optional[str]:
    required_margin = float(req.required_profit_margin_pct or 0.0)
    if scenario.net_profit < 0:
        return "NEGATIVE_PROFIT"
    if scenario.profit_pct < required_margin:
        return "BELOW_MARGIN"
    if scenario.verdict == "PASS":
        return "VERDICT_FAIL"
    return None


def compute_breakpoints(req: AnalyzeRequest, stress: List[StressTestScenario]) -> Breakpoints:
    """
    Find the first scenario where the deal 'breaks' under an ordered mild→severe ladder.
    """
    if not stress:
        return Breakpoints(first_break_scenario=None, break_reason=None, is_fragile=False)

    # Map by name for fast lookup
    by_name = {s.name: s for s in stress}

    # Ordered ladder (mild → severe)
    ordered_names: List[str] = [
        "ARV -5%",
        "Rehab +10%",
        "Hold +2 mo",
        "ARV -10%",
        "Rehab +20%",
    ]

    # If Rehab Reality added holding months, prefer that after harsher baseline scenarios
    for s in stress:
        if s.name.startswith("Rehab Reality (Hold +"):
            ordered_names.append(s.name)
            break

    # Scan in order; skip names not present
    for name in ordered_names:
        s = by_name.get(name)
        if not s:
            continue
        reason = _break_reason(req, s)
        if reason is not None:
            is_fragile = name in ("ARV -5%", "Rehab +10%", "Hold +2 mo")
            return Breakpoints(first_break_scenario=name, break_reason=reason, is_fragile=is_fragile)

    return Breakpoints(first_break_scenario=None, break_reason=None, is_fragile=False)


def build_notes(req: AnalyzeRequest, m: BaseMetrics, max_safe_offer: float, rr: RehabReality, bp: Breakpoints) -> List[str]:
    notes: List[str] = []

    if m.net_profit <= 0:
        notes.append("Deal is underwater after realistic costs. This is a PASS unless terms change.")
    elif m.profit_pct < float(req.required_profit_margin_pct or 0.0):
        notes.append("Margin is below your required threshold. Consider lowering offer or tightening rehab assumptions.")
    else:
        notes.append("Numbers pencil if assumptions are real. Verify ARV and rehab before moving.")

    # Rehab Reality note (single clean line)
    pct = rr.rehab_ratio * 100.0
    if rr.severity == "MEDIUM":
        notes.append(f"Rehab is ~{pct:.0f}% of purchase price (MEDIUM). Budget for overruns and timeline risk.")
    elif rr.severity == "HEAVY":
        notes.append(f"Rehab is ~{pct:.0f}% of purchase price (HEAVY). Deal is sensitive to overruns and delays.")
    elif rr.severity == "EXTREME":
        notes.append(f"Rehab is ~{pct:.0f}% of purchase price (EXTREME). High execution risk—stress tests matter.")
    # LIGHT: no extra note (keeps noise low)

    # Breakpoint note (new, single line)
    if bp.first_break_scenario:
        notes.append(f"Breakpoint: Deal fails under {bp.first_break_scenario}.")
    else:
        notes.append("Breakpoint: Deal holds up through mild stress.")

    if req.purchase_price > max_safe_offer and max_safe_offer > 0:
        notes.append(f"Your purchase price is above Max Safe Offer (~${max_safe_offer:,.0f}).")

    if req.est_monthly_rent is None:
        notes.append("No rent provided — BRRRR score is limited. Add rent to evaluate hold strategy.")
    else:
        notes.append("Rent provided — BRRRR logic includes rent-to-cost check.")

    return notes


def analyze_deal(req: AnalyzeRequest) -> AnalyzeResponse:
    rr = evaluate_rehab_reality(req)

    max_safe_offer = compute_max_safe_offer(req)
    base = compute_base_metrics(req)

    stress = build_stress_tests(req, rr)

    flip_score = compute_flip_score(req, base)
    brrrr_score = compute_brrrr_score(req, base)
    wholesale_score = compute_wholesale_score(req, base, max_safe_offer)

    best = pick_best_strategy(flip_score, brrrr_score, wholesale_score)

    risk_codes, typed_flags = build_risk_flags(req, base, max_safe_offer, rr)

    flip_verdict = verdict_from_score(flip_score)
    brrrr_verdict = verdict_from_score(brrrr_score)
    wholesale_verdict = verdict_from_score(wholesale_score)

    best_score = {"flip": flip_score, "brrrr": brrrr_score, "wholesale": wholesale_score}[best]
    score_verdict = verdict_from_score(best_score)  # keep for reference/debug

    # -----------------------
    # Verdict Engine (v1) — TOP-LEVEL authority
    # Build deterministic stress_results in fixed order.
    # -----------------------
    by_name = {s.name: s for s in stress}

    stress_results = []
    for nm in V1_MILD_STRESS_ORDER:
        s = by_name.get(nm)
        if not s:
            continue
        stress_results.append(
            {"name": s.name, "profit": float(s.net_profit), "margin": float(s.profit_pct)}
        )

    # Safety: if something went wrong and we have zero stresses, fall back to Base metrics
    if not stress_results:
        stress_results = [{"name": "Base", "profit": float(base.net_profit), "margin": float(base.profit_pct)}]

    overall_verdict, verdict_reason = evaluate_verdict(
        stress_results=stress_results,
        min_profit=V1_MIN_PROFIT,
        min_margin=V1_MIN_MARGIN,
    )
    allowed = outputs_allowed(overall_verdict)

    # Breakpoints (use stress tests + required margin)
    bp = compute_breakpoints(req, stress)

    # If fragile, add one clean flag (typed + code list)
    if bp.is_fragile:
        fragile = RiskFlag(
            code="fragile_deal",
            label="Deal breaks under mild stress",
            severity="moderate",
        )
        typed_flags.append(fragile)
        risk_codes.append(fragile.code)

    confidence = compute_confidence_score(req, base, typed_flags, stress, rr)

    rent_to_cost_ratio = None
    if req.est_monthly_rent is not None and base.total_project_cost > 0:
        rent_to_cost_ratio = (float(req.est_monthly_rent) * 12.0) / base.total_project_cost

    assignment_spread = max_safe_offer - req.purchase_price if max_safe_offer > 0 else None

    notes = build_notes(req, base, max_safe_offer, rr, bp)

    # ✅ NEW: Narratives (backend-first) - wrapped for safety
    try:
        narratives: Dict[str, Any] = NarrativeGenerator.build(
            base=base,  # ✅ pass BaseMetrics into narratives
            overall_verdict=overall_verdict,
            confidence_score=confidence,
            best_strategy=best,
            rehab_reality=rr,
            breakpoints=bp,
            typed_flags=typed_flags,
            stress_tests=stress,
        )
    except Exception as e:
        # Narrative generation failed - don't crash the analysis
        narratives = None

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
        verdict_reason=verdict_reason,
        allowed_outputs=allowed,

        flip_verdict=flip_verdict,
        brrrr_verdict=brrrr_verdict,
        wholesale_verdict=wholesale_verdict,

        rehab_reality=rr,
        breakpoints=bp,

        confidence_score=confidence,
        risk_flags=risk_codes,
        typed_flags=typed_flags,
        stress_tests=stress,
        notes=notes,

        rent_to_cost_ratio=rent_to_cost_ratio,
        assignment_spread=assignment_spread,

        # ✅ NEW: include voice
        narratives=narratives,
    )