"""
Targeted tests for Realtime Connection State and Live Bid/Ask/Spread Status (Issue #1).
Verifies:
1. Market session state mapping (OPEN vs CLOSED) via canonical governed semantics.
2. Live projection state and dictionary serialization of market_session and is_market_closed.
3. WebSocket consumer initial_snapshot emission on connection accept.
4. Correct mapping of connection states (MARKET_CLOSED, LIVE, RECONNECTING, FEED_ERROR).
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
import json
import pytest

from engine.paper.continuity import is_expected_market_closure
from apps.live_monitor.models import LiveMonitorState
from apps.live_monitor.services import XauUsdLiveProjectionService
from apps.live_monitor.types import XauUsdLiveProjectionState


@pytest.mark.unit
@pytest.mark.django_db
class TestMarketSessionStateMapping:
    """Verify governed XAUUSD session semantics."""

    def test_canonical_closure_classifier_weekends(self):
        # Sunday 15:00 UTC -> CLOSED
        sunday_closed = datetime(2026, 9, 27, 15, 0, 0, tzinfo=timezone.utc)
        assert is_expected_market_closure(sunday_closed) is True

        # Friday 20:45 UTC -> OPEN
        friday_open = datetime(2026, 9, 25, 20, 45, 0, tzinfo=timezone.utc)
        assert is_expected_market_closure(friday_open) is False

        # Friday 21:15 UTC -> CLOSED
        friday_closed = datetime(2026, 9, 25, 21, 15, 0, tzinfo=timezone.utc)
        assert is_expected_market_closure(friday_closed) is True

        # Sunday 21:15 UTC -> OPEN (Summer schedule reopen)
        sunday_open = datetime(2026, 9, 27, 21, 15, 0, tzinfo=timezone.utc)
        assert is_expected_market_closure(sunday_open) is False

    @patch("apps.live_monitor.services.datetime")
    def test_assemble_projection_market_closed(self, mock_dt):
        sunday_15h = datetime(2026, 9, 27, 15, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = sunday_15h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=Decimal("4286.10"),
            current_ask=Decimal("4286.40"),
            spread=Decimal("0.30"),
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is True
        assert proj.market_session == "CLOSED"

        proj_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        assert proj_dict["is_market_closed"] is True
        assert proj_dict["market_session"] == "CLOSED"

    @patch("apps.live_monitor.services.datetime")
    def test_assemble_projection_market_open(self, mock_dt):
        monday_10h = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = monday_10h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=Decimal("4286.10"),
            current_ask=Decimal("4286.40"),
            spread=Decimal("0.30"),
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is False
        assert proj.market_session == "OPEN"

        proj_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        assert proj_dict["is_market_closed"] is False
        assert proj_dict["market_session"] == "OPEN"


@pytest.mark.unit
@pytest.mark.django_db
class TestWebSocketInitialSnapshotEmission:
    """Verify WebSocket consumer sends initial_snapshot with market session info."""

    def test_consumer_initial_snapshot(self):
        async def _test():
            from apps.live_monitor.consumers import LiveMonitorAsyncWebsocketConsumer

            user = MagicMock()
            user.is_authenticated = True

            scope = {
                "type": "websocket",
                "path": "/live/ws/",
                "query_string": b"symbol=XAUUSD",
                "user": user,
            }

            sent_messages = []

            async def fake_send(msg):
                sent_messages.append(msg)

            async def fake_receive():
                return {"type": "websocket.disconnect"}

            consumer = LiveMonitorAsyncWebsocketConsumer(scope, fake_receive, fake_send)
            await consumer()

            # Check that accept was sent
            assert any(m.get("type") == "websocket.accept" for m in sent_messages)

            # Check that initial_snapshot was sent
            snapshot_msgs = [
                json.loads(m["text"])
                for m in sent_messages
                if m.get("type") == "websocket.send" and "initial_snapshot" in m.get("text", "")
            ]
            assert len(snapshot_msgs) == 1
            snap = snapshot_msgs[0]
            assert snap["event_type"] == "initial_snapshot"
            assert snap["instrument"] == "XAUUSD"
            assert "is_market_closed" in snap["data"]
            assert "market_session" in snap["data"]
            assert "reference_price" in snap["data"]
            assert "reference_feed_status" in snap["data"]
            assert "execution_quote_available" in snap["data"]
            assert "primary_execution_venue_status" in snap["data"]
            assert "secondary_execution_venue_status" in snap["data"]

        asyncio.run(_test())


def classify_ui_status(
    is_market_closed: bool,
    ws_connected: bool,
    is_reconnecting: bool,
    feed_error: bool,
    reference_feed_status: str,
    has_live_quote: bool,
    has_reference_price: bool,
) -> str:
    """Mirrors the canonical UI state machine in static/dashboard/js/dashboard.js."""
    if is_market_closed:
        return "MARKET CLOSED"
    if is_reconnecting:
        return "RECONNECTING"
    if not ws_connected:
        return "CONNECTING"
    is_provider_fault = reference_feed_status in ("UNHEALTHY", "DOWN", "ERROR", "STALE")
    if feed_error or is_provider_fault or (not has_live_quote and not has_reference_price):
        return "FEED ERROR"
    if has_live_quote:
        return "LIVE"
    if has_reference_price and reference_feed_status == "HEALTHY":
        return "REFERENCE LIVE"
    return "CONNECTING"


@pytest.mark.unit
@pytest.mark.django_db
class TestTargetedRealtimeSemantics:
    """
    Targeted Realtime Semantics Specification:
    CASE A: Market closed -> MARKET CLOSED
    CASE B: Market open, reference provider healthy, reference price valid, bid/ask unavailable -> REFERENCE LIVE
    CASE C: Market open, reference provider unhealthy -> FEED ERROR
    CASE D: Market open, real execution quote available -> LIVE
    CASE E: WebSocket disconnected during open market -> RECONNECTING
    """

    @patch("apps.live_monitor.services.datetime")
    def test_case_a_market_closed(self, mock_dt):
        """CASE A: Market closed -> MARKET CLOSED."""
        sunday_15h = datetime(2026, 9, 27, 15, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = sunday_15h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=None,
            current_ask=None,
            spread=None,
            feed_health_data={
                "reference_price": "4211.95",
                "reference_price_source": "twelve_data",
                "xauusd_primary_status": "HEALTHY",
            },
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is True
        assert proj.market_session == "CLOSED"

        proj_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        assert proj_dict["is_market_closed"] is True
        assert proj_dict["market_session"] == "CLOSED"

        ui_status = classify_ui_status(
            is_market_closed=proj.is_market_closed,
            ws_connected=True,
            is_reconnecting=False,
            feed_error=False,
            reference_feed_status=proj.reference_feed_status,
            has_live_quote=proj.execution_quote_available,
            has_reference_price=bool(proj.reference_price),
        )
        assert ui_status == "MARKET CLOSED"

    @patch("apps.live_monitor.services.datetime")
    def test_case_b_market_open_reference_live(self, mock_dt):
        """CASE B: Market open, reference provider healthy, reference price valid, bid/ask unavailable -> REFERENCE LIVE."""
        monday_10h = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = monday_10h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=None,
            current_ask=None,
            spread=None,
            feed_health_data={
                "reference_price": "4211.95",
                "reference_price_timestamp": "2026-09-28T09:00:00+00:00",
                "reference_price_source": "twelve_data",
                "xauusd_primary_status": "HEALTHY",
                "primary_execution_venue_status": "HALTED",
                "secondary_execution_venue_status": "NOT_CONFIGURED",
            },
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is False
        assert proj.market_session == "OPEN"
        assert proj.reference_price == Decimal("4211.95")
        assert proj.reference_feed_status == "HEALTHY"
        assert proj.reference_price_source == "twelve_data"
        assert proj.current_bid is None
        assert proj.current_ask is None
        assert proj.spread is None
        assert proj.execution_quote_available is False
        assert proj.primary_execution_venue_status == "HALTED"
        assert proj.secondary_execution_venue_status == "NOT_CONFIGURED"

        proj_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        assert proj_dict["reference_price"] == "4211.95"
        assert proj_dict["current_bid"] is None
        assert proj_dict["current_ask"] is None
        assert proj_dict["spread"] is None
        assert proj_dict["execution_quote_available"] is False

        ui_status = classify_ui_status(
            is_market_closed=proj.is_market_closed,
            ws_connected=True,
            is_reconnecting=False,
            feed_error=False,
            reference_feed_status=proj.reference_feed_status,
            has_live_quote=proj.execution_quote_available,
            has_reference_price=bool(proj.reference_price),
        )
        assert ui_status == "REFERENCE LIVE"

    @patch("apps.live_monitor.services.datetime")
    def test_case_c_market_open_reference_unhealthy_feed_error(self, mock_dt):
        """CASE C: Market open, reference provider unhealthy -> FEED ERROR."""
        monday_10h = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = monday_10h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=None,
            current_ask=None,
            spread=None,
            feed_health_data={
                "xauusd_primary_status": "UNHEALTHY",
            },
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is False
        assert proj.reference_feed_status == "UNHEALTHY"

        ui_status = classify_ui_status(
            is_market_closed=proj.is_market_closed,
            ws_connected=True,
            is_reconnecting=False,
            feed_error=False,
            reference_feed_status=proj.reference_feed_status,
            has_live_quote=proj.execution_quote_available,
            has_reference_price=bool(proj.reference_price),
        )
        assert ui_status == "FEED ERROR"

    @patch("apps.live_monitor.services.datetime")
    def test_case_d_market_open_execution_quote_live(self, mock_dt):
        """CASE D: Market open, real execution quote available -> LIVE."""
        monday_10h = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = monday_10h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=Decimal("4211.90"),
            current_ask=Decimal("4212.10"),
            spread=Decimal("0.20"),
            feed_health_data={
                "reference_price": "4211.95",
                "xauusd_primary_status": "HEALTHY",
                "primary_execution_venue_status": "CONNECTED",
            },
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is False
        assert proj.current_bid == Decimal("4211.90")
        assert proj.current_ask == Decimal("4212.10")
        assert proj.spread == Decimal("0.20")
        assert proj.execution_quote_available is True

        ui_status = classify_ui_status(
            is_market_closed=proj.is_market_closed,
            ws_connected=True,
            is_reconnecting=False,
            feed_error=False,
            reference_feed_status=proj.reference_feed_status,
            has_live_quote=proj.execution_quote_available,
            has_reference_price=bool(proj.reference_price),
        )
        assert ui_status == "LIVE"

    @patch("apps.live_monitor.services.datetime")
    def test_case_e_ws_disconnected_reconnecting(self, mock_dt):
        """CASE E: WebSocket disconnected during open market -> RECONNECTING."""
        monday_10h = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = monday_10h
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_bid=None,
            current_ask=None,
            spread=None,
            feed_health_data={
                "reference_price": "4211.95",
                "xauusd_primary_status": "HEALTHY",
            },
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.is_market_closed is False

        ui_status = classify_ui_status(
            is_market_closed=proj.is_market_closed,
            ws_connected=False,
            is_reconnecting=True,
            feed_error=False,
            reference_feed_status=proj.reference_feed_status,
            has_live_quote=proj.execution_quote_available,
            has_reference_price=bool(proj.reference_price),
        )
        assert ui_status == "RECONNECTING"
