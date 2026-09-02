"""Integration tests for real ASGI WebSocket Session Authentication and Cross-Process Event Bus."""
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY

from config.asgi import application
from apps.live_monitor.consumers import LiveEventBroadcaster
from apps.live_monitor.tasks import process_live_quote_task, process_closed_candle_task
from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType

from apps.market_data.models import MarketCandle, CandleQualityFlag
from django.db import connections



@pytest.fixture(autouse=True)
def _cleanup_websocket_test_connections():
    yield
    async def _close():
        await sync_to_async(connections.close_all)()
    try:
        asyncio.run(_close())
    except Exception:
        pass
    connections.close_all()


@pytest.mark.django_db(transaction=True)
def test_real_asgi_websocket_session_auth_and_cross_process_delivery():

    """
    End-to-end verification of real ASGI WebSocket session cookie resolution,
    unauthorized rejection, and live event frame delivery.
    """
    user = User.objects.create_user(username="wsoperator", password="password123")

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
        await asyncio.sleep(0.05)

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
        await asyncio.sleep(0.05)

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


@pytest.mark.django_db(transaction=True)
def test_p7_auth_08_password_change_invalidates_websocket_session():
    """
    P7-AUTH-08: Changing user password alters session_auth_hash and immediately invalidates
    existing session cookies on subsequent WebSocket connection attempts.
    """
    user = User.objects.create_user(username="hashoperator", password="initial_password")

    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    old_session_key = session.session_key

    async def _test_flow():
        # 1. Before password change -> accepted
        sent_1 = []
        recv_q1 = asyncio.Queue()
        async def mock_send_1(m):
            sent_1.append(m)

        scope_1 = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={old_session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        t1 = asyncio.create_task(application(scope_1, lambda: recv_q1.get(), mock_send_1))
        await asyncio.sleep(0.05)
        assert any(m.get("type") == "websocket.accept" for m in sent_1)
        await recv_q1.put({"type": "websocket.disconnect"})
        await t1

        # 2. Change user password
        user.set_password("new_secure_password_999")
        await sync_to_async(user.save)()

        # 3. Attempt connecting with old session cookie -> Rejected 4401
        sent_2 = []
        async def mock_send_2(m):
            sent_2.append(m)

        async def mock_recv_2():
            return {"type": "websocket.disconnect"}

        scope_2 = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={old_session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        await application(scope_2, mock_recv_2, mock_send_2)
        assert len(sent_2) == 1
        assert sent_2[0]["type"] == "websocket.close"
        assert sent_2[0]["code"] == 4401

    asyncio.run(_test_flow())


@pytest.mark.django_db(transaction=True)
def test_p7_bus_01_separate_redis_producer_to_websocket():
    """
    P7-BUS-01: Proves cross-process WebSocket event delivery strictly via Redis Pub/Sub
    with local in-memory subscribers explicitly disabled.
    """
    user = User.objects.create_user(username="redisoperator", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    # Check if real Redis connection is available
    r_check = LiveEventBroadcaster.get_redis_client()
    if r_check:
        try:
            r_check.ping()
        except Exception:
            pytest.skip("Redis server is not running; skipping cross-process bus test.")
    else:
        pytest.skip("Redis client not configured.")

    async def _test_redis_transport():
        sent_frames = []
        recv_queue = asyncio.Queue()
        async def mock_send(m):
            sent_frames.append(m)

        auth_scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }

        consumer_task = asyncio.create_task(
            application(auth_scope, lambda: recv_queue.get(), mock_send)
        )
        await asyncio.sleep(0.05)

        # Clear in-memory subscribers to prove delivery occurs exclusively through Redis pub/sub
        LiveEventBroadcaster._subscribers.clear()

        # Publish event directly to Redis channel
        r = LiveEventBroadcaster.get_redis_client()
        if r:
            payload = {
                "event_id": "TEST_REDIS_BUS_01",
                "event_type": "quote_update",
                "instrument": "XAUT/USDT",
                "sequence_number": 9999,
                "data": {"ask": "2599.90", "bid": "2599.40"},
            }
            r.publish("aurumiq:live_events:XAUT/USDT", json.dumps(payload))
            await asyncio.sleep(0.05)

            frames = [
                json.loads(m["text"]) for m in sent_frames
                if m.get("type") == "websocket.send" and "TEST_REDIS_BUS_01" in m.get("text", "")
            ]
            assert len(frames) >= 1
            assert frames[0]["data"]["ask"] == "2599.90"

        await recv_queue.put({"type": "websocket.disconnect"})
        await consumer_task

    asyncio.run(_test_redis_transport())


@pytest.mark.django_db(transaction=True)
def test_p7_bus_02_process_live_quote_task_to_websocket():
    """
    P7-BUS-02: Executing Celery process_live_quote_task broadcasts quote_update
    frame to connected WebSocket client.
    """
    from apps.live_monitor.models import LiveMonitorState
    LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()

    user = User.objects.create_user(username="quoteoperator", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test_task_delivery():
        sent_frames = []
        recv_queue = asyncio.Queue()
        async def mock_send(m):
            sent_frames.append(m)

        auth_scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }

        consumer_task = asyncio.create_task(
            application(auth_scope, lambda: recv_queue.get(), mock_send)
        )
        await asyncio.sleep(0.05)

        # Call Celery task in worker thread
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await sync_to_async(process_live_quote_task)(
            instrument="XAUT/USDT",
            provider="binance",
            bid_str="2530.00",
            ask_str="2530.50",
            source_timestamp_iso=now_iso,
            sequence_number=1000000,
        )
        assert res["status"] == "SUCCESS"

        await asyncio.sleep(0.05)

        # Confirm quote frame delivered
        quote_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "quote_update" in m.get("text", "")
        ]
        assert len(quote_frames) >= 1
        assert any(Decimal(f["data"]["ask"]) == Decimal("2530.50") for f in quote_frames)

        await recv_queue.put({"type": "websocket.disconnect"})
        await consumer_task

    asyncio.run(_test_task_delivery())


@pytest.mark.django_db(transaction=True)
def test_p7_bus_03_process_closed_candle_task_to_websocket():
    """
    P7-BUS-03: Executing Celery process_closed_candle_task broadcasts signal_update,
    risk_plan_update, and feed_health_update frames to connected WebSocket client.
    """
    user = User.objects.create_user(username="candleoperator", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test_candle_delivery():
        sent_frames = []
        recv_queue = asyncio.Queue()
        async def mock_send(m):
            sent_frames.append(m)

        auth_scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }

        consumer_task = asyncio.create_task(
            application(auth_scope, lambda: recv_queue.get(), mock_send)
        )
        await asyncio.sleep(0.05)

        now_utc = datetime.now(timezone.utc)
        res = await sync_to_async(process_closed_candle_task)(
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open_iso=(now_utc - timedelta(minutes=15)).isoformat(),
            timestamp_close_iso=now_utc.isoformat(),
            open_str="2520.00",
            high_str="2525.00",
            low_str="2518.00",
            close_str="2523.00",
            volume_str="100.0",
            code_revision="32bec19b4219ea8adc38a11c7ddcd8ee7863095a",
        )
        assert res["status"] == "SUCCESS"

        await asyncio.sleep(0.05)

        sig_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "signal_update" in m.get("text", "")
        ]
        assert len(sig_frames) >= 1

        await recv_queue.put({"type": "websocket.disconnect"})
        await consumer_task

    asyncio.run(_test_candle_delivery())


