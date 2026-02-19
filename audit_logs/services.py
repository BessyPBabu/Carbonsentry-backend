import logging
from .models import AuditLog

logger = logging.getLogger(__name__)


def log_action(request=None, action='', entity_type='', entity_id='', details=None, actor=None, organization=None):
    """
    Reusable audit log helper. Call from any view or service.
    Never raises — audit logging must not break the caller.
    """
    try:
        resolved_actor = actor
        resolved_org = organization
        ip = None

        if request:
            resolved_actor = resolved_actor or request.user
            if hasattr(request.user, 'organization'):
                resolved_org = resolved_org or request.user.organization
            ip = _get_ip(request)

        AuditLog.objects.create(
            organization=resolved_org,
            actor=resolved_actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details or {},
            ip_address=ip,
        )

    except Exception:
        logger.exception(
            "log_action: failed — action=%s entity=%s:%s",
            action, entity_type, entity_id,
        )


def _get_ip(request):
    try:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
    except Exception:
        return None