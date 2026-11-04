import typing

import pytest

from reports.compliance_engine import generate_vendor_compliance_report
from reports.schema import VendorComplianceReport
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
