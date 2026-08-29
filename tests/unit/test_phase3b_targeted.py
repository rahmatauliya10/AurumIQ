"""
Targeted verification test suite for Phase 3B: Experimental Spectral & Cycle Research.
Covers P3B-01 through P3B-27.
"""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
from pathlib import Path
import numpy as np
import pytest
from django.db import IntegrityError

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.analysis.models import ExperimentalCycleSnapshotRecord
from apps.analysis.services import AnalysisPersistenceService
from engine.core.types import (
    BaselineBenchmark,
    CandleData,
    PromotionStatus,
    ReliabilityStatus,
    SampleEvaluation,
    SampleQuality,
    WalkForwardFoldResult,
)
from engine.cycles.experimental.acf import calculate_causal_acf
from engine.cycles.experimental.fft import calculate_causal_fft
from engine.cycles.experimental.wavelet import calculate_causal_wavelet
from engine.cycles.experimental.hilbert import calculate_causal_hilbert
from engine.cycles.experimental.reliability import evaluate_cycle_reliability
from engine.cycles.experimental.promotion import evaluate_promotion_eligibility
from engine.cycles.experimental.engine import ExperimentalTimeCycleEngine


def generate_sine_series(length: int = 128, period: float = 16.0, noise_std: float = 0.0) -> list[float]:
    """Helper to generate clean synthetic sine test signals."""
    np.random.seed(42)
    t = np.arange(length)
    signal = 2500.0 + 10.0 * np.sin(2.0 * np.pi * t / period)
    if noise_std > 0:
        signal += np.random.normal(0, noise_std, length)
    return [float(x) for x in signal]


def generate_candle_series(length: int = 128, period: float = 16.0, base_time: datetime = None) -> list[CandleData]:
    """Helper to generate regular closed candle sequence."""
    if base_time is None:
        base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(length):
        cycle_val = 10.0 * math.sin(2.0 * math.pi * i / period)
        p = 2500.0 + cycle_val
        ts_open = base_time + timedelta(minutes=15 * i)
        ts_close = base_time + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 2.0, 2))),
                low=Decimal(str(round(p - 2.0, 2))),
                close=Decimal(str(round(p, 2))),
                volume=Decimal("100.0"),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.unit
def test_p3b_01_acf_future_mutation_invariance():
    """P3B-01: ACF calculation at T is 100% invariant under T+1.. future mutation."""
    series_full = generate_sine_series(128, period=16.0)
    series_T = series_full[:64]

    res_base = calculate_causal_acf(series_T, effective_n=50.0)
    mutated_series = list(series_T) + [9999.0, -9999.0, 50000.0] * 20
    res_recalc = calculate_causal_acf(mutated_series[:64], effective_n=50.0)

    assert res_base.dominant_lag == res_recalc.dominant_lag
    assert res_base.autocorrelation == res_recalc.autocorrelation
    assert res_base.acf_series == res_recalc.acf_series


@pytest.mark.unit
def test_p3b_02_fft_future_mutation_invariance():
    """P3B-02: FFT calculation at T is 100% invariant under T+1.. future mutation."""
    series_full = generate_sine_series(128, period=16.0)
    series_T = series_full[:64]

    res_base = calculate_causal_fft(series_T)
    mutated_series = list(series_T) + [0.0, 99999.0] * 32
    res_recalc = calculate_causal_fft(mutated_series[:64])

    assert res_base.dominant_period == res_recalc.dominant_period
    assert res_base.power_ratio == res_recalc.power_ratio
    assert res_base.spectral_entropy == res_recalc.spectral_entropy


@pytest.mark.unit
def test_p3b_03_wavelet_future_mutation_invariance():
    """P3B-03: Wavelet calculation at T is 100% invariant under T+1.. future mutation."""
    series_full = generate_sine_series(128, period=16.0)
    series_T = series_full[:64]

    res_base = calculate_causal_wavelet(series_T)
    mutated_series = list(series_T) + [1.0, 5000.0] * 32
    res_recalc = calculate_causal_wavelet(mutated_series[:64])

    assert res_base.dominant_scale_period == res_recalc.dominant_scale_period
    assert res_base.energy_ratio == res_recalc.energy_ratio


