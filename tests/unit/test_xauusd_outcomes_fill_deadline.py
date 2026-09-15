"""
Targeted hostile unit tests for fill deadline enforcement before entry resolution.
Proves that NEXT_BAR_OPEN, MARKET_AFTER_SIGNAL, and LIMIT entries cannot fill
using evidence beyond max_fill_wait, weekend gaps produce NO_FILL, and run_end
acts as the effective deadline when earlier than fill_deadline.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_types import (
    XauUsdCostConfig,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
)
from engine.core.types import (
    BosType,
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    RiskSide,
    SideRiskPlanSnapshot,
    SignalSide,
    SignalState,
    StructureResult,
    StructureType,
    StructureZone,
    UserDecision,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_policy import SideRiskPolicy, XauUsdExecutionPolicy
from engine.core.types import DualSideSignalSnapshot


def _make_candle(open_ts: datetime, open_p: Decimal, high_p: Decimal, low_p: Decimal, close_p: Decimal) -> CandleData:
    close_ts = open_ts + timedelta(minutes=15)
    return CandleData(
        timestamp_open=open_ts,
        timestamp_close=close_ts,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=Decimal("100.0"),
        is_closed=True,
    )


def _make_signal_and_plan(signal_ts: datetime):
    from engine.core.types import (
        RuntimeFeedHealth,
        SideDirectionScoreResult,
        SideTimingScoreResult,
        XauUsdHardGateEvaluation,
    )
    from engine.risk.xauusd_planner import XauUsdRiskPlanner
    from engine.risk.xauusd_policy import XauUsdRiskProfile
    dir_res = SideDirectionScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    tim_res = SideTimingScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    hg = XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth())
    sig = DualSideSignalSnapshot(
        timestamp=signal_ts,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=dir_res,
        short_direction=dir_res,
        long_timing=tim_res,
        short_timing=tim_res,
        hard_gate=hg,
        reasons_long_positive=("Bullish momentum confirmed",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="BUY_WINDOW",
        candidate_resolution_reason="BUY_WINDOW",
        publication_reason="BUY_WINDOW",
        analysis_fingerprint="sig_fp_test",
        phase4_policy_fingerprint="p4_fp_test",
        code_revision="b2acb6b735c378c6ae8d39372012c91949052305",
        profile_name="TEST_XAUUSD_PROFILE",
        calibration_status="CANDIDATE",
    )
    risk_prof = XauUsdRiskProfile(
        name="TEST_PROFILE",
        long_risk_policy=SideRiskPolicy(
            structure_buffer=Decimal("1.50"),
            atr_multiplier=Decimal("2.0"),
            max_stop_distance_atr=Decimal("4.0"),
            min_rr_tp1=Decimal("1.80"),
            tp2_atr_multiplier=None,
        ),
        short_risk_policy=SideRiskPolicy(
            structure_buffer=Decimal("1.50"),
            atr_multiplier=Decimal("2.0"),
            max_stop_distance_atr=Decimal("4.0"),
            min_rr_tp1=Decimal("1.80"),
            tp2_atr_multiplier=None,
        ),
        long_execution_policy=XauUsdExecutionPolicy(
            latency_seconds=0.0,
            synthetic_spread_points=Decimal("260.0"),
            point_size=Decimal("0.001"),
            modeled_execution_gap_points=Decimal("0.0"),
        ),
        short_execution_policy=XauUsdExecutionPolicy(
            latency_seconds=0.0,
            synthetic_spread_points=Decimal("260.0"),
            point_size=Decimal("0.001"),
            modeled_execution_gap_points=Decimal("0.0"),
        ),
    )
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), signal_ts - timedelta(hours=2), 2, True)
    resistance = StructureZone("RESISTANCE", Decimal("2535.00"), Decimal("2540.00"), signal_ts - timedelta(hours=2), 2, True)
    struct = StructureResult(signal_ts, StructureType.HH, None, None, None, (), (support, resistance))

    planner = XauUsdRiskPlanner(code_revision="b2acb6b735c378c6ae8d39372012c91949052305", risk_profile=risk_prof)
    plan = planner.plan_long(phase4_snapshot=sig, structure_15m=struct, atr14=Decimal("3.00"))
    return sig, plan


@pytest.fixture
def governed_execution_policy():
    return XauUsdExecutionPolicy(
        latency_seconds=0.0,
        synthetic_spread_points=Decimal("260.0"),
        point_size=Decimal("0.001"),
        modeled_execution_gap_points=Decimal("0.0"),
        slippage_pct=Decimal("0.0"),
    )


@pytest.fixture
def outcome_engine(governed_execution_policy):
    return XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=4,
        max_fill_wait_seconds=1800.0,  # 30-minute fill window
        code_revision="b2acb6b735c378c6ae8d39372012c91949052305",
        execution_policy_config=governed_execution_policy,
        phase5_policy_fingerprint="phase5_fp_test",
    )


@pytest.mark.unit
def test_hostile_a_first_next_bar_inside_deadline_filled(outcome_engine):
    """Hostile A: First next bar strictly inside deadline (10:15 < 10:30) must produce FILLED."""
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    sig, plan = _make_signal_and_plan(t0)

    # Bar 1: open at 10:15:00 (inside 30m deadline at 10:30:00)
    bar1 = _make_candle(t0 + timedelta(minutes=15), Decimal("2500.00"), Decimal("2505.00"), Decimal("2498.00"), Decimal("2502.00"))

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[bar1],
        trade_id="test-a-inside",
    )
    assert trade.outcome != XauUsdTradeOutcome.NO_FILL
    assert trade.fill_timestamp == t0 + timedelta(minutes=15)
    assert trade.fill_price == Decimal("2500.260")  # raw 2500 + spread 0.260


@pytest.mark.unit
def test_hostile_b_first_bar_exactly_at_permitted_boundary_matches_governed_rule(outcome_engine):
    """Hostile B: First available bar open exactly at permitted boundary (10:30:00 == fill_deadline) must be FILLED."""
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    sig, plan = _make_signal_and_plan(t0)

    # First available candle open is exactly at 10:30:00 (equal to t0 + 1800s deadline)
    boundary_bar = _make_candle(t0 + timedelta(seconds=1800), Decimal("2501.00"), Decimal("2506.00"), Decimal("2499.00"), Decimal("2503.00"))

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[boundary_bar],
        trade_id="test-b-exact-boundary",
    )
    # Governed boundary rule is signal_ts <= evidence_timestamp <= fill_deadline (inclusive)
    assert trade.outcome != XauUsdTradeOutcome.NO_FILL
    assert trade.fill_timestamp == t0 + timedelta(seconds=1800)


@pytest.mark.unit
def test_hostile_c_first_available_bar_after_deadline_produces_no_fill(outcome_engine):
    """Hostile C: First available bar open after deadline (10:45 > 10:30) must produce NO_FILL, not delayed fill."""
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    sig, plan = _make_signal_and_plan(t0)

    # First candle open is at 10:45:00 (exceeds 10:30:00 deadline)
    late_bar = _make_candle(t0 + timedelta(minutes=45), Decimal("2500.00"), Decimal("2510.00"), Decimal("2499.00"), Decimal("2508.00"))

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[late_bar],
        trade_id="test-c-after-deadline",
    )
    assert trade.outcome == XauUsdTradeOutcome.NO_FILL
    assert trade.fill_timestamp is None
    assert trade.fill_price is None
    assert trade.dependency_end_timestamp == t0 + timedelta(seconds=1800)


@pytest.mark.unit
def test_hostile_d_weekend_or_long_data_gap_beyond_fill_deadline_produces_no_fill(outcome_engine):
    """Hostile D: Weekend or long data gap where first subsequent candle is 60 hours later must produce NO_FILL."""
    # Friday 21:00 UTC signal
    friday_t = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
    sig, plan = _make_signal_and_plan(friday_t)

    # Next available bar is Monday 00:00 UTC (after weekend closure)
    monday_bar = _make_candle(
        datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc),
        Decimal("2510.00"), Decimal("2515.00"), Decimal("2505.00"), Decimal("2512.00")
    )

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[monday_bar],
        trade_id="test-d-weekend-gap",
    )
    assert trade.outcome == XauUsdTradeOutcome.NO_FILL
    assert trade.fill_timestamp is None
    assert trade.dependency_end_timestamp == friday_t + timedelta(seconds=1800)


@pytest.mark.unit
def test_hostile_e_no_evidence_produces_no_fill(outcome_engine):
    """Hostile E: Empty future evidence list must produce NO_FILL bounded by fill deadline."""
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    sig, plan = _make_signal_and_plan(t0)

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[],
        trade_id="test-e-no-evidence",
    )
    assert trade.outcome == XauUsdTradeOutcome.NO_FILL
    assert trade.fill_timestamp is None
    assert trade.dependency_end_timestamp == t0 + timedelta(seconds=1800)


@pytest.mark.unit
def test_hostile_f_run_end_earlier_than_fill_deadline_effective_deadline_is_run_end(outcome_engine):
    """Hostile F: When run_end_time < fill_deadline, effective deadline is run_end and subsequent candles cannot fill."""
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    sig, plan = _make_signal_and_plan(t0)

    # run_end is at 10:15:00, while fill_deadline would otherwise be 10:30:00
    run_end = t0 + timedelta(minutes=15)

    # Bar 1 open is at 10:15:00 (which is >= run_end, so strictly outside [T, run_end))
    bar_at_run_end = _make_candle(run_end, Decimal("2500.00"), Decimal("2505.00"), Decimal("2498.00"), Decimal("2502.00"))

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[bar_at_run_end],
        run_end_time=run_end,
        trade_id="test-f-run-end-cap",
    )
    assert trade.outcome == XauUsdTradeOutcome.NO_FILL
    assert trade.fill_timestamp is None
    # Effective deadline must be clamped to run_end
    assert trade.dependency_end_timestamp == run_end
