"""Items-only photo assessment contracts. Images never enter persistence."""
import base64
import binascii
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, JsonValue, StrictBool, StrictStr, ValidationError, field_validator, model_validator

from app.item_models import ItemAnalyzeResponse, ItemInputModel, ItemOutputModel, MoneyInput, MoneyResult


def clean_text(value: str) -> str:
    if "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("text must not contain NUL or unpaired surrogates")
    return value


Text = Annotated[StrictStr, AfterValidator(clean_text)]


class AssessmentInputModel(ItemInputModel):
    @model_validator(mode="wrap")
    @classmethod
    def safe_errors(cls, value, handler):
        try:
            return handler(value)
        except ValidationError as exc:
            errors = exc.errors(include_url=False)
            for error in errors:
                # Do not echo image data or malformed Unicode into a 422 body.
                error["input"] = "[invalid input]"
                error["loc"] = tuple(part.encode("utf-8", "backslashreplace").decode()
                                     if isinstance(part, str) else part for part in error["loc"])
            raise ValidationError.from_exception_data(cls.__name__, errors) from exc


class AssessmentPhoto(AssessmentInputModel):
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: Annotated[StrictStr, Field(min_length=1, max_length=4_194_304)]

    @field_validator("data")
    @classmethod
    def valid_base64(cls, value):
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("photo must be valid base64") from None
        if not raw or len(raw) > 3 * 1024 * 1024:
            raise ValueError("photo must be between 1 byte and 3 MiB")
        return value

    @model_validator(mode="after")
    def image_signature(self):
        raw = base64.b64decode(self.data)
        matches = {"image/jpeg": raw.startswith(b"\xff\xd8\xff"),
                   "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
                   "image/webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"}
        if not matches[self.media_type]:
            raise ValueError("photo bytes do not match the declared image type")
        return self


class ItemAssessmentRequest(AssessmentInputModel):
    request_id: UUID
    description: Annotated[Text, Field(max_length=500)] = ""
    photos: Annotated[list[AssessmentPhoto], Field(min_length=1, max_length=3)]
    asking_price: MoneyInput | None = None
    location: Annotated[Text, Field(max_length=100)] | None = None


class ListingCandidate(AssessmentInputModel):
    title: Annotated[Text, Field(min_length=1, max_length=200)]
    url: Annotated[Text, Field(max_length=2048)]
    price: MoneyInput
    currency: Literal["USD"]
    condition: Annotated[Text, Field(max_length=200)]
    comparable: StrictBool
    single_item: StrictBool
    market: Literal["local_pickup", "national_shipping", "unknown"]
    # This is an extraction judgment, not a verified location claim.
    location: Annotated[Text, Field(max_length=100)] | None = None


class RepairSuggestion(AssessmentInputModel):
    job_id: Annotated[Text, Field(max_length=60)]
    reason: Annotated[Text, Field(max_length=300)]


class ProviderAssessment(AssessmentInputModel):
    item_name: Annotated[Text, Field(min_length=1, max_length=200)]
    category: Annotated[Text, Field(max_length=100)]
    asking_price: MoneyInput | None
    repairs: Annotated[list[RepairSuggestion], Field(max_length=10)]
    repair_unknowns: Annotated[list[Annotated[Text, Field(max_length=300)]], Field(max_length=10)]
    listings: Annotated[list[ListingCandidate], Field(max_length=12)]


class ConfirmedRepair(AssessmentInputModel):
    job_id: Annotated[Text, Field(max_length=60)]
    materials_cost: MoneyInput


class AssessmentConfirmation(AssessmentInputModel):
    repairs: Annotated[list[ConfirmedRepair], Field(max_length=10)]
    resale_source: Literal["assessment", "user_estimate"]
    preset_acknowledged: StrictBool


class BudgetResponse(ItemInputModel):
    month: str
    spent: MoneyResult
    reserved: MoneyResult
    target: MoneyResult
    reservation_per_run: MoneyResult
    warning: bool
    available: bool
    reason: str | None
    message: str | None
    resets_at: str


class ListingEvidence(ItemOutputModel):
    title: str
    url: str
    price: MoneyResult
    currency: Literal["USD"]
    source: str
    condition: str
    market: Literal["local_pickup", "national_shipping", "unknown"]
    location: str | None
    retrieved_at: str
    price_type: Literal["asking"]
    eligible: bool
    exclusion_reason: str | None
    supporting_quotes: list[str]


class ResaleEstimate(ItemOutputModel):
    low: MoneyResult
    high: MoneyResult
    source: Literal["online_asking_prices"]
    eligible_count: int
    method: Literal["linear_quartiles_v1"]
    label: str


class SuggestedRepair(ItemOutputModel):
    job_id: str
    label: str
    materials_cost: MoneyResult
    reason: str
    confirmed: Literal[False]


class AssessmentResult(ItemOutputModel):
    schema_version: Literal[1]
    item_name: str
    category: str
    asking_price: MoneyResult | None
    asking_price_source: Literal["user_entered", "description_extraction"]
    listings: list[ListingEvidence]
    resale: ResaleEstimate | None
    repair_suggestions: list[SuggestedRepair]
    repair_unknowns: list[str]
    repair_catalog_version: str
    assessed_at: str
    questions: list[str]
    preset: dict[str, JsonValue]
    inputs: dict[str, JsonValue]
    analysis_result: ItemAnalyzeResponse


class ItemAssessmentResponse(ItemOutputModel):
    id: str
    schema_version: Literal[1]
    status: Literal["processing", "completed", "failed", "uncertain"]
    result: AssessmentResult | None
    failure_code: str | None
    actual_cost: float | None
    message: str | None
