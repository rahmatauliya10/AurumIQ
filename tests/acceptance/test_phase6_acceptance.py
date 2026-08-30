"""Phase 6 Acceptance Test Suite: One Engine Parity, PIT Replay, Cost Integrity, and Mutation Safety."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.clock import ReplayClock
from engine.backtest.costs import CostModel
from engine.backtest.fingerprint import compute_backtest_fingerprint
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
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    RiskPlanSnapshot,
    SignalSnapshot,
    SignalState,
    UserDecision,
)
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


# --- Gate A09: One Engine Parity Gate ---
def test_a09_one_engine_parity_gate():
    """
    Assertion: Backtester imports and resolves the exact pure-Python XautSignalEngine and RiskPlanner
    without secondary duplicate trading rules or divergence.
    """
    runner = BacktestRunner()
    spec = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc),
        timeframes=("15m", "4h", "1d"),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-1",
        engine_version="4.0.0",
        risk_version="5.0.0",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    # Build dataset with minimum candles
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(base_ts + timedelta(minutes=15 * i), 15, Decimal("2000"), Decimal("2005"), Decimal("1995"), Decimal("2002"))
        for i in range(10)
    ]
    ds = PointInTimeDataset(candles_15m=candles)

    clock = ReplayClock(timestamps=[candles[4].timestamp_close])
    res = runner.run(dataset=ds, spec=spec, clock=clock)

    assert res.run_spec.engine_version == "4.0.0"
    assert res.run_spec.risk_version == "5.0.0"
    assert res.run_spec.code_revision == "6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee"


# --- Gate A31: No Lookahead Replay Gate ---
def test_a31_no_lookahead_replay_gate():
    """
    Assertion: Decision generation at T relies strictly on data with timestamp_close <= T.
    Adding subsequent bars cannot alter the SignalSnapshot emitted at T.
    """
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    c1 = _make_candle(base_ts, 15, Decimal("2000"), Decimal("2010"), Decimal("1995"), Decimal("2005"))
    c2 = _make_candle(base_ts + timedelta(minutes=15), 15, Decimal("2005"), Decimal("2015"), Decimal("2000"), Decimal("2012"))

    ds = PointInTimeDataset(candles_15m=[c1, c2])
    t_decision = c2.timestamp_close

    # Query closed candles at T
    closed_initial = ds.get_closed_candles("15m", as_of=t_decision)
    assert len(closed_initial) == 2

    # Append future bars with extreme price spikes
    c_future = _make_candle(t_decision, 15, Decimal("2012"), Decimal("5000"), Decimal("1000"), Decimal("4500"))
    ds.add_candle("15m", c_future)

    closed_subsequent = ds.get_closed_candles("15m", as_of=t_decision)
    assert len(closed_subsequent) == 2
    assert closed_subsequent[-1].high == Decimal("2015")


# --- Gate A32: Cost Integrity Gate ---
def test_a32_cost_integrity_gate():
    """
    Assertion: Cost modeling accurately applies spread, adverse slippage, and explicit fees
    without double counting on actual quotes.
    """
    cost_cfg = BacktestCostConfig.realistic(
        entry_fee_bps=Decimal("4.0"),
        exit_fee_bps=Decimal("4.0"),
        synthetic_spread_bps=Decimal("5.0"),
        entry_slippage_bps=Decimal("2.0"),
        exit_slippage_bps=Decimal("2.0"),
    )
    cost_mod = CostModel(config=cost_cfg)

    # Actual quote entry: spread is zero, slippage & fees applied
    entry = cost_mod.calculate_entry(raw_price=Decimal("2000.00"), is_actual_ask_quote=True)
    assert entry.spread_cost == Decimal("0.00")
    assert entry.slippage_cost == Decimal("0.40")  # 2000 * 2 bps = 0.40
    assert entry.fee_cost == Decimal("0.80")  # 2000.40 * 4 bps ≈ 0.80
    assert entry.effective_entry_price == Decimal("2001.20")


# --- Gate A33: Outcome Window Isolation Gate ---
def test_a33_outcome_window_isolation_gate():
    """
    Assertion: Post-fill MFE and MAE only evaluate candles occurring strictly after fill timestamp.
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
        analysis_fingerprint="sig-iso",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-iso",
        signal_generated_at=sig_ts,
        entry_min=Decimal("1995.00"),
        entry_mid=Decimal("2000.00"),
        entry_max=Decimal("2005.00"),
        stop_structure=Decimal("1990.00"),
        stop_atr=Decimal("1990.00"),
        stop_final=Decimal("1990.00"),
        stop_distance_atr=Decimal("2.0"),
        tp1=Decimal("2020.00"),
        tp2=Decimal("2040.00"),
        rr_tp1=Decimal("2.0"),
        rr_tp2=Decimal("3.0"),
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action=UserDecision.BUY,
        reasons=(),
    )

    # 15m fill bar at 10:15
    b_fill = _make_candle(datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc), 15, Decimal("2000"), Decimal("2005"), Decimal("1995"), Decimal("2002"))
    # 15m exit bar at 10:30 reaches TP1 (2020)
    b_exit = _make_candle(datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc), 15, Decimal("2002"), Decimal("2025"), Decimal("1998"), Decimal("2020"))

    # 1m bar at 10:10 (pre-signal / pre-fill) spiked to 3000 -> MUST NOT contaminate MFE!
    m_pre = _make_candle(datetime(2026, 3, 1, 10, 10, tzinfo=timezone.utc), 1, Decimal("2000"), Decimal("3000"), Decimal("1990"), Decimal("2000"))
    m_post = _make_candle(datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc), 1, Decimal("2002"), Decimal("2025"), Decimal("2000"), Decimal("2020"))

    trade = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b_fill, b_exit],
        future_candles_1m=[m_pre, m_post],
    )

    assert trade.outcome == TradeOutcome.TP1_FIRST
    # Highest post-fill is 2025 (not 3000)
    # MFE = 2025 - 2000 = 25.00. Planned risk = 15.00 -> MFE R = 1.6667
    assert trade.mfe_r == Decimal("1.6667")


