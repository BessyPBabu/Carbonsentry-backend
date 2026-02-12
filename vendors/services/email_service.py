import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger("vendors.email")


class EmailService:
    @classmethod
    def send(cls, subject, body, recipient):
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
            logger.info(
                "Email sent successfully",
                extra={
                    "recipient": recipient,
                    "subject": subject[:50]
                }
            )
            return True
        except Exception as e:
            logger.error(
                "Email send failed",
                extra={
                    "recipient": recipient,
                    "subject": subject[:50],
                    "error": str(e)
                },
                exc_info=True
            )
            raise