from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ReadabilityOutput(BaseModel):
    is_readable: bool = Field(
        description="False ONLY if document is completely blank, pure noise, or fully corrupted. True for all other cases."
    )
    quality_score: float = Field(
        ge=0, le=100,
        description="0-100 quality score. Computer-generated PDFs should score 85-98."
    )
    language: str = Field(default="English")
    issues: list[str] = Field(default_factory=list)

    @field_validator("quality_score")
    @classmethod
    def clamp_score(cls, v):
        return max(0.0, min(100.0, v))


class RelevanceOutput(BaseModel):
    is_relevant: bool = Field(
        description="True if document relates to carbon, emissions, or sustainability. When in doubt, True."
    )
    document_type: str = Field(
        description="Closest matching document type from the valid list provided"
    )
    confidence: float = Field(
        ge=0, le=100,
        description="0-100 confidence. Clear carbon certificate = 90-100. Obvious non-compliance doc = 0-30."
    )
    indicators: list[str] = Field(
        default_factory=list,
        description="1-3 specific things observed in the document that support the classification"
    )

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v):
        return max(0.0, min(100.0, v))


class AuthenticityOutput(BaseModel):
    score: float = Field(
        ge=50, le=100,
        description=(
            "Authenticity score. Floor is 50 (digital docs are legitimate). "
            "Complete document with all fields = 88-100. "
            "Document with SAMPLE/DRAFT watermarks or placeholders = 50-60."
        )
    )
    indicators: list[str] = Field(default_factory=list, max_length=10)
    red_flags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("score")
    @classmethod
    def clamp_floor(cls, v):
        return max(50.0, min(100.0, v))


class MetadataOutput(BaseModel):
    co2_value: Optional[float] = Field(
        None, ge=0, le=10_000_000_000,
        description="Numeric CO2 value only (no units). null if not found."
    )
    co2_unit: str = Field(
        default="tonnes",
        description="One of: tonnes | kg | metric_tons. Default tonnes."
    )
    co2_confidence: float = Field(
        default=0, ge=0, le=100,
        description="How clearly the CO2 value was visible. 85+ = clearly labelled."
    )

    issue_date: Optional[str] = Field(
        None,
        description="YYYY-MM-DD format. null if not present."
    )
    issue_date_confidence: float = Field(
        default=0, ge=0, le=100,
        description="How clearly the issue date was visible. 85+ = explicitly labelled."
    )

    expiry_date: Optional[str] = Field(
        None,
        description="YYYY-MM-DD format. null if not present. Future dates are valid."
    )
    expiry_date_confidence: float = Field(
        default=0, ge=0, le=100,
        description="How clearly the expiry date was visible. 85+ = explicitly labelled."
    )

    issuing_authority: str = Field(
        default="",
        description="Name of issuing organisation. Empty string if not found."
    )
    issuing_authority_confidence: float = Field(
        default=0, ge=0, le=100,
        description="How clearly the issuing authority was identified. 85+ = explicitly named."
    )

    certificate_number: str = Field(
        default="",
        description="Certificate/reference ID. Empty string if not found."
    )
    verification_standard: str = Field(
        default="",
        description="e.g. ISO 14064, GHG Protocol. Empty string if not mentioned."
    )