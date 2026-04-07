import uuid
import logging

from django.db import models
from django.conf import settings

logger = logging.getLogger(__name__)


class Report(models.Model):
    REPORT_TYPE_CHOICES = [
        ('vendor_risk', 'Vendor Risk Report'),
        ('compliance_summary', 'Compliance Summary'),
        ('emissions_overview', 'Emissions Overview'),
        ('document_audit', 'Document Audit Report'),
        ('vendor_compliance_report', 'Vendor Compliance Report'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('approved', 'Approved'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        'accounts.Organization',
        on_delete=models.CASCADE,
        related_name='reports'
    )

    report_type = models.CharField(max_length=30, choices=REPORT_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    vendor = models.ForeignKey(
        'vendors.Vendor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reports'
    )
    data = models.JSONField(default=dict)


    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='generated_reports'
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_reports'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['organization', 'report_type']),
            models.Index(fields=['organization', 'status']),
        ]

    def __str__(self):
        return f"{self.title} [{self.status}]"