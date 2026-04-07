import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .services import log_action

logger = logging.getLogger(__name__)


def _safe_connect():
    try:
        from vendors.models import Vendor, Document
        from ai_validation.models import DocumentValidation, ManualReviewQueue

        @receiver(post_save, sender=Vendor)
        def on_vendor_save(sender, instance, created, **kwargs):
            # risk_calculator saves only risk_level + compliance_status — don't log those
            update_fields = set(kwargs.get('update_fields') or [])
            system_only = {'risk_level', 'compliance_status', 'last_updated'}
            if not created and update_fields and update_fields.issubset(system_only):
                return
            try:
                log_action(
                    action='vendor_created' if created else 'vendor_updated',
                    entity_type='Vendor',
                    entity_id=str(instance.id),
                    organization=instance.organization,
                    details={'name': instance.name, 'industry': str(instance.industry)},
                )
            except Exception:
                logger.exception("on_vendor_save: error for vendor=%s", instance.id)

        @receiver(post_delete, sender=Vendor)
        def on_vendor_delete(sender, instance, **kwargs):
            try:
                log_action(
                    action='vendor_deleted',
                    entity_type='Vendor',
                    entity_id=str(instance.id),
                    organization=instance.organization,
                    details={'name': instance.name},
                )
            except Exception:
                logger.exception("on_vendor_delete: error for vendor=%s", instance.id)

        @receiver(post_save, sender=Document)
        def on_document_save(sender, instance, created, **kwargs):
            if not created:
                return
            try:
                log_action(
                    action='document_uploaded',
                    entity_type='Document',
                    entity_id=str(instance.id),
                    organization=instance.vendor.organization,
                    details={
                        'vendor': instance.vendor.name,
                        'document_type': str(instance.document_type),
                        'status': instance.status,
                    },
                )
            except Exception:
                logger.exception("on_document_save: error for document=%s", instance.id)

        @receiver(post_save, sender=DocumentValidation)
        def on_validation_save(sender, instance, created, **kwargs):
            try:
                if created:
                    action = 'validation_triggered'
                elif instance.status == 'completed':
                    action = 'validation_completed'
                else:
                    return
                log_action(
                    action=action,
                    entity_type='DocumentValidation',
                    entity_id=str(instance.id),
                    organization=instance.document.vendor.organization,
                    details={
                        'document_id': str(instance.document.id),
                        'status':      instance.status,
                        'confidence':  str(instance.overall_confidence) if instance.overall_confidence else None,
                        'flagged':     instance.requires_manual_review,
                    },
                )
            except Exception:
                logger.exception("on_validation_save: error for validation=%s", instance.id)

        @receiver(post_save, sender=ManualReviewQueue)
        def on_review_save(sender, instance, created, **kwargs):
            if instance.status != 'resolved':
                return
            try:
                log_action(
                    action='review_resolved',
                    entity_type='ManualReviewQueue',
                    entity_id=str(instance.id),
                    organization=instance.document_validation.document.vendor.organization,
                    details={
                        'decision':    instance.resolution_decision,
                        'document_id': str(instance.document_validation.document.id),
                        'vendor':      instance.document_validation.document.vendor.name,
                        'reviewer':    instance.assigned_to.email if instance.assigned_to else None,
                    },
                )
            except Exception:
                logger.exception("on_review_save: error for review=%s", instance.id)

        logger.info("audit_logs.signals: all handlers connected")

    except Exception:
        logger.exception("audit_logs.signals: failed to connect handlers")


_safe_connect()