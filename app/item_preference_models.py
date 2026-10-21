"""Buying preferences only; no changes to the calculator's default contract."""
from datetime import datetime, timezone

from pydantic import field_serializer

from .item_models import (
    HourlyInput, ItemInputModel, ItemOutputModel, MoneyInput, NumericValue, PercentageInput,
)


class SaveItemPreferencesRequest(ItemInputModel):
    """Full replacement: omitted fields become unknown, just like explicit null."""
    hourly_value: HourlyInput | None = None
    target_profit: MoneyInput | None = None
    contingency_pct: PercentageInput | None = None
    fee_pct: PercentageInput | None = None
    pickup: MoneyInput | None = None
    fee_fixed: MoneyInput | None = None


class ItemPreferencesResponse(ItemOutputModel):
    hourly_value: NumericValue | None
    target_profit: NumericValue | None
    contingency_pct: NumericValue | None
    fee_pct: NumericValue | None
    pickup: NumericValue | None
    fee_fixed: NumericValue | None
    updated_at: datetime

    @field_serializer("updated_at")
    def utc_updated_at(self, value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
