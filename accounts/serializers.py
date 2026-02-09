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
from .utils.validators import (
    validate_organization_name,
    validate_full_name,
    validate_industry,
    validate_country,
)
from .utils.email_verification import (
    generate_verification_token,
    hash_token,
    send_organization_verification_email,
    send_user_welcome_email,
)
logger = logging.getLogger("accounts.serializers")


class OrganizationRegisterSerializer(serializers.ModelSerializer):
    admin_email = serializers.EmailField(write_only=True)
    password = serializers.CharField(write_only=True)

    class Meta:
        model = Organization
        fields = ["name", "industry", "country", "admin_email", "password"]

    def validate_name(self, value):
        value = validate_organization_name(value)
        if Organization.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("Organization name already exists")
        return value
    
    def validate_industry(self, value):
        return validate_industry(value)

    def validate_country(self, value):
        return validate_country(value)

    def validate_admin_email(self, value):
        value = value.lower().strip()
        
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already registered")
        
        return value

    def validate_password(self, value):
        validate_strong_password(value)
        return value

    @transaction.atomic
    def create(self, validated_data):
        admin_email = validated_data.pop("admin_email")
        password = validated_data.pop("password")

        try:
            token = generate_verification_token()
            hashed_token = hash_token(token)
            
            organization = Organization.objects.create(
                primary_email=admin_email,
                is_verified=False,
                email_verification_token=hashed_token,
                **validated_data
            )

            User.objects.create_user(
                email=admin_email,
                password=password,
                role=User.Role.ADMIN,
                organization=organization,
                is_active=False,
            )

            send_organization_verification_email(organization, token)

            logger.info(
                "organization_registered | org_id=%s admin_email=%s verification_sent=True",
                organization.id,
                admin_email,
            )

            return organization

        except Exception as exc:
            logger.exception(
                "organization_registration_failed | admin_email=%s reason=%s",
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
        value = validate_organization_name(value)

        qs = Organization.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("Organization name already exists")
        return value
    
    def validate_industry(self, value):
        return validate_industry(value)

    def validate_country(self, value):
        return validate_country(value)



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

    def validate_full_name(self, value):
        return validate_full_name(value)

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
        from django.contrib.auth.tokens import PasswordResetTokenGenerator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        
        request = self.context["request"]
        organization = request.user.organization

        password = validated_data.pop("password", None)
        send_reset_email = False

        if not password:
            password = None  # Will set unusable password
            send_reset_email = True

        try:
            user = User.objects.create_user(
                organization= organization,
                password=password,
                must_change_password=True if not password else False,
                email=validated_data.pop("email"),
                full_name=validated_data.get("full_name", ""),
                role=validated_data.get("role"),
                is_active=False,
            )

            if send_reset_email:
                token_generator = PasswordResetTokenGenerator()
                token = token_generator.make_token(user)
                uid = urlsafe_base64_encode(force_bytes(user.id))
                
                send_user_welcome_email(user, token, uid)

            logger.info(
                "user_created | user_id=%s role=%s org_id=%s by=%s email_sent=%s",
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
            raise serializers.ValidationError("User creation failed. Please try again.")


class EditUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["full_name", "email", "role", "is_active", "password"]
        read_only_fields = ["email"]

    def validate_full_name(self, value):
        return validate_full_name(value)
    
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

            if 'is_active' in validated_data:
                instance.is_active = validated_data.pop('is_active')

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
            self.user = User.objects.get(id=user_id)
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
        self.user.is_active = True
        self.user.save()

        logger.info("password_reset | user_id=%s is_active=%s", self.user.id, self.user.is_active)


class ForceChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_strong_password(value)
        return value

    def validate(self, data):
        user = self.context["request"].user
        

        if not user.check_password(data["current_password"]):
            raise serializers.ValidationError({
                "current_password": "Current password is incorrect"
            })
        
        if data["current_password"] == data["new_password"]:
            raise serializers.ValidationError({
                "new_password": "New password must be different from current password"
            })
        
        return data

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.must_change_password = False
        user.is_active = True
        user.save()

        logger.info("password_changed | user_id=%s is_active=%s", user.id, user.is_active)