import uuid
import secrets
import logging
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

CHAT_TOKEN_EXPIRY_HOURS = 72


def _make_otp():
    return str(secrets.randbelow(1_000_000)).zfill(6)


class ChatToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)

    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE, related_name='chat_tokens'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='created_chat_tokens'
    )

    sent_to_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_revoked = models.BooleanField(default=False)

    # vendor must enter this before WebSocket access is granted
    otp_code = models.CharField(max_length=6, blank=True)
    otp_verified = models.BooleanField(default=False)
    otp_attempts = models.IntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=CHAT_TOKEN_EXPIRY_HOURS)
        if self._state.adding and not self.otp_code:
            self.otp_code = _make_otp()
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return not self.is_revoked and timezone.now() < self.expires_at

    def __str__(self):
        status = 'valid' if self.is_valid else 'expired'
        return f"ChatToken({self.vendor.name}, {status})"


class Message(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('vendor_message', 'Vendor Message'),
        ('internal_note',  'Internal Note'),
    ]
    SENDER_TYPE_CHOICES = [
        ('officer', 'Officer'),
        ('vendor',  'Vendor'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE, related_name='messages'
    )
    message_type = models.CharField(
        max_length=20, choices=MESSAGE_TYPE_CHOICES, default='vendor_message'
    )
    sender_type = models.CharField(max_length=10, choices=SENDER_TYPE_CHOICES)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sent_messages'
    )
    vendor_sender_name = models.CharField(max_length=100, blank=True, default='')
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.sender_type}] {self.vendor.name} — {self.created_at:%Y-%m-%d %H:%M}"