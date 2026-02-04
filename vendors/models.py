import uuid
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class Industry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
    )
    description = models.TextField(blank=True)

    class Meta:
        db_table = "industries"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DocumentType(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        db_index=True,
    )
    description = models.TextField(blank=True)

    class Meta:
        db_table = "document_types"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class IndustryRequiredDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    industry = models.ForeignKey(
        Industry,
        on_delete=models.CASCADE,
        related_name="required_documents",
    )
    document_type = models.ForeignKey(
        DocumentType,
        on_delete=models.CASCADE,
    )
    mandatory = models.BooleanField(default=True)

    class Meta:
        db_table = "industry_required_documents"
        unique_together = ("industry", "document_type")


class Vendor(models.Model):
    COMPLIANCE_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("compliant", "Compliant"),
        ("non_compliant", "Non Compliant"),
        ("expired", "Expired"),
    ]
    RISK_LEVEL_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="vendors",
    )
    name = models.CharField(max_length=255)
    industry = models.ForeignKey(Industry, on_delete=models.PROTECT)
    country = models.CharField(max_length=100)
    contact_email = models.EmailField()
    compliance_status = models.CharField(
        max_length=20,
        choices=COMPLIANCE_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    risk_level = models.CharField(
        max_length=20,
        choices=RISK_LEVEL_CHOICES,
        default="medium",
        db_index=True,
    )
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vendors"
        unique_together = ("organization", "contact_email")
        indexes = [
            models.Index(fields=["organization", "compliance_status"]),
            models.Index(fields=["organization", "risk_level"]),
        ]

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()

        if self.contact_email:
            self.contact_email = self.contact_email.lower().strip()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class VendorBulkUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
    )
    uploaded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    total_rows = models.PositiveIntegerField()
    success_count = models.PositiveIntegerField()
    failure_count = models.PositiveIntegerField()
    error_summary = models.JSONField(default=list, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vendor_bulk_uploads"
        ordering = ["-uploaded_at"]


class Document(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("uploaded", "Uploaded"),
        ("valid", "Valid"),
        ("expired", "Expired"),
        ("invalid", "Invalid"),
        ("flagged", "Flagged"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.ForeignKey(
        DocumentType,
        on_delete=models.PROTECT,
    )
    file = models.FileField(
        upload_to="vendor_documents/%Y/%m/",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    upload_token = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
    )
    upload_token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    expiry_date = models.DateField(null=True, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents"
        unique_together = ("vendor", "document_type")
        indexes = [
            models.Index(fields=["vendor", "status"]),
            models.Index(fields=["expiry_date"]),
        ]


class AIReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="ai_reviews",
    )
    confidence_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    issue_type = models.CharField(max_length=100, blank=True)
    ai_summary = models.TextField(blank=True)
    flagged = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_reviews"


class HumanReview(models.Model):
    DECISION_CHOICES = [
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("needs_changes", "Needs Changes"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ai_review = models.ForeignKey(
        AIReview,
        on_delete=models.CASCADE,
        related_name="human_reviews",
    )
    reviewer = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    comment = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "human_reviews"


class VendorRiskScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="risk_scores",
    )
    overall_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    ai_confidence_avg = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    risk_reason = models.TextField(blank=True)
    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vendor_risk_scores"
        ordering = ["-calculated_at"]


class EmailCampaign(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="email_campaigns",
    )

    industry = models.ForeignKey(
        "vendors.Industry",
        on_delete=models.PROTECT,
    )

    subject = models.CharField(max_length=255)
    body_template = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "email_campaigns"
        ordering = ["-created_at"]


class EmailDispatch(models.Model):
    STATUS_CHOICES = [
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    campaign = models.ForeignKey(
        "vendors.EmailCampaign",
        on_delete=models.CASCADE,
        related_name="dispatches",
    )

    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.CASCADE,
    )

    recipient_email = models.EmailField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="sent",
        db_index=True,
    )

    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "email_dispatches"
        unique_together = ("campaign", "vendor")
