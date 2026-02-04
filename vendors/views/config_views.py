import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from vendors.models import Industry, DocumentType, IndustryRequiredDocument
from vendors.serializers.industry_serializers import IndustrySerializer
from vendors.serializers.document_type_serializers import DocumentTypeSerializer
from vendors.serializers.industry_document_serializers import (
    IndustryRequiredDocumentSerializer,
)

logger = logging.getLogger("vendors.config_views")


class IndustryListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        industries = Industry.objects.all().order_by("name")
        return Response(IndustrySerializer(industries, many=True).data)

    def post(self, request):
        serializer = IndustrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        industry = serializer.save()
        logger.info("Industry created", extra={"industry": industry.name})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DocumentTypeListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        docs = DocumentType.objects.all().order_by("name")
        return Response(DocumentTypeSerializer(docs, many=True).data)

    def post(self, request):
        serializer = DocumentTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc = serializer.save()
        logger.info("Document type created", extra={"document": doc.name})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class IndustryRequiredDocumentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        mappings = (
            IndustryRequiredDocument.objects
            .select_related("industry", "document_type")
            .order_by("industry__name")
        )
        serializer = IndustryRequiredDocumentSerializer(mappings, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = IndustryRequiredDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mapping = serializer.save()
        logger.info(
            "Industry document mapping created",
            extra={
                "industry": mapping.industry.name,
                "document": mapping.document_type.name,
            },
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)
