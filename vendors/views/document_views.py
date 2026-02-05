import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q

from vendors.models import Document
from vendors.serializers.document_serializers import (
    DocumentListSerializer,
    DocumentDetailSerializer
)

logger = logging.getLogger("vendors.document_views")


class DocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        documents = Document.objects.filter(
            vendor__organization=request.user.organization
        ).select_related('vendor', 'document_type')

        status_filter = request.query_params.get('status')
        if status_filter:
            documents = documents.filter(status=status_filter)

        vendor_filter = request.query_params.get('vendor')
        if vendor_filter:
            documents = documents.filter(vendor_id=vendor_filter)

        search = request.query_params.get('search')
        if search:
            documents = documents.filter(
                Q(vendor__name__icontains=search) |
                Q(document_type__name__icontains=search)
            )

        documents = documents.order_by('-uploaded_at', '-id')
        page = request.query_params.get('page', 1)
        try:
            page = int(page)
        except ValueError:
            page = 1
        serializer = DocumentListSerializer(documents, many=True)
        
        logger.info(
            "Documents list fetched",
            extra={
                "count": documents.count(),
                "organization": request.user.organization.name
            }
        )

        return Response(serializer.data)


class DocumentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        try:
            document = Document.objects.select_related(
                'vendor',
                'vendor__industry',
                'document_type'
            ).get(
                id=document_id,
                vendor__organization=request.user.organization,
            )
            
            serializer = DocumentDetailSerializer(document)
            return Response(serializer.data)

        except Document.DoesNotExist:
            logger.warning(
                "Document not found",
                extra={"document_id": document_id}
            )
            return Response(
                {"detail": "Document not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )