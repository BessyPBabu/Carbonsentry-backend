from rest_framework import serializers
from vendors.models import Industry


class IndustrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Industry
        fields = ["id", "name", "description"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Industry name cannot be empty")
        return value
