import logging
from collections import defaultdict
from django.conf import settings
from django.db import transaction

from vendors.models import Document, Vendor
from vendors.models import EmailCampaign
from vendors.models import EmailDispatch
from vendors.services.upload_token_services import UploadTokenService
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
                body_template="Please upload the following documents:\n\n{DOCUMENT_LIST}\n\nUpload Link: {UPLOAD_LINK}",
            )

            for vendor in vendor_list:
                pending_docs = Document.objects.filter(vendor=vendor, status="pending")
                
                if not pending_docs.exists():
                    logger.info(
                        "Skipping vendor - no pending documents",
                        extra={"vendor_id": str(vendor.id)}
                    )
                    continue
                
                token = UploadTokenService.generate_for_vendor(vendor)
                doc_list = "\n".join([f"- {doc.document_type.name}" for doc in pending_docs])
                
                upload_link = f"{settings.FRONTEND_URL}/upload/{token}"
                body = cls._generate_email_body(vendor, doc_list, upload_link)

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
                    
                    logger.info(
                        "Email sent successfully",
                        extra={
                            "vendor_id": str(vendor.id),
                            "email": vendor.contact_email,
                            "document_count": pending_docs.count()
                        }
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

    @classmethod
    def _generate_email_body(cls, vendor, document_list, upload_link):
        body = f"""Dear {vendor.name} Team,

Greetings from CarbonSentry Compliance Platform!

We are reaching out to request the submission of essential carbon compliance documents 
for your organization. As part of our vendor compliance verification process, we require 
the following documents to be uploaded at your earliest convenience.

REQUIRED DOCUMENTS:
─────────────────────────────────────────────────────────────
{document_list}
─────────────────────────────────────────────────────────────

UPLOAD LINK:
{upload_link}

IMPORTANT INFORMATION:
- This secure upload link can be used to upload ALL required documents
- Link is valid for 72 hours from the time of this email
- You can upload documents one at a time using the same link
- Please ensure documents are in PDF, JPG, PNG, DOC, or DOCX format
- Maximum file size: 10MB per document

UPLOAD INSTRUCTIONS:
1. Click on the upload link above
2. Select the document type from the dropdown
3. Choose your file using the upload interface
4. Confirm the upload
5. Repeat for each required document
6. You will receive a confirmation once each upload is successful

If you encounter any issues or have questions regarding the required documents, 
please contact our compliance team at compliance@carbonsentry.com

Thank you for your cooperation in maintaining environmental compliance standards.

Best regards,
CarbonSentry Compliance Team

─────────────────────────────────────────────────────────────
This is an automated message. Please do not reply to this email.
For support, contact: compliance@carbonsentry.com
─────────────────────────────────────────────────────────────"""

        return body.strip()