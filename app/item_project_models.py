"""Read-only project views over immutable estimates and outcome revisions."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, field_serializer

from .item_models import ItemAnalyzeResponse, ItemOutputModel, MoneyResult
from .item_outcome_models import OutcomeField, ItemOutcomeFigures, ItemOutcomeResponse
from .saved_item_models import ItemId, SavedItemResponse


ItemProjectStage = Literal["considering", "bought", "listed", "sold"]
OutcomeLineKey = Literal[
    "purchase_price", "sale_price", "repairs", "pickup", "delivery", "storage",
    "selling_fees", "other_expenses", "time",
]
OutcomeEffectKey = Literal[
    "sale_price", "repairs", "pickup", "delivery", "storage", "selling_fees",
    "other_expenses", "time", "rounding",
]


class OutcomeLine(ItemOutputModel):
    key: OutcomeLineKey
    label: str
    expected_low: MoneyResult | None
    expected_high: MoneyResult | None
    actual: MoneyResult | None
    # Positive differences improve profit. Purchase has no difference: it is
    # the common basis, not a prediction error. These are not additive effects.
    difference_low: MoneyResult | None
    difference_high: MoneyResult | None
    missing_fields: list[OutcomeField]


class OutcomeEffect(ItemOutputModel):
    key: OutcomeEffectKey
    label: str
    amount: MoneyResult


class OutcomeExplanation(ItemOutputModel):
    baseline_sale_price: MoneyResult
    baseline_profit: MoneyResult
    actual_profit: MoneyResult
    total_effect: MoneyResult
    effects: list[OutcomeEffect]
    dominant_effect: OutcomeEffectKey | None
    headline: str | None


class OutcomeComparison(ItemOutputModel):
    original_estimate: SavedItemResponse
    resale_source: Literal["assessment", "national_reference", "user_estimate", "unknown"]
    expected_at_purchase_price: ItemAnalyzeResponse | None
    expected_missing_fields: list[OutcomeField]
    lines: list[OutcomeLine]
    actual: ItemOutcomeFigures
    cash_explanation: OutcomeExplanation | None
    after_time_explanation: OutcomeExplanation | None


class ItemProjectSummary(ItemOutputModel):
    root_item_id: ItemId
    latest_version: SavedItemResponse
    version_count: int = Field(ge=1)
    stage: ItemProjectStage
    calculated: ItemOutcomeFigures | None
    updated_at: datetime

    @field_serializer("updated_at")
    def utc_updated_at(self, value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


class ItemProjectListResponse(ItemOutputModel):
    items: list[ItemProjectSummary]
    limit: int
    offset: int
    next_offset: int | None


class ItemProjectResponse(ItemOutputModel):
    summary: ItemProjectSummary
    versions: list[SavedItemResponse]
    latest_outcome: ItemOutcomeResponse | None
    comparison: OutcomeComparison | None
    comparison_unavailable_reason: Literal[
        "No outcome recorded", "No estimate linked", "The estimate was incomplete",
    ] | None
