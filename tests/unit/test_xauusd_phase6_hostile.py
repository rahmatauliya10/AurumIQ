"""Hostile edge-case and regression tests for Phase 6 XAUUSD backtest engine."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import pytest

from engine.backtest.clock import ReplayClock
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_fingerprint import (
    compute_xauusd_backtest_fingerprint,
    compute_xauusd_dataset_identity,
    compute_xauusd_walkforward_fingerprint,
)
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
    XauUsdWalkForwardConfig,
)
import engine.backtest.xauusd_outcomes as xauusd_outcomes_mod
import engine.backtest.xauusd_replay as xauusd_replay_mod
import engine.backtest.xauusd_types as xauusd_types_mod
from engine.core.types import (
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RiskSide,
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
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideTimingPolicy,
    uncalibrated_xauusd_signal_profile,
)


@pytest.fixture
def calibrated_risk_profile():
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
    return Phase4SignalProfile(
        name="XAUUSD_TEST_CALIBRATED",
        long_direction=SideDirectionPolicy(
            weight_regime=20.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=10.0,
            weight_structure_bos=10.0,
            weight_pullback=10.0,
            weight_momentum=5.0,
            weight_volume=5.0,
        ),
        short_direction=SideDirectionPolicy(
            weight_regime=20.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=10.0,
            weight_structure_bos=10.0,
            weight_pullback=10.0,
            weight_momentum=5.0,
            weight_volume=5.0,
        ),
        long_timing=SideTimingPolicy(
            weight_entry_zone=30.0,
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=15.0,
            weight_volume_response=10.0,
        ),
        short_timing=SideTimingPolicy(
            weight_entry_zone=30.0,
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=15.0,
            weight_volume_response=10.0,
        ),
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
    )


def make_candle(ts_open: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal, is_closed: bool = True, source_id: str = "TEST") -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=is_closed,
        source_id=source_id,
    )


def test_hostile_future_candle_leakage_prevented():
    """Verify that candles closing after T are strictly invisible at T."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    for i in range(10):
        t_open = eval_t - timedelta(minutes=15 * (10 - i))
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")))

    dataset.add_candle("15m", make_candle(eval_t, Decimal("2700.00"), Decimal("2750.00"), Decimal("2690.00"), Decimal("2740.00")))

    closed_at_t = dataset.get_closed_candles("15m", as_of=eval_t)
    assert len(closed_at_t) == 10
    assert all(c.timestamp_close <= eval_t for c in closed_at_t)


def test_hostile_post_end_time_evidence_mutation_invariance(calibrated_risk_profile, calibrated_signal_profile):
    """Mutating or adding data after spec.end_time must NEVER alter signals, trades, metrics, or run fingerprint."""
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    start_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    end_t = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)

    # 1. Dataset 1 (Clean in-window data)
    ds1 = PointInTimeDataset()
    for i in range(40):
        t_open = start_t - timedelta(hours=5) + timedelta(minutes=15 * i)
        ds1.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")))

    ds_hash = compute_xauusd_dataset_identity(
        candles_15m=ds1.get_closed_candles("15m", as_of=end_t),
        start_time=start_t,
        end_time=end_t,
    )

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=start_t,
        end_time=end_t,
        timeframes=("15m",),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash=ds_hash,
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
        code_revision=code_rev,
        signal_profile=calibrated_signal_profile,
        risk_profile=calibrated_risk_profile,
    )

    runner = XauUsdBacktestRunner()
    m1, t1, s1, fp1 = runner.run_point_in_time(dataset=ds1, spec=spec)

    # 2. Dataset 2: Add wild corrupted candles strictly AFTER end_t
    ds2 = PointInTimeDataset()
    for c in ds1.get_closed_candles("15m", as_of=end_t + timedelta(days=1)):
        ds2.add_candle("15m", c)
    # Wild data after end_t
    for i in range(20):
        t_wild = end_t + timedelta(minutes=15 * (i + 1))
        ds2.add_candle("15m", make_candle(t_wild, Decimal("9999.00"), Decimal("9999.00"), Decimal("9999.00"), Decimal("9999.00")))

    m2, t2, s2, fp2 = runner.run_point_in_time(dataset=ds2, spec=spec)

    # Invariant: Output must be 100% identical
    assert fp1 == fp2
    assert len(s1) == len(s2)
    assert len(t1) == len(t2)
    assert m1.trade_count == m2.trade_count
    assert m1.net_expectancy_r == m2.net_expectancy_r


