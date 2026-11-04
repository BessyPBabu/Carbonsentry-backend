import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone

from accounts.models import Organization
from accounts.utils.email_verification import generate_verification_token, hash_token
from vendors.models import Document, DocumentType, Industry, Vendor
from ai_validation.models import (
    DocumentValidation, ExtractedMetadata, IndustryEmissionThreshold, VendorRiskProfile,
)
from ai_validation.services.risk_calculator import RiskCalculator

RISK_URL = "/api/ai-validation/risk-profiles/"
HIGH_RISK_URL = "/api/ai-validation/risk-profiles/high_risk/"
DASH_STATS_URL = "/api/ai-validation/risk-profiles/dashboard_stats/"
VENDOR_LIST_URL = "/api/vendors/"


def vendor_detail_url(vid):
    return f"/api/vendors/{vid}/"


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
        organization=verified_org, name="Consistency Vendor",
        industry=industry, country="India", contact_email="consistency@vendor.com",
    )


@pytest.fixture
def scored_vendor(vendor, threshold):
    doc_type = DocumentType.objects.create(name="Emission Report")
    document = Document.objects.create(vendor=vendor, document_type=doc_type, status="valid")
    validation = DocumentValidation.objects.create(
        document=document, status="completed",
        started_at=timezone.now(), completed_at=timezone.now(),
        overall_confidence=Decimal("80"), requires_manual_review=False,
    )
    ExtractedMetadata.objects.create(
        document_validation=validation, document=document,
        co2_value=Decimal("100"), co2_unit="tonnes",
        co2_extraction_confidence=Decimal("90"),
    )
    return vendor


@pytest.fixture
def other_org_profile(industry):
    org = Organization.objects.create(
        name="Other Corp", industry="Tech", country="India",
        primary_email="other@other.com", is_verified=True,
        email_verification_token=hash_token(generate_verification_token()),
    )
    other_vendor = Vendor.objects.create(
        organization=org, name="Other Vendor", industry=industry,
        country="USA", contact_email="other@vendor.com",
    )
    return VendorRiskProfile.objects.create(
        vendor=other_vendor, organization=org,
        risk_level="critical", risk_score=Decimal("90"),
    )


def counts(data):
    return {k: data[k] for k in ("low_risk", "medium_risk", "high_risk", "critical_risk", "unknown_risk")}


@pytest.mark.django_db
class TestReconcile:

    @pytest.mark.parametrize("level,score,expected_level,expected_score", [
        ("critical", 35, "critical", 80),
        ("low", 85, "critical", 85),
        ("medium", 50, "medium", 50),
        ("high", 59, "high", 60),
        ("low", 39, "low", 39),
        ("low", 40, "medium", 40),
        ("medium", 59, "medium", 59),
        ("low", 60, "high", 60),
        ("high", 79, "high", 79),
        ("low", 80, "critical", 80),
        ("medium", 100, "critical", 100),
        ("low", 0, "low", 0),
    ])
    def test_reconcile(self, level, score, expected_level, expected_score):
        final_level, final_score = RiskCalculator()._reconcile(level, float(score))
        assert final_level == expected_level
        assert final_score == expected_score

    def test_score_never_exceeds_100(self):
        _, final_score = RiskCalculator()._reconcile("critical", 120.0)
        assert final_score == 100

    def test_unknown_has_no_score(self, vendor, threshold):
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level == "unknown"
        assert profile.risk_score is None

    def test_critical_emissions_raise_score_to_band_floor(self, vendor, threshold):
        doc_type = DocumentType.objects.create(name="Emission Report")
        document = Document.objects.create(vendor=vendor, document_type=doc_type, status="valid")
        validation = DocumentValidation.objects.create(
            document=document, status="completed",
            started_at=timezone.now(), completed_at=timezone.now(),
            overall_confidence=Decimal("80"), requires_manual_review=False,
        )
        ExtractedMetadata.objects.create(
            document_validation=validation, document=document,
            co2_value=Decimal("15000"), co2_unit="tonnes",
            co2_extraction_confidence=Decimal("90"),
            expiry_date=date.today() + timedelta(days=365),
        )
        profile = RiskCalculator().calculate(vendor)
        assert profile.risk_level == "critical"
        assert profile.risk_score == Decimal("80")


