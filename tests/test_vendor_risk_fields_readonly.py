import pytest

from vendors.models import Industry, Vendor

VENDOR_LIST_URL = "/api/vendors/"


def vendor_detail_url(vid):
    return f"/api/vendors/{vid}/"


@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology")


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Locked Vendor",
        industry=industry, country="India", contact_email="locked@vendor.com",
    )


@pytest.mark.django_db
class TestVendorRiskFieldsNotWritable:

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_update_methods_not_allowed(self, officer_client, vendor, method):
        res = getattr(officer_client, method)(
            vendor_detail_url(vendor.id),
            {"risk_level": "low", "compliance_status": "compliant"},
            format="json",
        )
        assert res.status_code == 405
        vendor.refresh_from_db()
        assert vendor.risk_level == "unknown"
        assert vendor.compliance_status == "pending"

    def test_admin_patch_cannot_change_risk_level(self, admin_client, vendor):
        res = admin_client.patch(
            vendor_detail_url(vendor.id), {"risk_level": "low"}, format="json"
        )
        assert res.status_code == 405
        vendor.refresh_from_db()
        assert vendor.risk_level == "unknown"

    def test_create_ignores_supplied_risk_fields(self, officer_client, industry):
        res = officer_client.post(
            VENDOR_LIST_URL,
            {
                "name": "Fresh Vendor", "industry": str(industry.id), "country": "India",
                "contact_email": "fresh@vendor.com",
                "risk_level": "low", "compliance_status": "compliant",
            },
            format="json",
        )
        assert res.status_code == 201
        created = Vendor.objects.get(contact_email="fresh@vendor.com")
        assert created.risk_level == "unknown"
        assert created.compliance_status == "pending"
