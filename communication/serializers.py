import logging

from rest_framework import serializers

from .models import ChatToken, Message

logger = logging.getLogger(__name__)


MAX_MESSAGE_LENGTH = 5000

class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'vendor', 'message_type', 'sender_type',
            'sender', 'sender_name', 'vendor_sender_name',
            'content', 'is_read', 'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'sender', 'sender_type', 'vendor_sender_name']

    def validate_content(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Message content cannot be empty")
        if len(value) > MAX_MESSAGE_LENGTH:
            raise serializers.ValidationError(
                f"Message cannot exceed {MAX_MESSAGE_LENGTH} characters"
            )
        return value

    def get_sender_name(self, obj):
        if obj.sender_type == 'officer' and obj.sender:
            return obj.sender.full_name or obj.sender.email
        return obj.vendor_sender_name or obj.vendor.name


class ChatTokenSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    chat_url = serializers.SerializerMethodField()
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = ChatToken
        fields = [
            'id', 'token', 'vendor', 'vendor_name',
            'sent_to_email', 'created_by', 'created_by_name',
            'created_at', 'expires_at', 'is_revoked', 'is_valid',
            'chat_url',
        ]
        read_only_fields = [
            'id', 'token', 'created_by', 'created_at', 'expires_at', 'is_valid'
        ]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.full_name or obj.created_by.email
        return None

    def get_chat_url(self, obj):
        from django.conf import settings
        frontend_base = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
        return f"{frontend_base}/vendor-chat/{obj.token}"


class SendChatInviteSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    email = serializers.EmailField(required=False, allow_blank=True)


class ChatVendorListSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    vendor_name = serializers.CharField()
    last_message = serializers.CharField(allow_null=True)
    last_message_at = serializers.DateTimeField(allow_null=True)
    unread_count = serializers.IntegerField()
    has_active_token = serializers.BooleanField()


class VerifyOtpSerializer(serializers.Serializer):
    token = serializers.UUIDField()
    otp_code = serializers.CharField(min_length=6, max_length=6)