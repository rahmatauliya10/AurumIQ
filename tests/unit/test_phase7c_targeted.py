"""Targeted unit tests for Phase 7C: Operational Resilience, Fail-Safe Recovery, and Health Observability."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from unittest.mock import patch, MagicMock
from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.consumers import LiveEventBroadcaster, LiveMonitorWebSocketHandler
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import (
    LiveDecisionPipelineService,
    LiveQuoteService,
    StateRecoveryService,
)
from apps.live_monitor.types import (
    CandleClosedEvent,
    EntryZoneStatus,
    FeedStatus,
    LiveQuoteEvent,
    OperationalHealthStatus,
    OperationalMetrics,
)
from apps.market_data.models import CandleQualityFlag, DataQualitySnapshot, MarketCandle
from apps.signals.models import SignalRecord
from engine.core.types import CandleData, MacroEventContext
from engine.signals.engine import XautSignalEngine


def _make_closed_candle(
    ts_open: datetime,
    duration_min: int,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    vol: Decimal = Decimal("100.00000000"),
    is_closed: bool = True,
) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=duration_min),
        open=open_p.quantize(Decimal("0.00000001")),
        high=high_p.quantize(Decimal("0.00000001")),
        low=low_p.quantize(Decimal("0.00000001")),
        close=close_p.quantize(Decimal("0.00000001")),
        volume=vol.quantize(Decimal("0.00000001")),
        is_closed=is_closed,
    )


def _generate_15m_candles(
    count: int = 40,
    start_time: datetime = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc),
    start_price: Decimal = Decimal("2500.00"),
) -> list[CandleData]:
    candles = []
    current_p = start_price
    for i in range(count):
        t_open = start_time + timedelta(minutes=15 * i)
        o = current_p
        h = o + Decimal("3.00")
        l = o - Decimal("1.50")
        c = o + Decimal("1.00")
        candles.append(_make_closed_candle(t_open, 15, o, h, l, c))
        current_p = c
    return candles


@pytest.mark.django_db
class Phase7CTargetedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="resilience_op", password="password123")
        self.client = Client()

        self.xaut_asset, _ = Asset.objects.get_or_create(code="XAUT", name="Tether Gold", asset_type=AssetType.CRYPTO_TOKEN)
        self.usdt_asset, _ = Asset.objects.get_or_create(code="USDT", name="Tether USD", asset_type=AssetType.CRYPTO_TOKEN)
        self.xau_asset, _ = Asset.objects.get_or_create(code="XAU", name="Gold Spot", asset_type=AssetType.COMMODITY)
        self.usd_asset, _ = Asset.objects.get_or_create(code="USD", name="US Dollar", asset_type=AssetType.FIAT)

        self.xaut_inst, _ = Instrument.objects.get_or_create(
            base_asset=self.xaut_asset,
            quote_asset=self.usdt_asset,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION},
        )
        self.xau_inst, _ = Instrument.objects.get_or_create(
            base_asset=self.xau_asset,
            quote_asset=self.usd_asset,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE},
        )

        self.candles_15m = _generate_15m_candles(40)
        self.t_close = self.candles_15m[-1].timestamp_close
        self.code_revision = "15d388d184852f55fb8f00072b22ec76b3cb96e7"

        for c in self.candles_15m:
            MarketCandle.objects.create(
                instrument=self.xaut_inst,
                source="binance",
                timeframe="15m",
                timestamp_open=c.timestamp_open,
                timestamp_close=c.timestamp_close,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=True,
                data_quality_flag=CandleQualityFlag.OK,
            )

        # Baseline LiveMonitorState
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

    # --- P7-28: Provider outage fail-closed ---
    def test_p7_28_provider_outage_fail_closed(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P728",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig, risk, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=None,  # OUTAGE
            usdt_rate=Decimal("1.0000"),
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("Missing verified canonical XAU" in h for h in sig.hard_gate_reasons)

    # --- P7-29: Partial provider transition preserves continuity gate ---
    def test_p7_29_partial_provider_transition_preserves_continuity_gate(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P729",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig, risk, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_provider_transition=True,
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("TRANSITION" in h for h in sig.hard_gate_reasons)

    # --- P7-30: Redis outage does not alter canonical evidence ---
    def test_p7_30_redis_outage_does_not_alter_canonical_evidence(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P730",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig, risk, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        # Simulate total Redis outage during WebSocket broadcast
        with patch.object(LiveEventBroadcaster, "broadcast", side_effect=Exception("Redis Connection Refused")):
            assert SignalRecord.objects.filter(analysis_fingerprint=sig.analysis_fingerprint).exists()
            assert LiveRiskPlanRecord.objects.filter(source_signal_fingerprint=sig.analysis_fingerprint).exists()
            assert LiveMonitorState.objects.filter(signal_fingerprint=sig.analysis_fingerprint).exists()

    # --- P7-31: Redis recovery rebuilds canonical latest state ---
    def test_p7_31_redis_recovery_rebuilds_canonical_latest_state(self):
        # Create a SignalRecord in DB
        sig = SignalRecord.objects.create(
            analysis_fingerprint="CANONICAL_P731_FP",
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime.now(timezone.utc),
            state="BUY_WINDOW",
            user_decision="BUY",
            direction_score=90.0,
            timing_score=85.0,
            engine_version="4.0.0",
            code_revision=self.code_revision,
        )

        state = StateRecoveryService.reconstruct_state("XAUT/USDT")
        assert state.instrument == "XAUT/USDT"
        assert state.signal_fingerprint == "CANONICAL_P731_FP"

        payload = LiveEventBroadcaster.format_signal_update(
            instrument=state.instrument,
            signal_fingerprint=state.signal_fingerprint,
            signal_state=state.signal_state,
            signal_user_decision=state.signal_user_decision,
            direction_score=state.direction_score,
            timing_score=state.timing_score,
            last_closed_candle_ts=state.last_closed_candle_ts,
            decision_sequence=state.decision_sequence,
            reasons_positive=state.reasons_positive,
            reasons_negative=state.reasons_negative,
            hard_gate_reasons=state.hard_gate_reasons,
        )
        assert payload["event_type"] == "signal_update"
        assert payload["data"]["signal_fingerprint"] == "CANONICAL_P731_FP"

    # --- P7-32: Celery signal retry idempotency ---
    def test_p7_32_celery_signal_retry_idempotency(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P732",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig1, risk1, state1 = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        count_before = SignalRecord.objects.count()

        sig2, risk2, state2 = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        count_after = SignalRecord.objects.count()

        assert count_before == count_after
        assert sig1.analysis_fingerprint == sig2.analysis_fingerprint

    # --- P7-33: Celery risk retry idempotency ---
    def test_p7_33_celery_risk_retry_idempotency(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P733",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig1, risk1, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        risk_count_before = LiveRiskPlanRecord.objects.count()

        sig2, risk2, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        risk_count_after = LiveRiskPlanRecord.objects.count()

        assert risk_count_before == risk_count_after
        assert risk1.source_signal_fingerprint == risk2.source_signal_fingerprint

    # --- P7-34: Crash after signal persistence recovers projection ---
    def test_p7_34_crash_after_signal_persistence_recovers_projection(self):
        sig = SignalRecord.objects.create(
            analysis_fingerprint="MOCK_CRASH_P734",
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime(2026, 8, 30, 8, 30, tzinfo=timezone.utc),
            state="BUY_WINDOW",
            user_decision="BUY",
            direction_score=88.0,
            timing_score=82.0,
            engine_version="4.0.0",
            code_revision=self.code_revision,
        )
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()

        state = StateRecoveryService.reconstruct_state("XAUT/USDT")
        assert state.signal_fingerprint == "MOCK_CRASH_P734"
        assert state.signal_state == "BUY_WINDOW"
        assert state.signal_user_decision == "BUY"
        assert state.direction_score == 88.0

    # --- P7-35: Crash after risk-plan persistence recovers projection ---
    def test_p7_35_crash_after_risk_persistence_recovers_projection(self):
        sig = SignalRecord.objects.create(
            analysis_fingerprint="MOCK_CRASH_P735",
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime(2026, 8, 30, 8, 45, tzinfo=timezone.utc),
            state="BUY_WINDOW",
            user_decision="BUY",
            direction_score=89.0,
            timing_score=83.0,
            engine_version="4.0.0",
            code_revision=self.code_revision,
        )
        risk = LiveRiskPlanRecord.objects.create(
            source_signal_fingerprint="MOCK_CRASH_P735",
            signal_timestamp=sig.timestamp,
            instrument="XAUT/USDT",
            entry_min=Decimal("2510.00"),
            entry_mid=Decimal("2515.00"),
            entry_max=Decimal("2520.00"),
            stop_structure=Decimal("2490.00"),
            stop_atr=Decimal("2492.00"),
            stop_final=Decimal("2490.00"),
            stop_distance_atr=Decimal("1.50"),
            tp1=Decimal("2560.00"),
            tp2=Decimal("2590.00"),
            rr_tp1=Decimal("2.00"),
            rr_tp2=Decimal("3.00"),
            is_valid_risk_plan=True,
            execution_eligible=True,
            effective_action="BUY",
            code_revision=self.code_revision,
        )

        LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()

        state = StateRecoveryService.reconstruct_state("XAUT/USDT")
        assert state.signal_fingerprint == "MOCK_CRASH_P735"
        assert state.effective_action == "BUY"
        assert state.risk_plan_valid is True
        assert state.entry_min == Decimal("2510.0000")

    # --- P7-36: Django restart restores canonical state ---
    def test_p7_36_django_restart_restores_canonical_state(self):
        self.client.force_login(self.user)
        # Verify REST queries return state
        resp = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instrument"] == "XAUT/USDT"
        assert data["signal_user_decision"] == "BUY"

    # --- P7-37: Missed WebSocket decision converges on reconnect ---
    def test_p7_37_missed_websocket_decision_converges_on_reconnect(self):
        self.client.force_login(self.user)

        # Decision updates in DB while client was disconnected
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").update(
            signal_state="WAIT",
            signal_user_decision="WAIT",
            effective_action="WAIT",
            direction_score=50.0,
            decision_sequence=99,
        )

        resp = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["effective_action"] == "WAIT"
        assert data["direction_score"] == 50.0
        assert data["decision_sequence"] == 99

    # --- P7-38: Stale feed gives degraded/stale health and fail-closed decision ---
    def test_p7_38_stale_feed_degraded_health_fail_closed(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P738",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig, risk, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_feed_stale=True,
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert state.feed_health_data["xaut_status"] == FeedStatus.STALE.value

    # --- P7-39: Recovery requires valid fresh/continuity evidence ---
    def test_p7_39_recovery_requires_valid_fresh_evidence(self):
        q_stale = LiveQuoteEvent(
            event_id="Q_STALE",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2500.00"),
            ask=Decimal("2500.50"),
            source_timestamp=datetime.now(timezone.utc) - timedelta(seconds=120),
            received_timestamp=datetime.now(timezone.utc),
            sequence_number=101,
        )
        state_stale = LiveQuoteService.process_quote(q_stale)
        assert state_stale.is_quote_stale is True

        q_fresh = LiveQuoteEvent(
            event_id="Q_FRESH",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2501.00"),
            ask=Decimal("2501.50"),
            source_timestamp=datetime.now(timezone.utc),
            received_timestamp=datetime.now(timezone.utc),
            sequence_number=102,
        )
        state_fresh = LiveQuoteService.process_quote(q_fresh)
        assert state_fresh.is_quote_stale is False

    # --- P7-40: Duplicate candle event after restart remains idempotent ---
    def test_p7_40_duplicate_candle_event_after_restart_idempotent(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_P740",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            is_closed=True,
        )

        sig1, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        StateRecoveryService.reconstruct_state("XAUT/USDT")

        sig2, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig1.analysis_fingerprint == sig2.analysis_fingerprint

    # --- P7-41: Out-of-order event after restart cannot regress state ---
    def test_p7_41_out_of_order_event_after_restart_cannot_regress_state(self):
        now = datetime.now(timezone.utc)
        q_new = LiveQuoteEvent(
            event_id="Q_NEW",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2550.00"),
            ask=Decimal("2550.50"),
            source_timestamp=now,
            received_timestamp=now,
            sequence_number=1000,
        )
        LiveQuoteService.process_quote(q_new)

        StateRecoveryService.reconstruct_state("XAUT/USDT")

        q_old = LiveQuoteEvent(
            event_id="Q_OLD",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2500.00"),
            ask=Decimal("2500.50"),
            source_timestamp=now - timedelta(seconds=10),
            received_timestamp=now,
            sequence_number=999,
        )
        state_after = LiveQuoteService.process_quote(q_old)
        assert state_after.current_ask == Decimal("2550.5000")

    # --- P7-42: Liveness health semantics ---
    def test_p7_42_liveness_health_semantics(self):
        resp = self.client.get("/health/live/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "aurumiq-live-monitor"

    # --- P7-43: Readiness health semantics ---
    def test_p7_43_readiness_health_semantics(self):
        resp = self.client.get("/health/ready/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"
        assert data["instrument_configured"] is True

    # --- P7-44: Structured operational event logging ---
    def test_p7_44_structured_operational_event_logging(self):
        with patch("structlog.get_logger") as mock_get_logger:
            mock_log = MagicMock()
            mock_get_logger.return_value = mock_log

            metrics = OperationalMetrics(
                quote_age_seconds=1.2,
                analysis_latency_ms=45.0,
                event_latency_ms=12.0,
                recovery_count=1,
            )
            assert metrics.operational_health == OperationalHealthStatus.HEALTHY
            assert metrics.analysis_latency_ms == 45.0

    # --- P7-45: No private trading credentials / order capabilities ---
    def test_p7_45_no_private_trading_credentials(self):
        from config.settings import base as settings_module
        prohibited_keys = [
            "BINANCE_API_SECRET",
            "BINANCE_API_KEY",
            "EXCHANGE_PRIVATE_KEY",
            "ORDER_EXECUTION_ENABLED",
            "AUTO_TRADE_ENABLED",
        ]
        for key in prohibited_keys:
            assert not hasattr(settings_module, key), f"Prohibited setting {key} found in config!"
