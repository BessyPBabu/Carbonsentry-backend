import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from vendors.models import Vendor, Document
from vendors.serializers.vendor_serializers import VendorListSerializer
from vendors.serializers.document_serializers import DocumentListSerializer

logger = logging.getLogger("vendors.vendor_views")


class VendorListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendors = Vendor.objects.filter(
            organization=request.user.organization
        )

        logger.info("Vendor list fetched", extra={"count": vendors.count()})

        serializer = VendorListSerializer(vendors, many=True)
        return Response(serializer.data)


class VendorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        try:
            vendor = Vendor.objects.get(
                id=vendor_id,
                organization=request.user.organization,
            )
            serializer = VendorListSerializer(vendor)
            return Response(serializer.data)

        except Vendor.DoesNotExist:
            logger.warning("Vendor not found", extra={"vendor_id": vendor_id})
            return Response(status=status.HTTP_404_NOT_FOUND)


class VendorDocumentListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, vendor_id):
        documents = Document.objects.filter(
            vendor_id=vendor_id,
            vendor__organization=request.user.organization,
        )

        serializer = DocumentListSerializer(documents, many=True)
        return Response(serializer.data)
