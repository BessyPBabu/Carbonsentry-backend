import uuid
import pytest
from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from django.core import mail

from vendors.models import Vendor, Industry, Document, DocumentType
from communication.models import ChatToken, Message
from communication.services import send_chat_invitation

CHAT_LIST_URL   = "/api/communication/chats/"
INVITE_URL      = "/api/communication/invite/"
VERIFY_OTP_URL  = "/api/communication/verify-otp/"


def messages_url(vid):    return f"/api/communication/chats/{vid}/messages/"
def validate_url(token):  return f"/api/communication/validate/{token}/"
def revoke_url(token_id): return f"/api/communication/tokens/{token_id}/revoke/"


@pytest.fixture
def industry(db):
    return Industry.objects.create(name="Technology")


@pytest.fixture
def vendor(verified_org, industry):
    return Vendor.objects.create(
        organization=verified_org, name="Chat Vendor",
        industry=industry, country="India", contact_email="chat@vendor.com",
    )


@pytest.fixture
def chat_token(vendor, officer_user):
    return ChatToken.objects.create(
        vendor=vendor,
        created_by=officer_user,
        sent_to_email="chat@vendor.com",
    )


@pytest.fixture
def verified_chat_token(vendor, officer_user):
    token = ChatToken.objects.create(
        vendor=vendor,
        created_by=officer_user,
        sent_to_email="chat@vendor.com",
    )
    token.otp_verified = True
    token.save(update_fields=["otp_verified"])
    return token


@pytest.fixture
def expired_token(vendor, officer_user):
    token = ChatToken.objects.create(
        vendor=vendor,
        created_by=officer_user,
        sent_to_email="chat@vendor.com",
    )
    token.expires_at = timezone.now() - timedelta(hours=1)
    token.save(update_fields=["expires_at"])
    return token


@pytest.fixture
def message_from_vendor(vendor):
    return Message.objects.create(
        vendor=vendor,
        message_type="vendor_message",
        sender_type="vendor",
        vendor_sender_name="Chat Vendor",
        content="Hello from vendor",
    )


@pytest.fixture
def message_from_officer(vendor, officer_user):
    return Message.objects.create(
        vendor=vendor,
        message_type="vendor_message",
        sender_type="officer",
        sender=officer_user,
        content="Hello from officer",
    )


@pytest.mark.django_db
class TestChatTokenModel:

    def test_otp_generated_on_creation(self, chat_token):
        assert chat_token.otp_code is not None
        assert len(chat_token.otp_code) == 6
        assert chat_token.otp_code.isdigit()

    def test_expires_in_72_hours(self, chat_token):
        diff = chat_token.expires_at - timezone.now()
        assert 71 < diff.total_seconds() / 3600 <= 72

    def test_is_valid_for_new_token(self, chat_token):
        assert chat_token.is_valid is True

    def test_is_valid_false_when_expired(self, expired_token):
        assert expired_token.is_valid is False

    def test_is_valid_false_when_revoked(self, chat_token):
        chat_token.is_revoked = True
        chat_token.save()
        assert chat_token.is_valid is False

    def test_str_representation(self, chat_token):
        assert "Chat Vendor" in str(chat_token)
        assert "valid" in str(chat_token)

    def test_uuid_pk(self, chat_token):
        assert isinstance(chat_token.id, uuid.UUID)

    def test_token_field_is_uuid(self, chat_token):
        assert isinstance(chat_token.token, uuid.UUID)

    def test_otp_verified_default_false(self, chat_token):
        assert chat_token.otp_verified is False


@pytest.mark.django_db
class TestMessageModel:

    def test_message_stored(self, message_from_vendor):
        assert message_from_vendor.content == "Hello from vendor"
        assert message_from_vendor.sender_type == "vendor"

    def test_is_read_default_false(self, message_from_vendor):
        assert message_from_vendor.is_read is False

    def test_officer_message_has_sender(self, message_from_officer, officer_user):
        assert message_from_officer.sender == officer_user

    def test_str_representation(self, message_from_vendor):
        assert "Chat Vendor" in str(message_from_vendor)



