import uuid
import pytest
from django.core.cache import cache
from audit_logs.models import AuditLog
from audit_logs.services import log_action
from vendors.models import Vendor, Industry

AUDIT_LOG_URL    = "/api/audit_logs/"
EXPORT_CSV_URL   = "/api/audit_logs/export_csv/"
ACTION_LIST_URL  = "/api/audit_logs/action_choices/"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology")


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Audit Vendor",
        industry=industry, country="India", contact_email="audit@vendor.com",
    )


@pytest.fixture
def audit_log(verified_org, admin_user):
    return AuditLog.objects.create(
        organization=verified_org,
        actor=admin_user,
        action="vendor_created",
        entity_type="Vendor",
        entity_id=str(uuid.uuid4()),
        details={"name": "Test Vendor"},
    )


@pytest.fixture
def multiple_logs(verified_org, admin_user, officer_user):
    logs = []
    for action in ["vendor_created", "document_uploaded", "validation_completed"]:
        logs.append(AuditLog.objects.create(
            organization=verified_org,
            actor=admin_user,
            action=action,
            entity_type="Vendor",
            entity_id=str(uuid.uuid4()),
            details={},
        ))
    return logs


@pytest.mark.django_db
class TestAuditLogModel:

    def test_str_includes_actor_and_action(self, audit_log, admin_user):
        s = str(audit_log)
        assert admin_user.email in s
        assert "vendor_created" in s

    def test_uuid_pk(self, audit_log):
        assert isinstance(audit_log.id, uuid.UUID)

    def test_details_stored_as_json(self, audit_log):
        assert audit_log.details == {"name": "Test Vendor"}

    def test_ordering_newest_first(self, multiple_logs):
        logs = list(AuditLog.objects.all())
        for i in range(len(logs) - 1):
            assert logs[i].created_at >= logs[i + 1].created_at

    def test_system_actor_null(self, verified_org):
        log = AuditLog.objects.create(
            organization=verified_org,
            actor=None,
            action="vendor_created",
            entity_type="Vendor",
            entity_id=str(uuid.uuid4()),
        )
        assert log.actor is None
        assert "system" in str(log)


@pytest.mark.django_db
class TestLogAction:

    def test_creates_audit_log(self, verified_org, admin_user):
        log_action(
            action="vendor_created",
            entity_type="Vendor",
            entity_id="test-id",
            organization=verified_org,
            actor=admin_user,
            details={"name": "Test"},
        )
        assert AuditLog.objects.filter(action="vendor_created").exists()

    def test_stores_details(self, verified_org, admin_user):
        log_action(
            action="vendor_created",
            entity_type="Vendor",
            entity_id="test-id",
            organization=verified_org,
            actor=admin_user,
            details={"name": "Test"},
        )
        log = AuditLog.objects.get(action="vendor_created")
        assert log.details["name"] == "Test"

    def test_works_without_actor(self, verified_org):
        log_action(
            action="vendor_created",
            entity_type="Vendor",
            entity_id="test-id",
            organization=verified_org,
        )
        log = AuditLog.objects.get(action="vendor_created")
        assert log.actor is None

    def test_request_extracts_user_and_org(self, verified_org, admin_user):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.user = admin_user
        request.user.organization = verified_org
        request.META = {"REMOTE_ADDR": "127.0.0.1"}

        log_action(action="vendor_created", entity_type="Vendor", entity_id="x", request=request)
        log = AuditLog.objects.get(action="vendor_created")
        assert log.actor == admin_user
        assert log.ip_address == "127.0.0.1"

    def test_does_not_raise_on_exception(self, verified_org):
        
        from unittest.mock import patch
        with patch.object(AuditLog.objects, "create", side_effect=Exception("DB down")):
            log_action(action="vendor_created", entity_type="Vendor", entity_id="x")
        

    def test_ip_from_forwarded_header(self, verified_org, admin_user):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.user = admin_user
        request.user.organization = verified_org
        request.META = {"HTTP_X_FORWARDED_FOR": "10.0.0.1, 192.168.1.1"}

        log_action(action="vendor_created", entity_type="Vendor", entity_id="x", request=request)
        log = AuditLog.objects.get(action="vendor_created")
        assert log.ip_address == "10.0.0.1"


