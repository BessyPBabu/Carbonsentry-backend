import uuid
import logging
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# how long a vendor chat token stays valid after generation
CHAT_TOKEN_EXPIRY_HOURS = 72


class ChatToken(models.Model):
    # one token per chat invitation — officer generates this, emails it to vendor
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)

    vendor = models.ForeignKey(
        'vendors.Vendor',
        on_delete=models.CASCADE,
        related_name='chat_tokens'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_chat_tokens'
    )

    # vendor's contact email this was sent to
    sent_to_email = models.EmailField()

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    # once revoked, the token is dead even if not expired
    is_revoked = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=CHAT_TOKEN_EXPIRY_HOURS)
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return not self.is_revoked and timezone.now() < self.expires_at

    def __str__(self):
        return f"ChatToken for {self.vendor.name} — {'valid' if self.is_valid else 'expired'}"


class Message(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('vendor_message', 'Vendor Message'),   # shows in vendor chat, email sent
        ('internal_note', 'Internal Note'),     # officer-only, never emailed
    ]

    SENDER_TYPE_CHOICES = [
        ('officer', 'Officer'),
        ('vendor', 'Vendor'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    vendor = models.ForeignKey(
        'vendors.Vendor',
        on_delete=models.CASCADE,
        related_name='messages'
    )

    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default='vendor_message'
    )

    # who sent this — either an officer (user FK) or a vendor (no user account)
    sender_type = models.CharField(max_length=10, choices=SENDER_TYPE_CHOICES)

    # set when sender_type = 'officer'
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_messages'
    )

    # set when sender_type = 'vendor' — just a display name, no user account
    vendor_sender_name = models.CharField(max_length=100, blank=True, default='')

    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.sender_type}] {self.vendor.name} — {self.created_at:%Y-%m-%d %H:%M}"