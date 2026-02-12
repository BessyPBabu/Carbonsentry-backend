from celery import shared_task
from django.apps import apps


@shared_task(bind=True, max_retries=3)
def validate_document_async(self, document_id):
    """Async task to validate document"""
    Document = apps.get_model('vendors', 'Document')
    
    try:
        document = Document.objects.get(id=document_id)
        
        # Import here to avoid circular imports
        from .services.orchestrator import ValidationOrchestrator
        
        orchestrator = ValidationOrchestrator()
        validation = orchestrator.validate_document(document)
        
        return {
            'success': True,
            'document_id': str(document_id),
            'validation_id': str(validation.id),
            'status': validation.status
        }
        
    except Document.DoesNotExist:
        return {'success': False, 'error': 'Document not found'}
    
    except Exception as e:
        # Retry on failure
        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))