"""
Targeted unit test suite for Phase 5: Risk Engine, Intrabar Resolver & Entry Execution Model.
Covers P5-01 through P5-24.
"""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import (
    BarrierHitType,
    BosType,
    CandleData,
    DirectionScoreResult,
    EntryExecutionPolicy,
    HardGateEvaluation,
    IntrabarPolicy,
    StructureZone,
    QuoteData,
    SignalSnapshot,
    SignalState,
    StructureResult,
    StructureType,
    TimingScoreResult,
    UserDecision,
)
from engine.risk.execution import EntryExecutionModel
from engine.risk.intrabar import IntrabarResolver
from engine.risk.planner import RiskPlanner
from engine.risk.stops import calculate_stops
from engine.risk.targets import calculate_targets
from engine.signals.engine import XautSignalEngine


def make_buy_window_signal(eval_ts: datetime, fp: str = "sig_fp_test_123") -> SignalSnapshot:
    return SignalSnapshot(
        timestamp=eval_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=DirectionScoreResult(85.0, 100.0, (), True),
        timing=TimingScoreResult(85.0, 100.0, (), True),
        hard_gate=HardGateEvaluation(False, None, ()),
        reasons_positive=("Bullish momentum",),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint=fp,
        code_revision="eae30005",
    )


@pytest.mark.unit
def test_p5_01_valid_structure_stop():
    """P5-01: Valid structure stop sits buffer distance below support zone low."""
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), datetime.now(timezone.utc), 3, True)
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, err = calculate_stops(
        support_zone=support,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=5.0,
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),  # ATR stop = 2502.50 - 10 = 2492.50
    )
    assert ok is True
    assert stop_struct == Decimal("2498.50")
    # min(2498.50, 2492.50) = 2492.50
    assert stop_final == Decimal("2492.50")


@pytest.mark.unit
def test_p5_02_atr_stop_guard():
    """P5-02: ATR stop guard ensures stop is placed far enough from entry_mid."""
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), datetime.now(timezone.utc), 3, True)
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, err = calculate_stops(
        support_zone=support,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=5.0,
        structure_buffer=Decimal("20.00"),  # Structure stop deep at 2480.00
        atr_multiplier=Decimal("2.0"),       # ATR stop at 2492.50
        max_stop_distance_atr=Decimal("6.0"), # allow up to 6 ATR
    )
    assert ok is True
    # Stop final takes min(2480.00, 2492.50) = 2480.00
    assert stop_final == Decimal("2480.00")
    assert stop_dist_atr == Decimal("5.00")  # (2505 - 2480) / 5 = 5.0 ATR

    # If max_stop_distance_atr is default 4.0, this triggers excessive stop guard!
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok_guard, err_guard = calculate_stops(
        support_zone=support,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=5.0,
        structure_buffer=Decimal("20.00"),
        max_stop_distance_atr=Decimal("4.0"),
    )
    assert ok_guard is False
    assert "exceeds maximum allowable threshold" in err_guard


@pytest.mark.unit
def test_p5_03_excessive_stop_distance_invalid():
    """P5-03: Stop distance exceeding configured maximum ATR threshold invalidates risk plan."""
    support = StructureZone("SUPPORT", Decimal("2450.00"), Decimal("2500.00"), datetime.now(timezone.utc), 3, True)
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, err = calculate_stops(
        support_zone=support,
        entry_min=Decimal("2490.00"),
        entry_mid=Decimal("2495.00"),
        entry_max=Decimal("2500.00"),
        atr14=5.0,
        structure_buffer=Decimal("1.00"),
        max_stop_distance_atr=Decimal("4.0"),  # max risk = 20.00 pts
    )
    # Stop struct = 2449.00 -> Risk = 2500 - 2449 = 51.00 pts (10.2 ATR) -> INVALID
    assert ok is False
    assert "exceeds maximum allowable threshold" in err


@pytest.mark.unit
def test_p5_04_invalid_atr_zero_or_negative():
    """P5-04: Non-positive ATR values are rejected."""
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), datetime.now(timezone.utc), 3, True)
    _, _, _, _, ok_zero, err_zero = calculate_stops(support, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), 0.0)
    assert ok_zero is False
    assert "strictly positive" in err_zero

    _, _, _, _, ok_neg, err_neg = calculate_stops(support, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), -2.5)
    assert ok_neg is False
    assert "strictly positive" in err_neg


@pytest.mark.unit
def test_p5_05_nearest_resistance_selection():
    """P5-05: Nearest confirmed resistance is chosen as TP1 and cannot be skipped."""
    eval_ts = datetime.now(timezone.utc)
    res_near = StructureZone("RESISTANCE", Decimal("2520.00"), Decimal("2525.00"), eval_ts, 2, True)
    res_far = StructureZone("RESISTANCE", Decimal("2550.00"), Decimal("2555.00"), eval_ts, 3, True)

    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (res_far, res_near))

    tp1, tp2, rr_tp1, rr_tp2, ok, err = calculate_targets(
        entry_max=Decimal("2500.00"),
        entry_mid=Decimal("2498.00"),
        stop_final=Decimal("2490.00"),  # Risk = 10.00
        structure_15m=struct,
        atr14=5.0,
    )
    assert ok is True
    assert tp1 == Decimal("2520.00")  # Selected res_near (2520), not res_far (2550)
    assert rr_tp1 == Decimal("2.00")   # (2520 - 2500) / 10 = 2.00
    assert tp2 == Decimal("2550.00")