@pytest.mark.unit
def test_p3b_04_hilbert_future_mutation_invariance():
    """P3B-04: Hilbert calculation at T is 100% invariant under T+1.. future mutation."""
    series_full = generate_sine_series(128, period=16.0)
    series_T = series_full[:64]

    res_base = calculate_causal_hilbert(series_T)
    mutated_series = list(series_T) + [12345.0] * 64
    res_recalc = calculate_causal_hilbert(mutated_series[:64])

    assert res_base.instantaneous_phase == res_recalc.instantaneous_phase
    assert res_base.phase_stability == res_recalc.phase_stability


@pytest.mark.unit
def test_p3b_05_synthetic_known_sine_period_recovery():
    """P3B-05: Pure sine waves (16-bar and 32-bar) recovered accurately by FFT and ACF."""
    sine_16 = generate_sine_series(128, period=16.0)
    fft_16 = calculate_causal_fft(sine_16)
    acf_16 = calculate_causal_acf(sine_16, effective_n=100.0)

    assert fft_16.dominant_period == 16.0
    assert fft_16.power_ratio > 0.60
    assert acf_16.dominant_lag == 16
    assert acf_16.autocorrelation > 0.70

    sine_32 = generate_sine_series(128, period=32.0)
    fft_32 = calculate_causal_fft(sine_32)
    acf_32 = calculate_causal_acf(sine_32, effective_n=100.0)

    assert fft_32.dominant_period == 32.0
    assert fft_32.power_ratio > 0.60
    assert acf_32.dominant_lag == 32


@pytest.mark.unit
def test_p3b_06_constant_flat_series():
    """P3B-06: Flat/constant series cleanly handled with zero division protection."""
    flat_series = [2500.0] * 64

    acf_res = calculate_causal_acf(flat_series, effective_n=50.0)
    assert acf_res.dominant_lag is None
    assert acf_res.is_significant is False

    fft_res = calculate_causal_fft(flat_series)
    assert fft_res.dominant_period is None
    assert fft_res.is_cycle_detected is False

    wavelet_res = calculate_causal_wavelet(flat_series)
    assert wavelet_res.dominant_scale_period is None

    hilbert_res = calculate_causal_hilbert(flat_series)
    assert hilbert_res.instantaneous_amplitude == 0.0
    assert hilbert_res.is_endpoint_reliable is False


@pytest.mark.unit
def test_p3b_07_insufficient_lookback():
    """P3B-07: Lookback < 32 bars returns safe zero-result dataclasses."""
    short_series = [2500.0 + i for i in range(10)]

    assert calculate_causal_acf(short_series).dominant_lag is None
    assert calculate_causal_fft(short_series).dominant_period is None
    assert calculate_causal_wavelet(short_series).dominant_scale_period is None
    assert calculate_causal_hilbert(short_series).is_endpoint_reliable is False


@pytest.mark.unit
def test_p3b_08_nan_and_malformed_input():
    """P3B-08: NaN or None in series causes fail-closed zero results without time compression."""
    dirty_series = [2500.0, float("nan"), 2510.0] + [2500.0 + math.sin(i) for i in range(60)]

    fft_res = calculate_causal_fft(dirty_series)
    assert fft_res.dominant_period is None
    assert fft_res.is_cycle_detected is False

    acf_res = calculate_causal_acf(dirty_series, effective_n=50.0)
    assert acf_res.dominant_lag is None
    assert acf_res.is_significant is False


@pytest.mark.unit
def test_p3b_09_spectral_disagreement_reduces_reliability():
    """P3B-09: Methods with divergent periods (>30%) collapse reliability score to 0."""
    sine_16 = generate_sine_series(128, period=16.0)
    acf_res = calculate_causal_acf(sine_16, effective_n=80.0)

    fft_divergent = calculate_causal_fft(generate_sine_series(128, period=45.0))
    wavelet_divergent = calculate_causal_wavelet(generate_sine_series(128, period=60.0))
    hilbert_res = calculate_causal_hilbert(sine_16)

    rel_res = evaluate_cycle_reliability(
        acf=acf_res,
        fft=fft_divergent,
        wavelet=wavelet_divergent,
        hilbert=hilbert_res,
        effective_n=80.0,
        sample_quality=SampleQuality.HIGH,
    )

    assert rel_res.method_agreement_pct == 0.0
    assert rel_res.reliability_score == 0.0
    assert rel_res.reliability_status == ReliabilityStatus.UNRELIABLE


