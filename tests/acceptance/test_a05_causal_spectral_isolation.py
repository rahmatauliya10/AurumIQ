"""Acceptance Test A05: Causal Spectral Isolation & Public API Point-in-Time Invariance."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
import pytest

from engine.core.types import CandleData
from engine.cycles.experimental.engine import ExperimentalTimeCycleEngine


@pytest.mark.acceptance
def test_a05_causal_spectral_isolation_under_future_mutation():
    """
    A05 & P3B-19: Causal spectral calculation via public Engine API with as_of.
    
    Verification Workflow:
      1. Provide full 128-candle sequence to engine.analyze(candles=all_candles, as_of=T).
      2. Mutate future candles (bars 64..127) aggressively.
      3. Pass full mutated 128-candle sequence to engine.analyze(candles=mutated_candles, as_of=T).
      4. Assert 100% numerical and structural invariance at timestamp T without external slicing!
    """
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    base_price = 2500.0
    period = 16.0

    original_candles = []
    for i in range(128):
        cycle_val = 15.0 * math.sin(2.0 * math.pi * i / period)
        noise = 0.5 * math.sin(2.0 * math.pi * i / 4.0)
        p = base_price + (i * 0.2) + cycle_val + noise
        c_open = Decimal(str(round(p - 1.0, 2)))
        c_close = Decimal(str(round(p, 2)))
        c_high = Decimal(str(round(p + 2.0, 2)))
        c_low = Decimal(str(round(p - 2.0, 2)))
        c_vol = Decimal("100.0")
        ts_open = t0 + timedelta(minutes=15 * i)
        ts_close = t0 + timedelta(minutes=15 * (i + 1))
        original_candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=c_open,
                high=c_high,
                low=c_low,
                close=c_close,
                volume=c_vol,
                is_closed=True,
            )
        )

    # Cutoff time T at closed bar 63
    T = original_candles[63].timestamp_close

    engine = ExperimentalTimeCycleEngine(experimental_version="3.1.0-3B")
    # Pass ALL 128 original candles with as_of=T
    snapshot_base = engine.analyze(
        candles=original_candles,
        as_of=T,
        timeframe="15m",
        effective_n=50.0,
    )

    # 3. Aggressively mutate future candles (index 64 to 127)
    mutated_candles = list(original_candles)
    for i in range(64, 128):
        wild_p = base_price * 10.0 + (i * 50.0) + (1000.0 if i % 2 == 0 else -1000.0)
        ts_open = t0 + timedelta(minutes=15 * i)
        ts_close = t0 + timedelta(minutes=15 * (i + 1))
        mutated_candles[i] = CandleData(
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open=Decimal(str(wild_p)),
            high=Decimal(str(wild_p + 100.0)),
            low=Decimal(str(wild_p - 100.0)),
            close=Decimal(str(wild_p + 10.0)),
            volume=Decimal("99999.0"),
            is_closed=True,
        )

    # 4. Pass ALL 128 mutated candles to engine.analyze(as_of=T)
    snapshot_re = engine.analyze(
        candles=mutated_candles,
        as_of=T,
        timeframe="15m",
        effective_n=50.0,
    )

    # 5. Assert 100% Invariance across all spectral metrics
    assert snapshot_base.timestamp == snapshot_re.timestamp == T
    assert snapshot_base.acf.dominant_lag == snapshot_re.acf.dominant_lag
    assert snapshot_base.acf.autocorrelation == snapshot_re.acf.autocorrelation
    assert snapshot_base.acf.acf_series == snapshot_re.acf.acf_series

    assert snapshot_base.fft.dominant_period == snapshot_re.fft.dominant_period
    assert snapshot_base.fft.power_ratio == snapshot_re.fft.power_ratio
    assert snapshot_base.fft.spectral_entropy == snapshot_re.fft.spectral_entropy

    assert snapshot_base.wavelet.dominant_scale_period == snapshot_re.wavelet.dominant_scale_period
    assert snapshot_base.wavelet.energy_ratio == snapshot_re.wavelet.energy_ratio

    assert snapshot_base.hilbert.instantaneous_phase == snapshot_re.hilbert.instantaneous_phase
    assert snapshot_base.hilbert.phase_stability == snapshot_re.hilbert.phase_stability

    assert snapshot_base.reliability.reliability_score == snapshot_re.reliability.reliability_score
    assert snapshot_base.production_weight == 0.0
    assert snapshot_re.production_weight == 0.0
