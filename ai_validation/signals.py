from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import DocumentValidation

@receiver(post_save, sender=DocumentValidation)
def validation_status_changed(sender, instance, created, **kwargs):
    """
    Signal to handle validation status changes
    Could trigger notifications, webhooks, etc.
    """
    if not created and instance.status == 'completed':
        # Update document status
        if instance.requires_manual_review:
            instance.document.status = 'flagged'
        else:
            instance.document.status = 'valid'
        instance.document.save()
        
        # Could add: Send notification to compliance officer
        # Could add: Update vendor risk profile immediately