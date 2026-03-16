from django.urls import path

from .views import (
    ChatVendorListView,
    RevokeChatTokenView,
    SendChatInviteView,
    VendorChatTokenValidateView,
    VendorMessagesView,
    VerifyOtpView,
)

urlpatterns = [
    path('chats/', ChatVendorListView.as_view(), name='chat-vendor-list'),
    path('chats/<uuid:vendor_id>/messages/', VendorMessagesView.as_view(), name='vendor-messages'),
    path('invite/', SendChatInviteView.as_view(), name='send-chat-invite'),
    path('tokens/<uuid:token_id>/revoke/', RevokeChatTokenView.as_view(), name='revoke-token'),
    path('validate/<uuid:token>/', VendorChatTokenValidateView.as_view(), name='validate-token'),
    path('verify-otp/', VerifyOtpView.as_view(), name='verify-otp'),
]