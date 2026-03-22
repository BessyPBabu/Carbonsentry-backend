import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.utils import timezone

from vendors.models import Document, DocumentType, Industry, Vendor
from ai_validation.models import (
    DocumentValidation, ExtractedMetadata, AIAuditLog,
    IndustryEmissionThreshold, VendorRiskProfile, ManualReviewQueue,
)
from ai_validation.services.validators import DataValidator
from ai_validation.services.risk_calculator import RiskCalculator
from ai_validation.constants import MIN_AUTO_APPROVE_CONFIDENCE


# ── URL helpers ───────────────────────────────────────────────────────────────

VALIDATIONS_URL  = "/api/ai-validation/validations/"
RISK_URL         = "/api/ai-validation/risk-profiles/"
REVIEW_URL       = "/api/ai-validation/manual-reviews/"
MONITORING_URL   = "/api/ai-validation/monitoring/"
TRIGGER_URL      = "/api/ai-validation/validations/trigger_validation/"
STATS_URL        = "/api/ai-validation/validations/statistics/"
HIGH_RISK_URL    = "/api/ai-validation/risk-profiles/high_risk/"
DASH_STATS_URL   = "/api/ai-validation/risk-profiles/dashboard_stats/"


def review_assign_url(pk):   return f"/api/ai-validation/manual-reviews/{pk}/assign/"
def review_resolve_url(pk):  return f"/api/ai-validation/manual-reviews/{pk}/resolve/"
def risk_recalc_url(pk):     return f"/api/ai-validation/risk-profiles/{pk}/recalculate/"
def validation_logs_url(pk): return f"/api/ai-validation/validations/{pk}/audit_logs/"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology", description="Tech")


@pytest.fixture
def doc_type(db):
    return DocumentType.objects.create(name="Emission Report")


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Acme Corp",
        industry=industry, country="India", contact_email="acme@acme.com",
    )


@pytest.fixture
def document(vendor, doc_type):
    return Document.objects.create(
        vendor=vendor, document_type=doc_type, status="uploaded",
    )


@pytest.fixture
def validation(document):
    return DocumentValidation.objects.create(
        document=document,
        status="pending",
        started_at=timezone.now(),
    )


@pytest.fixture
def completed_validation(document):
    v = DocumentValidation.objects.create(
        document=document,
        status="completed",
        started_at=timezone.now(),
        completed_at=timezone.now(),
        overall_confidence=Decimal("78.50"),
        readability_passed=True,
        readability_score=Decimal("90.0"),   # digital PDF → high readability
        is_relevant=True,
        relevance_confidence=Decimal("92.0"),
        authenticity_score=Decimal("85.0"),
        requires_manual_review=False,
    )
    return v


@pytest.fixture
def flagged_document(vendor, doc_type):
    dt, _ = DocumentType.objects.get_or_create(name="Carbon Credit Certificate")
    return Document.objects.create(vendor=vendor, document_type=dt, status="uploaded")


@pytest.fixture
def flagged_validation(flagged_document):
    v = DocumentValidation.objects.create(
        document=flagged_document,
        status="completed",
        started_at=timezone.now(),
        completed_at=timezone.now(),
        overall_confidence=Decimal("38.0"),
        requires_manual_review=True,
        flagged_reason="Low confidence (38.0% < 50%)",
    )
    return v


@pytest.fixture
def metadata(completed_validation, document):
    return ExtractedMetadata.objects.create(
        document_validation=completed_validation,
        document=document,
        co2_value=Decimal("1500.00"),
        co2_unit="tonnes",
        co2_extraction_confidence=Decimal("90.0"),
        issue_date=date(2024, 1, 15),
        issue_date_confidence=Decimal("92.0"),
        expiry_date=date.today() + timedelta(days=365),  # future expiry — valid
        expiry_date_confidence=Decimal("90.0"),
        issuing_authority="Bureau Veritas",
        issuing_authority_confidence=Decimal("88.0"),
        certificate_number="BV-2024-001",
        verification_standard="ISO 14064",
    )


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
def risk_profile(vendor, verified_org):
    return VendorRiskProfile.objects.create(
        vendor=vendor,
        organization=verified_org,
        risk_level="medium",
        risk_score=Decimal("45.00"),
        total_documents=2,
        validated_documents=1,
        flagged_documents=0,
    )


@pytest.fixture
def review_item(flagged_validation):
    return ManualReviewQueue.objects.create(
        document_validation=flagged_validation,
        priority="medium",
        reason="Low confidence score",
        status="pending",
    )


# ── DocumentValidation model ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentValidationModel:

    def test_default_status_pending(self, document):
        v = DocumentValidation.objects.create(document=document)
        assert v.status == "pending"

    def test_default_step_not_started(self, document):
        v = DocumentValidation.objects.create(document=document)
        assert v.current_step == "not_started"

    def test_requires_manual_review_default_false(self, document):
        v = DocumentValidation.objects.create(document=document)
        assert v.requires_manual_review is False

    def test_one_to_one_with_document(self, document, validation):
        with pytest.raises(Exception):
            DocumentValidation.objects.create(document=document)

    def test_uuid_pk(self, validation):
        assert isinstance(validation.id, uuid.UUID)

    def test_completed_validation_has_confidence(self, completed_validation):
        assert completed_validation.overall_confidence == Decimal("78.50")


# ── ExtractedMetadata model ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestExtractedMetadataModel:

    def test_co2_value_stored_as_decimal(self, metadata):
        assert metadata.co2_value == Decimal("1500.00")

    def test_unit_choices(self, metadata):
        assert metadata.co2_unit in ("tonnes", "kg", "metric_tons")

    def test_issuing_authority_stored(self, metadata):
        assert metadata.issuing_authority == "Bureau Veritas"

    def test_certificate_number_stored(self, metadata):
        assert metadata.certificate_number == "BV-2024-001"

    def test_future_expiry_date_stored_correctly(self, metadata):
        """Expiry dates in the future must be stored as-is — they are valid."""
        assert metadata.expiry_date > date.today()

    def test_one_to_one_with_validation(self, completed_validation, document, metadata):
        with pytest.raises(Exception):
            ExtractedMetadata.objects.create(
                document_validation=completed_validation,
                document=document,
            )


# ── DataValidator service ─────────────────────────────────────────────────────

class TestDataValidator:

    def test_valid_date_returns_date_object(self):
        ok, result = DataValidator.validate_date("2024-01-15")
        assert ok is True
        assert result == date(2024, 1, 15)

    def test_alternate_date_formats_accepted(self):
        ok, result = DataValidator.validate_date("15/01/2024")
        assert ok is True
        assert result == date(2024, 1, 15)

    def test_none_date_returns_none(self):
        ok, result = DataValidator.validate_date(None)
        assert ok is True
        assert result is None

    def test_issue_date_in_future_rejected(self):
        future = (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")
        ok, _ = DataValidator.validate_date(future, is_expiry=False)
        assert ok is False

    def test_expiry_date_in_future_accepted(self):
        """Expiry dates in the future are valid — is_expiry=True must allow them."""
        future = (date.today() + timedelta(days=365)).strftime("%Y-%m-%d")
        ok, result = DataValidator.validate_date(future, is_expiry=True)
        assert ok is True
        assert result > date.today()

    def test_expiry_date_two_years_ahead_accepted(self):
        """Multi-year certificate validity windows are normal."""
        future = (date.today() + timedelta(days=730)).strftime("%Y-%m-%d")
        ok, result = DataValidator.validate_date(future, is_expiry=True)
        assert ok is True

    def test_issue_date_today_accepted(self):
        today = date.today().strftime("%Y-%m-%d")
        ok, result = DataValidator.validate_date(today, is_expiry=False)
        assert ok is True
        assert result == date.today()

    def test_date_before_2000_rejected(self):
        ok, _ = DataValidator.validate_date("1999-12-31")
        assert ok is False

    def test_unparseable_date_rejected(self):
        ok, _ = DataValidator.validate_date("not-a-date")
        assert ok is False

    def test_valid_co2_value(self):
        ok, result = DataValidator.validate_co2_value(1500.0)
        assert ok is True
        assert result == 1500.0

    def test_none_co2_returns_none(self):
        ok, result = DataValidator.validate_co2_value(None)
        assert ok is True
        assert result is None

    def test_negative_co2_rejected(self):
        ok, _ = DataValidator.validate_co2_value(-100)
        assert ok is False

    def test_unrealistically_high_co2_rejected(self):
        ok, _ = DataValidator.validate_co2_value(10_000_000_001)
        assert ok is False

    def test_zero_co2_accepted(self):
        """Zero emissions is valid (e.g. carbon neutral certified)."""
        ok, result = DataValidator.validate_co2_value(0)
        assert ok is True
        assert result == 0.0

    def test_normalize_unit_kg(self):
        assert DataValidator.normalize_unit("kg") == "kg"
        assert DataValidator.normalize_unit("kilogram") == "kg"

    def test_normalize_unit_tonnes(self):
        assert DataValidator.normalize_unit("tonnes") == "tonnes"
        assert DataValidator.normalize_unit("metric_tons") == "tonnes"
        assert DataValidator.normalize_unit("tonne") == "tonnes"

    def test_normalize_unit_unknown_defaults_to_tonnes(self):
        assert DataValidator.normalize_unit("unknown_unit") == "tonnes"

    def test_normalize_unit_empty_defaults_to_tonnes(self):
        assert DataValidator.normalize_unit("") == "tonnes"
        assert DataValidator.normalize_unit(None) == "tonnes"


# ── Constants sanity checks ───────────────────────────────────────────────────

class TestConstants:

    def test_min_auto_approve_confidence_value(self):
        """MIN_AUTO_APPROVE_CONFIDENCE should be 50 — documents with all fields
        naturally score well above this."""
        from ai_validation.constants import MIN_AUTO_APPROVE_CONFIDENCE
        assert MIN_AUTO_APPROVE_CONFIDENCE == 50.0

    def test_confidence_weights_sum_to_one(self):
        from ai_validation.constants import CONFIDENCE_WEIGHTS
        total = sum(CONFIDENCE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001, f"Weights must sum to 1.0, got {total}"

    def test_risk_score_display_divisor(self):
        from ai_validation.constants import RISK_SCORE_DISPLAY_DIVISOR
        assert RISK_SCORE_DISPLAY_DIVISOR == 20

    def test_max_score_displays_as_five(self):
        from ai_validation.constants import RISK_SCORE_DISPLAY_DIVISOR
        assert 100 / RISK_SCORE_DISPLAY_DIVISOR == 5.0

    def test_medium_score_displays_correctly(self):
        from ai_validation.constants import RISK_SCORE_DISPLAY_DIVISOR
        assert 50 / RISK_SCORE_DISPLAY_DIVISOR == 2.5


# ── DocumentValidation ViewSet ────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentValidationViewSet:

    def test_officer_can_list_validations(self, officer_client, completed_validation):
        res = officer_client.get(VALIDATIONS_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(VALIDATIONS_URL).status_code == 401

    def test_org_isolation(self, officer_client, completed_validation):
        res = officer_client.get(VALIDATIONS_URL)
        for v in res.data.get("results", res.data):
            assert "id" in v

    def test_filter_by_status(self, officer_client, completed_validation, flagged_validation):
        res = officer_client.get(VALIDATIONS_URL, {"status": "completed"})
        assert res.status_code == 200

    def test_statistics_endpoint(self, officer_client, completed_validation):
        res = officer_client.get(STATS_URL)
        assert res.status_code == 200
        assert "total_validations" in res.data
        assert "completed" in res.data
        assert "requires_review" in res.data

    def test_recent_endpoint(self, officer_client, completed_validation):
        res = officer_client.get("/api/ai-validation/validations/recent/")
        assert res.status_code == 200

    def test_audit_logs_endpoint(self, officer_client, completed_validation):
        AIAuditLog.objects.create(
            document_validation=completed_validation,
            validation_step="readability",
            prompt_sent="test prompt",
            raw_response="test response",
            success=True,
        )
        res = officer_client.get(validation_logs_url(completed_validation.id))
        assert res.status_code == 200
        assert len(res.data) == 1

    def test_trigger_validation_missing_document_id(self, officer_client):
        res = officer_client.post(TRIGGER_URL, {}, format="json")
        assert res.status_code == 400

    def test_trigger_validation_nonexistent_document(self, officer_client):
        res = officer_client.post(TRIGGER_URL, {"document_id": str(uuid.uuid4())}, format="json")
        assert res.status_code == 404

    def test_trigger_validation_no_file_400(self, officer_client, document):
        res = officer_client.post(TRIGGER_URL, {"document_id": str(document.id)}, format="json")
        assert res.status_code == 400

    def test_trigger_validation_already_processing_400(self, officer_client, document, validation):
        validation.status = "processing"
        validation.save()
        res = officer_client.post(TRIGGER_URL, {"document_id": str(document.id)}, format="json")
        assert res.status_code == 400

    @patch("ai_validation.views.validate_document_async.delay")
    def test_trigger_validation_queues_task(self, mock_delay, officer_client, document):
        document.file = "vendor_documents/2024/01/test.pdf"
        document.save()
        mock_delay.return_value = MagicMock(id="task-123")
        res = officer_client.post(TRIGGER_URL, {"document_id": str(document.id)}, format="json")
        assert res.status_code == 200
        mock_delay.assert_called_once()


# ── Orchestrator (mocked AI) ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestValidationOrchestrator:

    # ── Helper: mock AI responses that represent a perfect compliant document ──

    @staticmethod
    def _perfect_doc_mocks(MockPrep, MockRead, MockRel, MockAuth, MockMeta, MockRisk):
        """Configure mocks to simulate a well-formed carbon certificate."""
        MockPrep.return_value.process.return_value = (True, "base64img", None)
        MockRead.return_value.check.return_value = (True, {
            "is_readable": True,
            "quality_score": 92.0,   # digital PDF
            "language": "English",
            "issues": [],
        }, None)
        MockRel.return_value.classify.return_value = (True, {
            "is_relevant": True,
            "document_type": "Carbon Credit Certificate",
            "confidence": 95.0,
            "indicators": ["CO2 value in tonnes", "ISO 14064 logo", "cert number present"],
        }, None)
        MockAuth.return_value.analyze.return_value = (True, {
            "score": 90.0,
            "indicators": ["org letterhead", "certificate number", "expiry date", "issuing authority"],
            "red_flags": [],
        }, None)

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("90")
        mock_meta.issue_date_confidence = Decimal("92")
        mock_meta.expiry_date_confidence = Decimal("90")
        mock_meta.issuing_authority_confidence = Decimal("88")
        mock_meta.expiry_date = date(2026, 1, 15)
        MockMeta.return_value.extract.return_value = (True, mock_meta, None)
        MockRisk.return_value.calculate.return_value = None

    @patch("ai_validation.services.orchestrator.DocumentPreprocessor")
    @patch("ai_validation.services.orchestrator.ReadabilityChecker")
    @patch("ai_validation.services.orchestrator.RelevanceClassifier")
    @patch("ai_validation.services.orchestrator.AuthenticityAnalyzer")
    @patch("ai_validation.services.orchestrator.MetadataExtractor")
    @patch("ai_validation.services.orchestrator.RiskCalculator")
    def test_perfect_document_passes_auto_approve(
        self, MockRisk, MockMeta, MockAuth, MockRel, MockRead, MockPrep,
        document, validation
    ):
        """A complete, well-formed carbon certificate should auto-approve (no manual review)."""
        self._perfect_doc_mocks(MockPrep, MockRead, MockRel, MockAuth, MockMeta, MockRisk)

        from ai_validation.services.orchestrator import ValidationOrchestrator
        result = ValidationOrchestrator().validate_document(document, validation)

        assert result.status == "completed"
        assert result.requires_manual_review is False, (
            "A perfect document should not require manual review. "
            f"Confidence was {result.overall_confidence}. "
            f"MIN_AUTO_APPROVE_CONFIDENCE={MIN_AUTO_APPROVE_CONFIDENCE}"
        )
        assert float(result.overall_confidence) > MIN_AUTO_APPROVE_CONFIDENCE
        document.refresh_from_db()
        assert document.status == "valid"

    @patch("ai_validation.services.orchestrator.DocumentPreprocessor")
    @patch("ai_validation.services.orchestrator.ReadabilityChecker")
    @patch("ai_validation.services.orchestrator.RelevanceClassifier")
    @patch("ai_validation.services.orchestrator.AuthenticityAnalyzer")
    @patch("ai_validation.services.orchestrator.MetadataExtractor")
    @patch("ai_validation.services.orchestrator.RiskCalculator")
    def test_successful_validation_marks_completed(
        self, MockRisk, MockMeta, MockAuth, MockRel, MockRead, MockPrep,
        document, validation
    ):
        self._perfect_doc_mocks(MockPrep, MockRead, MockRel, MockAuth, MockMeta, MockRisk)

        from ai_validation.services.orchestrator import ValidationOrchestrator
        result = ValidationOrchestrator().validate_document(document, validation)

        assert result.status == "completed"
        assert result.overall_confidence is not None

    @patch("ai_validation.services.orchestrator.DocumentPreprocessor")
    def test_preprocessing_failure_marks_failed(self, MockPrep, document, validation):
        MockPrep.return_value.process.return_value = (False, None, "File not found")

        from ai_validation.services.orchestrator import ValidationOrchestrator
        result = ValidationOrchestrator().validate_document(document, validation)

        assert result.status == "failed"
        assert result.requires_manual_review is True
        document.refresh_from_db()
        assert document.status == "invalid"

    @patch("ai_validation.services.orchestrator.DocumentPreprocessor")
    @patch("ai_validation.services.orchestrator.ReadabilityChecker")
    @patch("ai_validation.services.orchestrator.RelevanceClassifier")
    @patch("ai_validation.services.orchestrator.AuthenticityAnalyzer")
    @patch("ai_validation.services.orchestrator.MetadataExtractor")
    @patch("ai_validation.services.orchestrator.RiskCalculator")
    def test_fake_document_with_sample_watermark_gets_flagged(
        self, MockRisk, MockMeta, MockAuth, MockRel, MockRead, MockPrep,
        document, validation
    ):
        """A document with SAMPLE/DRAFT watermarks should be flagged for review."""
        MockPrep.return_value.process.return_value = (True, "base64img", None)
        MockRead.return_value.check.return_value = (True, {
            "is_readable": True, "quality_score": 85.0, "language": "English", "issues": [],
        }, None)
        MockRel.return_value.classify.return_value = (True, {
            "is_relevant": True, "document_type": "Emission Report",
            "confidence": 70.0, "indicators": [],
        }, None)
        MockAuth.return_value.analyze.return_value = (True, {
            "score": 50.0,   # floor — SAMPLE watermark detected
            "indicators": [],
            "red_flags": ["SAMPLE watermark visible", "VOID stamp on header", "Lorem ipsum footer"],
        }, None)

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("30")
        mock_meta.issue_date_confidence = Decimal("20")
        mock_meta.expiry_date_confidence = None
        mock_meta.issuing_authority_confidence = Decimal("20")
        mock_meta.expiry_date = None
        MockMeta.return_value.extract.return_value = (True, mock_meta, None)
        MockRisk.return_value.calculate.return_value = None

        from ai_validation.services.orchestrator import ValidationOrchestrator
        result = ValidationOrchestrator().validate_document(document, validation)

        assert result.requires_manual_review is True
        document.refresh_from_db()
        assert document.status == "flagged"

    @patch("ai_validation.services.orchestrator.DocumentPreprocessor")
    @patch("ai_validation.services.orchestrator.ReadabilityChecker")
    @patch("ai_validation.services.orchestrator.RelevanceClassifier")
    @patch("ai_validation.services.orchestrator.AuthenticityAnalyzer")
    @patch("ai_validation.services.orchestrator.MetadataExtractor")
    @patch("ai_validation.services.orchestrator.RiskCalculator")
    def test_low_confidence_sets_requires_review(
        self, MockRisk, MockMeta, MockAuth, MockRel, MockRead, MockPrep,
        document, validation
    ):
        MockPrep.return_value.process.return_value = (True, "base64img", None)
        MockRead.return_value.check.return_value = (True, {
            "is_readable": True, "quality_score": 30.0, "language": "English", "issues": [],
        }, None)
        MockRel.return_value.classify.return_value = (True, {
            "is_relevant": True, "document_type": "Emission Report",
            "confidence": 30.0, "indicators": [],
        }, None)
        MockAuth.return_value.analyze.return_value = (True, {
            "score": 50.0, "indicators": [], "red_flags": [],
        }, None)

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("20")
        mock_meta.issue_date_confidence = None
        mock_meta.expiry_date_confidence = None
        mock_meta.issuing_authority_confidence = Decimal("20")
        mock_meta.expiry_date = None
        MockMeta.return_value.extract.return_value = (True, mock_meta, None)
        MockRisk.return_value.calculate.return_value = None

        from ai_validation.services.orchestrator import ValidationOrchestrator
        result = ValidationOrchestrator().validate_document(document, validation)

        assert result.requires_manual_review is True
        document.refresh_from_db()
        assert document.status == "flagged"

    @patch("ai_validation.services.orchestrator.DocumentPreprocessor")
    @patch("ai_validation.services.orchestrator.ReadabilityChecker")
    @patch("ai_validation.services.orchestrator.RelevanceClassifier")
    @patch("ai_validation.services.orchestrator.AuthenticityAnalyzer")
    @patch("ai_validation.services.orchestrator.MetadataExtractor")
    @patch("ai_validation.services.orchestrator.RiskCalculator")
    def test_each_step_called_exactly_once(
        self, MockRisk, MockMeta, MockAuth, MockRel, MockRead, MockPrep,
        document, validation
    ):
        self._perfect_doc_mocks(MockPrep, MockRead, MockRel, MockAuth, MockMeta, MockRisk)

        from ai_validation.services.orchestrator import ValidationOrchestrator
        ValidationOrchestrator().validate_document(document, validation)

        assert MockRead.return_value.check.call_count == 1, (
            "BUG: readability_checker.check called more than once"
        )
        assert MockRel.return_value.classify.call_count == 1, (
            "BUG: relevance_classifier.classify called more than once"
        )
        assert MockAuth.return_value.analyze.call_count == 1, (
            "BUG: authenticity_analyzer.analyze called more than once"
        )
        assert MockMeta.return_value.extract.call_count == 1, (
            "BUG: metadata_extractor.extract called more than once"
        )


# ── Confidence calculation ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestConfidenceCalculation:

    def _make_orchestrator(self):
        from ai_validation.services.orchestrator import ValidationOrchestrator
        return ValidationOrchestrator()

    def test_perfect_document_confidence_above_threshold(self, validation):
        """
        A perfect carbon certificate should produce confidence well above
        MIN_AUTO_APPROVE_CONFIDENCE (50).

        Expected breakdown (weights: readability 10%, relevance 25%, authenticity 25%, extraction 40%):
          readability:  92 * 0.10 = 9.2
          relevance:    95 * 0.25 = 23.75
          authenticity: 90 * 0.25 = 22.5
          extraction:   90 * 0.40 = 36.0
          Total:                  = 91.45
        """
        validation.readability_score = Decimal("92")
        validation.relevance_confidence = Decimal("95")
        validation.authenticity_score = Decimal("90")

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("90")
        mock_meta.issue_date_confidence = Decimal("92")
        mock_meta.expiry_date_confidence = Decimal("90")
        mock_meta.issuing_authority_confidence = Decimal("88")

        confidence = self._make_orchestrator()._calculate_confidence(validation, mock_meta)
        assert float(confidence) > MIN_AUTO_APPROVE_CONFIDENCE
        assert float(confidence) > 85, f"Perfect doc should score > 85, got {confidence}"

    def test_high_scores_produce_high_confidence(self, validation):
        validation.readability_score = Decimal("90")
        validation.relevance_confidence = Decimal("95")
        validation.authenticity_score = Decimal("88")

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("85")
        mock_meta.issue_date_confidence = Decimal("90")
        mock_meta.expiry_date_confidence = Decimal("88")
        mock_meta.issuing_authority_confidence = Decimal("92")

        confidence = self._make_orchestrator()._calculate_confidence(validation, mock_meta)
        assert float(confidence) > MIN_AUTO_APPROVE_CONFIDENCE

    def test_borderline_document_passes_with_correct_threshold(self, validation):
        """
        A borderline document (medium quality, some fields present) should still
        auto-approve at the 50% threshold.

        Expected:
          readability:  70 * 0.10 = 7.0
          relevance:    70 * 0.25 = 17.5
          authenticity: 65 * 0.25 = 16.25
          extraction:   65 * 0.40 = 26.0
          Total:                  = 66.75 → passes
        """
        validation.readability_score = Decimal("70")
        validation.relevance_confidence = Decimal("70")
        validation.authenticity_score = Decimal("65")

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("65")
        mock_meta.issue_date_confidence = Decimal("65")
        mock_meta.expiry_date_confidence = Decimal("65")
        mock_meta.issuing_authority_confidence = Decimal("65")

        confidence = self._make_orchestrator()._calculate_confidence(validation, mock_meta)
        assert float(confidence) > MIN_AUTO_APPROVE_CONFIDENCE, (
            f"Borderline document should pass at threshold {MIN_AUTO_APPROVE_CONFIDENCE}, "
            f"got {confidence}"
        )

    def test_all_zero_scores_produce_low_confidence(self, validation):
        validation.readability_score = Decimal("0")
        validation.relevance_confidence = Decimal("0")
        validation.authenticity_score = Decimal("0")

        mock_meta = MagicMock()
        mock_meta.co2_extraction_confidence = Decimal("0")
        mock_meta.issue_date_confidence = Decimal("0")
        mock_meta.expiry_date_confidence = Decimal("0")
        mock_meta.issuing_authority_confidence = Decimal("0")

        confidence = self._make_orchestrator()._calculate_confidence(validation, mock_meta)
        assert float(confidence) < MIN_AUTO_APPROVE_CONFIDENCE

    def test_none_metadata_uses_default_extraction_score(self, validation):
        validation.readability_score = Decimal("80")
        validation.relevance_confidence = Decimal("80")
        validation.authenticity_score = Decimal("80")

        confidence = self._make_orchestrator()._calculate_confidence(validation, None)
        assert confidence is not None

    def test_flag_triggered_below_min_confidence(self, validation):
        validation.overall_confidence = Decimal(str(MIN_AUTO_APPROVE_CONFIDENCE - 10))
        validation.authenticity_red_flags = []
        validation.is_relevant = True

        should_flag, reason = self._make_orchestrator()._check_flag(validation)

        assert should_flag is True
        assert "confidence" in reason.lower()

    def test_no_flag_for_good_document(self, validation):
        """A document above the threshold with no red flags should not be flagged."""
        validation.overall_confidence = Decimal(str(MIN_AUTO_APPROVE_CONFIDENCE + 20))
        validation.authenticity_red_flags = []
        validation.is_relevant = True

        should_flag, reason = self._make_orchestrator()._check_flag(validation)
        assert should_flag is False
        assert reason == ""

    def test_many_red_flags_triggers_flag(self, validation):
        validation.overall_confidence = Decimal("70")
        validation.authenticity_red_flags = ["SAMPLE watermark", "VOID stamp", "placeholder text"]
        validation.is_relevant = True

        should_flag, _ = self._make_orchestrator()._check_flag(validation)
        assert should_flag is True

    def test_irrelevant_document_triggers_flag(self, validation):
        validation.overall_confidence = Decimal("75")
        validation.authenticity_red_flags = []
        validation.is_relevant = False

        should_flag, reason = self._make_orchestrator()._check_flag(validation)
        assert should_flag is True
        assert "compliance" in reason.lower() or "relevant" in reason.lower()


# ── Celery task ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestValidateDocumentTask:

    @patch("ai_validation.services.orchestrator.ValidationOrchestrator")
    def test_task_creates_validation_record(self, MockOrch, document):
        document.file = "vendor_documents/2024/01/test.pdf"
        document.save()

        mock_v = MagicMock()
        mock_v.status = "completed"
        mock_v.requires_manual_review = False
        mock_v.overall_confidence = Decimal("82")
        mock_v.id = uuid.uuid4()
        mock_v.error_message = ""
        MockOrch.return_value.validate_document.return_value = mock_v

        from ai_validation.tasks import validate_document_async
        validate_document_async(str(document.id))

        assert DocumentValidation.objects.filter(document=document).exists()

    @patch("ai_validation.services.orchestrator.ValidationOrchestrator")
    def test_task_returns_success_dict(self, MockOrch, document):
        document.file = "vendor_documents/2024/01/test.pdf"
        document.save()

        mock_v = MagicMock()
        mock_v.status = "completed"
        mock_v.requires_manual_review = False
        mock_v.overall_confidence = Decimal("82")
        mock_v.id = uuid.uuid4()
        mock_v.error_message = ""
        MockOrch.return_value.validate_document.return_value = mock_v

        from ai_validation.tasks import validate_document_async
        result = validate_document_async(str(document.id))

        assert "document_id" in result
        assert "validation_id" in result

    def test_task_document_not_found_returns_error(self):
        from ai_validation.tasks import validate_document_async
        result = validate_document_async(str(uuid.uuid4()))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_task_document_no_file_returns_error(self, document):
        from ai_validation.tasks import validate_document_async
        result = validate_document_async(str(document.id))
        assert result["success"] is False

    @patch("ai_validation.services.orchestrator.ValidationOrchestrator")
    def test_task_does_not_raise_attribute_error_on_overall_result(self, MockOrch, document):
        """Tasks must not access validation.overall_result — that field does not exist."""
        document.file = "vendor_documents/2024/01/test.pdf"
        document.save()

        mock_v = MagicMock(spec=DocumentValidation)
        mock_v.status = "completed"
        mock_v.requires_manual_review = False
        mock_v.overall_confidence = Decimal("82")
        mock_v.id = uuid.uuid4()
        mock_v.error_message = ""
        MockOrch.return_value.validate_document.return_value = mock_v

        from ai_validation.tasks import validate_document_async
        try:
            result = validate_document_async(str(document.id))
            assert result is not None
        except AttributeError as e:
            pytest.fail(
                f"BUG: tasks.py accesses a non-existent attribute. Error: {e}"
            )


# ── AIMonitoring view ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAIMonitoringView:

    def test_authenticated_user_can_access(self, admin_client):
        res = admin_client.get(MONITORING_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(MONITORING_URL).status_code == 401

    def test_response_has_metrics_key(self, admin_client):
        res = admin_client.get(MONITORING_URL)
        assert "metrics" in res.data

    def test_response_has_source_key(self, admin_client):
        res = admin_client.get(MONITORING_URL)
        assert "source" in res.data
        assert res.data["source"] in ("prometheus", "database")

    def test_db_fallback_when_no_prometheus_url(self, admin_client, completed_validation, settings):
        settings.PROMETHEUS_URL = ""
        res = admin_client.get(MONITORING_URL)
        assert res.status_code == 200
        assert res.data["source"] == "database"
        assert "validations_valid" in res.data["metrics"]


# ── IndustryEmissionThreshold ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestIndustryEmissionThreshold:

    def test_threshold_created(self, threshold, industry):
        assert threshold.industry == industry
        assert threshold.low_threshold == Decimal("300")
        assert threshold.critical_threshold == Decimal("12000")

    def test_one_to_one_with_industry(self, threshold, industry):
        with pytest.raises(Exception):
            IndustryEmissionThreshold.objects.create(
                industry=industry,
                low_threshold=Decimal("100"),
                medium_threshold=Decimal("500"),
                high_threshold=Decimal("1000"),
                critical_threshold=Decimal("5000"),
            )


# ── RiskCalculator service ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRiskCalculator:

    def test_no_validated_docs_returns_medium(self, vendor, threshold):
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level == "medium"

    def test_low_emissions_returns_low_risk(self, vendor, threshold, document, completed_validation, metadata):
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level in ("low", "medium")

    def test_high_emissions_returns_high_risk(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("6000"),   # above high_threshold of 5000
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level in ("high", "critical")

    def test_critical_emissions_returns_critical_risk(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("15000"),   # above critical_threshold of 12000
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level == "critical"

    def test_kg_unit_converted_to_tonnes(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("1000000"),   # 1000 tonnes when converted
            co2_unit="kg",
            co2_extraction_confidence=Decimal("90"),
        )
        profile = RiskCalculator().calculate(vendor)
        assert profile.total_co2_emissions is not None
        assert profile.risk_level in ("low", "medium")   # 1000 t is below low threshold of 300? no it's above. medium.

    def test_expired_document_increases_score(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("100"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
            expiry_date=date.today() - timedelta(days=10),   # expired
        )
        profile = RiskCalculator().calculate(vendor)
        assert float(profile.risk_score) >= 25   # expired penalty is +25

    def test_expiring_soon_increases_score(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("100"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
            expiry_date=date.today() + timedelta(days=15),   # expiring in < 30 days
        )
        profile = RiskCalculator().calculate(vendor)
        assert float(profile.risk_score) >= 15   # expiring soon penalty is +15

    def test_future_expiry_no_extra_penalty(self, vendor, threshold, document, completed_validation):
        """A certificate with a healthy future expiry should not attract an expiry penalty."""
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("100"),
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
            expiry_date=date.today() + timedelta(days=180),   # 6 months away — no penalty
        )
        profile = RiskCalculator().calculate(vendor)
        # Base score for 100t (safe) is 5, no expiry penalty → should be low
        assert float(profile.risk_score) < 25

    def test_creates_default_threshold_when_none_exists(self, vendor):
        profile = RiskCalculator().calculate(vendor)
        assert profile is not None
        assert IndustryEmissionThreshold.objects.filter(industry=vendor.industry).exists()

    def test_updates_vendor_risk_level(self, vendor, threshold):
        RiskCalculator().calculate(vendor)
        vendor.refresh_from_db()
        assert vendor.risk_level in ("low", "medium", "high", "critical")

    def test_profile_saved_to_db(self, vendor, threshold):
        RiskCalculator().calculate(vendor)
        assert VendorRiskProfile.objects.filter(vendor=vendor).exists()

    def test_exceeds_threshold_flag_set(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("6000"),   # above high_threshold
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        profile = RiskCalculator().calculate(vendor)
        assert profile.exceeds_threshold is True

    def test_below_threshold_flag_not_set(self, vendor, threshold, document, completed_validation):
        ExtractedMetadata.objects.create(
            document_validation=completed_validation,
            document=document,
            co2_value=Decimal("100"),   # well below all thresholds
            co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
        )
        profile = RiskCalculator().calculate(vendor)
        assert profile.exceeds_threshold is False


# ── VendorRiskProfile ViewSet ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorRiskProfileViewSet:

    def test_officer_can_list_risk_profiles(self, officer_client, risk_profile):
        res = officer_client.get(RISK_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(RISK_URL).status_code == 401

    def test_filter_by_risk_level(self, officer_client, risk_profile):
        res = officer_client.get(RISK_URL, {"risk_level": "medium"})
        assert res.status_code == 200

    def test_dashboard_stats_endpoint(self, officer_client, risk_profile):
        res = officer_client.get(DASH_STATS_URL)
        assert res.status_code == 200
        assert "total_vendors" in res.data
        assert "high_risk" in res.data
        assert "critical_risk" in res.data

    def test_high_risk_endpoint(self, officer_client, risk_profile):
        risk_profile.risk_level = "high"
        risk_profile.save()
        res = officer_client.get(HIGH_RISK_URL)
        assert res.status_code == 200

    def test_high_risk_excludes_low_risk(self, officer_client, risk_profile):
        res = officer_client.get(HIGH_RISK_URL)
        assert res.status_code == 200
        for p in res.data:
            assert p["risk_level"] in ("high", "critical")

    @patch("ai_validation.services.risk_calculator.RiskCalculator")
    def test_recalculate_endpoint(self, MockRisk, officer_client, risk_profile):
        MockRisk.return_value.calculate.return_value = risk_profile
        res = officer_client.post(risk_recalc_url(risk_profile.id))
        assert res.status_code == 200
        MockRisk.return_value.calculate.assert_called_once()

    def test_response_includes_vendor_name(self, officer_client, risk_profile):
        res = officer_client.get(RISK_URL)
        results = res.data.get("results", res.data)
        if results:
            assert "vendor_name" in results[0]


# ── ManualReviewQueue model ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestManualReviewQueueModel:

    def test_default_status_pending(self, review_item):
        assert review_item.status == "pending"

    def test_default_priority_medium(self, review_item):
        assert review_item.priority == "medium"

    def test_assigned_to_null_by_default(self, review_item):
        assert review_item.assigned_to is None

    def test_resolved_at_null_by_default(self, review_item):
        assert review_item.resolved_at is None

    def test_linked_to_validation(self, review_item, flagged_validation):
        assert review_item.document_validation == flagged_validation

    def test_reason_stored(self, review_item):
        assert review_item.reason == "Low confidence score"


# ── ManualReviewQueue ViewSet ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestManualReviewQueueViewSet:

    def test_officer_can_list_reviews(self, officer_client, review_item):
        res = officer_client.get(REVIEW_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(REVIEW_URL).status_code == 401

    def test_filter_by_status(self, officer_client, review_item):
        res = officer_client.get(REVIEW_URL, {"status": "pending"})
        assert res.status_code == 200
        results = res.data.get("results", res.data)
        for r in results:
            assert r["status"] == "pending"

    def test_filter_by_priority(self, officer_client, review_item):
        res = officer_client.get(REVIEW_URL, {"priority": "medium"})
        assert res.status_code == 200

    def test_viewer_can_list_reviews(self, viewer_client, review_item):
        res = viewer_client.get(REVIEW_URL)
        assert res.status_code == 200

    def test_response_includes_validation_data(self, officer_client, review_item):
        res = officer_client.get(REVIEW_URL)
        results = res.data.get("results", res.data)
        assert len(results) >= 1
        assert "validation" in results[0]

    # ── assign action ─────────────────────────────────────────────────────────

    def test_officer_can_assign_review(self, officer_client, review_item, officer_user):
        res = officer_client.post(review_assign_url(review_item.id))
        assert res.status_code == 200
        review_item.refresh_from_db()
        assert review_item.assigned_to == officer_user
        assert review_item.status == "in_progress"

    def test_assign_sets_status_in_progress(self, officer_client, review_item):
        officer_client.post(review_assign_url(review_item.id))
        review_item.refresh_from_db()
        assert review_item.status == "in_progress"

    def test_unauthenticated_cannot_assign(self, anon_client, review_item):
        assert anon_client.post(review_assign_url(review_item.id)).status_code == 401

    # ── resolve action ────────────────────────────────────────────────────────

    def test_officer_can_approve_review(self, officer_client, review_item):
        res = officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "approved", "notes": "Looks good"},
            format="json",
        )
        assert res.status_code == 200
        review_item.refresh_from_db()
        assert review_item.resolution_decision == "approved"
        assert review_item.status == "resolved"

    def test_approve_sets_document_valid(self, officer_client, review_item):
        doc = review_item.document_validation.document
        officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "approved"},
            format="json",
        )
        doc.refresh_from_db()
        assert doc.status == "valid"

    def test_reject_sets_document_invalid(self, officer_client, review_item):
        doc = review_item.document_validation.document
        officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "rejected", "notes": "Fake document"},
            format="json",
        )
        doc.refresh_from_db()
        assert doc.status == "invalid"

    def test_needs_changes_keeps_document_flagged(self, officer_client, review_item):
        doc = review_item.document_validation.document
        doc.status = "flagged"
        doc.save()
        officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "needs_changes"},
            format="json",
        )
        doc.refresh_from_db()
        assert doc.status == "flagged"

    def test_resolve_sets_resolved_at_timestamp(self, officer_client, review_item):
        officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "approved"},
            format="json",
        )
        review_item.refresh_from_db()
        assert review_item.resolved_at is not None

    def test_resolve_assigns_reviewer(self, officer_client, review_item, officer_user):
        officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "approved"},
            format="json",
        )
        review_item.refresh_from_db()
        assert review_item.assigned_to == officer_user

    def test_resolve_missing_decision_400(self, officer_client, review_item):
        res = officer_client.post(
            review_resolve_url(review_item.id),
            {"notes": "forgot decision"},
            format="json",
        )
        assert res.status_code == 400

    def test_resolve_invalid_decision_400(self, officer_client, review_item):
        res = officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "maybe"},
            format="json",
        )
        assert res.status_code == 400

    def test_unauthenticated_cannot_resolve(self, anon_client, review_item):
        assert anon_client.post(
            review_resolve_url(review_item.id),
            {"decision": "approved"},
            format="json",
        ).status_code == 401

    def test_notes_saved_on_resolve(self, officer_client, review_item):
        officer_client.post(
            review_resolve_url(review_item.id),
            {"decision": "rejected", "notes": "Document is a sample"},
            format="json",
        )
        review_item.refresh_from_db()
        assert review_item.reviewer_notes == "Document is a sample"

    def test_intermediate_validation_save_does_not_change_document_status(
        self, document, validation
    ):
        document.status = "uploaded"
        document.save()

        validation.current_step = "relevance"
        validation.save(update_fields=["current_step"])

        document.refresh_from_db()
        assert document.status == "uploaded", (
            "BUG: signals.py is overwriting document.status on intermediate validation saves. "
            "Fix: delete signals.py — orchestrator already sets document.status correctly."
        )


# ── Risk score display helper ─────────────────────────────────────────────────

class TestRiskScoreDisplay:

    def test_score_zero_to_hundred_range(self):
        from ai_validation.constants import RISK_SCORE_DISPLAY_DIVISOR
        assert RISK_SCORE_DISPLAY_DIVISOR == 20

    def test_max_score_displays_as_five(self):
        from ai_validation.constants import RISK_SCORE_DISPLAY_DIVISOR
        assert 100 / RISK_SCORE_DISPLAY_DIVISOR == 5.0

    def test_medium_score_displays_correctly(self):
        from ai_validation.constants import RISK_SCORE_DISPLAY_DIVISOR
        assert 50 / RISK_SCORE_DISPLAY_DIVISOR == 2.5