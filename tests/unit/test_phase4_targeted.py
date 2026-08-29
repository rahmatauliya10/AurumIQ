"""
Targeted verification test suite for Phase 4: Direction Score, Timing Score, and Selective Gate.
Covers P4-01 through P4-22.
"""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
from pathlib import Path
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.market_data.models import MarketCandle
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from apps.signals.tasks import analyze_closed_candle
from engine.core.types import (
    AcfResult,
    BosType,
    CandleData,
    Cycle3ASnapshot,
    Cycle3BExperimentalSnapshot,
    DirectionScoreResult,
    EventImpact,
    FftResult,
    HardGateEvaluation,
    HilbertResult,
    MacroEventContext,
    RegimeResult,
    RegimeType,
    ReliabilityStatus,
    SampleQuality,
    SessionContext,
    SessionType,
    SignalState,
    StructureResult,
    StructureType,
    SwingDurationContext,
    CalendarSeasonalityContext,
    TimingScoreResult,
    UserDecision,
    WaveletResult,
    CycleReliabilityResult,
)
from engine.signals.direction import calculate_direction_score
from engine.signals.timing import calculate_timing_score
from engine.signals.gate import evaluate_hard_gates, evaluate_selective_gate
from engine.signals.explainer import (
    compute_canonical_fingerprint,
    compute_research_fingerprint,
    explain_signal,
)
from engine.signals.engine import XautSignalEngine


def generate_candle_series(length: int = 64, trend_step: float = 1.0) -> list[CandleData]:
    base_time = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(length):
        p = 2500.0 + float(i) * trend_step
        ts_open = base_time + timedelta(minutes=15 * i)
        ts_close = base_time + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 2.0, 2))),
                low=Decimal(str(round(p - 1.0, 2))),
                close=Decimal(str(round(p + 1.0, 2))),
                volume=Decimal("150.0"),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.unit
def test_p4_01_direction_component_total_exactly_100():
    """P4-01: Sum of maximum direction component weights is exactly 100.0."""
    res = calculate_direction_score(
        regime=None,
        features_15m=None,
        structure_15m=None,
    )
    max_sum = sum(c.max_score for c in res.components)
    assert max_sum == 100.0
    assert res.max_score == 100.0


@pytest.mark.unit
def test_p4_02_timing_component_total_exactly_100():
    """P4-02: Sum of maximum timing component weights is exactly 100.0."""
    res = calculate_timing_score(
        latest_closed_candle=None,
        features_15m=None,
        structure_15m=None,
    )
    max_sum = sum(c.max_score for c in res.components)
    assert max_sum == 100.0
    assert res.max_score == 100.0


@pytest.mark.unit
def test_p4_03_missing_evidence_never_receives_positive_points():
    """P4-03: When input feeds are None/missing, zero positive points are awarded."""
    dir_res = calculate_direction_score(
        regime=None,
        features_15m=None,
        structure_15m=None,
        xau_reference_is_bullish=None,
        xaut_basis_zscore=None,
    )
    assert dir_res.total_score == 0.0
    assert all(c.score == 0.0 for c in dir_res.components)

    tim_res = calculate_timing_score(
        latest_closed_candle=None,
        features_15m=None,
        structure_15m=None,
        cycle_3a=None,
        macro_context=None,
    )
    assert tim_res.total_score == 0.0
    assert all(c.score == 0.0 for c in tim_res.components)


@pytest.mark.unit
def test_p4_04_bearish_high_vol_regime_cannot_buy():
    """P4-04: Bearish or High Volatility regime forces AVOID state and decision."""
    dir_res = DirectionScoreResult(90.0, 100.0, (), True)
    tim_res = TimingScoreResult(90.0, 100.0, (), True)
    hard_gate = HardGateEvaluation(False, None, ())
    struct = StructureResult(datetime.now(timezone.utc), StructureType.HH, BosType.BULLISH, None, None, (), ())

    bear_regime = RegimeResult(RegimeType.BEAR_TREND, 0.95, datetime.now(timezone.utc))
    state, decision = evaluate_selective_gate(
        direction=dir_res, timing=tim_res, regime=bear_regime,
        structure=struct, hard_gate=hard_gate, is_reversal_confirmed=True,
    )
    assert state == SignalState.AVOID
    assert decision == UserDecision.AVOID

    high_vol_regime = RegimeResult(RegimeType.HIGH_VOLATILITY, 0.90, datetime.now(timezone.utc))
    state_hv, decision_hv = evaluate_selective_gate(
        direction=dir_res, timing=tim_res, regime=high_vol_regime,
        structure=struct, hard_gate=hard_gate, is_reversal_confirmed=True,
    )
    assert state_hv == SignalState.AVOID
    assert decision_hv == UserDecision.AVOID


