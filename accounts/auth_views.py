import logging
from django.conf import settings
from django.core.mail import send_mail
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.exceptions import ValidationError as DRFValidationError

from .models import User
from .auth_serializers import EmailTokenObtainPairSerializer
from .serializers import (
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
    ForceChangePasswordSerializer,
)

from .throttling import LoginRateThrottle, PasswordResetRateThrottle

logger = logging.getLogger("accounts.auth")


class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    throttle_classes   = [LoginRateThrottle]
    serializer_class   = EmailTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        from audit_logs.services import log_action

        email = request.data.get("email", "").lower().strip()

        try:
            user = User.objects.select_related("organization").filter(email=email).first()

            if user and not user.organization.is_verified:
                logger.warning(
                    "login_blocked.org_not_verified | email=%s org_id=%s",
                    email, user.organization_id,
                )
                return Response(
                    {
                        "error": "Organization not verified",
                        "message": "Please verify your organization email address before logging in.",
                        "email": user.organization.primary_email,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            response = super().post(request, *args, **kwargs)

            if response.status_code == 200 and user:
                logger.info("login_success | email=%s", email)
                log_action(
                    action='user_login',
                    entity_type='User',
                    entity_id=str(user.id),
                    organization=user.organization,
                    actor=user,
                    request=request,
                    details={'email': email},
                )

            return response

        except (AuthenticationFailed, DRFValidationError):
            raise
        except Exception as exc:
            logger.exception("login_error | email=%s error=%s", email, str(exc))
            return Response(
                {"error": "Login failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from audit_logs.services import log_action

        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"error": "Refresh token is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            logger.info("logout | user=%s", request.user.id)
            log_action(
                action='user_logout',
                entity_type='User',
                entity_id=str(request.user.id),
                organization=request.user.organization,
                actor=request.user,
                request=request,
            )
            return Response({"message": "Logged out successfully"})
        except TokenError:
            return Response({"message": "Logged out"})
        except Exception:
            logger.exception("logout_error | user=%s", request.user.id)
            return Response({"error": "Logout failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes   = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email, is_active=True)

            token_generator = PasswordResetTokenGenerator()
            token           = token_generator.make_token(user)
            uid             = urlsafe_base64_encode(force_bytes(user.id))
            reset_link      = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}"

            send_mail(
                subject="CarbonSentry - Password Reset Request",
                message=f"Hello {user.full_name},\n\nReset your password here:\n{reset_link}\n\nExpires in 24 hours.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )

            logger.info("password_reset_email_sent | email=%s", email)
            return Response({"message": "Password reset link sent to your email", "email": email})

        except User.DoesNotExist:
            return Response({"message": "If an account exists with this email, you will receive a reset link"})
        except Exception as exc:
            logger.exception("password_reset_email_failed | email=%s error=%s", email, str(exc))
            return Response({"error": "Failed to send reset email."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes   = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info("password_reset_completed | user_id=%s", serializer.user.id)
        return Response({
            "message": "Password reset successful. You can now log in.",
            "login_url": f"{settings.FRONTEND_URL}/login",
        })


class ForceChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ForceChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        user = request.user
        logger.info("password_change_success | user=%s role=%s", user.email, user.role)

        redirect_mapping = {
            User.Role.ADMIN:   "/admin/dashboard",
            User.Role.OFFICER: "/officer/dashboard",
            User.Role.VIEWER:  "/viewer/dashboard",
        }

        return Response({
            "message": "Password changed successfully",
            "user": {
                "email":               user.email,
                "role":                user.role,
                "is_active":           user.is_active,
                "must_change_password": user.must_change_password,
            },
            "redirect_url": redirect_mapping.get(user.role, "/dashboard"),
        })