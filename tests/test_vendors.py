import io
import uuid
import pytest
from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from django.db import IntegrityError

from accounts.models import Organization
from vendors.models import (
    Industry, DocumentType, IndustryRequiredDocument,
    Vendor, Document, VendorBulkUpload,
)
from vendors.services.csv_parser import parse_csv, CsvParsingError
from vendors.services.upload_token_services import UploadTokenService
from vendors.services.industry_mapper import get_or_create_industry
from vendors.services.vendor_creator import VendorCreatorService, VendorCreationError


# ── URL helpers ───────────────────────────────────────────────────────────────

VENDOR_LIST_URL  = "/api/vendors/"
VENDOR_BULK_URL  = "/api/vendors/bulk-upload/"
VENDOR_EMAIL_URL = "/api/vendors/send-emails/"
INDUSTRY_URL     = "/api/vendors/config/industries/"
DOC_TYPE_URL     = "/api/vendors/config/document-types/"
DOCUMENTS_URL    = "/api/vendors/documents/"


def vendor_detail_url(vid):   return f"/api/vendors/{vid}/"
def vendor_docs_url(vid):     return f"/api/vendors/{vid}/documents/"
def doc_detail_url(did):      return f"/api/vendors/documents/{did}/"
def doc_resend_url(did):      return f"/api/vendors/documents/{did}/resend-link/"
def public_upload_url(token): return f"/api/vendors/upload/{token}/"


@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology", description="Tech")


@pytest.fixture
def industry_mfg(db):
    return Industry.objects.create(name="Manufacturing", description="Mfg")


@pytest.fixture
def doc_type(db):
    return DocumentType.objects.create(name="Emission Report", description="GHG")


@pytest.fixture
def doc_type2(db):
    return DocumentType.objects.create(name="ISO 14064 Certificate", description="ISO")


@pytest.fixture
def doc_type3(db):
    return DocumentType.objects.create(name="Carbon Credit Certificate", description="CC")


@pytest.fixture
def required_doc(industry, doc_type):
    return IndustryRequiredDocument.objects.create(
        industry=industry, document_type=doc_type, mandatory=True
    )


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Acme Corp",
        industry=industry, country="India", contact_email="acme@acme.com",
    )


@pytest.fixture
def vendor2(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Beta Ltd",
        industry=industry, country="Germany", contact_email="beta@beta.com",
    )


@pytest.fixture
def document(vendor, doc_type):
    return Document.objects.create(vendor=vendor, document_type=doc_type, status="pending")


@pytest.fixture
def uploaded_document(vendor, doc_type2):
    return Document.objects.create(vendor=vendor, document_type=doc_type2, status="uploaded")


@pytest.fixture
def expired_document(vendor, doc_type2):
    return Document.objects.create(vendor=vendor, document_type=doc_type2, status="expired")


@pytest.fixture
def invalid_document(vendor, doc_type3):
    return Document.objects.create(vendor=vendor, document_type=doc_type3, status="invalid")


@pytest.fixture
def vendor_with_token(vendor):
    token = UploadTokenService.generate_for_vendor(vendor)
    vendor.refresh_from_db()
    vendor._plain_token = token
    return vendor


@pytest.fixture
def other_org(db):
    from accounts.utils.email_verification import generate_verification_token, hash_token
    return Organization.objects.create(
        name="Other Corp", industry="Tech", country="India",
        primary_email="other@other.com", is_verified=True,
        email_verification_token=hash_token(generate_verification_token()),
    )


@pytest.fixture
def other_vendor(other_org, industry):
    return Vendor.objects.create(
        organization=other_org, name="Other Vendor",
        industry=industry, country="USA", contact_email="other@vendor.com",
    )


# ── Industry model ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestIndustryModel:

    def test_str_returns_name(self, industry):
        assert str(industry) == "Technology"

    def test_name_stripped_on_save(self, db):
        ind = Industry.objects.create(name="  Finance  ", description="")
        assert ind.name == "Finance"

    def test_unique_name(self, industry):
        with pytest.raises(IntegrityError):
            Industry.objects.create(name="Technology")

    def test_ordering_by_name(self, db):
        Industry.objects.create(name="Zebra")
        Industry.objects.create(name="Alpha")
        names = list(Industry.objects.values_list("name", flat=True))
        assert names == sorted(names)


