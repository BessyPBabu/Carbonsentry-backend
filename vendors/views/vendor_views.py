import logging
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator, EmptyPage
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from accounts.permissions import IsAdmin, IsOfficer
from vendors.models import Vendor, Document, Industry, IndustryRequiredDocument
from vendors.serializers.vendor_serializers import VendorListSerializer, VendorDetailSerializer
from vendors.serializers.document_serializers import DocumentListSerializer
from vendors.utils.validators import (
    validate_vendor_name,
    validate_vendor_email,
    validate_vendor_country,
)

logger = logging.getLogger("vendors.vendor_views")



def get_organization(request):
    if hasattr(request.user, "organization") and request.user.organization:
        return request.user.organization
    return None


def no_org_response():
    return Response(
        {"error": "User is not associated with any organization"},
        status=status.HTTP_403_FORBIDDEN,
    )


def paginate(queryset, page_number, page_size=50):
    try:
        page_number = max(1, int(page_number))
    except (ValueError, TypeError):
        page_number = 1

    paginator = Paginator(queryset, page_size)
    try:
        return paginator, paginator.get_page(page_number)
    except EmptyPage:
        return paginator, paginator.get_page(paginator.num_pages)


class VendorListCreateView(APIView):

    def get_permissions(self):

        if self.request.method == "POST":
            if not self.request.user or not self.request.user.is_authenticated:
                return [IsAuthenticated()]
            return [IsAuthenticated(), (IsOfficer() if self.request.user.role == "officer" else IsAdmin())]
        return [IsAuthenticated()]

    def get(self, request):
        org = get_organization(request)
        if not org:
            return no_org_response()

        vendors = (
            Vendor.objects
            .filter(organization=org)
            .select_related("industry")
            .order_by("-last_updated")
        )

        search = request.query_params.get("search", "").strip()
        if search:
            vendors = vendors.filter(
                Q(name__icontains=search) | Q(contact_email__icontains=search)
            )

        industry_id = request.query_params.get("industry", "").strip()
        if industry_id:
            vendors = vendors.filter(industry_id=industry_id)

        compliance_status = request.query_params.get("compliance_status", "").strip()
        if compliance_status:
            vendors = vendors.filter(compliance_status=compliance_status)

        risk_level = request.query_params.get("risk_level", "").strip()
        if risk_level:
            vendors = vendors.filter(risk_level=risk_level)

        paginator, page_obj = paginate(vendors, request.query_params.get("page", 1))

        logger.info(
            "vendors.list | org=%s count=%s page=%s",
            org.id, paginator.count, page_obj.number,
        )

        return Response({
            "count": paginator.count,
            "total_pages": paginator.num_pages,
            "current_page": page_obj.number,
            "page_size": 50,
            "results": VendorListSerializer(page_obj.object_list, many=True).data,
        })

    def post(self, request):
        org = get_organization(request)
        if not org:
            return no_org_response()

        industry_id = request.data.get("industry")
        errors = {}
        name = country = contact_email = None

        try:
            name = validate_vendor_name(request.data.get("name", ""))
        except DRFValidationError as exc:
            errors["name"] = list(exc.detail)

        try:
            country = validate_vendor_country(request.data.get("country", ""))
        except DRFValidationError as exc:
            errors["country"] = list(exc.detail)

        try:
            contact_email = validate_vendor_email(request.data.get("contact_email", ""))
        except DRFValidationError as exc:
            errors["contact_email"] = list(exc.detail)

        if not industry_id:
            errors["industry"] = ["Industry is required"]

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            industry = Industry.objects.get(id=industry_id)
        except Industry.DoesNotExist:
            return Response(
                {"industry": ["Invalid industry selected"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Vendor.objects.filter(organization=org, contact_email=contact_email).exists():
            return Response(
                {"contact_email": ["A vendor with this email already exists"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            vendor = Vendor.objects.create(
                organization=org,
                name=name,
                industry=industry,
                country=country,
                contact_email=contact_email,
            )

            required_docs = (
                IndustryRequiredDocument.objects
                .filter(industry=industry)
                .select_related("document_type")
            )
            documents = [
                Document(vendor=vendor, document_type=req.document_type, status="pending")
                for req in required_docs
            ]
            if documents:
                Document.objects.bulk_create(documents)

        logger.info(
            "vendors.created | vendor=%s org=%s docs=%s",
            vendor.id, org.id, len(documents),
        )
        return Response(VendorDetailSerializer(vendor).data, status=status.HTTP_201_CREATED)


class VendorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        org = get_organization(request)
        if not org:
            return no_org_response()

        try:
            vendor = Vendor.objects.select_related("industry").get(
                id=vendor_id, organization=org
            )
        except Vendor.DoesNotExist:
            return Response({"error": "Vendor not found"}, status=status.HTTP_404_NOT_FOUND)

        logger.info("vendors.detail | vendor=%s user=%s", vendor_id, request.user.id)
        return Response(VendorDetailSerializer(vendor).data)


class VendorDocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        org = get_organization(request)
        if not org:
            return no_org_response()

        if not Vendor.objects.filter(id=vendor_id, organization=org).exists():
            return Response({"error": "Vendor not found"}, status=status.HTTP_404_NOT_FOUND)

        documents = (
            Document.objects
            .filter(vendor_id=vendor_id, vendor__organization=org)
            .select_related("vendor", "document_type")
            .prefetch_related("validation")
        )

        logger.info(
            "vendors.documents | vendor=%s count=%s user=%s",
            vendor_id, documents.count(), request.user.id,
        )
        return Response(DocumentListSerializer(documents, many=True).data)


class VendorSendEmailsView(APIView):
    permission_classes = [IsAuthenticated, IsOfficer | IsAdmin]

    def post(self, request):
        vendor_ids = request.data.get("vendor_ids", [])

        if not vendor_ids:
            return Response({"error": "No vendors selected"}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(vendor_ids, list):
            return Response(
                {"error": "vendor_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST
            )

        org = get_organization(request)
        if not org:
            return no_org_response()

        vendors = Vendor.objects.filter(id__in=vendor_ids, organization=org)
        if not vendors.exists():
            return Response({"error": "No valid vendors found"}, status=status.HTTP_404_NOT_FOUND)

        from vendors.services.email_campaign_service import EmailCampaignService
        EmailCampaignService.run(organization=org, vendors=list(vendors))

        logger.info(
            "vendors.emails_sent | count=%s user=%s org=%s",
            vendors.count(), request.user.email, org.id,
        )
        return Response({
            "message": f"Emails sent to {vendors.count()} vendor(s)",
            "count": vendors.count(),
        })