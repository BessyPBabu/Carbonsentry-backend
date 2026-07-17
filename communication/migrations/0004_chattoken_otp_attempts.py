from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communication", "0003_chattoken_otp_code_chattoken_otp_verified"),
    ]

    operations = [
        migrations.AddField(
            model_name="chattoken",
            name="otp_attempts",
            field=models.IntegerField(default=0),
        ),
    ]