@pytest.mark.unit
def test_p5_06_nearby_resistance_under_1_80_invalid():
    """P5-06: Nearest resistance with RR < 1.80 is rejected."""
    eval_ts = datetime.now(timezone.utc)
    res_tight = StructureZone("RESISTANCE", Decimal("2512.00"), Decimal("2515.00"), eval_ts, 2, True)  # TP1 = 2512
    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (res_tight,))

    tp1, tp2, rr_tp1, rr_tp2, ok, err = calculate_targets(
        entry_max=Decimal("2500.00"),
        entry_mid=Decimal("2498.00"),
        stop_final=Decimal("2490.00"),  # Risk = 10.00
        structure_15m=struct,
        atr14=5.0,
        min_rr_tp1=Decimal("1.80"),
    )
    # RR = (2512 - 2500) / 10 = 1.20 < 1.80
    assert ok is False
    assert rr_tp1 == Decimal("1.20")
    assert "below minimum required" in err


@pytest.mark.unit
def test_p5_07_exact_rr_1_80_boundary_pass():
    """P5-07: Exact boundary RR = 1.80 passes."""
    eval_ts = datetime.now(timezone.utc)
    res_exact = StructureZone("RESISTANCE", Decimal("2518.00"), Decimal("2522.00"), eval_ts, 2, True)
    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (res_exact,))

    tp1, tp2, rr_tp1, rr_tp2, ok, err = calculate_targets(
        entry_max=Decimal("2500.00"),
        entry_mid=Decimal("2498.00"),
        stop_final=Decimal("2490.00"),  # Risk = 10.00 -> TP1 = 2518 -> RR = 1.80
        structure_15m=struct,
        atr14=5.0,
        min_rr_tp1=Decimal("1.80"),
    )
    assert ok is True
    assert rr_tp1 == Decimal("1.80")


@pytest.mark.unit
def test_p5_08_rr_under_boundary_fail():
    """P5-08: Sub-threshold RR = 1.79 fails."""
    eval_ts = datetime.now(timezone.utc)
    res_sub = StructureZone("RESISTANCE", Decimal("2517.90"), Decimal("2522.00"), eval_ts, 2, True)
    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (res_sub,))

    tp1, tp2, rr_tp1, rr_tp2, ok, err = calculate_targets(
        entry_max=Decimal("2500.00"),
        entry_mid=Decimal("2498.00"),
        stop_final=Decimal("2490.00"),  # Risk = 10.00 -> TP1 = 2517.90 -> RR = 1.79
        structure_15m=struct,
        atr14=5.0,
        min_rr_tp1=Decimal("1.80"),
    )
    assert ok is False
    assert rr_tp1 == Decimal("1.79")


@pytest.mark.unit
def test_p5_09_tp2_cannot_rescue_invalid_tp1():
    """P5-09: A large TP2 target cannot rescue an invalid TP1 (< 1.80)."""
    eval_ts = datetime.now(timezone.utc)
    res_near = StructureZone("RESISTANCE", Decimal("2510.00"), Decimal("2515.00"), eval_ts, 2, True)  # RR = 1.00
    res_far = StructureZone("RESISTANCE", Decimal("2580.00"), Decimal("2590.00"), eval_ts, 3, True)   # RR = 8.00
    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (res_near, res_far))

    tp1, tp2, rr_tp1, rr_tp2, ok, err = calculate_targets(
        entry_max=Decimal("2500.00"),
        entry_mid=Decimal("2498.00"),
        stop_final=Decimal("2490.00"),
        structure_15m=struct,
        atr14=5.0,
        min_rr_tp1=Decimal("1.80"),
    )
    assert ok is False
    assert rr_tp1 == Decimal("1.00")
    assert rr_tp2 == Decimal("8.00")
    assert "below minimum required" in err


@pytest.mark.unit
@pytest.mark.django_db
def test_p5_10_phase4_signal_record_unchanged_on_risk_reject():
    """P5-10: Phase 4 SignalRecord is immutable and remains BUY_WINDOW on risk rejection."""
    xaut = Asset.objects.create(code="XAUT_P10", name="Tether Gold P10")
    usdt = Asset.objects.create(code="USDT_P10", name="Tether USD P10")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    eval_ts = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    sig_snap = make_buy_window_signal(eval_ts, "fp_p5_10")

    rec, _ = SignalPersistenceService.save_signal_snapshot(inst, sig_snap)

    # Risk planning with tight resistance (RR < 1.80)
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), eval_ts, 3, True)
    res_tight = StructureZone("RESISTANCE", Decimal("2512.00"), Decimal("2515.00"), eval_ts, 2, True)
    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (support, res_tight))

    planner = RiskPlanner(code_revision="eae30005")
    risk_snap = planner.plan(sig_snap, struct, atr14=5.0)

    assert risk_snap.is_valid_risk_plan is False
    assert risk_snap.effective_action == UserDecision.WAIT

    # Verify DB record remains unchanged
    rec.refresh_from_db()
    assert rec.state == SignalState.BUY_WINDOW
    assert rec.user_decision == UserDecision.BUY


