"""User-reported actuals, independent of estimate assumptions and defaults."""
import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, field_serializer, model_validator

from .item_models import HoursInput, HourlyInput, ItemInputModel, ItemOutputModel, MoneyInput, MoneyResult
from .saved_item_models import ItemId


OutcomeStatus = Literal["bought", "listed", "sold"]
SaleChannel = Literal["facebook_marketplace", "craigslist", "offerup", "ebay", "in_person", "other"]
OutcomeField = Literal[
    "status", "purchase_price", "materials", "hired_labor", "pickup", "delivery", "storage",
    "selling_fees", "other_expenses", "hours", "hourly_value", "sale_price",
    "purchased_on", "listed_on", "sold_on",
]
CASH_FIELDS = ("purchase_price", "materials", "hired_labor", "pickup", "delivery", "storage",
               "selling_fees", "other_expenses")
NUMBER_FIELDS = (*CASH_FIELDS, "hours", "hourly_value", "sale_price")


def _calendar_date(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("must be a calendar date in YYYY-MM-DD format")
    return date.fromisoformat(value)


CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]


class ItemOutcomeFacts(ItemInputModel):
    """Complete replacement snapshot: omitted optional fields become unknown."""
    status: OutcomeStatus
    purchase_price: MoneyInput | None = Field(default=None, description="All-in purchase, including acquisition tax/fees.")
    materials: MoneyInput | None = None
    hired_labor: MoneyInput | None = None
    pickup: MoneyInput | None = None
    delivery: MoneyInput | None = None
    storage: MoneyInput | None = None
    selling_fees: MoneyInput | None = None
    other_expenses: MoneyInput | None = None
    hours: HoursInput | None = None
    hourly_value: HourlyInput | None = None
    sale_price: MoneyInput | None = Field(default=None, description="Gross proceeds before separately entered selling fees.")
    sale_channel: SaleChannel | None = None
    purchased_on: CalendarDate | None = None
    listed_on: CalendarDate | None = None
    sold_on: CalendarDate | None = None

    @field_serializer(*NUMBER_FIELDS, when_used="json")
    def number_json(self, value):
        return float(value) if value is not None else None

    @model_validator(mode="after")
    def consistent_progress(self):
        if self.status != "sold":
            for field in ("sale_price", "sale_channel", "sold_on"):
                if getattr(self, field) is not None:
                    raise ValueError(f"{field} requires status sold")
        if self.status == "bought" and self.listed_on is not None:
            raise ValueError("listed_on requires status listed or sold")
        # Compare every known pair, including purchase/sale when listing is unknown.
        for first, last in (("purchased_on", "listed_on"), ("listed_on", "sold_on"), ("purchased_on", "sold_on")):
            left, right = getattr(self, first), getattr(self, last)
            if left is not None and right is not None and left > right:
                raise ValueError(f"{first} must not be after {last}")
        return self


class SaveItemOutcomeRequest(ItemOutcomeFacts):
    request_id: UUID
    expected_previous_id: UUID | None = Field(description="Required; explicitly null for the first revision.")
    based_on_item_id: ItemId | None = Field(default=None, description="Owned estimate in this root family; never inferred.")


class ItemOutcomeFigures(ItemOutputModel):
    cash_invested: MoneyResult | None
    cash_profit: MoneyResult | None
    profit_after_time: MoneyResult | None
    days_to_sell: int | None
    days_held: int | None
    missing_fields: dict[Literal["cash_invested", "cash_profit", "profit_after_time", "days_to_sell", "days_held"], list[OutcomeField]] = Field(
        description="Missing inputs per figure; status means sold is required. Never treat missing cash as zero.")


class ItemOutcomeResponse(ItemOutputModel):
    id: UUID
    root_item_id: ItemId
    based_on_item_id: ItemId | None
    previous_id: UUID | None
    revision_number: int = Field(gt=0)
    schema_version: Literal[1]
    created_at: datetime
    inputs: ItemOutcomeFacts
    calculated: ItemOutcomeFigures

    @field_serializer("created_at")
    def utc_created_at(self, value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
