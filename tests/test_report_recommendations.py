import typing

import pytest

from reports.compliance_engine import generate_vendor_compliance_report
from reports.models import Report
from reports.schema import VendorComplianceReport, resolve_recommendations
from reports.serializers import ReportSerializer
from reports.services import PDFExporter
from vendors.models import Industry, Vendor


@pytest.fixture
def vendor(verified_org):
    industry = Industry.objects.create(name="Technology")
    return Vendor.objects.create(
        organization=verified_org, name="Rec Vendor", industry=industry,
        country="Germany", contact_email="rec@vendor.com",
    )


@pytest.mark.django_db
class TestEngineRecommendations:

    def test_new_report_has_recommendations_matching_checklist_actions(self, verified_org, vendor):
        data = generate_vendor_compliance_report(vendor, verified_org)
        assert isinstance(data["recommendations"], list)
        assert data["recommendations"]
        assert all(isinstance(r, str) for r in data["recommendations"])
        assert data["recommendations"] == [a["action"] for a in data["action_checklist"]]

    def test_action_checklist_keeps_structured_shape(self, verified_org, vendor):
        data = generate_vendor_compliance_report(vendor, verified_org)
        for item in data["action_checklist"]:
            assert set(item) == {"priority", "action", "owner", "urgency"}

    def test_engine_keys_equal_schema_keys(self, verified_org, vendor):
        data = generate_vendor_compliance_report(vendor, verified_org)
        assert set(data) == set(typing.get_type_hints(VendorComplianceReport))

    def test_nested_engine_keys_equal_schema_keys(self, verified_org, vendor):
        from reports.schema import SECTION_TYPES

        data = generate_vendor_compliance_report(vendor, verified_org)
        for section in ("vendor", "emission_verification", "scope_emissions", "vendor_retention"):
            assert set(data[section]) == set(typing.get_type_hints(SECTION_TYPES[section])), section
        for section in ("regulatory_applicability", "regulatory_risk_exposure", "compliance_gap_analysis"):
            for item in data[section]:
                assert set(item) == set(typing.get_type_hints(SECTION_TYPES[section])), section


def _texts(elements):
    return [e.text for e in elements if hasattr(e, "text")]


def _report(org, user, vendor, data, status="generated"):
    return Report.objects.create(
        organization=org, report_type="vendor_compliance_report", title="Rec",
        vendor=vendor, generated_by=user, status=status, data=data,
    )


OLD_CHECKLIST = [
    {"priority": 1, "action": "Request certificate renewal for 1 expired document(s)",
     "owner": "Compliance Officer", "urgency": "high"},
    {"priority": 2, "action": "Escalate to senior management", "owner": "Senior Management", "urgency": "critical"},
]


@pytest.mark.django_db
class TestRecommendationsRendering:

    def test_new_report_recommendations_in_data_and_pdf_content(self, verified_org, officer_user, vendor):
        data = generate_vendor_compliance_report(vendor, verified_org)
        report = _report(verified_org, officer_user, vendor, data)
        texts = _texts(PDFExporter()._build_body(report))
        assert "Recommendations" in texts
        assert any(data["recommendations"][0] in t for t in texts)
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_old_report_with_only_action_checklist_still_renders(self, verified_org, officer_user, vendor):
        report = _report(verified_org, officer_user, vendor, {"vendor": {"name": "V"}, "action_checklist": OLD_CHECKLIST})
        texts = _texts(PDFExporter()._build_body(report))
        assert any("Request certificate renewal" in t for t in texts)
        assert not any("{'priority'" in t for t in texts)
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_report_with_neither_key_does_not_crash(self, verified_org, officer_user, vendor):
        report = _report(verified_org, officer_user, vendor, {"vendor": {"name": "V"}})
        texts = _texts(PDFExporter()._build_body(report))
        assert any("No recommendations" in t for t in texts)
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_serializer_falls_back_without_touching_stored_data(self, verified_org, officer_user, vendor):
        stored = {"vendor": {"name": "V"}, "action_checklist": OLD_CHECKLIST}
        report = _report(verified_org, officer_user, vendor, stored, status="approved")
        out = ReportSerializer(report).data
        assert out["data"]["recommendations"] == [i["action"] for i in OLD_CHECKLIST]
        assert "recommendations" not in report.data
        report.refresh_from_db()
        assert report.data == {"vendor": {"name": "V"}, "action_checklist": OLD_CHECKLIST}
        assert report.status == "approved"

    def test_serializer_leaves_data_alone_when_nothing_to_resolve(self, verified_org, officer_user, vendor):
        report = _report(verified_org, officer_user, vendor, {"vendor": {"name": "V"}})
        assert ReportSerializer(report).data["data"] == {"vendor": {"name": "V"}}

    def test_serializer_keeps_existing_recommendations(self, verified_org, officer_user, vendor):
        report = _report(verified_org, officer_user, vendor, {"recommendations": ["Keep me"], "action_checklist": OLD_CHECKLIST})
        assert ReportSerializer(report).data["data"]["recommendations"] == ["Keep me"]


class TestResolveRecommendations:

    @pytest.mark.parametrize("data", [
        None, [], "x", {}, {"action_checklist": None}, {"action_checklist": "bad"},
        {"action_checklist": [None, 3, {"action": None}, {"action": " "}, {"owner": "x"}]},
        {"recommendations": None, "action_checklist": []},
    ])
    def test_malformed_input_yields_empty_list(self, data):
        assert resolve_recommendations(data) == []

    def test_prefers_recommendations(self):
        assert resolve_recommendations({"recommendations": ["a"], "action_checklist": OLD_CHECKLIST}) == ["a"]

    def test_falls_back_to_checklist_actions(self):
        assert resolve_recommendations({"action_checklist": OLD_CHECKLIST}) == [i["action"] for i in OLD_CHECKLIST]