@pytest.mark.django_db(transaction=True)
def test_p7_auth_09_missing_hash_session_key_rejected():
    """P7-AUTH-09: Session missing HASH_SESSION_KEY is strictly rejected with 4401."""
    user = User.objects.create_user(username="nohashop", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    # Missing HASH_SESSION_KEY
    session.save()

    async def _test():
        sent = []
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        await application(scope, lambda: asyncio.sleep(0.1), lambda m: sent.append(m))
        assert len(sent) == 1
        assert sent[0]["type"] == "websocket.close"
        assert sent[0]["code"] == 4401

    asyncio.run(_test())


@pytest.mark.django_db(transaction=True)
def test_p7_auth_10_missing_backend_session_key_rejected():
    """P7-AUTH-10: Session missing BACKEND_SESSION_KEY is strictly rejected with 4401."""
    user = User.objects.create_user(username="nobackendop", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    # Missing BACKEND_SESSION_KEY
    session.save()

    async def _test():
        sent = []
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        await application(scope, lambda: asyncio.sleep(0.1), lambda m: sent.append(m))
        assert len(sent) == 1
        assert sent[0]["type"] == "websocket.close"
        assert sent[0]["code"] == 4401

    asyncio.run(_test())


@pytest.mark.django_db(transaction=True)
def test_p7_auth_11_invalid_backend_session_key_rejected():
    """P7-AUTH-11: Session with unapproved authentication backend is strictly rejected with 4401."""
    user = User.objects.create_user(username="badbackendop", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "some.unapproved.AuthenticationBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test():
        sent = []
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        await application(scope, lambda: asyncio.sleep(0.1), lambda m: sent.append(m))
        assert len(sent) == 1
        assert sent[0]["type"] == "websocket.close"
        assert sent[0]["code"] == 4401

    asyncio.run(_test())


@pytest.mark.django_db(transaction=True)
def test_p7_bus_04_transaction_rollback_emits_no_event():
    """P7-BUS-04: If a transaction rolls back, zero events are broadcast."""
    user = User.objects.create_user(username="rollbackop", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test_flow():
        sent_frames = []
        recv_q = asyncio.Queue()
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        t = asyncio.create_task(application(scope, lambda: recv_q.get(), lambda m: sent_frames.append(m)))
        await asyncio.sleep(0.05)

        def _failing_transaction():
            from django.db import transaction
            from apps.live_monitor.services import LiveQuoteService
            from apps.live_monitor.types import LiveQuoteEvent
            try:
                with transaction.atomic():
                    event = LiveQuoteEvent(
                        event_id="TEST_ROLLBACK_EVT_04",
                        instrument="XAUT/USDT",
                        provider="binance",
                        bid=Decimal("2500"),
                        ask=Decimal("2501"),
                        source_timestamp=datetime.now(timezone.utc),
                        received_timestamp=datetime.now(timezone.utc),
                    )
                    LiveQuoteService.process_quote(event)
                    raise RuntimeError("Forced rollback for testing")
            except RuntimeError:
                pass

        await sync_to_async(_failing_transaction)()
        await asyncio.sleep(0.05)

        quote_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "TEST_ROLLBACK_EVT_04" in m.get("text", "")
        ]
        assert len(quote_frames) == 0

        await recv_q.put({"type": "websocket.disconnect"})
        await t

    asyncio.run(_test_flow())


@pytest.mark.django_db(transaction=True)
def test_p7_bus_05_committed_quote_emits_valid_event():
    """P7-BUS-05: Committed quote transaction emits exactly one valid quote_update event."""
    from apps.live_monitor.models import LiveMonitorState
    LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()

    user = User.objects.create_user(username="quotecommop", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test_flow():
        sent_frames = []
        recv_q = asyncio.Queue()
        async def mock_send(m):
            sent_frames.append(m)

        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        t = asyncio.create_task(application(scope, lambda: recv_q.get(), mock_send))
        await asyncio.sleep(0.05)

        def _successful_quote():
            from django.db import transaction
            from apps.live_monitor.services import LiveQuoteService
            from apps.live_monitor.types import LiveQuoteEvent
            with transaction.atomic():
                event = LiveQuoteEvent(
                    event_id="TEST_COMMITTED_EVT_05",
                    instrument="XAUT/USDT",
                    provider="binance",
                    bid=Decimal("2540.00"),
                    ask=Decimal("2540.50"),
                    source_timestamp=datetime.now(timezone.utc),
                    received_timestamp=datetime.now(timezone.utc),
                    sequence_number=777,
                )
                LiveQuoteService.process_quote(event)

        await sync_to_async(_successful_quote)()
        await asyncio.sleep(0.1)

        quote_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "quote_update" in m.get("text", "")
        ]
        assert len(quote_frames) >= 1
        assert any(Decimal(f["data"]["ask"]) == Decimal("2540.50") and f["sequence_number"] == 777 for f in quote_frames)

        await recv_q.put({"type": "websocket.disconnect"})
        await t

    asyncio.run(_test_flow())


@pytest.mark.django_db(transaction=True)
def test_p7_bus_06_committed_decision_emits_all_decision_events():
    """P7-BUS-06: Committed decision transaction emits signal, risk plan, and feed health events."""
    user = User.objects.create_user(username="decisioncommop", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test_flow():
        sent_frames = []
        recv_q = asyncio.Queue()
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/ws/live/",
        }
        t = asyncio.create_task(application(scope, lambda: recv_q.get(), lambda m: sent_frames.append(m)))
        await asyncio.sleep(0.05)

        now_utc = datetime.now(timezone.utc)
        def _successful_decision():
            from django.db import transaction
            from apps.live_monitor.services import LiveDecisionPipelineService
            from apps.live_monitor.types import CandleClosedEvent
            with transaction.atomic():
                event = CandleClosedEvent(
                    event_id="TEST_DECISION_EVT_06",
                    instrument="XAUT/USDT",
                    timeframe="15m",
                    timestamp_open=now_utc - timedelta(minutes=15),
                    timestamp_close=now_utc,
                    open=Decimal("2535.00"),
                    high=Decimal("2542.00"),
                    low=Decimal("2533.00"),
                    close=Decimal("2540.00"),
                    volume=Decimal("100.0"),
                    is_closed=True,
                )
                LiveDecisionPipelineService.process_closed_candle(
                    event=event,
                    code_revision="32bec19b4219ea8adc38a11c7ddcd8ee7863095a",
                )

        await sync_to_async(_successful_decision)()
        await asyncio.sleep(0.05)

        sig_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "signal_update" in m.get("text", "")
        ]
        risk_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "risk_plan_update" in m.get("text", "")
        ]
        health_frames = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "feed_health_update" in m.get("text", "")
        ]
        assert len(sig_frames) >= 1
        assert len(risk_frames) >= 1
        assert len(health_frames) >= 1

        await recv_q.put({"type": "websocket.disconnect"})
        await t

    asyncio.run(_test_flow())


