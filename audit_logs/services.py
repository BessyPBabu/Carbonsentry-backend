import logging
from .models import AuditLog

logger = logging.getLogger(__name__)

# Whitelist of valid action values - must match AuditLog.ACTION_CHOICES keys exactly
_VALID_ACTIONS = {c[0] for c in AuditLog.ACTION_CHOICES}


def log_action(
    action: str,
    entity_type: str = '',
    entity_id: str = '',
    details: dict = None,
    actor=None,
    organization=None,
    request=None,
):
    """
    Safe audit log writer. Never raises — logs a warning if it fails.

    Can be called two ways:
      1. With request:  log_action(action='user_login', request=request, ...)
      2. Without request: log_action(action='vendor_created', actor=user, organization=org, ...)
    """
    try:
        resolved_actor = actor
        resolved_org   = organization
        ip             = None

        if request is not None:
            if resolved_actor is None and hasattr(request, 'user') and request.user.is_authenticated:
                resolved_actor = request.user
            if resolved_org is None and resolved_actor is not None:
                resolved_org = getattr(resolved_actor, 'organization', None)
            ip = _get_ip(request)

        # Warn but still save with a fallback action if action is invalid
        if action not in _VALID_ACTIONS:
            logger.warning(
                "log_action: unknown action '%s' — saving anyway. "
                "Add it to AuditLog.ACTION_CHOICES and run migrations.",
                action,
            )

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
            "log_action: FAILED — action=%s entity=%s:%s",
            action, entity_type, entity_id,
        )


def _get_ip(request) -> str | None:
    try:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip() or None
        return request.META.get('REMOTE_ADDR', '').strip() or None
    except Exception:
        return None