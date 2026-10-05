"""Persistence contracts for immutable, owner-scoped Items snapshots."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, JsonValue, StrictInt, StrictStr, TypeAdapter, field_serializer, field_validator

from .item_models import ItemAnalyzeRequest, ItemAnalyzeResponse, ItemInputModel, ItemOutputModel


ItemId = Annotated[StrictInt, Field(gt=0)]
_http_url = TypeAdapter(HttpUrl)


def saved_inputs(inputs: ItemAnalyzeRequest) -> dict:
    """Keep supplied fields recursively, converting normalized Decimals to numbers."""
    def numbers(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {key: numbers(entry) for key, entry in value.items()}
        return value
    return numbers(inputs.model_dump(exclude_unset=True))


class SaveItemRequest(ItemInputModel):
    inputs: ItemAnalyzeRequest
    listing_url: Annotated[StrictStr, Field(max_length=2048)] | None = None
    notes: Annotated[StrictStr, Field(max_length=5000)] | None = None
    parent_item_id: ItemId | None = None

    @field_validator("listing_url")
    @classmethod
    def safe_listing_url(cls, value):
        if value is None:
            return None
        # Reject controls even at the edges; trim ordinary surrounding spaces.
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("listing URL must not contain control characters")
        value = value.strip()
        if not value or any(char.isspace() for char in value) or "\\" in value:
            raise ValueError("enter an absolute http or https URL without internal whitespace")
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or not parsed.hostname:
            raise ValueError("listing URL must be an absolute http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("listing URL must not contain credentials")
        _http_url.validate_python(value)
        return value


class SavedItemResponse(ItemOutputModel):
    id: ItemId
    schema_version: Literal[1]
    created_at: datetime
    parent_item_id: ItemId | None
    root_item_id: ItemId
    inputs: dict[str, JsonValue] = Field(description=(
        "Normalized ItemAnalyzeRequest containing only explicitly supplied fields, "
        "including within personal_defaults. Numeric values are JSON numbers."
    ))
    analysis_result: ItemAnalyzeResponse
    listing_url: str | None
    notes: str | None

    @field_serializer("created_at")
    def serialize_created_at(self, value):
        # SQLite returns naive values even for timezone=True; stored times are UTC.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


class SavedItemListResponse(ItemOutputModel):
    items: list[SavedItemResponse]
    limit: int
    offset: int
    next_offset: int | None
