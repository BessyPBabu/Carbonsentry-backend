import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q
from django.core.paginator import Paginator


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
        ).select_related(
            'vendor', 
            'document_type', 
            'vendor__industry'
        ).prefetch_related(
            'validation'  # Add this to optimize validation queries
        )

       # Status filter
        status_filter = request.query_params.get('status', '').strip()
        if status_filter:
            documents = documents.filter(status=status_filter)

        # Vendor filter
        vendor_filter = request.query_params.get('vendor', '').strip()
        if vendor_filter:
            documents = documents.filter(vendor_id=vendor_filter)

        # Search filter (vendor name or document type)
        search = request.query_params.get('search', '').strip()
        if search:
            documents = documents.filter(
                Q(vendor__name__icontains=search) |
                Q(document_type__name__icontains=search)
            )

        # Order by uploaded date (most recent first)
        documents = documents.order_by('-uploaded_at', '-id')

        # Pagination
        page_number = request.query_params.get('page', 1)
        page_size = 50
        
        try:
            page_number = int(page_number)
        except (ValueError, TypeError):
            page_number = 1

        paginator = Paginator(documents, page_size)
        page_obj = paginator.get_page(page_number)

        # Serialize
        serializer = DocumentListSerializer(page_obj.object_list, many=True)
        
        logger.info(
            "Documents list fetched",
            extra={
                "count": paginator.count,
                "page": page_number,
                "total_pages": paginator.num_pages,
                "organization": request.user.organization.name,
                "filters": {
                    "status": status_filter,
                    "vendor": vendor_filter,
                    "search": search
                }
            }
        )

        return Response({
            'count': paginator.count,
            'total_pages': paginator.num_pages,
            'current_page': page_number,
            'page_size': page_size,
            'results': serializer.data
        })


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