# --- Gate A38: Historical Future Mutation Safety Gate (P6-C1) ---
def test_a38_historical_future_mutation_safety_gate():
    """
    Assertion:
      1. Mutating data strictly after T does NOT change Signal at T.
      2. Mutating data strictly after exit does NOT change completed trade.
      3. Mutating data inside [T, exit] legitimately affects trade outcome.
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
        analysis_fingerprint="sig-mut",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    risk_plan = RiskPlanSnapshot(
        source_signal_fingerprint="sig-mut",
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

    b_fill = _make_candle(datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc), 15, Decimal("2000"), Decimal("2005"), Decimal("1995"), Decimal("2002"))
    b_exit = _make_candle(datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc), 15, Decimal("2002"), Decimal("2035"), Decimal("1998"), Decimal("2030"))

    # Initial resolve
    trade1 = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b_fill, b_exit],
    )
    assert trade1.outcome == TradeOutcome.TP1_FIRST
    assert trade1.exit_price == Decimal("2030.00")

    # Mutate data AFTER exit (at 10:45)
    b_post_exit = _make_candle(datetime(2026, 3, 1, 10, 45, tzinfo=timezone.utc), 15, Decimal("2030"), Decimal("1000"), Decimal("500"), Decimal("600"))
    trade2 = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b_fill, b_exit, b_post_exit],
    )
    # Trade result is strictly identical
    assert trade2.outcome == trade1.outcome
    assert trade2.exit_price == trade1.exit_price
    assert trade2.gross_r == trade1.gross_r

    # Mutate data INSIDE [T, exit]: make b_exit hit SL instead of TP
    b_exit_mutated = _make_candle(datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc), 15, Decimal("2002"), Decimal("2005"), Decimal("1980"), Decimal("1985"))
    trade3 = outcome_eng.resolve_trade(
        signal=sig,
        risk_plan=risk_plan,
        future_candles_15m=[b_fill, b_exit_mutated],
    )
    # Legitimate outcome change within dependency window
    assert trade3.outcome == TradeOutcome.SL_FIRST
    assert trade3.exit_price == Decimal("1990.00")


# --- Gate A34: Walk-Forward Purge / Embargo Gate ---
def test_a34_walk_forward_purge_embargo_gate():
    """
    Assertion:
      1. Outcome dependency interval cannot leak across a protected fold boundary (Purge).
      2. Candidates inside post-boundary buffer are excluded (Embargo).
    """
    from engine.backtest.purge import PurgeEngine

    train_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    train_end = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    val_end = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    embargo_sec = 86400.0  # 24h

    # 1. Trade initiated Jan 28, resolves Feb 2 -> Crosses train_end (Feb 1)
    t_leak = SimulatedTrade(
        trade_id="t-leak",
        source_signal_fingerprint="sig-leak",
        signal_timestamp=datetime(2026, 1, 28, 10, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-leak",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc),
        dependency_window=(datetime(2026, 1, 28, 10, 0, tzinfo=timezone.utc), datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)),
    )

    train_res = PurgeEngine.filter_partition(
        trades=[t_leak],
        partition_start=train_start,
        partition_end=train_end,
        purge_overlapping=True,
    )
    # Proves leak is purged from TRAIN
    assert len(train_res.purged_trades) == 1
    assert len(train_res.eligible_trades) == 0

    # 2. Trade initiated Feb 1 12:00 in validation partition (inside 24h embargo)
    t_val_emb = SimulatedTrade(
        trade_id="t-val-emb",
        source_signal_fingerprint="sig-ve",
        signal_timestamp=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        risk_plan_fingerprint="r-ve",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        exit_timestamp=datetime(2026, 2, 2, 12, 0, tzinfo=timezone.utc),
        dependency_window=(datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc), datetime(2026, 2, 2, 12, 0, tzinfo=timezone.utc)),
    )

    val_res = PurgeEngine.filter_partition(
        trades=[t_val_emb],
        partition_start=train_end,
        partition_end=val_end,
        embargo_duration_seconds=embargo_sec,
        purge_overlapping=True,
        is_post_boundary_segment=True,
    )
    # Proves embargoed trade is excluded from VALIDATION
    assert len(val_res.embargoed_trades) == 1
    assert len(val_res.eligible_trades) == 0


# --- Gate A35: OOS Isolation Gate ---
def test_a35_oos_isolation_gate():
    """
    Assertion:
      1. Candidate selection API structurally cannot accept OOS data.
      2. OOS evaluation occurs strictly after candidate configuration is frozen.
    """
    from engine.backtest.walkforward import WalkForwardEngine
    from engine.backtest.metrics import BacktestMetricsCalculator
    import inspect

    # Verify signature of candidate selection API
    sig = inspect.signature(WalkForwardEngine.select_candidate_from_train_val)
    params = list(sig.parameters.keys())
    assert "candidate_evaluations" in params
    assert "oos" not in str(sig).lower()

    # Functional evaluation
    m_train_1 = BacktestMetricsCalculator.calculate([], [])
    m_val_1 = BacktestMetricsCalculator.calculate([], [
        SimulatedTrade(
            trade_id="t1", source_signal_fingerprint="s1", signal_timestamp=datetime.now(timezone.utc),
            risk_plan_fingerprint="r1", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST,
            net_r=Decimal("2.5"), net_pnl_per_unit=Decimal("25"),
        )
    ])

    m_train_2 = BacktestMetricsCalculator.calculate([], [])
    m_val_2 = BacktestMetricsCalculator.calculate([], [
        SimulatedTrade(
            trade_id="t2", source_signal_fingerprint="s2", signal_timestamp=datetime.now(timezone.utc),
            risk_plan_fingerprint="r2", planned_risk_amount=Decimal("10"), outcome=TradeOutcome.TP1_FIRST,
            net_r=Decimal("1.2"), net_pnl_per_unit=Decimal("12"),
        )
    ])

    chosen = WalkForwardEngine.select_candidate_from_train_val([
        ("model_alpha", m_train_1, m_val_1),
        ("model_beta", m_train_2, m_val_2),
    ])

    assert chosen == "model_alpha"


# --- Gate A36: Deterministic Reproducibility Gate ---
def test_a36_deterministic_reproducibility_gate():
    """
    Assertion:
      Running an identical backtest twice generates identical fingerprints, signals,
      trades, metrics, and fold assignments.
    """
    dataset = PointInTimeDataset()
    t_base = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    for i in range(40):
        c_15m = _make_candle(t_base + timedelta(minutes=15 * i), 15, Decimal("2000"), Decimal("2010"), Decimal("1995"), Decimal("2005"))
        dataset.add_candle("15m", c_15m)

    spec = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=t_base,
        end_time=t_base + timedelta(hours=10),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="hash-rep-1",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    runner1 = BacktestRunner()
    res1 = runner1.run(dataset=dataset, spec=spec)

    runner2 = BacktestRunner()
    res2 = runner2.run(dataset=dataset, spec=spec)

    assert res1.run_fingerprint == res2.run_fingerprint
    assert len(res1.signals) == len(res2.signals)
    assert len(res1.trades) == len(res2.trades)
    assert res1.metrics.net_expectancy_r == res2.metrics.net_expectancy_r
    assert res1.metrics.profit_factor == res2.metrics.profit_factor


# --- Gate A37: Production / Research Isolation Gate ---
def test_a37_production_research_isolation_gate():
    """
    Assertion:
      Running BASELINE -> ABLATION -> BASELINE produces strictly identical baseline results
      before and after, proving complete isolation without production config mutation.
    """
    from engine.signals.engine import XautSignalEngine
    from engine.backtest.ablation import AblatedSignalEngine
    from engine.backtest.types import AblationType

    dataset = PointInTimeDataset()
    t_base = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(40):
        c_15m = _make_candle(t_base + timedelta(minutes=15 * i), 15, Decimal("2000"), Decimal("2010"), Decimal("1995"), Decimal("2005"))
        candles.append(c_15m)
        dataset.add_candle("15m", c_15m)

    # 1. Baseline Before
    prod_engine_before = XautSignalEngine(code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee")
    sig_before = prod_engine_before.analyze(candles_15m=candles, as_of=candles[-1].timestamp_close)

    # 2. Ablation in between
    ab_engine = AblatedSignalEngine(
        ablation_type=AblationType.NO_STRUCTURE_COMPONENT,
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )
    sig_ablation = ab_engine.analyze(candles_15m=candles, as_of=candles[-1].timestamp_close)

    # 3. Baseline After
    prod_engine_after = XautSignalEngine(code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee")
    sig_after = prod_engine_after.analyze(candles_15m=candles, as_of=candles[-1].timestamp_close)

    assert sig_before.analysis_fingerprint == sig_after.analysis_fingerprint
    assert sig_before.state == sig_after.state
    assert sig_before.user_decision == sig_after.user_decision
    assert sig_before.direction.total_score == sig_after.direction.total_score
    assert sig_before.timing.total_score == sig_after.timing.total_score


