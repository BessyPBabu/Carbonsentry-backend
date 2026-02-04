import logging
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from vendors.models import Document
from vendors.serializers.public_upload_serializers import VendorPublicUploadSerializer

logger = logging.getLogger("vendors.public_upload")


class VendorPublicUploadView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, token):
        try:
            document = get_object_or_404(
                Document,
                upload_token=token,
                status="pending"  
            )

            if not document.upload_token_expires_at or document.upload_token_expires_at < timezone.now():
                logger.warning(
                    "Upload rejected - token expired",
                    extra={"token": token[:8] + "..."}
                )
                return Response(
                    {"detail": "This upload link has expired"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = VendorPublicUploadSerializer(
                data=request.data,
                context={"document": document},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            logger.info(
                "Document uploaded successfully",
                extra={
                    "document_id": str(document.id),
                    "vendor_id": str(document.vendor_id),
                }
            )

            return Response(
                {"message": "Document uploaded successfully"},
                status=status.HTTP_200_OK,
            )

        except Exception:
            logger.exception("Public upload failed")
            return Response(
                {"detail": "Upload failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )