import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from vendors.models import Vendor, Industry, Document, DocumentType
from ai_validation.models import (
    DocumentValidation, ExtractedMetadata, IndustryEmissionThreshold, VendorRiskProfile,
)
from ai_validation.services.risk_calculator import RiskCalculator
from ai_validation.services.orchestrator import ValidationOrchestrator
from ai_validation.services.metadata_extractor import MetadataExtractor
from reports.services import PDFExporter
from reports.models import Report


# ── Shared fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology")


@pytest.fixture
def threshold(industry):
    return IndustryEmissionThreshold.objects.create(
        industry=industry,
        low_threshold=Decimal("300"),
        medium_threshold=Decimal("1500"),
        high_threshold=Decimal("5000"),
        critical_threshold=Decimal("12000"),
    )


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Bugfix Vendor",
        industry=industry, country="India", contact_email="bugfix@vendor.com",
    )


@pytest.fixture
def doc_type(db):
    return DocumentType.objects.create(name="Emission Report")


@pytest.fixture
def document(vendor, doc_type):
    return Document.objects.create(vendor=vendor, document_type=doc_type, status="uploaded")


@pytest.fixture
def validation(document):
    from django.utils import timezone
    return DocumentValidation.objects.create(
        document=document, status="pending", started_at=timezone.now(),
    )


# ── Bug 1: compliance_status ignored emissions/risk ─────────────────────────

@pytest.mark.django_db
class TestBug1_ComplianceStatusRespectsEmissions:

    def test_valid_docs_but_critical_emissions_marks_non_compliant(
        self, vendor, threshold, document, validation
    ):
        document.status = "valid"
        document.save(update_fields=["status"])
        validation.status = "completed"
        validation.save(update_fields=["status"])
        ExtractedMetadata.objects.create(
            document_validation=validation,
            document=document,
            co2_value=Decimal("15000"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        profile = RiskCalculator().calculate(vendor)
        vendor.refresh_from_db()

        assert profile.exceeds_threshold is True
        assert profile.risk_level == "critical"
        assert vendor.compliance_status == "non_compliant", (
            "BUG: vendor with critical emissions but clean docs was still shown compliant"
        )

    def test_valid_docs_within_threshold_stays_compliant(
        self, vendor, threshold, document, validation
    ):
        document.status = "valid"
        document.save(update_fields=["status"])
        ExtractedMetadata.objects.create(
            document_validation=validation,
            document=document,
            co2_value=Decimal("100"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        RiskCalculator().calculate(vendor)
        vendor.refresh_from_db()
        assert vendor.compliance_status == "compliant"


# ── Bug 2: orchestrator never flagged high emissions for manual review ──────

@pytest.mark.django_db
class TestBug2_OrchestratorFlagsHighEmissions:

    def test_exceeds_threshold_triggers_manual_review_flag(self, validation, threshold):
        validation.overall_confidence = Decimal("85")
        validation.authenticity_red_flags = []
        validation.is_relevant = True
        validation.detected_document_type = "Emission Report"

        mock_meta = MagicMock()
        mock_meta.co2_value = Decimal("15000")
        mock_meta.co2_unit = "tonnes"
        mock_meta.expiry_date = None

        should_flag, reason = ValidationOrchestrator()._flag_decision(validation, mock_meta)
        assert should_flag is True
        assert "exceeds_emission_threshold" in reason

    def test_within_threshold_does_not_flag_on_emissions(self, validation, threshold):
        validation.overall_confidence = Decimal("85")
        validation.authenticity_red_flags = []
        validation.is_relevant = True
        validation.detected_document_type = "Emission Report"

        mock_meta = MagicMock()
        mock_meta.co2_value = Decimal("100")
        mock_meta.co2_unit = "tonnes"
        mock_meta.expiry_date = None

        should_flag, reason = ValidationOrchestrator()._flag_decision(validation, mock_meta)
        assert "exceeds_emission_threshold" not in reason

    def test_threshold_lookup_failure_does_not_crash_flag_decision(self, validation):
        validation.overall_confidence = Decimal("85")
        validation.authenticity_red_flags = []
        validation.is_relevant = True
        validation.detected_document_type = "Emission Report"

        mock_meta = MagicMock()
        mock_meta.co2_value = Decimal("100")
        mock_meta.co2_unit = "tonnes"
        mock_meta.expiry_date = None

        orch = ValidationOrchestrator()
        with patch.object(orch.risk, "_get_threshold", side_effect=Exception("db down")):
            should_flag, reason = orch._flag_decision(validation, mock_meta)
        # must not raise — failure is swallowed and logged


# ── Bug 3: brand-new vendors defaulted to "medium" instead of "unknown" ─────

@pytest.mark.django_db
class TestBug3_UnknownDefaultRiskLevel:

    def test_vendor_model_default_is_unknown(self, vendor):
        assert vendor.risk_level == "unknown"

    def test_no_validated_docs_returns_unknown(self, vendor, threshold):
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level == "unknown"

    def test_unknown_is_a_valid_choice(self):
        choices = dict(Vendor.RISK_LEVEL_CHOICES)
        assert "unknown" in choices


# ── Bug 4: NGT report exposure row ignored emissions ─────────────────────────

@pytest.mark.django_db
class TestBug4_NGTExposureReflectsEmissions:

    def test_ngt_at_risk_when_exceeds_threshold_even_with_clean_docs(
        self, verified_org, vendor, threshold, document, validation
    ):
        document.status = "valid"
        document.save(update_fields=["status"])
        validation.status = "completed"
        validation.save(update_fields=["status"])
        ExtractedMetadata.objects.create(
            document_validation=validation,
            document=document,
            co2_value=Decimal("15000"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        RiskCalculator().calculate(vendor)

        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org,
            vendor=vendor,
        )
        ngt = next(
            (e for e in data["regulatory_risk_exposure"] if e["regulation"] == "NGT India"),
            None,
        )
        assert ngt is not None
        assert ngt["status"] == "at_risk", (
            "BUG: NGT exposure row showed compliant for a vendor exceeding emission threshold"
        )


# ── Bug 5: hardcoded threshold dicts drifting from DB ────────────────────────

@pytest.mark.django_db
class TestBug5_ThresholdsComeFromDB:

    def test_compliance_engine_reads_db_threshold_not_hardcoded_default(
        self, verified_org, vendor, industry, threshold
    ):
        from reports.compliance_engine import _get_threshold
        # threshold fixture sets high=5000; DEFAULT_THRESHOLDS default for Technology is different
        result = _get_threshold("Technology")
        assert result["high"] == 5000.0
        assert result["critical"] == 12000.0

    def test_threshold_endpoint_returns_db_values(self, admin_client, threshold):
        res = admin_client.get("/api/ai-validation/thresholds/")
        assert res.status_code == 200
        row = next(t for t in res.data if t["industry_name"] == "Technology")
        assert Decimal(row["high_threshold"]) == Decimal("5000")

    def test_threshold_endpoint_requires_auth(self, anon_client):
        assert anon_client.get("/api/ai-validation/thresholds/").status_code == 401


# ── Bug 6: fallback model gemini-2.0-flash was deprecated (404) ─────────────

class TestBug6_FallbackModelValid:

    def test_fallback_model_is_not_deprecated(self):
        from ai_validation.services.langchain_client import _FALLBACK_MODEL
        assert _FALLBACK_MODEL != "gemini-2.0-flash", (
            "BUG: fallback model is the deprecated gemini-2.0-flash — every fallback "
            "call returns 404 and extraction silently fails"
        )

    def test_primary_model_unchanged(self):
        from ai_validation.services.langchain_client import _PRIMARY_MODEL
        assert _PRIMARY_MODEL == "gemini-2.5-flash"


# ── Bug 7: cached extraction results kept string dates → crashed orchestrator ─

@pytest.mark.django_db
class TestBug7_CacheHydrationFixesDateTypeError:

    def test_hydrate_cached_converts_dates_and_numbers(self):
        extractor = MetadataExtractor.__new__(MetadataExtractor)
        cached = {
            "co2_value": "1500.5",
            "co2_confidence": "90",
            "co2_unit": "tonnes",
            "issue_date": "2026-01-15",
            "issue_date_confidence": "88",
            "expiry_date": "2027-01-15",
            "expiry_date_confidence": "85",
            "issuing_authority": "Bureau Veritas",
            "issuing_authority_confidence": "80",
            "certificate_number": "X-1",
            "verification_standard": "ISO 14064",
        }
        hydrated = extractor._hydrate_cached(cached)

        assert isinstance(hydrated["issue_date"], date)
        assert isinstance(hydrated["expiry_date"], date)
        assert isinstance(hydrated["co2_value"], Decimal)
        assert isinstance(hydrated["co2_confidence"], Decimal)

    def test_hydrate_cached_handles_garbage_date_gracefully(self):
        extractor = MetadataExtractor.__new__(MetadataExtractor)
        hydrated = extractor._hydrate_cached({"expiry_date": "not-a-date"})
        assert hydrated["expiry_date"] is None

    def test_hydrate_cached_handles_missing_fields(self):
        extractor = MetadataExtractor.__new__(MetadataExtractor)
        hydrated = extractor._hydrate_cached({})
        assert hydrated.get("expiry_date") is None
        assert hydrated.get("co2_value") is None

    def test_cache_hit_path_produces_real_date_object_end_to_end(
        self, validation, document
    ):
        cached_payload = {
            "co2_value": "500",
            "co2_unit": "tonnes",
            "co2_confidence": "90",
            "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            "expiry_date_confidence": "85",
            "issue_date": None,
            "issue_date_confidence": 0,
            "issuing_authority": "Test Authority",
            "issuing_authority_confidence": "70",
            "certificate_number": "C-1",
            "verification_standard": "ISO 14064",
        }
        with patch(
            "ai_validation.services.metadata_extractor.get_cached",
            return_value=cached_payload,
        ):
            extractor = MetadataExtractor()
            ok, metadata, err = extractor.extract("fakeb64", validation, file_path="/fake/path.pdf")

        assert ok is True
        assert metadata is not None
        assert isinstance(metadata.expiry_date, date), (
            "BUG: cached expiry_date stayed a string and would crash "
            "orchestrator._flag_decision's date comparison"
        )

    def test_flag_decision_does_not_crash_with_hydrated_date(self, validation, threshold):
        validation.overall_confidence = Decimal("80")
        validation.authenticity_red_flags = []
        validation.is_relevant = True
        validation.detected_document_type = "Emission Report"

        mock_meta = MagicMock()
        mock_meta.co2_value = Decimal("500")
        mock_meta.expiry_date = date.today() + timedelta(days=100)  # real date, not str

        should_flag, reason = ValidationOrchestrator()._flag_decision(validation, mock_meta)
        assert isinstance(should_flag, bool)

    def test_flag_decision_would_crash_with_unhydrated_string_date(self, validation, threshold):
        validation.overall_confidence = Decimal("80")
        validation.authenticity_red_flags = []
        validation.is_relevant = True
        validation.detected_document_type = "Emission Report"

        mock_meta = MagicMock()
        mock_meta.co2_value = Decimal("500")
        mock_meta.expiry_date = "2026-03-15"  # simulates the old unhydrated bug

        with pytest.raises(TypeError):
            ValidationOrchestrator()._flag_decision(validation, mock_meta)


# ── Bug 8: PDF export KeyError 'status' on vendor_compliance_report ─────────

@pytest.mark.django_db
class TestBug8_PDFExportKeyMismatch:

    def test_pdf_export_with_populated_document_details_does_not_crash(
        self, verified_org, vendor, officer_user
    ):
        report = Report.objects.create(
            organization=verified_org,
            report_type="vendor_compliance_report",
            title="Bug8 Regression",
            vendor=vendor,
            generated_by=officer_user,
            status="generated",
            data={
                "vendor": {
                    "name": vendor.name, "industry": "Technology", "country": "India",
                    "compliance_status": "non_compliant", "risk_level": "critical",
                },
                "regulatory_applicability": [],
                "emission_verification": {
                    "total_documents": 1, "valid_documents": 1, "flagged_documents": 0,
                    "invalid_documents": 0, "expired_documents": 0,
                    "average_ai_confidence": 88.4, "reasonable_assurance_met": True,
                    "document_details": [{
                        "document_type": "Emission Report",
                        "document_status": "valid",
                        "validation_status": "completed",
                        "confidence": 88.4,
                        "co2_extracted": 5000.0,
                        "co2_unit": "tonnes",
                        "assurance_met": True,
                    }],
                },
                "scope_emissions": {
                    "total_co2_tonnes": 5000, "risk_score": 40, "exceeds_threshold": True,
                },
                "regulatory_risk_exposure": [],
                "compliance_gap_analysis": [],
                "recommendations": [],
            },
        )
        pdf_bytes = PDFExporter().export(report)
        assert pdf_bytes.startswith(b"%PDF"), (
            "BUG: PDF export crashed with KeyError('status') on populated document_details"
        )

    def test_pdf_export_via_api_endpoint_with_document_details(
        self, officer_client, verified_org, vendor, officer_user
    ):
        report = Report.objects.create(
            organization=verified_org,
            report_type="vendor_compliance_report",
            title="Bug8 API Regression",
            vendor=vendor,
            generated_by=officer_user,
            status="generated",
            data={
                "vendor": {"name": vendor.name, "industry": "Technology", "country": "India",
                           "compliance_status": "compliant", "risk_level": "low"},
                "regulatory_applicability": [],
                "emission_verification": {
                    "total_documents": 1, "valid_documents": 1, "flagged_documents": 0,
                    "invalid_documents": 0, "expired_documents": 0,
                    "average_ai_confidence": 90.0, "reasonable_assurance_met": True,
                    "document_details": [{
                        "document_type": "Emission Report",
                        "document_status": "valid",
                        "validation_status": "completed",
                        "confidence": 90.0,
                        "co2_extracted": 100.0,
                        "co2_unit": "tonnes",
                        "assurance_met": True,
                    }],
                },
                "scope_emissions": {"total_co2_tonnes": 100, "risk_score": 5, "exceeds_threshold": False},
                "regulatory_risk_exposure": [],
                "compliance_gap_analysis": [],
                "recommendations": [],
            },
        )
        res = officer_client.get(f"/api/reports/{report.id}/download_pdf/")
        assert res.status_code == 200
        assert res["Content-Type"] == "application/pdf"


# ── End-to-end: full pipeline sanity check tying multiple fixes together ────

@pytest.mark.django_db
class TestFullPipelineRegression:

    def test_critical_emitter_is_consistent_across_risk_profile_vendor_and_report(
        self, verified_org, vendor, threshold, document, validation
    ):
        document.status = "valid"
        document.save(update_fields=["status"])
        validation.status = "completed"
        validation.save(update_fields=["status"])
        ExtractedMetadata.objects.create(
            document_validation=validation,
            document=document,
            co2_value=Decimal("20000"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("95"),
        )

        profile = RiskCalculator().calculate(vendor)
        vendor.refresh_from_db()

        assert profile.risk_level == "critical"
        assert profile.exceeds_threshold is True
        assert vendor.risk_level == "critical"
        assert vendor.compliance_status == "non_compliant"

        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org,
            vendor=vendor,
        )
        assert data["vendor"]["compliance_status"] == "non_compliant"
        assert data["vendor"]["risk_level"] == "critical"
        ngt = next(
            (e for e in data["regulatory_risk_exposure"] if e["regulation"] == "NGT India"),
            None,
        )
        if ngt:
            assert ngt["status"] == "at_risk"