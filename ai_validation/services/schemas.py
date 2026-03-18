from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# each schema is the exact shape we expect from the LLM for that pipeline step
# using Pydantic v2 — with_structured_output() validates the response automatically
# so a malformed LLM reply raises a validation error instead of silently defaulting


class ReadabilityOutput(BaseModel):
    is_readable: bool = Field(
        description="False only if document is completely blank, fully corrupted, or pure noise"
    )
    quality_score: float = Field(
        ge=0, le=100,
        description="0-100 readability quality score"
    )
    language: str = Field(default="English")
    issues: list[str] = Field(default_factory=list)

    @field_validator("quality_score")
    @classmethod
    def clamp_score(cls, v):
        return max(0.0, min(100.0, v))


class RelevanceOutput(BaseModel):
    is_relevant: bool = Field(
        description="True if document relates to carbon, emissions, sustainability"
    )
    document_type: str = Field(
        description="Closest matching document type from the valid list"
    )
    confidence: float = Field(
        ge=0, le=100,
        description="0-100 confidence in the classification"
    )
    indicators: list[str] = Field(
        default_factory=list,
        description="1-3 things observed in the document"
    )

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v):
        return max(0.0, min(100.0, v))


class AuthenticityOutput(BaseModel):
    score: float = Field(
        ge=50, le=100,
        description="Authenticity score — floor is 50 because digital docs are normal"
    )
    indicators: list[str] = Field(default_factory=list, max_length=10)
    red_flags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("score")
    @classmethod
    def clamp_floor(cls, v):
        # digital documents are not penalised for being digital
        return max(50.0, min(100.0, v))


class MetadataOutput(BaseModel):
    co2_value: Optional[float] = Field(
        None, ge=0, le=10_000_000_000,
        description="Numeric CO2 value only, no units"
    )
    co2_unit: str = Field(
        default="tonnes",
        description="tonnes | kg | metric_tons"
    )
    co2_confidence: float = Field(default=0, ge=0, le=100)

    issue_date: Optional[str] = Field(
        None,
        description="YYYY-MM-DD format or null"
    )
    issue_date_confidence: float = Field(default=0, ge=0, le=100)

    expiry_date: Optional[str] = Field(
        None,
        description="YYYY-MM-DD format or null — future dates are valid for expiry"
    )
    expiry_date_confidence: float = Field(default=0, ge=0, le=100)

    issuing_authority: str = Field(default="")
    issuing_authority_confidence: float = Field(default=0, ge=0, le=100)

    certificate_number: str = Field(default="")
    verification_standard: str = Field(default="")