@pytest.mark.django_db
class TestAuditLogViewSet:

    def test_authenticated_user_can_list(self, admin_client, audit_log):
        res = admin_client.get(AUDIT_LOG_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(AUDIT_LOG_URL).status_code == 401

    def test_officer_can_list(self, officer_client, audit_log):
        res = officer_client.get(AUDIT_LOG_URL)
        assert res.status_code == 200

    def test_viewer_can_list(self, viewer_client, audit_log):
        res = viewer_client.get(AUDIT_LOG_URL)
        assert res.status_code == 200

    def test_response_has_pagination(self, admin_client, audit_log):
        res = admin_client.get(AUDIT_LOG_URL)
        assert "count" in res.data
        assert "results" in res.data

    def test_org_isolation(self, admin_client, verified_org):
        from accounts.utils.email_verification import generate_verification_token, hash_token
        from accounts.models import Organization
        other_org = Organization.objects.create(
            name="Other Corp", industry="Tech", country="India",
            primary_email="other@other.com", is_verified=True,
            email_verification_token=hash_token(generate_verification_token()),
        )
        AuditLog.objects.create(
            organization=other_org,
            action="vendor_created",
            entity_type="Vendor",
            entity_id="foreign-id",
        )
        res = admin_client.get(AUDIT_LOG_URL)
        results = res.data.get("results", res.data)
        entity_ids = [r["entity_id"] for r in results]
        assert "foreign-id" not in entity_ids

    def test_filter_by_action(self, admin_client, multiple_logs):
        res = admin_client.get(AUDIT_LOG_URL, {"action": "vendor_created"})
        results = res.data.get("results", res.data)
        assert all(r["action"] == "vendor_created" for r in results)

    def test_filter_by_entity_type(self, admin_client, multiple_logs):
        res = admin_client.get(AUDIT_LOG_URL, {"entity_type": "Vendor"})
        results = res.data.get("results", res.data)
        assert all(r["entity_type"] == "Vendor" for r in results)

    def test_filter_by_date_from(self, admin_client, audit_log):
        import datetime
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        res = admin_client.get(AUDIT_LOG_URL, {"date_from": yesterday})
        assert res.status_code == 200

    def test_invalid_date_from_returns_400_not_500(self, admin_client, audit_log):
        res = admin_client.get(AUDIT_LOG_URL, {"date_from": "not-a-date"})
        assert res.status_code == 400

    def test_invalid_date_to_returns_400_not_500(self, admin_client, audit_log):
        res = admin_client.get(AUDIT_LOG_URL, {"date_to": "banana"})
        assert res.status_code == 400

    def test_response_includes_actor_name(self, admin_client, audit_log):
        res = admin_client.get(AUDIT_LOG_URL)
        results = res.data.get("results", res.data)
        assert len(results) >= 1
        assert "actor_name" in results[0]

    def test_actor_name_uses_full_name_attribute(self, admin_client, audit_log, admin_user):
        admin_user.full_name = "Admin User"
        admin_user.save()
        res = admin_client.get(AUDIT_LOG_URL)
        results = res.data.get("results", res.data)
        assert any(r["actor_name"] == "Admin User" for r in results)

    def test_system_actor_shows_as_system(self, admin_client, verified_org):
        AuditLog.objects.create(
            organization=verified_org, actor=None,
            action="vendor_created", entity_type="Vendor", entity_id="x",
        )
        res = admin_client.get(AUDIT_LOG_URL)
        results = res.data.get("results", res.data)
        assert any(r["actor_name"] == "System" for r in results)


@pytest.mark.django_db
class TestAuditLogCsvExport:

    def test_export_returns_csv_content_type(self, admin_client, audit_log):
        res = admin_client.get(EXPORT_CSV_URL)
        assert res.status_code == 200
        assert "text/csv" in res["Content-Type"]

    def test_export_has_attachment_header(self, admin_client, audit_log):
        res = admin_client.get(EXPORT_CSV_URL)
        assert "attachment" in res["Content-Disposition"]
        assert ".csv" in res["Content-Disposition"]

    def test_export_contains_header_row(self, admin_client, audit_log):
        res = admin_client.get(EXPORT_CSV_URL)
        content = res.content.decode("utf-8")
        assert "Timestamp" in content
        assert "Action" in content
        assert "Actor" in content

    def test_export_contains_log_data(self, admin_client, audit_log):
        res = admin_client.get(EXPORT_CSV_URL)
        content = res.content.decode("utf-8")
        assert "vendor_created" in content.lower() or "Vendor Created" in content

    def test_formula_injection_prefix_neutralized(self, admin_client, verified_org):
        AuditLog.objects.create(
            organization=verified_org,
            actor=None,
            action="vendor_created",
            entity_type="Vendor",
            entity_id="=cmd|'/c calc'!A1",
            details={},
        )
        res = admin_client.get(EXPORT_CSV_URL)
        content = res.content.decode("utf-8")
        assert "'=cmd" in content

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(EXPORT_CSV_URL).status_code == 401


@pytest.mark.django_db
class TestActionChoices:

    def test_returns_list_of_choices(self, admin_client):
        res = admin_client.get(ACTION_LIST_URL)
        assert res.status_code == 200
        assert isinstance(res.data, list)
        assert len(res.data) > 0

    def test_each_choice_has_value_and_label(self, admin_client):
        res = admin_client.get(ACTION_LIST_URL)
        for choice in res.data:
            assert "value" in choice
            assert "label" in choice

    def test_vendor_created_in_choices(self, admin_client):
        res = admin_client.get(ACTION_LIST_URL)
        values = [c["value"] for c in res.data]
        assert "vendor_created" in values


@pytest.mark.django_db
class TestAuditLogSignals:

    def test_vendor_created_signal_fires(self, verified_org, industry):
        initial_count = AuditLog.objects.filter(action="vendor_created").count()
        Vendor.objects.create(
            organization=verified_org, name="Signal Vendor",
            industry=industry, country="India", contact_email="signal@v.com",
        )
        assert AuditLog.objects.filter(action="vendor_created").count() == initial_count + 1

    def test_vendor_updated_signal_fires(self, vendor):
        initial_count = AuditLog.objects.filter(action="vendor_updated").count()
        vendor.name = "Updated Vendor"
        vendor.save()
        assert AuditLog.objects.filter(action="vendor_updated").count() == initial_count + 1

    def test_vendor_deleted_signal_fires(self, verified_org, industry):
        v = Vendor.objects.create(
            organization=verified_org, name="Delete Me",
            industry=industry, country="India", contact_email="delete@v.com",
        )
        initial_count = AuditLog.objects.filter(action="vendor_deleted").count()
        v.delete()
        assert AuditLog.objects.filter(action="vendor_deleted").count() == initial_count + 1

    def test_intermediate_validation_save_does_not_log(self, verified_org, industry, vendor):
        from vendors.models import Document, DocumentType
        dt = DocumentType.objects.create(name="Sig Test Doc")
        doc = Document.objects.create(vendor=vendor, document_type=dt, status="uploaded")
        from ai_validation.models import DocumentValidation
        from django.utils import timezone as tz

        v = DocumentValidation.objects.create(document=doc, started_at=tz.now())
        initial_count = AuditLog.objects.filter(action="validation_triggered").count()

        for step in ("readability", "relevance", "authenticity"):
            v.current_step = step
            v.save(update_fields=["current_step"])

        assert AuditLog.objects.filter(
            action="validation_triggered"
        ).count() == initial_count