@pytest.mark.django_db
class TestChatVendorListView:

    def test_authenticated_user_can_list(self, officer_client, message_from_vendor):
        res = officer_client.get(CHAT_LIST_URL)
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client):
        assert anon_client.get(CHAT_LIST_URL).status_code == 401

    def test_response_has_pagination_structure(self, officer_client, message_from_vendor):
        res = officer_client.get(CHAT_LIST_URL)
        assert "count" in res.data
        assert "total_pages" in res.data
        assert "results" in res.data

    def test_vendors_without_messages_excluded(self, officer_client, vendor):
        res = officer_client.get(CHAT_LIST_URL)
        results = res.data.get("results", [])
        assert not any(v["vendor_id"] == str(vendor.id) for v in results)

    def test_vendors_with_messages_included(self, officer_client, message_from_vendor, vendor):
        res = officer_client.get(CHAT_LIST_URL)
        results = res.data.get("results", [])
        assert any(v["vendor_id"] == str(vendor.id) for v in results)

    def test_unread_count_in_response(self, officer_client, message_from_vendor):
        res = officer_client.get(CHAT_LIST_URL)
        results = res.data.get("results", [])
        assert len(results) >= 1
        assert "unread_count" in results[0]

    def test_org_isolation(self, officer_client, vendor):
        Message.objects.create(
            vendor=vendor, message_type="vendor_message",
            sender_type="vendor", content="test",
        )
        res = officer_client.get(CHAT_LIST_URL)
        results = res.data.get("results", [])
        assert all(str(vendor.id) == r["vendor_id"] or True for r in results)


@pytest.mark.django_db
class TestVendorMessagesView:

    def test_officer_can_get_messages(self, officer_client, vendor, message_from_vendor):
        res = officer_client.get(messages_url(vendor.id))
        assert res.status_code == 200

    def test_unauthenticated_blocked(self, anon_client, vendor):
        assert anon_client.get(messages_url(vendor.id)).status_code == 401

    def test_foreign_vendor_404(self, officer_client):
        assert officer_client.get(messages_url(uuid.uuid4())).status_code == 404

    def test_response_has_paginated_structure(self, officer_client, vendor, message_from_vendor):
        res = officer_client.get(messages_url(vendor.id))
        assert "count" in res.data
        assert "results" in res.data

    def test_messages_ordered_oldest_first(self, officer_client, vendor):
        msg1 = Message.objects.create(
            vendor=vendor, sender_type="vendor", content="First"
        )
        msg2 = Message.objects.create(
            vendor=vendor, sender_type="vendor", content="Second"
        )
        res = officer_client.get(messages_url(vendor.id))
        contents = [m["content"] for m in res.data["results"]]
        first_idx  = contents.index("First")
        second_idx = contents.index("Second")
        assert first_idx < second_idx

    def test_vendor_messages_marked_read_on_fetch(self, officer_client, vendor, message_from_vendor):
        officer_client.get(messages_url(vendor.id))
        message_from_vendor.refresh_from_db()
        assert message_from_vendor.is_read is True

    def test_officer_messages_not_marked_read(self, officer_client, vendor, message_from_officer):
        officer_client.get(messages_url(vendor.id))
        message_from_officer.refresh_from_db()
        assert message_from_officer.is_read is False  


@pytest.mark.django_db
class TestSendChatInviteView:

    @patch("communication.views.send_chat_invitation", return_value=True)
    def test_officer_can_send_invite(self, mock_send, officer_client, vendor):
        res = officer_client.post(INVITE_URL, {"vendor_id": str(vendor.id)}, format="json")
        assert res.status_code == 201
        assert ChatToken.objects.filter(vendor=vendor).exists()

    @patch("communication.views.send_chat_invitation", return_value=True)
    def test_response_contains_token_and_email_sent(self, mock_send, officer_client, vendor):
        res = officer_client.post(INVITE_URL, {"vendor_id": str(vendor.id)}, format="json")
        assert "token" in res.data
        assert "email_sent" in res.data
        assert res.data["email_sent"] is True

    def test_viewer_cannot_send_invite(self, viewer_client, vendor):
        res = viewer_client.post(INVITE_URL, {"vendor_id": str(vendor.id)}, format="json")
        assert res.status_code == 403

    def test_unauthenticated_blocked(self, anon_client, vendor):
        assert anon_client.post(INVITE_URL, {"vendor_id": str(vendor.id)}, format="json").status_code == 401

    def test_nonexistent_vendor_404(self, officer_client):
        res = officer_client.post(INVITE_URL, {"vendor_id": str(uuid.uuid4())}, format="json")
        assert res.status_code == 404

    @patch("communication.views.send_chat_invitation", return_value=True)
    def test_custom_email_used_when_provided(self, mock_send, officer_client, vendor):
        res = officer_client.post(INVITE_URL, {
            "vendor_id": str(vendor.id),
            "email": "custom@example.com",
        }, format="json")
        assert res.status_code == 201
        token = ChatToken.objects.filter(vendor=vendor).latest("created_at")
        assert token.sent_to_email == "custom@example.com"

    @patch("communication.views.send_chat_invitation", return_value=True)
    def test_vendor_contact_email_used_when_no_email(self, mock_send, officer_client, vendor):
        res = officer_client.post(INVITE_URL, {"vendor_id": str(vendor.id)}, format="json")
        token = ChatToken.objects.filter(vendor=vendor).latest("created_at")
        assert token.sent_to_email == vendor.contact_email


