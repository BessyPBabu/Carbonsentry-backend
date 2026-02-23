import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_chat_invitation(chat_token):
    # build the public vendor chat URL using the token UUID
    frontend_base = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
    chat_url = f"{frontend_base}/vendor-chat/{chat_token.token}"

    subject = f"Secure Chat Invitation — {chat_token.vendor.name}"

    body = f"""
Hello,

You have been invited to a secure chat session regarding compliance documentation for {chat_token.vendor.name}.

Click the link below to join the chat:

{chat_url}

This link is valid for 72 hours and can only be used once at a time.
After that, please contact your compliance officer for a new link.

This is a secure, encrypted communication channel. Do not share this link with anyone.

Regards,
CarbonSentry Compliance Team
    """.strip()

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[chat_token.sent_to_email],
            fail_silently=False,
        )
        logger.info(
            "Chat invitation email sent | vendor=%s to=%s token=%s",
            chat_token.vendor.id, chat_token.sent_to_email, chat_token.token
        )
        return True
    except Exception as exc:
        logger.exception(
            "Failed to send chat invitation | vendor=%s to=%s error=%s",
            chat_token.vendor.id, chat_token.sent_to_email, str(exc)
        )
        return False