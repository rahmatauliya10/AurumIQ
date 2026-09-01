"""
Official Acceptance Test Suite for Phase 6 XAUUSD Backtest Validation Engine.
Strictly covers official contracts:
  - XAU-P6-01: Historical LONG PIT Replay, Risk Parity, Execution & Normalized R
  - XAU-P6-02: Historical SHORT PIT Replay, Risk Parity, BID Execution & Normalized R
  - XAU-P6-03: Combined Side-Aware Parity, Walk-Forward Purge/Embargo & Ablation Immutability
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.clock import ReplayClock
from engine.backtest.purge import PurgeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_ablation import XauUsdAblationEngine
from engine.backtest.xauusd_fingerprint import (
    compute_xauusd_backtest_fingerprint,
    compute_xauusd_dataset_identity,
)
from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdAblationType,
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
    XauUsdWalkForwardConfig,
)
from engine.backtest.xauusd_walkforward import XauUsdWalkForwardEngine
from engine.core.types import (
    BarrierHitType,
    BosType,
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RegimeType,
    RiskSide,
    SessionType,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    StructureResult,
    StructureType,
    StructureZone,
    UserDecision,
    XauUsdHardGateEvaluation,
    RuntimeFeedHealth,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)
from engine.signals.engine import XauUsdSignalEngine
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideTimingPolicy,
)


@pytest.fixture
def calibrated_risk_profile():
    """Explicit calibrated test risk profile."""
    return XauUsdRiskProfile(
        name="XAUUSD_TEST_CALIBRATED",
        long_risk_policy=SideRiskPolicy(
            structure_buffer=Decimal("1.50"),
            atr_multiplier=Decimal("2.0"),
            max_stop_distance_atr=Decimal("4.0"),
            min_rr_tp1=Decimal("1.80"),
            tp2_atr_multiplier=Decimal("2.5"),
        ),
        short_risk_policy=SideRiskPolicy(
            structure_buffer=Decimal("1.50"),
            atr_multiplier=Decimal("2.0"),
            max_stop_distance_atr=Decimal("4.0"),
            min_rr_tp1=Decimal("1.80"),
            tp2_atr_multiplier=Decimal("2.5"),
        ),
        long_execution_policy=XauUsdExecutionPolicy(
            latency_seconds=1.0,
            synthetic_spread_pct=Decimal("0.02"),
            slippage_pct=Decimal("0.01"),
        ),
        short_execution_policy=XauUsdExecutionPolicy(
            latency_seconds=1.0,
            synthetic_spread_pct=Decimal("0.02"),
            slippage_pct=Decimal("0.01"),
        ),
    )


@pytest.fixture
def calibrated_signal_profile():
    """Explicit calibrated test signal profile."""
    from engine.signals.profile import calibrated_xauusd_signal_profile
    return calibrated_xauusd_signal_profile()


def make_candle(
    tf: str,
    ts_open: datetime,
    o: Decimal,
    h: Decimal,
    l: Decimal,
    c: Decimal,
    v: Decimal = Decimal("100"),
    is_closed: bool = True,
) -> CandleData:
    """Helper creating CandleData."""
    if tf == "15m":
        ts_close = ts_open + timedelta(minutes=15)
    elif tf == "1h":
        ts_close = ts_open + timedelta(hours=1)
    elif tf == "4h":
        ts_close = ts_open + timedelta(hours=4)
    elif tf == "1d":
        ts_close = ts_open + timedelta(days=1)
    elif tf == "1m":
        ts_close = ts_open + timedelta(minutes=1)
    elif tf == "5m":
        ts_close = ts_open + timedelta(minutes=5)
    else:
        ts_close = ts_open + timedelta(minutes=15)

    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_close,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        is_closed=is_closed,
        source_id="TEST",
    )


def make_mock_dual_side_signal(
    candidate_state: SignalState,
    candidate_decision: UserDecision,
    timestamp: datetime,
    side: SignalSide = SignalSide.LONG,
) -> DualSideSignalSnapshot:
    """Helper creating canonical DualSideSignalSnapshot."""
    dir_res = SideDirectionScoreResult(RiskSide.LONG if side == SignalSide.LONG else RiskSide.SHORT, 85.0, 100.0, (), True, True)
    tim_res = SideTimingScoreResult(RiskSide.LONG if side == SignalSide.LONG else RiskSide.SHORT, 85.0, 100.0, (), True, True)
    hg = XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth())
    return DualSideSignalSnapshot(
        timestamp=timestamp,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=candidate_state,
        candidate_user_decision=candidate_decision,
        long_direction=dir_res,
        short_direction=dir_res,
        long_timing=tim_res,
        short_timing=tim_res,
        hard_gate=hg,
        reasons_long_positive=("Bullish momentum confirmed",) if side == SignalSide.LONG else (),
        reasons_long_negative=(),
        reasons_short_positive=("Bearish momentum confirmed",) if side == SignalSide.SHORT else (),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="Production blocked pending Phase 6",
        candidate_resolution_reason="Layer A candidate evaluated",
        publication_reason="Layer B production authority blocked",
        analysis_fingerprint=f"sig-{side.value.lower()}-{timestamp.strftime('%Y%m%d%H%M')}",
        phase4_policy_fingerprint="p4_policy_fp_test",
        code_revision="46e388a106b9bdc388e646c73570e7879142c837",
        profile_name="XAUUSD_TEST",
        calibration_status="CANDIDATE_NOT_FROZEN",
    )


# ==============================================================================
# XAU-P6-01: Historical LONG PIT Replay, Risk Parity, Execution & Normalized R
# ==============================================================================

def test_xau_p6_01_long_contract(calibrated_risk_profile, calibrated_signal_profile):
    """
    Contract XAU-P6-01: Proves complete Point-in-Time causality, Phase 4 & 5 parity,
    causal execution fill, intrabar barrier resolution, and normalized R for LONG replay,
    including end-to-end execution through XauUsdBacktestRunner.
    """
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    eval_time = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    # 1. Build PIT Candle History <= T (Bullish Structure with swing pivots)
    dataset = PointInTimeDataset()
    for i in range(30):
        t_open = eval_time - timedelta(minutes=15 * (30 - i))
        if i < 10:
            p = Decimal("2620.00") - Decimal(str(i * 1.5))
        elif i < 20:
            p = Decimal("2605.00") + Decimal(str((i - 10) * 1.2))
        else:
            p = Decimal("2617.00") - Decimal(str((i - 20) * 0.2))
        dataset.add_candle(
            "15m",
            make_candle("15m", t_open, p, p + Decimal("2.00"), p - Decimal("1.00"), p + Decimal("0.50")),
        )

    # 2. Add Future Post-T Candles (for fill and outcome replay)
    future_start = eval_time
    # Candle 1: Formed immediately post signal
    dataset.add_candle(
        "15m",
        make_candle("15m", future_start, Decimal("2615.00"), Decimal("2616.00"), Decimal("2614.50"), Decimal("2615.00")),
    )
    # Candle 2: Fill bar on open >= earliest_exec_ts & TP1 target reach
    dataset.add_candle(
        "15m",
        make_candle("15m", future_start + timedelta(minutes=15), Decimal("2615.00"), Decimal("2640.00"), Decimal("2614.50"), Decimal("2638.00")),
    )

    # 3. Create Support Zone, Resistance Target & Phase 4 BUY_WINDOW Signal Snapshot
    support_zone = StructureZone("SUPPORT", Decimal("2610.00"), Decimal("2615.00"), eval_time - timedelta(hours=2), 2, True)
    resistance_target = StructureZone("RESISTANCE", Decimal("2635.00"), Decimal("2640.00"), eval_time - timedelta(hours=2), 2, True)
    struct_res = StructureResult(eval_time, StructureType.HH, BosType.BULLISH, None, None, (), (support_zone, resistance_target))

    signal_snap = make_mock_dual_side_signal(
        candidate_state=SignalState.BUY_WINDOW,
        candidate_decision=UserDecision.BUY,
        timestamp=eval_time,
        side=SignalSide.LONG,
    )

    # Invariant: Published Layer B decision is ALWAYS WAIT
    assert signal_snap.user_decision == UserDecision.WAIT
    assert signal_snap.state == SignalState.NO_TRADE

    # 4. Phase 5 LONG Risk Planning Parity
    risk_planner = XauUsdRiskPlanner(
        code_revision=code_rev,
        risk_profile=calibrated_risk_profile,
    )
    risk_plan = risk_planner.plan_long(
        phase4_snapshot=signal_snap,
        structure_15m=struct_res,
        atr14=Decimal("3.00"),
    )

    assert risk_plan.is_valid_risk_plan is True
    assert risk_plan.execution_eligible is True
    assert risk_plan.entry_max == Decimal("2615.00")
    assert risk_plan.stop_final == Decimal("2606.50")
    planned_risk = risk_plan.entry_max - risk_plan.stop_final
    assert planned_risk == Decimal("8.50")

    # 5. Outcome Engine Resolution with Side-Aware Execution & Intrabar Replay
    outcome_engine = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=10,
        max_fill_wait_bars_15m=4,
        code_revision=code_rev,
        execution_policy_config=calibrated_risk_profile.long_execution_policy,
        phase5_policy_fingerprint=risk_planner.policy_fingerprint,
    )
    future_candles = dataset.get_closed_candles("15m", as_of=eval_time + timedelta(hours=2))
    post_t_candles = [c for c in future_candles if c.timestamp_close > eval_time]

    sim_trade = outcome_engine.resolve_trade(
        signal=signal_snap,
        risk_plan=risk_plan,
        future_candles_15m=post_t_candles,
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-long-1",
    )

    # 6. Verify Outcome, Fill, Exit & Normalized R
    assert sim_trade.outcome == XauUsdTradeOutcome.TP1_FIRST
    assert sim_trade.side == SignalSide.LONG
    # SideAware Ask execution: 2615.00 + 0.523 (spread) + 0.2615 (slippage) = 2615.7845
    assert sim_trade.fill_price == Decimal("2615.784500")
    assert sim_trade.exit_price == risk_plan.tp1
    assert sim_trade.planned_risk_amount == planned_risk

    expected_pnl = risk_plan.tp1 - sim_trade.fill_price
    expected_r = (expected_pnl / planned_risk).quantize(Decimal("0.0001"))
    assert sim_trade.gross_r == expected_r
    assert sim_trade.net_r == expected_r
    assert sim_trade.net_r > Decimal("1.80")

    # 7. End-to-End Execution Path via XauUsdBacktestRunner
    for i in range(30):
        t_open_1h = eval_time - timedelta(hours=(30 - i))
        base_1h = Decimal("2550.00") + Decimal(str(i * 2.5))
        dataset.add_candle("1h", make_candle("1h", t_open_1h, base_1h, base_1h + Decimal("4.0"), base_1h - Decimal("1.0"), base_1h + Decimal("3.0")))

    for i in range(25):
        t_open_4h = eval_time - timedelta(hours=4 * (25 - i))
        base_4h = Decimal("2500.00") + Decimal(str(i * 8.0))
        dataset.add_candle("4h", make_candle("4h", t_open_4h, base_4h, base_4h + Decimal("10.0"), base_4h - Decimal("2.0"), base_4h + Decimal("8.0")))

    for i in range(25):
        t_open_1d = eval_time - timedelta(days=(25 - i))
        base_1d = Decimal("2400.00") + Decimal(str(i * 15.0))
        dataset.add_candle("1d", make_candle("1d", t_open_1d, base_1d, base_1d + Decimal("20.0"), base_1d - Decimal("3.0"), base_1d + Decimal("15.0")))

    runner = XauUsdBacktestRunner()
    from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity_from_dataset
    runner_spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=eval_time,
        end_time=eval_time + timedelta(hours=2),
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash="",
        holding_horizon_bars_15m=10,
        max_fill_wait_bars_15m=4,
        code_revision=code_rev,
        signal_profile=calibrated_signal_profile,
        risk_profile=calibrated_risk_profile,
    )
    runner_metrics, runner_trades, runner_signals, runner_fp = runner.run_point_in_time(
        dataset=dataset,
        spec=runner_spec,
    )
    assert runner_fp != ""
    assert any(s.candidate_state == SignalState.BUY_WINDOW and s.candidate_user_decision == UserDecision.BUY for s in runner_signals)
    assert runner_metrics.long_candidate_count >= 1
    assert runner_metrics.long_valid_risk_count >= 1
    long_trades = [t for t in runner_trades if t.side == SignalSide.LONG]
    assert len(long_trades) >= 1
    t0 = long_trades[0]
    assert t0.risk_plan_fingerprint != ""
    assert t0.execution_evidence_fingerprint != ""
    assert t0.fill_price is not None
    assert t0.planned_risk_amount > Decimal("0.0")
    assert t0.outcome == XauUsdTradeOutcome.TP1_FIRST
    assert t0.net_r > Decimal("0.0")
    assert all(s.user_decision == UserDecision.WAIT for s in runner_signals)


# ==============================================================================
# XAU-P6-02: Historical SHORT PIT Replay, Risk Parity, Execution & Normalized R
# ==============================================================================

def test_xau_p6_02_short_contract(calibrated_risk_profile, calibrated_signal_profile):
    """
    Contract XAU-P6-02: Proves complete Point-in-Time causality, Phase 4 & 5 parity,
    causal BID fill execution, intrabar barrier resolution, and normalized R for SHORT replay,
    including end-to-end execution through XauUsdBacktestRunner.
    """
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    eval_time = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)

    # 1. Build PIT Candle History <= T (Bearish Structure with swing pivots)
    dataset = PointInTimeDataset()
    for i in range(30):
        t_open = eval_time - timedelta(minutes=15 * (30 - i))
        if i < 10:
            p = Decimal("2630.00") + Decimal(str(i * 1.5))
        elif i < 20:
            p = Decimal("2645.00") - Decimal(str((i - 10) * 1.2))
        else:
            p = Decimal("2633.00") + Decimal(str((i - 20) * 0.2))
        dataset.add_candle(
            "15m",
            make_candle("15m", t_open, p, p + Decimal("1.00"), p - Decimal("2.00"), p - Decimal("0.50")),
        )

    # 2. Add Future Post-T Candles (for fill and outcome replay)
    future_start = eval_time
    # Candle 1: Formed immediately post signal
    dataset.add_candle(
        "15m",
        make_candle("15m", future_start, Decimal("2635.00"), Decimal("2635.50"), Decimal("2634.00"), Decimal("2635.00")),
    )
    # Candle 2: Fill bar on open >= earliest_exec_ts & TP1 target reach for Short (price drops)
    dataset.add_candle(
        "15m",
        make_candle("15m", future_start + timedelta(minutes=15), Decimal("2635.00"), Decimal("2635.00"), Decimal("2610.00"), Decimal("2612.00")),
    )

    # 3. Create Resistance Zone, Support Target & Phase 4 SELL_WINDOW Signal Snapshot
    resistance_zone = StructureZone("RESISTANCE", Decimal("2635.00"), Decimal("2640.00"), eval_time - timedelta(hours=2), 2, True)
    support_target = StructureZone("SUPPORT", Decimal("2610.00"), Decimal("2615.00"), eval_time - timedelta(hours=2), 2, True)
    struct_res = StructureResult(eval_time, StructureType.LL, BosType.BEARISH, None, None, (), (support_target, resistance_zone))

    signal_snap = make_mock_dual_side_signal(
        candidate_state=SignalState.SELL_WINDOW,
        candidate_decision=UserDecision.SELL,
        timestamp=eval_time,
        side=SignalSide.SHORT,
    )

    assert signal_snap.candidate_state == SignalState.SELL_WINDOW
    assert signal_snap.candidate_user_decision == UserDecision.SELL
    assert signal_snap.user_decision == UserDecision.WAIT
    assert signal_snap.state == SignalState.NO_TRADE

    # 4. Phase 5 SHORT Risk Planning Parity
    risk_planner = XauUsdRiskPlanner(
        code_revision=code_rev,
        risk_profile=calibrated_risk_profile,
    )
    risk_plan = risk_planner.plan_short(
        phase4_snapshot=signal_snap,
        structure_15m=struct_res,
        atr14=Decimal("3.00"),
    )

    assert risk_plan.is_valid_risk_plan is True
    assert risk_plan.execution_eligible is True
    assert risk_plan.entry_min == Decimal("2635.00")
    assert risk_plan.stop_final == Decimal("2643.50")
    planned_risk = risk_plan.stop_final - risk_plan.entry_min
    assert planned_risk == Decimal("8.50")

    # 5. Outcome Engine Resolution with Short Intrabar Replay
    outcome_engine = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=10,
        max_fill_wait_bars_15m=4,
        code_revision=code_rev,
        execution_policy_config=calibrated_risk_profile.short_execution_policy,
        phase5_policy_fingerprint=risk_planner.policy_fingerprint,
    )
    future_candles = dataset.get_closed_candles("15m", as_of=eval_time + timedelta(hours=2))
    post_t_candles = [c for c in future_candles if c.timestamp_close > eval_time]

    sim_trade = outcome_engine.resolve_trade(
        signal=signal_snap,
        risk_plan=risk_plan,
        future_candles_15m=post_t_candles,
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-short-1",
    )

    # 6. Verify Outcome, Short Fill, Short Exit & Normalized R
    assert sim_trade.outcome == XauUsdTradeOutcome.TP1_FIRST
    assert sim_trade.side == SignalSide.SHORT
    # SideAware Bid execution: 2635.00 - 0.527 (spread) - 0.2635 (slippage) = 2634.2095
    assert sim_trade.fill_price == Decimal("2634.209500")
    assert sim_trade.exit_price == risk_plan.tp1
    assert sim_trade.planned_risk_amount == planned_risk

    expected_pnl = sim_trade.fill_price - risk_plan.tp1
    expected_r = (expected_pnl / planned_risk).quantize(Decimal("0.0001"))
    assert sim_trade.gross_r == expected_r
    assert sim_trade.net_r == expected_r
    assert sim_trade.net_r > Decimal("0.00")

    # 7. End-to-End Execution Path via XauUsdBacktestRunner
    for i in range(30):
        t_open_1h = eval_time - timedelta(hours=(30 - i))
        base_1h = Decimal("2700.00") - Decimal(str(i * 2.5))
        dataset.add_candle("1h", make_candle("1h", t_open_1h, base_1h, base_1h + Decimal("1.0"), base_1h - Decimal("4.0"), base_1h - Decimal("3.0")))

    for i in range(25):
        t_open_4h = eval_time - timedelta(hours=4 * (25 - i))
        base_4h = Decimal("2750.00") - Decimal(str(i * 8.0))
        dataset.add_candle("4h", make_candle("4h", t_open_4h, base_4h, base_4h + Decimal("2.0"), base_4h - Decimal("10.0"), base_4h - Decimal("8.0")))

    for i in range(25):
        t_open_1d = eval_time - timedelta(days=(25 - i))
        base_1d = Decimal("2800.00") - Decimal(str(i * 15.0))
        dataset.add_candle("1d", make_candle("1d", t_open_1d, base_1d, base_1d + Decimal("3.0"), base_1d - Decimal("20.0"), base_1d - Decimal("15.0")))

    runner = XauUsdBacktestRunner()
    from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity_from_dataset
    runner_spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=eval_time,
        end_time=eval_time + timedelta(hours=2),
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash="",
        holding_horizon_bars_15m=10,
        max_fill_wait_bars_15m=4,
        code_revision=code_rev,
        signal_profile=calibrated_signal_profile,
        risk_profile=calibrated_risk_profile,
    )
    runner_metrics, runner_trades, runner_signals, runner_fp = runner.run_point_in_time(
        dataset=dataset,
        spec=runner_spec,
    )
    assert runner_fp != ""
    assert any(s.candidate_state == SignalState.SELL_WINDOW and s.candidate_user_decision == UserDecision.SELL for s in runner_signals)
    assert runner_metrics.short_candidate_count >= 1
    assert runner_metrics.short_valid_risk_count >= 1
    short_trades = [t for t in runner_trades if t.side == SignalSide.SHORT]
    assert len(short_trades) >= 1
    t0 = short_trades[0]
    assert t0.risk_plan_fingerprint != ""
    assert t0.execution_evidence_fingerprint != ""
    assert t0.fill_price is not None
    assert t0.planned_risk_amount > Decimal("0.0")
    assert t0.outcome == XauUsdTradeOutcome.TP1_FIRST
    assert t0.net_r > Decimal("0.0")
    assert all(s.user_decision == UserDecision.WAIT for s in runner_signals)


# ==============================================================================
# XAU-P6-03: Combined Side-Aware Parity, Walk-Forward Purge/Embargo & Ablation
# ==============================================================================

def test_xau_p6_03_combined_contract(calibrated_risk_profile, calibrated_signal_profile):
    """
    Contract XAU-P6-03: Proves side-aware reporting, walk-forward chronological folding,
    dependency purging, post-boundary embargo, and component ablation baseline immutability.
    """
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    start_time = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)

    # 1. Build Multi-Hour PIT Dataset
    dataset = PointInTimeDataset()
    total_bars = 80
    for i in range(total_bars):
        t_open = start_time + timedelta(minutes=15 * i)
        base_p = Decimal("2600.00") + Decimal(str((i % 10) * 2.0))
        dataset.add_candle(
            "15m",
            make_candle("15m", t_open, base_p, base_p + Decimal("2.00"), base_p - Decimal("2.00"), base_p + Decimal("0.50")),
        )

    ds_hash = compute_xauusd_dataset_identity(
        candles_15m=dataset.get_closed_candles("15m", as_of=end_time),
        start_time=start_time,
        end_time=end_time,
    )

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=start_time + timedelta(hours=5),
        end_time=end_time - timedelta(hours=2),
        timeframes=("15m",),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash=ds_hash,
        holding_horizon_bars_15m=8,
        max_fill_wait_bars_15m=4,
        code_revision=code_rev,
        signal_profile=calibrated_signal_profile,
        risk_profile=calibrated_risk_profile,
    )

    # 2. Side-Aware Metrics Verification (LONG, SHORT, COMBINED)
    t1 = XauUsdSimulatedTrade(
        trade_id="t-long-1",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="sig-long-1",
        signal_timestamp=start_time + timedelta(hours=6),
        risk_plan_fingerprint="risk-1",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        fill_timestamp=start_time + timedelta(hours=6, minutes=1),
        fill_price=Decimal("2610.00"),
        exit_timestamp=start_time + timedelta(hours=6, minutes=30),
        exit_price=Decimal("2620.00"),
        gross_pnl_per_unit=Decimal("10.00"),
        net_pnl_per_unit=Decimal("10.00"),
        gross_r=Decimal("2.0000"),
        net_r=Decimal("2.0000"),
        dependency_window=(start_time + timedelta(hours=6), start_time + timedelta(hours=6, minutes=30)),
    )

    t2 = XauUsdSimulatedTrade(
        trade_id="t-short-1",
        side=SignalSide.SHORT,
        candidate_state=SignalState.SELL_WINDOW,
        candidate_user_decision=UserDecision.SELL,
        source_signal_fingerprint="sig-short-1",
        signal_timestamp=start_time + timedelta(hours=8),
        risk_plan_fingerprint="risk-2",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        fill_timestamp=start_time + timedelta(hours=8, minutes=1),
        fill_price=Decimal("2620.00"),
        exit_timestamp=start_time + timedelta(hours=8, minutes=30),
        exit_price=Decimal("2610.00"),
        gross_pnl_per_unit=Decimal("10.00"),
        net_pnl_per_unit=Decimal("10.00"),
        gross_r=Decimal("2.0000"),
        net_r=Decimal("2.0000"),
        dependency_window=(start_time + timedelta(hours=8), start_time + timedelta(hours=8, minutes=30)),
    )

    # Trade 3 crosses boundary: signals at 9h, dependency ends at 12h
    t3_crosses = XauUsdSimulatedTrade(
        trade_id="t-long-cross",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="sig-long-cross",
        signal_timestamp=start_time + timedelta(hours=9),
        risk_plan_fingerprint="risk-3",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        fill_timestamp=start_time + timedelta(hours=9, minutes=1),
        fill_price=Decimal("2610.00"),
        exit_timestamp=start_time + timedelta(hours=12),
        exit_price=Decimal("2620.00"),
        gross_pnl_per_unit=Decimal("10.00"),
        net_pnl_per_unit=Decimal("10.00"),
        gross_r=Decimal("2.0000"),
        net_r=Decimal("2.0000"),
        dependency_window=(start_time + timedelta(hours=9), start_time + timedelta(hours=12)),
    )

    all_trades = [t1, t2, t3_crosses]
    signals = [
        make_mock_dual_side_signal(SignalState.BUY_WINDOW, UserDecision.BUY, start_time + timedelta(hours=6), SignalSide.LONG),
        make_mock_dual_side_signal(SignalState.SELL_WINDOW, UserDecision.SELL, start_time + timedelta(hours=8), SignalSide.SHORT),
        make_mock_dual_side_signal(SignalState.BUY_WINDOW, UserDecision.BUY, start_time + timedelta(hours=9), SignalSide.LONG),
    ]

    metrics_comb = XauUsdMetricsCalculator.calculate(signals, all_trades)
    metrics_long = XauUsdMetricsCalculator.calculate(signals, all_trades, filter_side=SignalSide.LONG)
    metrics_short = XauUsdMetricsCalculator.calculate(signals, all_trades, filter_side=SignalSide.SHORT)

    assert metrics_comb.trade_count == 3
    assert metrics_long.trade_count == 2
    assert metrics_short.trade_count == 1
    assert metrics_comb.long_trade_count == 2
    assert metrics_comb.short_trade_count == 1

    # 3. Dependency-Window Purge Verification
    purge_res = PurgeEngine.filter_partition(
        trades=all_trades,
        partition_start=start_time,
        partition_end=start_time + timedelta(hours=10),
        purge_overlapping=True,
    )
    assert len(purge_res.eligible_trades) == 2
    assert len(purge_res.purged_trades) == 1
    assert purge_res.purged_trades[0].trade_id == "t-long-cross"

    # 4. Post-Boundary Embargo Verification
    t4_embargo = XauUsdSimulatedTrade(
        trade_id="t-embargoed",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="sig-emb",
        signal_timestamp=start_time + timedelta(hours=10, minutes=30),
        risk_plan_fingerprint="risk-4",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        dependency_window=(start_time + timedelta(hours=10, minutes=30), start_time + timedelta(hours=11, minutes=30)),
    )
    embargo_res = PurgeEngine.filter_partition(
        trades=[t4_embargo],
        partition_start=start_time + timedelta(hours=10),
        partition_end=start_time + timedelta(hours=15),
        embargo_duration_seconds=3600.0,
        is_post_boundary_segment=True,
    )
    assert len(embargo_res.embargoed_trades) == 1
    assert len(embargo_res.eligible_trades) == 0

    # 5. Component Ablation & Baseline Immutability Verification
    ablation_engine = XauUsdAblationEngine()
    ablation_report = ablation_engine.run_ablation(
        dataset=dataset,
        baseline_spec=spec,
        ablation_types=[
            XauUsdAblationType.NO_REGIME_FILTER,
            XauUsdAblationType.NO_STRUCTURE_COMPONENT,
            XauUsdAblationType.NO_MTF_TREND,
            XauUsdAblationType.NO_PHASE3A_SESSION,
            XauUsdAblationType.NO_PHASE3A_SWING_MATURITY,
            XauUsdAblationType.NO_MACRO_BLACKOUT,
        ],
    )

    assert ablation_report.immutability_verified is True
    assert ablation_report.baseline_hash != ""
    assert len(ablation_report.comparisons) == 6
