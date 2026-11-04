import pytest

from reports.compliance_engine import _is_indian, generate_vendor_compliance_report
from vendors.models import Industry, Vendor


@pytest.mark.parametrize("country", ["India", " INDIA ", "IN", "ind", "Bharat", "Republic of India"])
def test_indian_names_match(country):
    assert _is_indian(country) is True


@pytest.mark.parametrize("country", ["Finland", "Spain", "China", "Indonesia", "Singapore", "", "  ", None])
def test_other_countries_do_not_match(country):
    assert _is_indian(country) is False


@pytest.fixture
def make_vendor(verified_org):
    industry = Industry.objects.create(name="Technology")

    def _make(country):
        return Vendor.objects.create(
            organization=verified_org, name=f"V {country}", industry=industry,
            country=country, contact_email="v@vendor.com",
        )
    return _make


def _regulations(data):
    return (
        {r["regulation"] for r in data["regulatory_risk_exposure"]},
        {r["regulation"] for r in data["regulatory_applicability"]},
    )


@pytest.mark.django_db
class TestNgtOnlyForIndianVendors:

    @pytest.mark.parametrize("country", ["India", " INDIA ", "IN"])
    def test_indian_vendor_gets_ngt(self, verified_org, make_vendor, country):
        exposure, applicability = _regulations(generate_vendor_compliance_report(make_vendor(country), verified_org))
        assert "NGT India" in exposure
        assert any(r.startswith("NGT") for r in applicability)

    @pytest.mark.parametrize("country", ["Finland", "Spain", "China", "Indonesia"])
    def test_other_vendor_has_no_ngt(self, verified_org, make_vendor, country):
        exposure, applicability = _regulations(generate_vendor_compliance_report(make_vendor(country), verified_org))
        assert "NGT India" not in exposure
        assert not any(r.startswith("NGT") for r in applicability)
