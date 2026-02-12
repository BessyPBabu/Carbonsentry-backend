from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Avg
from .models import (
    DocumentValidation, VendorRiskProfile, ManualReviewQueue, AIAuditLog
)
from .serializers import (
    DocumentValidationSerializer, VendorRiskProfileSerializer,
    ManualReviewQueueSerializer, AIAuditLogSerializer
)
from .tasks import validate_document_async


class DocumentValidationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DocumentValidationSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return DocumentValidation.objects.filter(
            document__vendor__organization=self.request.user.organization
        ).select_related('document', 'document__vendor', 'metadata')
    
    @action(detail=False, methods=['post'])
    def trigger_validation(self, request):
        """Trigger validation for a document"""
        document_id = request.data.get('document_id')
        
        if not document_id:
            return Response(
                {'error': 'document_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Trigger async task
        task = validate_document_async.delay(document_id)
        
        return Response({
            'message': 'Validation started',
            'task_id': task.id,
            'document_id': document_id
        })
    
    @action(detail=True, methods=['get'])
    def audit_logs(self, request, pk=None):
        """Get audit logs for a validation"""
        validation = self.get_object()
        logs = validation.audit_logs.all()
        serializer = AIAuditLogSerializer(logs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get validation statistics for dashboard"""
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
            'by_step': queryset.values('current_step').annotate(count=Count('id'))
        }
        
        return Response(stats)
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent validations"""
        recent = self.get_queryset().order_by('-created_at')[:10]
        serializer = self.get_serializer(recent, many=True)
        return Response(serializer.data)


class VendorRiskProfileViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VendorRiskProfileSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return VendorRiskProfile.objects.filter(
            organization=self.request.user.organization
        ).select_related('vendor')
    
    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get dashboard statistics"""
        queryset = self.get_queryset()
        
        stats = {
            'total_vendors': queryset.count(),
            'low_risk': queryset.filter(risk_level='low').count(),
            'medium_risk': queryset.filter(risk_level='medium').count(),
            'high_risk': queryset.filter(risk_level='high').count(),
            'critical_risk': queryset.filter(risk_level='critical').count(),
        }
        
        return Response(stats)
    
    @action(detail=False, methods=['get'])
    def high_risk(self, request):
        """Get high and critical risk vendors"""
        high_risk = self.get_queryset().filter(
            risk_level__in=['high', 'critical']
        ).order_by('-risk_score')
        
        serializer = self.get_serializer(high_risk, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):
        """Manually trigger risk recalculation"""
        profile = self.get_object()
        
        from ai_validation.services.risk_calculator import RiskCalculator
        calculator = RiskCalculator()
        updated_profile = calculator.calculate(profile.vendor)
        
        serializer = self.get_serializer(updated_profile)
        return Response(serializer.data)


class ManualReviewQueueViewSet(viewsets.ModelViewSet):
    serializer_class = ManualReviewQueueSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return ManualReviewQueue.objects.filter(
            document_validation__document__vendor__organization=self.request.user.organization
        ).select_related('document_validation', 'assigned_to')
    
    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Assign review to current user"""
        review = self.get_object()
        review.assigned_to = request.user
        review.status = 'in_progress'
        review.save()
        
        return Response(self.get_serializer(review).data)
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Resolve review"""
        review = self.get_object()
        
        decision = request.data.get('decision')
        notes = request.data.get('notes', '')
        
        if not decision:
            return Response(
                {'error': 'decision is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        review.resolution_decision = decision
        review.reviewer_notes = notes
        review.status = 'resolved'
        review.assigned_to = request.user
        review.save()
        
        return Response(self.get_serializer(review).data)