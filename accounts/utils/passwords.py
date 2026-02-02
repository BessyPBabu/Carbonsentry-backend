import secrets
import string
from rest_framework import serializers


def validate_strong_password(password: str) -> None:
    if not password:
        raise serializers.ValidationError("Password cannot be empty")

    if len(password) < 8:
        raise serializers.ValidationError(
            "Password must be at least 8 characters long"
        )

    if not any(c.islower() for c in password):
        raise serializers.ValidationError(
            "Password must contain at least one lowercase letter"
        )

    if not any(c.isupper() for c in password):
        raise serializers.ValidationError(
            "Password must contain at least one uppercase letter"
        )

    if not any(c.isdigit() for c in password):
        raise serializers.ValidationError(
            "Password must contain at least one number"
        )

    if not any(c in string.punctuation for c in password):
        raise serializers.ValidationError(
            "Password must contain at least one special character"
        )


def generate_temp_password(length: int = 12) -> str:
    if length < 8:
        raise ValueError("Temporary password length must be at least 8")

    alphabet = (
        string.ascii_lowercase +
        string.ascii_uppercase +
        string.digits +
        string.punctuation
    )

    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_strong_password(password)
            return password
        except serializers.ValidationError:
            continue
