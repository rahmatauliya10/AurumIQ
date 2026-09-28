"""
Unit and regression tests for Outcome Semantics Remediation.
Proves fail-closed post-fill geometric validation:
  LONG requires: stop_final < fill_price < tp1
  SHORT requires: tp1 < fill_price < stop_final

Rejects stale retroactively-hit barrier trades as ENTRY_INVALIDATED_STALE_RISK_PLAN.
Enforces invariant: resolved TP1_FIRST must have gross_r >= 0, resolved SL_FIRST must have gross_r <= 0.
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
    BarrierHitType,
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
    RiskSide,
    RiskCandidateStatus,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideRiskPlanSnapshot,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)


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


def _build_test_harness(side: RiskSide):
    signal_ts = datetime(2024, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    is_long = (side == RiskSide.LONG)

    dir_res = SideDirectionScoreResult(side, 85.0, 100.0, (), True, True)
    tim_res = SideTimingScoreResult(side, 85.0, 100.0, (), True, True)
    hg = XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth())

    sig_state = SignalState.BUY_WINDOW if is_long else SignalState.SELL_WINDOW
    sig_decision = UserDecision.BUY if is_long else UserDecision.SELL

    sig = DualSideSignalSnapshot(
        timestamp=signal_ts,
        instrument="XAUUSD",
        timeframe="15m",
        state=sig_state,
        user_decision=sig_decision,
        candidate_state=sig_state,
        candidate_user_decision=sig_decision,
        long_direction=dir_res,
        short_direction=dir_res,
        long_timing=tim_res,
        short_timing=tim_res,
        hard_gate=hg,
        reasons_long_positive=("Signal confirmed",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="SIGNAL_WINDOW",
        candidate_resolution_reason="SIGNAL_WINDOW",
        publication_reason="SIGNAL_WINDOW",
        analysis_fingerprint="sig_fp_remediation_test",
        phase4_policy_fingerprint="p4_fp_remediation_test",
        code_revision="test_code_revision",
        profile_name="TEST_PROFILE",
        calibration_status="CANDIDATE",
    )

    exec_pol = XauUsdExecutionPolicy(
        latency_seconds=0.0,
        synthetic_spread_points=Decimal("0.0"),
        point_size=Decimal("0.001"),
        modeled_execution_gap_points=Decimal("0.0"),
    )

    long_risk = SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.5"), None)
    short_risk = SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.5"), None)

    risk_prof = XauUsdRiskProfile(
        name="TEST_PROFILE",
        long_risk_policy=long_risk,
        short_risk_policy=short_risk,
        long_execution_policy=exec_pol,
        short_execution_policy=exec_pol,
    )

    engine = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=32,
        max_fill_wait_bars_15m=8,
        code_revision="test_code_revision",
        long_execution_policy=exec_pol,
        short_execution_policy=exec_pol,
        phase5_policy_fingerprint="p5_fp_remediation_test",
    )

    return signal_ts, sig, engine


def _make_risk_plan(
    side: RiskSide,
    signal_ts: datetime,
    sig_fp: str,
    cand_state: SignalState,
    cand_decision: UserDecision,
    entry_min: Decimal,
    entry_max: Decimal,
    stop_final: Decimal,
    tp1: Decimal,
) -> SideRiskPlanSnapshot:
    is_long = (side == RiskSide.LONG)
    status = (
        RiskCandidateStatus.VALID_LONG_RISK_CANDIDATE
        if is_long
        else RiskCandidateStatus.VALID_SHORT_RISK_CANDIDATE
    )
    action = UserDecision.BUY if is_long else UserDecision.SELL
    return SideRiskPlanSnapshot(
        side=side,
        source_phase4_fingerprint=sig_fp,
        source_candidate_state=cand_state,
        source_candidate_decision=cand_decision,
        signal_generated_at=signal_ts,
        entry_min=entry_min,
        entry_mid=(entry_min + entry_max) / Decimal("2"),
        entry_max=entry_max,
        stop_structure=stop_final,
        stop_atr=stop_final,
        stop_final=stop_final,
        stop_distance_atr=Decimal("2.0"),
        tp1=tp1,
        tp2=tp1 + Decimal("10.0") if is_long else tp1 - Decimal("10.0"),
        planned_rr_tp1=Decimal("1.5"),
        planned_rr_tp2=Decimal("2.5"),
        risk_candidate_valid=True,
        risk_candidate_status=status,
        simulation_eligible=True,
        candidate_effective_action=action,
        publication_effective_action=UserDecision.WAIT,
        reasons=("Valid plan",),
        entry_zone_fingerprint="ez_fp",
        tp1_zone_fingerprint="tp1_fp",
        tp2_zone_fingerprint="tp2_fp",
        phase5_policy_fingerprint="p5_fp",
        risk_plan_fingerprint="rp_fp",
        risk_version="5.0.0",
        code_revision="test_rev",
    )


# =========================================================================
# LONG TESTS
# =========================================================================

def test_long_case_a_valid_fill_and_tp_touch_produces_positive_gross_r():
    """Case A: LONG fill < TP1 and > SL, then TP touched -> TP1_FIRST positive R."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.LONG)

    # Risk plan: entry 2000-2005, SL=1990, TP1=2020.
    plan = _make_risk_plan(
        side=RiskSide.LONG,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_final=Decimal("1990.00"),
        tp1=Decimal("2020.00"),
    )

    # Fill bar at 12:00: Open=2002.00 (within SL < fill < TP1)
    # Candle reaches high=2025.00 (touches TP1 2020.00)
    c1 = _make_candle(signal_ts, Decimal("2002.00"), Decimal("2025.00"), Decimal("2000.00"), Decimal("2022.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-long-a",
    )

    assert trade.outcome == XauUsdTradeOutcome.TP1_FIRST
    assert trade.fill_price == Decimal("2002.00")
    assert trade.exit_price == Decimal("2020.00")
    assert trade.exit_price >= trade.fill_price
    assert trade.gross_r > Decimal("0")
    assert trade.net_r > Decimal("0")


def test_long_case_b_stale_fill_above_tp1_invalidated():
    """Case B: LONG fill >= TP1 -> ENTRY_INVALIDATED_STALE_RISK_PLAN, not TP1_FIRST."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.LONG)

    # Risk plan: SL=1990, TP1=2020
    plan = _make_risk_plan(
        side=RiskSide.LONG,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_final=Decimal("1990.00"),
        tp1=Decimal("2020.00"),
    )

    # Market gapped up and opened at 2025.00 (>= TP1 2020.00)
    c1 = _make_candle(signal_ts, Decimal("2025.00"), Decimal("2030.00"), Decimal("2022.00"), Decimal("2028.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-long-b",
    )

    assert trade.outcome == XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN
    assert trade.outcome != XauUsdTradeOutcome.TP1_FIRST
    assert trade.fill_price == Decimal("2025.00")
    assert trade.exit_price is None
    assert trade.gross_r is None
    assert trade.net_r is None


def test_long_case_c_stale_fill_below_sl_invalidated():
    """Case C: LONG fill <= SL -> ENTRY_INVALIDATED_STALE_RISK_PLAN, not SL_FIRST."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.LONG)

    # Risk plan: SL=1990, TP1=2020
    plan = _make_risk_plan(
        side=RiskSide.LONG,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_final=Decimal("1990.00"),
        tp1=Decimal("2020.00"),
    )

    # Market gapped down and opened at 1985.00 (<= SL 1990.00)
    c1 = _make_candle(signal_ts, Decimal("1985.00"), Decimal("1988.00"), Decimal("1980.00"), Decimal("1982.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-long-c",
    )

    assert trade.outcome == XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN
    assert trade.outcome != XauUsdTradeOutcome.SL_FIRST
    assert trade.fill_price == Decimal("1985.00")
    assert trade.exit_price is None
    assert trade.gross_r is None
    assert trade.net_r is None


# =========================================================================
# SHORT TESTS
# =========================================================================

def test_short_case_d_valid_fill_and_tp_touch_produces_positive_gross_r():
    """Case D: SHORT fill > TP1 and < SL, then TP touched -> TP1_FIRST positive R."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.SHORT)

    # Risk plan: entry 2050-2055, SL=2065, TP1=2035.
    plan = _make_risk_plan(
        side=RiskSide.SHORT,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2050.00"),
        entry_max=Decimal("2055.00"),
        stop_final=Decimal("2065.00"),
        tp1=Decimal("2035.00"),
    )

    # Fill bar at 12:00: Open=2052.00 (within TP1 < fill < SL)
    # Candle drops low=2030.00 (touches TP1 2035.00)
    c1 = _make_candle(signal_ts, Decimal("2052.00"), Decimal("2054.00"), Decimal("2030.00"), Decimal("2032.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-short-d",
    )

    assert trade.outcome == XauUsdTradeOutcome.TP1_FIRST
    assert trade.fill_price == Decimal("2052.00")
    assert trade.exit_price == Decimal("2035.00")
    assert trade.exit_price <= trade.fill_price
    assert trade.gross_r > Decimal("0")
    assert trade.net_r > Decimal("0")


def test_short_case_e_stale_fill_below_tp1_invalidated():
    """Case E: SHORT fill <= TP1 -> ENTRY_INVALIDATED_STALE_RISK_PLAN, not TP1_FIRST."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.SHORT)

    # Risk plan: SL=2065, TP1=2035
    plan = _make_risk_plan(
        side=RiskSide.SHORT,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2050.00"),
        entry_max=Decimal("2055.00"),
        stop_final=Decimal("2065.00"),
        tp1=Decimal("2035.00"),
    )

    # Market gapped down and opened at 2030.00 (<= TP1 2035.00)
    c1 = _make_candle(signal_ts, Decimal("2030.00"), Decimal("2032.00"), Decimal("2025.00"), Decimal("2028.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-short-e",
    )

    assert trade.outcome == XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN
    assert trade.outcome != XauUsdTradeOutcome.TP1_FIRST
    assert trade.fill_price == Decimal("2030.00")
    assert trade.exit_price is None
    assert trade.gross_r is None
    assert trade.net_r is None


def test_short_case_f_stale_fill_above_sl_invalidated():
    """Case F: SHORT fill >= SL -> ENTRY_INVALIDATED_STALE_RISK_PLAN, not SL_FIRST."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.SHORT)

    # Risk plan: SL=2065, TP1=2035
    plan = _make_risk_plan(
        side=RiskSide.SHORT,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2050.00"),
        entry_max=Decimal("2055.00"),
        stop_final=Decimal("2065.00"),
        tp1=Decimal("2035.00"),
    )

    # Market gapped up and opened at 2070.00 (>= SL 2065.00)
    c1 = _make_candle(signal_ts, Decimal("2070.00"), Decimal("2075.00"), Decimal("2068.00"), Decimal("2072.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-short-f",
    )

    assert trade.outcome == XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN
    assert trade.outcome != XauUsdTradeOutcome.SL_FIRST
    assert trade.fill_price == Decimal("2070.00")
    assert trade.exit_price is None
    assert trade.gross_r is None
    assert trade.net_r is None


# =========================================================================
# INVARIANT & DEFENSE-IN-DEPTH TESTS
# =========================================================================

def test_intrabar_resolver_rejects_inverted_fill_price():
    """SideAwareIntrabarResolver defense-in-depth rejects inverted fill_price."""
    resolver = SideAwareIntrabarResolver()
    c = _make_candle(
        datetime(2024, 3, 4, 12, 0, tzinfo=timezone.utc),
        Decimal("2080.00"), Decimal("2085.00"), Decimal("2075.00"), Decimal("2082.00")
    )

    # SHORT with fill_price (2070.00) <= tp_price (2075.00)
    res_short = resolver.resolve(
        side=RiskSide.SHORT,
        parent_candle=c,
        tp_price=Decimal("2075.00"),
        sl_price=Decimal("2090.00"),
        fill_price=Decimal("2070.00"),
    )
    assert res_short.barrier_hit == BarrierHitType.UNRESOLVED
    assert "Invalid SHORT barrier ordering" in res_short.reasons[0]

    # LONG with fill_price (2090.00) >= tp_price (2085.00)
    res_long = resolver.resolve(
        side=RiskSide.LONG,
        parent_candle=c,
        tp_price=Decimal("2085.00"),
        sl_price=Decimal("2070.00"),
        fill_price=Decimal("2090.00"),
    )
    assert res_long.barrier_hit == BarrierHitType.UNRESOLVED
    assert "Invalid LONG barrier ordering" in res_long.reasons[0]


def test_resolved_sl_first_represents_adverse_exit():
    """Resolved SL_FIRST must strictly represent an adverse exit relative to fill."""
    signal_ts, sig, engine = _build_test_harness(RiskSide.LONG)
    plan = _make_risk_plan(
        side=RiskSide.LONG,
        signal_ts=signal_ts,
        sig_fp=sig.analysis_fingerprint,
        cand_state=sig.candidate_state,
        cand_decision=sig.candidate_user_decision,
        entry_min=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_final=Decimal("1990.00"),
        tp1=Decimal("2020.00"),
    )

    # Fill at 2002.00, drops to 1985.00 hitting SL at 1990.00
    c1 = _make_candle(signal_ts, Decimal("2002.00"), Decimal("2004.00"), Decimal("1985.00"), Decimal("1988.00"))

    trade = engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[c1],
        execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id="test-sl-invariant",
    )

    assert trade.outcome == XauUsdTradeOutcome.SL_FIRST
    assert trade.exit_price <= trade.fill_price
    assert trade.gross_r < Decimal("0")
    assert trade.net_r < Decimal("0")
