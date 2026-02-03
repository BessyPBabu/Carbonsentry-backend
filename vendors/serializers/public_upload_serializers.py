from django.utils import timezone
from rest_framework import serializers


class VendorPublicUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate(self, attrs):
        document = self.context["document"]

        if document.status != "pending":
            raise serializers.ValidationError("Upload not allowed.")

        if not document.upload_token:
            raise serializers.ValidationError("Invalid link.")

        return attrs

    def save(self):
        document = self.context["document"]
        document.file = self.validated_data["file"]
        document.status = "uploaded"
        document.uploaded_at = timezone.now()
        document.upload_token = None
        document.upload_token_expires_at = None
        document.save()

        return document
