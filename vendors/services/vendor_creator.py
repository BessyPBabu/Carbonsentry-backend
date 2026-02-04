import logging
from django.db import transaction
from vendors.models import Vendor, Document, IndustryRequiredDocument

logger = logging.getLogger("vendors.vendor_creator")


class VendorCreationError(Exception):
    pass

class VendorCreatorService:

    @classmethod
    @transaction.atomic
    def create_vendor(cls, organization, data, industry):
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

            return vendor

        except Exception as e:
            logger.exception("Vendor creation failed")
            raise VendorCreationError(f"Failed to create vendor: {str(e)}")