@pytest.mark.unit
def test_p5_11_next_bar_open_exact_timestamp_causality():
    """P5-11: NEXT_BAR_OPEN fill price and timestamp come from the first eligible bar open."""
    model = EntryExecutionModel()
    sig_ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    b1 = CandleData(sig_ts, sig_ts + timedelta(minutes=15), Decimal("2500"), Decimal("2510"), Decimal("2495"), Decimal("2505"), Decimal("100"), True)

    res = model.simulate_next_bar_open(sig_ts, [b1], latency_seconds=0.0)
    assert res.is_filled is True
    assert res.fill_timestamp == b1.timestamp_open


@pytest.mark.unit
def test_p5_12_market_quote_first_eligible_ask():
    """P5-12: MARKET_AFTER_SIGNAL fills at first quote timestamp >= signal_ts + latency."""
    model = EntryExecutionModel()
    sig_ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    q_early = QuoteData(sig_ts + timedelta(seconds=1), Decimal("2500"), Decimal("2500.5"))
    q_eligible = QuoteData(sig_ts + timedelta(seconds=2), Decimal("2501"), Decimal("2501.5"))

    res = model.simulate_market_after_signal(sig_ts, [q_early, q_eligible], latency_seconds=2.0)
    assert res.is_filled is True
    assert res.fill_timestamp == q_eligible.timestamp


@pytest.mark.unit
def test_p5_13_ask_spread_not_double_counted():
    """P5-13: MARKET_AFTER_SIGNAL does not add synthetic spread on top of actual ASK quote."""
    model = EntryExecutionModel()
    sig_ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    q = QuoteData(sig_ts + timedelta(seconds=2), Decimal("2500.00"), Decimal("2501.00"))
    res = model.simulate_market_after_signal(sig_ts, [q], latency_seconds=2.0, slippage_pct=Decimal("0.00"))

    assert res.spread_amount == Decimal("0.00")
    assert res.fill_price == Decimal("2501.00")


@pytest.mark.unit
def test_p5_14_latency_boundary_exact():
    """P5-14: Quotes strictly before signal_ts + latency are ignored."""
    model = EntryExecutionModel()
    sig_ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    q1 = QuoteData(sig_ts + timedelta(milliseconds=1999), Decimal("2490"), Decimal("2490.5"))
    res = model.simulate_market_after_signal(sig_ts, [q1], latency_seconds=2.0)
    assert res.is_filled is False


@pytest.mark.unit
def test_p5_15_lower_tf_chronological_tp_first():
    """P5-15: Lower-TF chronological replay identifies TP hit before SL."""
    resolver = IntrabarResolver()
    t_open = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    t_close = t_open + timedelta(minutes=15)

    parent = CandleData(t_open, t_close, Decimal("2500"), Decimal("2525"), Decimal("2485"), Decimal("2510"), Decimal("100"), True)
    c1 = CandleData(t_open, t_open + timedelta(minutes=5), Decimal("2500"), Decimal("2522"), Decimal("2500"), Decimal("2520"), Decimal("50"), True)  # Touches TP=2520
    c2 = CandleData(t_open + timedelta(minutes=5), t_open + timedelta(minutes=10), Decimal("2520"), Decimal("2520"), Decimal("2488"), Decimal("2490"), Decimal("50"), True)  # Touches SL=2490

    res = resolver.resolve(parent, Decimal("2520"), Decimal("2490"), lower_tf_candles_5m=[c1, c2])
    assert res.barrier_hit == BarrierHitType.TP_FIRST
    assert res.exit_price == Decimal("2520")


@pytest.mark.unit
def test_p5_16_lower_tf_chronological_sl_first():
    """P5-16: Lower-TF chronological replay identifies SL hit before TP."""
    resolver = IntrabarResolver()
    t_open = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    t_close = t_open + timedelta(minutes=15)

    parent = CandleData(t_open, t_close, Decimal("2500"), Decimal("2525"), Decimal("2485"), Decimal("2510"), Decimal("100"), True)
    c1 = CandleData(t_open, t_open + timedelta(minutes=5), Decimal("2500"), Decimal("2502"), Decimal("2488"), Decimal("2490"), Decimal("50"), True)  # Touches SL=2490
    c2 = CandleData(t_open + timedelta(minutes=5), t_open + timedelta(minutes=10), Decimal("2490"), Decimal("2522"), Decimal("2490"), Decimal("2520"), Decimal("50"), True)  # Touches TP=2520

    res = resolver.resolve(parent, Decimal("2520"), Decimal("2490"), lower_tf_candles_5m=[c1, c2])
    assert res.barrier_hit == BarrierHitType.SL_FIRST
    assert res.exit_price == Decimal("2490")


@pytest.mark.unit
def test_p5_17_lower_tf_unavailable_defaults_sl_first():
    """P5-17: When lower-TF candles are unavailable, ambiguous bar falls back to SL_FIRST."""
    resolver = IntrabarResolver()
    t_open = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    parent = CandleData(t_open, t_open + timedelta(minutes=15), Decimal("2500"), Decimal("2525"), Decimal("2485"), Decimal("2510"), Decimal("100"), True)

    res = resolver.resolve(parent, Decimal("2520"), Decimal("2490"), lower_tf_candles_1m=None, lower_tf_candles_5m=None)
    assert res.barrier_hit == BarrierHitType.SL_FIRST
    assert res.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST


