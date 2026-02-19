import uuid
from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('vendor_created',      'Vendor Created'),
        ('vendor_updated',      'Vendor Updated'),
        ('vendor_deleted',      'Vendor Deleted'),
        ('document_uploaded',   'Document Uploaded'),
        ('document_deleted',    'Document Deleted'),
        ('validation_triggered','Validation Triggered'),
        ('validation_completed','Validation Completed'),
        ('review_resolved',     'Review Resolved'),
        ('message_sent',        'Message Sent'),
        ('user_login',          'User Login'),
        ('user_logout',         'User Logout'),
        ('report_generated',    'Report Generated'),
        ('report_approved',     'Report Approved'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'accounts.Organization',
        on_delete=models.CASCADE,
        related_name='audit_logs',
        null=True, blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=50, blank=True)
    entity_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', '-created_at']),
            models.Index(fields=['action']),
            models.Index(fields=['entity_type', 'entity_id']),
        ]

    def __str__(self):
        actor = self.actor.email if self.actor else 'system'
        return f"{actor} — {self.action} — {self.entity_type}:{self.entity_id}"