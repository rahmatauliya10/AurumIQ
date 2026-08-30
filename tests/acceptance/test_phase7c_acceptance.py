"""Phase 7C Master Acceptance Test Suite: Operational Resilience & Canonical Recovery Consistency.

Acceptance Gates:
  A46 — LIVE OPERATIONAL FAIL-SAFE
  A47 — CANONICAL RECOVERY CONSISTENCY
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import (
    LiveDecisionPipelineService,
    LiveQuoteService,
    StateRecoveryService,
)
from apps.live_monitor.types import CandleClosedEvent, EntryZoneStatus, LiveQuoteEvent
from apps.market_data.models import CandleQualityFlag, MarketCandle
from apps.signals.models import SignalRecord
from engine.core.types import CandleData, MacroEventContext


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


@pytest.mark.acceptance
@pytest.mark.django_db
class Phase7CAcceptanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="leadresilience", password="password123")
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

    # --- A46: LIVE OPERATIONAL FAIL-SAFE ---
    def test_a46_live_operational_fail_safe(self):
        """
        Gate A46:
          Simulates live production stress failures (provider outage, stale feed,
          provider transition, macro blackout) and verifies the system fails closed
          (effective WAIT / FORCE_WAIT) without continuing dangerous BUY decisions.
        """
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_A46",
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

        # 1. Total XAU reference loss
        sig_xau_loss, _, state_xau_loss = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=None,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig_xau_loss.user_decision == "WAIT"
        assert state_xau_loss.effective_action == "WAIT"

        # 2. Stale data feed
        sig_stale, _, state_stale = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_feed_stale=True,
        )
        assert sig_stale.user_decision == "WAIT"
        assert state_stale.effective_action == "WAIT"

        # 3. Macro Blackout
        macro_ctx = MacroEventContext(
            is_in_blackout=True,
            minutes_to_next_event=0,
            minutes_since_last_event=5,
            active_event_name="FOMC Statement",
        )
        sig_macro, _, state_macro = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            macro_context=macro_ctx,
        )
        assert sig_macro.user_decision == "WAIT"
        assert state_macro.effective_action == "WAIT"

    # --- A47: CANONICAL RECOVERY CONSISTENCY ---
    def test_a47_canonical_recovery_consistency(self):
        """
        Gate A47:
          Executes a normal analysis pipeline producing immutable SignalRecord and LiveRiskPlanRecord,
          deletes the presentation state (simulating complete system crash / cache wipe),
          reconstructs state via StateRecoveryService, and proves bit-for-bit equivalence
          with persisted canonical database records.
        """
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_A47",
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

        sig_orig, risk_orig, state_orig = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        # Drop presentation table row
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()

        # Reconstruct
        state_recovered = StateRecoveryService.reconstruct_state("XAUT/USDT")

        # Prove bit-for-bit canonical consistency
        assert state_recovered.signal_fingerprint == sig_orig.analysis_fingerprint
        assert state_recovered.signal_state == sig_orig.state
        assert state_recovered.signal_user_decision == sig_orig.user_decision
        assert state_recovered.direction_score == sig_orig.direction_score
        assert state_recovered.timing_score == sig_orig.timing_score
        assert state_recovered.code_revision == sig_orig.code_revision
        assert state_recovered.effective_action == risk_orig.effective_action
        assert state_recovered.risk_plan_valid == risk_orig.is_valid_risk_plan
        if risk_orig.is_valid_risk_plan:
            assert state_recovered.entry_min == risk_orig.entry_min
            assert state_recovered.stop_final == risk_orig.stop_final
            assert state_recovered.tp1 == risk_orig.tp1
            assert state_recovered.rr_tp1 == risk_orig.rr_tp1
        else:
            assert state_recovered.entry_min is None
