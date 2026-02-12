import uuid
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class DocumentValidation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('requires_review', 'Requires Manual Review'),
    ]
    
    STEP_CHOICES = [
        ('not_started', 'Not Started'),
        ('readability', 'Readability Check'),
        ('relevance', 'Relevance Classification'),
        ('authenticity', 'Authenticity Analysis'),
        ('extraction', 'Metadata Extraction'),
        ('risk_analysis', 'Risk Analysis'),
        ('completed', 'Completed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.OneToOneField('vendors.Document', on_delete=models.CASCADE, related_name='validation')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    current_step = models.CharField(max_length=20, choices=STEP_CHOICES, default='not_started')
    
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_processing_time_seconds = models.IntegerField(null=True, blank=True)
    
    # Step results
    readability_passed = models.BooleanField(null=True, blank=True)
    readability_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    readability_issues = models.JSONField(default=list, blank=True)
    
    is_relevant = models.BooleanField(null=True, blank=True)
    detected_document_type = models.CharField(max_length=255, blank=True)
    relevance_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    authenticity_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    authenticity_indicators = models.JSONField(default=list, blank=True)
    authenticity_red_flags = models.JSONField(default=list, blank=True)
    
    overall_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    retry_count = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    
    requires_manual_review = models.BooleanField(default=False)
    flagged_reason = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'document_validations'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['-created_at']),
        ]


class ExtractedMetadata(models.Model):
    UNIT_CHOICES = [
        ('tonnes', 'Metric Tonnes CO2e'),
        ('kg', 'Kilograms CO2e'),
        ('metric_tons', 'Metric Tons CO2e'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_validation = models.OneToOneField(DocumentValidation, on_delete=models.CASCADE, related_name='metadata')
    document = models.ForeignKey('vendors.Document', on_delete=models.CASCADE, related_name='extracted_metadata')
    
    co2_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    co2_unit = models.CharField(max_length=20, choices=UNIT_CHOICES, blank=True)
    co2_extraction_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    issue_date = models.DateField(null=True, blank=True)
    issue_date_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    expiry_date = models.DateField(null=True, blank=True)
    expiry_date_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    issuing_authority = models.CharField(max_length=500, blank=True)
    issuing_authority_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    certificate_number = models.CharField(max_length=255, blank=True)
    verification_standard = models.CharField(max_length=100, blank=True)
    
    raw_extracted_data = models.JSONField(default=dict, blank=True)
    extraction_timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'extracted_metadata'


class AIAuditLog(models.Model):
    STEP_CHOICES = [
        ('readability', 'Readability Check'),
        ('relevance', 'Relevance Classification'),
        ('authenticity', 'Authenticity Analysis'),
        ('extraction', 'Metadata Extraction'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_validation = models.ForeignKey(DocumentValidation, on_delete=models.CASCADE, related_name='audit_logs')
    
    validation_step = models.CharField(max_length=20, choices=STEP_CHOICES)
    prompt_sent = models.TextField()
    raw_response = models.TextField()
    parsed_response = models.JSONField(default=dict, blank=True)
    
    model_used = models.CharField(max_length=100, default='gemini-1.5-flash')
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'ai_audit_logs'
        ordering = ['-created_at']


class IndustryEmissionThreshold(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    industry = models.OneToOneField('vendors.Industry', on_delete=models.CASCADE, related_name='emission_threshold')
    
    low_threshold = models.DecimalField(max_digits=15, decimal_places=2)
    medium_threshold = models.DecimalField(max_digits=15, decimal_places=2)
    high_threshold = models.DecimalField(max_digits=15, decimal_places=2)
    critical_threshold = models.DecimalField(max_digits=15, decimal_places=2)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'industry_emission_thresholds'


class VendorRiskProfile(models.Model):
    RISK_LEVEL_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
        ('unknown', 'Unknown'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.OneToOneField('vendors.Vendor', on_delete=models.CASCADE, related_name='risk_profile')
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE)
    
    risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES, default='unknown')
    risk_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    total_documents = models.IntegerField(default=0)
    validated_documents = models.IntegerField(default=0)
    flagged_documents = models.IntegerField(default=0)
    
    total_co2_emissions = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    exceeds_threshold = models.BooleanField(default=False)
    
    avg_document_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'vendor_risk_profiles'


class ManualReviewQueue(models.Model):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_validation = models.ForeignKey(DocumentValidation, on_delete=models.CASCADE, related_name='manual_reviews')
    
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    reason = models.CharField(max_length=255)
    
    assigned_to = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    reviewer_notes = models.TextField(blank=True)
    resolution_decision = models.CharField(max_length=50, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'manual_review_queue'
        ordering = ['-created_at']