@pytest.mark.unit
def test_p4_05_watch_threshold_exact_boundaries():
    """P4-05: WATCH requires Direction >= 70.0."""
    regime = RegimeResult(RegimeType.BULL_TREND, 0.90, datetime.now(timezone.utc))
    struct = StructureResult(datetime.now(timezone.utc), StructureType.HL, BosType.NONE, None, None, (), ())
    hard_gate = HardGateEvaluation(False, None, ())

    # Case A: Direction = 69.9 -> NO_TRADE
    dir_sub = DirectionScoreResult(69.9, 100.0, (), False)
    tim_sub = TimingScoreResult(50.0, 100.0, (), False)
    s1, d1 = evaluate_selective_gate(dir_sub, tim_sub, regime, struct, hard_gate)
    assert s1 == SignalState.NO_TRADE
    assert d1 == UserDecision.WAIT

    # Case B: Direction = 70.0 -> WATCH
    dir_ok = DirectionScoreResult(70.0, 100.0, (), True)
    s2, d2 = evaluate_selective_gate(dir_ok, tim_sub, regime, struct, hard_gate)
    assert s2 == SignalState.WATCH
    assert d2 == UserDecision.WAIT


@pytest.mark.unit
def test_p4_06_ready_threshold_exact_boundaries():
    """P4-06: READY requires Direction >= 75.0, Timing >= 70.0, and near support."""
    regime = RegimeResult(RegimeType.BULL_TREND, 0.90, datetime.now(timezone.utc))
    struct = StructureResult(datetime.now(timezone.utc), StructureType.HL, BosType.NONE, None, None, (), ())
    hard_gate = HardGateEvaluation(False, None, ())

    dir_75 = DirectionScoreResult(75.0, 100.0, (), True)
    tim_69 = TimingScoreResult(69.9, 100.0, (), False)
    tim_70 = TimingScoreResult(70.0, 100.0, (), True)

    # Timing < 70 -> Falls back to WATCH
    s1, d1 = evaluate_selective_gate(dir_75, tim_69, regime, struct, hard_gate, is_near_support=True)
    assert s1 == SignalState.WATCH

    # Timing >= 70 and near support -> READY
    s2, d2 = evaluate_selective_gate(dir_75, tim_70, regime, struct, hard_gate, is_near_support=True)
    assert s2 == SignalState.READY
    assert d2 == UserDecision.WAIT


@pytest.mark.unit
def test_p4_07_buy_window_threshold_exact_boundaries():
    """P4-07: BUY_WINDOW requires Direction >= 80.0, Timing >= 80.0, and closed reversal."""
    regime = RegimeResult(RegimeType.BULL_TREND, 0.90, datetime.now(timezone.utc))
    struct = StructureResult(datetime.now(timezone.utc), StructureType.HL, BosType.NONE, None, None, (), ())
    hard_gate = HardGateEvaluation(False, None, ())

    dir_80 = DirectionScoreResult(80.0, 100.0, (), True)
    tim_80 = TimingScoreResult(80.0, 100.0, (), True)

    # Without reversal confirmation -> falls back to READY if near support
    s1, d1 = evaluate_selective_gate(dir_80, tim_80, regime, struct, hard_gate, is_reversal_confirmed=False, is_near_support=True)
    assert s1 == SignalState.READY
    assert d1 == UserDecision.WAIT

    # With reversal confirmation -> BUY_WINDOW
    s2, d2 = evaluate_selective_gate(dir_80, tim_80, regime, struct, hard_gate, is_reversal_confirmed=True, is_near_support=True)
    assert s2 == SignalState.BUY_WINDOW
    assert d2 == UserDecision.BUY


