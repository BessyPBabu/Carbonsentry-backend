import logging
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from vendors.models import Document, Vendor
from vendors.serializers.public_upload_serializers import VendorPublicUploadSerializer

logger = logging.getLogger("vendors.public_upload")


class VendorPublicUploadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            vendor = get_object_or_404(
                Vendor,
                upload_token=token,
            )

            if not vendor.upload_token_expires_at or vendor.upload_token_expires_at < timezone.now():
                logger.warning(
                    "Upload link access rejected - token expired",
                    extra={
                        "token": token[:8] + "...",
                        "vendor_id": str(vendor.id)
                    }
                )
                return Response(
                    {"detail": "This upload link has expired. Please contact support for a new link."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            pending_docs = Document.objects.filter(
                vendor=vendor,
                status="pending"
            ).select_related('document_type')

            logger.info(
                "Upload form accessed",
                extra={
                    "vendor_id": str(vendor.id),
                    "pending_count": pending_docs.count()
                }
            )

            return Response({
                "vendor_name": vendor.name,
                "pending_documents": [
                    {
                        "id": str(doc.id),
                        "document_type": doc.document_type.name,
                        "document_type_id": str(doc.document_type.id),
                    }
                    for doc in pending_docs
                ]
            }, status=status.HTTP_200_OK)

        except Vendor.DoesNotExist:
            logger.warning(
                "Upload link access rejected - invalid token",
                extra={"token": token[:8] + "..."}
            )
            return Response(
                {"detail": "Invalid upload link. Please check your email for the correct link."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.exception(
                "Failed to load upload form",
                extra={"token": token[:8] + "..."}
            )
            return Response(
                {"detail": "Failed to load upload form. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request, token):
        try:
            vendor = get_object_or_404(
                Vendor,
                upload_token=token,
            )
            if not vendor.upload_token_expires_at or vendor.upload_token_expires_at < timezone.now():
                logger.warning(
                    "Upload rejected - token expired",
                    extra={
                        "token": token[:8] + "...",
                        "vendor_id": str(vendor.id)
                    }
                )
                return Response(
                    {"detail": "This upload link has expired. Please contact support for a new link."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            document_id = request.data.get('document_id')
            if not document_id:
                logger.warning(
                    "Upload rejected - missing document_id",
                    extra={"vendor_id": str(vendor.id)}
                )
                return Response(
                    {"detail": "Document ID is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                document = Document.objects.get(
                    id=document_id,
                    vendor=vendor,
                    status="pending"
                )
            except Document.DoesNotExist:
                logger.warning(
                    "Upload rejected - document not found or already uploaded",
                    extra={
                        "vendor_id": str(vendor.id),
                        "document_id": document_id
                    }
                )
                return Response(
                    {"detail": "Document not found or already uploaded"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            serializer = VendorPublicUploadSerializer(
                data=request.data,
                context={"document": document, "vendor": vendor},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            logger.info(
                "Document uploaded successfully",
                extra={
                    "document_id": str(document.id),
                    "vendor_id": str(vendor.id),
                    "document_type": document.document_type.name
                }
            )
            remaining_pending = Document.objects.filter(
                vendor=vendor,
                status="pending"
            ).exists()

            if not remaining_pending:
                vendor.upload_token = None
                vendor.upload_token_expires_at = None
                vendor.save(update_fields=["upload_token", "upload_token_expires_at"])
                
                logger.info(
                    "All documents uploaded - token invalidated",
                    extra={"vendor_id": str(vendor.id)}
                )

            return Response(
                {
                    "detail": "Document uploaded successfully",
                    "all_complete": not remaining_pending
                },
                status=status.HTTP_200_OK,
            )

        except Vendor.DoesNotExist:
            logger.warning(
                "Upload rejected - invalid token",
                extra={"token": token[:8] + "..."}
            )
            return Response(
                {"detail": "Invalid upload link. Please check your email for the correct link."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.exception(
                "Public upload failed",
                extra={
                    "token": token[:8] + "...",
                    "error": str(e)
                }
            )
            return Response(
                {"detail": "Upload failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )