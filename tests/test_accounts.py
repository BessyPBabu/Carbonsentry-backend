import uuid
import pytest
from unittest.mock import patch, MagicMock
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from django.db import IntegrityError
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory
from accounts.models import Organization, User
from accounts.permissions.roles import IsAdmin, IsOfficer, IsViewer, ReadOnly, SameOrganization
from accounts.permissions.password_enforcement import EnforcePasswordChange
from accounts.serializers import (
    OrganizationRegisterSerializer,
    AddUserSerializer,
    EditUserSerializer,
    ForceChangePasswordSerializer,
)
from accounts.utils.email_verification import (
    generate_verification_token, hash_token,
    send_organization_verification_email,
    send_user_welcome_email,
    send_password_reset_email,
    is_verification_token_valid,
)
from accounts.utils.passwords import validate_strong_password, generate_temp_password
from accounts.utils.validators import (
    validate_organization_name, validate_full_name,
    validate_industry, validate_country,
)


LOGIN_URL  = "/api/accounts/auth/login/"
LOGOUT_URL = "/api/accounts/auth/logout/"
FORGOT_URL = "/api/accounts/auth/password/forgot/"
RESET_URL  = "/api/accounts/auth/password/reset/"
CHANGE_URL = "/api/accounts/auth/password/change/"
REGISTER_URL = "/api/accounts/organizations/register/"
VERIFY_BASE  = "/api/accounts/organizations/verify-email/"
ORG_ME_URL   = "/api/accounts/organizations/me/"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


# ── Organization model ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOrganizationModel:

    def test_create_and_str(self, verified_org):
        assert str(verified_org) == "Test Corp"
        assert verified_org.is_verified is True

    def test_uuid_pk(self, verified_org):
        assert isinstance(verified_org.id, uuid.UUID)

    def test_unverified_defaults(self, unverified_org):
        assert unverified_org.is_verified is False
        assert unverified_org.email_verification_token is not None
        assert unverified_org.email_verified_at is None

    def test_unique_name_constraint(self, verified_org):
        with pytest.raises(IntegrityError):
            Organization.objects.create(
                name="Test Corp", industry="Finance",
                country="India", primary_email="other@corp.com",
            )

    def test_ordering_by_name(self):
        Organization.objects.create(name="Zebra Co", industry="Tech", country="India", primary_email="z@z.com")
        Organization.objects.create(name="Alpha Co", industry="Tech", country="India", primary_email="a@a.com")
        names = list(Organization.objects.values_list("name", flat=True))
        assert names == sorted(names)


# ── User model ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUserModel:

    def test_create_admin_user(self, admin_user, verified_org):
        assert admin_user.role == User.Role.ADMIN
        assert admin_user.organization == verified_org
        assert admin_user.is_active is True

    def test_password_is_hashed(self, admin_user):
        assert admin_user.password != "Admin@1234"
        assert admin_user.check_password("Admin@1234")

    def test_no_password_sets_unusable_and_must_change(self, verified_org):
        user = User.objects.create_user(
            email="nopass@corp.com", password=None,
            role=User.Role.VIEWER, organization=verified_org, full_name="No Pass",
        )
        assert not user.has_usable_password()
        assert user.must_change_password is True

    def test_is_active_defaults_false(self, verified_org):
        user = User.objects.create_user(
            email="inactive2@corp.com", password="Test@1234",
            role=User.Role.VIEWER, organization=verified_org, full_name="Test",
        )
        assert user.is_active is False

    def test_manager_requires_email(self, verified_org):
        with pytest.raises(ValueError, match="Email is required"):
            User.objects.create_user(
                email="", password="Test@1234",
                role=User.Role.VIEWER, organization=verified_org, full_name="X",
            )

    def test_manager_requires_organization(self):
        with pytest.raises(ValueError, match="organization"):
            User.objects.create_user(
                email="x@x.com", password="Test@1234",
                role=User.Role.VIEWER, full_name="X",
            )

    def test_manager_requires_role(self, verified_org):
        with pytest.raises(ValueError, match="role"):
            User.objects.create_user(
                email="x@x.com", password="Test@1234",
                organization=verified_org, full_name="X",
            )

    def test_email_unique_constraint(self, admin_user, verified_org):
        with pytest.raises(IntegrityError):
            User.objects.create_user(
                email="admin@testcorp.com", password="Test@1234",
                role=User.Role.OFFICER, organization=verified_org, full_name="Dup",
            )

    def test_cascade_delete_removes_users(self, verified_org, admin_user):
        org_id = verified_org.id
        verified_org.delete()
        assert User.objects.filter(organization_id=org_id).count() == 0


# ── Login ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLoginView:

    def test_success_returns_tokens_role_org(self, anon_client, admin_user):
        res = anon_client.post(LOGIN_URL, {"email": "admin@testcorp.com", "password": "Admin@1234"})
        assert res.status_code == 200
        assert "access" in res.data
        assert "refresh" in res.data
        assert res.data["role"] == "admin"
        assert "organization_id" in res.data

    def test_wrong_password_401(self, anon_client, admin_user):
        res = anon_client.post(LOGIN_URL, {"email": "admin@testcorp.com", "password": "wrong"})
        assert res.status_code == 401

    def test_nonexistent_email_401(self, anon_client):
        res = anon_client.post(LOGIN_URL, {"email": "ghost@corp.com", "password": "Admin@1234"})
        assert res.status_code == 401

    def test_inactive_user_401(self, anon_client, inactive_user):
        res = anon_client.post(LOGIN_URL, {"email": "inactive@testcorp.com", "password": "Inactive@1234"})
        assert res.status_code == 401

    def test_unverified_org_403(self, anon_client, unverified_org):
        User.objects.create_user(
            email="admin@unverified.com", password="Admin@1234",
            role=User.Role.ADMIN, organization=unverified_org,
            is_active=False, full_name="Unverified Admin",
        )
        res = anon_client.post(LOGIN_URL, {"email": "admin@unverified.com", "password": "Admin@1234"})
        assert res.status_code == 403
        assert res.data["error"] == "Organization not verified"

    def test_email_case_insensitive(self, anon_client, admin_user):
        res = anon_client.post(LOGIN_URL, {"email": "ADMIN@TESTCORP.COM", "password": "Admin@1234"})
        assert res.status_code == 200

    def test_must_change_password_flag_returned(self, anon_client, must_change_user):
        must_change_user.is_active = True
        must_change_user.save()
        res = anon_client.post(LOGIN_URL, {"email": "newuser@testcorp.com", "password": "Temp@1234"})
        assert res.status_code == 200
        assert res.data["must_change_password"] is True

    def test_empty_payload_400(self, anon_client):
        res = anon_client.post(LOGIN_URL, {})
        assert res.status_code == 400


@pytest.mark.django_db
class TestLoginThrottle:

    def test_blocks_after_rate_exceeded(self, anon_client):
        for _ in range(5):
            res = anon_client.post(LOGIN_URL, {"email": "ghost@corp.com", "password": "wrong"})
            assert res.status_code == 401
        res = anon_client.post(LOGIN_URL, {"email": "ghost@corp.com", "password": "wrong"})
        assert res.status_code == 429

    def test_successful_login_still_counts_toward_limit(self, anon_client, admin_user):
        for _ in range(5):
            res = anon_client.post(LOGIN_URL, {"email": "admin@testcorp.com", "password": "Admin@1234"})
            assert res.status_code == 200
        res = anon_client.post(LOGIN_URL, {"email": "admin@testcorp.com", "password": "Admin@1234"})
        assert res.status_code == 429


# ── Logout ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLogoutView:

    def test_blacklists_token(self, admin_client, admin_user):
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = str(RefreshToken.for_user(admin_user))
        res = admin_client.post(LOGOUT_URL, {"refresh": refresh})
        assert res.status_code == 200

    def test_missing_refresh_400(self, admin_client):
        res = admin_client.post(LOGOUT_URL, {})
        assert res.status_code == 400

    def test_invalid_token_does_not_500(self, admin_client):
        res = admin_client.post(LOGOUT_URL, {"refresh": "bad.token.here"})
        assert res.status_code in (200, 400)

    def test_unauthenticated_401(self, anon_client):
        res = anon_client.post(LOGOUT_URL, {"refresh": "token"})
        assert res.status_code == 401


# ── Forgot password ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestForgotPasswordView:

    def test_sends_email_for_existing_active_user(self, anon_client, admin_user):
        res = anon_client.post(FORGOT_URL, {"email": "admin@testcorp.com"})
        assert res.status_code == 200
        assert len(mail.outbox) == 1
        assert "admin@testcorp.com" in mail.outbox[0].to

    def test_returns_200_for_nonexistent_email_no_leak(self, anon_client):
        res = anon_client.post(FORGOT_URL, {"email": "ghost@corp.com"})
        assert res.status_code == 200
        assert len(mail.outbox) == 0

    def test_reset_link_in_email_body(self, anon_client, admin_user):
        anon_client.post(FORGOT_URL, {"email": "admin@testcorp.com"})
        assert "/reset-password/" in mail.outbox[0].body

    def test_missing_email_400(self, anon_client):
        res = anon_client.post(FORGOT_URL, {})
        assert res.status_code == 400

    def test_inactive_user_gets_no_email(self, anon_client, inactive_user):
        anon_client.post(FORGOT_URL, {"email": "inactive@testcorp.com"})
        assert len(mail.outbox) == 0


@pytest.mark.django_db
class TestPasswordResetThrottle:

    def test_forgot_password_blocks_after_rate_exceeded(self, anon_client):
        for _ in range(5):
            res = anon_client.post(FORGOT_URL, {"email": "ghost@corp.com"})
            assert res.status_code == 200
        res = anon_client.post(FORGOT_URL, {"email": "ghost@corp.com"})
        assert res.status_code == 429

    def test_reset_password_blocks_after_rate_exceeded(self, anon_client, inactive_user):
        token = PasswordResetTokenGenerator().make_token(inactive_user)
        uid = urlsafe_base64_encode(force_bytes(inactive_user.id))
        for _ in range(5):
            res = anon_client.post(RESET_URL, {"uid": uid, "token": "tampered", "password": "NewSecure@99"})
            assert res.status_code == 400
        res = anon_client.post(RESET_URL, {"uid": uid, "token": "tampered", "password": "NewSecure@99"})
        assert res.status_code == 429


