from django.urls import path
from .views import VendorMessageListView, VendorChatListView

urlpatterns = [
    path('chats/', VendorChatListView.as_view(), name='chat-list'),
    path('vendors/<uuid:vendor_id>/messages/', VendorMessageListView.as_view(), name='vendor-messages'),
]