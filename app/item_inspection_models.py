"""Owner-entered inspection observations; no identity, condition or price verdict."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_serializer, field_validator, model_validator

from .item_models import ItemInputModel, ItemOutputModel
from .item_outcome_models import CalendarDate
from .saved_item_models import ItemId


InspectionKind = Literal["chair", "storage", "general"]
InspectionAnswer = Literal["unchecked", "okay", "issue"]
CHECK_IDS = {
    "chair": ("chair-frame", "chair-height", "chair-controls", "chair-surfaces", "chair-wheels"),
    "storage": ("storage-frame", "storage-moving", "storage-finish", "storage-damp", "storage-fit"),
    "general": ("general-frame", "general-moving", "general-surface", "general-damp", "general-fit"),
}


class ItemInspectionFacts(ItemInputModel):
    """Complete replacement observation snapshot. Missing checks stay unchecked."""
    guide_version: Literal["furniture-v1"] = "furniture-v1"
    kind: InspectionKind
    brand_hint: Literal["herman_miller", "steelcase"] | None = Field(
        default=None, description="User-selected chair label guide, not a verified brand.")
    label_text: Annotated[StrictStr, Field(max_length=500)] | None = None
    notes: Annotated[StrictStr, Field(max_length=5000)] | None = None
    inspected_on: CalendarDate | None = None
    answers: dict[StrictStr, InspectionAnswer] = Field(default_factory=dict, max_length=5)

    @field_validator("label_text", "notes")
    @classmethod
    def no_nul(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("inspection text must not contain NUL characters")
        return value

    @model_validator(mode="after")
    def checks_match_kind(self):
        allowed = CHECK_IDS[self.kind]
        if any(key not in allowed for key in self.answers):
            raise ValueError("check IDs must belong to the selected furniture kind")
        if self.kind != "chair" and self.brand_hint is not None:
            raise ValueError("brand_hint is only available for the chair guide")
        self.answers = {key: self.answers.get(key, "unchecked") for key in allowed}
        return self


class SaveItemInspectionRequest(ItemInspectionFacts):
    request_id: UUID
    expected_previous_id: UUID | None = Field(description="Required; explicitly null for the first inspection revision.")


class ItemInspectionResponse(ItemOutputModel):
    id: UUID
    root_item_id: ItemId
    previous_id: UUID | None
    revision_number: int = Field(gt=0)
    schema_version: Literal[1]
    source: Literal["user_reported"] = "user_reported"
    created_at: datetime
    inputs: ItemInspectionFacts

    @field_serializer("created_at")
    def utc_created_at(self, value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