# ── Vendor model ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorModel:

    def test_str_returns_name(self, vendor):
        assert str(vendor) == "Acme Corp"

    def test_email_lowercased_on_save(self, verified_org, industry):
        v = Vendor.objects.create(
            organization=verified_org, name="Upper Co",
            industry=industry, country="India", contact_email="UPPER@UPPER.COM",
        )
        assert v.contact_email == "upper@upper.com"

    def test_name_stripped_on_save(self, verified_org, industry):
        v = Vendor.objects.create(
            organization=verified_org, name="  Spaced  ",
            industry=industry, country="India", contact_email="spaced@spaced.com",
        )
        assert v.name == "Spaced"

    def test_unique_email_per_org(self, vendor, verified_org, industry):
        with pytest.raises(IntegrityError):
            Vendor.objects.create(
                organization=verified_org, name="Dup",
                industry=industry, country="India", contact_email="acme@acme.com",
            )

    def test_default_compliance_status_pending(self, vendor):
        assert vendor.compliance_status == "pending"

    def test_default_risk_level_medium(self, vendor):
        assert vendor.risk_level == "medium"

    def test_cascade_delete_removes_documents(self, vendor, document):
        vid = vendor.id
        vendor.delete()
        assert Document.objects.filter(vendor_id=vid).count() == 0


# ── Document model ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentModel:

    def test_default_status_pending(self, document):
        assert document.status == "pending"

    def test_default_upload_attempts_zero(self, document):
        assert document.upload_attempts == 0

    def test_unique_vendor_document_type(self, vendor, doc_type):
        # FIX: create the first one here, then attempt the duplicate
        Document.objects.create(vendor=vendor, document_type=doc_type, status="pending")
        with pytest.raises(IntegrityError):
            Document.objects.create(vendor=vendor, document_type=doc_type, status="pending")


# ── Vendor list / create ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorListCreateView:

    def test_authenticated_user_can_list_vendors(self, officer_client, vendor):
        res = officer_client.get(VENDOR_LIST_URL)
        assert res.status_code == 200
        assert res.data["count"] >= 1

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(VENDOR_LIST_URL).status_code == 401

    def test_org_isolation(self, officer_client, other_vendor):
        res = officer_client.get(VENDOR_LIST_URL)
        ids = [v["id"] for v in res.data["results"]]
        assert str(other_vendor.id) not in ids

    def test_search_by_name(self, officer_client, vendor, vendor2):
        res = officer_client.get(VENDOR_LIST_URL, {"search": "Acme"})
        names = [v["name"] for v in res.data["results"]]
        assert "Acme Corp" in names
        assert "Beta Ltd" not in names

    def test_filter_by_compliance_status(self, officer_client, vendor):
        vendor.compliance_status = "compliant"
        vendor.save()
        res = officer_client.get(VENDOR_LIST_URL, {"compliance_status": "compliant"})
        assert res.data["count"] >= 1

    def test_filter_by_risk_level(self, officer_client, vendor):
        vendor.risk_level = "high"
        vendor.save()
        res = officer_client.get(VENDOR_LIST_URL, {"risk_level": "high"})
        assert res.data["count"] >= 1

    def test_pagination_structure(self, officer_client, vendor):
        res = officer_client.get(VENDOR_LIST_URL)
        assert "count" in res.data
        assert "total_pages" in res.data
        assert "results" in res.data

    def test_create_vendor_success(self, officer_client, industry, required_doc):
        res = officer_client.post(VENDOR_LIST_URL, {
            "name": "New Vendor", "industry": str(industry.id),
            "country": "India", "contact_email": "new@vendor.com",
        })
        assert res.status_code == 201
        assert Vendor.objects.filter(contact_email="new@vendor.com").exists()

    def test_create_vendor_creates_required_documents(self, officer_client, industry, required_doc):
        officer_client.post(VENDOR_LIST_URL, {
            "name": "Doc Vendor", "industry": str(industry.id),
            "country": "India", "contact_email": "docvendor@v.com",
        })
        vendor = Vendor.objects.get(contact_email="docvendor@v.com")
        assert Document.objects.filter(vendor=vendor).count() == 1

    def test_create_vendor_missing_name_400(self, officer_client, industry):
        res = officer_client.post(VENDOR_LIST_URL, {
            "industry": str(industry.id), "country": "India", "contact_email": "x@x.com",
        })
        assert res.status_code == 400

    def test_create_vendor_missing_industry_400(self, officer_client):
        res = officer_client.post(VENDOR_LIST_URL, {
            "name": "X", "country": "India", "contact_email": "x@x.com",
        })
        assert res.status_code == 400

    def test_create_vendor_invalid_industry_400(self, officer_client):
        res = officer_client.post(VENDOR_LIST_URL, {
            "name": "X", "industry": str(uuid.uuid4()),
            "country": "India", "contact_email": "x@x.com",
        })
        assert res.status_code == 400

    def test_create_vendor_duplicate_email_400(self, officer_client, vendor, industry):
        res = officer_client.post(VENDOR_LIST_URL, {
            "name": "Dup", "industry": str(industry.id),
            "country": "India", "contact_email": "acme@acme.com",
        })
        assert res.status_code == 400

    def test_viewer_cannot_create_vendor(self, viewer_client, industry):
        res = viewer_client.post(VENDOR_LIST_URL, {
            "name": "Viewer Hack", "industry": str(industry.id),
            "country": "India", "contact_email": "viewerhack@x.com",
        })
        assert res.status_code == 403


