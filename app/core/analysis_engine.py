from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from math import pow


@dataclass
class DealInputs:
    purchase_price: float
    arv: float
    rehab_budget: float
    closing_cost_pct: float = 0.04  # 4% default
    selling_cost_pct: float = 0.06  # realtor + seller closing
    holding_months: float = 6.0
    annual_interest_rate: float = 0.10  # 10% money
    loan_to_cost_pct: float = 0.90      # leverage
    required_profit_margin_pct: float = 0.15  # 15% on ARV
    est_monthly_rent: Optional[float] = None


@dataclass
class DealAnalysis:
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


def analyze_deal(inputs: DealInputs) -> DealAnalysis:
    # Basic costs
    closing_costs_buy = inputs.purchase_price * inputs.closing_cost_pct
    rehab = inputs.rehab_budget
    total_cost_before_finance = inputs.purchase_price + closing_costs_buy + rehab

    # Finance / holding
    loan_amount = total_cost_before_finance * inputs.loan_to_cost_pct
    equity_in = total_cost_before_finance - loan_amount

    # simple interest-only model on full loan
    interest_cost = loan_amount * inputs.annual_interest_rate * (inputs.holding_months / 12.0)

    # taxes / utilities buffer
    soft_holding_cost = inputs.purchase_price * 0.01 * (inputs.holding_months / 6.0)

    total_project_cost = total_cost_before_finance + interest_cost + soft_holding_cost

    # Selling costs
    selling_costs = inputs.arv * inputs.selling_cost_pct

    # Profit calcs
    gross_profit = inputs.arv - total_project_cost - selling_costs
    net_profit = gross_profit

    profit_pct = net_profit / total_project_cost if total_project_cost > 0 else 0.0

    # Annualized ROI
    if inputs.holding_months > 0 and equity_in > 0:
        simple_roi = net_profit / equity_in
        annualized_roi = pow(1 + simple_roi, 12.0 / inputs.holding_months) - 1
    else:
        annualized_roi = 0.0

    # Max safe offer based on required margin
    target_profit = inputs.arv * inputs.required_profit_margin_pct
    target_total_cost = inputs.arv - selling_costs - target_profit
    max_safe_offer = target_total_cost - (soft_holding_cost + rehab + closing_costs_buy)

    # Risk flags
    risk_flags: List[str] = []
    spread_pct = (inputs.arv - total_project_cost - selling_costs) / inputs.arv if inputs.arv else 0

    if spread_pct < inputs.required_profit_margin_pct:
        risk_flags.append("thin_spread")

    if rehab > inputs.purchase_price * 0.6:
        risk_flags.append("high_rehab_vs_purchase")

    if inputs.holding_months > 9:
        risk_flags.append("long_hold_period")

    if net_profit < 25000:
        risk_flags.append("low_absolute_profit")

    if annualized_roi < 0.25:
        risk_flags.append("low_annualized_roi")

    # Strategy scoring – v1, simple but usable
    flip_score = 0.0
    brrrr_score = 0.0
    wholesale_score = 0.0

    # Flip: strong profit + ROI
    flip_score += max(0, min(100, annualized_roi * 200))
    flip_score += 20 if "thin_spread" not in risk_flags else -20

    # BRRRR: rent vs all-in + equity left
    if inputs.est_monthly_rent:
        rent_ratio = inputs.est_monthly_rent / total_project_cost
        brrrr_score += max(0, min(60, rent_ratio * 6000))  # 1% → ~60
        refi_amount = inputs.arv * 0.75
        equity_left = inputs.arv - refi_amount
        if equity_left / inputs.arv > 0.20:
            brrrr_score += 30
        if net_profit < 0:
            brrrr_score -= 20
    else:
        brrrr_score = flip_score * 0.4

    # Wholesale: how much spread for end buyer
    end_buyer_required_margin = 0.15
    end_buyer_target_profit = inputs.arv * end_buyer_required_margin
    end_buyer_target_total_cost = inputs.arv - selling_costs - end_buyer_target_profit
    end_buyer_max_offer = end_buyer_target_total_cost - (soft_holding_cost + rehab + closing_costs_buy)

    assignment_spread = end_buyer_max_offer - inputs.purchase_price
    if assignment_spread > 5000:
        wholesale_score += min(100, assignment_spread / 500 * 10)
    else:
        wholesale_score -= 10

    return DealAnalysis(
        total_project_cost=round(total_project_cost, 2),
        gross_profit=round(gross_profit, 2),
        net_profit=round(net_profit, 2),
        profit_pct=round(profit_pct, 4),
        annualized_roi=round(annualized_roi, 4),
        max_safe_offer=round(max_safe_offer, 2),
        risk_flags=risk_flags,
        flip_score=round(flip_score, 1),
        brrrr_score=round(brrrr_score, 1),
        wholesale_score=round(wholesale_score, 1),
    )
