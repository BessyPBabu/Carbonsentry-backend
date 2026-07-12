from rest_framework import serializers
from vendors.models import Vendor
from vendors.utils.validators import (
    validate_vendor_name,
    validate_vendor_email,
    validate_vendor_country,
)


class VendorListSerializer(serializers.ModelSerializer):
    industry = serializers.CharField(source='industry.name', read_only=True)
    industry_id = serializers.UUIDField(source='industry.id', read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "industry",
            "industry_id",
            "country",
            "contact_email",
            "compliance_status",
            "risk_level",
            "last_updated",
        ]

class VendorDetailSerializer(serializers.ModelSerializer):
    industry = serializers.CharField(source='industry.name', read_only=True)
    industry_id = serializers.UUIDField(source='industry.id', read_only=True)
    
    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "industry",
            "industry_id",
            "country",
            "contact_email",
            "compliance_status",
            "risk_level",
            "last_updated",
        ]


class VendorCreateSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Vendor
        fields = [
            "name",
            "industry",
            "country",
            "contact_email",
        ]
    
    def validate_name(self, value):
        return validate_vendor_name(value)
    
    def validate_contact_email(self, value):
        return validate_vendor_email(value)
    
    def validate_country(self, value):
        return validate_vendor_country(value)