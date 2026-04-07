import logging
import time
from collections import defaultdict

from django.conf import settings
from django.db import transaction

from vendors.models import Document, Vendor, EmailCampaign, EmailDispatch
from vendors.services.upload_token_services import UploadTokenService
from vendors.services.email_service import EmailService

logger = logging.getLogger("vendors.email_campaign")

_EMAIL_GAP_SECONDS = 0.5


class EmailCampaignService:

    @classmethod
    def run(cls, organization, vendors, async_bulk: bool = False):
        """
        async_bulk=True  → queues a Celery task (for bulk upload, avoids Mailtrap rate limits)
        async_bulk=False → sends synchronously (for single vendor add / resend)
        """
        if async_bulk and len(vendors) > 1:
            from vendors.services.email_tasks import send_bulk_vendor_emails_task
            send_bulk_vendor_emails_task.delay({
                'organization_id': str(organization.id),
                'vendor_ids': [str(v.id) for v in vendors],
            })
            logger.info(
                "EmailCampaignService: queued async bulk email org=%s vendors=%d",
                organization.id, len(vendors),
            )
            return

        # Synchronous path — used for single vendor or resend
        cls._run_sync(organization, vendors)

    @classmethod
    @transaction.atomic
    def _run_sync(cls, organization, vendors):
        grouped = defaultdict(list)
        for vendor in vendors:
            grouped[vendor.industry].append(vendor)

        for industry, vendor_list in grouped.items():
            campaign = EmailCampaign.objects.create(
                organization=organization,
                industry=industry,
                subject=f"Carbon Compliance Documents Required — {industry.name}",
                body_template="",
            )

            for vendor in vendor_list:
                # Include pending, invalid, and expired documents
                pending_docs = Document.objects.filter(
                    vendor=vendor,
                    status__in=('pending', 'invalid', 'expired'),
                )

                if not pending_docs.exists():
                    logger.info(
                        "EmailCampaignService: skipping vendor=%s no pending/invalid/expired docs",
                        vendor.id,
                    )
                    continue

                try:
                    token      = UploadTokenService.generate_for_vendor(vendor)
                    doc_list   = "\n".join([f"- {d.document_type.name}" for d in pending_docs])
                    upload_link = f"{settings.FRONTEND_URL}/upload/{token}"
                    body       = cls._generate_email_body(vendor, doc_list, upload_link)

                    time.sleep(_EMAIL_GAP_SECONDS)

                    EmailService.send(
                        subject=campaign.subject,
                        body=body,
                        recipient=vendor.contact_email,
                    )

                    EmailDispatch.objects.create(
                        campaign=campaign,
                        vendor=vendor,
                        recipient_email=vendor.contact_email,
                        status='sent',
                    )
                    logger.info(
                        "EmailCampaignService: sent vendor=%s email=%s",
                        vendor.id, vendor.contact_email,
                    )

                except Exception as exc:
                    logger.error(
                        "EmailCampaignService: failed vendor=%s — %s",
                        vendor.id, exc,
                    )
                    EmailDispatch.objects.create(
                        campaign=campaign,
                        vendor=vendor,
                        recipient_email=vendor.contact_email,
                        status='failed',
                        error_message=str(exc)[:500],
                    )

    @classmethod
    def _generate_email_body(cls, vendor, document_list: str, upload_link: str) -> str:
        return f"""Dear {vendor.name} Team,

Greetings from CarbonSentry Compliance Platform!

We require the following carbon compliance documents from your organization:

REQUIRED DOCUMENTS:
─────────────────────────────────────────────────────────
{document_list}
─────────────────────────────────────────────────────────

UPLOAD LINK (valid 72 hours):
{upload_link}

INSTRUCTIONS:
1. Click the upload link above
2. Select the document type  
3. Upload your file (PDF, JPG, PNG — max 10MB)
4. Repeat for each required document

For support: compliance@carbonsentry.com

Best regards,
CarbonSentry Compliance Team
""".strip()