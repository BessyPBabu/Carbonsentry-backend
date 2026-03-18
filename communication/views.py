import logging
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from .models import ChatToken, Message
from .serializers import (
    ChatTokenSerializer,
    ChatVendorListSerializer,
    MessageSerializer,
    SendChatInviteSerializer,
    VerifyOtpSerializer,
)
from .services import send_chat_invitation

logger = logging.getLogger(__name__)


class MessagePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class ChatVendorListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            from vendors.models import Vendor

            vendors = Vendor.objects.filter(
                organization=request.user.organization
            ).prefetch_related("messages", "chat_tokens")

            result = []
            for vendor in vendors:
                msgs = list(vendor.messages.order_by("-created_at"))
                if not msgs:
                    continue

                last = msgs[0]
                unread = sum(
                    1 for m in msgs
                    if not m.is_read and m.sender_type == "vendor"
                )
                has_active = any(t.is_valid for t in vendor.chat_tokens.all())

                result.append({
                    "vendor_id":       str(vendor.id),
                    "vendor_name":     vendor.name,
                    "last_message":    last.content[:80],
                    "last_message_at": last.created_at,
                    "unread_count":    unread,
                    "has_active_token": has_active,
                })

            result.sort(key=lambda x: x["last_message_at"], reverse=True)

            paginator = MessagePagination()
            page_size = paginator.page_size
            try:
                page = int(request.query_params.get("page", 1))
            except (ValueError, TypeError):
                page = 1

            start = (page - 1) * page_size
            end   = start + page_size
            page_data = result[start:end]

            return Response({
                "count":       len(result),
                "total_pages": max(1, -(-len(result) // page_size)),
                "current_page": page,
                "results":     ChatVendorListSerializer(page_data, many=True).data,
            })

        except Exception as exc:
            logger.exception("ChatVendorListView.get | error=%s", exc)
            return Response(
                {"error": "Failed to load chats"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VendorMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_id):
        try:
            from vendors.models import Vendor

            vendor = Vendor.objects.filter(
                id=vendor_id, organization=request.user.organization
            ).first()

            if not vendor:
                return Response(
                    {"error": "Vendor not found"}, status=status.HTTP_404_NOT_FOUND
                )

            messages = (
                Message.objects
                .filter(vendor=vendor)
                .select_related("sender")
                .order_by("created_at")
            )

            Message.objects.filter(
                vendor=vendor, sender_type="vendor", is_read=False
            ).update(is_read=True)

            paginator = MessagePagination()
            page = paginator.paginate_queryset(messages, request)
            serializer = MessageSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        except Exception as exc:
            logger.exception(
                "VendorMessagesView.get | vendor=%s error=%s", vendor_id, exc
            )
            return Response(
                {"error": "Failed to load messages"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SendChatInviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role not in ("officer", "admin"):
            return Response(
                {"error": "Only officers and admins can send chat invitations."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SendChatInviteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        try:
            from vendors.models import Vendor

            vendor = Vendor.objects.filter(
                id=data["vendor_id"], organization=request.user.organization
            ).first()

            if not vendor:
                return Response(
                    {"error": "Vendor not found"}, status=status.HTTP_404_NOT_FOUND
                )

            target_email = data.get("email") or vendor.contact_email
            if not target_email:
                return Response(
                    {"error": "No email address available for this vendor."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            chat_token = ChatToken.objects.create(
                vendor=vendor,
                created_by=request.user,
                sent_to_email=target_email,
            )

            sent = send_chat_invitation(chat_token)
            if not sent:
                logger.error(
                    "SendChatInviteView: email failed | vendor=%s", vendor.id
                )

            logger.info(
                "chat_invite.created | officer=%s vendor=%s to=%s",
                request.user.id, vendor.id, target_email,
            )

            return Response(
                {
                    "message":    f"Chat invitation sent to {target_email}",
                    "token":      ChatTokenSerializer(chat_token).data,
                    "email_sent": sent,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            logger.exception("SendChatInviteView.post | error=%s", exc)
            return Response(
                {"error": "Failed to send invitation"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RevokeChatTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token_id):
        try:
            token = ChatToken.objects.get(
                id=token_id,
                vendor__organization=request.user.organization,
            )
            token.is_revoked = True
            token.save(update_fields=["is_revoked"])

            logger.info(
                "chat_token.revoked | token=%s by=%s", token_id, request.user.id
            )
            return Response({"message": "Token revoked successfully"})

        except ChatToken.DoesNotExist:
            return Response(
                {"error": "Token not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as exc:
            logger.exception("RevokeChatTokenView.post | error=%s", exc)
            return Response(
                {"error": "Failed to revoke token"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VendorChatTokenValidateView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            chat_token = ChatToken.objects.select_related("vendor").get(token=token)

            if not chat_token.is_valid:
                reason = "revoked" if chat_token.is_revoked else "expired"
                return Response({"valid": False, "reason": reason})

            return Response({
                "valid":        True,
                "otp_required": not chat_token.otp_verified,
                "vendor_id":    str(chat_token.vendor.id),
                "vendor_name":  chat_token.vendor.name,
                "expires_at":   chat_token.expires_at.isoformat(),
            })

        except ChatToken.DoesNotExist:
            return Response({"valid": False, "reason": "not_found"})
        except Exception as exc:
            logger.exception("VendorChatTokenValidateView | error=%s", exc)
            return Response(
                {"valid": False, "reason": "error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyOtpView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token_value = serializer.validated_data["token"]
        otp_code    = serializer.validated_data["otp_code"].strip()

        try:
            chat_token = ChatToken.objects.select_related("vendor").get(
                token=token_value
            )

            if not chat_token.is_valid:
                return Response(
                    {"success": False, "reason": "This chat link has expired or been revoked."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if chat_token.otp_verified:
                return Response({
                    "success":     True,
                    "vendor_id":   str(chat_token.vendor.id),
                    "vendor_name": chat_token.vendor.name,
                })

            if chat_token.otp_code != otp_code:
                logger.warning("VerifyOtpView: wrong OTP | token=%s", token_value)
                return Response(
                    {"success": False, "reason": "Incorrect verification code. Please check your email."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            chat_token.otp_verified = True
            chat_token.save(update_fields=["otp_verified"])

            logger.info("otp.verified | vendor=%s", chat_token.vendor.id)

            return Response({
                "success":     True,
                "vendor_id":   str(chat_token.vendor.id),
                "vendor_name": chat_token.vendor.name,
            })

        except ChatToken.DoesNotExist:
            return Response(
                {"success": False, "reason": "Invalid chat link."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as exc:
            logger.exception("VerifyOtpView | error=%s", exc)
            return Response(
                {"success": False, "reason": "Verification failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )