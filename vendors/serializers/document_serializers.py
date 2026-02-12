from rest_framework import serializers
from vendors.models import Document
from ai_validation.models import DocumentValidation


class DocumentListSerializer(serializers.ModelSerializer):
    document_type = serializers.StringRelatedField()
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_id = serializers.UUIDField(source='vendor.id', read_only=True)
    vendor = serializers.SerializerMethodField()
    validation = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 
            'vendor_id',
            'vendor_name',
            'document_type',
            'document_type_name',
            'file',
            'status',
            'expiry_date',
            'uploaded_at',
            'validation'
        ]

    def get_validation(self, obj):
        """Get validation info if exists"""
        try:
            validation = obj.validation
            return {
                'id': str(validation.id),
                'status': validation.status,
                'current_step': validation.current_step,
                'overall_confidence': float(validation.overall_confidence) if validation.overall_confidence else None,
                'requires_manual_review': validation.requires_manual_review
            }
        except DocumentValidation.DoesNotExist:
            return None

    

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