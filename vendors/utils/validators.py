import re
from rest_framework import serializers


def validate_vendor_name(value):
    value = " ".join((value or "").strip().split())

    if len(value) < 2:
        raise serializers.ValidationError(
            "Vendor name must be at least 2 characters long"
        )
    if len(value) > 255:
        raise serializers.ValidationError(
            "Vendor name must be 255 characters or less"
        )
    if not re.match(r"^[a-zA-Z0-9\s\-&\.,'\']+$", value):
        raise serializers.ValidationError(
            "Vendor name can only contain letters, numbers, spaces, "
            "and basic punctuation (- & . , ')"
        )
    if value.replace(" ", "").replace(".", "").replace(",", "").isdigit():
        raise serializers.ValidationError("Vendor name cannot be only numbers")
    if not any(c.isalnum() for c in value):
        raise serializers.ValidationError(
            "Vendor name must contain at least one letter or number"
        )
    return value


def validate_vendor_email(value):
    value = (value or "").strip().lower()

    if not value:
        raise serializers.ValidationError("Contact email is required")

    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', value):
        raise serializers.ValidationError("Invalid email format")

    if '..' in value:
        raise serializers.ValidationError("Email cannot contain consecutive dots")

    local_part = value.split('@')[0]
    if local_part.startswith('.') or local_part.endswith('.'):
        raise serializers.ValidationError("Email local part cannot start or end with a dot")

    return value


def validate_vendor_country(value):
    value = " ".join((value or "").strip().split())

    if len(value) < 2:
        raise serializers.ValidationError(
            "Country must be at least 2 characters long"
        )
    if len(value) > 100:
        raise serializers.ValidationError(
            "Country cannot exceed 100 characters"
        )
    if not re.match(r'^[a-zA-Z\s\-\'\.]+$', value):
        raise serializers.ValidationError(
            "Country name can only contain letters, spaces, hyphens, and apostrophes"
        )
    return value