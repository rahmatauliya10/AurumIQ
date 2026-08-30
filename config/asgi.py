"""ASGI config for XAUT Signal Intelligence with native WebSocket transport."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_http_app = get_asgi_application()


async def application(scope, receive, send):
    """
    Master ASGI 3.0 protocol router handling HTTP and WebSocket transports.
    """
    scope_type = scope.get("type")

    if scope_type == "http":
        await django_http_app(scope, receive, send)

    elif scope_type == "websocket":
        from apps.live_monitor.consumers import LiveMonitorAsyncWebsocketConsumer
        consumer = LiveMonitorAsyncWebsocketConsumer(scope, receive, send)
        await consumer()

    elif scope_type == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                break
    else:
        await django_http_app(scope, receive, send)

