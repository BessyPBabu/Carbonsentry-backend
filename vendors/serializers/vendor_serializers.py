from rest_framework import serializers
from vendors.models import Vendor


class VendorListSerializer(serializers.ModelSerializer):
    industry = serializers.StringRelatedField()

    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "industry",
            "country",
            "compliance_status",
            "risk_level",
        ]

class VendorDetailSerializer(serializers.ModelSerializer):
    industry = serializers.StringRelatedField()
    
    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "industry",
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
        if not value or not value.strip():
            raise serializers.ValidationError("Vendor name cannot be empty")
        return value.strip()
    
    def validate_contact_email(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Contact email cannot be empty")
        return value.strip().lower()
    
    def validate_country(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Country cannot be empty")
        return value.strip()
