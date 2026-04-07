import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from accounts.permissions import IsAdmin, IsOfficer
from vendors.serializers.bulk_upload_serializers import VendorBulkUploadSerializer
from vendors.services.csv_parser import parse_csv, CsvParsingError
from vendors.models import VendorBulkUpload
from audit_logs.services import log_action

logger = logging.getLogger("vendors.bulk_upload_view")


class VendorBulkUploadView(APIView):
    permission_classes = [IsAuthenticated, IsOfficer | IsAdmin]

    def post(self, request):
        serializer = VendorBulkUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        csv_file    = serializer.validated_data["csv_file"]
        send_emails = serializer.validated_data.get("send_emails", False)
        user        = request.user
        org         = user.organization

        bulk_upload = VendorBulkUpload.objects.create(
            organization=org,
            uploaded_by=user,
            total_rows=0,
            success_count=0,
            failure_count=0,
        )

        logger.info(
            "bulk_upload.started | id=%s user=%s org=%s send_emails=%s",
            bulk_upload.id, user.email, org.name, send_emails,
        )

        try:
            rows = list(parse_csv(csv_file))
        except CsvParsingError as exc:
            logger.warning("bulk_upload.csv_parse_failed | error=%s", exc)
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        from vendors.services.industry_mapper import get_or_create_industry
        from vendors.services.vendor_creator import VendorCreatorService, VendorCreationError

        total_rows    = 0
        success_count = 0
        failure_count = 0
        error_summary = []
        created_ids   = []
        created_vendors = []

        for row_number, row in rows:
            total_rows += 1
            try:
                industry = get_or_create_industry(row.get("industry", ""))
                vendor   = VendorCreatorService.create_vendor(
                    organization=org,
                    data=row,
                    industry=industry,
                    send_emails=False,  # we batch-send below after all vendors created
                )
                success_count += 1
                created_ids.append(str(vendor.id))
                created_vendors.append(vendor)

                log_action(
                    action='vendor_created',
                    entity_type='Vendor',
                    entity_id=str(vendor.id),
                    organization=org,
                    actor=user,
                    details={'name': vendor.name, 'source': 'bulk_upload'},
                )

            except (VendorCreationError, ValueError) as exc:
                failure_count += 1
                error_summary.append({"row": row_number, "error": str(exc), "data": row})
            except Exception:
                failure_count += 1
                logger.exception("bulk_upload.row_error | row=%s", row_number)
                error_summary.append({"row": row_number, "error": "Unexpected error", "data": row})

        bulk_upload.total_rows    = total_rows
        bulk_upload.success_count = success_count
        bulk_upload.failure_count = failure_count
        bulk_upload.error_summary = error_summary
        bulk_upload.save()

        # Queue bulk email via Celery — avoids Mailtrap rate limits
        if send_emails and created_vendors:
            from vendors.services.email_tasks import send_bulk_vendor_emails_task
            send_bulk_vendor_emails_task.delay({
                'organization_id': str(org.id),
                'vendor_ids': created_ids,
            })
            logger.info(
                "bulk_upload.emails_queued | count=%d org=%s",
                len(created_ids), org.id,
            )

        log_action(
            action='vendor_created',
            entity_type='VendorBulkUpload',
            entity_id=str(bulk_upload.id),
            organization=org,
            actor=user,
            details={
                'total': total_rows,
                'success': success_count,
                'failed': failure_count,
                'emails_queued': send_emails and len(created_ids) > 0,
            },
        )

        logger.info(
            "bulk_upload.completed | id=%s total=%s success=%s failure=%s",
            bulk_upload.id, total_rows, success_count, failure_count,
        )

        return Response({
            "bulk_upload_id":  str(bulk_upload.id),
            "total_rows":      total_rows,
            "success_count":   success_count,
            "failure_count":   failure_count,
            "error_summary":   error_summary,
            "vendor_ids":      created_ids,
            "emails_queued":   send_emails and len(created_ids) > 0,
        })