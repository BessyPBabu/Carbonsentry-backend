import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Avg
from django.utils import timezone

from .models import (
    DocumentValidation, VendorRiskProfile, ManualReviewQueue, AIAuditLog
)
from .serializers import (
    DocumentValidationSerializer, VendorRiskProfileSerializer,
    ManualReviewQueueSerializer, AIAuditLogSerializer
)
from .tasks import validate_document_async

logger = logging.getLogger("ai_validation.views")


class DocumentValidationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DocumentValidationSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        try:
            if not hasattr(self.request.user, 'organization'):
                logger.error("User has no organization")
                return DocumentValidation.objects.none()

            return DocumentValidation.objects.filter(
                document__vendor__organization=self.request.user.organization
            ).select_related(
                'document', 
                'document__vendor', 
                'document__document_type',
                'metadata'
            )
        except Exception as e:
            logger.exception("Error in get_queryset for DocumentValidation")
            return DocumentValidation.objects.none()
    
    @action(detail=False, methods=['post'])
    def trigger_validation(self, request):
        """Trigger validation for a document"""
        try:
            document_id = request.data.get('document_id')
            
            if not document_id:
                logger.warning("Validation trigger attempted without document_id")
                return Response(
                    {'error': 'document_id is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify document exists and belongs to user's organization
            from vendors.models import Document
            
            try:
                document = Document.objects.select_related('vendor').get(
                    id=document_id,
                    vendor__organization=request.user.organization
                )
            except Document.DoesNotExist:
                logger.warning(
                    f"Document not found or access denied: {document_id}",
                    extra={"user_id": str(request.user.id)}
                )
                return Response(
                    {'error': 'Document not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Check if document has a file
            if not document.file:
                logger.warning(f"Document has no file: {document_id}")
                return Response(
                    {'error': 'Document has no file uploaded'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Trigger async task
            task = validate_document_async.delay(str(document_id))
            
            logger.info(
                "Validation triggered",
                extra={
                    "document_id": str(document_id),
                    "task_id": task.id,
                    "user_id": str(request.user.id)
                }
            )
            
            return Response({
                'message': 'Validation started',
                'task_id': task.id,
                'document_id': str(document_id)
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("Failed to trigger validation")
            return Response(
                {'error': 'Failed to start validation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def audit_logs(self, request, pk=None):
        """Get audit logs for a validation"""
        try:
            validation = self.get_object()
            logs = validation.audit_logs.all().order_by('-created_at')
            serializer = AIAuditLogSerializer(logs, many=True)
            
            logger.info(
                "Audit logs fetched",
                extra={
                    "validation_id": str(pk),
                    "log_count": logs.count()
                }
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"Failed to fetch audit logs for validation {pk}")
            return Response(
                {'error': 'Failed to fetch audit logs'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get validation statistics for dashboard"""
        try:
            queryset = self.get_queryset()
            
            stats = {
                'total_validations': queryset.count(),
                'completed': queryset.filter(status='completed').count(),
                'processing': queryset.filter(status='processing').count(),
                'failed': queryset.filter(status='failed').count(),
                'requires_review': queryset.filter(requires_manual_review=True).count(),
                'avg_confidence': queryset.filter(
                    overall_confidence__isnull=False
                ).aggregate(avg=Avg('overall_confidence'))['avg'],
                'by_step': list(queryset.values('current_step').annotate(count=Count('id')))
            }
            
            logger.info(
                "Validation statistics fetched",
                extra={
                    "organization_id": str(request.user.organization.id),
                    "total": stats['total_validations']
                }
            )
            
            return Response(stats, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("Failed to fetch validation statistics")
            return Response(
                {'error': 'Failed to fetch statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent validations"""
        try:
            recent = self.get_queryset().order_by('-created_at')[:10]
            serializer = self.get_serializer(recent, many=True)
            
            logger.info(
                "Recent validations fetched",
                extra={"count": len(serializer.data)}
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("Failed to fetch recent validations")
            return Response(
                {'error': 'Failed to fetch recent validations'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VendorRiskProfileViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VendorRiskProfileSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        try:
            if not hasattr(self.request.user, 'organization'):
                logger.error("User has no organization")
                return VendorRiskProfile.objects.none()

            queryset = VendorRiskProfile.objects.filter(
                organization=self.request.user.organization
            ).select_related('vendor', 'vendor__industry')
            
            # Apply filters
            risk_level = self.request.query_params.get('risk_level')
            if risk_level:
                queryset = queryset.filter(risk_level=risk_level)
                logger.debug(f"Applied risk_level filter: {risk_level}")
            
            vendor_id = self.request.query_params.get('vendor')
            if vendor_id:
                queryset = queryset.filter(vendor_id=vendor_id)
                logger.debug(f"Applied vendor filter: {vendor_id}")
            
            return queryset.order_by('-risk_score')
            
        except Exception as e:
            logger.exception("Error in get_queryset for VendorRiskProfile")
            return VendorRiskProfile.objects.none()
    
    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get dashboard statistics"""
        try:
            queryset = self.get_queryset()
            
            stats = {
                'total_vendors': queryset.count(),
                'low_risk': queryset.filter(risk_level='low').count(),
                'medium_risk': queryset.filter(risk_level='medium').count(),
                'high_risk': queryset.filter(risk_level='high').count(),
                'critical_risk': queryset.filter(risk_level='critical').count(),
            }
            
            logger.info(
                "Risk dashboard stats fetched",
                extra={
                    "organization_id": str(request.user.organization.id),
                    "stats": stats
                }
            )
            
            return Response(stats, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("Failed to fetch risk dashboard stats")
            return Response(
                {'error': 'Failed to fetch statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def high_risk(self, request):
        """Get high and critical risk vendors"""
        try:
            high_risk = self.get_queryset().filter(
                risk_level__in=['high', 'critical']
            ).order_by('-risk_score')
            
            serializer = self.get_serializer(high_risk, many=True)
            
            logger.info(
                "High risk vendors fetched",
                extra={"count": high_risk.count()}
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("Failed to fetch high risk vendors")
            return Response(
                {'error': 'Failed to fetch high risk vendors'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):
        """Manually trigger risk recalculation"""
        try:
            profile = self.get_object()
            
            from ai_validation.services.risk_calculator import RiskCalculator
            calculator = RiskCalculator()
            updated_profile = calculator.calculate(profile.vendor)
            
            serializer = self.get_serializer(updated_profile)
            
            logger.info(
                "Risk recalculated",
                extra={
                    "profile_id": str(pk),
                    "vendor_id": str(profile.vendor.id),
                    "new_risk_level": updated_profile.risk_level
                }
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"Failed to recalculate risk for profile {pk}")
            return Response(
                {'error': 'Failed to recalculate risk'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ManualReviewQueueViewSet(viewsets.ModelViewSet):
    serializer_class = ManualReviewQueueSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        try:
            if not hasattr(self.request.user, 'organization'):
                logger.error("User has no organization")
                return ManualReviewQueue.objects.none()

            queryset = ManualReviewQueue.objects.filter(
                document_validation__document__vendor__organization=self.request.user.organization
            ).select_related(
                'document_validation',
                'document_validation__document',
                'document_validation__document__vendor',
                'document_validation__document__document_type',
                'assigned_to'
            ).prefetch_related('document_validation__metadata')
            
            # Apply filters
            status_filter = self.request.query_params.get('status')
            if status_filter:
                queryset = queryset.filter(status=status_filter)
                logger.debug(f"Applied status filter: {status_filter}")
            
            priority = self.request.query_params.get('priority')
            if priority:
                queryset = queryset.filter(priority=priority)
                logger.debug(f"Applied priority filter: {priority}")
            
            return queryset.order_by('-created_at')
            
        except Exception as e:
            logger.exception("Error in get_queryset for ManualReviewQueue")
            return ManualReviewQueue.objects.none()
    
    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Assign review to current user"""
        try:
            review = self.get_object()
            review.assigned_to = request.user
            review.status = 'in_progress'
            review.save()
            
            logger.info(
                "Review assigned",
                extra={
                    "review_id": str(pk),
                    "assigned_to": request.user.email
                }
            )
            
            return Response(
                self.get_serializer(review).data,
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            logger.exception(f"Failed to assign review {pk}")
            return Response(
                {'error': 'Failed to assign review'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Resolve review"""
        try:
            review = self.get_object()
            
            decision = request.data.get('decision')
            notes = request.data.get('notes', '')
            
            if not decision:
                logger.warning(f"Review resolution attempted without decision: {pk}")
                return Response(
                    {'error': 'decision is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if decision not in ['approved', 'rejected', 'needs_changes']:
                logger.warning(f"Invalid decision: {decision}")
                return Response(
                    {'error': 'Invalid decision. Must be approved, rejected, or needs_changes'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            review.resolution_decision = decision
            review.reviewer_notes = notes
            review.status = 'resolved'
            review.assigned_to = request.user
            review.resolved_at = timezone.now()
            review.save()
            
            logger.info(
                "Review resolved",
                extra={
                    "review_id": str(pk),
                    "decision": decision,
                    "resolved_by": request.user.email
                }
            )
            
            return Response(
                self.get_serializer(review).data,
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            logger.exception(f"Failed to resolve review {pk}")
            return Response(
                {'error': 'Failed to resolve review'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )