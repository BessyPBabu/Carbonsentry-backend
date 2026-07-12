import logging
from django.db import transaction
from rest_framework.exceptions import ValidationError as DRFValidationError
from vendors.models import Vendor, Document, IndustryRequiredDocument
from vendors.utils.validators import (
    validate_vendor_name,
    validate_vendor_email,
    validate_vendor_country,
)

logger = logging.getLogger("vendors.vendor_creator")


class VendorCreationError(Exception):
    pass

class VendorCreatorService:

    @classmethod
    @transaction.atomic
    def create_vendor(cls, organization, data, industry, send_emails=False):
        try:
            name = validate_vendor_name(data.get("name", ""))
            country = validate_vendor_country(data.get("country", ""))
            contact_email = validate_vendor_email(data.get("contact_email", ""))
        except DRFValidationError as exc:
            message = str(exc.detail[0]) if exc.detail else "Invalid vendor data"
            logger.warning("Vendor validation failed", extra={"error": message})
            raise VendorCreationError(message)

        try:
            vendor = Vendor.objects.create(
                organization=organization,
                name=name,
                industry=industry,
                country=country,
                contact_email=contact_email,
            )
            
            required_docs = IndustryRequiredDocument.objects.filter(
                industry=vendor.industry
            )

            documents = []
            for req in required_docs:
                documents.append(
                    Document(
                        vendor=vendor,
                        document_type=req.document_type,
                        status="pending",
                    )
                )

            Document.objects.bulk_create(documents)

            logger.info(
                "Vendor created with pending documents",
                extra={
                    "vendor_id": str(vendor.id),
                    "document_count": len(documents),
                },
            )
            

            if send_emails is True:
                try:
                    from vendors.services.email_campaign_service import EmailCampaignService
                    EmailCampaignService.run(
                        organization=organization,
                        vendors=[vendor],
                    )
                    logger.info(
                        "Email sent successfully for new vendor",
                        extra={"vendor_id": str(vendor.id)}
                    )
                except Exception as email_error:
                    logger.error(
                        "Failed to send email for new vendor",
                        extra={
                            "vendor_id": str(vendor.id),
                            "error": str(email_error)
                        }
                    )

            return vendor


        except Exception as e:
            logger.exception("Vendor creation failed")
            raise VendorCreationError(f"Failed to create vendor: {str(e)}")
