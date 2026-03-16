import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_chat_invitation(chat_token):
    frontend_base = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
    chat_url = f"{frontend_base}/vendor-chat/{chat_token.token}"

    subject = f"Secure Chat Invitation — {chat_token.vendor.name}"

    # OTP is prominently displayed so vendors can find it easily
    body = f"""Hello,

You have been invited to a secure chat session regarding compliance documentation for {chat_token.vendor.name}.

STEP 1 — Click your chat link:
{chat_url}

STEP 2 — Enter this verification code when prompted:

    ┌──────────────────┐
    │   Code: {chat_token.otp_code}     │
    └──────────────────┘

This code is valid for the lifetime of the chat link (72 hours).

Do not share this link or code with anyone. This is a private, encrypted channel.

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
            "Chat invitation sent | vendor=%s to=%s token=%s",
            chat_token.vendor.id, chat_token.sent_to_email, chat_token.token,
        )
        return True
    except Exception as exc:
        logger.exception(
            "Chat invitation failed | vendor=%s to=%s: %s",
            chat_token.vendor.id, chat_token.sent_to_email, exc,
        )
        return False