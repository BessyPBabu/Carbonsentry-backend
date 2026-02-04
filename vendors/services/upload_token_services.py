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
    def generate_for_document(cls, document):
        try:
            token = secrets.token_urlsafe(32)
            expiry = timezone.now() + timedelta(hours=cls.EXPIRY_HOURS)

            document.upload_token = token
            document.upload_token_expires_at = expiry
            document.save(update_fields=["upload_token", "upload_token_expires_at"])

            logger.info(
                "Upload token generated",
                extra={
                    "document_id": str(document.id),
                    "vendor_id": str(document.vendor_id),
                },
            )

            return token

        except Exception:
            logger.exception("Upload token generation failed")
            raise
