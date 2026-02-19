from rest_framework import serializers
from .models import VendorMessage


class VendorMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_email = serializers.SerializerMethodField()
    is_outbound = serializers.SerializerMethodField()

    class Meta:
        model = VendorMessage
        fields = [
            'id', 'vendor', 'direction', 'message',
            'sender', 'sender_name', 'sender_email',
            'sender_display_name', 'is_outbound',
            'email_sent', 'email_error', 'created_at',
        ]
        read_only_fields = [
            'id', 'vendor', 'organization', 'sender',
            'email_sent', 'email_error', 'created_at',
        ]

    def get_sender_name(self, obj):
        if obj.direction == 'vendor_reply':
            return obj.sender_display_name or obj.vendor.name
        if obj.sender:
            return obj.sender.get_full_name() or obj.sender.email
        return 'Unknown'

    def get_sender_email(self, obj):
        if obj.sender:
            return obj.sender.email
        return ''

    def get_is_outbound(self, obj):
        return obj.direction in ('vendor_facing', 'internal_note')


class SendMessageSerializer(serializers.Serializer):
    message = serializers.CharField(min_length=1, max_length=5000)
    direction = serializers.ChoiceField(choices=['internal_note', 'vendor_facing'])