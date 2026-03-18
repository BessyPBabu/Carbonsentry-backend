import logging
from rest_framework import serializers
from .models import Report

logger = logging.getLogger(__name__)


class ReportSerializer(serializers.ModelSerializer):
    generated_by_name = serializers.SerializerMethodField()
    approved_by_name  = serializers.SerializerMethodField()
    vendor_name       = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = [
            "id", "report_type", "title", "status",
            "vendor", "vendor_name",
            "data",
            "date_from", "date_to",
            "generated_by", "generated_by_name", "generated_at",
            "approved_by", "approved_by_name", "approved_at", "approval_notes",
        ]
        read_only_fields = [
            "id", "status", "data",
            "generated_by", "generated_at",
            "approved_by", "approved_at",
        ]

    def get_generated_by_name(self, obj):
        if obj.generated_by:
            return obj.generated_by.full_name or obj.generated_by.email
        return None

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.full_name or obj.approved_by.email
        return None

    def get_vendor_name(self, obj):
        return obj.vendor.name if obj.vendor else None


class GenerateReportSerializer(serializers.Serializer):
    report_type = serializers.ChoiceField(choices=[
        "vendor_risk", "compliance_summary",
        "emissions_overview", "document_audit",
    ])
    title     = serializers.CharField(max_length=255)
    vendor_id = serializers.UUIDField(required=False, allow_null=True)
    date_from = serializers.DateField(required=False, allow_null=True)
    date_to   = serializers.DateField(required=False, allow_null=True)

    def validate(self, data):
        if data.get("report_type") == "vendor_risk" and not data.get("vendor_id"):
            raise serializers.ValidationError(
                {"vendor_id": "vendor_id is required for vendor_risk reports."}
            )
        return data


class ApproveReportSerializer(serializers.Serializer):
    approval_notes = serializers.CharField(
        required=False, allow_blank=True, default=""
    )