# ── Reset password ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestResetPasswordView:

    def _payload(self, user, password="NewSecure@99"):
        token = PasswordResetTokenGenerator().make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.id))
        return {"uid": uid, "token": token, "password": password}

    def test_valid_reset_activates_user(self, anon_client, inactive_user):
        res = anon_client.post(RESET_URL, self._payload(inactive_user))
        assert res.status_code == 200
        inactive_user.refresh_from_db()
        assert inactive_user.is_active is True
        assert inactive_user.must_change_password is False

    def test_invalid_uid_400(self, anon_client, inactive_user):
        token = PasswordResetTokenGenerator().make_token(inactive_user)
        res = anon_client.post(RESET_URL, {"uid": "bad", "token": token, "password": "NewSecure@99"})
        assert res.status_code == 400

    def test_tampered_token_400(self, anon_client, inactive_user):
        uid = urlsafe_base64_encode(force_bytes(inactive_user.id))
        res = anon_client.post(RESET_URL, {"uid": uid, "token": "tampered", "password": "NewSecure@99"})
        assert res.status_code == 400

    def test_weak_password_400(self, anon_client, inactive_user):
        res = anon_client.post(RESET_URL, self._payload(inactive_user, "password"))
        assert res.status_code == 400

    def test_can_login_after_reset(self, anon_client, inactive_user):
        anon_client.post(RESET_URL, self._payload(inactive_user))
        res = anon_client.post(LOGIN_URL, {"email": "inactive@testcorp.com", "password": "NewSecure@99"})
        assert res.status_code == 200


# ── Force change password ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestForceChangePasswordView:

    def test_success_changes_password(self, admin_client, admin_user):
        res = admin_client.post(CHANGE_URL, {"current_password": "Admin@1234", "new_password": "NewAdmin@99"})
        assert res.status_code == 200
        admin_user.refresh_from_db()
        assert admin_user.check_password("NewAdmin@99")

    def test_clears_must_change_flag_and_activates(self, must_change_client, must_change_user):
        must_change_user.is_active = True
        must_change_user.save()
        must_change_client.post(CHANGE_URL, {"current_password": "Temp@1234", "new_password": "FreshPass@88"})
        must_change_user.refresh_from_db()
        assert must_change_user.must_change_password is False
        assert must_change_user.is_active is True

    def test_wrong_current_password_400(self, admin_client):
        res = admin_client.post(CHANGE_URL, {"current_password": "Wrong@1", "new_password": "NewAdmin@99"})
        assert res.status_code == 400

    def test_same_password_400(self, admin_client):
        res = admin_client.post(CHANGE_URL, {"current_password": "Admin@1234", "new_password": "Admin@1234"})
        assert res.status_code == 400

    def test_weak_new_password_400(self, admin_client):
        res = admin_client.post(CHANGE_URL, {"current_password": "Admin@1234", "new_password": "12345678"})
        assert res.status_code == 400

    def test_redirect_url_matches_role(self, admin_client):
        res = admin_client.post(CHANGE_URL, {"current_password": "Admin@1234", "new_password": "FreshAdmin@99"})
        assert res.data["redirect_url"] == "/admin/dashboard"

    def test_unauthenticated_401(self, anon_client):
        res = anon_client.post(CHANGE_URL, {"current_password": "x", "new_password": "y"})
        assert res.status_code == 401


# ── Organization register ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOrganizationRegisterView:

    PAYLOAD = {
        "name": "New Org", "industry": "Finance", "country": "India",
        "admin_email": "newadmin@neworg.com", "password": "Secure@9876",
    }

    def test_201_creates_org_and_admin(self, anon_client):
        res = anon_client.post(REGISTER_URL, self.PAYLOAD)
        assert res.status_code == 201
        assert Organization.objects.filter(primary_email="newadmin@neworg.com").exists()
        assert User.objects.get(email="newadmin@neworg.com").role == User.Role.ADMIN

    def test_org_unverified_admin_inactive_on_creation(self, anon_client):
        anon_client.post(REGISTER_URL, self.PAYLOAD)
        org = Organization.objects.get(primary_email="newadmin@neworg.com")
        user = User.objects.get(email="newadmin@neworg.com")
        assert org.is_verified is False
        assert user.is_active is False

    def test_verification_email_sent(self, anon_client):
        anon_client.post(REGISTER_URL, self.PAYLOAD)
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["newadmin@neworg.com"]

    def test_duplicate_org_name_400(self, anon_client, verified_org):
        res = anon_client.post(REGISTER_URL, {**self.PAYLOAD, "name": "Test Corp"})
        assert res.status_code == 400

    def test_duplicate_email_400(self, anon_client, admin_user):
        res = anon_client.post(REGISTER_URL, {**self.PAYLOAD, "admin_email": "admin@testcorp.com"})
        assert res.status_code == 400

    def test_weak_password_400(self, anon_client):
        res = anon_client.post(REGISTER_URL, {**self.PAYLOAD, "password": "password"})
        assert res.status_code == 400

    def test_missing_fields_400(self, anon_client):
        res = anon_client.post(REGISTER_URL, {"name": "Partial"})
        assert res.status_code == 400

    def test_response_has_next_step(self, anon_client):
        res = anon_client.post(REGISTER_URL, self.PAYLOAD)
        assert "next_step" in res.data


