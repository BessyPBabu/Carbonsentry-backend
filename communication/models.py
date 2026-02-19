import uuid
from django.db import models
from django.conf import settings


class VendorMessage(models.Model):
    DIRECTION_CHOICES = [
        ('internal_note', 'Internal Note'),
        ('vendor_facing', 'Vendor Facing'),
        ('vendor_reply',  'Vendor Reply'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        'vendors.Vendor',
        on_delete=models.CASCADE,
        related_name='messages',
    )
    organization = models.ForeignKey(
        'accounts.Organization',
        on_delete=models.CASCADE,
        related_name='vendor_messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sent_messages',
    )
    sender_display_name = models.CharField(max_length=255, blank=True)

    message = models.TextField()
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default='internal_note')

    email_sent = models.BooleanField(default=False)
    email_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['vendor', 'organization']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.direction} — {self.vendor.name} ({self.created_at:%Y-%m-%d})"