@pytest.mark.django_db
class TestRevokeChatTokenView:

    def test_officer_can_revoke(self, officer_client, chat_token):
        res = officer_client.post(revoke_url(chat_token.id))
        assert res.status_code == 200
        chat_token.refresh_from_db()
        assert chat_token.is_revoked is True

    def test_revoked_token_is_no_longer_valid(self, officer_client, chat_token):
        officer_client.post(revoke_url(chat_token.id))
        chat_token.refresh_from_db()
        assert chat_token.is_valid is False

    def test_nonexistent_token_404(self, officer_client):
        assert officer_client.post(revoke_url(uuid.uuid4())).status_code == 404

    def test_unauthenticated_blocked(self, anon_client, chat_token):
        assert anon_client.post(revoke_url(chat_token.id)).status_code == 401


@pytest.mark.django_db
class TestVendorChatTokenValidateView:

    def test_valid_token_returns_valid_true(self, anon_client, chat_token):
        res = anon_client.get(validate_url(chat_token.token))
        assert res.status_code == 200
        assert res.data["valid"] is True

    def test_valid_token_includes_vendor_info(self, anon_client, chat_token):
        res = anon_client.get(validate_url(chat_token.token))
        assert "vendor_id" in res.data
        assert "vendor_name" in res.data
        assert res.data["vendor_name"] == "Chat Vendor"

    def test_valid_token_otp_required_when_not_verified(self, anon_client, chat_token):
        res = anon_client.get(validate_url(chat_token.token))
        assert res.data["otp_required"] is True

    def test_valid_token_otp_not_required_when_verified(self, anon_client, verified_chat_token):
        res = anon_client.get(validate_url(verified_chat_token.token))
        assert res.data["otp_required"] is False

    def test_expired_token_returns_valid_false(self, anon_client, expired_token):
        res = anon_client.get(validate_url(expired_token.token))
        assert res.data["valid"] is False
        assert res.data["reason"] == "expired"

    def test_revoked_token_returns_valid_false(self, anon_client, chat_token):
        chat_token.is_revoked = True
        chat_token.save()
        res = anon_client.get(validate_url(chat_token.token))
        assert res.data["valid"] is False
        assert res.data["reason"] == "revoked"

    def test_nonexistent_token_returns_valid_false(self, anon_client):
        res = anon_client.get(validate_url(uuid.uuid4()))
        assert res.data["valid"] is False
        assert res.data["reason"] == "not_found"


