"""Items v1 contract. All amounts are USD; percentages are decimal fractions."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Annotated, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer,
    StrictStr, ValidationError, model_validator,
)


CENT = Decimal("0.01")


def _number(value, *, maximum: int, places: int | None = None) -> Decimal:
    # Check before coercion/rounding: bool is an int subclass, and a tiny
    # negative must not become an accepted zero-cent amount.
    if type(value) not in (int, float):
        raise ValueError("must be a JSON number, not a string or boolean")
    number = Decimal(str(value))
    if not number.is_finite() or not 0 <= number <= maximum:
        raise ValueError(f"must be finite and between 0 and {maximum}")
    with localcontext() as context:
        context.prec = 40
        if places is not None:
            if number.normalize().as_tuple().exponent < -places:
                raise ValueError(f"must have at most {places} decimal places")
            return number.copy_abs() if number == 0 else number
        rounded = number.quantize(CENT, rounding=ROUND_HALF_UP)
        return rounded.copy_abs() if rounded == 0 else rounded


MoneyInput = Annotated[Decimal, BeforeValidator(
    lambda value: _number(value, maximum=1_000_000), json_schema_input_type=int | float,
)]
HourlyInput = Annotated[Decimal, BeforeValidator(
    lambda value: _number(value, maximum=1_000), json_schema_input_type=int | float,
)]
HoursInput = Annotated[Decimal, BeforeValidator(
    lambda value: _number(value, maximum=10_000, places=2), json_schema_input_type=int | float,
)]
PercentageInput = Annotated[Decimal, BeforeValidator(
    lambda value: _number(value, maximum=1, places=6), json_schema_input_type=int | float,
)]


def _safe_error_input(value):
    """Remove non-finite floats from validation details, including nested extras."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value), True
    if isinstance(value, (dict, list)):
        entries = value.items() if isinstance(value, dict) else enumerate(value)
        cleaned, changed = {}, False
        for key, entry in entries:
            cleaned[key], nested_changed = _safe_error_input(entry)
            changed |= nested_changed
        return (cleaned if isinstance(value, dict) else list(cleaned.values())), changed
    return value, False


class ItemInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="wrap")
    @classmethod
    def json_safe_validation_errors(cls, value, handler):
        try:
            return handler(value)
        except ValidationError as exc:
            # Non-standard JSON NaN/Infinity can reach Python's JSON decoder.
            # Keep the usual 422 validation detail JSON-safe for those inputs.
            errors = exc.errors(include_url=False)
            changed = False
            for error in errors:
                error["input"], input_changed = _safe_error_input(error.get("input"))
                changed |= input_changed
            if changed:
                raise ValidationError.from_exception_data(cls.__name__, errors) from exc
            raise


class ItemPersonalDefaults(ItemInputModel):
    hourly_value: HourlyInput | None = None
    target_profit: MoneyInput | None = None
    contingency_pct: PercentageInput | None = None
    fee_pct: PercentageInput | None = None


class ItemAnalyzeRequest(ItemPersonalDefaults):
    item_name: Annotated[StrictStr, Field(max_length=200)] | None = None
    category: Annotated[StrictStr, Field(max_length=100)] | None = None
    purchase_price: MoneyInput | None = Field(
        default=None, description="All-in purchase amount, including any fees or tax.",
    )
    resale_low: MoneyInput | None = None
    resale_high: MoneyInput | None = None
    repairs: MoneyInput | None = None
    pickup: MoneyInput | None = None
    delivery: MoneyInput | None = None
    storage: MoneyInput | None = None
    fee_fixed: MoneyInput | None = None
    hours: HoursInput | None = None
    personal_defaults: ItemPersonalDefaults | None = None

    @model_validator(mode="before")
    @classmethod
    def ordered_resale(cls, value):
        if isinstance(value, dict):
            low, high = value.get("resale_low"), value.get("resale_high")
            if type(low) in (int, float) and type(high) in (int, float):
                low, high = Decimal(str(low)), Decimal(str(high))
                if low.is_finite() and high.is_finite() and low > high:
                    raise ValueError("resale_low must not exceed resale_high")
        return value


def _money_json(value: Decimal) -> float:
    with localcontext() as context:
        context.prec = 40
        rounded = value.quantize(CENT, rounding=ROUND_HALF_UP)
    return 0.0 if rounded == 0 else float(rounded)


# Serialization is explicit: Pydantic otherwise emits Decimal values as strings.
MoneyResult = Annotated[Decimal, PlainSerializer(_money_json, return_type=float, when_used="json")]
NumericValue = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
ItemStatus = Literal["needs_info", "offer_only", "within_budget", "stretch", "skip"]
InputSource = Literal["user_entered", "default"]
DefaultOrigin = Literal["personal", "application"]
FinancialInput = Literal[
    "purchase_price", "resale_low", "resale_high", "repairs", "pickup",
    "delivery", "storage", "fee_fixed", "hours", "hourly_value",
    "target_profit", "contingency_pct", "fee_pct",
]
# Stable public order, independent of request key order and model inheritance.
FINANCIAL_INPUTS: tuple[FinancialInput, ...] = (
    "purchase_price", "resale_low", "resale_high", "repairs", "pickup",
    "delivery", "storage", "fee_fixed", "hours", "hourly_value",
    "target_profit", "contingency_pct", "fee_pct",
)
REQUIRED_INPUTS = FINANCIAL_INPUTS[1:]


class ItemOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemAssumption(ItemOutputModel):
    value: NumericValue | None
    source: InputSource | None
    default_origin: DefaultOrigin | None = None


class ItemTextAssumption(ItemOutputModel):
    value: str | None
    source: InputSource | None
    default_origin: DefaultOrigin | None = None


class ItemAssumptions(ItemOutputModel):
    item_name: ItemTextAssumption
    category: ItemTextAssumption
    purchase_price: ItemAssumption
    resale_low: ItemAssumption
    resale_high: ItemAssumption
    repairs: ItemAssumption
    pickup: ItemAssumption
    delivery: ItemAssumption
    storage: ItemAssumption
    fee_fixed: ItemAssumption
    hours: ItemAssumption
    hourly_value: ItemAssumption
    target_profit: ItemAssumption
    contingency_pct: ItemAssumption
    fee_pct: ItemAssumption


class ItemScenario(ItemOutputModel):
    resale: MoneyResult
    contingency: MoneyResult
    selling_fees: MoneyResult
    own_time_value: MoneyResult
    raw_max_offer: MoneyResult = Field(description="Ceiling before whole-dollar flooring; cent-rounded on the wire.")
    max_offer: int = Field(description="Most you should pay, including any fees or tax.")
    cash_left: MoneyResult | None
    profit_after_time: MoneyResult | None
    target_shortfall: MoneyResult | None


class ItemAnalyzeResponse(ItemOutputModel):
    schema_version: Literal[1] = 1
    status: ItemStatus
    missing_inputs: list[FinancialInput]
    assumptions: ItemAssumptions
    low: ItemScenario | None = None
    high: ItemScenario | None = None
