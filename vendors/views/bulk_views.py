import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from vendors.serializers.bulk_upload_serializers import VendorBulkUploadSerializer
from vendors.services.csv_parser import parse_csv, CsvParsingError

from vendors.models import VendorBulkUpload

logger = logging.getLogger("vendors.bulk_upload_view")


class VendorBulkUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VendorBulkUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        csv_file = serializer.validated_data["csv_file"]
        send_emails = serializer.validated_data.get("send_emails", False)

        user = request.user
        organization = user.organization

        total_rows = 0
        success_count = 0
        failure_count = 0
        error_summary = []
        created_vendors = []  

        bulk_upload = VendorBulkUpload.objects.create(
            organization=organization,
            uploaded_by=user,
            total_rows=0,
            success_count=0,
            failure_count=0,
        )

        logger.info(
            "Vendor bulk upload started",
            extra={
                "bulk_upload_id": str(bulk_upload.id),
                "user": user.email,
                "organization": organization.name,
            },
        )

        try:
            rows = parse_csv(csv_file)
        except CsvParsingError as exc:
            logger.warning(
                "CSV parsing failed",
                extra={"error": str(exc)},
            )
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for row_number, row in rows:
            total_rows += 1

            try:
                
                from vendors.services.industry_mapper import get_or_create_industry
                industry = get_or_create_industry(row.get("industry", ""))

                from vendors.services.vendor_creator import VendorCreatorService,VendorCreationError

                vendor = VendorCreatorService.create_vendor(
                    organization=organization,
                    data=row,
                    industry=industry,
                )
                success_count += 1
                created_vendors.append(vendor)  

            except ValueError as exc:
                
                failure_count += 1
                error_summary.append(
                    {
                        "row": row_number,
                        "error": str(exc),
                        "data": row,
                    }
                )

            except VendorCreationError as exc:
                failure_count += 1
                error_summary.append(
                    {
                        "row": row_number,
                        "error": str(exc),
                        "data": row,
                    }
                )

            except Exception:
                failure_count += 1
                logger.exception(
                    "Unhandled error during vendor bulk upload",
                    extra={"row": row_number},
                )
                error_summary.append(
                    {
                        "row": row_number,
                        "error": "Unexpected error",
                        "data": row,
                    }
                )

        bulk_upload.total_rows = total_rows
        bulk_upload.success_count = success_count
        bulk_upload.failure_count = failure_count
        bulk_upload.error_summary = error_summary
        bulk_upload.save()

       
        if send_emails and created_vendors:
            try:
                from vendors.services.email_campaign_service import EmailCampaignService
                EmailCampaignService.run(organization, created_vendors)
                logger.info(
                    "Email campaign executed",
                    extra={
                        "bulk_upload_id": str(bulk_upload.id),
                        "vendor_count": len(created_vendors),
                    },
                )
            except Exception:
                logger.exception(
                    "Email campaign failed",
                    extra={"bulk_upload_id": str(bulk_upload.id)},
                )

        logger.info(
            "Vendor bulk upload completed",
            extra={
                "bulk_upload_id": str(bulk_upload.id),
                "total": total_rows,
                "success": success_count,
                "failure": failure_count,
            },
        )

        response_data = {
            "bulk_upload_id": str(bulk_upload.id),
            "total_rows": total_rows,
            "success_count": success_count,
            "failure_count": failure_count,
            "error_summary": error_summary,
        }

        return Response(response_data, status=status.HTTP_200_OK)