"""Integration tests for real ASGI WebSocket Session Authentication and Cross-Process Event Bus."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY

from config.asgi import application
from apps.live_monitor.consumers import LiveEventBroadcaster


@pytest.mark.django_db(transaction=True)
def test_real_asgi_websocket_session_auth_and_cross_process_delivery():
    """
    End-to-end verification of real ASGI WebSocket session cookie resolution,
    unauthorized rejection, and live event frame delivery.
    """
    # 1. Create active operator user
    user = User.objects.create_user(username="wsoperator", password="password123")

    # 2. Create authenticated Django session in database
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    session_key = session.session_key
    assert session_key is not None

    async def _run_e2e_flow():
        # --- Case A: Anonymous connection without cookie header -> Rejected with 4401 ---
        sent_anon = []
        async def mock_send_anon(msg):
            sent_anon.append(msg)

        async def mock_recv_anon():
            return {"type": "websocket.disconnect"}

        anon_scope = {
            "type": "websocket",
            "headers": [],
            "path": "/ws/live/",
        }

        await application(anon_scope, mock_recv_anon, mock_send_anon)
        assert len(sent_anon) == 1
        assert sent_anon[0]["type"] == "websocket.close"
        assert sent_anon[0]["code"] == 4401

        # --- Case B: Invalid/Tampered session cookie -> Rejected with 4401 ---
        sent_invalid = []
        async def mock_send_invalid(msg):
            sent_invalid.append(msg)

        async def mock_recv_invalid():
            return {"type": "websocket.disconnect"}

        invalid_scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", b"sessionid=nonexistent_tampered_session_key_9999"),
            ],
            "path": "/ws/live/",
        }

        await application(invalid_scope, mock_recv_invalid, mock_send_invalid)
        assert len(sent_invalid) == 1
        assert sent_invalid[0]["type"] == "websocket.close"
        assert sent_invalid[0]["code"] == 4401

        # --- Case C: Valid Session Cookie -> Authenticated, Accepted, and Frames Delivered ---
        sent_auth = []
        recv_queue = asyncio.Queue()

        async def mock_send_auth(msg):
            sent_auth.append(msg)

        async def mock_recv_auth():
            return await recv_queue.get()

        auth_scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", f"sessionid={session_key}".encode("latin1")),
            ],
            "path": "/ws/live/",
        }

        consumer_task = asyncio.create_task(
            application(auth_scope, mock_recv_auth, mock_send_auth)
        )
        await asyncio.sleep(0.02)

        # Confirm accept frame
        assert any(m.get("type") == "websocket.accept" for m in sent_auth)

        # Producer triggers cross-process broadcast
        quote_payload = LiveEventBroadcaster.format_quote_event(
            instrument="XAUT/USDT",
            bid=Decimal("2525.00"),
            ask=Decimal("2525.50"),
            spread=Decimal("0.50"),
            spread_pct=Decimal("0.0197"),
            source_timestamp=datetime.now(timezone.utc),
            sequence_number=305,
            entry_zone_status="INSIDE_ZONE",
        )
        LiveEventBroadcaster.broadcast(quote_payload)
        await asyncio.sleep(0.02)

        # Verify broadcast event arrived at WebSocket client
        quote_frames = [
            json.loads(m["text"]) for m in sent_auth
            if m.get("type") == "websocket.send" and "text" in m and "quote_update" in m["text"]
        ]
        assert len(quote_frames) >= 1
        assert quote_frames[0]["event_type"] == "quote_update"
        assert quote_frames[0]["data"]["ask"] == "2525.50"
        assert quote_frames[0]["sequence_number"] == 305

        # Disconnect clean
        await recv_queue.put({"type": "websocket.disconnect"})
        await consumer_task

    asyncio.run(_run_e2e_flow())
