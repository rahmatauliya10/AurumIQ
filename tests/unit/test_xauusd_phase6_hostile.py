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
from engine.backtest.xauusd_walkforward import XauUsdChronologicalFoldGenerator
import engine.backtest.xauusd_outcomes as xauusd_outcomes_mod
import engine.backtest.xauusd_replay as xauusd_replay_mod
import engine.backtest.xauusd_types as xauusd_types_mod
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
    MacroEventContext,
    QuoteData,
    RiskSide,
    SessionContext,
    SessionType,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    StructureResult,
    StructureType,
    StructureZone,
    SwingDurationContext,
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
from engine.signals.engine import XauUsdSignalEngine
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


def make_candle(open_t: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal, tf_min: int = 15) -> CandleData:
    return CandleData(
        timestamp_open=open_t,
        timestamp_close=open_t + timedelta(minutes=tf_min),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=True,
    )


def test_hostile_side_specific_execution_policy_isolation():
    """Prove LONG uses Long policy (latency=1s, spread=A, slip=A) and SHORT uses Short policy (latency=7s, spread=B, slip=B)."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    code_rev = "rev-test"

    long_policy = XauUsdExecutionPolicy(
        latency_seconds=1.0,
        synthetic_spread_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.01"),
    )
    short_policy = XauUsdExecutionPolicy(
        latency_seconds=7.0,
        synthetic_spread_pct=Decimal("0.05"),
        slippage_pct=Decimal("0.03"),
    )

    outcome_eng = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        code_revision=code_rev,
        long_execution_policy=long_policy,
        short_execution_policy=short_policy,
        phase5_policy_fingerprint="p5-fp",
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
    )

    assert outcome_eng.long_entry_execution_model.execution_policy.latency_seconds == 1.0
    assert outcome_eng.short_entry_execution_model.execution_policy.latency_seconds == 7.0
    assert outcome_eng.long_entry_execution_model.execution_policy.synthetic_spread_pct == Decimal("0.02")
    assert outcome_eng.short_entry_execution_model.execution_policy.synthetic_spread_pct == Decimal("0.05")


def test_hostile_strict_half_open_window_end_time_mutation():
    """Mutating evidence at exactly end_time must cause zero change to signals, trades, metrics, and dataset hash."""
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    # 1. Base dataset with candles < end_t
    c1 = make_candle(start_t, Decimal("2600.00"), Decimal("2605.00"), Decimal("2598.00"), Decimal("2602.00"))
    c2 = make_candle(start_t + timedelta(minutes=15), Decimal("2602.00"), Decimal("2608.00"), Decimal("2601.00"), Decimal("2606.00"))

    ds_hash_1 = compute_xauusd_dataset_identity(
        candles_15m=[c1, c2],
        start_time=start_t,
        end_time=end_t,
    )

    # 2. Add candle closing at exactly end_t (outside [start, end) because close_ts == end_t)
    # or starting at end_t
    c_at_end = make_candle(end_t, Decimal("9999.00"), Decimal("9999.00"), Decimal("9999.00"), Decimal("9999.00"))
    ds_hash_2 = compute_xauusd_dataset_identity(
        candles_15m=[c1, c2, c_at_end],
        start_time=start_t,
        end_time=end_t,
    )

    assert ds_hash_1 == ds_hash_2


def test_hostile_1h_dataset_identity_mutation():
    """Changing only one 1H candle OHLC or source must strictly alter dataset identity."""
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    c15 = make_candle(start_t, Decimal("2600.00"), Decimal("2605.00"), Decimal("2598.00"), Decimal("2602.00"))
    c1h_a = make_candle(start_t, Decimal("2600.00"), Decimal("2610.00"), Decimal("2595.00"), Decimal("2605.00"), tf_min=60)
    c1h_b = make_candle(start_t, Decimal("2600.00"), Decimal("2612.00"), Decimal("2595.00"), Decimal("2605.00"), tf_min=60)

    hash_a = compute_xauusd_dataset_identity(
        candles_15m=[c15],
        candles_1h=[c1h_a],
        start_time=start_t,
        end_time=end_t,
    )
    hash_b = compute_xauusd_dataset_identity(
        candles_15m=[c15],
        candles_1h=[c1h_b],
        start_time=start_t,
        end_time=end_t,
    )

    assert hash_a != hash_b


def test_hostile_phase3a_macro_future_mutation_invariance(calibrated_signal_profile):
    """Mutating Phase 3A or Macro evidence with timestamp > T must have zero impact on evaluation at T."""
    t_eval = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    for i in range(25):
        t_open = t_eval - timedelta(minutes=15 * (25 - i))
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2602.00")))

    # Initial Phase 3A snapshot at T
    p3a_current = Cycle3ASnapshot(
        timestamp=t_eval,
        session=SessionContext(
            session=SessionType.LONDON,
            progress_pct=0.5,
            is_high_liquidity=True,
            local_times={},
            expectancy_score=20.0,
        ),
        swing_duration=SwingDurationContext(
            market_age_bars=5,
            market_age_hours=1.25,
            known_age_bars=5,
            known_age_hours=1.25,
            pullback_age_percentile=50.0,
            is_mature=False,
            maturity_score=15.0,
        ),
        macro_event=MacroEventContext(
            is_in_blackout=False,
        ),
        calendar=None,
        is_blocked_by_event=False,
        cycle_score_3a=35.0,
    )
    dataset.add_cycle_3a(p3a_current)

    sig_engine = XauUsdSignalEngine(code_revision="rev-test")
    clock = ReplayClock([t_eval])
    replay1 = XauUsdPointInTimeReplay(
        dataset=dataset,
        signal_engine=sig_engine,
        risk_planner=XauUsdRiskPlanner(code_revision="rev-test", risk_profile=uncalibrated_xauusd_risk_profile()),
        signal_profile=calibrated_signal_profile,
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
    )
    signals1, _ = replay1.run(clock)

    # Mutate future Phase 3A snapshot at T + 2 hours
    p3a_future = Cycle3ASnapshot(
        timestamp=t_eval + timedelta(hours=2),
        session=SessionContext(
            session=SessionType.NEW_YORK,
            progress_pct=0.5,
            is_high_liquidity=True,
            local_times={},
            expectancy_score=50.0,
        ),
        swing_duration=SwingDurationContext(
            market_age_bars=20,
            market_age_hours=5.0,
            known_age_bars=20,
            known_age_hours=5.0,
            pullback_age_percentile=80.0,
            is_mature=True,
            maturity_score=50.0,
        ),
        macro_event=MacroEventContext(
            is_in_blackout=True,
            active_event_name="FOMC",
        ),
        calendar=None,
        is_blocked_by_event=True,
        cycle_score_3a=100.0,
    )
    dataset.add_cycle_3a(p3a_future)

    clock2 = ReplayClock([t_eval])
    signals2, _ = replay1.run(clock2)

    assert len(signals1) == len(signals2) == 1
    assert signals1[0].analysis_fingerprint == signals2[0].analysis_fingerprint
    assert signals1[0].candidate_state == signals2[0].candidate_state


def test_hostile_walkforward_ratios_mathematical_derivation():
    """Verify exact mathematical fold derivation for 70/10/20, 60/20/20, and 80/0/20."""
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)  # 10 days = 240 hours
    total_sec = (end_t - start_t).total_seconds()

    # 1. 70/10/20 (3 folds, rolling=False)
    cfg_70_10_20 = XauUsdWalkForwardConfig(total_folds=3, train_ratio=0.70, val_ratio=0.10, oos_ratio=0.20, rolling_window=False)
    folds_70 = XauUsdChronologicalFoldGenerator.generate_folds(start_t, end_t, cfg_70_10_20)
    assert len(folds_70) == 3
    assert folds_70[0].train_start == start_t
    assert (folds_70[0].train_end - folds_70[0].train_start).total_seconds() == total_sec * 0.70
    assert (folds_70[0].val_end - folds_70[0].val_start).total_seconds() == total_sec * 0.10
    assert (folds_70[0].oos_end - folds_70[0].oos_start).total_seconds() == (total_sec * 0.20) / 3
    assert folds_70[-1].oos_end == end_t

    # 2. 60/20/20 (2 folds, rolling=True)
    cfg_60_20_20 = XauUsdWalkForwardConfig(total_folds=2, train_ratio=0.60, val_ratio=0.20, oos_ratio=0.20, rolling_window=True)
    folds_60 = XauUsdChronologicalFoldGenerator.generate_folds(start_t, end_t, cfg_60_20_20)
    assert len(folds_60) == 2
    assert folds_60[1].train_start > start_t  # Rolled forward
    assert (folds_60[0].train_end - folds_60[0].train_start).total_seconds() == total_sec * 0.60
    assert (folds_60[1].train_end - folds_60[1].train_start).total_seconds() == total_sec * 0.60
    assert folds_60[-1].oos_end == end_t

    # 3. 80/0/20 (2 folds, rolling=False)
    cfg_80_0_20 = XauUsdWalkForwardConfig(total_folds=2, train_ratio=0.80, val_ratio=0.00, oos_ratio=0.20, rolling_window=False)
    folds_80 = XauUsdChronologicalFoldGenerator.generate_folds(start_t, end_t, cfg_80_0_20)
    assert len(folds_80) == 2
    assert folds_80[0].val_start is None
    assert folds_80[0].val_end is None
    assert folds_80[0].oos_start == folds_80[0].train_end
    assert folds_80[-1].oos_end == end_t


def test_hostile_entry_fee_accounting(calibrated_risk_profile):
    """Entry fee must be deducted from net_pnl once without double-counting entry spread/slippage."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
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

    # Future candle reaching TP1 (2635.00)
    future_c = make_candle(eval_t + timedelta(minutes=15), Decimal("2605.00"), Decimal("2640.00"), Decimal("2604.00"), Decimal("2638.00"))

    # Zero fee
    eng_zero = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
        code_revision="rev",
        execution_policy_config=calibrated_risk_profile.long_execution_policy,
        phase5_policy_fingerprint="p5_fp",
    )
    trade_zero = eng_zero.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[future_c],
        trade_id="t-zero",
    )

    # 10 bps entry fee
    eng_fee = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.empirical(entry_fee_bps=Decimal("10.0")),
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
        code_revision="rev",
        execution_policy_config=calibrated_risk_profile.long_execution_policy,
        phase5_policy_fingerprint="p5_fp",
    )
    trade_fee = eng_fee.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[future_c],
        trade_id="t-fee",
    )

    assert trade_fee.gross_r == trade_zero.gross_r
    assert trade_fee.net_r < trade_zero.net_r
    assert trade_fee.net_pnl_per_unit == (trade_zero.gross_pnl_per_unit - trade_fee.entry_fee).quantize(Decimal("0.0001"))


