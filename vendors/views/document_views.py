import logging

from django.conf import settings
from django.core.paginator import Paginator, EmptyPage
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrOfficer
from audit_logs.services import log_action
from vendors.models import Document
from vendors.serializers.document_serializers import (
    DocumentDetailSerializer,
    DocumentListSerializer,
)
from vendors.services.email_service import EmailService
from vendors.services.upload_token_services import UploadTokenService

logger = logging.getLogger("vendors.document_views")


class DocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            if not hasattr(request.user, 'organization') or not request.user.organization:
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            org = request.user.organization
            documents = (
                Document.objects
                .filter(vendor__organization=org)
                .select_related(
                    'vendor', 'vendor__organization',
                    'document_type', 'vendor__industry',
                )
                .prefetch_related('validation', 'validation__metadata')
            )

            status_filter = request.query_params.get('status', '').strip()
            if status_filter:
                documents = documents.filter(status=status_filter)

            vendor_filter = request.query_params.get('vendor', '').strip()
            if vendor_filter:
                try:
                    documents = documents.filter(vendor_id=vendor_filter)
                except Exception:
                    pass

            search = request.query_params.get('search', '').strip()
            if search:
                documents = documents.filter(
                    Q(vendor__name__icontains=search) |
                    Q(document_type__name__icontains=search)
                )

            documents = documents.order_by('-uploaded_at', '-id')

            try:
                page_number = max(1, int(request.query_params.get('page', 1)))
            except (ValueError, TypeError):
                page_number = 1

            page_size = int(request.query_params.get('page_size', 50))
            paginator = Paginator(documents, page_size)
            try:
                page_obj = paginator.get_page(page_number)
            except EmptyPage:
                page_obj = paginator.get_page(paginator.num_pages)

            serializer = DocumentListSerializer(
                page_obj.object_list, many=True, context={'request': request}
            )

            logger.info("Documents list fetched successfully")
            return Response({
                'count':        paginator.count,
                'total_pages':  paginator.num_pages,
                'current_page': page_obj.number,
                'page_size':    page_size,
                'results':      serializer.data,
            })

        except Exception:
            logger.exception("DocumentListView: failed for user=%s", request.user.id)
            return Response(
                {"error": "Failed to fetch documents. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DocumentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        try:
            if not hasattr(request.user, 'organization') or not request.user.organization:
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            document = Document.objects.select_related(
                'vendor', 'vendor__industry', 'document_type'
            ).get(
                id=document_id,
                vendor__organization=request.user.organization,
            )

            return Response(
                DocumentDetailSerializer(document, context={'request': request}).data
            )

        except Document.DoesNotExist:
            return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("DocumentDetailView: failed document=%s", document_id)
            return Response(
                {"error": "Failed to fetch document details"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DocumentResendLinkView(APIView):
    """
    POST /api/vendors/documents/<uuid>/resend-link/

    Allowed statuses: pending, invalid, expired
      - pending  → link never used or expired; vendor needs a fresh link
      - invalid  → AI rejected; reset to pending so vendor can reupload
      - expired  → cert expired; reset to pending so vendor can reupload

    Frontend must show the resend button for all three statuses.
    """
    permission_classes = [IsAuthenticated, IsAdminOrOfficer]

    def post(self, request, document_id):
        # ── Fetch document ────────────────────────────────────────────────
        try:
            doc = Document.objects.select_related(
                'vendor', 'vendor__industry', 'document_type'
            ).get(
                id=document_id,
                vendor__organization=request.user.organization,
            )
        except Document.DoesNotExist:
            return Response(
                {'error': 'Document not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── Status guard ──────────────────────────────────────────────────
        resendable = ('pending', 'invalid', 'expired')
        if doc.status not in resendable:
            return Response(
                {
                    'error': (
                        f'Cannot resend upload link for a document '
                        f'with status "{doc.status}". '
                        f'Only {resendable} documents can be resent.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        vendor           = doc.vendor
        previous_status  = doc.status

        # ── Reset invalid/expired docs back to pending ────────────────────
        # pending docs: keep as-is, just regenerate the token
        if doc.status in ('invalid', 'expired'):
            doc.status = 'pending'
            doc.file   = None
            doc.save(update_fields=['status', 'file'])
            logger.info(
                "DocumentResendLinkView: reset doc=%s from %s → pending",
                doc.id, previous_status,
            )

        # ── Generate fresh 72-hr upload token ────────────────────────────
        token       = UploadTokenService.generate_for_vendor(vendor)
        upload_link = f"{settings.FRONTEND_URL}/upload/{token}"

        # ── Send email ────────────────────────────────────────────────────
        try:
            EmailService.send(
                subject="CarbonSentry — New Document Upload Link",
                body=(
                    f"Dear {vendor.name} Team,\n\n"
                    f"A new secure upload link has been generated for your compliance document submission.\n\n"
                    f"Document: {doc.document_type.name}\n"
                    f"Upload Link: {upload_link}\n\n"
                    f"This link is valid for 72 hours.\n\n"
                    f"For support: compliance@carbonsentry.com\n\n"
                    f"Best regards,\nCarbonSentry Compliance Team"
                ),
                recipient=vendor.contact_email,
            )
            email_sent = True
        except Exception as exc:
            logger.error(
                "DocumentResendLinkView: email failed vendor=%s — %s", vendor.id, exc
            )
            email_sent = False

        # ── Audit log — uses the correct action key ───────────────────────
        log_action(
            action='document_reupload_requested',
            entity_type='Document',
            entity_id=str(doc.id),
            organization=request.user.organization,
            actor=request.user,
            request=request,
            details={
                'document_type':   doc.document_type.name,
                'vendor_id':       str(vendor.id),
                'vendor_name':     vendor.name,
                'sent_to':         vendor.contact_email,
                'previous_status': previous_status,
                'email_sent':      email_sent,
            },
        )

        logger.info(
            "DocumentResendLinkView: ok doc=%s vendor=%s previous=%s email=%s",
            doc.id, vendor.id, previous_status, email_sent,
        )

        return Response({
            'message':     f'Upload link sent to {vendor.contact_email}',
            'vendor_id':   str(vendor.id),
            'document_id': str(doc.id),
            'email_sent':  email_sent,
        })