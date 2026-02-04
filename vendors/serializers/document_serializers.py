from rest_framework import serializers
from vendors.models import Document


class DocumentListSerializer(serializers.ModelSerializer):
    document_type = serializers.StringRelatedField()

    class Meta:
        model = Document
        fields = [
            "id",
            "document_type",
            "status",
            "uploaded_at",
            "expiry_date",
        ]