@pytest.mark.django_db
class TestAtomicSave:

    def test_vendor_save_failure_rolls_back_profile(self, scored_vendor):
        with patch("vendors.models.Vendor.save", side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError):
                RiskCalculator().calculate(scored_vendor)

        profile = VendorRiskProfile.objects.get(vendor=scored_vendor)
        assert profile.risk_level == "unknown"
        assert profile.risk_score is None
        scored_vendor.refresh_from_db()
        assert scored_vendor.risk_level == "unknown"


@pytest.mark.django_db
class TestLevelConsistentAcrossEndpoints:

    @pytest.mark.parametrize("score,expected", [
        (39, "low"),
        (40, "medium"),
        (59, "medium"),
        (60, "high"),
        (79, "high"),
        (80, "critical"),
    ])
    def test_all_endpoints_report_same_level(
        self, officer_client, scored_vendor, other_org_profile, score, expected
    ):
        with patch.object(RiskCalculator, "_risk_score", return_value=float(score)):
            profile = RiskCalculator().calculate(scored_vendor)

        assert profile.risk_level == expected

        detail = officer_client.get(f"{RISK_URL}{profile.id}/")
        assert detail.status_code == 200
        assert detail.data["risk_level"] == expected

        vendor_detail = officer_client.get(vendor_detail_url(scored_vendor.id))
        assert vendor_detail.status_code == 200
        assert vendor_detail.data["risk_level"] == expected

        vendor_list = officer_client.get(VENDOR_LIST_URL)
        listed = next(v for v in vendor_list.data["results"] if v["id"] == str(scored_vendor.id))
        assert listed["risk_level"] == expected

        stats = officer_client.get(DASH_STATS_URL)
        assert stats.status_code == 200
        expected_counts = {
            "low_risk": 0, "medium_risk": 0, "high_risk": 0, "critical_risk": 0, "unknown_risk": 0,
        }
        expected_counts[f"{expected}_risk"] = 1
        assert counts(stats.data) == expected_counts
        assert stats.data["total_vendors"] == 1

        high_risk = officer_client.get(HIGH_RISK_URL)
        assert high_risk.status_code == 200
        ids = [p["vendor_id"] for p in high_risk.data]
        if expected in ("high", "critical"):
            assert ids == [str(scored_vendor.id)]
            assert high_risk.data[0]["risk_level"] == expected
        else:
            assert ids == []

    def test_vendor_and_profile_levels_match(self, scored_vendor):
        with patch.object(RiskCalculator, "_risk_score", return_value=85.0):
            profile = RiskCalculator().calculate(scored_vendor)
        scored_vendor.refresh_from_db()
        assert scored_vendor.risk_level == profile.risk_level == "critical"

    def test_dashboard_counts_ignore_risk_level_query_param(self, officer_client, scored_vendor):
        with patch.object(RiskCalculator, "_risk_score", return_value=65.0):
            RiskCalculator().calculate(scored_vendor)

        plain = officer_client.get(DASH_STATS_URL).data
        filtered = officer_client.get(DASH_STATS_URL, {"risk_level": "low"}).data
        assert plain == filtered
        assert plain["high_risk"] == 1

    def test_unknown_vendor_counted_as_unknown(self, officer_client, vendor, threshold):
        RiskCalculator().calculate(vendor)
        stats = officer_client.get(DASH_STATS_URL).data
        assert stats["unknown_risk"] == 1
        assert stats["total_vendors"] == 1

    def test_other_org_profile_not_visible(self, officer_client, other_org_profile):
        stats = officer_client.get(DASH_STATS_URL).data
        assert stats["total_vendors"] == 0
        assert stats["critical_risk"] == 0
        assert officer_client.get(HIGH_RISK_URL).data == []
        assert officer_client.get(f"{RISK_URL}{other_org_profile.id}/").status_code == 404

    def test_unknown_profile_sorts_after_scored_profiles(self, officer_client, scored_vendor, industry, verified_org):
        with patch.object(RiskCalculator, "_risk_score", return_value=50.0):
            RiskCalculator().calculate(scored_vendor)
        unknown_vendor = Vendor.objects.create(
            organization=verified_org, name="Unassessed", industry=industry,
            country="India", contact_email="unassessed@vendor.com",
        )
        RiskCalculator().calculate(unknown_vendor)

        res = officer_client.get(RISK_URL)
        results = res.data.get("results", res.data)
        assert [p["risk_level"] for p in results] == ["medium", "unknown"]