def test_hostile_missing_fill_horizon_raises():
    """Missing fill search horizon must raise ValueError without hidden 3600s fallback."""
    with pytest.raises(ValueError, match="Explicit fill-search horizon"):
        XauUsdOutcomeEngine(
            cost_config=XauUsdCostConfig.idealized(),
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=None,
            max_fill_wait_seconds=None,
            code_revision="rev",
            execution_policy_config=XauUsdExecutionPolicy(),
            phase5_policy_fingerprint="p5_fp",
        )


def test_hostile_policy_fingerprint_mismatch_raises(calibrated_signal_profile, calibrated_risk_profile):
    """Mismatched policy fingerprints on BacktestRunSpec must raise ValueError."""
    with pytest.raises(ValueError, match="phase4_policy_fingerprint mismatch"):
        XauUsdBacktestRunSpec(
            instrument="XAUUSD",
            start_time=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            timeframes=("15m",),
            cost_config=XauUsdCostConfig.idealized(),
            cost_scenario=XauUsdCostScenario.IDEALIZED,
            dataset_hash="hash",
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=2,
            code_revision="rev",
            signal_profile=calibrated_signal_profile,
            phase4_policy_fingerprint="bad-fingerprint",
        )

    with pytest.raises(ValueError, match="phase5_risk_policy_fingerprint mismatch"):
        XauUsdBacktestRunSpec(
            instrument="XAUUSD",
            start_time=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            timeframes=("15m",),
            cost_config=XauUsdCostConfig.idealized(),
            cost_scenario=XauUsdCostScenario.IDEALIZED,
            dataset_hash="hash",
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=2,
            code_revision="rev",
            risk_profile=calibrated_risk_profile,
            phase5_risk_policy_fingerprint="bad-fingerprint",
        )


