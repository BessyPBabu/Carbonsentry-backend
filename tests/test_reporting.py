import uuid
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.utils import timezone

from vendors.models import Vendor, Industry
from reports.models import Report

REPORTS_URL   = "/api/reports/"
GENERATE_URL  = "/api/reports/generate/"


def approve_url(pk):    return f"/api/reports/{pk}/approve/"
def download_url(pk):   return f"/api/reports/{pk}/download_pdf/"
def detail_url(pk):     return f"/api/reports/{pk}/"


@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology")


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Report Vendor",
        industry=industry, country="India", contact_email="report@vendor.com",
    )


@pytest.fixture
def draft_report(verified_org, officer_user):
    return Report.objects.create(
        organization=verified_org,
        report_type="compliance_summary",
        title="Q1 Compliance",
        generated_by=officer_user,
        status="draft",
    )


@pytest.fixture
def generated_report(verified_org, officer_user):
    return Report.objects.create(
        organization=verified_org,
        report_type="compliance_summary",
        title="Q1 Compliance",
        generated_by=officer_user,
        status="generated",
        data={"summary": {"total_vendors": 5}},
    )


@pytest.fixture
def approved_report(verified_org, officer_user, admin_user):
    return Report.objects.create(
        organization=verified_org,
        report_type="compliance_summary",
        title="Q1 Compliance",
        generated_by=officer_user,
        status="approved",
        data={"summary": {"total_vendors": 5}},
        approved_by=admin_user,
        approved_at=timezone.now(),
    )


@pytest.mark.django_db
class TestReportModel:

    def test_str_includes_title_and_status(self, draft_report):
        s = str(draft_report)
        assert "Q1 Compliance" in s
        assert "draft" in s

    def test_uuid_pk(self, draft_report):
        assert isinstance(draft_report.id, uuid.UUID)

    def test_default_status_draft(self, verified_org, officer_user):
        r = Report.objects.create(
            organization=verified_org,
            report_type="compliance_summary",
            title="Test",
            generated_by=officer_user,
        )
        assert r.status == "draft"

    def test_data_stored_as_json(self, generated_report):
        assert generated_report.data["summary"]["total_vendors"] == 5

    def test_org_cascade_delete(self, verified_org, officer_user):
        Report.objects.create(
            organization=verified_org,
            report_type="compliance_summary",
            title="Test",
            generated_by=officer_user,
        )
        org_id = verified_org.id
        verified_org.delete()
        assert Report.objects.filter(organization_id=org_id).count() == 0


