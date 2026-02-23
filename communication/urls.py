from django.urls import path
from .views import (
    ChatVendorListView,
    VendorMessagesView,
    SendChatInviteView,
    RevokeChatTokenView,
    VendorChatTokenValidateView,
)

urlpatterns = [
    # officer sidebar — list of vendors with messages
    path('chats/', ChatVendorListView.as_view(), name='chat-vendor-list'),

    # message history for a specific vendor
    path('chats/<uuid:vendor_id>/messages/', VendorMessagesView.as_view(), name='vendor-messages'),

    # send a chat invitation email to a vendor
    path('invite/', SendChatInviteView.as_view(), name='send-chat-invite'),

    # revoke a specific token
    path('tokens/<uuid:token_id>/revoke/', RevokeChatTokenView.as_view(), name='revoke-token'),

    # public — vendor validates their token before connecting via WS
    path('validate/<uuid:token>/', VendorChatTokenValidateView.as_view(), name='validate-token'),
]