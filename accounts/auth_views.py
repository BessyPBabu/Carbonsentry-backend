import logging
from django.conf import settings
from django.core.mail import send_mail
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from .auth_serializers import EmailTokenObtainPairSerializer

from .models import User
from .serializers import (
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
    ForceChangePasswordSerializer,
)

logger = logging.getLogger("accounts.auth")


class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = EmailTokenObtainPairSerializer


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email, is_active=True)

            token = PasswordResetTokenGenerator().make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.id))
            reset_link = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}/"

            send_mail(
                subject="CarbonSentry password reset",
                message=f"Reset your password: {reset_link}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
            )

            logger.info("auth.password_reset.sent | email=%s", email)

        except User.DoesNotExist:
            logger.warning(
                "auth.password_reset.unknown_email | email=%s",
                email,
            )

        return Response(
            {"message": "If the email exists, a reset link has been sent"},
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        logger.info("auth.password_reset.completed")
        return Response({"message": "Password reset successful"})


class ForceChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ForceChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        logger.info(
            "auth.password_changed | user=%s",
            request.user.email,
        )

        return Response({"message": "Password changed successfully"})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")

        if refresh:
            RefreshToken(refresh).blacklist()

        logger.info("auth.logout | user=%s", request.user.email)
        return Response({"message": "Logged out successfully"})
