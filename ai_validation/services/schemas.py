from typing import Optional
from pydantic import BaseModel, Field, field_validator


class RelevanceOutput(BaseModel):
    is_relevant: bool = Field(description="True if document relates to carbon, emissions, or sustainability")
    document_type: str = Field(description="Closest matching type from the valid list")
    confidence: float = Field(ge=0, le=100)
    indicators: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v):
        return max(0.0, min(100.0, float(v)))

    @field_validator("indicators")
    @classmethod
    def limit(cls, v):
        return v[:5]


class AuthenticityOutput(BaseModel):
    score: float = Field(ge=50, le=100, description="50 minimum — digital PDFs are legitimate")
    indicators: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def clamp(cls, v):
        return max(50.0, min(100.0, float(v)))

    @field_validator("indicators", "red_flags")
    @classmethod
    def limit(cls, v):
        return v[:10]


class MetadataOutput(BaseModel):
    co2_value: Optional[float] = Field(None, ge=0, le=10_000_000_000)
    co2_unit: str = Field(default="tonnes")
    co2_confidence: float = Field(default=0, ge=0, le=100)

    issue_date: Optional[str] = Field(None)
    issue_date_confidence: float = Field(default=0, ge=0, le=100)

    expiry_date: Optional[str] = Field(None)
    expiry_date_confidence: float = Field(default=0, ge=0, le=100)

    issuing_authority: str = Field(default="")
    issuing_authority_confidence: float = Field(default=0, ge=0, le=100)

    certificate_number: str = Field(default="")
    verification_standard: str = Field(default="")

    @field_validator("co2_confidence", "issue_date_confidence", "expiry_date_confidence", "issuing_authority_confidence")
    @classmethod
    def clamp(cls, v):
        return max(0.0, min(100.0, float(v)))

    @field_validator("co2_unit")
    @classmethod
    def normalise_unit(cls, v):
        if not v:
            return "tonnes"
        v = v.lower().strip()
        if "kg" in v or "kilogram" in v:
            return "kg"
        if any(k in v for k in ("ton", "tonne", "metric")):
            return "tonnes"
        return "tonnes"

    @field_validator("issuing_authority", "certificate_number", "verification_standard")
    @classmethod
    def strip_none(cls, v):
        return str(v).strip() if v else ""