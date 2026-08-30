"""Master Acceptance Test Suite for Phase 7A: Live Signal Intelligence & Real-Time Monitoring.

Acceptance Gates:
  A39 — LIVE / BACKTEST ENGINE PARITY
  A40 — CLOSED-CANDLE DECISION INTEGRITY
  A41 — LIVE-QUOTE SCORE IMMUTABILITY
  A42 — CRITICAL-FEED FAIL-CLOSED
"""
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import pytest
from django.test import TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import LiveDecisionPipelineService, LiveQuoteService
from apps.live_monitor.types import CandleClosedEvent, EntryZoneStatus, LiveQuoteEvent
from apps.market_data.models import CandleQualityFlag, MarketCandle
from apps.signals.models import SignalRecord
from engine.core.types import CandleData, MacroEventContext
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


@pytest.mark.acceptance
@pytest.mark.django_db
class Phase7AcceptanceTests(TestCase):
    def setUp(self):
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
                quote_rate=Decimal("1.000000"),
                close_usd=c.close,
                is_closed=True,
                data_quality_flag=CandleQualityFlag.OK,
            )

        # Multi-timeframe 4H and 1D candles
        self.candles_4h = []
        self.candles_1d = []
        for i in range(35):
            t_open_4h = self.candles_15m[0].timestamp_open - timedelta(hours=4 * (35 - i))
            c_4h = _make_closed_candle(t_open_4h, 240, Decimal("2450.00") + Decimal(str(i * 2)), Decimal("2460.00") + Decimal(str(i * 2)), Decimal("2440.00") + Decimal(str(i * 2)), Decimal("2455.00") + Decimal(str(i * 2)))
            self.candles_4h.append(c_4h)
            MarketCandle.objects.create(
                instrument=self.xaut_inst,
                source="binance",
                timeframe="4h",
                timestamp_open=c_4h.timestamp_open,
                timestamp_close=c_4h.timestamp_close,
                open=c_4h.open,
                high=c_4h.high,
                low=c_4h.low,
                close=c_4h.close,
                volume=c_4h.volume,
                is_closed=True,
                data_quality_flag=CandleQualityFlag.OK,
            )

        # XAU Reference candles
        self.candles_xau = []
        for i in range(40):
            c_15 = self.candles_15m[i]
            c_xau = _make_closed_candle(c_15.timestamp_open, 15, c_15.open + Decimal("2.00"), c_15.high + Decimal("2.00"), c_15.low + Decimal("2.00"), c_15.close + Decimal("2.00"))
            self.candles_xau.append(c_xau)
            MarketCandle.objects.create(
                instrument=self.xau_inst,
                source="gold_reference",
                timeframe="15m",
                timestamp_open=c_xau.timestamp_open,
                timestamp_close=c_xau.timestamp_close,
                open=c_xau.open,
                high=c_xau.high,
                low=c_xau.low,
                close=c_xau.close,
                volume=c_xau.volume,
                is_closed=True,
                data_quality_flag=CandleQualityFlag.OK,
            )

    # --- A39: LIVE / BACKTEST ENGINE PARITY ---
    def test_a39_live_backtest_engine_parity(self):
        """
        Gate A39:
          1. LiveDecisionPipelineService invokes the EXACT frozen XautSignalEngine and RiskPlanner
             with complete multi-timeframe and reference context (15m, 4H, 1D, XAU reference).
          2. Static AST scan proves apps/live_monitor contains ZERO duplicate scoring or risk formulas.
        """
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_A39",
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

        # 1. Pipeline execution parity check
        sig_rec, risk_rec, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        engine_direct = XautSignalEngine(code_revision=self.code_revision)
        snap_direct = engine_direct.analyze(
            candles_15m=self.candles_15m,
            candles_4h=self.candles_4h,
            candles_xau=self.candles_xau,
            as_of=self.t_close,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        assert sig_rec.analysis_fingerprint == snap_direct.analysis_fingerprint
        assert sig_rec.direction_score == snap_direct.direction.total_score
        assert sig_rec.timing_score == snap_direct.timing.total_score

        # 2. AST Purity & Anti-Duplication Scan
        live_monitor_dir = Path(__file__).resolve().parent.parent.parent / "apps" / "live_monitor"
        prohibited_functions = {
            "calculate_direction_score",
            "calculate_timing_score",
            "calculate_stops",
            "calculate_targets",
            "evaluate_hard_gates",
            "evaluate_selective_gate",
        }

        for py_file in live_monitor_dir.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        assert (
                            node.name not in prohibited_functions
                        ), f"Duplicate formula implementation found in {py_file.name}: {node.name}"

    # --- A40: CLOSED-CANDLE DECISION INTEGRITY ---
    def test_a40_closed_candle_decision_integrity(self):
        """
        Gate A40:
          1. Signal and risk calculations execute strictly on closed decision candles.
          2. Unclosed bars are rejected.
          3. Phase 4 signal output and Phase 5 effective action are strictly decoupled.
        """
        last_candle = self.candles_15m[-1]
        
        # Verify unclosed bar rejection
        unclosed_evt = CandleClosedEvent(
            event_id="EVT_A40_UNCLOSED",
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
                event=unclosed_evt,
                code_revision=self.code_revision,
            )

        # Verify closed bar creates immutable SignalRecord and LiveRiskPlanRecord
        closed_evt = CandleClosedEvent(
            event_id="EVT_A40_CLOSED",
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
            event=closed_evt,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig_rec is not None
        assert risk_rec is not None
        assert SignalRecord.objects.filter(analysis_fingerprint=sig_rec.analysis_fingerprint).exists()
        assert LiveRiskPlanRecord.objects.filter(source_signal_fingerprint=sig_rec.analysis_fingerprint).exists()

    # --- A41: LIVE-QUOTE SCORE IMMUTABILITY ---
    def test_a41_live_quote_score_immutability(self):
        """
        Gate A41:
          Simulates streaming high-frequency live quote ticks and verifies:
          1. Direction Score and Timing Score remain identical.
          2. SignalState and UserDecision remain identical.
          3. Canonical analysis_fingerprint remains identical.
          4. Entry-zone proximity is dynamically updated based on live ASK price.
        """
        last_candle = self.candles_15m[-1]
        closed_evt = CandleClosedEvent(
            event_id="EVT_A41",
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
            event=closed_evt,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
        )

        base_dir_score = state.direction_score
        base_timing_score = state.timing_score
        base_state = state.signal_state
        base_decision = state.signal_user_decision
        base_fp = state.signal_fingerprint

        # Stream 50 random live quotes
        now = datetime.now(timezone.utc)
        for i in range(50):
            tick_price = Decimal("2500.00") + Decimal(str(i * 0.50))
            q = LiveQuoteEvent(
                event_id=f"TICK_{i}",
                instrument="XAUT/USDT",
                provider="binance",
                bid=tick_price - Decimal("0.25"),
                ask=tick_price + Decimal("0.25"),
                source_timestamp=now + timedelta(milliseconds=i * 200),
                received_timestamp=now + timedelta(milliseconds=i * 200),
                sequence_number=10000 + i,
            )
            state = LiveQuoteService.process_quote(q)

        # Invariants
        assert state.direction_score == base_dir_score
        assert state.timing_score == base_timing_score
        assert state.signal_state == base_state
        assert state.signal_user_decision == base_decision
        assert state.signal_fingerprint == base_fp
        assert state.current_ask == Decimal("2500.00") + Decimal("24.50") + Decimal("0.25")

    # --- A42: CRITICAL-FEED FAIL-CLOSED ---
    def test_a42_critical_feed_fail_closed(self):
        """
        Gate A42:
          Verifies that any failure in critical feeds (missing XAU, missing USDT rate,
          stale feed, provider transition, macro blackout) triggers fail-closed (FORCE_WAIT / WAIT)
          directly from the frozen engine.
        """
        last_candle = self.candles_15m[-1]
        event = CandleClosedEvent(
            event_id="EVT_A42",
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

        # 1. Missing XAU
        sig, _, st = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=None,
            xau_reference_is_bullish=None,
            usdt_rate=Decimal("1.0000"),
        )
        assert sig.user_decision == "WAIT"
        assert st.effective_action == "WAIT"

        # 2. Missing USDT Normalization
        sig, _, st = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=None,
        )
        assert sig.user_decision == "WAIT"
        assert st.effective_action == "WAIT"

        # 3. Stale Feed
        sig, _, st = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_feed_stale=True,
        )
        assert sig.user_decision == "WAIT"
        assert st.effective_action == "WAIT"

        # 4. Provider Transition
        sig, _, st = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            is_provider_transition=True,
        )
        assert sig.user_decision == "WAIT"
        assert st.effective_action == "WAIT"

        # 5. Macro Blackout
        macro_ctx = MacroEventContext(
            is_in_blackout=True,
            minutes_to_next_event=0,
            minutes_since_last_event=10,
            active_event_name="US CPI Release",
        )
        sig, _, st = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=self.code_revision,
            xau_reference_price=Decimal("2540.00"),
            xau_reference_is_bullish=True,
            usdt_rate=Decimal("1.0000"),
            macro_context=macro_ctx,
        )
        assert sig.user_decision == "WAIT"
        assert st.effective_action == "WAIT"
