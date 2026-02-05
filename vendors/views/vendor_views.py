import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from vendors.models import Vendor, Document,Industry, IndustryRequiredDocument
from vendors.serializers.vendor_serializers import VendorListSerializer, VendorDetailSerializer
from vendors.serializers.document_serializers import DocumentListSerializer
from django.db import transaction

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
    
    def post(self, request):
        logger.info(
            "Vendor creation request received",
            extra={"user": request.user.email, "data": request.data}
        )

        try:
            name = request.data.get('name', '').strip()
            industry_id = request.data.get('industry')
            country = request.data.get('country', '').strip()
            contact_email = request.data.get('contact_email', '').strip().lower()

            errors = {}
            
            if not name:
                errors['name'] = ["Vendor name is required"]
            
            if not industry_id:
                errors['industry'] = ["Industry is required"]
            
            if not country:
                errors['country'] = ["Country is required"]
            
            if not contact_email:
                errors['contact_email'] = ["Contact email is required"]
            
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            try:
                industry = Industry.objects.get(id=industry_id)
            except Industry.DoesNotExist:
                return Response(
                    {"industry": ["Invalid industry selected"]},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if Vendor.objects.filter(
                organization=request.user.organization,
                contact_email=contact_email
            ).exists():
                return Response(
                    {"contact_email": ["A vendor with this email already exists"]},
                    status=status.HTTP_400_BAD_REQUEST
                )

            with transaction.atomic():
                vendor = Vendor.objects.create(
                    organization=request.user.organization,
                    name=name,
                    industry=industry,
                    country=country,
                    contact_email=contact_email,
                )

                required_docs = IndustryRequiredDocument.objects.filter(
                    industry=industry
                ).select_related('document_type')

                documents = [
                    Document(
                        vendor=vendor,
                        document_type=req.document_type,
                        status="pending",
                    )
                    for req in required_docs
                ]

                if documents:
                    Document.objects.bulk_create(documents)

                logger.info(
                    f"Vendor created: {vendor.name} with {len(documents)} documents",
                    extra={
                        "vendor_id": str(vendor.id),
                        "document_count": len(documents),
                    }
                )
            serializer = VendorDetailSerializer(vendor)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Vendor creation failed: {str(e)}")
            return Response(
                {"detail": "Failed to create vendor"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
