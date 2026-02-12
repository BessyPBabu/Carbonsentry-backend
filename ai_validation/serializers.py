from rest_framework import serializers
from .models import (
    DocumentValidation, ExtractedMetadata, VendorRiskProfile,
    ManualReviewQueue, AIAuditLog
)


class ExtractedMetadataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractedMetadata
        fields = [
            'id', 'co2_value', 'co2_unit', 'co2_extraction_confidence',
            'issue_date', 'issue_date_confidence',
            'expiry_date', 'expiry_date_confidence',
            'issuing_authority', 'issuing_authority_confidence',
            'certificate_number', 'verification_standard',
            'extraction_timestamp'
        ]


class DocumentValidationSerializer(serializers.ModelSerializer):
    metadata = ExtractedMetadataSerializer(read_only=True)
    document_id = serializers.UUIDField(source='document.id', read_only=True)
    document_name = serializers.CharField(source='document.document_type.name', read_only=True)
    vendor_id = serializers.UUIDField(source='document.vendor.id', read_only=True)
    vendor_name = serializers.CharField(source='document.vendor.name', read_only=True)
    
    class Meta:
        model = DocumentValidation
        fields = [
            'id', 'document', 'document_id', 'document_name', 
            'vendor_id', 'vendor_name',
            'status', 'current_step',
            'readability_passed', 'readability_score', 'readability_issues',
            'is_relevant', 'detected_document_type', 'relevance_confidence',
            'authenticity_score', 'authenticity_indicators', 'authenticity_red_flags',
            'overall_confidence', 'requires_manual_review', 'flagged_reason',
            'started_at', 'completed_at', 'total_processing_time_seconds',
            'metadata', 'created_at', 'updated_at'
        ]


class VendorRiskProfileSerializer(serializers.ModelSerializer):
    vendor_id = serializers.UUIDField(source='vendor.id', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_industry = serializers.CharField(source='vendor.industry.name', read_only=True)
    
    class Meta:
        model = VendorRiskProfile
        fields = [
            'id', 'vendor', 'vendor_id', 'vendor_name', 'vendor_industry',
            'risk_level', 'risk_score',
            'total_documents', 'validated_documents', 'flagged_documents',
            'total_co2_emissions', 'exceeds_threshold',
            'avg_document_confidence',
            'created_at', 'updated_at'
        ]


class ManualReviewQueueSerializer(serializers.ModelSerializer):
    validation = DocumentValidationSerializer(source='document_validation', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    
    class Meta:
        model = ManualReviewQueue
        fields = [
            'id', 'document_validation', 'validation',
            'priority', 'reason', 'status',
            'assigned_to', 'assigned_to_name',
            'reviewer_notes', 'resolution_decision',
            'created_at', 'resolved_at'
        ]
    
    def get_assigned_to_name(self, obj):
        if obj.assigned_to:
            return f"{obj.assigned_to.first_name} {obj.assigned_to.last_name}".strip() or obj.assigned_to.email
        return None


class AIAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAuditLog
        fields = [
            'id', 'validation_step',
            'prompt_sent', 'raw_response', 'parsed_response',
            'model_used', 'success', 'error_message',
            'created_at'
        ]