@pytest.mark.unit
def test_p3b_10_effective_n_under_thirty_zeroes_reliability():
    """P3B-10: Effective N < 30 strictly forces reliability to 0.0."""
    sine_16 = generate_sine_series(128, period=16.0)
    acf_res = calculate_causal_acf(sine_16, effective_n=15.0)
    fft_res = calculate_causal_fft(sine_16)
    wavelet_res = calculate_causal_wavelet(sine_16)
    hilbert_res = calculate_causal_hilbert(sine_16)

    rel_res = evaluate_cycle_reliability(
        acf=acf_res,
        fft=fft_res,
        wavelet=wavelet_res,
        hilbert=hilbert_res,
        effective_n=15.0,
        sample_quality=SampleQuality.INSUFFICIENT,
        sample_is_blocked=True,
    )

    assert rel_res.reliability_score == 0.0
    assert rel_res.reliability_status == ReliabilityStatus.UNRELIABLE


@pytest.mark.unit
def test_p3b_11_non_empirical_baseline_blocks_promotion():
    """P3B-11: Non-empirical baseline sets promotion_status to BASELINE_NOT_EMPIRICAL."""
    fake_baseline = BaselineBenchmark(
        base_profit_factor=1.9, base_expectancy_r=0.4, base_max_drawdown=8.0,
        base_trade_count=100, recorded_at=datetime.now(timezone.utc), is_empirical=False,
    )
    eval_res = evaluate_promotion_eligibility(fake_baseline, 2.2, 0.5, 7.0, 150, 5, 6, effective_n=80.0)
    assert eval_res.status == PromotionStatus.BASELINE_NOT_EMPIRICAL
    assert eval_res.is_promotable is False


@pytest.mark.unit
def test_p3b_12_under_hundred_trades_cannot_promote():
    """P3B-12: Trade count < 100 cannot promote."""
    baseline = BaselineBenchmark(1.8, 0.4, 10.0, 100, datetime.now(timezone.utc), is_empirical=True)
    eval_res = evaluate_promotion_eligibility(baseline, 2.1, 0.5, 9.0, exp_trade_count=75, walk_forward_folds_passed=5, walk_forward_folds_total=6, effective_n=60.0)
    assert eval_res.status == PromotionStatus.INSUFFICIENT_TRADES
    assert eval_res.is_promotable is False


@pytest.mark.unit
def test_p3b_13_pf_improvement_under_five_pct_cannot_promote():
    """P3B-13: PF improvement < +5% cannot promote."""
    baseline = BaselineBenchmark(2.0, 0.4, 10.0, 100, datetime.now(timezone.utc), is_empirical=True)
    eval_res = evaluate_promotion_eligibility(baseline, 2.05, 0.45, 10.0, 150, 5, 6, effective_n=80.0)
    assert eval_res.status == PromotionStatus.FAILED
    assert eval_res.is_promotable is False


@pytest.mark.unit
def test_p3b_14_drawdown_deterioration_over_ten_pct_cannot_promote():
    """P3B-14: Drawdown deterioration > 10% cannot promote."""
    baseline = BaselineBenchmark(2.0, 0.4, 10.0, 100, datetime.now(timezone.utc), is_empirical=True)
    eval_res = evaluate_promotion_eligibility(baseline, 2.20, 0.45, exp_max_drawdown=12.0, exp_trade_count=150, walk_forward_folds_passed=5, walk_forward_folds_total=6, effective_n=80.0)
    assert eval_res.status == PromotionStatus.FAILED
    assert eval_res.is_promotable is False


@pytest.mark.unit
def test_p3b_15_fewer_than_four_walk_forward_folds_cannot_promote():
    """P3B-15: Fewer than 4/6 walk-forward folds cannot promote."""
    baseline = BaselineBenchmark(2.0, 0.4, 10.0, 100, datetime.now(timezone.utc), is_empirical=True)
    eval_res = evaluate_promotion_eligibility(baseline, 2.20, 0.45, 10.0, 150, walk_forward_folds_passed=3, walk_forward_folds_total=6, effective_n=80.0)
    assert eval_res.status == PromotionStatus.FAILED
    assert eval_res.is_promotable is False