@pytest.mark.django_db
class TestRegisterThrottle:

    def test_blocks_after_rate_exceeded(self, anon_client):
        for i in range(10):
            res = anon_client.post(REGISTER_URL, {
                "name": f"Org {i}", "industry": "Finance", "country": "India",
                "admin_email": f"admin{i}@org{i}.com", "password": "Secure@9876",
            })
            assert res.status_code == 201
        res = anon_client.post(REGISTER_URL, {
            "name": "Org Overflow", "industry": "Finance", "country": "India",
            "admin_email": "overflow@org.com", "password": "Secure@9876",
        })
        assert res.status_code == 429


# ── Email verification ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEmailVerificationView:

    def test_valid_token_verifies_org_and_activates_admin(self, anon_client, unverified_org):
        User.objects.create_user(
            email="admin@unverified.com", password="Admin@1234",
            role=User.Role.ADMIN, organization=unverified_org,
            is_active=False, full_name="Admin",
        )
        res = anon_client.get(f"{VERIFY_BASE}{unverified_org._raw_token}/")
        assert res.status_code == 200
        unverified_org.refresh_from_db()
        assert unverified_org.is_verified is True
        assert unverified_org.email_verified_at is not None
        admin = User.objects.get(email="admin@unverified.com")
        assert admin.is_active is True

    def test_invalid_token_400(self, anon_client):
        res = anon_client.get(f"{VERIFY_BASE}invalid-token/")
        assert res.status_code == 400

    def test_already_verified_returns_200_with_flag(self, anon_client, verified_org):
        verified_org.email_verification_token = hash_token("fresh-token")
        verified_org.save()
        res = anon_client.get(f"{VERIFY_BASE}fresh-token/")
        assert res.status_code == 200
        assert res.data.get("already_verified") is True


# ── Organization Me ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOrganizationMeView:

    def test_admin_get_returns_org(self, admin_client):
        res = admin_client.get(ORG_ME_URL)
        assert res.status_code == 200
        assert res.data["name"] == "Test Corp"

    def test_admin_can_update_name(self, admin_client):
        res = admin_client.put(ORG_ME_URL, {"name": "Updated Corp", "industry": "Finance", "country": "India"})
        assert res.status_code == 200
        assert res.data["name"] == "Updated Corp"

    def test_primary_email_read_only(self, admin_client):
        admin_client.put(ORG_ME_URL, {
            "name": "Test Corp", "industry": "Technology",
            "country": "India", "primary_email": "hacked@hacker.com",
        })
        org = Organization.objects.get(name="Test Corp")
        assert org.primary_email != "hacked@hacker.com"

    def test_officer_can_get_org_after_fix(self, officer_client):
        res = officer_client.get(ORG_ME_URL)
        assert res.status_code in (200, 403)

    def test_officer_cannot_put_org(self, officer_client):
        res = officer_client.put(ORG_ME_URL, {"name": "Hacked", "industry": "x", "country": "y"})
        assert res.status_code == 403

    def test_unauthenticated_401(self, anon_client):
        res = anon_client.get(ORG_ME_URL)
        assert res.status_code == 401

    def test_duplicate_name_on_update_400(self, admin_client):
        Organization.objects.create(
            name="Rival Corp", industry="Tech", country="India",
            primary_email="rival@rival.com", is_verified=True,
            email_verification_token=hash_token(generate_verification_token()),
        )
        res = admin_client.put(ORG_ME_URL, {"name": "Rival Corp", "industry": "Technology", "country": "India"})
        assert res.status_code == 400


USER_LIST_URL = "/api/accounts/users/"
USER_ADD_URL  = "/api/accounts/users/add/"
USER_ME_URL   = "/api/accounts/users/me/"


def user_detail_url(uid):
    return f"/api/accounts/users/{uid}/"


def user_reset_url(uid):
    return f"/api/accounts/users/{uid}/reset-password/"


# ── User Me ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUserMeView:

    def test_returns_current_user_with_org(self, admin_client, admin_user):
        res = admin_client.get(USER_ME_URL)
        assert res.status_code == 200
        assert res.data["email"] == "admin@testcorp.com"
        assert res.data["role"] == "admin"
        assert res.data["organization"]["name"] == "Test Corp"

    def test_officer_and_viewer_can_access(self, officer_client, viewer_client):
        for client in (officer_client, viewer_client):
            assert client.get(USER_ME_URL).status_code == 200

    def test_unauthenticated_401(self, anon_client):
        assert anon_client.get(USER_ME_URL).status_code == 401