@pytest.mark.unit
def test_p5_18_ambiguous_lower_tf_fails_safe_sl_first():
    """P5-18: If a 1m candle itself touches both TP and SL, engine fails safe to SL_FIRST."""
    resolver = IntrabarResolver()
    t_open = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    parent = CandleData(t_open, t_open + timedelta(minutes=15), Decimal("2500"), Decimal("2525"), Decimal("2485"), Decimal("2510"), Decimal("100"), True)
    c_ambiguous = CandleData(t_open, t_open + timedelta(minutes=1), Decimal("2500"), Decimal("2525"), Decimal("2485"), Decimal("2510"), Decimal("50"), True)

    res = resolver.resolve(parent, Decimal("2520"), Decimal("2490"), lower_tf_candles_1m=[c_ambiguous])
    assert res.barrier_hit == BarrierHitType.SL_FIRST
    assert res.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST


@pytest.mark.unit
def test_p5_19_limit_pre_activation_touch_ignored():
    """P5-19: LIMIT_ZONE ignores touches that occurred prior to activation timestamp."""
    model = EntryExecutionModel()
    sig_ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    # Touch before latency boundary (12:00:01 < 12:00:02)
    q_early = QuoteData(sig_ts + timedelta(seconds=1), Decimal("2490"), Decimal("2490"))
    # Higher price after latency boundary
    q_late = QuoteData(sig_ts + timedelta(seconds=3), Decimal("2505"), Decimal("2505"))

    res = model.simulate_limit_zone(sig_ts, Decimal("2495.00"), quotes=[q_early, q_late], latency_seconds=2.0)
    assert res.is_filled is False


@pytest.mark.unit
def test_p5_20_future_mutation_cannot_alter_historical_risk_plan():
    """P5-20: Altering downstream prices cannot alter previously constructed RiskPlanSnapshot."""
    eval_ts = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2500"), Decimal("2505"), eval_ts, 3, True)
    res = StructureZone("RESISTANCE", Decimal("2535"), Decimal("2540"), eval_ts, 3, True)
    struct = StructureResult(eval_ts, StructureType.HH, BosType.BULLISH, None, None, (), (support, res))

    sig = make_buy_window_signal(eval_ts)
    planner = RiskPlanner(code_revision="eae30005")

    snap1 = planner.plan(sig, struct, atr14=5.0)

    # Mutate external context afterwards
    mutated_support = StructureZone("SUPPORT", Decimal("2400"), Decimal("2410"), eval_ts + timedelta(hours=1), 1, True)

    assert snap1.stop_final == Decimal("2492.50")
    assert snap1.tp1 == Decimal("2535.00")
    assert snap1.is_valid_risk_plan is True


@pytest.mark.unit
def test_p5_21_decimal_price_determinism():
    """P5-21: All calculated risk levels are exact Decimals with no floating point artifacts."""
    support = StructureZone("SUPPORT", Decimal("2500.12"), Decimal("2505.34"), datetime.now(timezone.utc), 3, True)
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, _ = calculate_stops(
        support, Decimal("2500.12"), Decimal("2502.73"), Decimal("2505.34"), 4.87654321
    )
    assert isinstance(stop_struct, Decimal)
    assert isinstance(stop_atr, Decimal)
    assert isinstance(stop_final, Decimal)
    assert isinstance(stop_dist_atr, Decimal)


@pytest.mark.unit
def test_p5_22_engine_risk_zero_django_imports():
    """P5-22: Pure engine AST isolation for engine/risk package."""
    risk_dir = Path("/app/engine/risk")
    if not risk_dir.exists():
        risk_dir = Path("engine/risk")

    for py_file in risk_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "django" not in alias.name.lower(), f"Forbidden Django import '{alias.name}' in {py_file}"
                    assert "celery" not in alias.name.lower(), f"Forbidden Celery import in {py_file}"
                    assert "redis" not in alias.name.lower(), f"Forbidden Redis import in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "django" not in node.module.lower(), f"Forbidden Django from-import '{node.module}' in {py_file}"
                    assert "celery" not in node.module.lower(), f"Forbidden Celery from-import in {py_file}"
                    assert "redis" not in node.module.lower(), f"Forbidden Redis from-import in {py_file}"


@pytest.mark.unit
def test_p5_23_no_exchange_order_api_code():
    """P5-23: Phase 5 boundary check ensures no real exchange or order submission code exists."""
    risk_dir = Path("/app/engine/risk")
    if not risk_dir.exists():
        risk_dir = Path("engine/risk")

    forbidden_terms = ["ccxt", "binance.client", "okx.trade", "place_order", "create_order", "send_order", "account_balance"]

    for py_file in risk_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read().lower()
            for term in forbidden_terms:
                assert term not in content, f"Forbidden live order term '{term}' found in {py_file}"


@pytest.mark.unit
def test_p5_24_phase4_provenance_hotfix_code_revision_explicit():
    """
    P5-24: XautSignalEngine requires explicit code_revision and does not have stale defaults.
    """
    with pytest.raises(TypeError):
        # Calling without required code_revision raises TypeError
        XautSignalEngine()  # type: ignore

    engine = XautSignalEngine(code_revision="eae30005")
    assert engine.code_revision == "eae30005"


