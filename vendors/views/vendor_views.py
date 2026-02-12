import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from vendors.models import Vendor, Document, Industry, IndustryRequiredDocument
from vendors.serializers.vendor_serializers import VendorListSerializer, VendorDetailSerializer
from vendors.serializers.document_serializers import DocumentListSerializer
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator


logger = logging.getLogger("vendors.vendor_views")


class VendorListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendors = Vendor.objects.filter(
            organization=request.user.organization
        ).select_related('industry')

        # Search filter (name or email)
        search = request.query_params.get('search', '').strip()
        if search:
            vendors = vendors.filter(
                Q(name__icontains=search) |
                Q(contact_email__icontains=search)
            )

        # Industry filter
        industry = request.query_params.get('industry', '').strip()
        if industry:
            vendors = vendors.filter(industry_id=industry)

        # Compliance status filter
        compliance_status = request.query_params.get('compliance_status', '').strip()
        if compliance_status:
            vendors = vendors.filter(compliance_status=compliance_status)

        # Risk level filter
        risk_level = request.query_params.get('risk_level', '').strip()
        if risk_level:
            vendors = vendors.filter(risk_level=risk_level)

        # Order by most recently updated
        vendors = vendors.order_by('-last_updated')

        # Pagination
        page_number = request.query_params.get('page', 1)
        page_size = 50
        
        try:
            page_number = int(page_number)
        except (ValueError, TypeError):
            page_number = 1

        paginator = Paginator(vendors, page_size)
        page_obj = paginator.get_page(page_number)

        serializer = VendorListSerializer(page_obj.object_list, many=True)
        
        logger.info(
            "Vendors list fetched",
            extra={
                "count": paginator.count,
                "page": page_number,
                "total_pages": paginator.num_pages,
                "filters": {
                    "search": search,
                    "industry": industry,
                    "compliance_status": compliance_status,
                    "risk_level": risk_level
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
                    "Vendor created successfully",
                    extra={
                        "vendor_id": str(vendor.id),
                        "document_count": len(documents),
                    }
                )
            
            serializer = VendorDetailSerializer(vendor)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception("Vendor creation failed")
            return Response(
                {"detail": "Failed to create vendor"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VendorSendEmailsView(APIView):
    """Separate view for sending emails to vendors"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        vendor_ids = request.data.get('vendor_ids', [])
        
        if not vendor_ids:
            return Response(
                {"error": "No vendors selected"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        vendors = Vendor.objects.filter(
            id__in=vendor_ids,
            organization=request.user.organization
        )
        
        if not vendors.exists():
            return Response(
                {"error": "No valid vendors found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            from vendors.services.email_campaign_service import EmailCampaignService
            EmailCampaignService.run(
                organization=request.user.organization,
                vendors=list(vendors),
            )
            
            logger.info(
                "Emails sent successfully",
                extra={
                    "vendor_count": vendors.count(),
                    "user": request.user.email
                }
            )
            
            return Response({
                "message": f"Emails sent to {vendors.count()} vendor(s)",
                "count": vendors.count()
            })
        except Exception as e:
            logger.exception("Failed to send emails")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    
class VendorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        try:
            vendor = Vendor.objects.select_related('industry').get(
                id=vendor_id,
                organization=request.user.organization,
            )
            serializer = VendorDetailSerializer(vendor)
            return Response(serializer.data)

        except Vendor.DoesNotExist:
            logger.warning("Vendor not found", extra={"vendor_id": str(vendor_id)})
            return Response(
                {"detail": "Vendor not found"},
                status=status.HTTP_404_NOT_FOUND
            )


class VendorDocumentListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, vendor_id):
        documents = Document.objects.filter(
            vendor_id=vendor_id,
            vendor__organization=request.user.organization,
        ).select_related('vendor', 'document_type')
        
        serializer = DocumentListSerializer(documents, many=True)
        return Response(serializer.data)