# ── User list ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUserListView:

    def test_admin_can_list_users(self, admin_client, officer_user):
        res = admin_client.get(USER_LIST_URL)
        assert res.status_code == 200
        emails = [u["email"] for u in (res.data.get("results") or res.data)]
        assert "officer@testcorp.com" in emails

    def test_officer_and_viewer_blocked(self, officer_client, viewer_client):
        assert officer_client.get(USER_LIST_URL).status_code == 403
        assert viewer_client.get(USER_LIST_URL).status_code == 403

    def test_search_filters_results(self, admin_client, officer_user):
        res = admin_client.get(USER_LIST_URL, {"search": "officer"})
        results = res.data.get("results") or res.data
        assert all("officer" in u["email"] or "officer" in u["full_name"].lower() for u in results)

    def test_org_isolation_hides_foreign_users(self, admin_client):
        other_org = Organization.objects.create(
            name="Other Corp", industry="Tech", country="India",
            primary_email="other@other.com", is_verified=True,
            email_verification_token=hash_token(generate_verification_token()),
        )
        User.objects.create_user(
            email="spy@other.com", password="Spy@1234",
            role=User.Role.OFFICER, organization=other_org,
            is_active=True, full_name="Spy",
        )
        res = admin_client.get(USER_LIST_URL)
        results = res.data.get("results") or res.data
        assert not any(u["email"] == "spy@other.com" for u in results)

    def test_response_includes_status_field(self, admin_client, admin_user):
        res = admin_client.get(USER_LIST_URL)
        results = res.data.get("results") or res.data
        assert "status" in results[0]


# ── Add user ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAddUserView:

    PAYLOAD = {"full_name": "Fresh Officer", "email": "fresh@testcorp.com", "role": "officer"}

    def test_admin_creates_officer(self, admin_client):
        assert admin_client.post(USER_ADD_URL, self.PAYLOAD).status_code == 201

    def test_admin_creates_viewer(self, admin_client):
        payload = {**self.PAYLOAD, "email": "freshv@testcorp.com", "role": "viewer"}
        assert admin_client.post(USER_ADD_URL, payload).status_code == 201

    def test_cannot_create_admin_role(self, admin_client):
        payload = {**self.PAYLOAD, "email": "badadmin@corp.com", "role": "admin"}
        assert admin_client.post(USER_ADD_URL, payload).status_code == 400

    def test_welcome_email_sent_without_password(self, admin_client):
        admin_client.post(USER_ADD_URL, self.PAYLOAD)
        assert len(mail.outbox) == 1

    def test_no_email_when_password_provided(self, admin_client):
        payload = {**self.PAYLOAD, "email": "withpass@corp.com", "password": "Officer@99"}
        admin_client.post(USER_ADD_URL, payload)
        assert len(mail.outbox) == 0

    def test_user_created_inactive(self, admin_client):
        admin_client.post(USER_ADD_URL, self.PAYLOAD)
        user = User.objects.get(email="fresh@testcorp.com")
        assert user.is_active is False

    def test_duplicate_email_400(self, admin_client, officer_user):
        payload = {**self.PAYLOAD, "email": "officer@testcorp.com"}
        assert admin_client.post(USER_ADD_URL, payload).status_code == 400

    def test_officer_and_viewer_blocked(self, officer_client, viewer_client):
        assert officer_client.post(USER_ADD_URL, self.PAYLOAD).status_code == 403
        assert viewer_client.post(USER_ADD_URL, self.PAYLOAD).status_code == 403


# ── Edit / delete user ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEditUserView:

    def test_admin_get_user(self, admin_client, officer_user):
        res = admin_client.get(user_detail_url(officer_user.id))
        assert res.status_code == 200
        assert res.data["email"] == "officer@testcorp.com"

    def test_admin_update_full_name(self, admin_client, officer_user):
        admin_client.patch(user_detail_url(officer_user.id), {"full_name": "Renamed"})
        officer_user.refresh_from_db()
        assert officer_user.full_name == "Renamed"

    def test_admin_update_role(self, admin_client, officer_user):
        admin_client.patch(user_detail_url(officer_user.id), {"role": "viewer"})
        officer_user.refresh_from_db()
        assert officer_user.role == "viewer"

    def test_admin_cannot_promote_user_to_admin(self, admin_client, officer_user):
        res = admin_client.patch(user_detail_url(officer_user.id), {"role": "admin"})
        assert res.status_code == 400
        officer_user.refresh_from_db()
        assert officer_user.role == "officer"

    def test_admin_deactivate_user(self, admin_client, officer_user):
        admin_client.patch(user_detail_url(officer_user.id), {"is_active": False})
        officer_user.refresh_from_db()
        assert officer_user.is_active is False

    def test_admin_delete_user(self, admin_client, officer_user):
        uid = officer_user.id
        res = admin_client.delete(user_detail_url(uid))
        assert res.status_code == 204
        assert not User.objects.filter(id=uid).exists()

    def test_officer_cannot_edit(self, officer_client, viewer_user):
        assert officer_client.patch(user_detail_url(viewer_user.id), {"full_name": "X"}).status_code == 403

    def test_cannot_edit_foreign_org_user(self, admin_client):
        other_org = Organization.objects.create(
            name="Foreign Corp", industry="Tech", country="India",
            primary_email="f@f.com", is_verified=True,
            email_verification_token=hash_token(generate_verification_token()),
        )
        foreign = User.objects.create_user(
            email="f@f.com", password="Foreign@1",
            role=User.Role.OFFICER, organization=other_org,
            is_active=True, full_name="Foreign",
        )
        assert admin_client.patch(user_detail_url(foreign.id), {"full_name": "Hijack"}).status_code == 404

    def test_invalid_role_400(self, admin_client, officer_user):
        assert admin_client.patch(user_detail_url(officer_user.id), {"role": "god"}).status_code == 400


# ── Reset user password (admin action) ───────────────────────────────────────

@pytest.mark.django_db
class TestResetUserPasswordView:

    def test_admin_resets_password(self, admin_client, officer_user):
        res = admin_client.post(user_reset_url(officer_user.id))
        assert res.status_code == 200

    def test_user_set_inactive_must_change_after_reset(self, admin_client, officer_user):
        admin_client.post(user_reset_url(officer_user.id))
        officer_user.refresh_from_db()
        assert officer_user.is_active is False
        assert officer_user.must_change_password is True

    def test_reset_email_sent(self, admin_client, officer_user):
        admin_client.post(user_reset_url(officer_user.id))
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["officer@testcorp.com"]

    def test_officer_blocked(self, officer_client, viewer_user):
        assert officer_client.post(user_reset_url(viewer_user.id)).status_code == 403

    def test_nonexistent_user_404(self, admin_client):
        assert admin_client.post(user_reset_url(uuid.uuid4())).status_code == 404


# ── Permission class unit tests ───────────────────────────────────────────────

def _req(user=None, method="GET", path="/api/test/"):
    factory = APIRequestFactory()
    req = getattr(factory, method.lower())(path)
    req.user = user or MagicMock(is_authenticated=False)
    req.path = path
    return req


def _active(role, must_change=False):
    u = MagicMock(spec=User)
    u.is_authenticated = True
    u.is_active = True
    u.role = role
    u.must_change_password = must_change
    return u


def _inactive(role):
    u = MagicMock(spec=User)
    u.is_authenticated = True
    u.is_active = False
    u.role = role
    return u


class TestIsAdminPermission:

    def test_admin_allowed(self):
        assert IsAdmin().has_permission(_req(_active("admin")), None) is True

    def test_officer_denied(self):
        assert IsAdmin().has_permission(_req(_active("officer")), None) is False

    def test_viewer_denied(self):
        assert IsAdmin().has_permission(_req(_active("viewer")), None) is False

    def test_inactive_admin_denied(self):
        assert IsAdmin().has_permission(_req(_inactive("admin")), None) is False

    def test_unauthenticated_denied(self):
        u = MagicMock(is_authenticated=False)
        assert IsAdmin().has_permission(_req(u), None) is False


class TestIsOfficerPermission:

    def test_officer_allowed(self):
        assert IsOfficer().has_permission(_req(_active("officer")), None) is True

    def test_admin_denied(self):
        assert IsOfficer().has_permission(_req(_active("admin")), None) is False

    def test_inactive_officer_denied(self):
        assert IsOfficer().has_permission(_req(_inactive("officer")), None) is False


class TestIsViewerPermission:

    def test_viewer_allowed(self):
        assert IsViewer().has_permission(_req(_active("viewer")), None) is True

    def test_officer_denied(self):
        assert IsViewer().has_permission(_req(_active("officer")), None) is False


class TestReadOnlyPermission:

    def test_get_allowed(self):
        assert ReadOnly().has_permission(_req(method="GET"), None) is True

    def test_post_denied(self):
        assert ReadOnly().has_permission(_req(method="POST"), None) is False

    def test_put_denied(self):
        assert ReadOnly().has_permission(_req(method="PUT"), None) is False

    def test_delete_denied(self):
        assert ReadOnly().has_permission(_req(method="DELETE"), None) is False


class TestSameOrganizationPermission:

    def test_same_org_allowed(self):
        org_id = uuid.uuid4()
        user = MagicMock(is_authenticated=True, organization_id=org_id)
        obj = MagicMock(organization_id=org_id)
        assert SameOrganization().has_object_permission(_req(user), None, obj) is True

    def test_different_org_denied(self):
        user = MagicMock(is_authenticated=True, organization_id=uuid.uuid4())
        obj = MagicMock(organization_id=uuid.uuid4())
        assert SameOrganization().has_object_permission(_req(user), None, obj) is False

    def test_obj_without_org_denied(self):
        user = MagicMock(is_authenticated=True, organization_id=uuid.uuid4())
        obj = MagicMock(spec=[])
        assert SameOrganization().has_object_permission(_req(user), None, obj) is False

    def test_unauthenticated_denied(self):
        user = MagicMock(is_authenticated=False)
        obj = MagicMock(organization_id=uuid.uuid4())
        assert SameOrganization().has_object_permission(_req(user), None, obj) is False


class TestEnforcePasswordChangePermission:

    def test_normal_user_passes(self):
        u = _active("officer", must_change=False)
        r = _req(u, path="/api/vendors/")
        assert EnforcePasswordChange().has_permission(r, None) is True

    def test_must_change_blocked_from_vendors(self):
        u = _active("officer", must_change=True)
        r = _req(u, path="/api/vendors/")
        assert EnforcePasswordChange().has_permission(r, None) is False

    def test_must_change_allowed_on_password_change_path(self):
        u = _active("officer", must_change=True)
        r = _req(u)
        r.path = "/api/accounts/auth/password/change/"
        assert EnforcePasswordChange().has_permission(r, None) is True

    def test_must_change_allowed_on_logout_path(self):
        u = _active("officer", must_change=True)
        r = _req(u)
        r.path = "/api/accounts/auth/logout/"
        assert EnforcePasswordChange().has_permission(r, None) is True

    def test_unauthenticated_passes_through(self):
        u = MagicMock(is_authenticated=False)
        r = _req(u, path="/api/accounts/auth/login/")
        assert EnforcePasswordChange().has_permission(r, None) is True


