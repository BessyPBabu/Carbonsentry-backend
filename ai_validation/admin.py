from django.contrib import admin
from .models import (
    DocumentValidation, ExtractedMetadata, AIAuditLog,
    IndustryEmissionThreshold, VendorRiskProfile, ManualReviewQueue
)


@admin.register(DocumentValidation)
class DocumentValidationAdmin(admin.ModelAdmin):
    list_display = ['document', 'status', 'overall_confidence', 'requires_manual_review', 'created_at']
    list_filter = ['status', 'requires_manual_review', 'current_step']
    search_fields = ['document__vendor__name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ExtractedMetadata)
class ExtractedMetadataAdmin(admin.ModelAdmin):
    list_display = ['document', 'co2_value', 'co2_unit', 'expiry_date']
    search_fields = ['document__vendor__name', 'issuing_authority']


@admin.register(IndustryEmissionThreshold)
class IndustryEmissionThresholdAdmin(admin.ModelAdmin):
    list_display = ['industry', 'low_threshold', 'medium_threshold', 'high_threshold', 'critical_threshold']
    list_filter = ['industry']


@admin.register(VendorRiskProfile)
class VendorRiskProfileAdmin(admin.ModelAdmin):
    list_display = ['vendor', 'risk_level', 'risk_score', 'total_documents', 'updated_at']
    list_filter = ['risk_level']
    search_fields = ['vendor__name']


@admin.register(ManualReviewQueue)
class ManualReviewQueueAdmin(admin.ModelAdmin):
    list_display = ['document_validation', 'priority', 'status', 'assigned_to', 'created_at']
    list_filter = ['status', 'priority']


@admin.register(AIAuditLog)
class AIAuditLogAdmin(admin.ModelAdmin):
    list_display = ['document_validation', 'validation_step', 'success', 'created_at']
    list_filter = ['validation_step', 'success']