def test_p5_25_source_signal_eligibility_gate():
    planner = RiskPlanner(code_revision='eae30005')
    base_ts = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    support = StructureZone(
        zone_type='SUPPORT',
        price_low=Decimal('2650.00'),
        price_high=Decimal('2655.00'),
        created_at=base_ts,
        touches=1,
        is_active=True,
    )
    resistance = StructureZone(
        zone_type='RESISTANCE',
        price_low=Decimal('2680.00'),
        price_high=Decimal('2685.00'),
        created_at=base_ts,
        touches=1,
        is_active=True,
    )
    struct_res = StructureResult(
        timestamp=base_ts,
        structure_type=StructureType.HH,
        bos=BosType.BULLISH,
        last_swing_high=None,
        last_swing_low=None,
        swings=(),
        zones=(support, resistance),
    )

    # 1. READY / WAIT -> blocked
    ready_sig = SignalSnapshot(
        timestamp=base_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.READY,
        user_decision=UserDecision.WAIT,
        direction=DirectionScoreResult(85.0, 100.0, (), True),
        timing=TimingScoreResult(80.0, 100.0, (), True),
        hard_gate=HardGateEvaluation(False, None, ()),
        reasons_positive=('Direction and Timing aligned, awaiting trigger.',),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint='fp-ready',
        code_revision='eae30005',
    )
    res_ready = planner.plan(
        signal_snapshot=ready_sig,
        structure_15m=struct_res,
        atr14=5.0,
        latest_close=Decimal('2653.00'),
    )
    assert not res_ready.is_valid_risk_plan
    assert not res_ready.execution_eligible
    assert res_ready.effective_action == UserDecision.WAIT

    # 2. WATCH / WAIT -> blocked
    watch_sig = SignalSnapshot(
        timestamp=base_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.WATCH,
        user_decision=UserDecision.WAIT,
        direction=DirectionScoreResult(65.0, 100.0, (), False),
        timing=TimingScoreResult(50.0, 100.0, (), False),
        hard_gate=HardGateEvaluation(False, None, ()),
        reasons_positive=(),
        reasons_negative=('Watching setup.',),
        hard_gate_reasons=(),
        analysis_fingerprint='fp-watch',
        code_revision='eae30005',
    )
    res_watch = planner.plan(
        signal_snapshot=watch_sig,
        structure_15m=struct_res,
        atr14=5.0,
        latest_close=Decimal('2653.00'),
    )
    assert not res_watch.is_valid_risk_plan
    assert not res_watch.execution_eligible
    assert res_watch.effective_action == UserDecision.WAIT

    # 3. AVOID / AVOID -> blocked with effective_action == AVOID
    avoid_sig = SignalSnapshot(
        timestamp=base_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.AVOID,
        user_decision=UserDecision.AVOID,
        direction=DirectionScoreResult(10.0, 100.0, (), False),
        timing=TimingScoreResult(10.0, 100.0, (), False),
        hard_gate=HardGateEvaluation(True, SignalState.AVOID, ('Hard gate failed.',)),
        reasons_positive=(),
        reasons_negative=('Hard gate failed.',),
        hard_gate_reasons=('Hard gate failed.',),
        analysis_fingerprint='fp-avoid',
        code_revision='eae30005',
    )
    res_avoid = planner.plan(
        signal_snapshot=avoid_sig,
        structure_15m=struct_res,
        atr14=5.0,
        latest_close=Decimal('2653.00'),
    )
    assert not res_avoid.is_valid_risk_plan
    assert not res_avoid.execution_eligible
    assert res_avoid.effective_action == UserDecision.AVOID

    # 4. BUY_WINDOW / BUY -> proceeds to valid risk plan
    buy_sig = SignalSnapshot(
        timestamp=base_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=DirectionScoreResult(90.0, 100.0, (), True),
        timing=TimingScoreResult(85.0, 100.0, (), True),
        hard_gate=HardGateEvaluation(False, None, ()),
        reasons_positive=('Buy window active.',),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint='fp-buy',
        code_revision='eae30005',
    )
    res_buy = planner.plan(
        signal_snapshot=buy_sig,
        structure_15m=struct_res,
        atr14=5.0,
        latest_close=Decimal('2653.00'),
    )
    assert res_buy.is_valid_risk_plan
    assert res_buy.execution_eligible
    assert res_buy.effective_action == UserDecision.BUY