@pytest.mark.django_db
class TestReportViewSet:

    def test_officer_can_list_reports(self, officer_client, generated_report):
        res = officer_client.get(REPORTS_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(REPORTS_URL).status_code == 401

    def test_viewer_can_list_reports(self, viewer_client, approved_report):
        res = viewer_client.get(REPORTS_URL)
        assert res.status_code == 200

    def test_pagination_structure(self, officer_client, generated_report):
        res = officer_client.get(REPORTS_URL)
        assert "count" in res.data
        assert "results" in res.data

    def test_org_isolation(self, officer_client, verified_org):
        from accounts.utils.email_verification import generate_verification_token, hash_token
        from accounts.models import Organization
        other_org = Organization.objects.create(
            name="Foreign Corp", industry="Tech", country="India",
            primary_email="foreign@f.com", is_verified=True,
            email_verification_token=hash_token(generate_verification_token()),
        )
        Report.objects.create(
            organization=other_org,
            report_type="compliance_summary",
            title="Foreign Report",
            status="generated",
        )
        res = officer_client.get(REPORTS_URL)
        titles = [r["title"] for r in res.data.get("results", [])]
        assert "Foreign Report" not in titles

    def test_viewer_only_sees_approved_reports(self, viewer_client, draft_report, approved_report):
        res = viewer_client.get(REPORTS_URL)
        results = res.data.get("results", [])
        for r in results:
            assert r["status"] == "approved"

    def test_filter_by_report_type(self, officer_client, generated_report):
        res = officer_client.get(REPORTS_URL, {"report_type": "compliance_summary"})
        results = res.data.get("results", [])
        assert all(r["report_type"] == "compliance_summary" for r in results)

    def test_filter_by_status(self, officer_client, generated_report, draft_report):
        res = officer_client.get(REPORTS_URL, {"status": "generated"})
        results = res.data.get("results", [])
        assert all(r["status"] == "generated" for r in results)

    def test_filter_by_vendor(self, officer_client, vendor, verified_org, officer_user):
        Report.objects.create(
            organization=verified_org,
            report_type="vendor_risk",
            title="Vendor Report",
            vendor=vendor,
            status="generated",
            data={},
            generated_by=officer_user,
        )
        res = officer_client.get(REPORTS_URL, {"vendor": str(vendor.id)})
        results = res.data.get("results", [])
        assert len(results) >= 1
        assert all(str(r.get("vendor")) == str(vendor.id) for r in results)

    def test_cannot_delete_approved_report(self, officer_client, approved_report):
        res = officer_client.delete(detail_url(approved_report.id))
        assert res.status_code == 403

    def test_can_delete_draft_report(self, officer_client, draft_report):
        res = officer_client.delete(detail_url(draft_report.id))
        assert res.status_code == 204

    def test_viewer_cannot_delete_draft_report(self, viewer_client, draft_report):
        res = viewer_client.delete(detail_url(draft_report.id))
        assert res.status_code == 403
        assert Report.objects.filter(id=draft_report.id).exists()

    def test_viewer_cannot_update_report(self, viewer_client, approved_report):
        res = viewer_client.patch(detail_url(approved_report.id), {"title": "Hacked"}, format="json")
        assert res.status_code == 403

    def test_officer_can_update_draft_report(self, officer_client, draft_report):
        res = officer_client.patch(detail_url(draft_report.id), {"title": "Updated Title"}, format="json")
        assert res.status_code == 200
        draft_report.refresh_from_db()
        assert draft_report.title == "Updated Title"

    def test_generated_by_name_in_response(self, officer_client, generated_report, officer_user):
        officer_user.full_name = "Officer User"
        officer_user.save()
        res = officer_client.get(REPORTS_URL)
        results = res.data.get("results", [])
        assert len(results) >= 1
        assert results[0]["generated_by_name"] is not None


@pytest.mark.django_db
class TestGenerateReport:

    @patch("reports.views.ReportGenerator")
    def test_officer_can_generate_compliance_summary(self, MockGen, officer_client):
        MockGen.return_value.generate.return_value = {"summary": {}}
        res = officer_client.post(GENERATE_URL, {
            "report_type": "compliance_summary",
            "title": "Test Report",
        }, format="json")
        assert res.status_code == 201
        assert Report.objects.filter(title="Test Report").exists()

    @patch("reports.views.ReportGenerator")
    def test_admin_can_generate_report(self, MockGen, admin_client):
        MockGen.return_value.generate.return_value = {"summary": {}}
        res = admin_client.post(GENERATE_URL, {
            "report_type": "compliance_summary",
            "title": "Admin Report",
        }, format="json")
        assert res.status_code == 201

    @patch("reports.views.ReportGenerator")
    def test_generated_report_has_status_generated(self, MockGen, officer_client):
        MockGen.return_value.generate.return_value = {"summary": {}}
        officer_client.post(GENERATE_URL, {
            "report_type": "compliance_summary",
            "title": "Status Test",
        }, format="json")
        report = Report.objects.get(title="Status Test")
        assert report.status == "generated"

    def test_viewer_cannot_generate(self, viewer_client):
        res = viewer_client.post(GENERATE_URL, {
            "report_type": "compliance_summary",
            "title": "Viewer Report",
        }, format="json")
        assert res.status_code == 403

    def test_missing_title_returns_400(self, officer_client):
        res = officer_client.post(GENERATE_URL, {
            "report_type": "compliance_summary",
        }, format="json")
        assert res.status_code == 400

    def test_vendor_risk_without_vendor_id_returns_400(self, officer_client):
        res = officer_client.post(GENERATE_URL, {
            "report_type": "vendor_risk",
            "title": "Risk Report",
        }, format="json")
        assert res.status_code == 400

    @patch("reports.views.ReportGenerator")
    def test_vendor_risk_with_vendor_id_succeeds(self, MockGen, officer_client, vendor):
        MockGen.return_value.generate.return_value = {"vendor": {}}
        res = officer_client.post(GENERATE_URL, {
            "report_type": "vendor_risk",
            "title": "Risk Report",
            "vendor_id": str(vendor.id),
        }, format="json")
        assert res.status_code == 201

    def test_invalid_report_type_returns_400(self, officer_client):
        res = officer_client.post(GENERATE_URL, {
            "report_type": "fake_type",
            "title": "Invalid",
        }, format="json")
        assert res.status_code == 400

    def test_nonexistent_vendor_id_returns_404(self, officer_client):
        res = officer_client.post(GENERATE_URL, {
            "report_type": "vendor_risk",
            "title": "Risk",
            "vendor_id": str(uuid.uuid4()),
        }, format="json")
        assert res.status_code == 404

    @patch("reports.views.ReportGenerator")
    def test_generation_failure_returns_500_and_keeps_draft(self, MockGen, officer_client):
        MockGen.return_value.generate.side_effect = Exception("AI failed")
        res = officer_client.post(GENERATE_URL, {
            "report_type": "compliance_summary",
            "title": "Failed Report",
        }, format="json")
        assert res.status_code == 500
        report = Report.objects.get(title="Failed Report")
        assert report.status == "draft"

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.post(GENERATE_URL, {
            "report_type": "compliance_summary", "title": "x"
        }, format="json").status_code == 401

   
    @patch("reports.services.generate_vendor_compliance_report")
    def test_vendor_compliance_report_delegates_to_compliance_engine(self, mock_engine, officer_client, vendor):
        mock_engine.return_value = {
            "vendor": {"name": vendor.name},
            "regulatory_applicability": [],
            "emission_verification": {},
            "scope_emissions": {},
            "regulatory_risk_exposure": [],
            "compliance_gap_analysis": [],
            "reduction_roadmap": None,
            "carbon_credit_guidance": None,
            "vendor_retention": {"recommendation": "retain"},
            "action_checklist": [],
        }
        res = officer_client.post(GENERATE_URL, {
            "report_type": "vendor_compliance_report",
            "title": "Compliance Engine Test",
            "vendor_id": str(vendor.id),
        }, format="json")
        assert res.status_code == 201
        mock_engine.assert_called_once()
        report = Report.objects.get(title="Compliance Engine Test")
        assert report.data["vendor_retention"]["recommendation"] == "retain"


@pytest.mark.django_db
class TestApproveReport:

    def test_officer_can_approve_generated_report(self, officer_client, generated_report):
        res = officer_client.patch(approve_url(generated_report.id), {}, format="json")
        assert res.status_code == 200
        generated_report.refresh_from_db()
        assert generated_report.status == "approved"

    def test_admin_can_approve_generated_report(self, admin_client, generated_report):
        res = admin_client.patch(approve_url(generated_report.id), {}, format="json")
        assert res.status_code == 200

    def test_approval_sets_approved_by(self, officer_client, generated_report, officer_user):
        officer_client.patch(approve_url(generated_report.id), {}, format="json")
        generated_report.refresh_from_db()
        assert generated_report.approved_by == officer_user

    def test_approval_sets_approved_at_timestamp(self, officer_client, generated_report):
        officer_client.patch(approve_url(generated_report.id), {}, format="json")
        generated_report.refresh_from_db()
        assert generated_report.approved_at is not None

    def test_approval_notes_saved(self, officer_client, generated_report):
        officer_client.patch(approve_url(generated_report.id), {
            "approval_notes": "Reviewed and approved"
        }, format="json")
        generated_report.refresh_from_db()
        assert generated_report.approval_notes == "Reviewed and approved"

    def test_viewer_cannot_approve(self, viewer_client, generated_report):
        res = viewer_client.patch(approve_url(generated_report.id), {}, format="json")
        assert res.status_code == 403

    def test_cannot_approve_already_approved(self, officer_client, approved_report):
        res = officer_client.patch(approve_url(approved_report.id), {}, format="json")
        assert res.status_code == 400

    def test_cannot_approve_draft(self, officer_client, draft_report):
        res = officer_client.patch(approve_url(draft_report.id), {}, format="json")
        assert res.status_code == 400

    def test_unauthenticated_blocked(self, anon_client, generated_report):
        assert anon_client.patch(approve_url(generated_report.id), {}, format="json").status_code == 401


@pytest.mark.django_db
class TestDownloadPdf:

    @patch("reports.views.PDFExporter")
    def test_officer_can_download_generated_report(self, MockExporter, officer_client, generated_report):
        MockExporter.return_value.export.return_value = b"%PDF-1.4 fake content"
        res = officer_client.get(download_url(generated_report.id))
        assert res.status_code == 200
        assert res["Content-Type"] == "application/pdf"

    @patch("reports.views.PDFExporter")
    def test_response_has_attachment_header(self, MockExporter, officer_client, generated_report):
        MockExporter.return_value.export.return_value = b"%PDF-1.4 fake"
        res = officer_client.get(download_url(generated_report.id))
        assert "attachment" in res["Content-Disposition"]
        assert ".pdf" in res["Content-Disposition"]

    def test_viewer_can_download_approved_report(self, viewer_client, approved_report):
        with patch("reports.views.PDFExporter") as MockExporter:
            MockExporter.return_value.export.return_value = b"%PDF-1.4 fake"
            res = viewer_client.get(download_url(approved_report.id))
        assert res.status_code == 200

    def test_viewer_cannot_download_unapproved_report(self, viewer_client, generated_report):
        res = viewer_client.get(download_url(generated_report.id))
        assert res.status_code in (403, 404)

    def test_cannot_download_draft_report(self, officer_client, draft_report):
        res = officer_client.get(download_url(draft_report.id))
        assert res.status_code == 400

    @patch("reports.views.PDFExporter")
    def test_pdf_generation_failure_returns_500(self, MockExporter, officer_client, generated_report):
        MockExporter.return_value.export.side_effect = RuntimeError("reportlab not installed")
        res = officer_client.get(download_url(generated_report.id))
        assert res.status_code == 500

    def test_unauthenticated_blocked(self, anon_client, generated_report):
        assert anon_client.get(download_url(generated_report.id)).status_code == 401


@pytest.mark.django_db
class TestReportGenerator:

    def test_invalid_report_type_raises(self, verified_org):
        from reports.services import ReportGenerator
        with pytest.raises(ValueError, match="Unknown report type"):
            ReportGenerator().generate(
                report_type="invalid_type",
                organization=verified_org,
            )

    def test_compliance_summary_structure(self, verified_org):
        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="compliance_summary",
            organization=verified_org,
        )
        assert "summary" in data
        assert "vendors" in data
        assert "total_vendors" in data["summary"]

    def test_emissions_overview_structure(self, verified_org):
        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="emissions_overview",
            organization=verified_org,
        )
        assert "summary" in data
        assert "vendor_emissions" in data
        assert "by_industry" in data

    def test_document_audit_structure(self, verified_org):
        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="document_audit",
            organization=verified_org,
        )
        assert "validation_summary" in data
        assert "quality_metrics" in data
        assert "vendor_summaries" in data

    def test_vendor_risk_requires_vendor(self, verified_org):
        from reports.services import ReportGenerator
        with pytest.raises((ValueError, AttributeError)):
            ReportGenerator().generate(
                report_type="vendor_risk",
                organization=verified_org,
                vendor=None,
            )

    def test_vendor_risk_structure(self, verified_org, vendor):
        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="vendor_risk",
            organization=verified_org,
            vendor=vendor,
        )
        assert "vendor" in data
        assert "risk_summary" in data
        assert "emissions" in data
        assert "documents" in data
        assert "recommendations" in data

    def test_vendor_compliance_report_requires_vendor(self, verified_org):
        from reports.services import ReportGenerator
        with pytest.raises(ValueError, match="vendor is required"):
            ReportGenerator().generate(
                report_type="vendor_compliance_report",
                organization=verified_org,
                vendor=None,
            )

    def test_vendor_compliance_report_uses_compliance_engine(self, verified_org, vendor):
        from reports.services import ReportGenerator
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org,
            vendor=vendor,
        )
        assert "vendor" in data
        assert "regulatory_applicability" in data
        assert "emission_verification" in data
        assert "scope_emissions" in data
        assert "vendor_retention" in data
        assert "action_checklist" in data
        assert "recommendation" in data["vendor_retention"]

    def test_vendor_compliance_report_has_reduction_fields_for_high_emissions(self, verified_org, vendor):
        from reports.services import ReportGenerator
        from ai_validation.models import VendorRiskProfile
        VendorRiskProfile.objects.create(
            vendor=vendor,
            organization=verified_org,
            risk_level="critical",
            risk_score=Decimal("90"),
            total_co2_emissions=Decimal("50000"),
            exceeds_threshold=True,
        )
        data = ReportGenerator().generate(
            report_type="vendor_compliance_report",
            organization=verified_org,
            vendor=vendor,
        )
        assert data["reduction_roadmap"] is not None
        assert data["carbon_credit_guidance"] is not None
        assert len(data["reduction_roadmap"]["strategies"]) > 0

    def test_no_decimal_values_in_output(self, verified_org):
        from reports.services import ReportGenerator
        from decimal import Decimal

        data = ReportGenerator().generate(
            report_type="compliance_summary",
            organization=verified_org,
        )

        def check_no_decimals(obj):
            if isinstance(obj, Decimal):
                pytest.fail(f"Decimal found in report output: {obj}")
            if isinstance(obj, dict):
                for v in obj.values():
                    check_no_decimals(v)
            if isinstance(obj, list):
                for item in obj:
                    check_no_decimals(item)

        check_no_decimals(data)

    def test_date_filtering(self, verified_org):
        from reports.services import ReportGenerator
        import datetime
        data = ReportGenerator().generate(
            report_type="document_audit",
            organization=verified_org,
            date_from=datetime.date(2024, 1, 1),
            date_to=datetime.date(2024, 12, 31),
        )
        assert "validation_summary" in data