# ── Vendor detail ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorDetailView:

    def test_get_vendor(self, officer_client, vendor):
        res = officer_client.get(vendor_detail_url(vendor.id))
        assert res.status_code == 200
        assert res.data["name"] == "Acme Corp"

    def test_vendor_from_other_org_returns_404(self, officer_client, other_vendor):
        assert officer_client.get(vendor_detail_url(other_vendor.id)).status_code == 404

    def test_nonexistent_vendor_404(self, officer_client):
        assert officer_client.get(vendor_detail_url(uuid.uuid4())).status_code == 404

    def test_unauthenticated_401(self, anon_client, vendor):
        assert anon_client.get(vendor_detail_url(vendor.id)).status_code == 401

    def test_response_includes_industry_name(self, officer_client, vendor):
        res = officer_client.get(vendor_detail_url(vendor.id))
        assert res.data["industry"] == "Technology"


# ── Vendor document list ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorDocumentListView:

    def test_returns_vendor_documents(self, officer_client, vendor, document):
        res = officer_client.get(vendor_docs_url(vendor.id))
        assert res.status_code == 200
        assert len(res.data) >= 1

    def test_empty_list_for_vendor_with_no_docs(self, officer_client, vendor2):
        res = officer_client.get(vendor_docs_url(vendor2.id))
        assert res.status_code == 200
        assert res.data == []

    def test_other_org_vendor_returns_404(self, officer_client, other_vendor):
        assert officer_client.get(vendor_docs_url(other_vendor.id)).status_code == 404

    def test_nonexistent_vendor_404(self, officer_client):
        assert officer_client.get(vendor_docs_url(uuid.uuid4())).status_code == 404


# ── Send emails ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorSendEmailsView:

    @patch("vendors.services.email_campaign_service.EmailCampaignService.run")
    def test_officer_can_send_emails(self, mock_run, officer_client, vendor, document):
        res = officer_client.post(VENDOR_EMAIL_URL, {"vendor_ids": [str(vendor.id)]}, format="json")
        assert res.status_code == 200
        mock_run.assert_called_once()

    def test_empty_vendor_ids_400(self, officer_client):
        res = officer_client.post(VENDOR_EMAIL_URL, {"vendor_ids": []}, format="json")
        assert res.status_code == 400

    def test_non_list_vendor_ids_400(self, officer_client):
        res = officer_client.post(VENDOR_EMAIL_URL, {"vendor_ids": "not-a-list"}, format="json")
        assert res.status_code == 400

    def test_foreign_org_vendors_ignored(self, officer_client, other_vendor):
        res = officer_client.post(VENDOR_EMAIL_URL, {"vendor_ids": [str(other_vendor.id)]}, format="json")
        assert res.status_code == 404

    def test_viewer_cannot_send_emails(self, viewer_client, vendor):
        res = viewer_client.post(VENDOR_EMAIL_URL, {"vendor_ids": [str(vendor.id)]}, format="json")
        assert res.status_code == 403

    def test_unauthenticated_401(self, anon_client, vendor):
        assert anon_client.post(VENDOR_EMAIL_URL, {"vendor_ids": [str(vendor.id)]}, format="json").status_code == 401