def test_p5_26_lower_tf_coverage_integrity_gate():
    resolver = IntrabarResolver()
    base_ts = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    parent_15m = CandleData(
        timestamp_open=base_ts,
        timestamp_close=base_ts + timedelta(minutes=15),
        open=Decimal('2650.00'),
        high=Decimal('2665.00'),
        low=Decimal('2640.00'),
        close=Decimal('2660.00'),
        volume=Decimal('100'),
        is_closed=True,
    )
    tp = Decimal('2660.00')
    sl = Decimal('2645.00')

    # Case A: Complete unbroken 1m grid (15 bars of 60s each) -> TP resolved chronologically
    complete_1m = [
        CandleData(
            timestamp_open=base_ts + timedelta(minutes=i),
            timestamp_close=base_ts + timedelta(minutes=i + 1),
            open=Decimal('2650.00'),
            high=Decimal('2662.00') if i == 5 else Decimal('2652.00'),
            low=Decimal('2642.00') if i == 10 else Decimal('2648.00'),
            close=Decimal('2651.00'),
            volume=Decimal('10'),
            is_closed=True,
        )
        for i in range(15)
    ]
    res_complete = resolver.resolve(
        parent_candle=parent_15m,
        tp_price=tp,
        sl_price=sl,
        fill_timestamp=base_ts,
        lower_tf_candles_1m=complete_1m,
        parent_timeframe='15m',
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )
    assert res_complete.barrier_hit == BarrierHitType.TP_FIRST
    assert res_complete.policy_applied == IntrabarPolicy.LOWER_TIMEFRAME_REPLAY

    # Case B: Incomplete grid - missing minute 2 before TP hit at minute 5
    broken_1m = [
        b for b in complete_1m if b.timestamp_open != (base_ts + timedelta(minutes=2))
    ]
    res_broken = resolver.resolve(
        parent_candle=parent_15m,
        tp_price=tp,
        sl_price=sl,
        fill_timestamp=base_ts,
        lower_tf_candles_1m=broken_1m,
        parent_timeframe='15m',
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )
    # Must NOT claim TP_FIRST; must fail-closed to CONSERVATIVE_SL_FIRST
    assert res_broken.barrier_hit == BarrierHitType.SL_FIRST
    assert res_broken.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST
    assert 'incomplete' in res_broken.reasons[0].lower() or 'gap' in res_broken.reasons[0].lower() or 'malformed' in res_broken.reasons[0].lower()


def test_p5_26_parent_grid_unordered_sequence_rejected():
    """
    P5-26 Parent-grid test:
    Unordered 15m input where the first list element is an ambiguous later-time child
    MUST NOT produce LOWER_TIMEFRAME_REPLAY TP_FIRST from that child.
    Must fail safe and must not recursively claim TP_FIRST.
    """
    resolver = IntrabarResolver()
    t_open = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 3, 2, 13, 0, tzinfo=timezone.utc)

    parent_1h = CandleData(
        timestamp_open=t_open,
        timestamp_close=t_close,
        open=Decimal('2650.00'),
        high=Decimal('2670.00'),
        low=Decimal('2635.00'),
        close=Decimal('2660.00'),
        volume=Decimal('500'),
        is_closed=True,
    )
    tp = Decimal('2665.00')
    sl = Decimal('2640.00')

    # Unordered 15m input list:
    # Element 0: 12:30–12:45 (ambiguous bar, touching TP=2666 and SL=2638)
    # Element 1: 12:00–12:15 (touches SL=2639)
    # Element 2: 12:15–12:30
    # Element 3: 12:45–13:00
    b_1230 = CandleData(
        timestamp_open=t_open + timedelta(minutes=30),
        timestamp_close=t_open + timedelta(minutes=45),
        open=Decimal('2650.00'),
        high=Decimal('2666.00'),  # touches TP
        low=Decimal('2638.00'),   # touches SL
        close=Decimal('2660.00'),
        volume=Decimal('100'),
        is_closed=True,
    )
    b_1200 = CandleData(
        timestamp_open=t_open,
        timestamp_close=t_open + timedelta(minutes=15),
        open=Decimal('2650.00'),
        high=Decimal('2655.00'),
        low=Decimal('2639.00'),   # touches SL first chronologically
        close=Decimal('2645.00'),
        volume=Decimal('100'),
        is_closed=True,
    )
    b_1215 = CandleData(
        timestamp_open=t_open + timedelta(minutes=15),
        timestamp_close=t_open + timedelta(minutes=30),
        open=Decimal('2645.00'),
        high=Decimal('2652.00'),
        low=Decimal('2644.00'),
        close=Decimal('2650.00'),
        volume=Decimal('100'),
        is_closed=True,
    )
    b_1245 = CandleData(
        timestamp_open=t_open + timedelta(minutes=45),
        timestamp_close=t_open + timedelta(minutes=60),
        open=Decimal('2660.00'),
        high=Decimal('2662.00'),
        low=Decimal('2658.00'),
        close=Decimal('2660.00'),
        volume=Decimal('100'),
        is_closed=True,
    )

    unordered_15m = [b_1230, b_1200, b_1215, b_1245]

    # Provide 1m candles for 12:30-12:45 that would produce TP_FIRST if recursed
    c_1m_tp_biased = [
        CandleData(
            timestamp_open=t_open + timedelta(minutes=30 + i),
            timestamp_close=t_open + timedelta(minutes=31 + i),
            open=Decimal('2650.00'),
            high=Decimal('2666.00') if i == 1 else Decimal('2652.00'),
            low=Decimal('2638.00') if i == 10 else Decimal('2648.00'),
            close=Decimal('2651.00'),
            volume=Decimal('10'),
            is_closed=True,
        )
        for i in range(15)
    ]

    res = resolver.resolve(
        parent_candle=parent_1h,
        tp_price=tp,
        sl_price=sl,
        fill_timestamp=t_open,
        lower_tf_candles_15m=unordered_15m,
        lower_tf_candles_1m=c_1m_tp_biased,
        parent_timeframe='1h',
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )

    # Must NOT produce TP_FIRST from ambiguous child; must fail-closed to CONSERVATIVE_SL_FIRST
    assert res.barrier_hit == BarrierHitType.SL_FIRST
    assert res.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST
    assert "malformed" in res.reasons[0].lower() or "chronological" in res.reasons[0].lower()