@pytest.mark.django_db
class TestPDFExporterEscaping:

    def test_ampersand_in_title_does_not_crash_export(self, verified_org, officer_user):
        from reports.services import PDFExporter
        report = Report.objects.create(
            organization=verified_org,
            report_type="compliance_summary",
            title="Smith & Sons — Q1 Compliance",
            generated_by=officer_user,
            status="generated",
            data={"summary": {"total_vendors": 1}, "vendors": []},
        )
        pdf_bytes = PDFExporter().export(report)
        assert pdf_bytes.startswith(b"%PDF")

    def test_angle_brackets_in_title_do_not_crash_export(self, verified_org, officer_user):
        from reports.services import PDFExporter
        report = Report.objects.create(
            organization=verified_org,
            report_type="compliance_summary",
            title="<Test> Report",
            generated_by=officer_user,
            status="generated",
            data={"summary": {"total_vendors": 1}, "vendors": []},
        )
        pdf_bytes = PDFExporter().export(report)
        assert pdf_bytes.startswith(b"%PDF")

    def test_ampersand_in_generator_name_does_not_crash_export(self, verified_org):
        from reports.services import PDFExporter
        from accounts.models import User
        user = User.objects.create_user(
            email="ampersand@test.com", password="Test@1234",
            role=User.Role.OFFICER, organization=verified_org,
            is_active=True, full_name="A & B Officer",
        )
        report = Report.objects.create(
            organization=verified_org,
            report_type="compliance_summary",
            title="Normal Title",
            generated_by=user,
            status="generated",
            data={"summary": {"total_vendors": 1}, "vendors": []},
        )
        pdf_bytes = PDFExporter().export(report)
        assert pdf_bytes.startswith(b"%PDF")

    def test_vendor_compliance_pdf_renders_new_sections(self, verified_org, vendor, officer_user):
        from reports.services import PDFExporter
        report = Report.objects.create(
            organization=verified_org,
            report_type="vendor_compliance_report",
            title="Full Compliance Report",
            vendor=vendor,
            generated_by=officer_user,
            status="generated",
            data={
                "vendor": {"name": vendor.name, "industry": "Manufacturing", "country": "India",
                           "compliance_status": "pending", "risk_level": "critical"},
                "regulatory_applicability": [],
                "emission_verification": {"total_documents": 0, "valid_documents": 0,
                                           "flagged_documents": 0, "invalid_documents": 0,
                                           "expired_documents": 0, "average_ai_confidence": 0,
                                           "reasonable_assurance_met": False, "document_details": []},
                "scope_emissions": {"total_co2_tonnes": 50000, "risk_score": 90, "exceeds_threshold": True},
                "regulatory_risk_exposure": [],
                "compliance_gap_analysis": [],
                "recommendations": ["Test recommendation"],
                "reduction_roadmap": {
                    "current_emissions_tco2e": 50000, "target_emissions_tco2e": 750,
                    "reduction_needed_tco2e": 49250, "reduction_needed_pct": 98.5,
                    "strategies": [{"strategy": "Switch to renewable energy",
                                    "typical_reduction_pct": 40, "timeframe": "6-18 months",
                                    "cost_level": "medium"}],
                },
                "carbon_credit_guidance": {
                    "credits_needed_tco2e": 50000,
                    "estimated_cost_usd_low": 250000, "estimated_cost_usd_high": 2500000,
                    "estimated_cost_inr_low": 20000000, "estimated_cost_inr_high": 200000000,
                },
                "vendor_retention": {
                    "recommendation": "review_for_replacement",
                    "reason": "Critical emissions", "confidence_level": "high",
                    "review_date": "Immediate",
                },
                "action_checklist": [
                    {"priority": 1, "action": "Escalate to senior management",
                     "owner": "Senior Management", "urgency": "critical"},
                ],
            },
        )
        pdf_bytes = PDFExporter().export(report)
        assert pdf_bytes.startswith(b"%PDF")