import logging
from collections import defaultdict
from django.conf import settings
from django.db import transaction

from vendors.models import Document
from vendors.models import EmailCampaign
from vendors.models import EmailDispatch
from vendors.services import UploadTokenService
from vendors.services.email_service import EmailService

logger = logging.getLogger("vendors.email_campaign")


class EmailCampaignService:
    @classmethod
    @transaction.atomic
    def run(cls, organization, vendors):
        grouped = defaultdict(list)
        for vendor in vendors:
            grouped[vendor.industry].append(vendor)

        for industry, vendor_list in grouped.items():
            campaign = EmailCampaign.objects.create(
                organization=organization,
                industry=industry,
                subject=f"Carbon Compliance Documents Required – {industry.name}",
                body_template="Please upload the following documents:\n\n{LINKS}",
            )

            for vendor in vendor_list:
                pending_docs = Document.objects.filter(vendor=vendor, status="pending")
                links = []

                for doc in pending_docs:
                    token = UploadTokenService.generate_for_document(doc)
                    links.append(
                        f"- {doc.document_type.name}: "
                        f"{settings.FRONTEND_UPLOAD_URL}/{token}"
                    )

                if not links:
                    continue

                body = campaign.body_template.replace("{LINKS}", "\n".join(links))

                try:
                    EmailService.send(
                        subject=campaign.subject,
                        body=body,
                        recipient=vendor.contact_email,
                    )

                    EmailDispatch.objects.create(
                        campaign=campaign,
                        vendor=vendor,
                        recipient_email=vendor.contact_email,
                        status="sent",
                    )
                except Exception as e:
                    logger.error(
                        "Email send failed",
                        extra={
                            "vendor_id": str(vendor.id),
                            "email": vendor.contact_email,
                            "error": str(e),
                        }
                    )
                    EmailDispatch.objects.create(
                        campaign=campaign,
                        vendor=vendor,
                        recipient_email=vendor.contact_email,
                        status="failed",
                        error_message=str(e),
                    )