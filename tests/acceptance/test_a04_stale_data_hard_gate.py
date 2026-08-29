"""
Acceptance Test A04: Stale Data Hard Gate.

Invariant:
  - When market feed delay exceeds threshold (stale data) or provider is in TRANSITION,
    the Selective Gate strictly overrides numerical scores to FORCE_WAIT.
  - user_decision is strictly WAIT.
  - High Direction Score (e.g. 90.0) cannot override the stale hard gate.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    SessionContext,
    SessionType,
    SwingDurationContext,
    MacroEventContext,
    CalendarSeasonalityContext,
    SampleQuality,
    SignalState,
    UserDecision,
)
from engine.signals.engine import XautSignalEngine


def generate_bullish_candles(length: int = 64) -> list[CandleData]:
    base_time = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(length):
        p = 2500.0 + float(i) * 1.5
        ts_open = base_time + timedelta(minutes=15 * i)
        ts_close = base_time + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 3.0, 2))),
                low=Decimal(str(round(p - 1.0, 2))),
                close=Decimal(str(round(p + 2.0, 2))),
                volume=Decimal("200.0"),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.unit
def test_a04_stale_data_hard_gate():
    candles = generate_bullish_candles(64)
    T = candles[-1].timestamp_close

    cycle_3a = Cycle3ASnapshot(
        timestamp=T,
        session=SessionContext(SessionType.LONDON_NY_OVERLAP, 50.0, True, {}, 85.0, SampleQuality.HIGH, 100.0),
        swing_duration=SwingDurationContext(10, 2.5, 8, 2.0, 50.0, True, 80.0, SampleQuality.HIGH, 100.0),
        macro_event=MacroEventContext(False, 120, 180, None, None, True),
        calendar=CalendarSeasonalityContext(2, "Wednesday", 14, 8, False, 80.0, 80.0, SampleQuality.HIGH, 100.0),
        is_blocked_by_event=False,
        cycle_score_3a=82.0,
    )

    engine = XautSignalEngine(code_revision="eae30005")

    # Case A: Healthy feed -> Can qualify for BUY_WINDOW / READY
    snap_healthy = engine.analyze(
        candles_15m=candles,
        as_of=T,
        xau_reference_price=Decimal("2590.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
        is_feed_stale=False,
        cycle_3a=cycle_3a,
    )
    assert snap_healthy.hard_gate.is_blocked is False

    # Case B: Stale feed -> Score is high, but Gate forces FORCE_WAIT
    snap_stale = engine.analyze(
        candles_15m=candles,
        as_of=T,
        xau_reference_price=Decimal("2590.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
        is_feed_stale=True,  # Stale data condition
        cycle_3a=cycle_3a,
    )

    assert snap_stale.hard_gate.is_blocked is True
    assert snap_stale.hard_gate.is_stale_data is True
    assert snap_stale.state == SignalState.FORCE_WAIT
    assert snap_stale.user_decision == UserDecision.WAIT
    assert any("Stale Feed" in r for r in snap_stale.hard_gate_reasons)
