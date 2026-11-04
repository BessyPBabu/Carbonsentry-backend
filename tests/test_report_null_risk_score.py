import pytest
from decimal import Decimal

from vendors.models import Industry, Vendor
from ai_validation.models import VendorRiskProfile
from reports.models import Report
from reports.services import PDFExporter, ReportGenerator, _format_score


@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology")


@pytest.fixture
def scored_vendor(verified_org, industry):
    vendor = Vendor.objects.create(
        organization=verified_org, name="Scored Vendor", industry=industry,
        country="India", contact_email="scored@vendor.com",
    )
    VendorRiskProfile.objects.create(
        vendor=vendor, organization=verified_org,
        risk_level="medium", risk_score=Decimal("50.00"),
    )
    return vendor


@pytest.fixture
def unknown_vendor(verified_org, industry):
    vendor = Vendor.objects.create(
        organization=verified_org, name="Unassessed Vendor", industry=industry,
        country="India", contact_email="unassessed@vendor.com",
    )
    VendorRiskProfile.objects.create(
        vendor=vendor, organization=verified_org,
        risk_level="unknown", risk_score=None,
    )
    return vendor


@pytest.fixture
def vendor_without_profile(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="No Profile Vendor", industry=industry,
        country="India", contact_email="noprofile@vendor.com",
    )


def make_report(org, user, report_type, data, vendor=None):
    return Report.objects.create(
        organization=org, report_type=report_type, title="Null score",
        vendor=vendor, generated_by=user, status="generated", data=data,
    )


@pytest.mark.django_db
class TestFormatScore:

    def test_none_is_na(self):
        assert _format_score(None) == "N/A"

    def test_zero_is_not_na(self):
        assert _format_score(0) == "0.0 / 100"

    def test_value_formatted(self):
        assert _format_score(45) == "45.0 / 100"


@pytest.mark.django_db
class TestNullRiskScoreReports:

    def test_compliance_report_for_unknown_vendor(
        self, verified_org, officer_user, scored_vendor, unknown_vendor
    ):
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org, vendor=unknown_vendor,
        )
        assert data["scope_emissions"]["risk_score"] is None
        assert data["scope_emissions"]["risk_level"] == "unknown"

        report = make_report(
            verified_org, officer_user, "vendor_compliance_report", data, unknown_vendor
        )
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_compliance_report_for_vendor_without_profile(
        self, verified_org, officer_user, vendor_without_profile
    ):
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org, vendor=vendor_without_profile,
        )
        assert data["scope_emissions"]["risk_score"] is None
        report = make_report(
            verified_org, officer_user, "vendor_compliance_report", data, vendor_without_profile
        )
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_scored_vendor_still_reports_numeric_score(self, verified_org, scored_vendor):
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org, vendor=scored_vendor,
        )
        assert data["scope_emissions"]["risk_score"] == 50.0

    def test_zero_score_is_preserved(self, verified_org, scored_vendor):
        VendorRiskProfile.objects.filter(vendor=scored_vendor).update(risk_score=Decimal("0"))
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org, vendor=scored_vendor,
        )
        assert data["scope_emissions"]["risk_score"] == 0.0

    def test_vendor_risk_report_for_unknown_vendor(
        self, verified_org, officer_user, unknown_vendor
    ):
        data = ReportGenerator().generate(
            report_type="vendor_risk", organization=verified_org, vendor=unknown_vendor,
        )
        assert data["risk_summary"]["overall_score"] is None
        report = make_report(verified_org, officer_user, "vendor_risk", data, unknown_vendor)
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_org_wide_reports_with_unknown_vendor(
        self, verified_org, officer_user, scored_vendor, unknown_vendor
    ):
        for report_type in ("compliance_summary", "emissions_overview"):
            data = ReportGenerator().generate(report_type=report_type, organization=verified_org)
            report = make_report(verified_org, officer_user, report_type, data)
            assert PDFExporter().export(report).startswith(b"%PDF")

    def test_download_pdf_endpoint_with_unknown_vendor(
        self, officer_client, verified_org, officer_user, unknown_vendor
    ):
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org, vendor=unknown_vendor,
        )
        report = make_report(
            verified_org, officer_user, "vendor_compliance_report", data, unknown_vendor
        )
        res = officer_client.get(f"/api/reports/{report.id}/download_pdf/")
        assert res.status_code == 200
        assert res["Content-Type"] == "application/pdf"
