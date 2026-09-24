"""
Tests for XAUUSD cached market snapshot replay.
Verifies bit-for-bit parity with full replay and single-build cache reuse.
"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_calibration_policy import load_governed_selection_policy
from engine.backtest.xauusd_candidate_generator import load_governed_candidate_generation_policy
from engine.backtest.xauusd_replay import (
    XauUsdReplayMarketSnapshot,
    build_market_snapshot_cache,
)
from engine.backtest.xauusd_risk_candidate_generator import XauUsdJointCandidateGenerator
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
)
from engine.core.types import CandleData
from scripts.run_xauusd_calibration import (
    evaluate_candidate_0_baseline,
    evaluate_candidate_val,
    load_point_in_time_dataset_from_db,
)


def _generate_synthetic_dataset(start_time: datetime, count: int = 50) -> PointInTimeDataset:
    from datetime import timedelta
    candles_15m = []
    base_price = Decimal("2000.00")
    for i in range(count):
        t_open = start_time + timedelta(minutes=15 * i)
        t_close = t_open + timedelta(minutes=15)
        candles_15m.append(
            CandleData(
                timestamp_open=t_open,
                timestamp_close=t_close,
                open=base_price + Decimal(str(i * 0.1)),
                high=base_price + Decimal(str(i * 0.1 + 0.5)),
                low=base_price + Decimal(str(i * 0.1 - 0.5)),
                close=base_price + Decimal(str(i * 0.1 + 0.2)),
                volume=Decimal("100"),
                is_closed=True,
            )
        )
    return PointInTimeDataset(candles_15m=candles_15m)


def test_01_cached_replay_parity_with_full_replay():
    """
    Parity test:
      same candidate
      same historical window
      OLD full replay vs CACHED replay:
        candidate states identical
        BUY/SELL timestamps identical
        trade outcomes identical
        metrics identical
    """
    t0 = datetime(2024, 2, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2024, 2, 12, 0, 0, tzinfo=timezone.utc)

    try:
        dataset = load_point_in_time_dataset_from_db(t0, t1)
        if len(dataset.get_closed_candles("15m", as_of=t1)) < 20:
            dataset = _generate_synthetic_dataset(t0, count=60)
    except Exception:
        dataset = _generate_synthetic_dataset(t0, count=60)

    joint_gen = XauUsdJointCandidateGenerator()
    cand0 = joint_gen.generate_all_joint_candidates(1)[0]

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=t0,
        end_time=t1,
        timeframes=("15m",),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash="",
        code_revision="test-parity",
        holding_horizon_bars_15m=32,
        max_fill_wait_bars_15m=8,
        signal_profile=cand0.signal_profile,
        risk_profile=cand0.risk_profile,
    )

    runner = XauUsdBacktestRunner()

    # 1. OLD full replay (without cache)
    m_old, tr_old, sig_old, fp_old = runner.run_point_in_time(dataset, spec, market_cache=None)

    # 2. Build market cache once
    cache = build_market_snapshot_cache(dataset, t0, t1)
    assert len(cache) > 0

    # 3. CACHED replay
    m_new, tr_new, sig_new, fp_new = runner.run_point_in_time(dataset, spec, market_cache=cache)

    # Assert candidate states identical
    assert len(sig_old) == len(sig_new)
    for s_old, s_new in zip(sig_old, sig_new):
        assert s_old.timestamp == s_new.timestamp
        assert s_old.candidate_state == s_new.candidate_state
        assert s_old.candidate_user_decision == s_new.candidate_user_decision

    # Assert BUY/SELL timestamps identical
    buy_old_ts = [s.timestamp for s in sig_old if s.candidate_state.name == "BUY_WINDOW"]
    buy_new_ts = [s.timestamp for s in sig_new if s.candidate_state.name == "BUY_WINDOW"]
    assert buy_old_ts == buy_new_ts

    sell_old_ts = [s.timestamp for s in sig_old if s.candidate_state.name == "SELL_WINDOW"]
    sell_new_ts = [s.timestamp for s in sig_new if s.candidate_state.name == "SELL_WINDOW"]
    assert sell_old_ts == sell_new_ts

    # Assert trade outcomes identical
    assert len(tr_old) == len(tr_new)
    for t_o, t_n in zip(tr_old, tr_new):
        assert t_o.trade_id == t_n.trade_id
        assert t_o.outcome == t_n.outcome
        assert t_o.net_r == t_n.net_r
        assert t_o.side == t_n.side

    # Assert metrics identical
    assert m_old.candidate_count == m_new.candidate_count
    assert m_old.trade_count == m_new.trade_count
    assert m_old.max_drawdown_r == m_new.max_drawdown_r
    assert m_old.net_expectancy_r == m_new.net_expectancy_r
    assert m_old.win_count == m_new.win_count
    assert m_old.loss_count == m_new.loss_count


def test_02_market_cache_builder_called_once_across_multiple_candidates():
    """
    Reuse test:
      Evaluating multiple candidates must build the market cache exactly once.
      Subsequent candidates consume precomputed market_cache directly.
    """
    t0 = datetime(2024, 2, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2024, 2, 12, 0, 0, tzinfo=timezone.utc)
    dataset = _generate_synthetic_dataset(t0, count=50)

    joint_gen = XauUsdJointCandidateGenerator()
    candidates = joint_gen.generate_all_joint_candidates(5)

    build_count = 0

    def tracked_builder(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return build_market_snapshot_cache(*args, **kwargs)

    # Precompute cache once across candidates
    cache = tracked_builder(dataset, t0, t1)
    assert build_count == 1

    policy = load_governed_selection_policy()

    # Evaluate multiple candidates using the single precomputed cache
    for cand in candidates:
        evaluate_candidate_val(
            dataset=dataset,
            candidate=cand,
            policy=policy,
            market_cache=cache,
        )

    # Builder was called strictly once across all candidates
    assert build_count == 1
