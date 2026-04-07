import logging
import time

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

# Mailtrap sandbox allows ~3 emails/second on free plan.
# We add a 0.5s gap between each send to stay well within limits.
_EMAIL_GAP_SECONDS = 0.5


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_vendor_email_task(self, vendor_id: str, subject: str, body: str, recipient: str):
    from vendors.services.email_service import EmailService
    try:
        EmailService.send(subject=subject, body=body, recipient=recipient)
        logger.info("send_vendor_email_task: sent vendor=%s recipient=%s", vendor_id, recipient)
    except Exception as exc:
        logger.warning(
            "send_vendor_email_task: retry vendor=%s attempt=%d — %s",
            vendor_id, self.request.retries, exc,
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2)
def send_bulk_vendor_emails_task(self, campaign_data: dict):
    
    from vendors.models import Vendor, Document
    from vendors.services.upload_token_services import UploadTokenService
    from vendors.services.email_service import EmailService
    from vendors.models import EmailCampaign, EmailDispatch
    from accounts.models import Organization

    org_id    = campaign_data['organization_id']
    vendor_ids = campaign_data['vendor_ids']

    try:
        organization = Organization.objects.get(id=org_id)
    except Organization.DoesNotExist:
        logger.error("send_bulk_vendor_emails_task: org %s not found", org_id)
        return

    vendors = Vendor.objects.filter(
        id__in=vendor_ids,
        organization=organization,
    ).select_related('industry')

    from collections import defaultdict
    grouped = defaultdict(list)
    for v in vendors:
        grouped[v.industry].append(v)

    total_sent = 0
    total_failed = 0

    for industry, vendor_list in grouped.items():
        campaign = EmailCampaign.objects.create(
            organization=organization,
            industry=industry,
            subject=f"Carbon Compliance Documents Required — {industry.name}",
            body_template="",
        )

        for vendor in vendor_list:
            pending_docs = Document.objects.filter(vendor=vendor, status__in=('pending', 'invalid', 'expired'))

            if not pending_docs.exists():
                logger.info("send_bulk_vendor_emails_task: skipping vendor=%s no pending docs", vendor.id)
                continue

            try:
                token = UploadTokenService.generate_for_vendor(vendor)
                doc_list = "\n".join([f"- {d.document_type.name}" for d in pending_docs])
                upload_link = f"{settings.FRONTEND_URL}/upload/{token}"
                body = _build_email_body(vendor, doc_list, upload_link)

                # Rate-limit gap — critical for Mailtrap sandbox
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
                total_sent += 1
                logger.info(
                    "send_bulk_vendor_emails_task: sent vendor=%s email=%s",
                    vendor.id, vendor.contact_email,
                )

            except Exception as exc:
                total_failed += 1
                logger.error(
                    "send_bulk_vendor_emails_task: failed vendor=%s — %s",
                    vendor.id, exc,
                )
                EmailDispatch.objects.create(
                    campaign=campaign,
                    vendor=vendor,
                    recipient_email=vendor.contact_email,
                    status='failed',
                    error_message=str(exc)[:500],
                )

    logger.info(
        "send_bulk_vendor_emails_task: done org=%s sent=%d failed=%d",
        org_id, total_sent, total_failed,
    )

    if total_sent == 0 and total_failed > 0:
        raise self.retry(
            exc=RuntimeError(
                f"All {total_failed} vendor emails failed for org {org_id}"
            ),
            countdown=60 * (self.request.retries + 1),
        )


def _build_email_body(vendor, document_list: str, upload_link: str) -> str:
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