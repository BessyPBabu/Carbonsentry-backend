from rest_framework import serializers
from vendors.models import Document
from django.urls import reverse
from ai_validation.models import DocumentValidation


class DocumentListSerializer(serializers.ModelSerializer):
    document_type = serializers.CharField(source='document_type.name', read_only=True)
    document_type_id = serializers.UUIDField(source='document_type.id', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_id = serializers.UUIDField(source='vendor.id', read_only=True)
    vendor_industry = serializers.CharField(source='vendor.industry.name', read_only=True)
    validation = serializers.SerializerMethodField()

    file_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 
            'vendor_id',
            'vendor_name',
            'vendor_industry',
            'document_type',
            'document_type_id',
            'file',
            'file_url',       
            'download_url',
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
        
    def get_file_url(self, obj):
        """Get secure file URL for viewing"""
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(
                    reverse('document-file', kwargs={'document_id': obj.id})
                )
        return None
    
    def get_download_url(self, obj):
        """Get secure download URL"""
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(
                    reverse('document-download', kwargs={'document_id': obj.id})
                )
        return None


class DocumentDetailSerializer(serializers.ModelSerializer):
    document_type = serializers.CharField(source='document_type.name', read_only=True)
    document_type_id = serializers.UUIDField(source='document_type.id', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_email = serializers.EmailField(source='vendor.contact_email', read_only=True)
    vendor_industry = serializers.CharField(source='vendor.industry.name', read_only=True)
    vendor_id = serializers.UUIDField(source='vendor.id', read_only=True)

    file_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    
    class Meta:
        model = Document
        fields = [
            "id",
            "vendor_id",
            "vendor_name",
            "vendor_email",
            "vendor_industry",
            "document_type",
            "document_type_id",
            "status",
            "file",
            "file_url",      
            "download_url",
            "uploaded_at",
            "expiry_date",
        ]

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(
                    reverse('document-file', kwargs={'document_id': obj.id})
                )
        return None
    
    def get_download_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(
                    reverse('document-download', kwargs={'document_id': obj.id})
                )
        return None