# ── Bulk upload ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorBulkUploadView:

    def _csv(self, rows):
        lines = ["name,contact_email,industry,country"]
        for r in rows:
            lines.append(f"{r['name']},{r['email']},{r['industry']},{r['country']}")
        f = io.BytesIO("\n".join(lines).encode("utf-8"))
        f.name = "vendors.csv"
        return f

    def test_valid_csv_creates_vendors(self, officer_client, industry):
        res = officer_client.post(VENDOR_BULK_URL, {
            "csv_file": self._csv([{
                "name": "CSV Vendor", "email": "csv@csv.com",
                "industry": "Technology", "country": "India",
            }])
        }, format="multipart")
        assert res.status_code == 200
        assert res.data["success_count"] >= 1

    def test_returns_bulk_upload_summary(self, officer_client, industry):
        res = officer_client.post(VENDOR_BULK_URL, {
            "csv_file": self._csv([{
                "name": "Bulk1", "email": "b1@b.com",
                "industry": "Technology", "country": "India",
            }])
        }, format="multipart")
        assert "bulk_upload_id" in res.data
        assert "total_rows" in res.data
        assert "success_count" in res.data
        assert "failure_count" in res.data

    def test_missing_csv_returns_400(self, officer_client):
        assert officer_client.post(VENDOR_BULK_URL, {}, format="multipart").status_code == 400

    def test_non_csv_file_returns_400(self, officer_client):
        f = io.BytesIO(b"not a csv")
        f.name = "data.txt"
        assert officer_client.post(VENDOR_BULK_URL, {"csv_file": f}, format="multipart").status_code == 400

    def test_csv_missing_required_columns_returns_400(self, officer_client):
        f = io.BytesIO(b"name,email\nFoo,foo@foo.com")
        f.name = "vendors.csv"
        res = officer_client.post(VENDOR_BULK_URL, {"csv_file": f}, format="multipart")
        assert res.status_code == 400

    def test_empty_csv_returns_400(self, officer_client):
        f = io.BytesIO(b"")
        f.name = "vendors.csv"
        assert officer_client.post(VENDOR_BULK_URL, {"csv_file": f}, format="multipart").status_code == 400

    def test_bulk_upload_record_saved_to_db(self, officer_client, industry):
        res = officer_client.post(VENDOR_BULK_URL, {
            "csv_file": self._csv([{
                "name": "DBSave", "email": "db@save.com",
                "industry": "Technology", "country": "India",
            }])
        }, format="multipart")
        assert VendorBulkUpload.objects.filter(id=res.data["bulk_upload_id"]).exists()

    def test_unauthenticated_401(self, anon_client):
        assert anon_client.post(VENDOR_BULK_URL, {}).status_code == 401


# ── Config endpoints ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestConfigEndpoints:

    def test_list_industries(self, officer_client, industry):
        res = officer_client.get(INDUSTRY_URL)
        assert res.status_code == 200
        assert any(i["name"] == "Technology" for i in res.data)

    def test_create_industry(self, officer_client):
        res = officer_client.post(INDUSTRY_URL, {"name": "Finance", "description": ""})
        assert res.status_code == 201
        assert Industry.objects.filter(name="Finance").exists()

    def test_create_industry_empty_name_400(self, officer_client):
        assert officer_client.post(INDUSTRY_URL, {"name": "   "}).status_code == 400

    def test_list_document_types(self, officer_client, doc_type):
        res = officer_client.get(DOC_TYPE_URL)
        assert res.status_code == 200
        assert any(d["name"] == "Emission Report" for d in res.data)

    def test_create_document_type(self, officer_client):
        assert officer_client.post(DOC_TYPE_URL, {"name": "New Doc", "description": ""}).status_code == 201

    def test_unauthenticated_blocked_on_config(self, anon_client):
        assert anon_client.get(INDUSTRY_URL).status_code == 401
        assert anon_client.get(DOC_TYPE_URL).status_code == 401


