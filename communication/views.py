import logging

from django.db.models import Count, Max, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from .models import ChatToken, Message
from .serializers import (
    ChatTokenSerializer,
    ChatVendorListSerializer,
    MessageSerializer,
    SendChatInviteSerializer,
)
from .services import send_chat_invitation

logger = logging.getLogger(__name__)


class ChatVendorListView(APIView):
    # returns the sidebar list — one entry per vendor that has messages
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            from vendors.models import Vendor

            # all vendors in this officer's org
            vendors = Vendor.objects.filter(
                organization=request.user.organization
            ).prefetch_related('messages', 'chat_tokens')

            result = []
            for vendor in vendors:
                msgs = list(vendor.messages.order_by('-created_at'))
                if not msgs:
                    continue  # skip vendors with no messages

                last = msgs[0]
                unread = sum(1 for m in msgs if not m.is_read and m.sender_type == 'vendor')
                has_token = any(t.is_valid for t in vendor.chat_tokens.all())

                result.append({
                    'vendor_id': str(vendor.id),
                    'vendor_name': vendor.name,
                    'last_message': last.content[:80],
                    'last_message_at': last.created_at,
                    'unread_count': unread,
                    'has_active_token': has_token,
                })

            # sort by most recent message first
            result.sort(key=lambda x: x['last_message_at'], reverse=True)

            serializer = ChatVendorListSerializer(result, many=True)
            return Response(serializer.data)

        except Exception as exc:
            logger.exception("ChatVendorListView.get error: %s", str(exc))
            return Response(
                {'error': 'Failed to load chats'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VendorMessagesView(APIView):
    # fetches message history for a specific vendor — used on page load
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        try:
            from vendors.models import Vendor

            vendor = Vendor.objects.filter(
                id=vendor_id,
                organization=request.user.organization
            ).first()

            if not vendor:
                return Response({'error': 'Vendor not found'}, status=status.HTTP_404_NOT_FOUND)

            messages = Message.objects.filter(vendor=vendor).select_related('sender')

            # mark vendor messages as read
            Message.objects.filter(
                vendor=vendor,
                sender_type='vendor',
                is_read=False
            ).update(is_read=True)

            serializer = MessageSerializer(messages, many=True)
            return Response(serializer.data)

        except Exception as exc:
            logger.exception("VendorMessagesView.get error | vendor=%s %s", vendor_id, str(exc))
            return Response({'error': 'Failed to load messages'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SendChatInviteView(APIView):
    # officer sends a chat invitation email to the vendor
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role not in ('officer', 'admin'):
            return Response({'error': 'Only officers and admins can send chat invitations.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = SendChatInviteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        try:
            from vendors.models import Vendor

            vendor = Vendor.objects.filter(
                id=data['vendor_id'],
                organization=request.user.organization
            ).first()

            if not vendor:
                return Response({'error': 'Vendor not found'}, status=status.HTTP_404_NOT_FOUND)

            # use provided email or fall back to vendor's contact email
            target_email = data.get('email') or vendor.contact_email
            if not target_email:
                return Response(
                    {'error': 'No email address available for this vendor.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # create the token
            chat_token = ChatToken.objects.create(
                vendor=vendor,
                created_by=request.user,
                sent_to_email=target_email,
            )

            # send the email
            sent = send_chat_invitation(chat_token)
            if not sent:
                logger.error(
                    "SendChatInviteView: email failed | vendor=%s token=%s",
                    vendor.id, chat_token.token
                )

            logger.info(
                "Chat invite sent | officer=%s vendor=%s to=%s",
                request.user.id, vendor.id, target_email
            )

            token_serializer = ChatTokenSerializer(chat_token)
            return Response(
                {
                    'message': f'Chat invitation sent to {target_email}',
                    'token': token_serializer.data,
                    'email_sent': sent,
                },
                status=status.HTTP_201_CREATED
            )

        except Exception as exc:
            logger.exception("SendChatInviteView.post error: %s", str(exc))
            return Response({'error': 'Failed to send invitation'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RevokeChatTokenView(APIView):
    # officer can invalidate a token if they think the link was compromised
    permission_classes = [IsAuthenticated]

    def post(self, request, token_id):
        try:
            token = ChatToken.objects.get(
                id=token_id,
                vendor__organization=request.user.organization
            )
            token.is_revoked = True
            token.save(update_fields=['is_revoked'])

            logger.info(
                "Chat token revoked | token=%s by=%s", token_id, request.user.id
            )
            return Response({'message': 'Token revoked successfully'})

        except ChatToken.DoesNotExist:
            return Response({'error': 'Token not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            logger.exception("RevokeChatTokenView.post error: %s", str(exc))
            return Response({'error': 'Failed to revoke token'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VendorChatTokenValidateView(APIView):
    # public endpoint — vendor's browser calls this to validate the token before opening WS
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            chat_token = ChatToken.objects.select_related('vendor').get(token=token)

            if not chat_token.is_valid:
                reason = 'revoked' if chat_token.is_revoked else 'expired'
                logger.info("VendorChatTokenValidateView: token %s | token=%s", reason, token)
                return Response(
                    {'valid': False, 'reason': reason},
                    status=status.HTTP_200_OK
                )

            return Response({
                'valid': True,
                'vendor_id': str(chat_token.vendor.id),
                'vendor_name': chat_token.vendor.name,
                'expires_at': chat_token.expires_at.isoformat(),
            })

        except ChatToken.DoesNotExist:
            return Response(
                {'valid': False, 'reason': 'not_found'},
                status=status.HTTP_200_OK
            )
        except Exception as exc:
            logger.exception("VendorChatTokenValidateView error: %s", str(exc))
            return Response({'valid': False, 'reason': 'error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)