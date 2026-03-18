# tests/conftest.py
import pytest
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Organization, User
from accounts.utils.email_verification import generate_verification_token, hash_token


# ── Organizations ─────────────────────────────────────────────────────────────

@pytest.fixture
def verified_org():
    return Organization.objects.create(
        name="Test Corp",
        industry="Technology",
        country="India",
        primary_email="admin@testcorp.com",
        is_verified=True,
        email_verification_token=hash_token(generate_verification_token()),
    )


@pytest.fixture
def unverified_org():
    raw_token = generate_verification_token()
    org = Organization.objects.create(
        name="Unverified Corp",
        industry="Logistics",
        country="India",
        primary_email="admin@unverified.com",
        is_verified=False,
        email_verification_token=hash_token(raw_token),
    )
    org._raw_token = raw_token
    return org


# ── Users ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_user(verified_org):
    return User.objects.create_user(
        email="admin@testcorp.com",
        password="Admin@1234",
        role=User.Role.ADMIN,
        organization=verified_org,
        is_active=True,
        full_name="Admin User",
    )


@pytest.fixture
def officer_user(verified_org):
    return User.objects.create_user(
        email="officer@testcorp.com",
        password="Officer@1234",
        role=User.Role.OFFICER,
        organization=verified_org,
        is_active=True,
        full_name="Officer User",
    )


@pytest.fixture
def viewer_user(verified_org):
    return User.objects.create_user(
        email="viewer@testcorp.com",
        password="Viewer@1234",
        role=User.Role.VIEWER,
        organization=verified_org,
        is_active=True,
        full_name="Viewer User",
    )


@pytest.fixture
def inactive_user(verified_org):
    return User.objects.create_user(
        email="inactive@testcorp.com",
        password="Inactive@1234",
        role=User.Role.OFFICER,
        organization=verified_org,
        is_active=False,
        full_name="Inactive User",
    )


@pytest.fixture
def must_change_user(verified_org):
    return User.objects.create_user(
        email="newuser@testcorp.com",
        password="Temp@1234",
        role=User.Role.OFFICER,
        organization=verified_org,
        is_active=False,
        must_change_password=True,
        full_name="New User",
    )


# ── API clients ───────────────────────────────────────────────────────────────

def _auth_client(user):
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def admin_client(admin_user):
    return _auth_client(admin_user)


@pytest.fixture
def officer_client(officer_user):
    return _auth_client(officer_user)


@pytest.fixture
def viewer_client(viewer_user):
    return _auth_client(viewer_user)


@pytest.fixture
def must_change_client(must_change_user):
    return _auth_client(must_change_user)


# ── Mail ──────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_mail():
    mail.outbox = []
    yield
    mail.outbox = []