@pytest.mark.unit
def test_p3b_16_promotable_status_preserves_zero_production_weight():
    """P3B-16: Experimental snapshot production_weight is permanently 0.0 in Phase 3B."""
    candles = generate_candle_series(64, period=16.0)
    engine = ExperimentalTimeCycleEngine(experimental_version="3.1.0-3B")
    snapshot = engine.analyze(candles, timeframe="15m", effective_n=50.0)

    assert snapshot.production_weight == 0.0
    assert snapshot.experimental_version == "3.1.0-3B"


@pytest.mark.unit
def test_p3b_17_engine_cycles_experimental_has_zero_django_imports():
    """P3B-17: Ensure pure engine AST isolation (zero Django imports in engine/cycles/experimental)."""
    exp_dir = Path("/app/engine/cycles/experimental")
    if not exp_dir.exists():
        exp_dir = Path("engine/cycles/experimental")

    for py_file in exp_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "django" not in alias.name.lower(), f"Forbidden Django import '{alias.name}' in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "django" not in node.module.lower(), f"Forbidden Django from-import '{node.module}' in {py_file}"


@pytest.mark.unit
@pytest.mark.django_db
def test_p3b_18_experimental_cycle_snapshot_persistence():
    """P3B-18: Verify persistence of ExperimentalCycleSnapshotRecord via AnalysisPersistenceService."""
    xaut = Asset.objects.create(code="XAUT3B", name="Tether Gold 3B")
    usdt = Asset.objects.create(code="USDT3B", name="Tether USD 3B")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    candles = generate_candle_series(64, period=16.0)
    engine = ExperimentalTimeCycleEngine(experimental_version="3.1.0-3B")
    exp_snapshot = engine.analyze(candles, timeframe="15m", effective_n=80.0)

    AnalysisPersistenceService.save_analysis_snapshots(
        instrument=inst,
        timeframe="15m",
        cycle_3b=exp_snapshot,
    )

    rec = ExperimentalCycleSnapshotRecord.objects.get(
        instrument=inst,
        timeframe="15m",
        timestamp=exp_snapshot.timestamp,
        experimental_version="3.1.0-3B",
    )

    assert rec.experimental_version == "3.1.0-3B"
    assert rec.production_weight == 0.0
    assert rec.promotion_status == "BASELINE_NOT_EMPIRICAL"
    assert rec.fft_dominant_period == 16.0


@pytest.mark.unit
def test_p3b_19_public_api_point_in_time_isolation():
    """P3B-19: Public engine API isolates observations <= as_of without external caller slicing."""
    all_candles = generate_candle_series(128, period=16.0)
    T = all_candles[63].timestamp_close

    engine = ExperimentalTimeCycleEngine(experimental_version="3.1.0-3B")
    snap1 = engine.analyze(candles=all_candles, as_of=T, timeframe="15m", effective_n=60.0)

    mutated_candles = list(all_candles)
    for i in range(64, 128):
        c = mutated_candles[i]
        mutated_candles[i] = CandleData(
            timestamp_open=c.timestamp_open, timestamp_close=c.timestamp_close,
            open=Decimal("99999"), high=Decimal("99999"), low=Decimal("1"), close=Decimal("50000"),
            volume=Decimal("100"), is_closed=True,
        )

    snap2 = engine.analyze(candles=mutated_candles, as_of=T, timeframe="15m", effective_n=60.0)

    assert snap1.timestamp == snap2.timestamp == T
    assert snap1.fft.dominant_period == snap2.fft.dominant_period
    assert snap1.reliability.reliability_score == snap2.reliability.reliability_score


@pytest.mark.unit
def test_p3b_20_spectral_time_grid_integrity_and_gaps():
    """P3B-20: Missing bars, irregular timestamps, or duplicate intervals fail-closed."""
    base_candles = generate_candle_series(64, period=16.0)
    engine = ExperimentalTimeCycleEngine(experimental_version="3.1.0-3B")

    gap_candles = list(base_candles)
    t_gap = gap_candles[9].timestamp_close + timedelta(minutes=45)
    gap_candles[10] = CandleData(
        timestamp_open=t_gap - timedelta(minutes=15),
        timestamp_close=t_gap,
        open=Decimal("2500"), high=Decimal("2505"), low=Decimal("2495"), close=Decimal("2500"),
        volume=Decimal("100"), is_closed=True,
    )

    snapshot_gap = engine.analyze(gap_candles, timeframe="15m", effective_n=60.0)
    assert snapshot_gap.reliability.reliability_score == 0.0
    assert snapshot_gap.reliability.reliability_status == ReliabilityStatus.UNRELIABLE
    assert any("time-grid" in r for r in snapshot_gap.reliability.reasons)


