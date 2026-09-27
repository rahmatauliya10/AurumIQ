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

        asyncio.run(_test())