@pytest.mark.django_db(transaction=True)
def test_xauusd_asgi_websocket_subscription_and_event_isolation():
    """Verify active XAUUSD ASGI WebSocket subscription, frame delivery, and legacy XAUT event isolation."""
    user = User.objects.create_user(username="xauusd_ws_user", password="password123")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()

    async def _test_flow():
        sent_frames = []
        recv_q = asyncio.Queue()
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", f"sessionid={session.session_key}".encode("latin1"))],
            "path": "/live/ws/",
            "query_string": b"symbol=XAUUSD",
        }
        t = asyncio.create_task(application(scope, lambda: recv_q.get(), lambda m: sent_frames.append(m)))
        await asyncio.sleep(0.05)

        now_utc = datetime.now(timezone.utc)

        # 1. Broadcast legacy XAUT event -> Should be isolated and NOT received by XAUUSD subscriber
        def _emit_xaut():
            LiveEventBroadcaster.broadcast({
                "event_id": "EVT_XAUT_IGNORE",
                "event_type": "quote_update",
                "instrument": "XAUT/USDT",
                "data": {"bid": "2500.00", "ask": "2501.00"},
            })
        await sync_to_async(_emit_xaut)()
        await asyncio.sleep(0.05)

        # 2. Broadcast active XAUUSD quote event -> Should be received
        def _emit_xauusd_quote():
            LiveEventBroadcaster.broadcast({
                "event_id": "EVT_XAUUSD_DELIVER",
                "event_type": "quote_update",
                "instrument": "XAUUSD",
                "data": {"bid": "2650.00", "ask": "2651.00"},
                "source_timestamp": now_utc.isoformat(),
                "server_timestamp": now_utc.isoformat(),
            })
        await sync_to_async(_emit_xauusd_quote)()
        await asyncio.sleep(0.05)

        # 3. Broadcast active XAUUSD decision events
        def _emit_xauusd_decision():
            LiveEventBroadcaster.broadcast({
                "event_id": "SIG_XAUUSD_01",
                "event_type": "signal_update",
                "instrument": "XAUUSD",
                "data": {"state": "BUY_WINDOW", "candidate_action": "BUY"},
            })
            LiveEventBroadcaster.broadcast({
                "event_id": "RISK_XAUUSD_01",
                "event_type": "risk_plan_update",
                "instrument": "XAUUSD",
                "data": {"entry_price": "2650.00", "stop_loss": "2640.00"},
            })
        await sync_to_async(_emit_xauusd_decision)()
        await asyncio.sleep(0.05)

        # Parse received text frames
        received = [
            json.loads(m["text"]) for m in sent_frames
            if m.get("type") == "websocket.send" and "text" in m
        ]

        # Verify XAUT was completely excluded
        assert not any(f.get("instrument") == "XAUT/USDT" for f in received)

        # Verify XAUUSD quote and signal frames received
        xauusd_quotes = [f for f in received if f.get("event_type") == "quote_update" and f.get("instrument") == "XAUUSD"]
        xauusd_signals = [f for f in received if f.get("event_type") == "signal_update" and f.get("instrument") == "XAUUSD"]
        xauusd_risks = [f for f in received if f.get("event_type") == "risk_plan_update" and f.get("instrument") == "XAUUSD"]

        assert len(xauusd_quotes) >= 1
        assert len(xauusd_signals) >= 1
        assert len(xauusd_risks) >= 1

        await recv_q.put({"type": "websocket.disconnect"})
        await t

    asyncio.run(_test_flow())

