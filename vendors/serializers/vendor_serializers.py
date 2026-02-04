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
