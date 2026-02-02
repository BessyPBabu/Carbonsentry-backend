import logging

from django.conf import settings
from django.core.mail import send_mail

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import generics, status, filters

from .models import User
from .serializers import (
    OrganizationRegisterSerializer,
    OrganizationUpdateSerializer,
    UserListSerializer,
    AddUserSerializer,
    EditUserSerializer,
)
from .permissions import IsAdmin
from .utils.passwords import generate_temp_password

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
                "message": "Organization registered successfully",
                "organization_id": str(organization.id),
            },
            status=status.HTTP_201_CREATED,
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
            user.save(update_fields=["password", "must_change_password"])

            send_mail(
                subject="CarbonSentry password reset",
                message=(
                    f"Temporary password: {temp_password}\n\n"
                    "Please log in and change your password immediately.\n"
                    f"{settings.FRONTEND_URL}/login"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
            )

            logger.info(
                "user.password_reset.success | target=%s by_admin=%s",
                user.email,
                request.user.email,
            )

            return Response(
                {"message": "Password reset successful"},
                status=status.HTTP_200_OK,
            )

        except User.DoesNotExist:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
