from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OpportunityCreate(BaseModel):
    """Validated row used for CSV ingestion and inserts."""

    external_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    amount: float
    currency: str = "AED"
    stage: str = Field(min_length=1)
    source: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    created_at: datetime | None = None
    close_date: date | None = None
    last_contact_days_ago: int | None = None
    engagement_score: float | None = None
    sales_rep_available: bool | None = None
    industry: str | None = None
    num_interactions: int | None = None
    converted: int | None = None

    @field_validator("external_id", "customer_id", "stage", "source", "owner_id", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("amount must be greater than 0")
        return value

    @field_validator("converted")
    @classmethod
    def converted_must_be_binary(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value not in (0, 1):
            raise ValueError("converted must be 0, 1, or empty")
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            text = value.strip()
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
            raise ValueError(f"invalid created_at date format: {value!r}")
        return value

    @field_validator("close_date", mode="before")
    @classmethod
    def parse_close_date(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            text = value.strip()
            try:
                return datetime.strptime(text, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError(f"invalid close_date format: {value!r}") from exc
        return value

    @field_validator("sales_rep_available", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return value


class OpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: str
    external_id: str
    customer_id: str
    amount: float
    currency: str
    stage: str
    source: str
    owner_id: str
    created_at: datetime
    close_date: date | None
    last_contact_days_ago: int | None
    engagement_score: float | None
    sales_rep_available: bool | None
    industry: str | None
    num_interactions: int | None
    converted: int | None


class RowError(BaseModel):
    row: int
    reason: str


class OpportunityUploadResult(BaseModel):
    inserted: int
    rejected: int
    errors: list[RowError]


class OpportunityListResponse(BaseModel):
    items: list[OpportunityRead]
    total: int
    limit: int
    offset: int
