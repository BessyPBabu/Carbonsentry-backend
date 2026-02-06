import logging
from django.db import transaction
from vendors.models import Vendor, Document, IndustryRequiredDocument

logger = logging.getLogger("vendors.vendor_creator")


class VendorCreationError(Exception):
    pass

class VendorCreatorService:

    @classmethod
    @transaction.atomic
    def create_vendor(cls, organization, data, industry,send_emails=False):
        try:
            vendor = Vendor.objects.create(
                organization=organization,
                name=data["name"],
                industry=industry,
                country=data["country"],
                contact_email=data["contact_email"],
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
            logger.info(
                "Vendor created with pending documents",
                extra={
                    "vendor_id": str(vendor.id),
                    "document_count": len(documents),
                },
            )

            if send_emails is True:
                from vendors.services.email_campaign_service import EmailCampaignService

                EmailCampaignService.run(
                    organization=organization,
                    vendors=[vendor],
                )

            return vendor


        except Exception as e:
            logger.exception("Vendor creation failed")
            raise VendorCreationError(f"Failed to create vendor: {str(e)}")
