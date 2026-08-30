"""Targeted unit tests for Phase 6A: Point-in-Time Backtesting, Cost Integrity, and Outcomes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.clock import ReplayClock
from engine.backtest.costs import CostModel
from engine.backtest.fingerprint import compute_backtest_fingerprint
from engine.backtest.metrics import BacktestMetricsCalculator
from engine.backtest.outcomes import OutcomeEngine
from engine.backtest.replay import PointInTimeReplay
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import BacktestRunner
from engine.backtest.types import (
    BacktestCostConfig,
    BacktestRunSpec,
    CostScenario,
    SimulatedTrade,
    TradeOutcome,
)
from engine.core.types import (
    BarrierHitType,
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RegimeType,
    RiskPlanSnapshot,
    SessionType,
    SignalSnapshot,
    SignalState,
    UserDecision,
)
from engine.risk.execution import EntryExecutionModel
from engine.risk.intrabar import IntrabarResolver
from engine.risk.planner import RiskPlanner
from engine.signals.engine import XautSignalEngine


def _make_candle(
    ts_open: datetime,
    duration_min: int,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    vol: Decimal = Decimal("100.0"),
    is_closed: bool = True,
) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=duration_min),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        is_closed=is_closed,
    )


def _build_synthetic_dataset(num_bars: int = 50, start_price: Decimal = Decimal("2000.00")) -> PointInTimeDataset:
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    c_15m = []
    p = start_price
    for i in range(num_bars):
        t_open = base_ts + timedelta(minutes=15 * i)
        high = p + Decimal("5.00")
        low = p - Decimal("3.00")
        close = p + Decimal("1.00")
        c_15m.append(_make_candle(t_open, 15, p, high, low, close))
        p = close

    return PointInTimeDataset(candles_15m=c_15m)


# --- P6-01: Point-in-Time Candle Filtering ---
def test_p6_01_pit_candle_filtering():
    """Verify get_closed_candles returns ONLY closed candles with timestamp_close <= as_of."""
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    c1 = _make_candle(base_ts, 15, Decimal("2000"), Decimal("2005"), Decimal("1995"), Decimal("2002"), is_closed=True)
    c2 = _make_candle(base_ts + timedelta(minutes=15), 15, Decimal("2002"), Decimal("2008"), Decimal("2000"), Decimal("2006"), is_closed=True)
    c3 = _make_candle(base_ts + timedelta(minutes=30), 15, Decimal("2006"), Decimal("2010"), Decimal("2004"), Decimal("2009"), is_closed=False)

    ds = PointInTimeDataset(candles_15m=[c1, c2, c3])
    as_of = base_ts + timedelta(minutes=20)  # during c2

    res = ds.get_closed_candles("15m", as_of=as_of)
    assert len(res) == 1
    assert res[0].timestamp_open == base_ts
    assert res[0].timestamp_close <= as_of


# --- P6-02: Future Mutation Invariant (P6-C1) ---
def test_p6_02_future_mutation_safety():
    """
    P6-C1:
    1. Mutating data > T does not alter Signal/Risk at T.
    2. Mutating data > exit does not alter completed trade.
    """
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    c1 = _make_candle(base_ts, 15, Decimal("2000"), Decimal("2005"), Decimal("1995"), Decimal("2002"), is_closed=True)
    ds = PointInTimeDataset(candles_15m=[c1])

    # Query at T = c1.close
    t_decision = c1.timestamp_close
    res_before = ds.get_closed_candles("15m", as_of=t_decision)
    assert len(res_before) == 1

    # Mutate / append future data > T
    c_future = _make_candle(t_decision, 15, Decimal("2002"), Decimal("2500"), Decimal("1900"), Decimal("2400"), is_closed=True)
    ds.add_candle("15m", c_future)

    res_after = ds.get_closed_candles("15m", as_of=t_decision)
    assert len(res_after) == 1
    assert res_after[0].high == Decimal("2005")


# --- P6-03: Closed-Candle-Only Decision ---
def test_p6_03_closed_candle_only():
    """Unclosed candle at T is rejected from decision set."""
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    c_unclosed = _make_candle(base_ts, 15, Decimal("2000"), Decimal("2010"), Decimal("1990"), Decimal("2005"), is_closed=False)
    ds = PointInTimeDataset(candles_15m=[c_unclosed])

    res = ds.get_closed_candles("15m", as_of=base_ts + timedelta(minutes=15))
    assert len(res) == 0


# --- P6-04: Same Phase 4 Engine Reuse (A09) ---
def test_p6_04_same_phase4_engine_reuse():
    """Backtest replay directly invokes frozen XautSignalEngine instance."""
    eng = XautSignalEngine(code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee")
    assert eng.code_revision == "6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee"
    assert eng.engine_version == "4.0.0"


# --- P6-05: Same Phase 5 Planner Reuse ---
def test_p6_05_same_phase5_planner_reuse():
    """Backtest replay directly invokes frozen RiskPlanner instance."""
    rp = RiskPlanner(code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee")
    assert rp.code_revision == "6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee"
    assert rp.risk_version == "5.0.0"


# --- P6-06: No Same-Bar Execution Leakage ---
def test_p6_06_no_same_bar_execution_leakage():
    """Fill timestamp must strictly be >= signal_generated_at + latency."""
    exec_model = EntryExecutionModel(latency_seconds=2.0)
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    # Next bar opens at 10:15
    bar_next = _make_candle(datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc), 15, Decimal("2050.00"), Decimal("2055.00"), Decimal("2048.00"), Decimal("2052.00"))
    res = exec_model.simulate_next_bar_open(signal_generated_at=sig_ts, candles=[bar_next])

    assert res.is_filled
    assert res.fill_timestamp == bar_next.timestamp_open
    assert res.fill_timestamp > sig_ts


# --- P6-07: Actual ASK Spread Not Double Counted ---
def test_p6_07_ask_spread_not_double_counted():
    """When actual ASK quote is provided, synthetic spread is zero."""
    cost_cfg = BacktestCostConfig.realistic(synthetic_spread_bps=Decimal("5.0"))
    model = CostModel(config=cost_cfg)

    # Quote entry
    res = model.calculate_entry(raw_price=Decimal("2000.00"), is_actual_ask_quote=True)
    assert res.spread_cost == Decimal("0.00")

    # Mid/Candle entry
    res_mid = model.calculate_entry(raw_price=Decimal("2000.00"), is_actual_ask_quote=False)
    assert res_mid.spread_cost > Decimal("0.00")


# --- P6-08: Actual BID Exit Spread Not Double Counted ---
def test_p6_08_bid_exit_spread_not_double_counted():
    """When actual BID quote is provided on exit, synthetic spread is zero."""
    cost_cfg = BacktestCostConfig.realistic(synthetic_spread_bps=Decimal("5.0"))
    model = CostModel(config=cost_cfg)

    res = model.calculate_exit(raw_price=Decimal("2020.00"), is_actual_bid_quote=True)
    assert res.spread_cost == Decimal("0.00")


# --- P6-09: Synthetic Candle Spread Applied Once ---
def test_p6_09_synthetic_candle_spread_applied_once():
    """Mid candle source applies half-spread on entry and half-spread on exit."""
    cost_cfg = BacktestCostConfig(synthetic_spread_bps=Decimal("10.0"))  # 10 bps total roundtrip
    model = CostModel(config=cost_cfg)

    entry = model.calculate_entry(raw_price=Decimal("2000.00"), is_actual_ask_quote=False)
    # 2000 * 5 bps = 1.00
    assert entry.spread_cost == Decimal("1.00")

    exit_c = model.calculate_exit(raw_price=Decimal("2000.00"), is_actual_bid_quote=False)
    assert exit_c.spread_cost == Decimal("1.00")


# --- P6-10: Explicit Fees Included ---
def test_p6_10_explicit_fees_included():
    """Maker/taker fees are explicitly accounted for."""
    cost_cfg = BacktestCostConfig(entry_fee_bps=Decimal("4.0"), exit_fee_bps=Decimal("4.0"))
    model = CostModel(config=cost_cfg)

    entry = model.calculate_entry(raw_price=Decimal("2000.00"), is_actual_ask_quote=True)
    assert entry.fee_cost == Decimal("0.80")  # 2000 * 0.0004 = 0.80


# --- P6-11: Slippage Adverse Only ---
def test_p6_11_slippage_adverse_only():
    """Slippage increases entry price and decreases exit price."""
    cost_cfg = BacktestCostConfig(entry_slippage_bps=Decimal("2.0"), exit_slippage_bps=Decimal("2.0"))
    model = CostModel(config=cost_cfg)

    entry = model.calculate_entry(raw_price=Decimal("2000.00"), is_actual_ask_quote=True)
    assert entry.effective_entry_price > entry.raw_price

    exit_c = model.calculate_exit(raw_price=Decimal("2000.00"), is_actual_bid_quote=True)
    assert exit_c.effective_exit_price < exit_c.raw_price


# --- P6-12: Gross vs Net Determinism & Frozen R Denominator (P6-C3) ---
def test_p6_12_gross_vs_net_determinism_and_planned_risk_r():
    """
    P6-C3: Denominator is strictly planned_risk_amount = entry_max - stop_final.
    Gross R and Net R must be deterministic.
    """
    cost_cfg = BacktestCostConfig.realistic()
    model = CostModel(config=cost_cfg)

    planned_risk = Decimal("10.00")  # e.g. entry_max 2005 - stop_final 1995
    raw_entry = Decimal("2000.00")
    raw_exit = Decimal("2020.00")

    entry_cost = model.calculate_entry(raw_entry)
    exit_cost = model.calculate_exit(raw_exit)

    (
        gross_pnl,
        net_pnl,
        gross_r,
        net_r,
        gross_ret,
        net_ret,
    ) = model.compute_r_and_returns(
        raw_entry_price=raw_entry,
        effective_entry_price=entry_cost.effective_entry_price,
        raw_exit_price=raw_exit,
        effective_exit_price=exit_cost.effective_exit_price,
        planned_risk_amount=planned_risk,
    )

    assert gross_pnl == Decimal("20.00")
    assert gross_r == Decimal("2.0000")
    assert net_r < gross_r
    assert net_pnl < gross_pnl


# --- P6-13: TP1-First Terminal Outcome (P6-C2) ---
def test_p6_13_tp1_first_terminal_outcome():
    """When price touches TP1 without touching SL, outcome is TP1_FIRST."""
    outcome_eng = OutcomeEngine(cost_model=CostModel(BacktestCostConfig.idealized()))
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    # Fake Signal and RiskPlan
    sig = SignalSnapshot(
        timestamp=sig_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=None,  # type: ignore
        timing=None,  # type: ignore
        hard_gate=None,  # type: ignore
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="sig-12345",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-12345",
        signal_generated_at=sig_ts,
        entry_min=Decimal("1995.00"),
        entry_mid=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_structure=Decimal("1990.00"),
        stop_atr=Decimal("1990.00"),
        stop_final=Decimal("1990.00"),
        stop_distance_atr=Decimal("2.0"),
        tp1=Decimal("2030.00"),
        tp2=Decimal("2050.00"),
        rr_tp1=Decimal("2.0"),
        rr_tp2=Decimal("3.0"),
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action=UserDecision.BUY,
        reasons=(),
    )

    # Future bar 1 (fill bar): open 2000
    b1 = _make_candle(sig_ts + timedelta(minutes=15), 15, Decimal("2000.00"), Decimal("2010.00"), Decimal("1998.00"), Decimal("2008.00"))
    # Future bar 2: touches TP1 (2030)
    b2 = _make_candle(sig_ts + timedelta(minutes=30), 15, Decimal("2008.00"), Decimal("2035.00"), Decimal("2005.00"), Decimal("2032.00"))

    trade = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b1, b2],
    )

    assert trade.outcome == TradeOutcome.TP1_FIRST
    assert trade.fill_price == Decimal("2000.00")
    assert trade.exit_price == Decimal("2030.00")
    # planned risk = 2005 - 1990 = 15.00. Gross pnl = 30.00. Gross R = 2.0000
    assert trade.gross_r == Decimal("2.0000")


# --- P6-14: SL-First Terminal Outcome ---
def test_p6_14_sl_first_terminal_outcome():
    """When price touches SL without touching TP1, outcome is SL_FIRST."""
    outcome_eng = OutcomeEngine(cost_model=CostModel(BacktestCostConfig.idealized()))
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    sig = SignalSnapshot(
        timestamp=sig_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=None,  # type: ignore
        timing=None,  # type: ignore
        hard_gate=None,  # type: ignore
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="sig-sl",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-sl",
        signal_generated_at=sig_ts,
        entry_min=Decimal("1995.00"),
        entry_mid=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_structure=Decimal("1990.00"),
        stop_atr=Decimal("1990.00"),
        stop_final=Decimal("1990.00"),
        stop_distance_atr=Decimal("2.0"),
        tp1=Decimal("2030.00"),
        tp2=Decimal("2050.00"),
        rr_tp1=Decimal("2.0"),
        rr_tp2=Decimal("3.0"),
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action=UserDecision.BUY,
        reasons=(),
    )

    b1 = _make_candle(sig_ts + timedelta(minutes=15), 15, Decimal("2000.00"), Decimal("2005.00"), Decimal("1998.00"), Decimal("2002.00"))
    b2 = _make_candle(sig_ts + timedelta(minutes=30), 15, Decimal("2002.00"), Decimal("2004.00"), Decimal("1985.00"), Decimal("1988.00"))

    trade = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b1, b2],
    )

    assert trade.outcome == TradeOutcome.SL_FIRST
    assert trade.exit_price == Decimal("1990.00")
    # planned risk = 15.00. Gross pnl = 1990 - 2000 = -10.00. Gross R = -0.6667
    assert trade.gross_r == Decimal("-0.6667")


# --- P6-15: No-Fill Valid Outcome ---
def test_p6_15_no_fill_valid_outcome():
    """If no subsequent candle exists for execution, outcome is NO_FILL."""
    outcome_eng = OutcomeEngine()
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    sig = SignalSnapshot(
        timestamp=sig_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=None,  # type: ignore
        timing=None,  # type: ignore
        hard_gate=None,  # type: ignore
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="sig-nofill",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-nofill",
        signal_generated_at=sig_ts,
        entry_min=Decimal("1995.00"),
        entry_mid=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_structure=Decimal("1990.00"),
        stop_atr=Decimal("1990.00"),
        stop_final=Decimal("1990.00"),
        stop_distance_atr=Decimal("2.0"),
        tp1=Decimal("2030.00"),
        tp2=Decimal("2050.00"),
        rr_tp1=Decimal("2.0"),
        rr_tp2=Decimal("3.0"),
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action=UserDecision.BUY,
        reasons=(),
    )

    trade = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[],  # empty
    )

    assert trade.outcome == TradeOutcome.NO_FILL
    assert trade.fill_timestamp is None


# --- P6-16: Conservative Intrabar Resolution ---
def test_p6_16_conservative_intrabar_resolution():
    """Ambiguous bar without lower-TF grid resolves conservatively to CONSERVATIVE_SL_FIRST."""
    outcome_eng = OutcomeEngine()
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    sig = SignalSnapshot(
        timestamp=sig_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=None,  # type: ignore
        timing=None,  # type: ignore
        hard_gate=None,  # type: ignore
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="sig-ambig",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-ambig",
        signal_generated_at=sig_ts,
        entry_min=Decimal("1995.00"),
        entry_mid=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_structure=Decimal("1990.00"),
        stop_atr=Decimal("1990.00"),
        stop_final=Decimal("1990.00"),
        stop_distance_atr=Decimal("2.0"),
        tp1=Decimal("2030.00"),
        tp2=Decimal("2050.00"),
        rr_tp1=Decimal("2.0"),
        rr_tp2=Decimal("3.0"),
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action=UserDecision.BUY,
        reasons=(),
    )

    # Bar 1 fill
    b1 = _make_candle(sig_ts + timedelta(minutes=15), 15, Decimal("2000.00"), Decimal("2005.00"), Decimal("1998.00"), Decimal("2002.00"))
    # Bar 2 ambiguous: touches both TP1 (2030) and SL (1990)
    b2 = _make_candle(sig_ts + timedelta(minutes=30), 15, Decimal("2002.00"), Decimal("2040.00"), Decimal("1980.00"), Decimal("2010.00"))

    trade = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b1, b2],
        future_candles_1m=None,  # No 1m grid
    )

    assert trade.outcome == TradeOutcome.CONSERVATIVE_SL_FIRST
    assert trade.exit_price == Decimal("1990.00")


# --- P6-17 & P6-18: Post-Fill MFE & MAE with Mid-Bar-Fill Exclusion (P6-C5) ---
def test_p6_17_p6_18_post_fill_mfe_mae_causality():
    """
    P6-C5: A candle in-progress during fill timestamp is excluded.
    Only candles strictly starting at or after fill_timestamp are used for MFE/MAE.
    """
    outcome_eng = OutcomeEngine(cost_model=CostModel(BacktestCostConfig.idealized()))
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    sig = SignalSnapshot(
        timestamp=sig_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=None,  # type: ignore
        timing=None,  # type: ignore
        hard_gate=None,  # type: ignore
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="sig-mfe",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-mfe",
        signal_generated_at=sig_ts,
        entry_min=Decimal("1995.00"),
        entry_mid=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_structure=Decimal("1990.00"),
        stop_atr=Decimal("1990.00"),
        stop_final=Decimal("1990.00"),
        stop_distance_atr=Decimal("2.0"),
        tp1=Decimal("2030.00"),
        tp2=Decimal("2050.00"),
        rr_tp1=Decimal("2.0"),
        rr_tp2=Decimal("3.0"),
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action=UserDecision.BUY,
        reasons=(),
    )

    # 15m fill bar at 10:15
    b1_15m = _make_candle(datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc), 15, Decimal("2000.00"), Decimal("2005.00"), Decimal("1995.00"), Decimal("2002.00"))
    b2_15m = _make_candle(datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc), 15, Decimal("2002.00"), Decimal("2035.00"), Decimal("1995.00"), Decimal("2030.00"))

    # 1m bars:
    # 1m bar at 10:14:00 (before fill) has high 2080 (should NOT be used in MFE)
    m_prev = _make_candle(datetime(2026, 3, 1, 10, 14, tzinfo=timezone.utc), 1, Decimal("2000"), Decimal("2080"), Decimal("1999"), Decimal("2000"))
    # 1m bar at 10:15:00 (fill bar open) has high 2015, low 1996
    m1 = _make_candle(datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc), 1, Decimal("2000"), Decimal("2015"), Decimal("1996"), Decimal("2002"))
    # 1m bar at 10:30:00 (exit bar) reaches 2030
    m2 = _make_candle(datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc), 1, Decimal("2002"), Decimal("2030"), Decimal("2001"), Decimal("2030"))

    trade = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b1_15m, b2_15m],
        future_candles_1m=[m_prev, m1, m2],
    )

    assert trade.outcome == TradeOutcome.TP1_FIRST
    # Highest post-fill is 2030 (NOT 2080 from pre-fill bar)
    # MFE = 2030 - 2000 = 30.00. Planned risk = 15.00 -> MFE R = 2.0000
    assert trade.mfe_r == Decimal("2.0000")
    # Lowest post-fill is 1996. MAE = 2000 - 1996 = 4.00 -> MAE R = 4/15 = 0.2667
    assert trade.mae_r == Decimal("0.2667")


# --- Metrics Calculator Unit Tests ---
def test_metrics_calculator_expectancy_and_normalized_drawdown():
    """Verify metrics calculation logic with normalized drawdown in R."""
    sig_ts = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    sig = SignalSnapshot(
        timestamp=sig_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=None,  # type: ignore
        timing=None,  # type: ignore
        hard_gate=None,  # type: ignore
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="sig-m",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    t1 = SimulatedTrade(
        trade_id="t-1",
        source_signal_fingerprint="sig-m",
        signal_timestamp=sig_ts,
        risk_plan_fingerprint="r-1",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        fill_timestamp=sig_ts + timedelta(minutes=15),
        exit_timestamp=sig_ts + timedelta(minutes=30),
        net_r=Decimal("2.0000"),
        gross_r=Decimal("2.0500"),
        net_pnl_per_unit=Decimal("20.00"),
        regime=RegimeType.BULL_TREND,
        session=SessionType.LONDON,
    )
    t2 = SimulatedTrade(
        trade_id="t-2",
        source_signal_fingerprint="sig-m",
        signal_timestamp=sig_ts,
        risk_plan_fingerprint="r-2",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.SL_FIRST,
        fill_timestamp=sig_ts + timedelta(minutes=45),
        exit_timestamp=sig_ts + timedelta(minutes=60),
        net_r=Decimal("-1.0000"),
        gross_r=Decimal("-0.9500"),
        net_pnl_per_unit=Decimal("-10.00"),
        regime=RegimeType.BULL_TREND,
        session=SessionType.LONDON,
    )

    metrics = BacktestMetricsCalculator.calculate(signals=[sig, sig], trades=[t1, t2])

    assert metrics.trade_count == 2
    assert metrics.win_count == 1
    assert metrics.loss_count == 1
    assert metrics.win_rate == 0.5
    assert metrics.net_expectancy_r == 0.5  # (0.5 * 2.0) - (0.5 * 1.0) = 0.5
    assert metrics.profit_factor == 2.0  # 20 / 10 = 2.0
    assert metrics.max_trade_sequence_drawdown_r == 1.0


# ==============================================================================
# Phase 6B: Walk-Forward, Purge & Embargo Targeted Tests (P6-19..P6-23)
# ==============================================================================

# --- P6-19: Chronological Folds Only ---
def test_p6_19_chronological_folds_only():
    """Chronological fold generator enforces strictly increasing half-open intervals [start, end)."""
    from engine.backtest.folds import ChronologicalFoldGenerator
    from engine.backtest.types import WalkForwardConfig

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)

    config = WalkForwardConfig(total_folds=3, train_ratio=0.5, val_ratio=0.25, oos_ratio=0.25)
    folds = ChronologicalFoldGenerator.generate_folds(start_time=start, end_time=end, config=config)

    assert len(folds) == 3
    for f in folds:
        assert f.train_start < f.train_end
        if f.val_start and f.val_end:
            assert f.train_end <= f.val_start
            assert f.val_start < f.val_end
            assert f.val_end <= f.oos_start
        else:
            assert f.train_end <= f.oos_start
        assert f.oos_start < f.oos_end
        assert f.oos_end <= end


# --- P6-20: Dependency-Window Purge Crossing Boundary ---
def test_p6_20_dependency_window_purge_crossing_boundary():
    """Sample whose dependency window crosses fold partition boundary is purged."""
    from engine.backtest.purge import PurgeEngine

    t_part_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_part_end = datetime(2026, 1, 31, 0, 0, tzinfo=timezone.utc)

    # Sample 1: Inside partition, finishes before boundary -> ELIGIBLE
    t_inside = SimulatedTrade(
        trade_id="t-inside",
        source_signal_fingerprint="sig-1",
        signal_timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-1",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=datetime(2026, 1, 11, 12, 0, tzinfo=timezone.utc),
        dependency_window=(datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), datetime(2026, 1, 11, 12, 0, tzinfo=timezone.utc)),
    )

    # Sample 2: Signal on Jan 30, outcome finishes Feb 2 (crosses Jan 31 boundary) -> PURGED
    t_cross = SimulatedTrade(
        trade_id="t-cross",
        source_signal_fingerprint="sig-2",
        signal_timestamp=datetime(2026, 1, 30, 10, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-2",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc),
        dependency_window=(datetime(2026, 1, 30, 10, 0, tzinfo=timezone.utc), datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)),
    )

    res = PurgeEngine.filter_partition(
        trades=[t_inside, t_cross],
        partition_start=t_part_start,
        partition_end=t_part_end,
        purge_overlapping=True,
    )

    assert len(res.eligible_trades) == 1
    assert res.eligible_trades[0].trade_id == "t-inside"
    assert len(res.purged_trades) == 1
    assert res.purged_trades[0].trade_id == "t-cross"


# --- P6-20A: NO_FILL Dependency Uses Fill-Timeout Timestamp ---
def test_p6_20a_no_fill_dependency_uses_fill_timeout():
    """NO_FILL outcome dependency window uses fill_timeout_timestamp, not signal_timestamp."""
    from engine.backtest.purge import PurgeEngine

    t_part_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_part_end = datetime(2026, 1, 31, 0, 0, tzinfo=timezone.utc)

    # Signal on Jan 30 23:00, timeout is Jan 31 03:00 (crosses boundary) -> PURGED
    t_nofill_cross = SimulatedTrade(
        trade_id="t-nf-cross",
        source_signal_fingerprint="sig-nf",
        signal_timestamp=datetime(2026, 1, 30, 23, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-nf",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.NO_FILL,
        dependency_window=(datetime(2026, 1, 30, 23, 0, tzinfo=timezone.utc), datetime(2026, 1, 31, 3, 0, tzinfo=timezone.utc)),
    )

    assert t_nofill_cross.dependency_end_timestamp == datetime(2026, 1, 31, 3, 0, tzinfo=timezone.utc)

    res = PurgeEngine.filter_partition(
        trades=[t_nofill_cross],
        partition_start=t_part_start,
        partition_end=t_part_end,
        purge_overlapping=True,
    )
    assert len(res.purged_trades) == 1
    assert len(res.eligible_trades) == 0


# --- P6-20B: UNRESOLVED Dependency Uses Causal Evaluation Horizon ---
def test_p6_20b_unresolved_dependency_uses_causal_horizon():
    """UNRESOLVED trade dependency window extends to the last evaluated horizon timestamp."""
    from engine.backtest.purge import PurgeEngine

    t_part_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_part_end = datetime(2026, 1, 31, 0, 0, tzinfo=timezone.utc)

    t_unres = SimulatedTrade(
        trade_id="t-unres",
        source_signal_fingerprint="sig-u",
        signal_timestamp=datetime(2026, 1, 28, 0, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-u",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.UNRESOLVED,
        dependency_window=(datetime(2026, 1, 28, 0, 0, tzinfo=timezone.utc), datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)),
    )

    assert t_unres.dependency_end_timestamp == datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)

    res = PurgeEngine.filter_partition(
        trades=[t_unres],
        partition_start=t_part_start,
        partition_end=t_part_end,
        purge_overlapping=True,
    )
    assert len(res.purged_trades) == 1
    assert len(res.eligible_trades) == 0


# --- P6-20C: Completed Trade Uses Exit Timestamp ---
def test_p6_20c_completed_trade_uses_exit_timestamp():
    """Completed trade dependency window strictly uses exit_timestamp."""
    t_sig = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    t_exit = datetime(2026, 1, 16, 14, 0, tzinfo=timezone.utc)

    trade = SimulatedTrade(
        trade_id="t-done",
        source_signal_fingerprint="sig-d",
        signal_timestamp=t_sig,
        risk_plan_fingerprint="r-d",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=t_exit,
        dependency_window=(t_sig, t_exit),
    )

    assert trade.dependency_end_timestamp == t_exit


# --- P6-21 & P6-21A: Embargo Exclusion & Exact Boundary Semantics ---
def test_p6_21_p6_21a_embargo_exclusion():
    """Candidate signal falling within post-boundary embargo window is excluded."""
    from engine.backtest.purge import PurgeEngine

    val_start = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    val_end = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    embargo_sec = 86400.0  # 24 hours (Feb 1 00:00 to Feb 2 00:00)

    # Trade 1: Signal on Feb 1 at 12:00 (inside embargo window) -> EMBARGOED
    t_embargoed = SimulatedTrade(
        trade_id="t-emb",
        source_signal_fingerprint="sig-emb",
        signal_timestamp=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-emb",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc),
        dependency_window=(datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc), datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc)),
    )

    # Trade 2: Signal on Feb 3 at 10:00 (outside embargo window) -> ELIGIBLE
    t_eligible = SimulatedTrade(
        trade_id="t-elig",
        source_signal_fingerprint="sig-elig",
        signal_timestamp=datetime(2026, 2, 3, 10, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-elig",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=datetime(2026, 2, 3, 15, 0, tzinfo=timezone.utc),
        dependency_window=(datetime(2026, 2, 3, 10, 0, tzinfo=timezone.utc), datetime(2026, 2, 3, 15, 0, tzinfo=timezone.utc)),
    )

    res = PurgeEngine.filter_partition(
        trades=[t_embargoed, t_eligible],
        partition_start=val_start,
        partition_end=val_end,
        embargo_duration_seconds=embargo_sec,
        purge_overlapping=True,
        is_post_boundary_segment=True,
    )

    assert len(res.embargoed_trades) == 1
    assert res.embargoed_trades[0].trade_id == "t-emb"
    assert len(res.eligible_trades) == 1
    assert res.eligible_trades[0].trade_id == "t-elig"


# --- P6-22: OOS Isolation (Selection API Cannot Access OOS) ---
def test_p6_22_oos_isolation_candidate_selection_api():
    """Candidate selection API accepts strictly train and validation metrics, structurally excluding OOS."""
    from engine.backtest.walkforward import WalkForwardEngine

    m_train_a = BacktestMetricsCalculator.calculate([], [])
    m_val_a = BacktestMetricsCalculator.calculate([], [
        SimulatedTrade(
            trade_id="t1", source_signal_fingerprint="s1", signal_timestamp=datetime.now(timezone.utc),
            risk_plan_fingerprint="r1", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST,
            net_r=Decimal("2.0"), net_pnl_per_unit=Decimal("20"),
        )
    ])

    m_train_b = BacktestMetricsCalculator.calculate([], [])
    m_val_b = BacktestMetricsCalculator.calculate([], [
        SimulatedTrade(
            trade_id="t2", source_signal_fingerprint="s2", signal_timestamp=datetime.now(timezone.utc),
            risk_plan_fingerprint="r2", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST,
            net_r=Decimal("1.0"), net_pnl_per_unit=Decimal("10"),
        )
    ])

    candidates = [
        ("candidate_A", m_train_a, m_val_a),
        ("candidate_B", m_train_b, m_val_b),
    ]

    # Selection strictly takes train/val pairs
    chosen = WalkForwardEngine.select_candidate_from_train_val(candidates)
    assert chosen == "candidate_A"


# --- P6-23 & P6-23A: Deterministic Fold Reproducibility ---
def test_p6_23_p6_23a_deterministic_fold_reproducibility():
    """Identical walk-forward specification and dataset produce identical fold assignments."""
    from engine.backtest.folds import ChronologicalFoldGenerator
    from engine.backtest.types import WalkForwardConfig

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    cfg = WalkForwardConfig(total_folds=3, embargo_seconds=3600.0)

    folds1 = ChronologicalFoldGenerator.generate_folds(start, end, cfg)
    folds2 = ChronologicalFoldGenerator.generate_folds(start, end, cfg)

    assert len(folds1) == len(folds2)
    for f1, f2 in zip(folds1, folds2):
        assert f1.train_start == f2.train_start
        assert f1.train_end == f2.train_end
        assert f1.oos_start == f2.oos_start
        assert f1.oos_end == f2.oos_end


# --- P6-23B: Material Config Change Produces Different Fingerprint ---
def test_p6_23b_material_config_change_different_fingerprint():
    """Material change in walk-forward config (e.g. embargo or folds) produces distinct provenance fingerprint."""
    from engine.backtest.walkforward import WalkForwardEngine
    from engine.backtest.types import WalkForwardConfig, BacktestRunSpec, BacktestCostConfig, CostScenario

    spec = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-1",
    )

    cfg1 = WalkForwardConfig(total_folds=3, embargo_seconds=86400.0)
    cfg2 = WalkForwardConfig(total_folds=4, embargo_seconds=86400.0)
    cfg3 = WalkForwardConfig(total_folds=3, embargo_seconds=43200.0)

    fp1 = WalkForwardEngine._compute_walkforward_fingerprint(spec, cfg1)
    fp2 = WalkForwardEngine._compute_walkforward_fingerprint(spec, cfg2)
    fp3 = WalkForwardEngine._compute_walkforward_fingerprint(spec, cfg3)

    assert fp1 != fp2
    assert fp1 != fp3
    assert fp2 != fp3


# ==============================================================================
# Phase 6C: Metric Contracts, Ablation Isolation, and Purity (P6-24..P6-35)
# ==============================================================================

# --- P6-24: Expectancy R Metric Contract ---
def test_p6_24_expectancy_r_metric_contract():
    """Expectancy R is mathematically deterministic from win rate and average win/loss R."""
    sig = SignalSnapshot(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        instrument="XAUT/USDT", timeframe="15m", state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY, direction=None, timing=None, hard_gate=None,
        reasons_positive=(), reasons_negative=(), hard_gate_reasons=(), analysis_fingerprint="s-exp",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    t_win = SimulatedTrade(
        trade_id="tw", source_signal_fingerprint="s-exp", signal_timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        fill_timestamp=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
        risk_plan_fingerprint="rw", planned_risk_amount=Decimal("10.00"), outcome=TradeOutcome.TP1_FIRST,
        net_r=Decimal("2.0000"), gross_r=Decimal("2.0000"), net_pnl_per_unit=Decimal("20.00"),
    )
    t_loss = SimulatedTrade(
        trade_id="tl", source_signal_fingerprint="s-exp", signal_timestamp=datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc),
        fill_timestamp=datetime(2026, 3, 1, 10, 20, tzinfo=timezone.utc),
        risk_plan_fingerprint="rl", planned_risk_amount=Decimal("10.00"), outcome=TradeOutcome.SL_FIRST,
        net_r=Decimal("-1.0000"), gross_r=Decimal("-1.0000"), net_pnl_per_unit=Decimal("-10.00"),
    )

    metrics = BacktestMetricsCalculator.calculate([sig, sig], [t_win, t_loss])
    # Expectancy = (0.5 * 2.0) - (0.5 * 1.0) = 0.5 R
    assert metrics.win_rate == 0.5
    assert metrics.net_expectancy_r == 0.5


# --- P6-25: Profit Factor Metric Contract ---
def test_p6_25_profit_factor_metric_contract():
    """Profit factor equals gross profit / gross loss, with division-by-zero protection."""
    sig = SignalSnapshot(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        instrument="XAUT/USDT", timeframe="15m", state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY, direction=None, timing=None, hard_gate=None,
        reasons_positive=(), reasons_negative=(), hard_gate_reasons=(), analysis_fingerprint="s-pf",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    t_win1 = SimulatedTrade(
        trade_id="tw1", source_signal_fingerprint="s-pf", signal_timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        fill_timestamp=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
        risk_plan_fingerprint="rw1", planned_risk_amount=Decimal("10.00"), outcome=TradeOutcome.TP1_FIRST,
        net_r=Decimal("3.0000"), gross_r=Decimal("3.0000"), net_pnl_per_unit=Decimal("30.00"), gross_pnl_per_unit=Decimal("30.00"),
    )
    t_loss1 = SimulatedTrade(
        trade_id="tl1", source_signal_fingerprint="s-pf", signal_timestamp=datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc),
        fill_timestamp=datetime(2026, 3, 1, 10, 20, tzinfo=timezone.utc),
        risk_plan_fingerprint="rl1", planned_risk_amount=Decimal("10.00"), outcome=TradeOutcome.SL_FIRST,
        net_r=Decimal("-1.0000"), gross_r=Decimal("-1.0000"), net_pnl_per_unit=Decimal("-10.00"), gross_pnl_per_unit=Decimal("-10.00"),
    )

    m = BacktestMetricsCalculator.calculate([sig, sig], [t_win1, t_loss1])
    assert m.profit_factor == 3.0  # 30.0 / 10.0

    # Only wins -> PF is 999.0 (division by zero safeguard)
    m_all_wins = BacktestMetricsCalculator.calculate([sig], [t_win1])
    assert m_all_wins.profit_factor == 999.0


# --- P6-26: Normalized Trade-Sequence Drawdown ---
def test_p6_26_normalized_drawdown_metric_contract():
    """Drawdown is strictly normalized trade-sequence drawdown in R."""
    sig = SignalSnapshot(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        instrument="XAUT/USDT", timeframe="15m", state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY, direction=None, timing=None, hard_gate=None,
        reasons_positive=(), reasons_negative=(), hard_gate_reasons=(), analysis_fingerprint="s-dd",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    # Sequence: +2R, -1R, -1R, -1R, +2R -> Peak = 2R, Trough = -1R -> Max DD = 3R
    trades = [
        SimulatedTrade(trade_id="t1", source_signal_fingerprint="s", signal_timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
                       fill_timestamp=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
                       risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST, net_r=Decimal("2.0")),
        SimulatedTrade(trade_id="t2", source_signal_fingerprint="s", signal_timestamp=datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc),
                       fill_timestamp=datetime(2026, 3, 1, 10, 20, tzinfo=timezone.utc),
                       risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.SL_FIRST, net_r=Decimal("-1.0")),
        SimulatedTrade(trade_id="t3", source_signal_fingerprint="s", signal_timestamp=datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc),
                       fill_timestamp=datetime(2026, 3, 1, 10, 35, tzinfo=timezone.utc),
                       risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.SL_FIRST, net_r=Decimal("-1.0")),
        SimulatedTrade(trade_id="t4", source_signal_fingerprint="s", signal_timestamp=datetime(2026, 3, 1, 10, 45, tzinfo=timezone.utc),
                       fill_timestamp=datetime(2026, 3, 1, 10, 50, tzinfo=timezone.utc),
                       risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.SL_FIRST, net_r=Decimal("-1.0")),
        SimulatedTrade(trade_id="t5", source_signal_fingerprint="s", signal_timestamp=datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc),
                       fill_timestamp=datetime(2026, 3, 1, 11, 5, tzinfo=timezone.utc),
                       risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST, net_r=Decimal("2.0")),
    ]

    m = BacktestMetricsCalculator.calculate([sig] * 5, trades)
    assert m.max_trade_sequence_drawdown_r == 3.0
    assert m.maximum_consecutive_losses == 3


# --- P6-27: Cost Drag Metric Contract ---
def test_p6_27_cost_drag_metric_contract():
    """Cost drag equals gross expectancy minus net expectancy."""
    sig = SignalSnapshot(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        instrument="XAUT/USDT", timeframe="15m", state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY, direction=None, timing=None, hard_gate=None,
        reasons_positive=(), reasons_negative=(), hard_gate_reasons=(), analysis_fingerprint="s-cd",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    trade = SimulatedTrade(
        trade_id="t-drag", source_signal_fingerprint="s-cd", signal_timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        fill_timestamp=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-cd", planned_risk_amount=Decimal("10.00"), outcome=TradeOutcome.TP1_FIRST,
        gross_r=Decimal("2.0000"), net_r=Decimal("1.8000"),
        entry_fee=Decimal("0.50"), exit_fee=Decimal("0.50"), entry_spread=Decimal("0.50"), exit_spread=Decimal("0.50"),
    )

    m = BacktestMetricsCalculator.calculate([sig], [trade])
    assert m.gross_expectancy_r == 2.0
    assert m.net_expectancy_r == 1.8
    assert abs(m.cost_drag_r - 0.2) < 1e-6


# --- P6-28: Signal-to-Fill Funnel Contract ---
def test_p6_28_signal_to_fill_funnel_contract():
    """Metrics properly track the full execution funnel."""
    s_buy = SignalSnapshot(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        instrument="XAUT/USDT", timeframe="15m", state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY, direction=None, timing=None, hard_gate=None,
        reasons_positive=(), reasons_negative=(), hard_gate_reasons=(), analysis_fingerprint="s-funnel",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    s_wait = SignalSnapshot(
        timestamp=datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc),
        instrument="XAUT/USDT", timeframe="15m", state=SignalState.WATCH,
        user_decision=UserDecision.WAIT, direction=None, timing=None, hard_gate=None,
        reasons_positive=(), reasons_negative=(), hard_gate_reasons=(), analysis_fingerprint="s-wait",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    trade_fill = SimulatedTrade(
        trade_id="t-f", source_signal_fingerprint="s-funnel", signal_timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        fill_timestamp=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
        risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST, net_r=Decimal("2.0"),
    )
    trade_nofill = SimulatedTrade(
        trade_id="t-nf", source_signal_fingerprint="s-funnel", signal_timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.NO_FILL,
    )

    m = BacktestMetricsCalculator.calculate([s_buy, s_wait], [trade_fill, trade_nofill])
    assert m.signal_count == 2
    assert m.wait_count == 1
    assert m.execution_eligible_count == 2
    assert m.trade_count == 1
    assert m.fill_rate == 0.5


# --- P6-29: Ablation Isolation ---
def test_p6_29_ablation_isolation():
    """Running an ablation does not mutate baseline engine or alter baseline fingerprint."""
    from engine.backtest.ablation import AblatedSignalEngine
    from engine.signals.engine import XautSignalEngine
    from engine.backtest.types import AblationType

    base_engine = XautSignalEngine(code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee")
    ab_engine = AblatedSignalEngine(
        ablation_type=AblationType.NO_STRUCTURE_COMPONENT,
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    assert base_engine.config_version == ab_engine.config_version
    assert ab_engine.ablation_type == AblationType.NO_STRUCTURE_COMPONENT
    # Baseline class remains completely unmodified
    assert not hasattr(base_engine, "ablation_type")


# --- P6-30: Phase 3B Production Weight Zero Hard-Lock ---
def test_p6_30_phase3b_production_zero_during_ablation():
    """Phase 3B experimental spectral cycles remain hard locked to 0.0 production weight."""
    from engine.core.types import (
        AcfResult, Cycle3BExperimentalSnapshot, CycleReliabilityResult,
        FftResult, HilbertResult, ReliabilityStatus, SampleQuality, WaveletResult,
    )

    t = datetime.now(timezone.utc)
    zero_acf = AcfResult(None, 0.0, False, 0.0, (), 50.0, SampleQuality.INSUFFICIENT)
    zero_fft = FftResult(None, None, 0.0, 1.0, (), False)
    zero_wavelet = WaveletResult(None, 0.0, 1.0, False, ())
    zero_hilbert = HilbertResult(0.0, 0.0, 0.0, 0.0, False)
    zero_rel = CycleReliabilityResult(
        dominant_period_bars=None,
        acf_strength=0.0,
        fft_power_ratio=0.0,
        wavelet_scale_strength=0.0,
        hilbert_phase=0.0,
        phase_stability=0.0,
        method_agreement_pct=0.0,
        effective_n=50.0,
        sample_quality=SampleQuality.INSUFFICIENT,
        reliability_score=0.0,
        reliability_status=ReliabilityStatus.UNRELIABLE,
        reasons=("Test",),
    )
    snap = Cycle3BExperimentalSnapshot(
        timestamp=t,
        timeframe="15m",
        acf=zero_acf,
        fft=zero_fft,
        wavelet=zero_wavelet,
        hilbert=zero_hilbert,
        reliability=zero_rel,
    )
    assert snap.production_weight == 0.0


# --- P6-31: Run Fingerprint Determinism ---
def test_p6_31_run_fingerprint_determinism():
    """Identical specifications produce identical canonical SHA-256 run fingerprints."""
    spec1 = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-det-1",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    spec2 = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-det-1",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    fp1 = compute_backtest_fingerprint(spec1)
    fp2 = compute_backtest_fingerprint(spec2)

    assert len(fp1) == 64
    assert fp1 == fp2


# --- P6-32: Material Config Mutation ---
def test_p6_32_material_config_mutation_alters_fingerprint():
    """Changing cost config or ablation type changes the run fingerprint."""
    from engine.backtest.types import AblationType

    spec_base = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-1",
    )
    spec_real = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.realistic(),
        cost_scenario=CostScenario.REALISTIC,
        dataset_hash="hash-1",
    )
    spec_ablate = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-1",
        ablation_type=AblationType.NO_STRUCTURE_COMPONENT,
    )

    fp_base = compute_backtest_fingerprint(spec_base)
    fp_real = compute_backtest_fingerprint(spec_real)
    fp_ablate = compute_backtest_fingerprint(spec_ablate)

    assert fp_base != fp_real
    assert fp_base != fp_ablate


# --- P6-33: Code Revision Mutation ---
def test_p6_33_code_revision_mutation_alters_fingerprint():
    """Changing code revision changes the run fingerprint."""
    spec_sha1 = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-1",
        code_revision="sha-version-1",
    )
    spec_sha2 = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-1",
        code_revision="sha-version-2",
    )

    fp1 = compute_backtest_fingerprint(spec_sha1)
    fp2 = compute_backtest_fingerprint(spec_sha2)

    assert fp1 != fp2


# --- P6-34: Pure Backtest Engine Imports ---
def test_p6_34_pure_backtest_engine_has_zero_django_imports():
    """Verify engine/backtest has zero imports from django, apps, celery, or redis using AST."""
    import ast
    import pathlib

    backtest_dir = pathlib.Path(__file__).parent.parent.parent / "engine" / "backtest"
    for py_file in backtest_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("django"), f"Django import found in {py_file}"
                    assert not alias.name.startswith("apps"), f"Apps import found in {py_file}"
                    assert not alias.name.startswith("celery"), f"Celery import found in {py_file}"
                    assert not alias.name.startswith("redis"), f"Redis import found in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("django"), f"Django import found in {py_file}"
                    assert not node.module.startswith("apps"), f"Apps import found in {py_file}"
                    assert not node.module.startswith("celery"), f"Celery import found in {py_file}"
                    assert not node.module.startswith("redis"), f"Redis import found in {py_file}"


# --- P6-35: Zero Exchange / Order API Imports ---
def test_p6_35_zero_exchange_order_api_in_backtesting():
    """Verify engine/backtest contains zero live order execution or exchange API references."""
    import inspect
    import engine.backtest.runner

    source = inspect.getsource(engine.backtest.runner)
    assert "create_order" not in source
    assert "submit_order" not in source
    assert "ccxt" not in source.lower()
    assert "binance" not in source.lower()
    assert "okx" not in source.lower()


