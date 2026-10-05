"""Pure Items calculation: no house math, network, persistence or AI."""
from decimal import Decimal, ROUND_FLOOR, localcontext

from .item_models import (
    FINANCIAL_INPUTS, REQUIRED_INPUTS, ItemAnalyzeRequest, ItemAnalyzeResponse,
    ItemAssumption, ItemAssumptions, ItemScenario, ItemTextAssumption,
)


APPLICATION_DEFAULTS = {"hourly_value": Decimal("20.00"), "contingency_pct": Decimal("0.15")}


def _resolve(req: ItemAnalyzeRequest) -> ItemAssumptions:
    resolved = {}
    for name in FINANCIAL_INPUTS:
        source, origin = None, None
        if name in req.model_fields_set:
            value, source = getattr(req, name), "user_entered"
        elif req.personal_defaults is not None and name in req.personal_defaults.model_fields_set:
            value, source, origin = getattr(req.personal_defaults, name), "default", "personal"
        elif name in APPLICATION_DEFAULTS:
            value, source, origin = APPLICATION_DEFAULTS[name], "default", "application"
        else:
            value = None
        resolved[name] = ItemAssumption(
            value=value, source=source if value is not None else None,
            default_origin=origin if value is not None else None,
        )
    for name in ("item_name", "category"):
        value = getattr(req, name)
        resolved[name] = ItemTextAssumption(value=value, source="user_entered" if value is not None else None)
    return ItemAssumptions(**resolved)


def analyze_item(req: ItemAnalyzeRequest) -> ItemAnalyzeResponse:
    assumptions = _resolve(req)
    values = {name: getattr(assumptions, name).value for name in FINANCIAL_INPUTS}
    missing = [name for name in REQUIRED_INPUTS if values[name] is None]
    if missing:
        return ItemAnalyzeResponse(status="needs_info", missing_inputs=missing, assumptions=assumptions)

    # Bounded inputs need fewer than 28 digits; pin precision so callers cannot
    # change decisions through the thread's ambient decimal context.
    with localcontext() as context:
        context.prec = 40
        contingency = values["repairs"] * values["contingency_pct"]
        own_time = values["hours"] * values["hourly_value"]
        cash_costs = values["repairs"] + contingency + values["pickup"] + values["delivery"] + values["storage"]
        purchase, target = values["purchase_price"], values["target_profit"]

        def scenario(resale: Decimal) -> ItemScenario:
            fees = resale * values["fee_pct"] + values["fee_fixed"]
            available = resale - fees - cash_costs
            ceiling = available - own_time - target
            cash = available - purchase if purchase is not None else None
            profit = cash - own_time if cash is not None else None
            return ItemScenario(
                resale=resale, contingency=contingency, selling_fees=fees,
                own_time_value=own_time, raw_max_offer=ceiling,
                max_offer=int(ceiling.to_integral_value(rounding=ROUND_FLOOR)),
                cash_left=cash, profit_after_time=profit,
                target_shortfall=max(Decimal(0), target - profit) if profit is not None else None,
            )

        low, high = scenario(values["resale_low"]), scenario(values["resale_high"])
        if purchase is None:
            status = "offer_only"
        elif purchase <= low.raw_max_offer:
            status = "within_budget"
        elif purchase <= high.raw_max_offer:
            status = "stretch"
        else:
            status = "skip"
        return ItemAnalyzeResponse(
            status=status, missing_inputs=[], assumptions=assumptions, low=low, high=high,
        )
