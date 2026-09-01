"""Unit tests for Phase 6 XAUUSD Ablation Engine and Baseline Immutability."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_ablation import XauUsdAblationEngine
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity
from engine.backtest.xauusd_types import (
    XauUsdAblationType,
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
)
from engine.core.types import (
    CandleData,
    FeedCriticality,
)


def make_candle(ts_open: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=True,
        source_id="TEST",
    )


def test_ablation_profile_generation():
    """Test that creating ablated profiles alters only the targeted factor."""
    ab_engine = XauUsdAblationEngine()

    # 1. Baseline
    p_base = ab_engine.create_ablated_profile(XauUsdAblationType.BASELINE)
    assert p_base.long_direction.weight_regime is None or p_base.long_direction.weight_regime >= 0.0

    # 2. NO_REGIME_FILTER
    p_no_reg = ab_engine.create_ablated_profile(XauUsdAblationType.NO_REGIME_FILTER)
    assert p_no_reg.long_direction.weight_regime == 0.0
    assert p_no_reg.short_direction.weight_regime == 0.0

    # 3. NO_STRUCTURE_COMPONENT
    p_no_struct = ab_engine.create_ablated_profile(XauUsdAblationType.NO_STRUCTURE_COMPONENT)
    assert p_no_struct.long_direction.weight_structure_bos == 0.0
    assert p_no_struct.short_direction.weight_structure_bos == 0.0

    # 4. NO_MTF_TREND
    p_no_mtf = ab_engine.create_ablated_profile(XauUsdAblationType.NO_MTF_TREND)
    assert p_no_mtf.long_direction.weight_trend_4h == 0.0
    assert p_no_mtf.long_direction.weight_trend_1d == 0.0

    # 5. NO_MACRO_BLACKOUT
    p_no_macro = ab_engine.create_ablated_profile(XauUsdAblationType.NO_MACRO_BLACKOUT)
    assert p_no_macro.feed_policy.macro_blackout == FeedCriticality.OPTIONAL


def test_ablation_baseline_immutability():
    """Test that running ablations does not mutate baseline run output."""
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    for i in range(60):
        t_open = start_t + timedelta(minutes=15 * i)
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")))

    ds_hash = compute_xauusd_dataset_identity(
        candles_15m=dataset.get_closed_candles("15m", as_of=end_t),
        start_time=start_t,
        end_time=end_t,
    )

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=start_t + timedelta(hours=2),
        end_time=end_t - timedelta(hours=2),
        timeframes=("15m",),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash=ds_hash,
        holding_horizon_bars_15m=5,
        code_revision="46e388a106b9bdc388e646c73570e7879142c837",
    )

    ab_engine = XauUsdAblationEngine()
    report = ab_engine.run_ablation(
        dataset=dataset,
        baseline_spec=spec,
        ablation_types=[
            XauUsdAblationType.NO_REGIME_FILTER,
            XauUsdAblationType.NO_STRUCTURE_COMPONENT,
        ],
    )

    assert report.immutability_verified is True
    assert report.baseline_hash != ""