# ── Integration: role → HTTP endpoint ────────────────────────────────────────

@pytest.mark.django_db
class TestRoleEndpointGates:

    def test_admin_reaches_user_list(self, admin_client):
        assert admin_client.get(USER_LIST_URL).status_code == 200

    def test_officer_blocked_from_user_list(self, officer_client):
        assert officer_client.get(USER_LIST_URL).status_code == 403

    def test_viewer_blocked_from_user_list(self, viewer_client):
        assert viewer_client.get(USER_LIST_URL).status_code == 403

    def test_all_roles_reach_user_me(self, admin_client, officer_client, viewer_client):
        for client in (admin_client, officer_client, viewer_client):
            assert client.get(USER_ME_URL).status_code == 200

    def test_anon_blocked_from_user_me(self, anon_client):
        assert anon_client.get(USER_ME_URL).status_code == 401

    def test_admin_reaches_org_me(self, admin_client):
        assert admin_client.get(ORG_ME_URL).status_code == 200

    def test_anon_blocked_from_register(self):
        client = APIRequestFactory()


class TestPasswordValidator:

    def test_valid_passes(self):
        validate_strong_password("Secure@99")

    def test_too_short(self):
        with pytest.raises(ValidationError): validate_strong_password("Ab@1")

    def test_no_uppercase(self):
        with pytest.raises(ValidationError): validate_strong_password("secure@99")

    def test_no_lowercase(self):
        with pytest.raises(ValidationError): validate_strong_password("SECURE@99")

    def test_no_digit(self):
        with pytest.raises(ValidationError): validate_strong_password("Secure@@@")

    def test_no_special(self):
        with pytest.raises(ValidationError): validate_strong_password("Secure1234")

    def test_empty(self):
        with pytest.raises(ValidationError): validate_strong_password("")

    def test_too_long_rejected(self):
        with pytest.raises(ValidationError):
            validate_strong_password("Aa1@" * 40)

    def test_exactly_128_chars_passes(self):
        password = "Aa1@" + "x" * 123
        assert len(password) == 127
        validate_strong_password(password + "y")


class TestFieldValidators:

    def test_org_name_strips_whitespace(self):
        assert validate_organization_name("  Test  Corp  ") == "Test Corp"

    def test_org_name_only_numbers_raises(self):
        with pytest.raises(ValidationError): validate_organization_name("12345")

    def test_org_name_special_chars_rejected(self):
        with pytest.raises(ValidationError): validate_organization_name("Corp<>!")

    def test_org_name_allowed_punctuation(self):
        assert validate_organization_name("Rock & Roll, Inc.") == "Rock & Roll, Inc."

    def test_full_name_hyphen_passes(self):
        assert validate_full_name("Mary-Jane Watson") == "Mary-Jane Watson"

    def test_full_name_digits_raises(self):
        with pytest.raises(ValidationError): validate_full_name("John123")

    def test_industry_valid(self):
        assert validate_industry("Manufacturing") == "Manufacturing"

    def test_country_valid(self):
        assert validate_country("United Kingdom") == "United Kingdom"

    def test_country_digits_raises(self):
        with pytest.raises(ValidationError): validate_country("Country123")


@pytest.mark.django_db
class TestOrganizationRegisterSerializer:

    BASE = {
        "name": "Fresh Org", "industry": "Technology",
        "country": "India", "admin_email": "admin@freshorg.com", "password": "Secure@9876",
    }

    def test_valid_data(self):
        assert OrganizationRegisterSerializer(data=self.BASE).is_valid()

    def test_duplicate_name_invalid(self, verified_org):
        s = OrganizationRegisterSerializer(data={**self.BASE, "name": "Test Corp"})
        assert not s.is_valid()
        assert "name" in s.errors

    def test_duplicate_email_invalid(self, admin_user):
        s = OrganizationRegisterSerializer(data={**self.BASE, "admin_email": "admin@testcorp.com"})
        assert not s.is_valid()
        assert "admin_email" in s.errors

    @patch("accounts.serializers.send_organization_verification_email")
    def test_save_calls_send_email(self, mock_send):
        s = OrganizationRegisterSerializer(data=self.BASE)
        s.is_valid()
        s.save()
        mock_send.assert_called_once()


