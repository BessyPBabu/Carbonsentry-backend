from rest_framework import serializers


class VendorBulkUploadSerializer(serializers.Serializer):
    csv_file = serializers.FileField(required=True)
    send_emails = serializers.BooleanField(required=False, default=False)

    def validate_csv_file(self, file):
        name = (file.name or "").lower()

        if not name.endswith(".csv"):
            raise serializers.ValidationError("Only CSV files are allowed")

        if file.size == 0:
            raise serializers.ValidationError("CSV file is empty")

        if file.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("CSV file must be smaller than 10MB")

        return file
