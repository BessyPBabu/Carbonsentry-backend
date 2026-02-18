import logging

from django.utils import timezone
from django.db.models import Avg

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import DocumentValidation, VendorRiskProfile, ManualReviewQueue, AIAuditLog
from .serializers import (
    DocumentValidationSerializer, VendorRiskProfileSerializer,
    ManualReviewQueueSerializer, AIAuditLogSerializer,
)
from .tasks import validate_document_async

logger = logging.getLogger(__name__)


class DocumentValidationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DocumentValidationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            if not hasattr(self.request.user, 'organization'):
                logger.warning("get_queryset: user %s has no organization", self.request.user.id)
                return DocumentValidation.objects.none()

            qs = DocumentValidation.objects.filter(
                document__vendor__organization=self.request.user.organization
            ).select_related(
                'document', 'document__vendor',
                'document__document_type', 'metadata',
            )

            vendor_id = self.request.query_params.get('vendor')
            if vendor_id:
                qs = qs.filter(document__vendor_id=vendor_id)

            status_filter = self.request.query_params.get('status')
            if status_filter:
                qs = qs.filter(status=status_filter)

            return qs.order_by('-created_at')

        except Exception:
            logger.exception("get_queryset: unexpected error for user %s", self.request.user.id)
            return DocumentValidation.objects.none()

    @action(detail=False, methods=['post'])
    def trigger_validation(self, request):
        document_id = request.data.get('document_id')
        if not document_id:
            return Response({'error': 'document_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        from vendors.models import Document
        try:
            document = Document.objects.select_related('vendor').get(
                id=document_id,
                vendor__organization=request.user.organization,
            )
        except Document.DoesNotExist:
            logger.warning("trigger_validation: document %s not found for user %s", document_id, request.user.id)
            return Response({'error': 'Document not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("trigger_validation: error fetching document %s", document_id)
            return Response({'error': 'Failed to fetch document'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not document.file:
            return Response({'error': 'Document has no file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        if DocumentValidation.objects.filter(document=document, status='processing').exists():
            return Response({'error': 'Validation already in progress'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = validate_document_async.delay(str(document_id))
        except Exception:
            logger.exception("trigger_validation: failed to queue task for document %s", document_id)
            return Response({'error': 'Failed to start validation'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info("trigger_validation: queued task=%s for document=%s", task.id, document_id)
        return Response({'message': 'Validation started', 'task_id': task.id, 'document_id': str(document_id)})

    @action(detail=True, methods=['get'])
    def audit_logs(self, request, pk=None):
        try:
            validation = self.get_object()
            logs = validation.audit_logs.order_by('-created_at')
            return Response(AIAuditLogSerializer(logs, many=True).data)
        except Exception:
            logger.exception("audit_logs: error for validation %s", pk)
            return Response({'error': 'Failed to fetch audit logs'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        try:
            qs = self.get_queryset()
            return Response({
                'total_validations': qs.count(),
                'completed':         qs.filter(status='completed').count(),
                'processing':        qs.filter(status='processing').count(),
                'failed':            qs.filter(status='failed').count(),
                'requires_review':   qs.filter(requires_manual_review=True).count(),
                'avg_confidence':    qs.filter(
                    overall_confidence__isnull=False
                ).aggregate(avg=Avg('overall_confidence'))['avg'],
            })
        except Exception:
            logger.exception("statistics: error for user %s", request.user.id)
            return Response({'error': 'Failed to fetch statistics'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def recent(self, request):
        try:
            recent = self.get_queryset().order_by('-created_at')[:10]
            return Response(self.get_serializer(recent, many=True).data)
        except Exception:
            logger.exception("recent: error for user %s", request.user.id)
            return Response({'error': 'Failed to fetch recent validations'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VendorRiskProfileViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VendorRiskProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            if not hasattr(self.request.user, 'organization'):
                logger.warning("get_queryset: user %s has no organization", self.request.user.id)
                return VendorRiskProfile.objects.none()

            qs = VendorRiskProfile.objects.filter(
                organization=self.request.user.organization
            ).select_related('vendor', 'vendor__industry')

            if risk_level := self.request.query_params.get('risk_level'):
                qs = qs.filter(risk_level=risk_level)

            if vendor_id := self.request.query_params.get('vendor'):
                qs = qs.filter(vendor_id=vendor_id)

            return qs.order_by('-risk_score')

        except Exception:
            logger.exception("get_queryset: error for user %s", self.request.user.id)
            return VendorRiskProfile.objects.none()

    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        try:
            qs = self.get_queryset()
            return Response({
                'total_vendors': qs.count(),
                'low_risk':      qs.filter(risk_level='low').count(),
                'medium_risk':   qs.filter(risk_level='medium').count(),
                'high_risk':     qs.filter(risk_level='high').count(),
                'critical_risk': qs.filter(risk_level='critical').count(),
            })
        except Exception:
            logger.exception("dashboard_stats: error for user %s", request.user.id)
            return Response({'error': 'Failed to fetch stats'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def high_risk(self, request):
        try:
            qs = self.get_queryset().filter(risk_level__in=['high', 'critical'])
            return Response(self.get_serializer(qs, many=True).data)
        except Exception:
            logger.exception("high_risk: error for user %s", request.user.id)
            return Response({'error': 'Failed to fetch high risk vendors'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):
        try:
            profile = self.get_object()
            from ai_validation.services.risk_calculator import RiskCalculator
            updated = RiskCalculator().calculate(profile.vendor)
            logger.info("recalculate: done for vendor %s new_level=%s", profile.vendor.id, updated.risk_level)
            return Response(self.get_serializer(updated).data)
        except Exception:
            logger.exception("recalculate: error for profile %s", pk)
            return Response({'error': 'Failed to recalculate risk'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManualReviewQueueViewSet(viewsets.ModelViewSet):
    serializer_class = ManualReviewQueueSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            if not hasattr(self.request.user, 'organization'):
                logger.warning("get_queryset: user %s has no organization", self.request.user.id)
                return ManualReviewQueue.objects.none()

            qs = ManualReviewQueue.objects.filter(
                document_validation__document__vendor__organization=self.request.user.organization
            ).select_related(
                'document_validation',
                'document_validation__document',
                'document_validation__document__vendor',
                'document_validation__document__document_type',
                'document_validation__metadata',
                'assigned_to',
            )

            if s := self.request.query_params.get('status'):
                qs = qs.filter(status=s)

            if p := self.request.query_params.get('priority'):
                qs = qs.filter(priority=p)

            return qs.order_by('-created_at')

        except Exception:
            logger.exception("get_queryset: error for user %s", self.request.user.id)
            return ManualReviewQueue.objects.none()

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        try:
            review = self.get_object()
            review.assigned_to = request.user
            review.status = 'in_progress'
            review.save(update_fields=['assigned_to', 'status'])
            logger.info("assign: review=%s assigned to user=%s", pk, request.user.id)
            return Response(self.get_serializer(review).data)
        except Exception:
            logger.exception("assign: error for review %s", pk)
            return Response({'error': 'Failed to assign review'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        decision = request.data.get('decision')
        notes = request.data.get('notes', '')

        if not decision:
            return Response({'error': 'decision is required'}, status=status.HTTP_400_BAD_REQUEST)

        if decision not in ('approved', 'rejected', 'needs_changes'):
            return Response(
                {'error': 'decision must be: approved, rejected, or needs_changes'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            review = self.get_object()
            review.resolution_decision = decision
            review.reviewer_notes = notes
            review.status = 'resolved'
            review.assigned_to = request.user
            review.resolved_at = timezone.now()
            review.save()

            # update document status based on human decision
            doc = review.document_validation.document
            if decision == 'approved':
                doc.status = 'valid'
            elif decision == 'rejected':
                doc.status = 'invalid'
            # needs_changes keeps it as 'flagged'
            doc.save(update_fields=['status'])

            logger.info(
                "resolve: review=%s decision=%s doc=%s new_status=%s",
                pk, decision, doc.id, doc.status,
            )
            return Response(self.get_serializer(review).data)

        except Exception:
            logger.exception("resolve: error for review %s", pk)
            return Response({'error': 'Failed to resolve review'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)