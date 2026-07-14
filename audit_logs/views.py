import csv
import logging
from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AuditLog
from .serializers import AuditLogSerializer

logger = logging.getLogger(__name__)

_FORMULA_PREFIXES = ('=', '+', '-', '@')


def _csv_safe(value):
    text = str(value) if value is not None else ""
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _parse_date_param(value, param_name):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValidationError({param_name: f"Invalid date format, expected YYYY-MM-DD: {value}"})


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not hasattr(self.request.user, "organization"):
            logger.warning(
                "get_queryset: user %s has no organization", self.request.user.id
            )
            return AuditLog.objects.none()

        qs = AuditLog.objects.filter(
            organization=self.request.user.organization
        ).select_related("actor")

        action_filter = self.request.query_params.get("action")
        if action_filter:
            qs = qs.filter(action=action_filter)

        actor_id = self.request.query_params.get("actor")
        if actor_id:
            qs = qs.filter(actor_id=actor_id)

        entity_type = self.request.query_params.get("entity_type")
        if entity_type:
            qs = qs.filter(entity_type=entity_type)

        date_from = self.request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=_parse_date_param(date_from, "date_from"))

        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=_parse_date_param(date_to, "date_to"))

        return qs.order_by("-created_at")

    @action(detail=False, methods=["get"])
    def export_csv(self, request):
        try:
            qs = self.get_queryset()

            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = (
                f'attachment; filename="audit_log_{timezone.now():%Y%m%d_%H%M%S}.csv"'
            )

            writer = csv.writer(response)
            writer.writerow([
                "Timestamp", "Actor", "Action", "Entity Type",
                "Entity ID", "Details", "IP Address",
            ])

            
            for log in qs:
                writer.writerow([
                    log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    _csv_safe(log.actor.email if log.actor else "system"),
                    _csv_safe(log.get_action_display()),
                    _csv_safe(log.entity_type),
                    _csv_safe(log.entity_id),
                    _csv_safe(log.details),
                    _csv_safe(log.ip_address or ""),
                ])

            logger.info(
                "export_csv: exported %d rows | org=%s",
                qs.count(), request.user.organization.id,
            )
            return response

        except Exception:
            logger.exception(
                "export_csv: failed for user %s", request.user.id
            )
            return Response(
                {"error": "Export failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=["get"])
    def action_choices(self, request):
        return Response([
            {"value": value, "label": label}
            for value, label in AuditLog.ACTION_CHOICES
        ])