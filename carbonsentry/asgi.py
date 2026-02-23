import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'carbonsentry.settings')

# initialize Django before importing anything that touches models
django_asgi_app = get_asgi_application()

from communication.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    # all regular HTTP requests go through Django as usual
    'http': django_asgi_app,

    # WebSocket connections go through Channels
    # AllowedHostsOriginValidator blocks connections from unknown origins
    'websocket': AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})