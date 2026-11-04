import pytest

from reports.models import Report
from reports.services import PDFExporter
from vendors.models import Industry, Vendor


@pytest.fixture
def vendor(verified_org):
    industry = Industry.objects.create(name="Steel")
    return Vendor.objects.create(
        organization=verified_org, name="Pdf Vendor", industry=industry,
        country="India", contact_email="pdf@vendor.com",
    )


def _report(org, user, vendor, data):
    return Report.objects.create(
        organization=org, report_type="vendor_compliance_report", title="Pdf",
        vendor=vendor, generated_by=user, status="generated", data=data,
    )


def _texts(elements):
    out = []
    for e in elements:
        if hasattr(e, "text"):
            out.append(e.text)
        elif hasattr(e, "_cellvalues"):
            out.extend(str(c) for row in e._cellvalues for c in row)
    return out


@pytest.mark.django_db
class TestGapAndExposureRendering:

    def test_gaps_render_as_title_and_detail_not_dict_repr(self, verified_org, officer_user, vendor):
        report = _report(verified_org, officer_user, vendor, {"compliance_gap_analysis": [
            {"gap": "Expired certificates", "detail": "1 certificate(s) expired", "severity": "high"},
            {"gap": "Reasonable assurance not met", "detail": "Average AI confidence 60.0% < 75% threshold", "severity": "high"},
        ]})
        texts = _texts(PDFExporter()._build_body(report))
        assert any("Expired certificates: 1 certificate(s) expired" in t for t in texts)
        assert not any("'severity'" in t for t in texts)
        assert PDFExporter().export(report).startswith(b"%PDF")

    def test_cbam_and_ngt_financial_exposure_render(self, verified_org, officer_user, vendor):
        report = _report(verified_org, officer_user, vendor, {"regulatory_risk_exposure": [
            {"regulation": "EU CBAM", "status": "exposure_calculated", "detail": "100.00 tCO2e x EUR 65.0 ETS price",
             "financial_exposure": {"eur": 6500.0, "inr": 585000.0, "basis": "EU ETS carbon price estimate"}},
            {"regulation": "NGT India", "status": "at_risk", "detail": "Documents failed",
             "financial_exposure": {"inr": 1900000, "display": "Up to Rs 1.9 Crore"}},
            {"regulation": "SEBI BRSR Core", "status": "compliant", "detail": "ok", "financial_exposure": None},
        ]})
        texts = _texts(PDFExporter()._build_body(report))
        assert "EUR 6,500.00" in texts
        assert "INR 585,000.00" in texts
        assert "INR 1,900,000" in texts
        assert "Documents failed" in texts
        assert PDFExporter().export(report).startswith(b"%PDF")
