from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .auth_views import (
    LoginView,
    LogoutView,
    ForgotPasswordView,
    ResetPasswordView,
    ForceChangePasswordView,
)

from .views import (
    OrganizationRegisterView,
    VerifyOrganizationEmailView,
    OrganizationMeView,
    UserMeView,
    UserListView,
    AddUserView,
    EditUserView,
    ResetUserPasswordView,
)

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),

    path("auth/password/forgot/", ForgotPasswordView.as_view(), name="password-forgot"),
    path("auth/password/reset/", ResetPasswordView.as_view(), name="password-reset"),
    path("auth/password/change/", ForceChangePasswordView.as_view(), name="password-change"),

    path("organizations/register/", OrganizationRegisterView.as_view(), name="organization-register"),
    path("organizations/verify-email/<str:token>/", VerifyOrganizationEmailView.as_view(), name="organization-verify-email"),
    path("organizations/me/", OrganizationMeView.as_view(), name="organization-me"),

    path("users/me/", UserMeView.as_view(), name="user-me"),
    path("users/", UserListView.as_view(), name="user-list"),
    path("users/add/", AddUserView.as_view(), name="user-add"),
    path("users/<uuid:id>/", EditUserView.as_view(), name="user-detail"),
    path("users/<uuid:user_id>/reset-password/", ResetUserPasswordView.as_view(), name="user-reset-password"),
]
