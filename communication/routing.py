from django.urls import re_path
from .consumers import ChatConsumer

websocket_urlpatterns = [
    # vendor_id scopes the WebSocket to a single vendor's chat room
    re_path(r'ws/chat/(?P<vendor_id>[0-9a-f-]+)/$', ChatConsumer.as_asgi()),
]