@pytest.mark.unit
def test_p4_08_macro_blackout_overrides_score():
    """P4-08: Macro blackout forces FORCE_WAIT even with perfect 100/100 scores."""
    dir_100 = DirectionScoreResult(100.0, 100.0, (), True)
    tim_100 = TimingScoreResult(100.0, 100.0, (), True)
    regime = RegimeResult(RegimeType.BULL_TREND, 1.0, datetime.now(timezone.utc))
    struct = StructureResult(datetime.now(timezone.utc), StructureType.HH, BosType.BULLISH, None, None, (), ())

    hard_gate = evaluate_hard_gates(is_macro_blackout=True)
    state, decision = evaluate_selective_gate(dir_100, tim_100, regime, struct, hard_gate, is_reversal_confirmed=True)

    assert state == SignalState.FORCE_WAIT
    assert decision == UserDecision.WAIT


@pytest.mark.unit
def test_p4_09_provider_transition_overrides_score():
    """P4-09: Provider transition forces FORCE_WAIT."""
    hard_gate = evaluate_hard_gates(is_provider_transition=True)
    assert hard_gate.is_blocked is True
    assert hard_gate.override_state == SignalState.FORCE_WAIT
    assert any("TRANSITION" in r for r in hard_gate.block_reasons)


@pytest.mark.unit
def test_p4_10_missing_canonical_xau_blocks_buy_window():
    """P4-10: Missing canonical XAU reference feed blocks BUY_WINDOW."""
    hard_gate = evaluate_hard_gates(is_missing_xau=True)
    assert hard_gate.is_blocked is True
    assert any("XAU/USD" in r for r in hard_gate.block_reasons)


@pytest.mark.unit
def test_p4_11_missing_usdt_normalization_blocks_buy_window():
    """P4-11: Missing USDT/USD normalization rate blocks BUY_WINDOW."""
    hard_gate = evaluate_hard_gates(is_missing_normalization=True)
    assert hard_gate.is_blocked is True
    assert any("USDT/USD" in r for r in hard_gate.block_reasons)


@pytest.mark.unit
def test_p4_12_closed_candle_boundary():
    """P4-12: Unclosed candle cannot trigger false analysis signals."""
    hard_gate = evaluate_hard_gates(is_unclosed_candle=True)
    assert hard_gate.is_blocked is True
    assert any("unclosed" in r for r in hard_gate.block_reasons)


@pytest.mark.unit
def test_p4_13_phase3b_mutation_leaves_production_scores_identical():
    """
    P4-13: Mutating Phase 3B experimental output (weight=0.0) leaves Direction,
    Timing, State, Decision, and production analysis_fingerprint 100% identical.
    """
    candles = generate_candle_series(64)
    T = candles[-1].timestamp_close
    engine = XautSignalEngine()

    dummy_wavelet = WaveletResult(16.0, 0.5, 0.1, True, (), 0)
    dummy_hilbert = HilbertResult(0.5, 10.0, 0.1, 0.9, True)

    # 3B Snapshot Variant 1
    cycle_3b_v1 = Cycle3BExperimentalSnapshot(
        timestamp=T, timeframe="15m",
        acf=AcfResult(16, 0.8, True, 0.2, (), 50.0, SampleQuality.HIGH),
        fft=FftResult(16.0, 0.0625, 0.8, 0.2, (), True),
        wavelet=dummy_wavelet,
        hilbert=dummy_hilbert,
        reliability=CycleReliabilityResult(16.0, 0.8, 0.8, 0.5, 0.5, 0.9, 100.0, 50.0, SampleQuality.HIGH, 85.0, ReliabilityStatus.HIGH, ()),
    )

    # 3B Snapshot Variant 2 (Radically different / unpromoted)
    cycle_3b_v2 = Cycle3BExperimentalSnapshot(
        timestamp=T, timeframe="15m",
        acf=AcfResult(None, 0.0, False, 0.0, (), 10.0, SampleQuality.INSUFFICIENT),
        fft=FftResult(None, None, 0.0, 1.0, (), False),
        wavelet=dummy_wavelet,
        hilbert=dummy_hilbert,
        reliability=CycleReliabilityResult(None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, SampleQuality.INSUFFICIENT, 0.0, ReliabilityStatus.UNRELIABLE, ()),
    )

    snap1 = engine.analyze(
        candles_15m=candles, as_of=T,
        xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"),
        cycle_3b=cycle_3b_v1,
    )

    snap2 = engine.analyze(
        candles_15m=candles, as_of=T,
        xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"),
        cycle_3b=cycle_3b_v2,
    )

    # Invariants: Zero production impact
    assert snap1.direction.total_score == snap2.direction.total_score
    assert snap1.timing.total_score == snap2.timing.total_score
    assert snap1.state == snap2.state
    assert snap1.user_decision == snap2.user_decision
    assert snap1.analysis_fingerprint == snap2.analysis_fingerprint

    # Research fingerprints are distinct for audit
    assert snap1.research_fingerprint != snap2.research_fingerprint


@pytest.mark.unit
def test_p4_14_canonical_fingerprint_determinism():
    """P4-14: Identical production inputs yield 100% deterministic SHA-256 fingerprint."""
    candles = generate_candle_series(64)
    T = candles[-1].timestamp_close
    engine = XautSignalEngine()

    snap1 = engine.analyze(candles_15m=candles, as_of=T, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))
    snap2 = engine.analyze(candles_15m=candles, as_of=T, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))

    assert snap1.analysis_fingerprint == snap2.analysis_fingerprint
    assert len(snap1.analysis_fingerprint) == 64


@pytest.mark.unit
def test_p4_15_corrected_data_creates_new_fingerprint():
    """P4-15: Corrected price input for same timestamp creates a distinct fingerprint."""
    candles1 = generate_candle_series(64, trend_step=1.0)
    candles2 = generate_candle_series(64, trend_step=1.5)  # Corrected higher trend
    T = candles1[-1].timestamp_close
    engine = XautSignalEngine()

    snap1 = engine.analyze(candles_15m=candles1, as_of=T, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))
    snap2 = engine.analyze(candles_15m=candles2, as_of=T, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))

    assert snap1.analysis_fingerprint != snap2.analysis_fingerprint


@pytest.mark.unit
@pytest.mark.django_db
def test_p4_16_config_version_change_preserves_historical_record():
    """P4-16: ConfigVersion upgrade creates a new record while keeping old signal immutable."""
    xaut = Asset.objects.create(code="XAUT_P16", name="Tether Gold P16")
    usdt = Asset.objects.create(code="USDT_P16", name="Tether USD P16")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    candles = generate_candle_series(64)
    T = candles[-1].timestamp_close

    engine_v1 = XautSignalEngine(config_version="cfg-2026-v1")
    engine_v2 = XautSignalEngine(config_version="cfg-2026-v2")

    snap1 = engine_v1.analyze(candles_15m=candles, as_of=T, instrument=inst.symbol, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))
    rec1, _ = SignalPersistenceService.save_signal_snapshot(inst, snap1)

    snap2 = engine_v2.analyze(candles_15m=candles, as_of=T, instrument=inst.symbol, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))
    rec2, _ = SignalPersistenceService.save_signal_snapshot(inst, snap2)

    assert rec1.id != rec2.id
    assert rec1.config_version == "cfg-2026-v1"
    assert rec2.config_version == "cfg-2026-v2"
    assert SignalRecord.objects.filter(instrument=inst).count() == 2


