import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from vendors.models import Vendor
from .models import VendorMessage
from .serializers import VendorMessageSerializer, SendMessageSerializer
from audit_logs.services import log_action

logger = logging.getLogger(__name__)


class VendorMessageListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        try:
            vendor = self._get_vendor(request, vendor_id)
        except Vendor.DoesNotExist:
            return Response({'error': 'Vendor not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("get: failed to fetch vendor %s", vendor_id)
            return Response({'error': 'Failed to fetch vendor'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            messages = VendorMessage.objects.filter(
                vendor=vendor,
                organization=request.user.organization,
            ).select_related('sender')

            if request.user.role == 'viewer':
                messages = messages.filter(direction__in=['vendor_facing', 'vendor_reply'])

            return Response(VendorMessageSerializer(messages, many=True).data)

        except Exception:
            logger.exception("get: failed to fetch messages for vendor %s", vendor_id)
            return Response({'error': 'Failed to fetch messages'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request, vendor_id):
        if request.user.role == 'viewer':
            return Response({'error': 'Viewers cannot send messages'}, status=status.HTTP_403_FORBIDDEN)

        try:
            vendor = self._get_vendor(request, vendor_id)
        except Vendor.DoesNotExist:
            return Response({'error': 'Vendor not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("post: failed to fetch vendor %s", vendor_id)
            return Response({'error': 'Failed to fetch vendor'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer = SendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        direction = serializer.validated_data['direction']
        message_text = serializer.validated_data['message']

        try:
            msg = VendorMessage.objects.create(
                vendor=vendor,
                organization=request.user.organization,
                sender=request.user,
                message=message_text,
                direction=direction,
            )
        except Exception:
            logger.exception("post: failed to save message for vendor %s", vendor_id)
            return Response({'error': 'Failed to save message'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if direction == 'vendor_facing':
            self._send_email(msg, vendor)

        try:
            log_action(
                request=request,
                action='message_sent',
                entity_type='Vendor',
                entity_id=str(vendor.id),
                details={'direction': direction, 'preview': message_text[:100]},
            )
        except Exception:
            logger.warning("post: audit log failed for message to vendor %s", vendor_id)

        return Response(VendorMessageSerializer(msg).data, status=status.HTTP_201_CREATED)

    def _get_vendor(self, request, vendor_id):
        return Vendor.objects.get(id=vendor_id, organization=request.user.organization)

    def _send_email(self, msg, vendor):
        try:
            from vendors.services.email_service import EmailService
            EmailService().send_vendor_message(
                vendor=vendor,
                message_text=msg.message,
                sender_name=msg.sender.get_full_name() or msg.sender.email,
            )
            msg.email_sent = True
            msg.save(update_fields=['email_sent'])
            logger.info("_send_email: delivered to vendor %s", vendor.id)
        except Exception as e:
            logger.exception("_send_email: failed for vendor %s", vendor.id)
            msg.email_error = str(e)[:500]
            msg.save(update_fields=['email_error'])


class VendorChatListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            if not hasattr(request.user, 'organization'):
                return Response([])

            vendor_ids = VendorMessage.objects.filter(
                organization=request.user.organization
            ).values_list('vendor_id', flat=True).distinct()

            vendors = Vendor.objects.filter(
                id__in=vendor_ids,
                organization=request.user.organization,
            )

            result = []
            for vendor in vendors:
                last_msg = VendorMessage.objects.filter(
                    vendor=vendor,
                    organization=request.user.organization,
                ).order_by('-created_at').first()

                result.append({
                    'id': str(vendor.id),
                    'name': vendor.name,
                    'industry': str(vendor.industry) if vendor.industry else '',
                    'risk_level': vendor.risk_level or 'unknown',
                    'last_message': last_msg.message[:80] if last_msg else '',
                    'last_message_at': last_msg.created_at.isoformat() if last_msg else None,
                    'last_direction': last_msg.direction if last_msg else None,
                })

            result.sort(key=lambda x: x['last_message_at'] or '', reverse=True)
            return Response(result)

        except Exception:
            logger.exception("VendorChatListView.get: error for user %s", request.user.id)
            return Response({'error': 'Failed to fetch chat list'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)