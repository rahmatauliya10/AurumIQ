"""
Acceptance Test A23: Live Quote Score Immutability.

Invariant:
  - Real-time ticker/quote fluctuations and unclosed price updates do not alter
    the Direction Score, Timing Score, SignalState, or analysis_fingerprint
    derived from closed historical production inputs.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import CandleData
from engine.signals.engine import XautSignalEngine


def generate_candle_series(length: int = 64) -> list[CandleData]:
    base_time = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(length):
        p = 2500.0 + float(i)
        ts_open = base_time + timedelta(minutes=15 * i)
        ts_close = base_time + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 2.0, 2))),
                low=Decimal(str(round(p - 1.0, 2))),
                close=Decimal(str(round(p, 2))),
                volume=Decimal("100.0"),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.unit
def test_a23_live_quote_score_immutability():
    closed_candles = generate_candle_series(64)
    T = closed_candles[-1].timestamp_close

    engine = XautSignalEngine()

    # 1. Closed-candle baseline evaluation
    snap_baseline = engine.analyze(
        candles_15m=closed_candles,
        as_of=T,
        xau_reference_price=Decimal("2560.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )

    # 2. Simulate live intrabar unclosed ticker updates
    # Live tick arrives at T+5m with wild price fluctuation (e.g. $3000)
    unclosed_live_candle = CandleData(
        timestamp_open=T,
        timestamp_close=T + timedelta(minutes=15),
        open=Decimal("2564.0"),
        high=Decimal("3000.0"),
        low=Decimal("2400.0"),
        close=Decimal("2950.0"),
        volume=Decimal("5000.0"),
        is_closed=False,  # Unclosed live bar
    )

    all_candles_with_live = list(closed_candles) + [unclosed_live_candle]

    snap_recalc = engine.analyze(
        candles_15m=all_candles_with_live,
        as_of=T,  # Point-in-time boundary remains T
        xau_reference_price=Decimal("2560.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )

    # Invariants: Scores and fingerprints remain 100% identical
    assert snap_baseline.direction.total_score == snap_recalc.direction.total_score
    assert snap_baseline.timing.total_score == snap_recalc.timing.total_score
    assert snap_baseline.state == snap_recalc.state
    assert snap_baseline.user_decision == snap_recalc.user_decision
    assert snap_baseline.analysis_fingerprint == snap_recalc.analysis_fingerprint
