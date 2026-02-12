import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from vendors.models import Vendor, Document, Industry, IndustryRequiredDocument
from vendors.serializers.vendor_serializers import VendorListSerializer, VendorDetailSerializer
from vendors.serializers.document_serializers import DocumentListSerializer
from django.db import transaction
from django.db.models import Q, Prefetch
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

logger = logging.getLogger("vendors.vendor_views")


class VendorListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get paginated list of vendors with filters"""
        try:
            # Check if user has organization
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error(
                    "User has no organization",
                    extra={"user_id": str(request.user.id), "email": request.user.email}
                )
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )

            organization = request.user.organization
            
            # Base queryset with optimized queries
            vendors = Vendor.objects.filter(
                organization=organization
            ).select_related('industry').order_by('-last_updated')

            # Apply filters
            search = request.query_params.get('search', '').strip()
            if search:
                vendors = vendors.filter(
                    Q(name__icontains=search) |
                    Q(contact_email__icontains=search)
                )
                logger.debug(f"Applied search filter: {search}")

            industry = request.query_params.get('industry', '').strip()
            if industry:
                try:
                    vendors = vendors.filter(industry_id=industry)
                    logger.debug(f"Applied industry filter: {industry}")
                except Exception as e:
                    logger.warning(f"Invalid industry filter: {industry}", exc_info=True)

            compliance_status = request.query_params.get('compliance_status', '').strip()
            if compliance_status:
                vendors = vendors.filter(compliance_status=compliance_status)
                logger.debug(f"Applied compliance filter: {compliance_status}")

            risk_level = request.query_params.get('risk_level', '').strip()
            if risk_level:
                vendors = vendors.filter(risk_level=risk_level)
                logger.debug(f"Applied risk filter: {risk_level}")

            # Pagination
            page_number = request.query_params.get('page', 1)
            page_size = 50
            
            try:
                page_number = int(page_number)
                if page_number < 1:
                    page_number = 1
            except (ValueError, TypeError):
                logger.warning(f"Invalid page number: {page_number}, defaulting to 1")
                page_number = 1

            paginator = Paginator(vendors, page_size)
            
            try:
                page_obj = paginator.get_page(page_number)
            except EmptyPage:
                logger.warning(f"Page {page_number} is empty, returning last page")
                page_obj = paginator.get_page(paginator.num_pages)

            # Serialize data
            serializer = VendorListSerializer(page_obj.object_list, many=True)
            
            response_data = {
                'count': paginator.count,
                'total_pages': paginator.num_pages,
                'current_page': page_obj.number,
                'page_size': page_size,
                'results': serializer.data
            }

            logger.info(
                "Vendors list fetched successfully",
                extra={
                    "organization_id": str(organization.id),
                    "count": paginator.count,
                    "page": page_obj.number,
                    "filters_applied": {
                        "search": bool(search),
                        "industry": bool(industry),
                        "compliance": bool(compliance_status),
                        "risk": bool(risk_level)
                    }
                }
            )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(
                "Failed to fetch vendors list",
                extra={"user_id": str(request.user.id)}
            )
            return Response(
                {"error": "Failed to fetch vendors. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def post(self, request):
        """Create a new vendor"""
        logger.info(
            "Vendor creation request received",
            extra={"user": request.user.email, "data": request.data}
        )

        try:
            # Check organization
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error("User has no organization for vendor creation")
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )

            organization = request.user.organization

            # Extract and validate fields
            name = request.data.get('name', '').strip()
            industry_id = request.data.get('industry')
            country = request.data.get('country', '').strip()
            contact_email = request.data.get('contact_email', '').strip().lower()

            errors = {}
            
            if not name:
                errors['name'] = ["Vendor name is required"]
            elif len(name) > 255:
                errors['name'] = ["Vendor name must be 255 characters or less"]
            
            if not industry_id:
                errors['industry'] = ["Industry is required"]
            
            if not country:
                errors['country'] = ["Country is required"]
            elif len(country) > 100:
                errors['country'] = ["Country name must be 100 characters or less"]
            
            if not contact_email:
                errors['contact_email'] = ["Contact email is required"]
            
            if errors:
                logger.warning("Vendor creation validation failed", extra={"errors": errors})
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            
            # Validate industry exists
            try:
                industry = Industry.objects.get(id=industry_id)
            except Industry.DoesNotExist:
                logger.warning(f"Invalid industry ID: {industry_id}")
                return Response(
                    {"industry": ["Invalid industry selected"]},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                logger.exception("Error validating industry")
                return Response(
                    {"error": "Error validating industry"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Check for duplicate email
            if Vendor.objects.filter(
                organization=organization,
                contact_email=contact_email
            ).exists():
                logger.warning(
                    f"Duplicate vendor email: {contact_email}",
                    extra={"organization_id": str(organization.id)}
                )
                return Response(
                    {"contact_email": ["A vendor with this email already exists"]},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Create vendor with documents
            with transaction.atomic():
                vendor = Vendor.objects.create(
                    organization=organization,
                    name=name,
                    industry=industry,
                    country=country,
                    contact_email=contact_email,
                )

                # Create required documents
                required_docs = IndustryRequiredDocument.objects.filter(
                    industry=industry
                ).select_related('document_type')

                if not required_docs.exists():
                    logger.warning(
                        f"No required documents found for industry: {industry.name}",
                        extra={"industry_id": str(industry.id)}
                    )

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
                        "vendor_name": vendor.name,
                        "industry": industry.name,
                        "document_count": len(documents),
                        "organization_id": str(organization.id)
                    }
                )
            
            serializer = VendorDetailSerializer(vendor)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(
                "Vendor creation failed",
                extra={"user_id": str(request.user.id)}
            )
            return Response(
                {"error": "Failed to create vendor. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VendorSendEmailsView(APIView):
    """Send document request emails to vendors"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            vendor_ids = request.data.get('vendor_ids', [])
            
            if not vendor_ids:
                logger.warning("Email send attempted with no vendor IDs")
                return Response(
                    {"error": "No vendors selected"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not isinstance(vendor_ids, list):
                logger.warning(f"Invalid vendor_ids format: {type(vendor_ids)}")
                return Response(
                    {"error": "vendor_ids must be a list"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check organization
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error("User has no organization for email sending")
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            organization = request.user.organization
            
            vendors = Vendor.objects.filter(
                id__in=vendor_ids,
                organization=organization
            )
            
            if not vendors.exists():
                logger.warning(
                    "No valid vendors found for email sending",
                    extra={"vendor_ids": vendor_ids, "organization_id": str(organization.id)}
                )
                return Response(
                    {"error": "No valid vendors found"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            from vendors.services.email_campaign_service import EmailCampaignService
            
            EmailCampaignService.run(
                organization=organization,
                vendors=list(vendors),
            )
            
            logger.info(
                "Emails sent successfully",
                extra={
                    "vendor_count": vendors.count(),
                    "vendor_ids": [str(v.id) for v in vendors],
                    "user": request.user.email,
                    "organization_id": str(organization.id)
                }
            )
            
            return Response({
                "message": f"Emails sent to {vendors.count()} vendor(s)",
                "count": vendors.count()
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(
                "Failed to send emails",
                extra={"user_id": str(request.user.id)}
            )
            return Response(
                {"error": "Failed to send emails. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    
class VendorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        try:
            # Check organization
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error("User has no organization")
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )

            vendor = Vendor.objects.select_related('industry').get(
                id=vendor_id,
                organization=request.user.organization,
            )
            
            serializer = VendorDetailSerializer(vendor)
            
            logger.info(
                "Vendor details fetched",
                extra={
                    "vendor_id": str(vendor_id),
                    "user_id": str(request.user.id)
                }
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Vendor.DoesNotExist:
            logger.warning(
                "Vendor not found or access denied",
                extra={
                    "vendor_id": str(vendor_id),
                    "user_id": str(request.user.id)
                }
            )
            return Response(
                {"error": "Vendor not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception(
                "Failed to fetch vendor details",
                extra={"vendor_id": str(vendor_id)}
            )
            return Response(
                {"error": "Failed to fetch vendor details"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VendorDocumentListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, vendor_id):
        try:
            # Check organization and vendor access
            if not hasattr(request.user, 'organization') or not request.user.organization:
                logger.error("User has no organization")
                return Response(
                    {"error": "User is not associated with any organization"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Verify vendor exists and belongs to organization
            vendor_exists = Vendor.objects.filter(
                id=vendor_id,
                organization=request.user.organization
            ).exists()

            if not vendor_exists:
                logger.warning(
                    "Vendor not found or access denied",
                    extra={
                        "vendor_id": str(vendor_id),
                        "user_id": str(request.user.id)
                    }
                )
                return Response(
                    {"error": "Vendor not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            documents = Document.objects.filter(
                vendor_id=vendor_id,
                vendor__organization=request.user.organization,
            ).select_related('vendor', 'document_type').prefetch_related('validation')
            
            serializer = DocumentListSerializer(documents, many=True)
            
            logger.info(
                "Vendor documents fetched",
                extra={
                    "vendor_id": str(vendor_id),
                    "document_count": documents.count(),
                    "user_id": str(request.user.id)
                }
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(
                "Failed to fetch vendor documents",
                extra={"vendor_id": str(vendor_id)}
            )
            return Response(
                {"error": "Failed to fetch documents"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )