import re
from rest_framework import serializers


def validate_organization_name(value):
    value = " ".join(value.strip().split())
    
    if len(value) < 2:
        raise serializers.ValidationError(
            "Organization name must be at least 2 characters long"
        )
    if len(value) > 255:
        raise serializers.ValidationError(
            "Organization name cannot exceed 255 characters"
        )
    if not re.match(r"^[a-zA-Z0-9\s\-&\.,'\']+$", value) :
        raise serializers.ValidationError(
            "Organization name can only contain letters, numbers, spaces, "
            "and basic punctuation (- & . , ')"
        )
    if value.replace(" ", "").replace(".", "").replace(",", "").isdigit():
        raise serializers.ValidationError(
            "Organization name cannot be only numbers"
        )
    if not any(c.isalnum() for c in value):
        raise serializers.ValidationError(
            "Organization name must contain at least one letter or number"
        )
    
    return value


def validate_full_name(value):
    value = " ".join(value.strip().split())
    
    if len(value) < 2:
        raise serializers.ValidationError(
            "Full name must be at least 2 characters long"
        )
    
    if len(value) > 255:
        raise serializers.ValidationError(
            "Full name cannot exceed 255 characters"
        )
    
    if not re.match(r'^[a-zA-Z\s\-\'\.]+$', value):
        raise serializers.ValidationError(
            "Full name can only contain letters, spaces, hyphens, apostrophes, and periods"
        )
    
    if not any(c.isalpha() for c in value):
        raise serializers.ValidationError(
            "Full name must contain at least one letter"
        )
    
    return value


def validate_email_format(value):
    value = value.lower().strip()
    
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', value):
        raise serializers.ValidationError("Invalid email format")
    
    if '..' in value:
        raise serializers.ValidationError("Email cannot contain consecutive dots")
    
    local_part = value.split('@')[0]
    if local_part.startswith('.') or local_part.endswith('.'):
        raise serializers.ValidationError("Email local part cannot start or end with a dot")
    
    disposable_domains = [
        'tempmail.com', 
        '10minutemail.com', 
        'guerrillamail.com',
        'mailinator.com',
        'throwaway.email'
    ]
    domain = value.split('@')[1]
    if domain.lower() in disposable_domains:
        raise serializers.ValidationError(
            "Disposable email addresses are not allowed"
        )
    
    return value


def validate_industry(value):
    value = " ".join(value.strip().split())
    
    if len(value) < 2:
        raise serializers.ValidationError(
            "Industry must be at least 2 characters long"
        )
    
    if len(value) > 100:
        raise serializers.ValidationError(
            "Industry cannot exceed 100 characters"
        )
    
    if not re.match(r'^[a-zA-Z0-9\s\-&/,\.]+$', value):
        raise serializers.ValidationError(
            "Industry contains invalid characters"
        )
    
    return value


def validate_country(value):
    value = " ".join(value.strip().split())
    
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