# ── Document list ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentListView:

    def test_returns_org_documents(self, officer_client, document):
        res = officer_client.get(DOCUMENTS_URL)
        assert res.status_code == 200
        assert res.data["count"] >= 1

    def test_org_isolation(self, officer_client, other_vendor, doc_type):
        Document.objects.create(vendor=other_vendor, document_type=doc_type, status="pending")
        res = officer_client.get(DOCUMENTS_URL)
        vendor_ids = [str(d["vendor_id"]) for d in res.data["results"]]
        assert str(other_vendor.id) not in vendor_ids

    def test_filter_by_status(self, officer_client, document, uploaded_document):
        res = officer_client.get(DOCUMENTS_URL, {"status": "uploaded"})
        statuses = [d["status"] for d in res.data["results"]]
        assert all(s == "uploaded" for s in statuses)

    def test_filter_by_vendor(self, officer_client, vendor, document):
        res = officer_client.get(DOCUMENTS_URL, {"vendor": str(vendor.id)})
        assert res.status_code == 200
        assert res.data["count"] >= 1

    def test_search_by_vendor_name(self, officer_client, document):
        res = officer_client.get(DOCUMENTS_URL, {"search": "Acme"})
        assert res.status_code == 200

    def test_pagination_structure(self, officer_client, document):
        res = officer_client.get(DOCUMENTS_URL)
        assert "count" in res.data
        assert "total_pages" in res.data
        assert "results" in res.data

    def test_unauthenticated_401(self, anon_client):
        assert anon_client.get(DOCUMENTS_URL).status_code == 401

    def test_viewer_can_list_documents(self, viewer_client, document):
        assert viewer_client.get(DOCUMENTS_URL).status_code == 200


# ── Document detail ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentDetailView:

    def test_get_document_detail(self, officer_client, document):
        res = officer_client.get(doc_detail_url(document.id))
        assert res.status_code == 200
        assert str(res.data["id"]) == str(document.id)

    def test_foreign_org_document_404(self, officer_client, other_vendor, doc_type):
        other_doc = Document.objects.create(vendor=other_vendor, document_type=doc_type, status="pending")
        assert officer_client.get(doc_detail_url(other_doc.id)).status_code == 404

    def test_nonexistent_document_404(self, officer_client):
        assert officer_client.get(doc_detail_url(uuid.uuid4())).status_code == 404

    def test_unauthenticated_401(self, anon_client, document):
        assert anon_client.get(doc_detail_url(document.id)).status_code == 401

    def test_response_has_vendor_name(self, officer_client, document):
        assert officer_client.get(doc_detail_url(document.id)).data["vendor_name"] == "Acme Corp"

    def test_response_has_document_type_name(self, officer_client, document):
        assert officer_client.get(doc_detail_url(document.id)).data["document_type"] == "Emission Report"