def test_hostile_uncalibrated_policy_fails_closed(calibrated_risk_profile):
    """Uncalibrated policy must fail closed and return invalid risk / zero execution."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    uncal_planner = XauUsdRiskPlanner(code_revision="rev", risk_profile=uncalibrated_xauusd_risk_profile())

    sig = DualSideSignalSnapshot(
        timestamp=eval_t,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=SideDirectionScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_direction=SideDirectionScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        long_timing=SideTimingScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_timing=SideTimingScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        hard_gate=XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth()),
        reasons_long_positive=(),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="",
        candidate_resolution_reason="",
        publication_reason="",
        analysis_fingerprint="sig-long",
        phase4_policy_fingerprint="p4",
        code_revision="rev",
        profile_name="XAUUSD",
        calibration_status="CANDIDATE",
    )

    support = StructureZone("SUPPORT", Decimal("2600.00"), Decimal("2605.00"), eval_t - timedelta(hours=2), 2, True)
    struct = StructureResult(eval_t, StructureType.HH, None, None, None, (), (support,))

    plan = uncal_planner.plan_long(phase4_snapshot=sig, structure_15m=struct, atr14=Decimal("3.00"))
    assert plan.is_valid_risk_plan is False
    assert plan.execution_eligible is False


def test_hostile_atr_unavailable_fails_closed(calibrated_risk_profile):
    """When ATR14 is None or <= 0, risk planning must fail closed without synthetic fallback."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    planner = XauUsdRiskPlanner(code_revision="rev", risk_profile=calibrated_risk_profile)

    sig = DualSideSignalSnapshot(
        timestamp=eval_t,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=SideDirectionScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_direction=SideDirectionScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        long_timing=SideTimingScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_timing=SideTimingScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        hard_gate=XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth()),
        reasons_long_positive=(),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="",
        candidate_resolution_reason="",
        publication_reason="",
        analysis_fingerprint="sig-long",
        phase4_policy_fingerprint="p4",
        code_revision="rev",
        profile_name="XAUUSD",
        calibration_status="CANDIDATE",
    )

    support = StructureZone("SUPPORT", Decimal("2600.00"), Decimal("2605.00"), eval_t - timedelta(hours=2), 2, True)
    struct = StructureResult(eval_t, StructureType.HH, None, None, None, (), (support,))

    # None ATR
    plan_none = planner.plan_long(phase4_snapshot=sig, structure_15m=struct, atr14=None)
    assert plan_none.is_valid_risk_plan is False

    # Zero ATR
    plan_zero = planner.plan_long(phase4_snapshot=sig, structure_15m=struct, atr14=Decimal("0.0"))
    assert plan_zero.is_valid_risk_plan is False