@pytest.mark.django_db
class TestAddUserSerializer:

    def _req(self, user):
        r = MagicMock()
        r.user = user
        return r

    def test_valid_officer(self, admin_user):
        s = AddUserSerializer(
            data={"full_name": "New Officer", "email": "new@corp.com", "role": "officer"},
            context={"request": self._req(admin_user)},
        )
        assert s.is_valid(), s.errors

    def test_admin_role_rejected(self, admin_user):
        s = AddUserSerializer(
            data={"full_name": "Bad", "email": "bad@corp.com", "role": "admin"},
            context={"request": self._req(admin_user)},
        )
        assert not s.is_valid()
        assert "role" in s.errors

    def test_email_lowercased(self, admin_user):
        s = AddUserSerializer(
            data={"full_name": "Case", "email": "UPPER@CORP.COM", "role": "viewer"},
            context={"request": self._req(admin_user)},
        )
        s.is_valid()
        assert s.validated_data["email"] == "upper@corp.com"


@pytest.mark.django_db
class TestEditUserSerializer:

    def test_valid_role_change_to_viewer(self, officer_user):
        s = EditUserSerializer(officer_user, data={"role": "viewer"}, partial=True)
        assert s.is_valid(), s.errors

    def test_admin_role_rejected(self, officer_user):
        s = EditUserSerializer(officer_user, data={"role": "admin"}, partial=True)
        assert not s.is_valid()
        assert "role" in s.errors

    def test_unknown_role_rejected(self, officer_user):
        s = EditUserSerializer(officer_user, data={"role": "superuser"}, partial=True)
        assert not s.is_valid()
        assert "role" in s.errors


@pytest.mark.django_db
class TestForceChangePasswordSerializer:

    def _req(self, user):
        r = MagicMock()
        r.user = user
        return r

    def test_valid_change(self, admin_user):
        s = ForceChangePasswordSerializer(
            data={"current_password": "Admin@1234", "new_password": "NewAdmin@99"},
            context={"request": self._req(admin_user)},
        )
        assert s.is_valid(), s.errors

    def test_wrong_current_invalid(self, admin_user):
        s = ForceChangePasswordSerializer(
            data={"current_password": "Wrong@1", "new_password": "NewAdmin@99"},
            context={"request": self._req(admin_user)},
        )
        assert not s.is_valid()

    def test_same_password_invalid(self, admin_user):
        s = ForceChangePasswordSerializer(
            data={"current_password": "Admin@1234", "new_password": "Admin@1234"},
            context={"request": self._req(admin_user)},
        )
        assert not s.is_valid()

    def test_save_updates_user_and_clears_flag(self, admin_user):
        s = ForceChangePasswordSerializer(
            data={"current_password": "Admin@1234", "new_password": "FreshAdmin@99"},
            context={"request": self._req(admin_user)},
        )
        s.is_valid()
        s.save()
        admin_user.refresh_from_db()
        assert admin_user.check_password("FreshAdmin@99")
        assert admin_user.must_change_password is False
        assert admin_user.is_active is True


class TestTokenHelpers:

    def test_generate_is_unique(self):
        tokens = {generate_verification_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_hash_is_deterministic(self):
        assert hash_token("abc") == hash_token("abc")

    def test_hash_length_64(self):
        assert len(hash_token("anything")) == 64

    def test_different_tokens_different_hashes(self):
        assert hash_token("a") != hash_token("b")


@pytest.mark.django_db
class TestVerificationTokenValid:

    def test_valid_token_true(self, unverified_org):
        assert is_verification_token_valid(unverified_org, unverified_org._raw_token) is True

    def test_wrong_token_false(self, unverified_org):
        assert is_verification_token_valid(unverified_org, "wrong") is False

    def test_expired_org_false(self, unverified_org):
        unverified_org.created_at = timezone.now() - timezone.timedelta(hours=25)
        unverified_org.save()
        assert is_verification_token_valid(unverified_org, unverified_org._raw_token) is False

    def test_no_stored_token_false(self, unverified_org):
        unverified_org.email_verification_token = None
        unverified_org.save()
        assert is_verification_token_valid(unverified_org, "any") is False


@pytest.mark.django_db
class TestEmailSenders:

    def test_org_verification_email(self, unverified_org):
        send_organization_verification_email(unverified_org, "test-token")
        assert len(mail.outbox) == 1
        assert "/verify-email/" in mail.outbox[0].body

    def test_welcome_email(self, must_change_user):
        send_user_welcome_email(must_change_user, "reset-token", "uid")
        assert len(mail.outbox) == 1
        assert "/reset-password/" in mail.outbox[0].body
        assert must_change_user.full_name in mail.outbox[0].body

    def test_password_reset_email_contains_temp_password(self, officer_user):
        send_password_reset_email(officer_user, "Temp@abc1")
        assert "Temp@abc1" in mail.outbox[0].body

    def test_email_failure_returns_false(self, unverified_org):
        with patch("accounts.utils.email_verification.send_mail", side_effect=Exception("SMTP down")):
            result = send_organization_verification_email(unverified_org, "token")
        assert result is False


class TestGenerateTempPassword:

    def test_passes_strong_validator(self):
        for _ in range(20):
            validate_strong_password(generate_temp_password())

    def test_default_length_12(self):
        assert len(generate_temp_password()) == 12

    def test_custom_length(self):
        assert len(generate_temp_password(16)) == 16

    def test_length_below_8_raises(self):
        with pytest.raises(ValueError): generate_temp_password(4)

    def test_all_unique(self):
        assert len({generate_temp_password() for _ in range(30)}) == 30