import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q
from django.core.paginator import Paginator, EmptyPage
from rest_framework.views import APIView
from audit_logs.models import AuditLog
from vendors.models import Document
from vendors.services.upload_token_services import UploadTokenService
from vendors.services.email_service import EmailService
from vendors.serializers.document_serializers import (
    DocumentListSerializer,
    DocumentDetailSerializer
)

logger = logging.getLogger("vendors.document_views")


class DocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error("User has no organization")
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )

            organization = request.user.organization
            documents = Document.objects.filter(
                vendor__organization=organization
            ).select_related(
                'vendor', 
                'vendor__organization',
                'document_type', 
                'vendor__industry'
            ).prefetch_related('validation','validation__metadata')

            # Apply filters
            status_filter = request.query_params.get('status', '').strip()
            if status_filter:
                documents = documents.filter(status=status_filter)
                logger.debug(f"Applied status filter: {status_filter}")

            vendor_filter = request.query_params.get('vendor', '').strip()
            if vendor_filter:
                try:
                    documents = documents.filter(vendor_id=vendor_filter)
                    logger.debug(f"Applied vendor filter: {vendor_filter}")
                except Exception as e:
                    logger.warning(f"Invalid vendor filter: {vendor_filter}")

            search = request.query_params.get('search', '').strip()
            if search:
                documents = documents.filter(
                    Q(vendor__name__icontains=search) |
                    Q(document_type__name__icontains=search)
                )
                logger.debug(f"Applied search filter: {search}")

            # Order by most recent
            documents = documents.order_by('-uploaded_at', '-id')

            # Pagination
            page_number = request.query_params.get('page', 1)
            page_size = 50
            
            try:
                page_number = int(page_number)
                if page_number < 1:
                    page_number = 1
            except (ValueError, TypeError):
                logger.warning(f"Invalid page number: {page_number}")
                page_number = 1

            paginator = Paginator(documents, page_size)
            
            try:
                page_obj = paginator.get_page(page_number)
            except EmptyPage:
                logger.warning(f"Page {page_number} is empty")
                page_obj = paginator.get_page(paginator.num_pages)

            # Serialize
            serializer = DocumentListSerializer(
            page_obj.object_list, 
            many=True,
            context={'request': request} 
        )
            
            response_data = {
                'count': paginator.count,
                'total_pages': paginator.num_pages,
                'current_page': page_obj.number,
                'page_size': page_size,
                'results': serializer.data
            }

            logger.info(
                "Documents list fetched successfully",
                extra={
                    "organization_id": str(organization.id),
                    "count": paginator.count,
                    "page": page_obj.number,
                    "filters": {
                        "status": status_filter,
                        "vendor": vendor_filter,
                        "search": search
                    }
                }
            )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(
                "Failed to fetch documents",
                extra={"user_id": str(request.user.id)}
            )
            return Response(
                {"error": "Failed to fetch documents. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DocumentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        try:
            # Check organization
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error("User has no organization")
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )

            document = Document.objects.select_related(
                'vendor',
                'vendor__industry',
                'document_type'
            ).get(
                id=document_id,
                vendor__organization=request.user.organization,
            )
            
            serializer = DocumentDetailSerializer(
            document,
            context={'request': request} 
        )
            
            logger.info(
                "Document details fetched",
                extra={
                    "document_id": str(document_id),
                    "user_id": str(request.user.id)
                }
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Document.DoesNotExist:
            logger.warning(
                "Document not found or access denied",
                extra={
                    "document_id": str(document_id),
                    "user_id": str(request.user.id)
                }
            )
            return Response(
                {"error": "Document not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception(
                "Failed to fetch document details",
                extra={"document_id": str(document_id)}
            )
            return Response(
                {"error": "Failed to fetch document details"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        

class DocumentResendLinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, document_id):
        try:
            doc = Document.objects.select_related('vendor').get(
                id=document_id,
                vendor__organization=request.user.organization
            )
        except Document.DoesNotExist:
            return Response(
                {'error': 'Document not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # only allow resend for invalid or expired documents
        if doc.status not in ('invalid', 'expired'):
            return Response(
                {'error': f'Cannot resend link for document with status: {doc.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        vendor = doc.vendor

        # reset document so it appears in vendor upload form again
        doc.status = 'pending'
        doc.file   = None
        doc.save(update_fields=['status', 'file'])

        # regenerate the 72hr upload token on the vendor
        UploadTokenService.generate_for_vendor(vendor)

        # send fresh upload link email
        EmailService.send_upload_link(
            vendor=vendor,
            email=vendor.contact_email
        )

        # audit trail
        AuditLog.objects.create(
            actor=request.user,
            action='document_reupload_requested',
            entity_type='document',
            entity_id=str(doc.id),
            details={
                'document_type': doc.document_type,
                'vendor_id': str(vendor.id),
                'vendor_name': vendor.name,
                'sent_to': vendor.contact_email,
            }
        )

        return Response({
            'message': f'Upload link resent to {vendor.contact_email}',
            'vendor_id': str(vendor.id),
            'document_id': str(doc.id),
        })