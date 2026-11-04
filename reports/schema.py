from typing import Dict, FrozenSet, List, Optional, TypedDict


class VendorInfo(TypedDict):
    id: str
    name: str
    industry: str
    country: str
    compliance_status: str
    risk_level: str


class RegulationItem(TypedDict):
    regulation: str
    applies: bool
    requirement: str
    assurance_bar: str
    deadline: str
    consequence: str


class DocumentDetail(TypedDict):
    document_type: str
    document_status: str
    validation_status: str
    confidence: Optional[float]
    assurance_met: bool
    co2_extracted: Optional[float]
    co2_unit: Optional[str]
    issuing_authority: Optional[str]
    cert_number: Optional[str]
    verification_standard: Optional[str]
    issue_date: Optional[str]
    expiry_date: Optional[str]
    flagged_reasons: List[str]


class EmissionVerification(TypedDict):
    total_documents: int
    valid_documents: int
    flagged_documents: int
    invalid_documents: int
    pending_documents: int
    expired_documents: int
    average_ai_confidence: float
    reasonable_assurance_met: bool
    assurance_threshold: float
    document_details: List[DocumentDetail]


class ScopeEmissions(TypedDict):
    total_co2_tonnes: float
    extracted_from_docs: float
    unit: str
    emission_band: str
    risk_score: Optional[float]
    risk_level: str
    exceeds_threshold: bool
    industry_thresholds: Dict[str, float]


class FinancialExposure(TypedDict, total=False):
    eur: float
    inr: float
    basis: str
    display: str


class ExposureItem(TypedDict):
    regulation: str
    status: str
    detail: str
    financial_exposure: Optional[FinancialExposure]
    risk_rating: str


class GapItem(TypedDict):
    gap: str
    detail: str
    severity: str


class ReductionStrategy(TypedDict):
    strategy: str
    description: str
    typical_reduction_pct: float
    timeframe: str
    cost_level: str


class ReductionRoadmap(TypedDict):
    current_emissions_tco2e: float
    target_emissions_tco2e: float
    reduction_needed_tco2e: float
    reduction_needed_pct: float
    strategies: List[ReductionStrategy]
    note: str


class CreditType(TypedDict):
    type: str
    standard: str
    price_range: str
    best_for: str
    how_to_buy: str


class CarbonCreditGuidance(TypedDict):
    credits_needed_tco2e: float
    estimated_cost_usd_low: float
    estimated_cost_usd_high: float
    estimated_cost_inr_low: float
    estimated_cost_inr_high: float
    credit_types: List[CreditType]
    steps: List[str]
    important_note: str


class VendorRetention(TypedDict):
    recommendation: str
    reason: str
    confidence_level: str
    review_date: str


class ChecklistItem(TypedDict):
    priority: int
    action: str
    owner: str
    urgency: str


class VendorComplianceReport(TypedDict):
    generated_at: str
    vendor: VendorInfo
    regulatory_applicability: List[RegulationItem]
    emission_verification: EmissionVerification
    scope_emissions: ScopeEmissions
    regulatory_risk_exposure: List[ExposureItem]
    compliance_gap_analysis: List[GapItem]
    reduction_roadmap: Optional[ReductionRoadmap]
    carbon_credit_guidance: Optional[CarbonCreditGuidance]
    vendor_retention: VendorRetention
    recommendations: List[str]
    action_checklist: List[ChecklistItem]


SECTION_TYPES: Dict[str, type] = {
    "": VendorComplianceReport,
    "vendor": VendorInfo,
    "regulatory_applicability": RegulationItem,
    "emission_verification": EmissionVerification,
    "document_details": DocumentDetail,
    "scope_emissions": ScopeEmissions,
    "regulatory_risk_exposure": ExposureItem,
    "financial_exposure": FinancialExposure,
    "compliance_gap_analysis": GapItem,
    "reduction_roadmap": ReductionRoadmap,
    "strategies": ReductionStrategy,
    "carbon_credit_guidance": CarbonCreditGuidance,
    "vendor_retention": VendorRetention,
    "action_checklist": ChecklistItem,
}

EXPORTER_READS: Dict[str, FrozenSet[str]] = {
    "": frozenset({
        "vendor", "regulatory_applicability", "emission_verification", "scope_emissions",
        "regulatory_risk_exposure", "compliance_gap_analysis", "recommendations",
        "reduction_roadmap", "carbon_credit_guidance", "vendor_retention", "action_checklist",
    }),
    "vendor": frozenset({"name", "industry", "country", "compliance_status", "risk_level"}),
    "regulatory_applicability": frozenset({"regulation", "requirement", "deadline"}),
    "emission_verification": frozenset({
        "total_documents", "valid_documents", "flagged_documents", "invalid_documents",
        "expired_documents", "average_ai_confidence", "reasonable_assurance_met",
        "document_details",
    }),
    "document_details": frozenset({
        "document_type", "document_status", "confidence", "co2_extracted", "assurance_met",
    }),
    "scope_emissions": frozenset({"total_co2_tonnes", "risk_score", "exceeds_threshold"}),
    "regulatory_risk_exposure": frozenset({"regulation", "status", "detail", "financial_exposure"}),
    "financial_exposure": frozenset({"eur", "inr", "display"}),
    "compliance_gap_analysis": frozenset({"gap", "detail"}),
    "reduction_roadmap": frozenset({
        "current_emissions_tco2e", "target_emissions_tco2e", "reduction_needed_tco2e",
        "reduction_needed_pct", "strategies",
    }),
    "strategies": frozenset({"strategy", "typical_reduction_pct", "timeframe", "cost_level"}),
    "carbon_credit_guidance": frozenset({
        "credits_needed_tco2e", "estimated_cost_usd_low", "estimated_cost_usd_high",
        "estimated_cost_inr_low", "estimated_cost_inr_high",
    }),
    "vendor_retention": frozenset({"recommendation", "reason", "confidence_level", "review_date"}),
    "action_checklist": frozenset({"priority", "action", "owner", "urgency"}),
}

