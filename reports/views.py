import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Report
from .serializers import ApproveReportSerializer, GenerateReportSerializer, ReportSerializer
from .services import PDFExporter, ReportGenerator

logger = logging.getLogger(__name__)


class ReportViewSet(viewsets.ModelViewSet):
    serializer_class = ReportSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        qs = Report.objects.filter(
            organization=user.organization
        ).select_related('generated_by', 'approved_by', 'vendor')

        # viewers only see approved reports
        if user.role == 'viewer':
            qs = qs.filter(status='approved')

        # optional query filters
        report_type = self.request.query_params.get('report_type')
        status_filter = self.request.query_params.get('status')
        vendor_id = self.request.query_params.get('vendor')

        if report_type:
            qs = qs.filter(report_type=report_type)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if vendor_id:
            qs = qs.filter(vendor__id=vendor_id)

        return qs

    def destroy(self, request, *args, **kwargs):
        report = self.get_object()

        # approved reports cannot be deleted — they're part of the compliance record
        if report.status == 'approved':
            logger.warning(
                "User=%s attempted to delete approved report=%s",
                request.user.id, report.id
            )
            return Response(
                {'error': 'Approved reports cannot be deleted.'},
                status=status.HTTP_403_FORBIDDEN
            )

        logger.info("Report deleted | report=%s user=%s", report.id, request.user.id)
        return super().destroy(request, *args, **kwargs)

    # ------------------------------------------------------------------
    # POST /reports/generate/
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        if request.user.role == 'viewer':
            return Response(
                {'error': 'Viewers cannot generate reports.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = GenerateReportSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(
                "Invalid generate report payload | user=%s errors=%s",
                request.user.id, serializer.errors
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        report_type = data['report_type']
        vendor = None

        if data.get('vendor_id'):
            from vendors.models import Vendor
            try:
                vendor = Vendor.objects.get(
                    id=data['vendor_id'],
                    organization=request.user.organization
                )
            except Vendor.DoesNotExist:
                logger.warning(
                    "Vendor not found for report generation | vendor_id=%s user=%s",
                    data['vendor_id'], request.user.id
                )
                return Response(
                    {'error': 'Vendor not found.'},
                    status=status.HTTP_404_NOT_FOUND
                )

        # create the report record first (status=draft) so we have an ID
        report = Report.objects.create(
            organization=request.user.organization,
            report_type=report_type,
            title=data['title'],
            vendor=vendor,
            date_from=data.get('date_from'),
            date_to=data.get('date_to'),
            generated_by=request.user,
            status='draft',
        )

        logger.info(
            "Report record created | report=%s type=%s user=%s",
            report.id, report_type, request.user.id
        )

        try:
            generator = ReportGenerator()
            report_data = generator.generate(
                report_type=report_type,
                organization=request.user.organization,
                vendor=vendor,
                date_from=data.get('date_from'),
                date_to=data.get('date_to'),
            )
            report.data = report_data
            report.status = 'generated'
            report.save(update_fields=['data', 'status'])

            logger.info("Report generated successfully | report=%s", report.id)

        except Exception as exc:
            # mark it failed so users know something went wrong
            report.status = 'draft'
            report.save(update_fields=['status'])
            logger.exception(
                "Report generation failed | report=%s error=%s", report.id, str(exc)
            )
            return Response(
                {'error': 'Report generation failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        out_serializer = ReportSerializer(report)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # PATCH /reports/{id}/approve/
    # ------------------------------------------------------------------

    @action(detail=True, methods=['patch'], url_path='approve')
    def approve(self, request, pk=None):
        if request.user.role not in ('officer', 'admin'):
            return Response(
                {'error': 'Only officers and admins can approve reports.'},
                status=status.HTTP_403_FORBIDDEN
            )

        report = self.get_object()

        if report.status == 'approved':
            return Response(
                {'error': 'Report is already approved.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if report.status == 'draft':
            return Response(
                {'error': 'Draft reports cannot be approved — generate the report first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ApproveReportSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        report.status = 'approved'
        report.approved_by = request.user
        report.approved_at = timezone.now()
        report.approval_notes = serializer.validated_data.get('approval_notes', '')
        report.save(update_fields=['status', 'approved_by', 'approved_at', 'approval_notes'])

        logger.info(
            "Report approved | report=%s approver=%s", report.id, request.user.id
        )

        out_serializer = ReportSerializer(report)
        return Response(out_serializer.data)

    # ------------------------------------------------------------------
    # GET /reports/{id}/download_pdf/
    # ------------------------------------------------------------------

    @action(detail=True, methods=['get'], url_path='download_pdf')
    def download_pdf(self, request, pk=None):
        report = self.get_object()

        # viewers can only download approved reports
        if request.user.role == 'viewer' and report.status != 'approved':
            return Response(
                {'error': 'Viewers can only download approved reports.'},
                status=status.HTTP_403_FORBIDDEN
            )

        if report.status == 'draft':
            return Response(
                {'error': 'Cannot download a draft report — generate it first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        logger.info("PDF download requested | report=%s user=%s", report.id, request.user.id)

        try:
            exporter = PDFExporter()
            pdf_bytes = exporter.export(report)
        except RuntimeError as exc:
            # likely missing reportlab
            logger.error("PDF export failed | report=%s error=%s", report.id, str(exc))
            return Response(
                {'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as exc:
            logger.exception("Unexpected PDF export error | report=%s error=%s", report.id, str(exc))
            return Response(
                {'error': 'PDF generation failed.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in report.title)
        filename = f"report_{safe_title[:40]}.pdf"

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response