from decimal import Decimal

import pytest

from django.utils import timezone

from ai_validation.models import DocumentValidation, ExtractedMetadata, VendorRiskProfile
from reports.compliance_engine import _first_number, generate_vendor_compliance_report
from reports.models import Report
from reports.services import PDFExporter
from vendors.models import Document, DocumentType, Industry, Vendor


def _texts(elements):
    out = []
    for e in elements:
        if hasattr(e, "text"):
            out.append(e.text)
        elif hasattr(e, "_cellvalues"):
            out.extend(str(c) for row in e._cellvalues for c in row)
    return out


def test_first_number_keeps_zero():
    assert _first_number(0.0, 5.0) == 0.0
    assert _first_number(None, 5.0) == 5.0
    assert _first_number(None, None) == 0.0


@pytest.mark.django_db
class TestZeroIsARealValue:

    def test_pdf_renders_zero_confidence_and_zero_co2(self, verified_org, officer_user):
        vendor = Vendor.objects.create(
            organization=verified_org, name="Zero", industry=Industry.objects.create(name="Technology"),
            country="Germany", contact_email="z@v.com",
        )
        report = Report.objects.create(
            organization=verified_org, report_type="vendor_compliance_report", title="Zero",
            vendor=vendor, generated_by=officer_user, status="generated",
            data={"emission_verification": {"document_details": [
                {"document_type": "Cert", "document_status": "valid", "confidence": 0.0,
                 "co2_extracted": 0.0, "assurance_met": False},
                {"document_type": "Missing", "document_status": "valid", "confidence": None,
                 "co2_extracted": None, "assurance_met": False},
            ]}},
        )
        texts = _texts(PDFExporter()._build_body(report))
        assert "0.0%" in texts
        assert "0.00" in texts
        assert texts.count("—") >= 2

    def test_engine_keeps_zero_confidence_and_zero_co2(self, verified_org):
        industry = Industry.objects.create(name="Technology")
        vendor = Vendor.objects.create(
            organization=verified_org, name="ZeroEngine", industry=industry,
            country="Germany", contact_email="ze@v.com",
        )
        dtype = DocumentType.objects.create(name="Emission Certificate")
        doc = Document.objects.create(vendor=vendor, document_type=dtype, status="valid")
        validation = DocumentValidation.objects.create(
            document=doc, status="completed", overall_confidence=Decimal("0"), started_at=timezone.now(),
        )
        ExtractedMetadata.objects.create(document_validation=validation, document=doc, co2_value=Decimal("0"), co2_unit="tonnes")
        VendorRiskProfile.objects.create(
            vendor=vendor, organization=verified_org, risk_level="low",
            risk_score=Decimal("10"), total_co2_emissions=Decimal("0"),
        )
        data = generate_vendor_compliance_report(vendor, verified_org)
        detail = data["emission_verification"]["document_details"][0]
        assert detail["confidence"] == 0.0
        assert detail["co2_extracted"] == 0.0
        assert data["scope_emissions"]["total_co2_tonnes"] == 0.0
