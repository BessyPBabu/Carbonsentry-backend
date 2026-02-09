import secrets
import hashlib
from datetime import timedelta
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone


def generate_verification_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def send_organization_verification_email(organization, token):
    verification_link = f"{settings.FRONTEND_URL}/verify-email/{token}"
    login_link = f"{settings.FRONTEND_URL}/login"
    
    subject = "Verify your CarbonSentry organization"
    
    message = f"""
Hello,

Thank you for registering {organization.name} with CarbonSentry.

Please verify your email address by clicking the link below:
{verification_link}

This link will expire in 24 hours.

After verification, you can log in at:
{login_link}

Your registered email: {organization.primary_email}

Best regards,
CarbonSentry Team
    """.strip()
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[organization.primary_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Failed to send verification email: {e}")
        return False


def send_user_welcome_email(user, reset_token, uid):
    reset_link = f"{settings.FRONTEND_URL}/reset-password/{uid}/{reset_token}"
    
    subject = "Welcome to CarbonSentry - Set Your Password"
    
    message = f"""
Hello {user.full_name},

Your CarbonSentry account has been created by your organization administrator.

Account Details:
Email: {user.email}
Role: {user.get_role_display()}

To activate your account, please set your password by clicking the link below:
{reset_link}

This link will expire in 24 hours.

Once you set your password, you'll be able to log in and access your dashboard.

Best regards,
CarbonSentry Team
    """.strip()
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Failed to send welcome email: {e}")
        return False


def send_password_reset_email(user, temp_password):
    login_link = f"{settings.FRONTEND_URL}/login"
    
    subject = "CarbonSentry - Password Reset"
    
    message = f"""
Hello {user.full_name},

Your password has been reset by your organization administrator.

New Temporary Password: {temp_password}

Please log in and change this password immediately:
{login_link}

IMPORTANT: You must change this temporary password to regain full access to your account.

Best regards,
CarbonSentry Team
    """.strip()
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Failed to send password reset email: {e}")
        return False


def is_verification_token_valid(organization, token, hours=24):
    if not organization.email_verification_token:
        return False
    
    if organization.email_verification_token != hash_token(token):
        return False
    
    if organization.created_at < timezone.now() - timedelta(hours=hours):
        return False
    
    return True


