import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, db_index=True)
    industry = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    primary_email = models.EmailField(db_index=True)
    is_verified = models.BooleanField(default=False, db_index=True)
    email_verification_token = models.CharField(max_length=255, null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["industry"]),
            models.Index(fields=["country"]),
            models.Index(fields=["is_verified"]),
        ]

    def __str__(self):
        return self.name
    

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")

        if "organization" not in extra_fields:
            raise ValueError("User must belong to an organization")

        if "role" not in extra_fields:
            raise ValueError("User role is required")

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)

        must_change_password = extra_fields.pop('must_change_password', False)

        if password:
            user.set_password(password)
            user.must_change_password = must_change_password
            user.password_changed_at = timezone.now()
        else:
            user.set_unusable_password()
            user.must_change_password = True

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        if not password:
            raise ValueError("Superuser must have a password")

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "admin")
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        if "organization" not in extra_fields:
            raise ValueError("Superuser must belong to an organization")

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        OFFICER = "officer", "Compliance Officer"
        VIEWER = "viewer", "Viewer"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="users",
    )
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        db_index=True,
    )

    is_active = models.BooleanField(default=False, db_index=True)
    is_staff = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["organization", "role"]),
            models.Index(fields=["organization", "is_active"]),
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self):
        return self.email