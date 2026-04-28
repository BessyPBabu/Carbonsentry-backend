import logging
import time

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

_SOFT_LIMIT = 180
_HARD_LIMIT = 220


@shared_task(
    bind=True,
    max_retries=2,
    soft_time_limit=_SOFT_LIMIT,
    time_limit=_HARD_LIMIT,
)
def validate_document_async(self, document_id: str):
    from .metrics import (
        active_validations,
        confidence_histogram,
        validation_counter,
        validation_duration,
    )

    Document = apps.get_model("vendors", "Document")
    DocumentValidation = apps.get_model("ai_validation", "DocumentValidation")

    # ── Fetch and lock document ──────────────────────────────────────────────
    try:
        with transaction.atomic():
            try:
                document = (
                    Document.objects
                    .select_for_update()
                    .select_related("vendor", "document_type")
                    .get(id=document_id)
                )
            except Document.DoesNotExist:
                logger.error("validate_document_async: document not found document_id=%s", document_id)
                return {"success": False, "error": "document_not_found", "document_id": str(document_id)}

            if not document.file:
                logger.error("validate_document_async: no file attached document=%s", document_id)
                return {"success": False, "error": "no_file", "document_id": str(document_id)}

            validation, created = DocumentValidation.objects.get_or_create(
                document=document,
                defaults={"status": "processing", "started_at": timezone.now()},
            )

            if not created:
                if validation.status == "processing" and self.request.retries == 0:
                    logger.warning(
                        "validate_document_async: already processing document=%s", document_id
                    )
                    return {
                        "success": False,
                        "error": "already_processing",
                        "validation_id": str(validation.id),
                    }
                # Reset for retry
                validation.status = "processing"
                validation.current_step = "not_started"
                validation.started_at = timezone.now()
                validation.error_message = ""
                validation.retry_count = (validation.retry_count or 0) + 1
                validation.save(update_fields=[
                    "status", "current_step", "started_at", "error_message", "retry_count"
                ])

    except IntegrityError:
        logger.warning("validate_document_async: integrity error document=%s", document_id)
        return {"success": False, "error": "integrity_error", "document_id": str(document_id)}
    except Exception as exc:
        logger.error("validate_document_async: setup failed document=%s — %s", document_id, exc)
        return {"success": False, "error": str(exc), "document_id": str(document_id)}

    # ── Run validation pipeline ──────────────────────────────────────────────
    active_validations.inc()
    t0 = time.monotonic()

    try:
        from .services.orchestrator import ValidationOrchestrator
        validation = ValidationOrchestrator().validate_document(document, validation)

    except SoftTimeLimitExceeded:
        elapsed = round(time.monotonic() - t0, 2)
        logger.error(
            "validate_document_async: soft time limit exceeded document=%s elapsed=%.2fs",
            document_id, elapsed,
        )
        try:
            v = DocumentValidation.objects.filter(document_id=document_id).first()
            if v and v.status == "processing":
                v.status = "failed"
                v.error_message = f"soft_time_limit_exceeded_after_{elapsed}s"
                v.requires_manual_review = True
                v.flagged_reason = "timed_out"
                v.save(update_fields=["status", "error_message", "requires_manual_review", "flagged_reason"])
        except Exception:
            pass
        try:
            active_validations.dec()
        except Exception:
            pass
        try:
            validation_counter.labels(status="failed").inc()
            validation_duration.observe(elapsed)
        except Exception:
            pass
        return {"success": False, "error": "timeout", "document_id": str(document_id)}

    except Exception as exc:
        elapsed = round(time.monotonic() - t0, 2)
        logger.exception(
            "validate_document_async: unhandled error document=%s elapsed=%.2fs — %s",
            document_id, elapsed, exc,
        )
        try:
            v = DocumentValidation.objects.filter(document_id=document_id).first()
            if v:
                v.status = "failed"
                v.error_message = str(exc)[:1000]
                v.retry_count = (v.retry_count or 0) + 1
                v.save(update_fields=["status", "error_message", "retry_count"])
        except Exception:
            pass
        try:
            active_validations.dec()
        except Exception:
            pass
        try:
            validation_counter.labels(status="failed").inc()
            validation_duration.observe(elapsed)
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))

    # ── Record metrics ───────────────────────────────────────────────────────
    elapsed = round(time.monotonic() - t0, 2)
    try:
        active_validations.dec()
    except Exception:
        pass

    try:
        validation_duration.observe(elapsed)

        if validation.status == "failed":
            validation_counter.labels(status="failed").inc()
            logger.error(
                "validate_document_async: failed document=%s step=%s elapsed=%.2fs",
                document_id, validation.current_step, elapsed,
            )
        elif validation.requires_manual_review:
            validation_counter.labels(status="manual_review").inc()
            logger.info(
                "validate_document_async: flagged document=%s confidence=%.1f elapsed=%.2fs",
                document_id, float(validation.overall_confidence or 0), elapsed,
            )
        else:
            validation_counter.labels(status="valid").inc()
            logger.info(
                "validate_document_async: completed document=%s confidence=%.1f elapsed=%.2fs",
                document_id, float(validation.overall_confidence or 0), elapsed,
            )

        if validation.overall_confidence is not None:
            confidence_histogram.observe(float(validation.overall_confidence))

    except Exception:
        pass
    # ── Return result ─────────────────────────────────────────────────────────
    return {
        "success": validation.status != "failed",
        "document_id": str(document_id),
        "validation_id": str(validation.id),
        "status": validation.status,
        "confidence": float(validation.overall_confidence or 0),
        "requires_review": validation.requires_manual_review,
        "elapsed_s": elapsed,
    }