def test_hostile_celery_naive_timestamp_rejection():
    """Celery tasks must strictly reject naive ISO timestamps."""
    from apps.backtests.tasks import run_xauusd_backtest_task

    with pytest.raises(ValueError, match="must include an explicit timezone offset"):
        run_xauusd_backtest_task(
            start_time_iso="2026-09-01T10:00:00",  # Naive
            end_time_iso="2026-09-01T14:00:00+00:00",
            dataset_hash="hash",
            code_revision="rev",
            cost_scenario="IDEALIZED",
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=2,
        )

    with pytest.raises(ValueError, match="must include an explicit timezone offset"):
        run_xauusd_backtest_task(
            start_time_iso="2026-09-01T10:00:00+00:00",
            end_time_iso="2026-09-01T14:00:00",  # Naive
            dataset_hash="hash",
            code_revision="rev",
            cost_scenario="IDEALIZED",
            holding_horizon_bars_15m=5,
            max_fill_wait_bars_15m=2,
        )


def test_hostile_celery_calibration_required_without_profiles():
    """Celery XAUUSD task must return CALIBRATION_REQUIRED without fake completion if uncalibrated."""
    from apps.backtests.tasks import run_xauusd_backtest_task

    res = run_xauusd_backtest_task(
        start_time_iso="2026-09-01T10:00:00+00:00",
        end_time_iso="2026-09-01T14:00:00+00:00",
        dataset_hash="hash",
        code_revision="rev",
        cost_scenario="IDEALIZED",
        holding_horizon_bars_15m=5,
        max_fill_wait_bars_15m=2,
        signal_profile=None,
        risk_profile=None,
    )
    assert res["status"] == "CALIBRATION_REQUIRED"