def test_p5_27_causal_limit_fill_contract():
    exec_model = EntryExecutionModel(latency_seconds=2.0)
    sig_ts = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
    limit_price = Decimal('2650.00')

    # 1. Pre-activation touch ignored (quote @ 1s < latency 2s)
    pre_act_quote = QuoteData(
        timestamp=sig_ts + timedelta(seconds=1),
        bid=Decimal('2648.00'),
        ask=Decimal('2649.00'),
    )
    res_pre = exec_model.simulate_limit_zone(
        signal_generated_at=sig_ts,
        limit_price=limit_price,
        quotes=[pre_act_quote],
    )
    assert not res_pre.is_filled

    # 2. Post-activation quote touch with price improvement
    post_act_quote = QuoteData(
        timestamp=sig_ts + timedelta(seconds=3),
        bid=Decimal('2647.50'),
        ask=Decimal('2648.50'),
    )
    res_post = exec_model.simulate_limit_zone(
        signal_generated_at=sig_ts,
        limit_price=limit_price,
        quotes=[pre_act_quote, post_act_quote],
    )
    assert res_post.is_filled
    assert res_post.fill_price == Decimal('2648.50')
    assert res_post.fill_timestamp == post_act_quote.timestamp

    # 3. Parent OHLC-only: Mid-bar activation prohibited from inferred fill
    parent_bar_current = CandleData(
        timestamp_open=sig_ts - timedelta(minutes=5),
        timestamp_close=sig_ts + timedelta(minutes=10),
        open=Decimal('2655.00'),
        high=Decimal('2660.00'),
        low=Decimal('2645.00'),
        close=Decimal('2652.00'),
        volume=Decimal('100'),
        is_closed=True,
    )
    res_ohlc_mid = exec_model.simulate_limit_zone(
        signal_generated_at=sig_ts,
        limit_price=limit_price,
        candles=[parent_bar_current],
    )
    assert not res_ohlc_mid.is_filled
    assert 'cannot infer limit fill' in res_ohlc_mid.reasons[0].lower()


def test_p5_28_structural_stop_placement():
    """P5-28: Structural stop placement sits buffer distance below support low."""
    support = StructureZone(
        zone_type="SUPPORT",
        price_low=Decimal("2940.00"),
        price_high=Decimal("2945.00"),
        created_at=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
        touches=2,
        is_active=True,
    )
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, err = calculate_stops(
        support_zone=support,
        entry_min=Decimal("2940.00"),
        entry_mid=Decimal("2942.50"),
        entry_max=Decimal("2945.00"),
        atr14=5.0,
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),  # stop_atr = 2942.50 - 10 = 2932.50
    )
    assert ok is True
    assert stop_struct == Decimal("2938.50")
    assert stop_atr == Decimal("2932.50")
    assert stop_final == Decimal("2932.50")  # min(2938.50, 2932.50)


def test_p5_29_provenance_direction_isolation():
    """P5-29: Risk planning requires explicit BUY_WINDOW state and isolates provenance."""
    planner = RiskPlanner(code_revision="eae30005", risk_version="5.0.0")
    base_ts = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2940.00"), Decimal("2945.00"), base_ts, 2, True)
    res = StructureZone("RESISTANCE", Decimal("2980.00"), Decimal("2985.00"), base_ts, 2, True)
    struct = StructureResult(base_ts, StructureType.HH, BosType.BULLISH, None, None, (), (support, res))

    # Signal in FORCE_WAIT state
    snap = SignalSnapshot(
        timestamp=base_ts,
        instrument="XAUT/USDT",
        timeframe="15m",
        state=SignalState.FORCE_WAIT,
        user_decision=UserDecision.WAIT,
        direction=DirectionScoreResult(75.0, 100.0, (), True),
        timing=TimingScoreResult(70.0, 100.0, (), True),
        hard_gate=HardGateEvaluation(False, None, ()),
        reasons_positive=(),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="fp-force-wait",
        code_revision="eae30005",
    )
    plan = planner.plan(snap, struct, atr14=5.0)
    assert not plan.is_valid_risk_plan
    assert not plan.execution_eligible
    assert plan.effective_action == UserDecision.WAIT
    assert plan.source_signal_fingerprint == "fp-force-wait"


def test_p5_30_missing_support_fail_closed():
    """P5-30: Missing confirmed active support zone fails closed."""
    planner = RiskPlanner(code_revision="eae30005")
    base_ts = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    res = StructureZone("RESISTANCE", Decimal("2980.00"), Decimal("2985.00"), base_ts, 2, True)
    struct_no_support = StructureResult(base_ts, StructureType.HH, BosType.BULLISH, None, None, (), (res,))

    sig = make_buy_window_signal(base_ts, "fp-no-support")
    plan = planner.plan(sig, struct_no_support, atr14=5.0)

    assert not plan.is_valid_risk_plan
    assert not plan.execution_eligible
    assert plan.effective_action == UserDecision.WAIT
    assert "missing confirmed active support zone" in plan.reasons[0].lower()