# ── Document resend link ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentResendLinkView:

    @patch("vendors.views.document_views.EmailService.send")
    @patch("vendors.views.document_views.UploadTokenService.generate_for_vendor")
    def test_resend_for_expired_document(self, mock_token, mock_email, officer_client, expired_document):
        mock_token.return_value = "new-token"
        res = officer_client.post(doc_resend_url(expired_document.id))
        assert res.status_code == 200
        expired_document.refresh_from_db()
        assert expired_document.status == "pending"

    @patch("vendors.views.document_views.EmailService.send")
    @patch("vendors.views.document_views.UploadTokenService.generate_for_vendor")
    def test_resend_for_invalid_document(self, mock_token, mock_email, officer_client, invalid_document):
        mock_token.return_value = "new-token"
        res = officer_client.post(doc_resend_url(invalid_document.id))
        assert res.status_code == 200
        invalid_document.refresh_from_db()
        # invalid resets to pending
        assert invalid_document.status == "pending"

    # FIX: pending IS in resendable = ('pending', 'invalid', 'expired').
    # The original test wrongly expected 400. Pending documents need a fresh
    # link when the vendor never uploaded — this is the primary use case.
    @patch("vendors.views.document_views.EmailService.send")
    @patch("vendors.views.document_views.UploadTokenService.generate_for_vendor")
    def test_resend_for_pending_document_succeeds(self, mock_token, mock_email, officer_client, document):
        """Pending documents can be resent — vendor may not have received or used the link."""
        mock_token.return_value = "new-token"
        res = officer_client.post(doc_resend_url(document.id))
        assert res.status_code == 200
        # pending stays pending (no file to clear, no status reset needed)
        document.refresh_from_db()
        assert document.status == "pending"

    def test_cannot_resend_uploaded_document(self, officer_client, uploaded_document):
        """uploaded is NOT resendable — vendor already submitted the file."""
        res = officer_client.post(doc_resend_url(uploaded_document.id))
        assert res.status_code == 400

    def test_cannot_resend_valid_document(self, officer_client, vendor, doc_type):
        """valid documents need no resend."""
        valid_doc = Document.objects.create(
            vendor=vendor,
            document_type=doc_type,
            status="valid",
        )
        res = officer_client.post(doc_resend_url(valid_doc.id))
        assert res.status_code == 400

    def test_foreign_org_document_404(self, officer_client, other_vendor, doc_type):
        other_doc = Document.objects.create(vendor=other_vendor, document_type=doc_type, status="expired")
        assert officer_client.post(doc_resend_url(other_doc.id)).status_code == 404

    def test_nonexistent_document_404(self, officer_client):
        assert officer_client.post(doc_resend_url(uuid.uuid4())).status_code == 404

    @patch("vendors.views.document_views.EmailService.send")
    @patch("vendors.views.document_views.UploadTokenService.generate_for_vendor")
    def test_audit_log_does_not_crash(self, mock_token, mock_email, officer_client, expired_document):
        mock_token.return_value = "tok"
        res = officer_client.post(doc_resend_url(expired_document.id))
        assert res.status_code == 200

    def test_unauthenticated_401(self, anon_client, expired_document):
        assert anon_client.post(doc_resend_url(expired_document.id)).status_code == 401

    def test_viewer_cannot_resend_link(self, viewer_client, expired_document):
        assert viewer_client.post(doc_resend_url(expired_document.id)).status_code == 403


# ── Public upload ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorPublicUploadView:

    def test_valid_token_returns_pending_docs(self, anon_client, vendor_with_token, document):
        res = anon_client.get(public_upload_url(vendor_with_token._plain_token))
        assert res.status_code == 200
        assert "vendor_name" in res.data
        assert len(res.data["pending_documents"]) >= 1

    def test_invalid_token_404(self, anon_client):
        res = anon_client.get(public_upload_url("totally-invalid-token-xyz"))
        assert res.status_code == 404

    def test_expired_token_400(self, anon_client, vendor):
        vendor.upload_token = "expired-token"
        vendor.upload_token_expires_at = timezone.now() - timezone.timedelta(hours=1)
        vendor.save()
        assert anon_client.get(public_upload_url("expired-token")).status_code == 400

    def test_upload_file_success(self, anon_client, vendor_with_token, document):
        f = io.BytesIO(b"%PDF-1.4 fake content")
        f.name = "emission.pdf"
        res = anon_client.post(
            public_upload_url(vendor_with_token._plain_token),
            {"document_id": str(document.id), "file": f},
            format="multipart",
        )
        assert res.status_code == 200
        document.refresh_from_db()
        assert document.status == "uploaded"

    def test_upload_increments_upload_attempts(self, anon_client, vendor_with_token, document):
        f = io.BytesIO(b"content")
        f.name = "doc.pdf"
        anon_client.post(
            public_upload_url(vendor_with_token._plain_token),
            {"document_id": str(document.id), "file": f},
            format="multipart",
        )
        document.refresh_from_db()
        assert document.upload_attempts == 1

    def test_upload_missing_document_id_400(self, anon_client, vendor_with_token):
        f = io.BytesIO(b"content")
        f.name = "doc.pdf"
        res = anon_client.post(
            public_upload_url(vendor_with_token._plain_token),
            {"file": f},
            format="multipart",
        )
        assert res.status_code == 400

    def test_upload_already_uploaded_document_404(self, anon_client, vendor_with_token, uploaded_document):
        f = io.BytesIO(b"content")
        f.name = "doc.pdf"
        res = anon_client.post(
            public_upload_url(vendor_with_token._plain_token),
            {"document_id": str(uploaded_document.id), "file": f},
            format="multipart",
        )
        assert res.status_code == 404

    def test_token_cleared_when_all_docs_uploaded(self, anon_client, vendor_with_token, document):
        f = io.BytesIO(b"content")
        f.name = "doc.pdf"
        anon_client.post(
            public_upload_url(vendor_with_token._plain_token),
            {"document_id": str(document.id), "file": f},
            format="multipart",
        )
        vendor_with_token.refresh_from_db()
        assert vendor_with_token.upload_token is None