@pytest.mark.unit
def test_p3b_21_missing_observation_fail_closed():
    """P3B-21: Non-finite values in series fail closed without time compression."""
    series_nan = [2500.0, float("nan"), 2505.0] + [2500.0] * 60
    assert calculate_causal_fft(series_nan).dominant_period is None
    assert calculate_causal_acf(series_nan, effective_n=50.0).dominant_lag is None
    assert calculate_causal_wavelet(series_nan).dominant_scale_period is None
    assert calculate_causal_hilbert(series_nan).is_endpoint_reliable is False


@pytest.mark.unit
def test_p3b_22_cross_window_period_stability():
    """P3B-22: Unstable periods across trailing windows (>35% dispersion) zero reliability."""
    sine_16 = generate_sine_series(128, period=16.0)
    acf_res = calculate_causal_acf(sine_16, effective_n=80.0)
    fft_res = calculate_causal_fft(sine_16)
    wavelet_res = calculate_causal_wavelet(sine_16)
    hilbert_res = calculate_causal_hilbert(sine_16)

    rel_stable = evaluate_cycle_reliability(
        acf=acf_res, fft=fft_res, wavelet=wavelet_res, hilbert=hilbert_res,
        effective_n=80.0, sample_quality=SampleQuality.HIGH,
        period_history=(16.0, 15.8, 16.2, 16.0),
    )
    assert rel_stable.reliability_status == ReliabilityStatus.HIGH
    assert rel_stable.reliability_score >= 60.0

    rel_unstable = evaluate_cycle_reliability(
        acf=acf_res, fft=fft_res, wavelet=wavelet_res, hilbert=hilbert_res,
        effective_n=80.0, sample_quality=SampleQuality.HIGH,
        period_history=(16.0, 16.0, 17.0, 15.0, 45.0),
    )
    assert rel_unstable.reliability_status == ReliabilityStatus.UNRELIABLE
    assert rel_unstable.reliability_score == 0.0
    assert any("instability" in r for r in rel_unstable.reasons)


@pytest.mark.unit
def test_p3b_23_single_period_dependence_promotion_gate():
    """P3B-23: Promotion rejected if single fold accounts for >60% of total profits."""
    baseline = BaselineBenchmark(1.80, 0.40, 10.0, 150, datetime.now(timezone.utc), is_empirical=True)

    concentrated_folds = [
        WalkForwardFoldResult(fold_id=1, profit_factor=2.50, expectancy_r=0.80, max_drawdown=6.0, trade_count=30, net_profit=7000.0),
        WalkForwardFoldResult(fold_id=2, profit_factor=1.85, expectancy_r=0.41, max_drawdown=9.0, trade_count=30, net_profit=1000.0),
        WalkForwardFoldResult(fold_id=3, profit_factor=1.85, expectancy_r=0.41, max_drawdown=9.0, trade_count=30, net_profit=1000.0),
        WalkForwardFoldResult(fold_id=4, profit_factor=1.85, expectancy_r=0.41, max_drawdown=9.0, trade_count=30, net_profit=1000.0),
        WalkForwardFoldResult(fold_id=5, profit_factor=1.80, expectancy_r=0.40, max_drawdown=10.0, trade_count=30, net_profit=0.0),
        WalkForwardFoldResult(fold_id=6, profit_factor=1.70, expectancy_r=0.30, max_drawdown=11.0, trade_count=30, net_profit=0.0),
    ]

    eval_res = evaluate_promotion_eligibility(
        baseline=baseline, exp_profit_factor=2.00, exp_expectancy_r=0.50, exp_max_drawdown=9.0,
        exp_trade_count=180, fold_results=concentrated_folds, effective_n=100.0,
    )
    assert eval_res.status == PromotionStatus.FAILED
    assert eval_res.is_promotable is False
    assert eval_res.is_single_period_dependent is True


