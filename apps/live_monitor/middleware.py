"""ASGI Session Authentication Middleware for WebSocket and HTTP scopes."""
import http.cookies
from urllib.parse import parse_qs
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY
import structlog

logger = structlog.get_logger(__name__)


@sync_to_async
def get_user_from_session_key(session_key: str):
    """Resolve Django User from database session key."""
    if not session_key:
        return AnonymousUser()
    try:
        session = SessionStore(session_key=session_key)
        user_id = session.get(SESSION_KEY)
        if not user_id:
            return AnonymousUser()
        User = get_user_model()
        user = User.objects.filter(pk=user_id, is_active=True).first()
        return user or AnonymousUser()
    except Exception as e:
        logger.warning("session_auth_resolution_error", error=str(e))
        return AnonymousUser()


class SessionAuthMiddleware:
    """
    ASGI middleware resolving authenticated Django user from session cookies in scope headers.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if "user" not in scope or not scope["user"] or isinstance(scope["user"], AnonymousUser):
            # Extract Cookie header from scope
            headers = dict(scope.get("headers", []))
            cookie_header = headers.get(b"cookie", b"").decode("latin1")
            
            session_key = None
            if cookie_header:
                cookie = http.cookies.SimpleCookie()
                try:
                    cookie.load(cookie_header)
                    session_cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
                    if session_cookie_name in cookie:
                        session_key = cookie[session_cookie_name].value
                except Exception:
                    pass

            if session_key:
                scope["user"] = await get_user_from_session_key(session_key)
            else:
                scope["user"] = AnonymousUser()

        return await self.app(scope, receive, send)
