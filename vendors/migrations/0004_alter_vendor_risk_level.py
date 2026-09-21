from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vendors", "0003_document_upload_attempts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vendor",
            name="risk_level",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown"),
                    ("low", "Low"),
                    ("medium", "Medium"),
                    ("high", "High"),
                    ("critical", "Critical"),
                ],
                db_index=True,
                default="unknown",
                max_length=20,
            ),
        ),
    ]