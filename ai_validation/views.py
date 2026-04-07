import logging
import requests as http_requests

from django.conf import settings
from django.db.models import Avg
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DocumentValidation, VendorRiskProfile, ManualReviewQueue, AIAuditLog
from .serializers import (
    DocumentValidationSerializer,
    VendorRiskProfileSerializer,
    ManualReviewQueueSerializer,
    AIAuditLogSerializer,
)
from .tasks import validate_document_async

logger = logging.getLogger(__name__)


class DocumentValidationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DocumentValidationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            if not hasattr(self.request.user, "organization"):
                return DocumentValidation.objects.none()

            qs = DocumentValidation.objects.filter(
                document__vendor__organization=self.request.user.organization
            ).select_related(
                "document", "document__vendor",
                "document__document_type", "metadata",
            )

            vendor_id = self.request.query_params.get("vendor")
            if vendor_id:
                qs = qs.filter(document__vendor_id=vendor_id)

            status_filter = self.request.query_params.get("status")
            if status_filter:
                qs = qs.filter(status=status_filter)

            return qs.order_by("-created_at")

        except Exception:
            logger.exception("get_queryset: error for user %s", self.request.user.id)
            return DocumentValidation.objects.none()

    @action(detail=False, methods=["post"])
    def trigger_validation(self, request):
        document_id = request.data.get("document_id")
        if not document_id:
            return Response({"error": "document_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        from vendors.models import Document

        try:
            document = Document.objects.select_related("vendor").get(
                id=document_id,
                vendor__organization=request.user.organization,
            )
        except Document.DoesNotExist:
            return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("trigger_validation: error fetching document %s", document_id)
            return Response({"error": "Failed to fetch document"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not document.file:
            return Response({"error": "Document has no file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        if DocumentValidation.objects.filter(document=document, status="processing").exists():
            return Response({"error": "Validation already in progress"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = validate_document_async.delay(str(document_id))
        except Exception:
            logger.exception("trigger_validation: failed to queue task for document %s", document_id)
            return Response({"error": "Failed to start validation"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info("trigger_validation: queued task=%s for document=%s", task.id, document_id)
        return Response({"message": "Validation started", "task_id": task.id, "document_id": str(document_id)})

    @action(detail=True, methods=["get"])
    def audit_logs(self, request, pk=None):
        try:
            validation = self.get_object()
            logs = validation.audit_logs.order_by("-created_at")
            return Response(AIAuditLogSerializer(logs, many=True).data)
        except Exception:
            logger.exception("audit_logs: error for validation %s", pk)
            return Response({"error": "Failed to fetch audit logs"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"])
    def statistics(self, request):
        try:
            qs = self.get_queryset()
            return Response({
                "total_validations": qs.count(),
                "completed":         qs.filter(status="completed").count(),
                "processing":        qs.filter(status="processing").count(),
                "failed":            qs.filter(status="failed").count(),
                "requires_review":   qs.filter(requires_manual_review=True).count(),
                "avg_confidence":    qs.filter(
                    overall_confidence__isnull=False
                ).aggregate(avg=Avg("overall_confidence"))["avg"],
            })
        except Exception:
            logger.exception("statistics: error for user %s", request.user.id)
            return Response({"error": "Failed to fetch statistics"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"])
    def recent(self, request):
        try:
            recent = self.get_queryset().order_by("-created_at")[:10]
            return Response(self.get_serializer(recent, many=True).data)
        except Exception:
            logger.exception("recent: error for user %s", request.user.id)
            return Response({"error": "Failed to fetch recent validations"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VendorRiskProfileViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VendorRiskProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            if not hasattr(self.request.user, "organization"):
                return VendorRiskProfile.objects.none()

            qs = VendorRiskProfile.objects.filter(
                organization=self.request.user.organization
            ).select_related("vendor", "vendor__industry")

            if risk_level := self.request.query_params.get("risk_level"):
                qs = qs.filter(risk_level=risk_level)

            if vendor_id := self.request.query_params.get("vendor"):
                qs = qs.filter(vendor_id=vendor_id)

            return qs.order_by("-risk_score")

        except Exception:
            logger.exception("get_queryset: error for user %s", self.request.user.id)
            return VendorRiskProfile.objects.none()

    @action(detail=False, methods=["get"])
    def dashboard_stats(self, request):
        try:
            qs = self.get_queryset()
            return Response({
                "total_vendors": qs.count(),
                "low_risk":      qs.filter(risk_level="low").count(),
                "medium_risk":   qs.filter(risk_level="medium").count(),
                "high_risk":     qs.filter(risk_level="high").count(),
                "critical_risk": qs.filter(risk_level="critical").count(),
            })
        except Exception:
            logger.exception("dashboard_stats: error for user %s", request.user.id)
            return Response({"error": "Failed to fetch stats"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"])
    def high_risk(self, request):
        try:
            qs = self.get_queryset().filter(risk_level__in=["high", "critical"])
            return Response(self.get_serializer(qs, many=True).data)
        except Exception:
            logger.exception("high_risk: error for user %s", request.user.id)
            return Response({"error": "Failed to fetch high risk vendors"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        try:
            profile = self.get_object()
            from ai_validation.services.risk_calculator import RiskCalculator
            updated = RiskCalculator().calculate(profile.vendor)
            logger.info("recalculate: done for vendor %s new_level=%s", profile.vendor.id, updated.risk_level)
            return Response(self.get_serializer(updated).data)
        except Exception:
            logger.exception("recalculate: error for profile %s", pk)
            return Response({"error": "Failed to recalculate risk"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManualReviewQueueViewSet(viewsets.ModelViewSet):
    serializer_class = ManualReviewQueueSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            if not hasattr(self.request.user, "organization"):
                return ManualReviewQueue.objects.none()

            qs = ManualReviewQueue.objects.filter(
                document_validation__document__vendor__organization=self.request.user.organization
            ).select_related(
                "document_validation",
                "document_validation__document",
                "document_validation__document__vendor",
                "document_validation__document__document_type",
                "document_validation__metadata",
                "assigned_to",
            )

            if s := self.request.query_params.get("status"):
                qs = qs.filter(status=s)

            if p := self.request.query_params.get("priority"):
                qs = qs.filter(priority=p)

            if vendor_id := self.request.query_params.get("vendor"):
                qs = qs.filter(document_validation__document__vendor_id=vendor_id)

            ordering = self.request.query_params.get("ordering", "-created_at")
            # whitelist safe ordering fields to avoid arbitrary column injection
            SAFE_ORDERING = {
                "-created_at", "created_at",
                "-resolved_at", "resolved_at",
                "-priority", "priority",
            }
            if ordering not in SAFE_ORDERING:
                ordering = "-created_at"
            return qs.order_by(ordering)

        except Exception:
            logger.exception("get_queryset: error for user %s", self.request.user.id)
            return ManualReviewQueue.objects.none()

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        try:
            review = self.get_object()
            review.assigned_to = request.user
            review.status = "in_progress"
            review.save(update_fields=["assigned_to", "status"])
            return Response(self.get_serializer(review).data)
        except Exception:
            logger.exception("assign: error for review %s", pk)
            return Response({"error": "Failed to assign review"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        decision = request.data.get("decision")
        notes = request.data.get("notes", "")

        if not decision:
            return Response({"error": "decision is required"}, status=status.HTTP_400_BAD_REQUEST)

        if decision not in ("approved", "rejected", "needs_changes"):
            return Response(
                {"error": "decision must be: approved, rejected, or needs_changes"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            review = self.get_object()
            review.resolution_decision = decision
            review.reviewer_notes = notes
            review.status = "resolved"
            review.assigned_to = request.user
            review.resolved_at = timezone.now()
            review.save()

            doc = review.document_validation.document
            if decision == "approved":
                doc.status = "valid"
            elif decision == "rejected":
                doc.status = "invalid"
            doc.save(update_fields=["status"])

            logger.info(
                "resolve: review=%s decision=%s doc=%s new_status=%s",
                pk, decision, doc.id, doc.status,
            )
            return Response(self.get_serializer(review).data)

        except Exception:
            logger.exception("resolve: error for review %s", pk)
            return Response({"error": "Failed to resolve review"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AIMonitoringView(APIView):
    permission_classes = [IsAuthenticated]

    _PROMETHEUS_QUERIES = {
        "validations_valid":   'carbonsentry_validations_total{status="valid"}',
        "validations_invalid": 'carbonsentry_validations_total{status="failed"}',
        "validations_review":  'carbonsentry_validations_total{status="manual_review"}',
        "validations_failed":  'carbonsentry_validations_total{status="failed"}',
        "p95_duration_s":      "histogram_quantile(0.95, rate(carbonsentry_validation_duration_seconds_bucket[1h]))",
        "gemini_success":      'sum(carbonsentry_gemini_calls_total{success="true"})',
        "gemini_failed":       'sum(carbonsentry_gemini_calls_total{success="false"})',
        "median_confidence":   "histogram_quantile(0.50, rate(carbonsentry_confidence_score_bucket[1h]))",
        "active_validations":  "carbonsentry_active_validations",
        "review_queue_size":   "carbonsentry_manual_review_queue_size",
    }

    def get(self, request):
        if not hasattr(request.user, "organization"):
            return Response({"error": "No organization found"}, status=status.HTTP_403_FORBIDDEN)

        prometheus_url = getattr(settings, "PROMETHEUS_URL", "").strip()
        grafana_url = getattr(settings, "GRAFANA_URL", "").strip()

        if prometheus_url:
            metrics, source = self._from_prometheus(prometheus_url)
        else:
            metrics, source = self._from_db(request.user.organization), "database"

        return Response({
            "source": source,
            "grafana_url": grafana_url or None,
            "metrics": metrics,
        })

    def _from_prometheus(self, base_url):
        results = {}
        failed_queries = []

        for key, query in self._PROMETHEUS_QUERIES.items():
            try:
                resp = http_requests.get(
                    f"{base_url}/api/v1/query",
                    params={"query": query},
                    timeout=3,
                )
                if resp.ok:
                    data = resp.json().get("data", {}).get("result", [])
                    results[key] = round(float(data[0]["value"][1]), 2) if data else 0
                else:
                    results[key] = 0
                    failed_queries.append(key)
            except Exception as exc:
                logger.warning("AIMonitoringView._from_prometheus: %s failed — %s", key, exc)
                results[key] = 0
                failed_queries.append(key)

        if failed_queries:
            logger.warning(
                "AIMonitoringView._from_prometheus: %d queries failed: %s",
                len(failed_queries), failed_queries,
            )

        # if all queries failed, fall back to DB
        if len(failed_queries) == len(self._PROMETHEUS_QUERIES):
            logger.warning("AIMonitoringView: all Prometheus queries failed, using DB fallback")
            db_metrics = self._from_db(None)
            return db_metrics, "database"

        return results, "prometheus"

    def _from_db(self, organization):
        try:
            qs = DocumentValidation.objects.all()
            if organization:
                qs = qs.filter(document__vendor__organization=organization)

            completed = qs.filter(status="completed")
            avg_conf = (
                qs.filter(overall_confidence__isnull=False)
                .aggregate(avg=Avg("overall_confidence"))["avg"] or 0
            )
            queue_size = ManualReviewQueue.objects.filter(status="pending").count()
            if organization:
                queue_size = ManualReviewQueue.objects.filter(
                    status="pending",
                    document_validation__document__vendor__organization=organization,
                ).count()

            return {
                "validations_valid":   completed.filter(requires_manual_review=False).count(),
                "validations_invalid": qs.filter(status="failed").count(),
                "validations_review":  qs.filter(requires_manual_review=True).count(),
                "validations_failed":  qs.filter(status="failed").count(),
                "p95_duration_s":      None,
                "gemini_success":      None,
                "gemini_failed":       None,
                "median_confidence":   round(float(avg_conf), 1),
                "active_validations":  qs.filter(status="processing").count(),
                "review_queue_size":   queue_size,
            }
        except Exception:
            logger.exception("AIMonitoringView._from_db: error")
            return {}