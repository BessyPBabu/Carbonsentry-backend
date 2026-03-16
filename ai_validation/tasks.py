from celery import shared_task
from django.apps import apps
import time
from django.utils import timezone
from django.db import transaction, IntegrityError
from prometheus_client import Counter, Histogram

import logging

logger = logging.getLogger(__name__)


# counters increment once per event
validation_counter = Counter(
    'carbonsentry_validations_total',
    'Total document validations run',
    ['status']  # label: valid / invalid / manual_review / failed
)

validation_duration = Histogram(
    'carbonsentry_validation_duration_seconds',
    'Time taken for full validation pipeline',
    buckets=[1, 2, 5, 10, 20, 30, 60]
)

gemini_call_counter = Counter(
    'carbonsentry_gemini_calls_total',
    'Gemini API calls per pipeline step',
    ['step', 'success']  # step: readability/relevance/authenticity/extraction
)

confidence_histogram = Histogram(
    'carbonsentry_confidence_score',
    'Distribution of AI confidence scores',
    buckets=[10, 20, 30, 40, 50, 55, 60, 70, 80, 90, 100]
)

@shared_task(bind=True, max_retries=3)
def validate_document_async(self, document_id):
    Document = apps.get_model('vendors', 'Document')
    DocumentValidation = apps.get_model('ai_validation', 'DocumentValidation')

    try:
        logger.info(f"Starting validation for document {document_id}")
        
        with transaction.atomic():
            document = (
                Document.objects
                .select_for_update()
                .select_related('vendor', 'document_type')
                .get(id=document_id)
            )
            
            if not document.file:
                logger.error(f"Document {document_id} has no file attached")
                return {
                    "success": False,
                    "error": "No file attached",
                    "document_id": str(document_id)
                }

            validation, created = DocumentValidation.objects.get_or_create(
                document=document,
                defaults={
                    "status": "processing",
                    "started_at": timezone.now()
                }
            )

            if not created:
                logger.info(f"Resetting validation {validation.id}")
                validation.status = "processing"
                validation.current_step = "not_started"
                validation.started_at = timezone.now()
                validation.error_message = ""
                validation.retry_count = 0
                validation.save()

        from .services.orchestrator import ValidationOrchestrator

        start = time.time()

        orchestrator = ValidationOrchestrator()
        validation = orchestrator.validate_document(document, validation)

        # record duration regardless of outcome
        validation_duration.observe(time.time() - start)

        if validation.status == "failed":
            logger.error(
                f"Validation {validation.id} failed at '{validation.current_step}': "
                f"{validation.error_message}"
            )
            validation_counter.labels(status='failed').inc()
        else:
            logger.info(f"Validation {validation.id} completed successfully")
            validation.status = "completed"
            validation.completed_at = timezone.now()
            validation.save(update_fields=["status", "completed_at"])

            # pick the right label for the counter
            if validation.requires_manual_review:
                validation_counter.labels(status='manual_review').inc()
            elif validation.overall_result == 'valid':
                validation_counter.labels(status='valid').inc()
            else:
                validation_counter.labels(status='invalid').inc()

            # record confidence score if available
            if validation.overall_confidence:
                confidence_histogram.observe(float(validation.overall_confidence))

        return {
            "success": validation.status != "failed",
            "document_id": str(document_id),
            "validation_id": str(validation.id),
            "status": validation.status,
            "error": validation.error_message if validation.status == "failed" else None
        }
    
    except IntegrityError:
        logger.warning(f"Validation already exists for document {document_id}")
        validation = DocumentValidation.objects.get(document_id=document_id)
        return {
            "success": False,
            "error": "Validation already exists",
            "validation_id": str(validation.id)
        }

    except Document.DoesNotExist:
        logger.error(f"Document {document_id} not found")
        return {
            "success": False,
            "error": "Document not found"
        }

    except Exception as e:
        validation_counter.labels(status='failed').inc()
        logger.exception(f"Failed to validate document {document_id}")
        
        try:
            validation = DocumentValidation.objects.filter(document_id=document_id).first()
            if validation:
                validation.status = "failed"
                validation.error_message = str(e)
                validation.retry_count += 1
                validation.save(update_fields=["status", "error_message", "retry_count"])
        except Exception as save_error:
            logger.error(f"Could not save validation error: {save_error}")

        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))