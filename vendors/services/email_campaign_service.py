import logging
from collections import defaultdict
from django.conf import settings
from django.db import transaction

from vendors.models import Document
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
                body_template="Please upload the following documents:\n\n{LINKS}",
            )

            for vendor in vendor_list:
                pending_docs = Document.objects.filter(vendor=vendor, status="pending")
                links = []

                for doc in pending_docs:
                    token = UploadTokenService.generate_for_document(doc)
                    links.append(
                        f"- {doc.document_type.name}: "
                        f"{settings.FRONTEND_URL}/upload/{token}"
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

                    
    @classmethod
    def _generate_email_body(cls, vendor, document_links):
        

        body = f"""
                Dear {vendor.name} Team,

                Greetings from CarbonSentry Compliance Platform!

                We are reaching out to request the submission of essential carbon compliance documents 
                for your organization. As part of our vendor compliance verification process, we require 
                the following documents to be uploaded at your earliest convenience.

                REQUIRED DOCUMENTS:
                ─────────────────────────────────────────────────────────────
                {document_links}
                ─────────────────────────────────────────────────────────────

                IMPORTANT INFORMATION:
                - Each document has a secure, unique upload link
                - Links are valid for 72 hours from the time of this email
                - Each link can only be used once for security purposes
                - Please ensure documents are in PDF, JPG, PNG, DOC, or DOCX format
                - Maximum file size: 10MB per document

                UPLOAD INSTRUCTIONS:
                1. Click on the link corresponding to each document type
                2. Select your file using the upload interface
                3. Confirm the upload
                4. You will receive a confirmation once the upload is successful

                If you encounter any issues or have questions regarding the required documents, 
                please contact our compliance team at compliance@carbonsentry.com

                Thank you for your cooperation in maintaining environmental compliance standards.

                Best regards,
                CarbonSentry Compliance Team

                ─────────────────────────────────────────────────────────────
                This is an automated message. Please do not reply to this email.
                For support, contact: compliance@carbonsentry.com
                ─────────────────────────────────────────────────────────────
                        """

        return body.strip()