@pytest.mark.unit
def test_p4_17_engine_signals_has_zero_django_imports():
    """P4-17: Pure engine AST isolation (zero Django imports in engine/signals)."""
    signals_dir = Path("/app/engine/signals")
    if not signals_dir.exists():
        signals_dir = Path("engine/signals")

    for py_file in signals_dir.glob("*.py"):
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
def test_p4_18_exact_user_decision_mapping():
    """P4-18: Verify 1:1 mapping from Internal State to User Decision."""
    regime = RegimeResult(RegimeType.BULL_TREND, 1.0, datetime.now(timezone.utc))
    struct = StructureResult(datetime.now(timezone.utc), StructureType.HH, BosType.BULLISH, None, None, (), ())

    # 1. NO_TRADE -> WAIT
    s, d = evaluate_selective_gate(DirectionScoreResult(20.0, 100.0, (), False), TimingScoreResult(20.0, 100.0, (), False), regime, struct, HardGateEvaluation(False, None, ()))
    assert s == SignalState.NO_TRADE and d == UserDecision.WAIT

    # 2. AVOID -> AVOID
    s, d = evaluate_selective_gate(DirectionScoreResult(90.0, 100.0, (), True), TimingScoreResult(90.0, 100.0, (), True), RegimeResult(RegimeType.BEAR_TREND, 1.0, datetime.now(timezone.utc)), struct, HardGateEvaluation(False, None, ()))
    assert s == SignalState.AVOID and d == UserDecision.AVOID

    # 3. WATCH -> WAIT
    s, d = evaluate_selective_gate(DirectionScoreResult(72.0, 100.0, (), True), TimingScoreResult(50.0, 100.0, (), False), regime, struct, HardGateEvaluation(False, None, ()))
    assert s == SignalState.WATCH and d == UserDecision.WAIT

    # 4. READY -> WAIT
    s, d = evaluate_selective_gate(DirectionScoreResult(76.0, 100.0, (), True), TimingScoreResult(72.0, 100.0, (), True), regime, struct, HardGateEvaluation(False, None, ()), is_near_support=True)
    assert s == SignalState.READY and d == UserDecision.WAIT

    # 5. BUY_WINDOW -> BUY
    s, d = evaluate_selective_gate(DirectionScoreResult(82.0, 100.0, (), True), TimingScoreResult(82.0, 100.0, (), True), regime, struct, HardGateEvaluation(False, None, ()), is_reversal_confirmed=True)
    assert s == SignalState.BUY_WINDOW and d == UserDecision.BUY

    # 6. FORCE_WAIT -> WAIT
    s, d = evaluate_selective_gate(DirectionScoreResult(95.0, 100.0, (), True), TimingScoreResult(95.0, 100.0, (), True), regime, struct, HardGateEvaluation(True, SignalState.FORCE_WAIT, ("Stale",)))
    assert s == SignalState.FORCE_WAIT and d == UserDecision.WAIT


@pytest.mark.unit
def test_p4_19_explanation_component_totals_reconcile():
    """P4-19: Explanations reconcile with Direction and Timing component totals."""
    candles = generate_candle_series(64)
    T = candles[-1].timestamp_close
    engine = XautSignalEngine()

    snap = engine.analyze(candles_15m=candles, as_of=T, xau_reference_price=Decimal("2550.0"), xau_reference_is_bullish=True, usdt_rate=Decimal("1.0"))
    pos, neg, hard_reasons = explain_signal(snap.direction, snap.timing, snap.hard_gate, snap.state, snap.user_decision)

    assert len(pos) + len(neg) == len(snap.direction.components) + len(snap.timing.components)


@pytest.mark.unit
def test_p4_20_no_risk_plan_execution_logic_exists_in_phase4():
    """
    P4-20: Ensure Phase 4 contains zero RiskPlan, Stop Loss, Take Profit,
    Position Size, or execution order placement logic (Phase 5 boundary check).
    """
    signals_dir = Path("/app/engine/signals")
    if not signals_dir.exists():
        signals_dir = Path("engine/signals")

    forbidden_terms = ["riskplan", "stop_loss", "take_profit", "risk_reward", "positionsize", "order_placement", "fill_simulation"]

    for py_file in signals_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read().lower()
            for term in forbidden_terms:
                assert f"def {term}" not in content, f"Forbidden Phase 5 function definition '{term}' in {py_file}"
                assert f"class {term}" not in content, f"Forbidden Phase 5 class definition '{term}' in {py_file}"


