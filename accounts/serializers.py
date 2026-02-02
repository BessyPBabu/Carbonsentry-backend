import logging
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from rest_framework import serializers

from .models import Organization, User
from .utils.passwords import validate_strong_password, generate_temp_password

logger = logging.getLogger("accounts.serializers")


class OrganizationRegisterSerializer(serializers.ModelSerializer):
    admin_email = serializers.EmailField(write_only=True)
    password = serializers.CharField(write_only=True)

    class Meta:
        model = Organization
        fields = ["name", "industry", "country", "admin_email", "password"]

    def validate_name(self, value):
        value = " ".join(value.strip().split())
        if Organization.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("Organization name already exists")
        return value

    def validate_password(self, value):
        validate_strong_password(value)
        return value

    @transaction.atomic
    def create(self, validated_data):
        admin_email = validated_data.pop("admin_email").lower().strip()
        password = validated_data.pop("password")

        try:
            organization = Organization.objects.create(
                primary_email=admin_email,
                **validated_data
            )

            User.objects.create_user(
                email=admin_email,
                password=password,
                role=User.Role.ADMIN,
                organization=organization,
                is_active=True,
            )

            logger.info(
                "organization_created | org_id=%s admin_email=%s",
                organization.id,
                admin_email,
            )

            return organization

        except Exception as exc:
            logger.exception(
                "organization_create_failed | admin_email=%s reason=%s",
                admin_email,
                str(exc),
            )
            raise serializers.ValidationError("Organization registration failed")


class OrganizationUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["name", "industry", "country", "primary_email"]
        read_only_fields = ["primary_email"]

    def validate_name(self, value):
        value = " ".join(value.strip().split())
        qs = Organization.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("Organization name already exists")
        return value



class UserListSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "role", "status", "created_at", "last_login"]

    def get_status(self, obj):
        return "active" if obj.is_active else "inactive"


class AddUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["full_name", "email", "role", "password"]

    def validate_role(self, value):
        if value not in [User.Role.OFFICER, User.Role.VIEWER]:
            raise serializers.ValidationError("Invalid role for user creation")
        return value

    def validate_email(self, value):
        value = value.lower().strip()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists")
        return value

    def create(self, validated_data):
        request = self.context["request"]
        organization = request.user.organization

        password = validated_data.pop("password", None)
        temp_password = None

        if not password:
            temp_password = generate_temp_password()
            password = temp_password

        try:
            user = User.objects.create_user(
                organization=organization,
                password=password,
                must_change_password=bool(temp_password),
                **validated_data,
            )

            if temp_password:
                send_mail(
                    subject="CarbonSentry account created",
                    message=(
                        f"Email: {user.email}\n"
                        f"Temporary password: {temp_password}\n"
                        "You must change this password on first login."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                )

            logger.info(
                "user_created | user_id=%s role=%s org_id=%s by=%s",
                user.id,
                user.role,
                organization.id,
                request.user.email,
            )

            return user

        except Exception as exc:
            logger.exception(
                "user_create_failed | email=%s org_id=%s reason=%s",
                validated_data.get("email"),
                organization.id,
                str(exc),
            )
            raise serializers.ValidationError("User creation failed")


class EditUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["full_name", "email", "role", "is_active", "password"]

    def validate_email(self, value):
        value = value.lower().strip()
        qs = User.objects.filter(email=value)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("Email already in use")
        return value

    def validate_role(self, value):
        if value not in User.Role.values:
            raise serializers.ValidationError("Invalid role")
        return value

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)

        try:
            if password:
                validate_strong_password(password)
                instance.set_password(password)
                instance.must_change_password = False

            for attr, val in validated_data.items():
                setattr(instance, attr, val)

            instance.save()

            logger.info(
                "user_updated | user_id=%s updated_by=%s",
                instance.id,
                self.context["request"].user.email,
            )

            return instance

        except Exception as exc:
            logger.exception(
                "user_update_failed | user_id=%s reason=%s",
                instance.id,
                str(exc),
            )
            raise serializers.ValidationError("User update failed")


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower().strip()


class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_strong_password(value)
        return value

    def validate(self, data):
        try:
            user_id = force_str(urlsafe_base64_decode(data["uid"]))
            self.user = User.objects.get(id=user_id, is_active=True)
        except Exception:
            raise serializers.ValidationError("Invalid reset link")

        if not PasswordResetTokenGenerator().check_token(
            self.user, data["token"]
        ):
            raise serializers.ValidationError("Invalid or expired token")

        return data

    def save(self):
        self.user.set_password(self.validated_data["password"])
        self.user.must_change_password = False
        self.user.save()

        logger.info("password_reset | user_id=%s", self.user.id)


class ForceChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_strong_password(value)
        return value

    def validate(self, data):
        user = self.context["request"].user
        if not user.check_password(data["current_password"]):
            raise serializers.ValidationError("Current password is incorrect")
        return data

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.must_change_password = False
        user.save()

        logger.info("password_changed | user_id=%s", user.id)