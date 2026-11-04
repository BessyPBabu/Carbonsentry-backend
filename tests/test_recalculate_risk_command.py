import uuid
import pytest
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone

from accounts.models import Organization
from accounts.utils.email_verification import generate_verification_token, hash_token
from vendors.models import Document, DocumentType, Industry, Vendor
from ai_validation.models import (
    DocumentValidation, ExtractedMetadata, IndustryEmissionThreshold, VendorRiskProfile,
)
from ai_validation.services.risk_calculator import RiskCalculator


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


def seed_vendor(org, industry, email, emissions):
    vendor = Vendor.objects.create(
        organization=org, name=f"Vendor {email}", industry=industry,
        country="India", contact_email=email,
    )
    doc_type, _ = DocumentType.objects.get_or_create(name="Emission Report")
    document = Document.objects.create(vendor=vendor, document_type=doc_type, status="valid")
    validation = DocumentValidation.objects.create(
        document=document, status="completed",
        started_at=timezone.now(), completed_at=timezone.now(),
        overall_confidence=Decimal("80"), requires_manual_review=False,
    )
    ExtractedMetadata.objects.create(
        document_validation=validation, document=document,
        co2_value=Decimal(emissions), co2_unit="tonnes",
        co2_extraction_confidence=Decimal("90"),
    )
    return vendor


def seed_inconsistent_profile(vendor):
    VendorRiskProfile.objects.create(
        vendor=vendor, organization=vendor.organization,
        risk_level="low", risk_score=Decimal("10.00"),
    )
    Vendor.objects.filter(pk=vendor.pk).update(risk_level="low")


def run(*args):
    out = StringIO()
    call_command("recalculate_risk", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def stale_vendor(verified_org, industry, threshold):
    vendor = seed_vendor(verified_org, industry, "stale@vendor.com", "15000")
    seed_inconsistent_profile(vendor)
    return vendor


@pytest.mark.django_db
class TestRecalculateRiskCommand:

    def test_dry_run_saves_nothing(self, stale_vendor):
        output = run("--dry-run")

        assert "processed=1 would change=1 failed=0" in output
        profile = VendorRiskProfile.objects.get(vendor=stale_vendor)
        assert profile.risk_level == "low"
        assert profile.risk_score == Decimal("10.00")
        stale_vendor.refresh_from_db()
        assert stale_vendor.risk_level == "low"

    def test_dry_run_does_not_create_missing_profile(self, verified_org, industry, threshold):
        vendor = seed_vendor(verified_org, industry, "noprofile@vendor.com", "15000")
        output = run("--dry-run")

        assert "would change=1" in output
        assert not VendorRiskProfile.objects.filter(vendor=vendor).exists()

    def test_real_run_reconciles_inconsistent_profile(self, stale_vendor):
        output = run()

        assert "processed=1 changed=1 failed=0" in output
        profile = VendorRiskProfile.objects.get(vendor=stale_vendor)
        stale_vendor.refresh_from_db()
        assert profile.risk_level == "critical"
        assert profile.risk_score == Decimal("80.00")
        assert stale_vendor.risk_level == profile.risk_level

    def test_second_run_changes_nothing(self, stale_vendor):
        run()
        output = run()
        assert "processed=1 changed=0 failed=0" in output

    def test_vendor_profile_level_mismatch_is_repaired(self, verified_org, industry, threshold):
        vendor = seed_vendor(verified_org, industry, "mismatch@vendor.com", "100")
        RiskCalculator().calculate(vendor)
        Vendor.objects.filter(pk=vendor.pk).update(risk_level="critical")

        assert "changed=1" in run()
        vendor.refresh_from_db()
        assert vendor.risk_level == VendorRiskProfile.objects.get(vendor=vendor).risk_level

    def test_org_filter_limits_scope(self, stale_vendor, industry):
        other_org = Organization.objects.create(
            name="Other Corp", industry="Tech", country="India",
            primary_email="other@other.com", is_verified=True,
            email_verification_token=hash_token(generate_verification_token()),
        )
        other_vendor = seed_vendor(other_org, industry, "other@vendor.com", "15000")
        seed_inconsistent_profile(other_vendor)

        output = run("--org", str(stale_vendor.organization_id))

        assert "processed=1 changed=1 failed=0" in output
        assert VendorRiskProfile.objects.get(vendor=other_vendor).risk_level == "low"
        assert VendorRiskProfile.objects.get(vendor=stale_vendor).risk_level == "critical"

    def test_unknown_org_processes_nothing(self, stale_vendor):
        output = run("--org", str(uuid.uuid4()))
        assert "processed=0 changed=0 failed=0" in output

    def test_failure_is_skipped_and_counted(self, stale_vendor, verified_org, industry):
        good = seed_vendor(verified_org, industry, "good@vendor.com", "15000")
        seed_inconsistent_profile(good)

        real = RiskCalculator.calculate

        def flaky(self, vendor):
            if vendor.id == stale_vendor.id:
                raise RuntimeError("boom")
            return real(self, vendor)

        with patch(
            "vendors.management.commands.recalculate_risk.RiskCalculator.calculate", flaky
        ):
            output = run()

        assert "processed=1 changed=1 failed=1" in output
        assert VendorRiskProfile.objects.get(vendor=good).risk_level == "critical"
        assert VendorRiskProfile.objects.get(vendor=stale_vendor).risk_level == "low"

    def test_small_batch_size_processes_all_vendors(self, stale_vendor, verified_org, industry):
        other = seed_vendor(verified_org, industry, "second@vendor.com", "15000")
        seed_inconsistent_profile(other)

        assert "processed=2 changed=2 failed=0" in run("--batch-size", "1")
