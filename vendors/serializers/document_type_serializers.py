from rest_framework import serializers
from vendors.models import DocumentType


class DocumentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentType
        fields = ["id", "name", "description"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Document name cannot be empty")
        return value