@pytest.mark.unit
@pytest.mark.django_db
def test_p4_21_version_pinned_task_idempotency():
    """
    P4-21: Celery task idempotency is strictly pinned to invocation version payload.
    Retrying an old task payload preserves fingerprint X even if runtime environment changes.
    """
    xaut = Asset.objects.create(code="XAUT_P21", name="Tether Gold P21")
    usdt = Asset.objects.create(code="USDT_P21", name="Tether USD P21")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    base_time = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

    for i in range(64):
        p = Decimal(str(2500 + i))
        t_open = base_time + timedelta(minutes=15 * i)
        t_close = base_time + timedelta(minutes=15 * (i + 1))
        MarketCandle.objects.create(
            instrument=inst,
            source="BINANCE_P21",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=p - 1,
            high=p + 2,
            low=p - 1,
            close=p,
            volume=Decimal("100"),
            is_closed=True,
        )

    eval_ts_iso = (base_time + timedelta(minutes=15 * 64)).isoformat()

    # 1. Enqueue & execute task with Config A
    res1 = analyze_closed_candle(
        instrument_id=inst.id,
        timeframe="15m",
        candle_timestamp_iso=eval_ts_iso,
        engine_version="4.0.0",
        config_version="cfg-2026-v1",
        code_revision="2795de04",
    )

    fp_x = res1["fingerprint"]
    assert res1["created"] is True
    assert SignalRecord.objects.filter(instrument=inst).count() == 1

    # 2. Worker retries original task payload (with Config A)
    res_retry = analyze_closed_candle(
        instrument_id=inst.id,
        timeframe="15m",
        candle_timestamp_iso=eval_ts_iso,
        engine_version="4.0.0",
        config_version="cfg-2026-v1",
        code_revision="2795de04",
    )

    assert res_retry["fingerprint"] == fp_x
    assert res_retry["created"] is False
    assert SignalRecord.objects.filter(instrument=inst).count() == 1

    # 3. Execute new task with Config B
    res_new_config = analyze_closed_candle(
        instrument_id=inst.id,
        timeframe="15m",
        candle_timestamp_iso=eval_ts_iso,
        engine_version="4.0.0",
        config_version="cfg-2026-v2",
        code_revision="2795de04",
    )

    fp_y = res_new_config["fingerprint"]
    assert fp_y != fp_x
    assert res_new_config["created"] is True
    assert SignalRecord.objects.filter(instrument=inst).count() == 2


@pytest.mark.unit
def test_p4_22_gate_precedence_collision_matrix():
    """
    P4-22: Deterministic collision precedence resolution across combined market conditions.
    """
    regime_bull = RegimeResult(RegimeType.BULL_TREND, 1.0, datetime.now(timezone.utc))
    regime_bear = RegimeResult(RegimeType.BEAR_TREND, 1.0, datetime.now(timezone.utc))
    struct_ok = StructureResult(datetime.now(timezone.utc), StructureType.HH, BosType.BULLISH, None, None, (), ())

    dir_100 = DirectionScoreResult(100.0, 100.0, (), True)
    tim_100 = TimingScoreResult(100.0, 100.0, (), True)

    # 1. stale + bullish 100/100 -> FORCE_WAIT (WAIT)
    gate_stale = evaluate_hard_gates(is_feed_stale=True)
    s1, d1 = evaluate_selective_gate(dir_100, tim_100, regime_bull, struct_ok, gate_stale, is_reversal_confirmed=True)
    assert s1 == SignalState.FORCE_WAIT and d1 == UserDecision.WAIT

    # 2. stale + BEAR -> FORCE_WAIT (WAIT) [Safety override beats hostile condition]
    s2, d2 = evaluate_selective_gate(dir_100, tim_100, regime_bear, struct_ok, gate_stale, is_reversal_confirmed=True)
    assert s2 == SignalState.FORCE_WAIT and d2 == UserDecision.WAIT

    # 3. missing XAU + insufficient history (< 32 bars) -> FORCE_WAIT (WAIT)
    gate_missing_xau = evaluate_hard_gates(is_missing_xau=True)
    s3, d3 = evaluate_selective_gate(dir_100, tim_100, None, None, gate_missing_xau, is_data_sufficient=False)
    assert s3 == SignalState.FORCE_WAIT and d3 == UserDecision.WAIT

    # 4. healthy feeds + insufficient history -> NO_TRADE (WAIT)
    gate_healthy = evaluate_hard_gates()
    s4, d4 = evaluate_selective_gate(dir_100, tim_100, None, None, gate_healthy, is_data_sufficient=False)
    assert s4 == SignalState.NO_TRADE and d4 == UserDecision.WAIT

    # 5. healthy feeds + sufficient data + BEAR -> AVOID (AVOID)
    s5, d5 = evaluate_selective_gate(dir_100, tim_100, regime_bear, struct_ok, gate_healthy, is_data_sufficient=True)
    assert s5 == SignalState.AVOID and d5 == UserDecision.AVOID

    # 6. healthy feeds + sufficient data + bullish + thresholds met -> BUY_WINDOW (BUY)
    s6, d6 = evaluate_selective_gate(dir_100, tim_100, regime_bull, struct_ok, gate_healthy, is_reversal_confirmed=True, is_data_sufficient=True)
    assert s6 == SignalState.BUY_WINDOW and d6 == UserDecision.BUY