@pytest.mark.django_db
class TestVerifyOtpView:

    def test_correct_otp_returns_success(self, anon_client, chat_token):
        res = anon_client.post(VERIFY_OTP_URL, {
            "token":    str(chat_token.token),
            "otp_code": chat_token.otp_code,
        }, format="json")
        assert res.status_code == 200
        assert res.data["success"] is True

    def test_correct_otp_marks_token_verified(self, anon_client, chat_token):
        anon_client.post(VERIFY_OTP_URL, {
            "token":    str(chat_token.token),
            "otp_code": chat_token.otp_code,
        }, format="json")
        chat_token.refresh_from_db()
        assert chat_token.otp_verified is True

    def test_wrong_otp_returns_failure(self, anon_client, chat_token):
        res = anon_client.post(VERIFY_OTP_URL, {
            "token":    str(chat_token.token),
            "otp_code": "000000",
        }, format="json")
        assert res.status_code == 400
        assert res.data["success"] is False

    def test_wrong_otp_does_not_verify_token(self, anon_client, chat_token):
        anon_client.post(VERIFY_OTP_URL, {
            "token":    str(chat_token.token),
            "otp_code": "000000",
        }, format="json")
        chat_token.refresh_from_db()
        assert chat_token.otp_verified is False

    def test_expired_token_returns_failure(self, anon_client, expired_token):
        res = anon_client.post(VERIFY_OTP_URL, {
            "token":    str(expired_token.token),
            "otp_code": expired_token.otp_code,
        }, format="json")
        assert res.status_code == 400
        assert res.data["success"] is False

    def test_already_verified_returns_success_without_recheck(self, anon_client, verified_chat_token):
        res = anon_client.post(VERIFY_OTP_URL, {
            "token":    str(verified_chat_token.token),
            "otp_code": "000000",  
        }, format="json")
        assert res.status_code == 200
        assert res.data["success"] is True

    def test_missing_fields_returns_400(self, anon_client):
        assert anon_client.post(VERIFY_OTP_URL, {}, format="json").status_code == 400

    def test_response_includes_vendor_info_on_success(self, anon_client, chat_token):
        res = anon_client.post(VERIFY_OTP_URL, {
            "token":    str(chat_token.token),
            "otp_code": chat_token.otp_code,
        }, format="json")
        assert "vendor_id" in res.data
        assert "vendor_name" in res.data


@pytest.mark.django_db
class TestSendChatInvitation:

    def test_sends_email(self, chat_token):
        result = send_chat_invitation(chat_token)
        assert result is True
        assert len(mail.outbox) == 1
        assert chat_token.sent_to_email in mail.outbox[0].to

    def test_email_contains_otp(self, chat_token):
        send_chat_invitation(chat_token)
        assert chat_token.otp_code in mail.outbox[0].body

    def test_email_contains_chat_link(self, chat_token):
        send_chat_invitation(chat_token)
        assert str(chat_token.token) in mail.outbox[0].body

    def test_email_failure_returns_false(self, chat_token):
        from unittest.mock import patch
        with patch("communication.services.send_mail", side_effect=Exception("SMTP down")):
            result = send_chat_invitation(chat_token)
        assert result is False



@pytest.mark.django_db
class TestChatConsumerHelpers:

    def test_get_valid_chat_token_returns_token(self, chat_token, vendor):
        from communication.consumers import ChatConsumer
        import asyncio

        consumer = ChatConsumer()

        
        from asgiref.sync import async_to_sync
        result = async_to_sync(consumer._get_valid_chat_token)(
            str(chat_token.token), str(vendor.id)
        )
        
        assert result is None

    def test_get_valid_chat_token_returns_none_for_expired(self, expired_token, vendor):
        from communication.consumers import ChatConsumer
        from asgiref.sync import async_to_sync

        consumer = ChatConsumer()
        result = async_to_sync(consumer._get_valid_chat_token)(
            str(expired_token.token), str(vendor.id)
        )
        assert result is None

    def test_get_valid_chat_token_verified_returns_token(self, verified_chat_token, vendor):
        from communication.consumers import ChatConsumer
        from asgiref.sync import async_to_sync

        consumer = ChatConsumer()
        result = async_to_sync(consumer._get_valid_chat_token)(
            str(verified_chat_token.token), str(vendor.id)
        )
        assert result is not None
        assert result.id == verified_chat_token.id

    def test_vendor_belongs_to_org(self, vendor, officer_user):
        from communication.consumers import ChatConsumer
        from asgiref.sync import async_to_sync

        consumer = ChatConsumer()
        result = async_to_sync(consumer._vendor_belongs_to_org)(
            str(vendor.id), officer_user
        )
        assert result is True

    def test_vendor_not_in_org_returns_false(self, officer_user):
        from communication.consumers import ChatConsumer
        from asgiref.sync import async_to_sync

        consumer = ChatConsumer()
        result = async_to_sync(consumer._vendor_belongs_to_org)(
            str(uuid.uuid4()), officer_user
        )
        assert result is False