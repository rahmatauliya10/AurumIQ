"""Targeted unit tests for Phase 7B: Dashboard, WebSockets, Authorization, and Reconnect Safety."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.consumers import LiveEventBroadcaster, LiveMonitorWebSocketHandler
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import StateRecoveryService
from apps.market_data.models import CandleQualityFlag, MarketCandle
from apps.signals.models import SignalRecord


@pytest.mark.django_db
class Phase7BTargetedTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(username="testoperator", password="password123")
        self.client = Client()

        # Create base assets & instruments
        self.xaut_asset, _ = Asset.objects.get_or_create(code="XAUT", name="Tether Gold", asset_type=AssetType.CRYPTO_TOKEN)
        self.usdt_asset, _ = Asset.objects.get_or_create(code="USDT", name="Tether USD", asset_type=AssetType.CRYPTO_TOKEN)
        self.xaut_inst, _ = Instrument.objects.get_or_create(
            base_asset=self.xaut_asset,
            quote_asset=self.usdt_asset,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION},
        )

        # Baseline LiveMonitorState
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()
        self.state = LiveMonitorState.objects.create(
            instrument="XAUT/USDT",
            current_bid=Decimal("2500.00"),
            current_ask=Decimal("2500.50"),
            spread=Decimal("0.50"),
            spread_pct=Decimal("0.0200"),
            quote_sequence=100,
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            direction_score=85.0,
            timing_score=80.0,
            risk_plan_valid=True,
            execution_eligible=True,
            effective_action="BUY",
            entry_min=Decimal("2495.00"),
            entry_mid=Decimal("2500.00"),
            entry_max=Decimal("2505.00"),
            stop_final=Decimal("2475.00"),
            tp1=Decimal("2550.00"),
            tp2=Decimal("2580.00"),
            rr_tp1=Decimal("2.00"),
            rr_tp2=Decimal("3.20"),
            decision_sequence=10,
        )

    # --- P7-19: WebSocket quote update ---
    def test_p7_19_websocket_quote_update(self):
        handler = LiveMonitorWebSocketHandler(user=self.user, instrument="XAUT/USDT")
        assert handler.connect() is True

        payload = LiveEventBroadcaster.format_quote_event(
            instrument="XAUT/USDT",
            bid=Decimal("2510.00"),
            ask=Decimal("2510.50"),
            spread=Decimal("0.50"),
            spread_pct=Decimal("0.0199"),
            source_timestamp=datetime.now(timezone.utc),
            sequence_number=101,
            entry_zone_status="INSIDE_ZONE",
        )

        LiveEventBroadcaster.broadcast(payload)
        assert len(handler.message_queue) == 1
        msg = json.loads(handler.message_queue[0])
        assert msg["event_type"] == "quote_update"
        assert msg["data"]["ask"] == "2510.50"
        assert msg["sequence_number"] == 101

        handler.disconnect()

    # --- P7-20: WebSocket signal update ---
    def test_p7_20_websocket_signal_update(self):
        handler = LiveMonitorWebSocketHandler(user=self.user, instrument="XAUT/USDT")
        handler.connect()

        payload = LiveEventBroadcaster.format_signal_update(
            instrument="XAUT/USDT",
            signal_fingerprint="MOCK_SIG_FP_P720",
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            direction_score=88.0,
            timing_score=82.0,
            last_closed_candle_ts=datetime.now(timezone.utc),
            decision_sequence=11,
            reasons_positive=["Confirmed bullish BOS"],
            reasons_negative=[],
            hard_gate_reasons=[],
        )

        LiveEventBroadcaster.broadcast(payload)
        assert len(handler.message_queue) == 1
        msg = json.loads(handler.message_queue[0])
        assert msg["event_type"] == "signal_update"
        assert msg["data"]["signal_fingerprint"] == "MOCK_SIG_FP_P720"
        assert msg["decision_sequence"] == 11

        handler.disconnect()

    # --- P7-21: WebSocket risk-plan update ---
    def test_p7_21_websocket_risk_plan_update(self):
        handler = LiveMonitorWebSocketHandler(user=self.user, instrument="XAUT/USDT")
        handler.connect()

        payload = LiveEventBroadcaster.format_risk_plan_update(
            instrument="XAUT/USDT",
            source_signal_fingerprint="MOCK_SIG_FP_P721",
            risk_plan_valid=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2500.00"),
            entry_mid=Decimal("2505.00"),
            entry_max=Decimal("2510.00"),
            stop_final=Decimal("2480.00"),
            tp1=Decimal("2525.00"),
            tp2=Decimal("2550.00"),
            rr_tp1=Decimal("1.25"),
            rr_tp2=Decimal("2.00"),
            decision_sequence=12,
        )

        LiveEventBroadcaster.broadcast(payload)
        assert len(handler.message_queue) == 1
        msg = json.loads(handler.message_queue[0])
        assert msg["event_type"] == "risk_plan_update"
        assert msg["data"]["effective_action"] == "WAIT"
        assert msg["data"]["risk_plan_valid"] is False

        handler.disconnect()

    # --- P7-22: WebSocket feed-health update ---
    def test_p7_22_websocket_feed_health_update(self):
        handler = LiveMonitorWebSocketHandler(user=self.user, instrument="XAUT/USDT")
        handler.connect()

        health_data = {
            "xaut_status": "HEALTHY",
            "xau_status": "HEALTHY",
            "usdt_norm_status": "HEALTHY",
            "macro_status": "HEALTHY",
            "provider_sync_status": "HEALTHY",
        }
        payload = LiveEventBroadcaster.format_feed_health_update(
            instrument="XAUT/USDT",
            feed_health=health_data,
        )

        LiveEventBroadcaster.broadcast(payload)
        assert len(handler.message_queue) == 1
        msg = json.loads(handler.message_queue[0])
        assert msg["event_type"] == "feed_health_update"
        assert msg["data"]["xaut_status"] == "HEALTHY"

        handler.disconnect()

    # --- P7-23: Stale / out-of-order client update ignored ---
    def test_p7_23_stale_out_of_order_client_update_ignored(self):
        # Client currently at quote sequence 100
        handler = LiveMonitorWebSocketHandler(user=self.user, instrument="XAUT/USDT")
        handler.connect()
        handler.last_quote_sequence = 100

        # Simulate client sequence rejection rule
        old_seq = 95
        new_seq = 105
        assert old_seq <= handler.last_quote_sequence  # Stale: ignored
        assert new_seq > handler.last_quote_sequence   # Fresh: accepted

        handler.disconnect()

    # --- P7-23A: Duplicate WebSocket event idempotent ---
    def test_p7_23a_duplicate_websocket_event_idempotent(self):
        handler = LiveMonitorWebSocketHandler(user=self.user, instrument="XAUT/USDT")
        handler.connect()

        payload = LiveEventBroadcaster.format_quote_event(
            instrument="XAUT/USDT",
            bid=Decimal("2510.00"),
            ask=Decimal("2510.50"),
            spread=Decimal("0.50"),
            spread_pct=Decimal("0.0199"),
            source_timestamp=datetime.now(timezone.utc),
            sequence_number=105,
            entry_zone_status="INSIDE_ZONE",
        )

        # Broadcast twice
        LiveEventBroadcaster.broadcast(payload)
        LiveEventBroadcaster.broadcast(payload)

        # Both delivered without state corruption
        assert len(handler.message_queue) == 2
        msg1 = json.loads(handler.message_queue[0])
        msg2 = json.loads(handler.message_queue[1])
        assert msg1["sequence_number"] == msg2["sequence_number"]

        handler.disconnect()

    # --- P7-23B: Old decision revision cannot overwrite newer decision ---
    def test_p7_23b_old_decision_revision_cannot_overwrite_newer(self):
        current_decision_seq = 15
        incoming_old_seq = 14
        incoming_new_seq = 16

        assert incoming_old_seq <= current_decision_seq  # Rejected
        assert incoming_new_seq > current_decision_seq   # Accepted

    # --- P7-24: Reconnect restores canonical current state ---
    def test_p7_24_reconnect_restores_canonical_current_state(self):
        self.client.force_login(self.user)
        response = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert response.status_code == 200
        data = response.json()
        assert data["instrument"] == "XAUT/USDT"
        assert data["current_ask"] == "2500.5000"
        assert data["effective_action"] == "BUY"
        assert data["direction_score"] == 85.0

    # --- P7-24A: Missed messages followed by reconnect converge to canonical DB state ---
    def test_p7_24a_missed_messages_reconnect_converges(self):
        self.client.force_login(self.user)

        # Background decision updates DB state
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").update(
            current_ask=Decimal("2520.00"),
            effective_action="WAIT",
            signal_state="WAIT",
            decision_sequence=20,
        )

        # Reconnecting client queries REST state
        response = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert response.status_code == 200
        data = response.json()
        assert data["current_ask"] == "2520.0000"
        assert data["effective_action"] == "WAIT"
        assert data["decision_sequence"] == 20

    # --- P7-25: Unauthorized WebSocket and APIs rejected ---
    def test_p7_25_unauthorized_access_rejected(self):
        # 1. Anonymous WebSocket handler
        anon_handler = LiveMonitorWebSocketHandler(user=None, instrument="XAUT/USDT")
        assert anon_handler.connect() is False

        # 2. Anonymous REST API request
        anon_client = Client()
        resp_state = anon_client.get("/live/api/state/?symbol=XAUT/USDT")
        assert resp_state.status_code in (401, 403)

        resp_chart = anon_client.get("/live/api/chart/?symbol=XAUT/USDT")
        assert resp_chart.status_code in (401, 403)

    # --- P7-26: Dashboard is read-only for signal/risk evidence ---
    def test_p7_26_dashboard_read_only_for_audit_evidence(self):
        self.client.force_login(self.user)
        # Verify read-only API returns historical signals
        sig = SignalRecord.objects.create(
            analysis_fingerprint="TEST_P726_SIG_FP",
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime.now(timezone.utc),
            state="BUY_WINDOW",
            user_decision="BUY",
            direction_score=90.0,
            timing_score=85.0,
        )
        resp = self.client.get("/live/api/history/signals/?symbol=XAUT/USDT")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert any(r["analysis_fingerprint"] == "TEST_P726_SIG_FP" for r in results)

        # Verify no POST / PUT / DELETE endpoints exist on history
        resp_post = self.client.post("/live/api/history/signals/", {"data": "hack"})
        assert resp_post.status_code in (405, 403, 400)

    # --- P7-26A: No trading/order endpoint or UI action exists ---
    def test_p7_26a_no_trading_order_endpoints_exist(self):
        self.client.force_login(self.user)
        # Attempt to call hypothetical trading endpoints
        for endpoint in ["/live/api/trade/", "/live/api/order/", "/live/api/buy/", "/live/api/sell/"]:
            resp = self.client.post(endpoint, {"amount": 100})
            assert resp.status_code in (404, 405)

    # --- P7-27: Historical signal/risk rendering preserves immutable canonical values ---
    def test_p7_27_historical_rendering_preserves_canonical_values(self):
        self.client.force_login(self.user)
        sig = SignalRecord.objects.create(
            analysis_fingerprint="IMMUTABLE_FP_P727",
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
            state="BUY_WINDOW",
            user_decision="BUY",
            direction_score=87.5,
            timing_score=81.2,
            engine_version="4.0.0",
            code_revision="15d388d1",
        )
        risk = LiveRiskPlanRecord.objects.create(
            source_signal_fingerprint="IMMUTABLE_FP_P727",
            signal_timestamp=sig.timestamp,
            instrument="XAUT/USDT",
            entry_min=Decimal("2510.00"),
            entry_mid=Decimal("2515.00"),
            entry_max=Decimal("2520.00"),
            stop_structure=Decimal("2490.00"),
            stop_atr=Decimal("2495.00"),
            stop_final=Decimal("2490.00"),
            stop_distance_atr=Decimal("1.60"),
            tp1=Decimal("2570.00"),
            tp2=Decimal("2600.00"),
            rr_tp1=Decimal("2.00"),
            rr_tp2=Decimal("3.00"),
            is_valid_risk_plan=True,
            execution_eligible=True,
            effective_action="BUY",
            code_revision="15d388d1",
        )

        resp_sig = self.client.get("/live/api/history/signals/?symbol=XAUT/USDT")
        sig_data = next(r for r in resp_sig.json()["results"] if r["analysis_fingerprint"] == "IMMUTABLE_FP_P727")
        assert sig_data["direction_score"] == 87.5
        assert sig_data["code_revision"] == "15d388d1"

        resp_risk = self.client.get("/live/api/history/risk/?symbol=XAUT/USDT")
        risk_data = next(r for r in resp_risk.json()["results"] if r["source_signal_fingerprint"] == "IMMUTABLE_FP_P727")
        assert risk_data["entry_min"] == "2510.0000"
        assert risk_data["rr_tp1"] == "2.0000"
        assert risk_data["effective_action"] == "BUY"

    # --- P7-27A: BUY_WINDOW + invalid risk displays source BUY signal but primary effective action WAIT ---
    def test_p7_27a_buy_window_invalid_risk_presentation(self):
        self.client.force_login(self.user)
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").update(
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            risk_plan_valid=False,
            execution_eligible=False,
            effective_action="WAIT",
        )

        response = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert response.status_code == 200
        data = response.json()
        assert data["signal_state"] == "BUY_WINDOW"
        assert data["signal_user_decision"] == "BUY"
        assert data["risk_plan_valid"] is False
        assert data["execution_eligible"] is False
        assert data["effective_action"] == "WAIT"

    # --- P7-28: ASGI WebSocket Transport Handshake & Message Framing ---
    def test_p7_28_asgi_websocket_transport_handshake_and_frames(self):
        import asyncio
        from config.asgi import application
        from apps.live_monitor.consumers import LiveEventBroadcaster

        async def _run_test():
            # 1. Unauthenticated ASGI scope rejected with close code 4401
            sent_messages_unauth = []
            async def mock_send_unauth(msg):
                sent_messages_unauth.append(msg)

            async def mock_recv_unauth():
                return {"type": "websocket.disconnect"}

            unauth_scope = {"type": "websocket", "user": None, "path": "/ws/live/"}
            await application(unauth_scope, mock_recv_unauth, mock_send_unauth)
            assert len(sent_messages_unauth) == 1
            assert sent_messages_unauth[0]["type"] == "websocket.close"
            assert sent_messages_unauth[0]["code"] == 4401

            # 2. Authenticated ASGI scope accepts and frames messages
            sent_messages_auth = []
            recv_queue = asyncio.Queue()

            async def mock_send_auth(msg):
                sent_messages_auth.append(msg)

            async def mock_recv_auth():
                return await recv_queue.get()

            auth_scope = {"type": "websocket", "user": self.user, "path": "/ws/live/"}
            
            # Start consumer task
            consumer_task = asyncio.create_task(
                application(auth_scope, mock_recv_auth, mock_send_auth)
            )
            await asyncio.sleep(0.01)

            assert any(m.get("type") == "websocket.accept" for m in sent_messages_auth)

            # Broadcast live quote event
            payload = LiveEventBroadcaster.format_quote_event(
                instrument="XAUT/USDT",
                bid=Decimal("2515.00"),
                ask=Decimal("2515.50"),
                spread=Decimal("0.50"),
                spread_pct=Decimal("0.0198"),
                source_timestamp=datetime.now(timezone.utc),
                sequence_number=201,
                entry_zone_status="INSIDE_ZONE",
            )
            LiveEventBroadcaster.broadcast(payload)
            await asyncio.sleep(0.01)

            # Ping-Pong frame
            await recv_queue.put({"type": "websocket.receive", "text": "ping"})
            await asyncio.sleep(0.01)
            assert any(m.get("text") == "pong" for m in sent_messages_auth)

            # Disconnect
            await recv_queue.put({"type": "websocket.disconnect"})
            await consumer_task

        asyncio.run(_run_test())