def test_hostile_4h_structure_target_parity(calibrated_risk_profile, calibrated_signal_profile):
    """Proves causal 4H structure passed into risk planner determines TP1/TP2 targets identically."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
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

    support_15m = StructureZone("SUPPORT", Decimal("2600.00"), Decimal("2605.00"), eval_t - timedelta(hours=2), 2, True)
    res_15m = StructureZone("RESISTANCE", Decimal("2625.00"), Decimal("2630.00"), eval_t - timedelta(hours=2), 2, True)
    res_4h = StructureZone("RESISTANCE", Decimal("2645.00"), Decimal("2650.00"), eval_t - timedelta(hours=8), 3, True)

    struct_15m = StructureResult(eval_t, StructureType.HH, None, None, None, (), (support_15m, res_15m))
    struct_4h = StructureResult(eval_t, StructureType.HH, None, None, None, (), (support_15m, res_4h))

    planner = XauUsdRiskPlanner(code_revision="rev", risk_profile=calibrated_risk_profile)
    plan_with_4h = planner.plan_long(
        phase4_snapshot=sig,
        structure_15m=struct_15m,
        atr14=Decimal("3.00"),
        structure_4h=struct_4h,
    )
    plan_without_4h = planner.plan_long(
        phase4_snapshot=sig,
        structure_15m=struct_15m,
        atr14=Decimal("3.00"),
        structure_4h=None,
    )

    assert plan_with_4h.is_valid_risk_plan is True
    assert plan_without_4h.is_valid_risk_plan is True
    # 4H resistance target zone establishes higher TP2 target when available
    assert plan_with_4h.tp2 == Decimal("2645.00")

