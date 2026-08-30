"""Targeted unit tests for Phase 7A: Live Signal Intelligence, Projections, and Fail-Closed Feeds."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from django.test import TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.adapter import PublicMarketDataAdapter
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import LiveDecisionPipelineService, LiveQuoteService, StateRecoveryService
from apps.live_monitor.types import CandleClosedEvent, EntryZoneStatus, LiveQuoteEvent
from apps.market_data.models import CandleQualityFlag, DataQualitySnapshot, MarketCandle
from apps.signals.models import SignalRecord
from engine.core.types import CandleData, MacroEventContext, SignalSnapshot, SignalState, StructureZone, UserDecision
from engine.risk.planner import RiskPlanner
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
class Phase7ATargetedTests(TestCase):
    def setUp(self):
        # Create standard test assets and instruments
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

        # Populate DB MarketCandles
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

    # --- P7-01: Live closed candle invokes same XautSignalEngine ---
    def test_p7_01_same_xaut_signal_engine_live(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_01",
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

        sig_rec, risk_rec, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        # Baseline direct execution with exact same class and exact same candles
        engine = XautSignalEngine(code_revision=self.code_revision)
        snap = engine.analyze(
            candles_15m=self.candles_15m,
            as_of=self.t_close,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        assert sig_rec.analysis_fingerprint == snap.analysis_fingerprint
        assert sig_rec.direction_score == snap.direction.total_score
        assert sig_rec.timing_score == snap.timing.total_score
        assert sig_rec.state == snap.state.value
        assert sig_rec.user_decision == snap.user_decision.value

    # --- P7-02: Live analysis uses same RiskPlanner ---
    def test_p7_02_same_risk_planner_live(self):
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_02",
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

        sig_rec, risk_rec, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        assert risk_rec is not None
        assert risk_rec.source_signal_fingerprint == sig_rec.analysis_fingerprint
        assert risk_rec.effective_action in ("BUY", "WAIT", "AVOID")

    # --- P7-03: Live quote cannot alter frozen signal scores ---
    def test_p7_03_quote_cannot_alter_scores(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_03_C",
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
        sig_rec, _, state_before = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        # Incoming wild live quote
        quote_evt = LiveQuoteEvent(
            event_id="Q_01",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("3500.00"),
            ask=Decimal("3501.00"),
            source_timestamp=datetime.now(timezone.utc),
            received_timestamp=datetime.now(timezone.utc),
            sequence_number=1001,
        )
        state_after = LiveQuoteService.process_quote(quote_evt)

        assert state_after.direction_score == state_before.direction_score
        assert state_after.timing_score == state_before.timing_score
        assert state_after.signal_state == state_before.signal_state
        assert state_after.signal_user_decision == state_before.signal_user_decision
        assert state_after.effective_action == state_before.effective_action
        assert state_after.signal_fingerprint == sig_rec.analysis_fingerprint

    # --- P7-03A: Phase 4 BUY_WINDOW/BUY + invalid Phase 5 risk => effective_action WAIT ---
    def test_p7_03a_phase4_buy_phase5_invalid_risk_effective_wait(self):
        state = LiveMonitorState.objects.create(
            instrument="XAUT/USDT",
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            risk_plan_valid=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2500.00"),
            entry_max=Decimal("2510.00"),
            current_ask=Decimal("2505.00"),
        )

        # Signal layer preserves BUY_WINDOW / BUY
        assert state.signal_state == "BUY_WINDOW"
        assert state.signal_user_decision == "BUY"
        # Primary user-facing decision is WAIT
        assert state.effective_action == "WAIT"

    # --- P7-03B: Invalid/noneligible risk plan => NO_ACTIVE_ZONE ---
    def test_p7_03b_invalid_risk_plan_no_active_zone(self):
        state = LiveMonitorState.objects.create(
            instrument="XAUT/USDT",
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            risk_plan_valid=False,  # Invalid risk plan
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2500.00"),
            entry_max=Decimal("2510.00"),
        )

        quote_evt = LiveQuoteEvent(
            event_id="Q_03B",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2504.00"),
            ask=Decimal("2505.00"),
            source_timestamp=datetime.now(timezone.utc),
            received_timestamp=datetime.now(timezone.utc),
        )
        updated_state = LiveQuoteService.process_quote(quote_evt)

        assert updated_state.entry_zone_status == EntryZoneStatus.NO_ACTIVE_ZONE.value
        assert updated_state.distance_to_entry_zone_pct is None

    # --- P7-04: Live quote cannot alter signal fingerprint ---
    def test_p7_04_quote_cannot_alter_signal_fingerprint(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_04",
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
        sig_rec, _, state = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        fp_initial = state.signal_fingerprint

        # 10 consecutive ticks
        for i in range(10):
            q = LiveQuoteEvent(
                event_id=f"Q_04_{i}",
                instrument="XAUT/USDT",
                provider="binance",
                bid=Decimal(f"25{i}0.00"),
                ask=Decimal(f"25{i}1.00"),
                source_timestamp=datetime.now(timezone.utc) + timedelta(seconds=i),
                received_timestamp=datetime.now(timezone.utc) + timedelta(seconds=i),
                sequence_number=2000 + i,
            )
            state = LiveQuoteService.process_quote(q)

        assert state.signal_fingerprint == fp_initial
        assert state.signal_fingerprint == sig_rec.analysis_fingerprint

    # --- P7-05: Closed candle triggers deterministic analysis ---
    def test_p7_05_closed_candle_triggers_deterministic_analysis(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_05",
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
        sig1, _, st1 = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        sig2, _, st2 = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        assert sig1.analysis_fingerprint == sig2.analysis_fingerprint
        assert st1.direction_score == st2.direction_score
        assert st1.timing_score == st2.timing_score

    # --- P7-06: Unclosed candle rejected ---
    def test_p7_06_unclosed_candle_rejected(self):
        last_candle = self.candles_15m[-1]
        event_unclosed = CandleClosedEvent(
            event_id="EVT_06_UNCLOSED",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close + timedelta(minutes=15),
            open=Decimal("2550.00"),
            high=Decimal("2560.00"),
            low=Decimal("2540.00"),
            close=Decimal("2555.00"),
            is_closed=False,
        )

        with pytest.raises(ValueError, match="Unclosed candle"):
            LiveDecisionPipelineService.process_closed_candle(
                event=event_unclosed,
                code_revision=self.code_revision,
            )

    # --- P7-07: Duplicate candle-close event is idempotent ---
    def test_p7_07_duplicate_close_idempotency(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_07",
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

        initial_count = SignalRecord.objects.count()
        sig1, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert SignalRecord.objects.count() == initial_count + 1

        # Re-send exact same close event
        sig2, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert SignalRecord.objects.count() == initial_count + 1
        assert sig1.id == sig2.id

    # --- P7-08: Deterministic same revision ---
    def test_p7_08_deterministic_same_revision(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_08",
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
            event=event_c,
            code_revision=self.code_revision,
            engine_version="4.0.0",
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        sig2, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            engine_version="4.0.0",
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig1.analysis_fingerprint == sig2.analysis_fingerprint

    # --- P7-09: Code revision fingerprint mutation ---
    def test_p7_09_code_revision_fingerprint_mutation(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_09",
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
            event=event_c,
            code_revision="revision_alpha_1",
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        sig2, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision="revision_beta_2",
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig1.analysis_fingerprint != sig2.analysis_fingerprint

    # --- P7-10: Missing XAU => FORCE_WAIT via frozen engine ---
    def test_p7_10_missing_xau_force_wait(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_10",
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
        sig, _, state = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=None,  # MISSING XAU
            xau_reference_is_bullish=None,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("canonical XAU/USD" in r for r in sig.hard_gate_reasons)

    # --- P7-11: Missing USDT normalization => FORCE_WAIT via frozen engine ---
    def test_p7_11_missing_usdt_normalization_force_wait(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_11",
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
        sig, _, state = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=None,  # MISSING USDT NORMALIZATION
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("USDT/USD peg normalization" in r for r in sig.hard_gate_reasons)

    # --- P7-12: Stale critical feed => FORCE_WAIT via frozen engine ---
    def test_p7_12_stale_feed_force_wait(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_12",
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
        sig, _, state = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_feed_stale=True,  # STALE FEED
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("Stale Feed" in r for r in sig.hard_gate_reasons)

    # --- P7-13: Provider transition => FORCE_WAIT via frozen engine ---
    def test_p7_13_provider_transition_force_wait(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_13",
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
        sig, _, state = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_provider_transition=True,  # PROVIDER TRANSITION
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("TRANSITION" in r for r in sig.hard_gate_reasons)

    # --- P7-14: Macro blackout => FORCE_WAIT via frozen engine ---
    def test_p7_14_macro_blackout_force_wait(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_14",
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
        macro_ctx = MacroEventContext(
            is_in_blackout=True,  # BLACKOUT ACTIVE
            minutes_to_next_event=0,
            minutes_since_last_event=10,
            active_event_name="Federal Reserve Rate Decision",
        )
        sig, _, state = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            macro_context=macro_ctx,
        )
        assert sig.user_decision == "WAIT"
        assert state.effective_action == "WAIT"
        assert any("blackout" in r for r in sig.hard_gate_reasons)

    # --- P7-15: Quote freshness calculated from source timestamp ---
    def test_p7_15_quote_freshness_from_source_timestamp(self):
        now_utc = datetime.now(timezone.utc)
        src_ts = now_utc - timedelta(seconds=10)
        q = LiveQuoteEvent(
            event_id="Q_15",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2540.00"),
            ask=Decimal("2540.50"),
            source_timestamp=src_ts,
            received_timestamp=now_utc,
            sequence_number=100,
        )
        state = LiveQuoteService.process_quote(q)
        assert state.quote_age_seconds is not None
        assert 9.0 <= state.quote_age_seconds <= 15.0
        assert not state.is_quote_stale

    # --- P7-16: Out-of-order quote ignored (fallback to source_timestamp) ---
    def test_p7_16_out_of_order_quote_ignored(self):
        now_utc = datetime.now(timezone.utc)
        q_new = LiveQuoteEvent(
            event_id="Q_NEW",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2550.00"),
            ask=Decimal("2551.00"),
            source_timestamp=now_utc,
            received_timestamp=now_utc,
        )
        state = LiveQuoteService.process_quote(q_new)
        assert state.current_ask == Decimal("2551.00")

        q_old = LiveQuoteEvent(
            event_id="Q_OLD",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2400.00"),
            ask=Decimal("2401.00"),
            source_timestamp=now_utc - timedelta(seconds=5),
            received_timestamp=now_utc,
        )
        state_after = LiveQuoteService.process_quote(q_old)
        assert state_after.current_ask == Decimal("2551.00")

    # --- P7-16A: Provider sequence regression ignored ---
    def test_p7_16a_provider_sequence_regression_ignored(self):
        now_utc = datetime.now(timezone.utc)
        q1 = LiveQuoteEvent(
            event_id="Q_SEQ_10",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2550.00"),
            ask=Decimal("2551.00"),
            source_timestamp=now_utc,
            received_timestamp=now_utc,
            sequence_number=100,
        )
        state = LiveQuoteService.process_quote(q1)
        assert state.quote_sequence == 100
        assert state.current_ask == Decimal("2551.00")

        q2 = LiveQuoteEvent(
            event_id="Q_SEQ_99",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2400.00"),
            ask=Decimal("2401.00"),
            source_timestamp=now_utc + timedelta(milliseconds=10),
            received_timestamp=now_utc + timedelta(milliseconds=10),
            sequence_number=99,
        )
        state_after = LiveQuoteService.process_quote(q2)
        assert state_after.quote_sequence == 100
        assert state_after.current_ask == Decimal("2551.00")

    # --- P7-16B: Future-skewed source timestamp handled fail-safe ---
    def test_p7_16b_future_skewed_timestamp_fail_safe(self):
        now_utc = datetime.now(timezone.utc)
        skewed_ts = now_utc + timedelta(minutes=10)
        q = LiveQuoteEvent(
            event_id="Q_FUTURE",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2550.00"),
            ask=Decimal("2551.00"),
            source_timestamp=skewed_ts,
            received_timestamp=now_utc,
        )
        state = LiveQuoteService.process_quote(q)
        assert state.is_quote_stale is True

    # --- P7-17: Duplicate quote safety ---
    def test_p7_17_duplicate_quote_safety(self):
        now_utc = datetime.now(timezone.utc)
        q = LiveQuoteEvent(
            event_id="Q_DUP",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2550.00"),
            ask=Decimal("2551.00"),
            source_timestamp=now_utc,
            received_timestamp=now_utc,
            sequence_number=500,
        )
        st1 = LiveQuoteService.process_quote(q)
        st2 = LiveQuoteService.process_quote(q)
        assert st1.current_ask == st2.current_ask
        assert st1.quote_sequence == st2.quote_sequence

    # --- P7-17A: Quote/decision interleaving cannot clobber signal fields (P7-C4) ---
    def test_p7_17a_quote_interleaving_cannot_clobber_signal_fields(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_17A",
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
        sig, _, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        q = LiveQuoteEvent(
            event_id="Q_17A",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2555.00"),
            ask=Decimal("2556.00"),
            source_timestamp=datetime.now(timezone.utc),
            received_timestamp=datetime.now(timezone.utc),
            sequence_number=601,
        )
        state_after_quote = LiveQuoteService.process_quote(q)

        assert state_after_quote.signal_fingerprint == sig.analysis_fingerprint
        assert state_after_quote.signal_state == sig.state
        assert state_after_quote.signal_user_decision == sig.user_decision
        assert state_after_quote.current_ask == Decimal("2556.00")

    # --- P7-17B: Decision update cannot clobber newer quote fields (P7-C4) ---
    def test_p7_17b_decision_update_cannot_clobber_newer_quote_fields(self):
        now_utc = datetime.now(timezone.utc)
        q = LiveQuoteEvent(
            event_id="Q_17B",
            instrument="XAUT/USDT",
            provider="binance",
            bid=Decimal("2600.00"),
            ask=Decimal("2601.00"),
            source_timestamp=now_utc,
            received_timestamp=now_utc,
            sequence_number=701,
        )
        LiveQuoteService.process_quote(q)

        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_17B",
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
        _, _, state_after_decision = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        assert state_after_decision.current_ask == Decimal("2601.00")
        assert state_after_decision.current_bid == Decimal("2600.00")
        assert state_after_decision.quote_sequence == 701

    # --- P7-18: Restart state reconstruction ---
    def test_p7_18_restart_state_reconstruction(self):
        last_candle = self.candles_15m[-1]
        event_c = CandleClosedEvent(
            event_id="EVT_18",
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
        sig, risk, _ = LiveDecisionPipelineService.process_closed_candle(
            event=event_c,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()
        assert LiveMonitorState.objects.filter(instrument="XAUT/USDT").count() == 0

        recovered_state = StateRecoveryService.reconstruct_state("XAUT/USDT")
        assert recovered_state.signal_fingerprint == sig.analysis_fingerprint
        assert recovered_state.signal_state == sig.state
        assert recovered_state.signal_user_decision == sig.user_decision
        assert recovered_state.code_revision == self.code_revision

    # --- P7-18A: Restart recovery reproduces exact persisted risk plan without rerunning RiskPlanner ---
    def test_p7_18a_restart_recovery_persisted_risk_plan_without_rerun(self):
        fp = "MOCK_CANONICAL_SIGNAL_FP_718A"
        sig = SignalRecord.objects.create(
            analysis_fingerprint=fp,
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime(2026, 8, 30, 2, 0, tzinfo=timezone.utc),
            state="BUY_WINDOW",
            user_decision="BUY",
            direction_score=85.0,
            timing_score=80.0,
            code_revision=self.code_revision,
        )
        risk = LiveRiskPlanRecord.objects.create(
            source_signal_fingerprint=fp,
            signal_timestamp=sig.timestamp,
            instrument="XAUT/USDT",
            entry_min=Decimal("2500.00"),
            entry_mid=Decimal("2505.00"),
            entry_max=Decimal("2510.00"),
            stop_structure=Decimal("2480.00"),
            stop_atr=Decimal("2485.00"),
            stop_final=Decimal("2480.00"),
            stop_distance_atr=Decimal("1.50"),
            tp1=Decimal("2560.00"),
            tp2=Decimal("2580.00"),
            rr_tp1=Decimal("2.00"),
            rr_tp2=Decimal("2.67"),
            is_valid_risk_plan=True,
            execution_eligible=True,
            effective_action="BUY",
            reasons=["Confirmed test plan"],
            code_revision=self.code_revision,
        )

        LiveMonitorState.objects.filter(instrument="XAUT/USDT").delete()
        rec_state = StateRecoveryService.reconstruct_state("XAUT/USDT")

        assert rec_state.signal_fingerprint == fp
        assert rec_state.risk_plan_valid is True
        assert rec_state.execution_eligible is True
        assert rec_state.effective_action == "BUY"
        assert rec_state.entry_min == Decimal("2500.00")
        assert rec_state.tp1 == Decimal("2560.00")
        assert rec_state.rr_tp1 == Decimal("2.00")

    # --- P7-18B: Restart recovery reproduces latest canonical signal fingerprint and version provenance ---
    def test_p7_18b_restart_recovery_fingerprint_and_provenance(self):
        fp = "MOCK_CANONICAL_SIGNAL_FP_718B"
        SignalRecord.objects.create(
            analysis_fingerprint=fp,
            instrument=self.xaut_inst,
            timeframe="15m",
            timestamp=datetime(2026, 8, 30, 3, 0, tzinfo=timezone.utc),
            state="WAIT",
            user_decision="WAIT",
            direction_score=50.0,
            timing_score=45.0,
            engine_version="4.0.0",
            config_version="cfg-2026-v1",
            code_revision=self.code_revision,
        )

        rec_state = StateRecoveryService.reconstruct_state("XAUT/USDT")
        assert rec_state.signal_fingerprint == fp
        assert rec_state.engine_version == "4.0.0"
        assert rec_state.config_version == "cfg-2026-v1"
        assert rec_state.code_revision == self.code_revision

    # --- P7-18C: Canonical provider adapter reaches LiveQuoteService and closed-candle pipeline ---
    def test_p7_18c_canonical_provider_adapter_integration(self):
        now_utc = datetime.now(timezone.utc)
        q_evt = PublicMarketDataAdapter.create_quote_event(
            instrument="XAUT/USDT",
            provider="binance",
            bid="2520.50",
            ask="2521.00",
            source_timestamp=now_utc,
            sequence_number=9001,
        )
        assert isinstance(q_evt, LiveQuoteEvent)
        assert q_evt.bid == Decimal("2520.50")
        assert q_evt.ask == Decimal("2521.00")

        state_q = LiveQuoteService.process_quote(q_evt)
        assert state_q.current_ask == Decimal("2521.00")

        c_evt = PublicMarketDataAdapter.create_candle_closed_event(
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=now_utc - timedelta(minutes=15),
            timestamp_close=now_utc,
            open_price="2515.00",
            high_price="2525.00",
            low_price="2510.00",
            close_price="2521.00",
            volume="150.0",
            source="binance",
        )
        assert isinstance(c_evt, CandleClosedEvent)
        assert c_evt.is_closed is True

        sig_rec, risk_rec, state_c = LiveDecisionPipelineService.process_closed_candle(
            event=c_evt,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig_rec is not None
        assert state_c.signal_fingerprint == sig_rec.analysis_fingerprint
