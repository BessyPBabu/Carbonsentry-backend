

import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        blank=True, null=True, upload_to="vendor_documents/%Y/%m/"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("uploaded", "Uploaded"),
                            ("valid", "Valid"),
                            ("expired", "Expired"),
                            ("invalid", "Invalid"),
                            ("flagged", "Flagged"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "upload_token",
                    models.CharField(
                        blank=True, db_index=True, max_length=255, null=True
                    ),
                ),
                (
                    "upload_token_expires_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("expiry_date", models.DateField(blank=True, null=True)),
                ("uploaded_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "documents",
            },
        ),
        migrations.CreateModel(
            name="DocumentType",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(db_index=True, max_length=255)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "db_table": "document_types",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Industry",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(db_index=True, max_length=255, unique=True)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "db_table": "industries",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="AIReview",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "confidence_score",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                ("issue_type", models.CharField(blank=True, max_length=100)),
                ("ai_summary", models.TextField(blank=True)),
                ("flagged", models.BooleanField(default=False)),
                ("reviewed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_reviews",
                        to="vendors.document",
                    ),
                ),
            ],
            options={
                "db_table": "ai_reviews",
            },
        ),
        migrations.AddField(
            model_name="document",
            name="document_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="vendors.documenttype"
            ),
        ),
        migrations.CreateModel(
            name="EmailCampaign",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("subject", models.CharField(max_length=255)),
                ("body_template", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_campaigns",
                        to="accounts.organization",
                    ),
                ),
                (
                    "industry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="vendors.industry",
                    ),
                ),
            ],
            options={
                "db_table": "email_campaigns",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="HumanReview",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("needs_changes", "Needs Changes"),
                        ],
                        max_length=20,
                    ),
                ),
                ("comment", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "ai_review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="human_reviews",
                        to="vendors.aireview",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "human_reviews",
            },
        ),
        migrations.CreateModel(
            name="Vendor",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("country", models.CharField(max_length=100)),
                ("contact_email", models.EmailField(max_length=254)),
                (
                    "compliance_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("compliant", "Compliant"),
                            ("non_compliant", "Non Compliant"),
                            ("expired", "Expired"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "risk_level",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        db_index=True,
                        default="medium",
                        max_length=20,
                    ),
                ),
                ("last_updated", models.DateTimeField(auto_now=True)),
                (
                    "industry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="vendors.industry",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vendors",
                        to="accounts.organization",
                    ),
                ),
            ],
            options={
                "db_table": "vendor",
            },
        ),
        migrations.CreateModel(
            name="EmailDispatch",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("recipient_email", models.EmailField(max_length=254)),
                (
                    "status",
                    models.CharField(
                        choices=[("sent", "Sent"), ("failed", "Failed")],
                        db_index=True,
                        default="sent",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True)),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dispatches",
                        to="vendors.emailcampaign",
                    ),
                ),
                (
                    "vendor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="vendors.vendor"
                    ),
                ),
            ],
            options={
                "db_table": "email_dispatches",
            },
        ),
        migrations.AddField(
            model_name="document",
            name="vendor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="vendors.vendor",
            ),
        ),
        migrations.CreateModel(
            name="VendorBulkUpload",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("total_rows", models.PositiveIntegerField()),
                ("success_count", models.PositiveIntegerField()),
                ("failure_count", models.PositiveIntegerField()),
                ("error_summary", models.JSONField(blank=True, default=list)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "vendor_bulk_uploads",
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.CreateModel(
            name="VendorRiskScore",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "overall_score",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "ai_confidence_avg",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                ("risk_reason", models.TextField(blank=True)),
                ("calculated_at", models.DateTimeField(auto_now_add=True)),
                (
                    "vendor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="risk_scores",
                        to="vendors.vendor",
                    ),
                ),
            ],
            options={
                "db_table": "vendor_risk_scores",
                "ordering": ["-calculated_at"],
            },
        ),
        migrations.CreateModel(
            name="IndustryRequiredDocument",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("mandatory", models.BooleanField(default=True)),
                (
                    "document_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="vendors.documenttype",
                    ),
                ),
                (
                    "industry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="required_documents",
                        to="vendors.industry",
                    ),
                ),
            ],
            options={
                "db_table": "industry_required_documents",
                "unique_together": {("industry", "document_type")},
            },
        ),
        migrations.AddIndex(
            model_name="vendor",
            index=models.Index(
                fields=["organization", "compliance_status"],
                name="vendor_organiz_22f82f_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="vendor",
            index=models.Index(
                fields=["organization", "risk_level"], name="vendor_organiz_bacd86_idx"
            ),
        ),
        migrations.AlterUniqueTogether(
            name="vendor",
            unique_together={("organization", "contact_email")},
        ),
        migrations.AlterUniqueTogether(
            name="emaildispatch",
            unique_together={("campaign", "vendor")},
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["vendor", "status"], name="documents_vendor__977b33_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["expiry_date"], name="documents_expiry__8f271f_idx"
            ),
        ),
        migrations.AlterUniqueTogether(
            name="document",
            unique_together={("vendor", "document_type")},
        ),
    ]
