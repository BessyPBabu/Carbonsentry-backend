import logging

from django.conf import settings
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import generics, status, filters

from .models import User, Organization
from .serializers import (
    OrganizationRegisterSerializer,
    OrganizationUpdateSerializer,
    UserListSerializer,
    AddUserSerializer,
    EditUserSerializer,
)
from .permissions import IsAdmin
from .utils.passwords import generate_temp_password
from .utils.email_verification import (
    hash_token,
    is_verification_token_valid,
    send_password_reset_email,
)

logger = logging.getLogger("accounts.views")


class OrganizationRegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OrganizationRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        organization = serializer.save()

        logger.info(
            "org.registered | org_id=%s admin_email=%s",
            organization.id,
            organization.primary_email,
        )

        return Response(
            {
                "message": "Registration successful! Please check your email to verify your account.",
                "email": organization.primary_email,
                "next_step": "Check your email and click the verification link to complete registration.",
            },
            status=status.HTTP_201_CREATED,
        )
    
class VerifyOrganizationEmailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            hashed_token = hash_token(token)
            
            organization = Organization.objects.filter(
                email_verification_token=hashed_token
            ).first()

            if not organization:
                return Response(
                    {
                        "error": "Invalid or expired verification link",
                        "message": "This verification link is invalid or has already been used."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            if organization.is_verified:
                logger.info(
                    "org.email_already_verified | org_id=%s email=%s",
                    organization.id,
                    organization.primary_email,
                )
                return Response(
                    {
                        "message": "Email already verified!",
                        "organization_name": organization.name,
                        "login_url": f"{settings.FRONTEND_URL}/login",
                        "next_step": "Your organization is already verified. You can log in now.",
                        "already_verified": True,  # Flag for frontend
                    },
                    status=status.HTTP_200_OK,  # Return 200, not error!
                )

            if not is_verification_token_valid(organization, token):
                return Response(
                    {
                        "error": "Verification link expired",
                        "message": "This verification link has expired. Please contact support."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            organization.is_verified = True
            organization.email_verified_at = timezone.now()
            organization.save()

            admin_user = User.objects.filter(
                organization=organization,
                role=User.Role.ADMIN
            ).first()
            
            if admin_user:
                admin_user.is_active = True
                admin_user.save()

            logger.info(
                "org.email_verified | org_id=%s email=%s",
                organization.id,
                organization.primary_email,
            )

            return Response(
                {
                    "message": "Email verified successfully!",
                    "organization_name": organization.name,
                    "login_url": f"{settings.FRONTEND_URL}/login",
                    "next_step": "You can now log in with your email and password.",
                },
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("org.email_verification_failed | error=%s", str(exc))
            return Response(
                {
                    "error": "Verification failed",
                    "message": "An error occurred during verification. Please try again."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OrganizationMeView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        serializer = OrganizationUpdateSerializer(request.user.organization)
        return Response(serializer.data)

    def put(self, request):
        serializer = OrganizationUpdateSerializer(
            request.user.organization,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        logger.info(
            "org.updated | org_id=%s by=%s",
            request.user.organization_id,
            request.user.email,
        )

        return Response(serializer.data)


class UserMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        return Response({
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "must_change_password": user.must_change_password,
            "organization": {
                "id": str(user.organization_id),
                "name": user.organization.name,
                "is_verified": user.organization.is_verified,
            },
        })


class UserListView(generics.ListAPIView):
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter]
    search_fields = ["email", "full_name"]

    def get_queryset(self):
        return User.objects.filter(
            organization=self.request.user.organization
        )


class AddUserView(generics.CreateAPIView):
    serializer_class = AddUserSerializer
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_serializer_context(self):
        return {"request": self.request}


class EditUserView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EditUserSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    lookup_field = "id"

    def get_queryset(self):
        return User.objects.filter(
            organization=self.request.user.organization
        )


class ResetUserPasswordView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request, user_id):
        try:
            user = User.objects.get(
                id=user_id,
                organization=request.user.organization,
            )

            temp_password = generate_temp_password()
            user.set_password(temp_password)
            user.must_change_password = True
            user.is_active = False
            user.save(update_fields=["password", "must_change_password", "is_active"])

            send_password_reset_email(user, temp_password)

            logger.info(
                "user.password_reset_by_admin | target=%s by_admin=%s",
                user.email,
                request.user.email,
            )

            return Response(
                {
                    "message": f"Password reset successful. A temporary password has been sent to {user.email}",
                    "email_sent": True,
                },
                status=status.HTTP_200_OK,
            )

        except User.DoesNotExist:
            logger.warning(
                "user.password_reset_failed | user_id=%s reason=not_found",
                user_id,
            )
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as exc:
            logger.exception(
                "user.password_reset_failed | user_id=%s error=%s",
                user_id,
                str(exc),
            )
            return Response(
                {"error": "Password reset failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
