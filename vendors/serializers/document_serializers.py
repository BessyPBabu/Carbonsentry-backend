from rest_framework import serializers
from vendors.models import Document


class DocumentListSerializer(serializers.ModelSerializer):
    document_type = serializers.StringRelatedField()
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_id = serializers.UUIDField(source='vendor.id', read_only=True)
    vendor = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "vendor_id",
            "vendor_name",
            "vendor", 
            "document_type",
            "status",
            "uploaded_at",
            "expiry_date",
            "file",
        ]
    
    def get_vendor(self, obj):
        return {
            "id": str(obj.vendor.id),
            "name": obj.vendor.name,
        }


class DocumentDetailSerializer(serializers.ModelSerializer):
    document_type = serializers.StringRelatedField()
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_email = serializers.EmailField(source='vendor.contact_email', read_only=True)
    vendor_industry = serializers.CharField(source='vendor.industry.name', read_only=True)
    vendor_id = serializers.UUIDField(source='vendor.id', read_only=True)
    
    class Meta:
        model = Document
        fields = [
            "id",
            "vendor_id",
            "vendor_name",
            "vendor_email",
            "vendor_industry",
            "document_type",
            "status",
            "file",
            "uploaded_at",
            "expiry_date",
            "upload_token_expires_at",
        ]