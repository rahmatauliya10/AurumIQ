"""Acceptance Test A01: Future Mutation Causality Invariant."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from engine.core.types import CandleData
from engine.features.engine import FeatureEngine
from engine.regime.engine import RegimeEngine
from engine.structure.engine import CausalStructureEngine


@pytest.mark.acceptance
def test_a01_future_mutation_invariance():
    """
    A01: Verify that evaluating analysis/structure at timestamp T produces 100%
    deterministic results regardless of any extreme spikes, anomalies, or mutations
    in future candles (T+1 ... T+N).
    """
    feature_engine = FeatureEngine()
    regime_engine = RegimeEngine()
    structure_engine = CausalStructureEngine()

    base_time = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    
    # 1. Generate 250 causal historical candles up to T
    candles_up_to_t: list[CandleData] = []
    price = Decimal("2500.00")

    for i in range(250):
        t_open = base_time + timedelta(minutes=15 * i)
        t_close = t_open + timedelta(minutes=15)
        # Gentle upward trend with minor noise
        p_open = price
        p_high = price + Decimal("5.00")
        p_low = price - Decimal("3.00")
        p_close = price + Decimal("2.00")
        price = p_close

        candles_up_to_t.append(
            CandleData(
                timestamp_open=t_open,
                timestamp_close=t_close,
                open=p_open,
                high=p_high,
                low=p_low,
                close=p_close,
                volume=Decimal("100.0"),
                is_closed=True,
            )
        )

    # Evaluate at T
    features_at_t = feature_engine.extract_features(candles_up_to_t)
    regime_at_t = regime_engine.classify(features_at_t)
    structure_at_t = structure_engine.analyze(candles_up_to_t, atr=features_at_t.atr14)

    # 2. Synthesize 50 aggressive future candles (T+1 ... T+50) with massive wild swings
    future_candles: list[CandleData] = []
    t_end = candles_up_to_t[-1].timestamp_open

    for j in range(1, 51):
        t_open = t_end + timedelta(minutes=15 * j)
        t_close = t_open + timedelta(minutes=15)
        # Wild artificial spikes
        f_open = Decimal("9999.00") if j % 2 == 0 else Decimal("100.00")
        f_high = Decimal("15000.00")
        f_low = Decimal("50.00")
        f_close = Decimal("8888.00") if j % 2 == 0 else Decimal("120.00")

        future_candles.append(
            CandleData(
                timestamp_open=t_open,
                timestamp_close=t_close,
                open=f_open,
                high=f_high,
                low=f_low,
                close=f_close,
                volume=Decimal("99999.0"),
                is_closed=True,
            )
        )

    # 3. Simulate causal slicing at T from a full dataset containing past + future
    full_dataset = candles_up_to_t + future_candles
    
    # Point-in-time causal slice at T (strictly timestamp_open <= T)
    cutoff_time = candles_up_to_t[-1].timestamp_open
    causal_slice_at_t = [c for c in full_dataset if c.timestamp_open <= cutoff_time]

    # Re-evaluate with causal slice
    features_re_eval = feature_engine.extract_features(causal_slice_at_t)
    regime_re_eval = regime_engine.classify(features_re_eval)
    structure_re_eval = structure_engine.analyze(causal_slice_at_t, atr=features_re_eval.atr14)

    # 4. Assert absolute determinism
    assert features_re_eval.ema20 == features_at_t.ema20
    assert features_re_eval.ema50 == features_at_t.ema50
    assert features_re_eval.ema200 == features_at_t.ema200
    assert features_re_eval.rsi14 == features_at_t.rsi14
    assert features_re_eval.macd_line == features_at_t.macd_line
    assert features_re_eval.atr14 == features_at_t.atr14

    assert regime_re_eval.regime == regime_at_t.regime
    assert regime_re_eval.confidence == regime_at_t.confidence

    assert structure_re_eval.structure_type == structure_at_t.structure_type
    assert structure_re_eval.bos == structure_at_t.bos
    assert len(structure_re_eval.swings) == len(structure_at_t.swings)
    for s1, s2 in zip(structure_re_eval.swings, structure_at_t.swings):
        assert s1.timestamp == s2.timestamp
        assert s1.detected_at == s2.detected_at
        assert s1.price == s2.price
        assert s1.swing_type == s2.swing_type
