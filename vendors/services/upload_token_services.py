import secrets
import logging
from datetime import timedelta
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger("vendors.upload_token")


class UploadTokenService:
    EXPIRY_HOURS = 72

    @classmethod
    @transaction.atomic
    def generate_for_vendor(cls, vendor):
        """Generate a single upload token for all vendor documents"""
        try:
            token = secrets.token_urlsafe(32)
            expiry = timezone.now() + timedelta(hours=cls.EXPIRY_HOURS)
            vendor.upload_token = token
            vendor.upload_token_expires_at = expiry
            vendor.save(update_fields=["upload_token", "upload_token_expires_at"])

            logger.info(
                "Upload token generated for vendor",
                extra={
                    "vendor_id": str(vendor.id),
                    "token": token[:10] + "...",
                },
            )

            return token

        except Exception:
            logger.exception("Upload token generation failed")
            raise