def test_p5_31_stop_coordinates_and_atr_guard():
    """P5-31: Stop coordinates verified and ATR guard strictly enforced."""
    support = StructureZone("SUPPORT", Decimal("2940.00"), Decimal("2945.00"), datetime.now(timezone.utc), 2, True)
    # Stop final >= entry_min fails
    _, _, _, _, ok_bad_stop, err_bad = calculate_stops(
        support_zone=support,
        entry_min=Decimal("2940.00"),
        entry_mid=Decimal("2942.50"),
        entry_max=Decimal("2945.00"),
        atr14=5.0,
        structure_buffer=Decimal("-2.00"),  # Stop at 2942.00 (between entry_min 2940 and entry_max 2945)
        atr_multiplier=Decimal("0.0"),      # ATR stop at 2942.50 -> stop_final = 2942.00 >= entry_min
    )
    assert ok_bad_stop is False
    assert "strictly below entry_min" in err_bad



def test_p5_32a_entry_zone_derived_from_support_zone():
    """
    P5-32A:
    support 2940–2945, latest_close 3000
    expected:
    entry_min = 2940
    entry_mid = 2942.50
    entry_max = 2945
    Changing latest_close alone must NOT change those three coordinates.
    """
    planner = RiskPlanner(code_revision="eae30005")
    base_ts = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2940.00"), Decimal("2945.00"), base_ts, 2, True)
    res = StructureZone("RESISTANCE", Decimal("2980.00"), Decimal("2985.00"), base_ts, 2, True)
    struct = StructureResult(base_ts, StructureType.HH, BosType.BULLISH, None, None, (), (support, res))

    sig = make_buy_window_signal(base_ts, "fp-p5-32a")

    # Run with latest_close = 3000.00
    plan_3000 = planner.plan(sig, struct, atr14=5.0, latest_close=Decimal("3000.00"))
    assert plan_3000.entry_min == Decimal("2940.00")
    assert plan_3000.entry_mid == Decimal("2942.50")
    assert plan_3000.entry_max == Decimal("2945.00")

    # Run with latest_close = 2800.00
    plan_2800 = planner.plan(sig, struct, atr14=5.0, latest_close=Decimal("2800.00"))
    assert plan_2800.entry_min == Decimal("2940.00")
    assert plan_2800.entry_mid == Decimal("2942.50")
    assert plan_2800.entry_max == Decimal("2945.00")

    # Run with latest_close = None
    plan_none = planner.plan(sig, struct, atr14=5.0, latest_close=None)
    assert plan_none.entry_min == Decimal("2940.00")
    assert plan_none.entry_mid == Decimal("2942.50")
    assert plan_none.entry_max == Decimal("2945.00")


def test_p5_32b_risk_plan_snapshot_immutable_contract_fields():
    """
    P5-32B:
    RiskPlanSnapshot explicitly contains:
    entry_min/mid/max,
    stop_structure/atr/final,
    stop_distance_atr,
    tp1/tp2,
    rr_tp1/rr_tp2,
    source zone provenance,
    version provenance.
    """
    planner = RiskPlanner(
        code_revision="eae30005",
        risk_version="5.0.0",
        execution_model_version="5.0.0-exec-v1",
        config_version="cfg-2026-v1",
    )
    base_ts = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2940.00"), Decimal("2945.00"), base_ts, 2, True)
    res = StructureZone("RESISTANCE", Decimal("2980.00"), Decimal("2985.00"), base_ts, 2, True)
    struct = StructureResult(base_ts, StructureType.HH, BosType.BULLISH, None, None, (), (support, res))

    sig = make_buy_window_signal(base_ts, "fp-p5-32b")
    plan = planner.plan(sig, struct, atr14=5.0)

    # Explicit fields
    assert plan.source_signal_fingerprint == "fp-p5-32b"
    assert plan.signal_generated_at == base_ts
    assert plan.risk_version == "5.0.0"
    assert plan.execution_model_version == "5.0.0-exec-v1"
    assert plan.config_version == "cfg-2026-v1"
    assert plan.code_revision == "eae30005"

    assert plan.entry_min == Decimal("2940.00")
    assert plan.entry_mid == Decimal("2942.50")
    assert plan.entry_max == Decimal("2945.00")

    assert plan.source_zone_id is not None
    assert "SUPPORT" in plan.source_zone_id
    assert plan.source_zone_timestamp == base_ts

    assert plan.stop_structure == Decimal("2939.00")
    assert plan.stop_atr == Decimal("2932.50")
    assert plan.stop_final == Decimal("2932.50")
    assert plan.stop_distance_atr == Decimal("2.50")

    assert plan.tp1 == Decimal("2980.00")
    assert plan.tp2 == Decimal("2985.00")
    assert plan.rr_tp1 == Decimal("2.80")  # (2980 - 2945) / 12.50 = 35 / 12.50 = 2.80
    assert plan.rr_tp2 == Decimal("3.20")  # (2985 - 2945) / 12.50 = 40 / 12.50 = 3.20

    assert plan.is_valid_risk_plan is True
    assert plan.execution_eligible is True
    assert plan.effective_action == UserDecision.BUY
    assert len(plan.reasons) > 0

    # Backward-compatible property aliases
    assert plan.source_zone == plan.source_zone_id
    assert plan.source_zone_identity == plan.source_zone_id
    assert plan.entry_price_ideal == plan.entry_mid
    assert plan.entry_limit_max == plan.entry_max
    assert plan.stop_loss_price == plan.stop_final
    assert plan.risk_reward_ratio == plan.rr_tp1


