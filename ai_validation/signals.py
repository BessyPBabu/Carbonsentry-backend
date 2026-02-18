from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import DocumentValidation

@receiver(post_save, sender=DocumentValidation)
def validation_status_changed(sender, instance, created, **kwargs):
    if not created and instance.status == 'completed':
        if instance.requires_manual_review:
            instance.document.status = 'flagged'
        else:
            instance.document.status = 'valid'
        instance.document.save()
        