# ── CSV parser ────────────────────────────────────────────────────────────────

class TestCsvParser:

    def _f(self, content):
        f = io.BytesIO(content.encode("utf-8"))
        f.name = "test.csv"
        return f

    def test_valid_csv_yields_rows(self):
        rows = list(parse_csv(self._f("name,contact_email,industry,country\nFoo,foo@foo.com,Tech,India")))
        assert len(rows) == 1
        _, row = rows[0]
        assert row["name"] == "Foo"

    def test_missing_required_column_raises(self):
        with pytest.raises(CsvParsingError, match="Missing required columns"):
            list(parse_csv(self._f("name,email\nFoo,foo@foo.com")))

    def test_empty_file_raises(self):
        with pytest.raises(CsvParsingError, match="empty"):
            list(parse_csv(self._f("")))

    def test_empty_rows_skipped(self):
        rows = list(parse_csv(self._f("name,contact_email,industry,country\n,,,\nFoo,f@f.com,Tech,India")))
        assert len(rows) == 1

    def test_headers_normalised_to_lowercase(self):
        rows = list(parse_csv(self._f("Name,Contact_Email,Industry,Country\nFoo,foo@foo.com,Tech,India")))
        _, row = rows[0]
        assert "name" in row
        assert "contact_email" in row

    def test_non_utf8_raises(self):
        f = io.BytesIO(b"\xff\xfe bad bytes")
        f.name = "bad.csv"
        with pytest.raises(CsvParsingError, match="UTF-8"):
            list(parse_csv(f))

    def test_row_values_stripped(self):
        rows = list(parse_csv(self._f(
            "name,contact_email,industry,country\n"
            "  Foo  ,  foo@foo.com  ,  Tech  ,  India  "
        )))
        _, row = rows[0]
        assert row["name"] == "Foo"
        assert row["contact_email"] == "foo@foo.com"


# ── Upload token service ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUploadTokenService:

    def test_generates_token_on_vendor(self, vendor):
        token = UploadTokenService.generate_for_vendor(vendor)
        vendor.refresh_from_db()
        assert vendor.upload_token == token
        assert vendor.upload_token_expires_at is not None

    def test_token_expires_in_72_hours(self, vendor):
        UploadTokenService.generate_for_vendor(vendor)
        vendor.refresh_from_db()
        diff = vendor.upload_token_expires_at - timezone.now()
        assert 71 < diff.total_seconds() / 3600 <= 72

    def test_token_is_unique(self, vendor, vendor2):
        assert UploadTokenService.generate_for_vendor(vendor) != UploadTokenService.generate_for_vendor(vendor2)

    def test_regenerate_overwrites_old_token(self, vendor):
        t1 = UploadTokenService.generate_for_vendor(vendor)
        t2 = UploadTokenService.generate_for_vendor(vendor)
        assert t1 != t2
        vendor.refresh_from_db()
        assert vendor.upload_token == t2