def test_hostile_naive_timestamp_strictly_rejected():
    """Naive timestamps must raise ValueError."""
    naive_t = datetime(2026, 9, 1, 10, 0)  # No tzinfo

    with pytest.raises(ValueError, match="must be timezone-aware"):
        XauUsdBacktestRunSpec(
            instrument="XAUUSD",
            start_time=naive_t,
            end_time=datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
            timeframes=("15m",),
            cost_config=XauUsdCostConfig.idealized(),
            cost_scenario=XauUsdCostScenario.IDEALIZED,
            dataset_hash="hash",
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=2,
            code_revision="rev",
        )

    with pytest.raises(ValueError, match="must be timezone-aware"):
        XauUsdBacktestRunSpec(
            instrument="XAUUSD",
            start_time=naive_t,
            end_time=datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
            timeframes=("15m",),
            cost_config=XauUsdCostConfig.idealized(),
            cost_scenario=XauUsdCostScenario.IDEALIZED,
            dataset_hash="hash",
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=2,
            code_revision="rev",
        )

    from engine.backtest.xauusd_outcomes import _require_utc
    with pytest.raises(ValueError, match="must be timezone-aware"):
        _require_utc(naive_t)


def test_hostile_unresolved_trade_semantics(calibrated_risk_profile):
    """UNRESOLVED trade must have exit_price=None, exit_timestamp=None, and proper dependency_end."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    outcome_engine = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
        code_revision="rev",
        execution_policy_config=calibrated_risk_profile.long_execution_policy,
        phase5_policy_fingerprint="p5_fp",
    )

    sig = DualSideSignalSnapshot(
        timestamp=eval_t,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=SideDirectionScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_direction=SideDirectionScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        long_timing=SideTimingScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_timing=SideTimingScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        hard_gate=XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth()),
        reasons_long_positive=(),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="",
        candidate_resolution_reason="",
        publication_reason="",
        analysis_fingerprint="sig-long",
        phase4_policy_fingerprint="p4",
        code_revision="rev",
        profile_name="XAUUSD",
        calibration_status="CANDIDATE",
    )

    support = StructureZone("SUPPORT", Decimal("2600.00"), Decimal("2605.00"), eval_t - timedelta(hours=2), 2, True)
    resistance = StructureZone("RESISTANCE", Decimal("2635.00"), Decimal("2640.00"), eval_t - timedelta(hours=2), 2, True)
    struct = StructureResult(eval_t, StructureType.HH, None, None, None, (), (support, resistance))

    planner = XauUsdRiskPlanner(code_revision="rev", risk_profile=calibrated_risk_profile)
    plan = planner.plan_long(phase4_snapshot=sig, structure_15m=struct, atr14=Decimal("3.00"))
    assert plan.is_valid_risk_plan is True

    # Single future candle that fills on open (2605.00) at 10:15 but neither touches TP nor SL
    future_c = make_candle(eval_t + timedelta(minutes=15), Decimal("2605.00"), Decimal("2606.00"), Decimal("2604.00"), Decimal("2605.00"))

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[future_c],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="t-unres",
    )

    assert trade.outcome == XauUsdTradeOutcome.UNRESOLVED
    assert trade.exit_price is None
    assert trade.exit_timestamp is None
    assert trade.gross_r is None
    assert trade.net_r is None
    assert trade.dependency_end_timestamp is not None


def test_hostile_empirical_zero_friction_rejected():
    """EMPIRICAL mode with zero friction must raise ValueError."""
    with pytest.raises(ValueError, match="EMPIRICAL cost configuration requires explicit non-zero friction"):
        XauUsdCostConfig.empirical(
            entry_fee_bps=Decimal("0.0"),
            exit_fee_bps=Decimal("0.0"),
            synthetic_spread_bps=Decimal("0.0"),
            entry_slippage_bps=Decimal("0.0"),
            exit_slippage_bps=Decimal("0.0"),
        )


def test_hostile_legacy_defaults_audit():
    """Audit source files to ensure no hardcoded 96-bar defaults or USDT dependencies in Phase 6."""
    outcomes_src = inspect.getsource(xauusd_outcomes_mod)
    replay_src = inspect.getsource(xauusd_replay_mod)
    types_src = inspect.getsource(xauusd_types_mod)

    all_code = outcomes_src + replay_src + types_src

    assert "96" not in all_code
    assert "XAUT" not in all_code
    assert "USDT" not in all_code
    assert "Tether" not in all_code