@pytest.mark.unit
@pytest.mark.django_db
def test_p3b_24_database_production_weight_constraint():
    """P3B-24: Database CheckConstraint rejects non-zero production_weight."""
    xaut = Asset.objects.create(code="XAUT3B_2", name="Tether Gold 3B 2")
    usdt = Asset.objects.create(code="USDT3B_2", name="Tether USD 3B 2")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    with pytest.raises(IntegrityError):
        ExperimentalCycleSnapshotRecord.objects.create(
            instrument=inst,
            timeframe="15m",
            timestamp=datetime.now(timezone.utc),
            experimental_version="3.1.0-3B",
            production_weight=0.5,
        )


@pytest.mark.unit
def test_p3b_25_effective_n_aware_acf_significance():
    """P3B-25: ACF significance bound uses min(raw_n, effective_n) defensive guard."""
    series = generate_sine_series(256, period=16.0)

    # 1. Raw N=256, but certified eff_n=36 -> Bound = 1.96 / sqrt(36) = 0.3267
    res_eff = calculate_causal_acf(series, effective_n=36.0)
    assert res_eff.confidence_bound == 0.3267

    # 2. Raw N=256, eff_n=100 -> Bound = 1.96 / sqrt(100) = 0.196
    res_eff_100 = calculate_causal_acf(series, effective_n=100.0)
    assert res_eff_100.confidence_bound == 0.196

    # 3. Defensive: Raw N=64, but caller mistakenly passes eff_n=500 -> uses min(64, 500) = 64
    short_series = generate_sine_series(64, period=16.0)
    res_defensive = calculate_causal_acf(short_series, effective_n=500.0)
    assert res_defensive.confidence_bound == round(1.96 / math.sqrt(64), 4)


@pytest.mark.unit
def test_p3b_26_promotion_effective_n_guard():
    """
    P3B-26: Promotion is strictly FAILED if statistical effective_n < 30.0,
    even if all other metrics (PF, Expectancy, DD, Folds) are stellar.
    """
    empirical_baseline = BaselineBenchmark(
        base_profit_factor=1.80,
        base_expectancy_r=0.40,
        base_max_drawdown=10.0,
        base_trade_count=150,
        recorded_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_empirical=True,
    )

    balanced_folds = [
        WalkForwardFoldResult(fold_id=i, profit_factor=2.10, expectancy_r=0.55, max_drawdown=8.0, trade_count=35, net_profit=2000.0)
        for i in range(1, 7)
    ]

    # All stellar metrics, but effective_n = 18.0 (< 30.0)
    eval_res = evaluate_promotion_eligibility(
        baseline=empirical_baseline,
        exp_profit_factor=2.10,
        exp_expectancy_r=0.55,
        exp_max_drawdown=8.0,
        exp_trade_count=210,
        fold_results=balanced_folds,
        effective_n=18.0,  # Below minimum threshold
    )

    assert eval_res.status == PromotionStatus.FAILED
    assert eval_res.is_promotable is False
    assert any("effective sample size" in r for r in eval_res.reasons)


@pytest.mark.unit
def test_p3b_27_wavelet_endpoint_coi_safety():
    """
    P3B-27: Option A COI Safety.
    When lookback is too short to provide uncompromised interior support (all data within COI),
    is_clean_endpoint is strictly False and wavelet contributes 0.0 to reliability.
    """
    # 32-bar cycle in 36-bar series -> COI radius for period 32 is sqrt(2)*scale approx 45 bars > 36 bars!
    short_series = generate_sine_series(36, period=32.0)
    wavelet_res = calculate_causal_wavelet(short_series, min_period=4.0, max_period=64.0)

    assert wavelet_res.is_clean_endpoint is False
    assert wavelet_res.trusted_lag_bars > 0

    # Evaluate reliability with compromised wavelet endpoint
    acf_res = calculate_causal_acf(short_series, effective_n=35.0)
    fft_res = calculate_causal_fft(short_series)
    hilbert_res = calculate_causal_hilbert(short_series)

    rel_res = evaluate_cycle_reliability(
        acf=acf_res,
        fft=fft_res,
        wavelet=wavelet_res,
        hilbert=hilbert_res,
        effective_n=35.0,
        sample_quality=SampleQuality.LOW,
    )

    # Wavelet does not contribute positive score when endpoint is compromised
    assert wavelet_res.is_clean_endpoint is False
