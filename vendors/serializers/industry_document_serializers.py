from rest_framework import serializers
from vendors.models import IndustryRequiredDocument


class IndustryRequiredDocumentSerializer(serializers.ModelSerializer):
    industry_name = serializers.CharField(source="industry.name", read_only=True)
    document_name = serializers.CharField(source="document_type.name", read_only=True)

    class Meta:
        model = IndustryRequiredDocument
        fields = [
            "id",
            "industry",
            "industry_name",
            "document_type",
            "document_name",
            "mandatory",
        ]

    def validate(self, attrs):
        industry = attrs.get("industry")
        document_type = attrs.get("document_type")

        if IndustryRequiredDocument.objects.filter(
            industry=industry,
            document_type=document_type,
        ).exists():
            raise serializers.ValidationError(
                "This document is already mapped to the industry"
            )

        return attrs
