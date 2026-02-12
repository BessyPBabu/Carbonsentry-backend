from django.utils import timezone
from rest_framework import serializers


class VendorPublicUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    document_id = serializers.UUIDField(required=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        document = self.context["document"]
        
        if document.status != "pending":
            raise serializers.ValidationError("Upload not allowed for this document.")
        
        # Validate expiry date is in future
        expiry_date = attrs.get('expiry_date')
        if expiry_date and expiry_date < timezone.now().date():
            raise serializers.ValidationError("Expiry date must be in the future.")
        
        return attrs

    def save(self):
        document = self.context["document"]
        document.file = self.validated_data["file"]
        document.status = "uploaded"
        document.uploaded_at = timezone.now()
        
        # Save expiry date if provided
        if 'expiry_date' in self.validated_data:
            document.expiry_date = self.validated_data['expiry_date']
        
        document.save()
        return document