# ── Industry mapper ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestIndustryMapper:

    def test_creates_new_industry(self, db):
        ind = get_or_create_industry("NewSector")
        assert Industry.objects.filter(name="NewSector").exists()

    def test_returns_existing_case_insensitive(self, industry):
        result = get_or_create_industry("technology")
        assert result.id == industry.id

    def test_empty_name_raises(self, db):
        with pytest.raises(ValueError, match="required"):
            get_or_create_industry("")

    def test_whitespace_only_raises(self, db):
        with pytest.raises(ValueError):
            get_or_create_industry("   ")

    def test_name_stripped(self, db):
        ind = get_or_create_industry("  Logistics  ")
        assert ind.name == "Logistics"


# ── Vendor creator service ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorCreatorService:

    def test_creates_vendor(self, verified_org, industry):
        vendor = VendorCreatorService.create_vendor(
            organization=verified_org,
            data={"name": "SVC Vendor", "contact_email": "svc@svc.com", "country": "India"},
            industry=industry,
        )
        assert vendor is not None
        assert Vendor.objects.filter(contact_email="svc@svc.com").exists()

    def test_creates_required_documents(self, verified_org, industry, required_doc):
        vendor = VendorCreatorService.create_vendor(
            organization=verified_org,
            data={"name": "DocSVC", "contact_email": "doc@svc.com", "country": "India"},
            industry=industry,
        )
        assert Document.objects.filter(vendor=vendor).count() == 1

    def test_missing_name_raises(self, verified_org, industry):
        with pytest.raises(VendorCreationError):
            VendorCreatorService.create_vendor(
                organization=verified_org,
                data={"name": "", "contact_email": "x@x.com", "country": "India"},
                industry=industry,
            )

    @patch("vendors.services.email_campaign_service.EmailCampaignService.run")
    def test_send_emails_true_calls_campaign(self, mock_run, verified_org, industry):
        VendorCreatorService.create_vendor(
            organization=verified_org,
            data={"name": "EmailSVC", "contact_email": "em@svc.com", "country": "India"},
            industry=industry,
            send_emails=True,
        )
        mock_run.assert_called_once()

    @patch("vendors.services.email_campaign_service.EmailCampaignService.run")
    def test_send_emails_false_does_not_call_campaign(self, mock_run, verified_org, industry):
        VendorCreatorService.create_vendor(
            organization=verified_org,
            data={"name": "NoEmail", "contact_email": "no@svc.com", "country": "India"},
            industry=industry,
            send_emails=False,
        )
        mock_run.assert_not_called()


# ── Bulk upload serializer ────────────────────────────────────────────────────

class TestVendorBulkUploadSerializer:

    def _file(self, name="test.csv", content=b"data", size=None):
        f = io.BytesIO(content if size is None else b"x" * size)
        f.name = name
        f.size = size or len(content)
        return f

    def test_valid_csv_passes(self):
        from vendors.serializers.bulk_upload_serializers import VendorBulkUploadSerializer
        s = VendorBulkUploadSerializer(data={"csv_file": self._file()})
        assert s.is_valid(), s.errors

    def test_non_csv_extension_fails(self):
        from vendors.serializers.bulk_upload_serializers import VendorBulkUploadSerializer
        s = VendorBulkUploadSerializer(data={"csv_file": self._file(name="data.txt")})
        assert not s.is_valid()
        assert "csv_file" in s.errors

    def test_empty_file_fails(self):
        from vendors.serializers.bulk_upload_serializers import VendorBulkUploadSerializer
        s = VendorBulkUploadSerializer(data={"csv_file": self._file(content=b"", size=0)})
        assert not s.is_valid()

    def test_oversized_file_fails(self):
        from vendors.serializers.bulk_upload_serializers import VendorBulkUploadSerializer
        s = VendorBulkUploadSerializer(data={"csv_file": self._file(size=11 * 1024 * 1024)})
        assert not s.is_valid()