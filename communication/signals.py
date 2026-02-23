import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender='communication.Message')
def on_message_save(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from audit_logs.services import log_action
        log_action(
            action='message_sent',
            entity_type='Message',
            entity_id=str(instance.id),
            organization=instance.vendor.organization,
            details={
                'vendor': instance.vendor.name,
                'sender_type': instance.sender_type,
                'message_type': instance.message_type,
            },
        )
    except Exception:
        logger.exception("on_message_save: audit log failed for message %s", instance.id)


@receiver(post_save, sender='communication.ChatToken')
def on_chat_token_save(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from audit_logs.services import log_action
        log_action(
            action='chat_invite_sent',
            entity_type='ChatToken',
            entity_id=str(instance.id),
            organization=instance.vendor.organization,
            details={
                'vendor': instance.vendor.name,
                'sent_to': instance.sent_to_email,
                'expires_at': instance.expires_at.isoformat(),
            },
        )
    except Exception:
        logger.exception("on_chat_token_save: audit log failed for token %s", instance.id)