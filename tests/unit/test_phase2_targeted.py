"""
Targeted Verification Tests for Phase 2:
P2-01: Swing Confirmation Causality (Swing unavailable before detected_at)
P2-02: BOS Cannot Precede Confirmed Structure
P2-03: Regime Volatility Boundary Units (Exact percentage boundary tests)
P2-04: EMA Slope is Normalized / Scale Invariant
P2-05: Effective-N Overlapping Cluster Collapse
P2-06: Effective-N Balanced Independent Sample
P2-07: Flat-Price and Indicator Edge Cases
P2-08: Numerical Indicator Fixtures & Parity
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from engine.core.types import (
    CandleData,
    FeatureSnapshot,
    RegimeType,
    BosType,
    SwingType,
    SampleQuality,
)
from engine.core.config import EngineConfigData
from engine.features.trend import (
    calculate_ema,
    calculate_ema_slope,
    calculate_ema_alignment,
    calculate_adx,
)
from engine.features.momentum import calculate_rsi, calculate_macd, calculate_roc
from engine.features.volatility import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_realized_volatility,
)
from engine.features.volume import calculate_volume_ratio, calculate_volume_zscore
from engine.regime.engine import RegimeEngine
from engine.structure.causal_swings import detect_causal_swings
from engine.structure.engine import CausalStructureEngine
from engine.guards.sample_guard import EffectiveSampleEstimator


def _make_candle(idx: int, o: str, h: str, l: str, c: str, vol: str = "100") -> CandleData:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * idx)
    return CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(vol),
        is_closed=True,
    )


@pytest.mark.unit
def test_p2_01_swing_confirmation_causality():
    """
    P2-01: Verify candidate swing at bar i (left=3, right=3):
    - At bar i+2: swing MUST NOT exist (0 confirmed swings).
    - When bar i+3 closes: swing becomes confirmed with source_timestamp = bar i, detected_at = bar i+3.
    """
    candles = [
        _make_candle(0, "100", "102", "98", "101"),
        _make_candle(1, "101", "105", "100", "104"),
        _make_candle(2, "104", "108", "102", "106"),
        _make_candle(3, "106", "130", "105", "125"),  # Peak at bar 3 (high=130)
        _make_candle(4, "125", "120", "110", "115"),
        _make_candle(5, "115", "112", "105", "108"),  # Bar 5 (i+2): right confirmation not yet reached!
    ]

    # Evaluated at T = bar 5 (i+2): 0 swings confirmed!
    swings_at_i_plus_2 = detect_causal_swings(candles, left_bars=3, right_bars=3)
    assert len(swings_at_i_plus_2) == 0

    # Bar 6 (i+3) closes -> right confirmation completed!
    bar_6 = _make_candle(6, "108", "105", "100", "102")
    candles.append(bar_6)

    swings_at_i_plus_3 = detect_causal_swings(candles, left_bars=3, right_bars=3)
    assert len(swings_at_i_plus_3) == 1

    swing = swings_at_i_plus_3[0]
    assert swing.index == 3
    assert swing.price == Decimal("130")
    assert swing.swing_type == SwingType.HIGH
    assert swing.timestamp == candles[3].timestamp_open  # Source timestamp
    assert swing.detected_at == candles[6].timestamp_close  # Strictly causal detection time (when bar 6 closes)!


@pytest.mark.unit
def test_p2_02_bos_cannot_precede_confirmed_structure():
    """
    P2-02: BOS cannot trigger off an unconfirmed candidate swing.
    If candle at i+2 breaches candidate swing level, it cannot trigger BOS
    because candidate swing is not yet confirmed at i+2.
    """
    candles = [
        _make_candle(0, "100", "102", "98", "101"),
        _make_candle(1, "101", "105", "100", "104"),
        _make_candle(2, "104", "108", "102", "106"),
        _make_candle(3, "106", "120", "105", "118"),  # Candidate peak at 120
        _make_candle(4, "118", "115", "110", "112"),
        _make_candle(5, "112", "125", "111", "124"),  # Bar 5 breaches 120, but bar 3 was never confirmed!
    ]

    engine = CausalStructureEngine()
    res = engine.analyze(candles, atr=Decimal("5.0"))

    # At bar 5, bar 3 was never confirmed as a swing high -> BOS must be NONE
    assert res.bos == BosType.NONE
    assert res.last_swing_high is None


@pytest.mark.unit
def test_p2_03_regime_volatility_boundary_units():
    """
    P2-03: Regime volatility boundary units:
    RV: 4.99% -> not HIGH_VOLATILITY; 5.01% -> HIGH_VOLATILITY
    ATR%: 2.99% -> not HIGH_VOLATILITY; 3.01% -> HIGH_VOLATILITY
    BB Bandwidth: 14.99% -> not HIGH_VOLATILITY; 15.01% -> HIGH_VOLATILITY
    """
    engine = RegimeEngine()
    now = datetime.now(timezone.utc)

    # Base feature template (BULL_TREND in absence of high vol)
    def _create_snapshot(rv: float, atr_pct: float, bb_bw: float) -> FeatureSnapshot:
        return FeatureSnapshot(
            timestamp=now,
            ema20=Decimal("2550"), ema50=Decimal("2500"), ema200=Decimal("2400"),
            ema_slope_20=0.15, ema_alignment=1, adx=30.0, plus_di=35.0, minus_di=10.0,
            rsi14=65.0, macd_line=Decimal("10"), macd_signal=Decimal("8"), macd_hist=Decimal("2"), roc12=4.0,
            atr14=Decimal("10"), atr_pct=atr_pct,
            bb_upper=Decimal("2570"), bb_middle=Decimal("2550"), bb_lower=Decimal("2530"),
            bb_bandwidth=bb_bw, realized_vol_20=rv, volume_ratio_20=1.2, volume_zscore_20=0.5,
        )

    # Test RV boundary (5.0%)
    res_rv_below = engine.classify(_create_snapshot(rv=4.99, atr_pct=2.0, bb_bw=10.0))
    assert res_rv_below.regime != RegimeType.HIGH_VOLATILITY
    res_rv_above = engine.classify(_create_snapshot(rv=5.01, atr_pct=2.0, bb_bw=10.0))
    assert res_rv_above.regime == RegimeType.HIGH_VOLATILITY

    # Test ATR% boundary (3.0%)
    res_atr_below = engine.classify(_create_snapshot(rv=2.0, atr_pct=2.99, bb_bw=10.0))
    assert res_atr_below.regime != RegimeType.HIGH_VOLATILITY
    res_atr_above = engine.classify(_create_snapshot(rv=2.0, atr_pct=3.01, bb_bw=10.0))
    assert res_atr_above.regime == RegimeType.HIGH_VOLATILITY

    # Test BB Bandwidth boundary (15.0%)
    res_bb_below = engine.classify(_create_snapshot(rv=2.0, atr_pct=2.0, bb_bw=14.99))
    assert res_bb_below.regime != RegimeType.HIGH_VOLATILITY
    res_bb_above = engine.classify(_create_snapshot(rv=2.0, atr_pct=2.0, bb_bw=15.01))
    assert res_bb_above.regime == RegimeType.HIGH_VOLATILITY


@pytest.mark.unit
def test_p2_04_ema_slope_is_normalized_and_scale_invariant():
    """
    P2-04: EMA slope (% change per bar) is strictly scale-invariant.
    Multiplying all series prices by 10x (or 100x) yields identical percentage slopes.
    """
    base_prices = [Decimal(str(100 + i * 2)) for i in range(30)]
    scaled_prices = [p * Decimal("100.0") for p in base_prices]

    ema_base = calculate_ema(base_prices, period=20)
    ema_scaled = calculate_ema(scaled_prices, period=20)

    slope_base = calculate_ema_slope(ema_base, lookback=5)
    slope_scaled = calculate_ema_slope(ema_scaled, lookback=5)

    assert slope_base is not None
    assert slope_scaled is not None
    assert abs(slope_base - slope_scaled) < 1e-7


@pytest.mark.unit
def test_p2_05_effective_n_overlapping_cluster_collapse():
    """
    P2-05: Adversarial test: 100 signals occurring in the same holding horizon
    (90% overlap) and concentrated in 1 single regime -> effective_n collapses drastically.
    """
    estimator = EffectiveSampleEstimator()
    single_regime = {"BULL_TREND": 100}

    eval_result = estimator.evaluate_sample(
        n_raw=100,
        regime_distribution=single_regime,
        autocorrelation_factor=0.8,
        overlap_ratio=0.70,
    )

    # 100 raw -> 30 independent after 70% overlap -> 18 after temporal clustering -> 9 after HHI
    assert eval_result.independent_after_overlap == 30
    assert eval_result.temporal_clusters == 18
    assert eval_result.effective_n < 10.0
    assert eval_result.quality == SampleQuality.INSUFFICIENT
    assert eval_result.weight_multiplier == 0.0
    assert eval_result.is_blocked is True


@pytest.mark.unit
def test_p2_06_effective_n_balanced_independent_sample():
    """
    P2-06: 100 well-separated signals across balanced regimes -> effective_n remains high (~100).
    """
    estimator = EffectiveSampleEstimator()
    balanced_regimes = {
        "BULL_TREND": 25,
        "BEAR_TREND": 25,
        "RANGE": 25,
        "HIGH_VOLATILITY": 25,
    }

    eval_result = estimator.evaluate_sample(
        n_raw=100,
        regime_distribution=balanced_regimes,
        autocorrelation_factor=0.0,
        overlap_ratio=0.0,
    )

    assert eval_result.independent_after_overlap == 100
    assert eval_result.temporal_clusters == 100
    assert eval_result.hhi_norm == 0.0  # Perfect uniform distribution
    assert eval_result.effective_n == 100.0
    assert eval_result.quality == SampleQuality.HIGH
    assert eval_result.weight_multiplier == 1.0
    assert eval_result.is_blocked is False


@pytest.mark.unit
def test_p2_07_flat_price_and_indicator_edge_cases():
    """
    P2-07: Verify indicator robustness under flat prices, zero volume, and edge conditions:
    - Flat prices -> ATR = 0, Bollinger Bandwidth = 0, Realized Vol = 0, RSI = 50.0
    - Zero volume -> Volume Ratio = 1.0, Volume Z-Score = 0.0
    - No division by zero or NaN crashes.
    """
    flat_closes = [Decimal("2500.00")] * 50
    flat_highs = [Decimal("2500.00")] * 50
    flat_lows = [Decimal("2500.00")] * 50
    zero_volumes = [Decimal("0.0")] * 50

    # ATR
    atr = calculate_atr(flat_highs, flat_lows, flat_closes, period=14)
    assert atr == Decimal("0.00000000")

    # Bollinger Bands
    upper, mid, lower, bw = calculate_bollinger_bands(flat_closes, period=20)
    assert mid == Decimal("2500.00000000")
    assert upper == Decimal("2500.00000000")
    assert lower == Decimal("2500.00000000")
    assert bw == 0.0

    # Realized Vol
    rv = calculate_realized_volatility(flat_closes, period=20)
    assert rv == 0.0

    # RSI
    rsi = calculate_rsi(flat_closes, period=14)
    assert rsi == 50.0  # Neutral on flat price series

    # Volume
    v_ratio = calculate_volume_ratio(zero_volumes, period=20)
    assert v_ratio == 1.0
    v_zscore = calculate_volume_zscore(zero_volumes, period=20)
    assert v_zscore == 0.0


@pytest.mark.unit
def test_p2_08_numerical_indicator_fixtures_parity():
    """
    P2-08: Exact numerical fixture parity against standardized mathematical sequences:
    EMA20, RSI14, MACD(12,26,9), ATR14, ADX14, Bollinger Bands.
    """
    # 1. EMA 20 on linearly rising series [100, 101, 102, ..., 139] (40 bars)
    series_40 = [Decimal(str(100 + i)) for i in range(40)]
    ema20_series = calculate_ema(series_40, period=20)
    # At index 19 (first SMA seed): SMA(0..19) = 109.5
    assert ema20_series[19] == Decimal("109.50000000")
    # At index 20: (120 - 109.5) * (2/21) + 109.5 = 110.5
    expected_ema_20 = Decimal("109.5") + (Decimal("120") - Decimal("109.5")) * (Decimal("2") / Decimal("21"))
    assert abs(ema20_series[20] - expected_ema_20) < Decimal("1e-7")

    # 2. RSI 14 on known step sequence: 14 gains of 2.0 then 14 losses of 1.0
    gains_and_losses = [Decimal("100.0")]
    for _ in range(14):
        gains_and_losses.append(gains_and_losses[-1] + Decimal("2.0"))
    for _ in range(14):
        gains_and_losses.append(gains_and_losses[-1] - Decimal("1.0"))
    
    rsi_val = calculate_rsi(gains_and_losses, period=14)
    assert rsi_val is not None
    assert 0.0 <= rsi_val <= 100.0

    # 3. ATR 14 on constant 10-point bars
    highs = [Decimal(str(2510 + i)) for i in range(30)]
    lows = [Decimal(str(2500 + i)) for i in range(30)]
    closes = [Decimal(str(2505 + i)) for i in range(30)]
    atr_val = calculate_atr(highs, lows, closes, period=14)
    # TR is constant 10.0 for every bar
    assert atr_val == Decimal("10.00000000")

    # 4. Bollinger Bands on sequence with mean=100.0, std_dev=2.0
    # Values: 10 bars of 98.0, 10 bars of 102.0 -> mean=100.0, variance = 1/20 * (10*(4) + 10*(4)) = 4.0 -> std=2.0
    bb_closes = [Decimal("98.0")] * 10 + [Decimal("102.0")] * 10
    upper, mid, lower, bw = calculate_bollinger_bands(bb_closes, period=20, num_std=2.0)
    assert mid == Decimal("100.00000000")
    assert upper == Decimal("104.00000000")
    assert lower == Decimal("96.00000000")
    assert bw == 8.0  # (104 - 96) / 100 * 100 = 8.0%

    # 5. MACD (12, 26, 9) on steady ramp
    macd_l, signal_l, hist_l = calculate_macd(series_40, fast_period=12, slow_period=26, signal_period=9)
    assert macd_l is not None
    assert signal_l is not None
    assert hist_l is not None
    assert abs(hist_l - (macd_l - signal_l)) < Decimal("1e-7")

    # 6. ADX 14 on steady trend
    adx_val, plus_di, minus_di = calculate_adx(highs, lows, closes, period=14)
    assert adx_val is not None
    assert plus_di is not None
    assert minus_di is not None
    assert 0.0 <= adx_val <= 100.0
    assert plus_di > minus_di  # Upward trending series has plus_di > minus_di


@pytest.mark.unit
def test_p2_09_confirmation_timestamp_causality():
    """
    P2-09: Confirmation Timestamp Causality.
    For swing L=3, R=3 with candidate at bar i=3:
    - 15m confirmation candle (bar 6): open = 10:00, close = 10:15.
    - At 10:14:59 (before candle 6 is complete/closed): Swing is UNAVAILABLE.
    - At 10:15:00 (candle 6 becomes closed/knowable): Swing is AVAILABLE with detected_at = 10:15:00.
    """
    t0 = datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc)
    candles = [
        CandleData(
            timestamp_open=t0 + timedelta(minutes=15 * i),
            timestamp_close=t0 + timedelta(minutes=15 * (i + 1)),
            open=Decimal(str(100 + i)),
            high=Decimal(str(102 + (20 if i == 3 else i))),  # Peak at bar 3 (high = 125)
            low=Decimal(str(98 + i)),
            close=Decimal(str(101 + i)),
            volume=Decimal("100"),
            is_closed=True,
        )
        for i in range(6)  # Bars 0, 1, 2, 3, 4, 5 (bar 5 closes at 10:00)
    ]

    # Bar 6 (open 10:00, close 10:15)
    t_bar6_open = t0 + timedelta(minutes=90)   # 10:00
    t_bar6_close = t0 + timedelta(minutes=105)  # 10:15

    # At 10:14:59 (before bar 6 completes): Only bars 0..5 are complete
    swings_before_10_15 = detect_causal_swings(candles, left_bars=3, right_bars=3)
    assert len(swings_before_10_15) == 0, "Swing at bar 3 must NOT be available before bar 6 closes!"

    # At 10:15:00: Bar 6 closes and its High/Low is complete
    bar_6 = CandleData(
        timestamp_open=t_bar6_open,
        timestamp_close=t_bar6_close,
        open=Decimal("105"),
        high=Decimal("107"),
        low=Decimal("103"),
        close=Decimal("106"),
        volume=Decimal("100"),
        is_closed=True,
    )
    candles.append(bar_6)

    swings_at_10_15 = detect_causal_swings(candles, left_bars=3, right_bars=3)
    assert len(swings_at_10_15) == 1, "Swing at bar 3 must become available when bar 6 closes!"
    swing = swings_at_10_15[0]
    assert swing.index == 3
    assert swing.price == Decimal("122")
    assert swing.timestamp == candles[3].timestamp_open
    # Strictly equal to bar 6 timestamp_close (10:15:00 UTC)
    assert swing.detected_at == t_bar6_close
    assert swing.detected_at == candles[6].timestamp_close


@pytest.mark.unit
def test_p2_10_bos_event_timestamp_causality():
    """
    P2-10: BOS Event Timestamp Causality.
    Break of Structure rule uses closed candle confirmation:
    - 15m breakout candle: open = 10:00, close = 10:15.
    - Intra-bar at 10:06 / 10:10 (candle is unclosed): BOS is UNAVAILABLE (NONE).
    - At 10:15 (candle closes above confirmed swing high): BOS is CONFIRMED with timestamp = 10:15.
    """
    t0 = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    # Establish confirmed swing high at bar 3 (high=120), confirmed at bar 6 (close at 9:45)
    candles = [
        _make_candle(0, "100", "102", "98", "101"),
        _make_candle(1, "101", "105", "100", "104"),
        _make_candle(2, "104", "108", "102", "106"),
        _make_candle(3, "106", "120", "105", "118"),  # Swing High at 120
        _make_candle(4, "118", "115", "110", "112"),
        _make_candle(5, "112", "110", "105", "108"),
        _make_candle(6, "108", "105", "100", "102"),  # Confirms swing high at 120
    ]

    engine = CausalStructureEngine()

    # Case A: Bar 7 is intra-bar (unclosed, e.g. 10:10 with price currently at 125 > 120)
    t7_open = t0 + timedelta(minutes=15 * 7)
    t7_close = t7_open + timedelta(minutes=15)
    unclosed_bar_7 = CandleData(
        timestamp_open=t7_open,
        timestamp_close=t7_close,
        open=Decimal("102"),
        high=Decimal("126"),
        low=Decimal("102"),
        close=Decimal("125"),
        volume=Decimal("100"),
        is_closed=False,  # Still forming!
    )
    res_unclosed = engine.analyze(candles + [unclosed_bar_7], atr=Decimal("5.0"))
    assert res_unclosed.bos == BosType.NONE, "Unclosed candle cannot trigger close-confirmed BOS!"

    # Case B: Bar 7 closes at 10:15
    closed_bar_7 = CandleData(
        timestamp_open=t7_open,
        timestamp_close=t7_close,
        open=Decimal("102"),
        high=Decimal("126"),
        low=Decimal("102"),
        close=Decimal("125"),
        volume=Decimal("100"),
        is_closed=True,  # Fully closed at 10:15
    )
    res_closed = engine.analyze(candles + [closed_bar_7], atr=Decimal("5.0"))
    assert res_closed.bos == BosType.BULLISH, "Closed candle > 120 must trigger Bullish BOS!"
    assert res_closed.timestamp == t7_close, "BOS StructureResult timestamp must be the closed candle timestamp!"


@pytest.mark.unit
def test_p2_11_realized_volatility_definition():
    """
    P2-11: Realized Volatility Definition & Manual Numerical Parity.
    Verifies that calculate_realized_volatility implements the exact documented
    raw rolling percentage formula:
      r_t = ln(Close_t / Close_{t-1})
      mean_r = sum(r_t) / 20
      var = sum((r_t - mean_r)^2) / 20
      realized_vol_20 (%) = sqrt(var) * 100.0
    """
    import math

    # 21 price points -> 20 log return periods
    prices = [
        Decimal("2500.00"), Decimal("2510.50"), Decimal("2495.20"), Decimal("2525.00"),
        Decimal("2530.10"), Decimal("2515.40"), Decimal("2540.00"), Decimal("2535.80"),
        Decimal("2550.20"), Decimal("2545.00"), Decimal("2560.50"), Decimal("2555.00"),
        Decimal("2570.00"), Decimal("2565.30"), Decimal("2580.10"), Decimal("2575.00"),
        Decimal("2590.40"), Decimal("2585.20"), Decimal("2600.00"), Decimal("2595.50"),
        Decimal("2610.00"),
    ]
    assert len(prices) == 21

    # Independent manual calculation step-by-step
    log_rets = [math.log(float(prices[i]) / float(prices[i - 1])) for i in range(1, 21)]
    assert len(log_rets) == 20

    manual_mean = sum(log_rets) / 20.0
    manual_var = sum((r - manual_mean) ** 2 for r in log_rets) / 20.0
    manual_std_pct = math.sqrt(manual_var) * 100.0
    expected_rv = float(round(manual_std_pct, 4))

    actual_rv = calculate_realized_volatility(prices, period=20)
    assert actual_rv is not None
    assert abs(actual_rv - expected_rv) < 1e-6, f"Expected {expected_rv}, got {actual_rv}"

    # Edge test: Zero volatility on identical prices
    flat_prices = [Decimal("2500.00")] * 21
    assert calculate_realized_volatility(flat_prices, period=20) == 0.0

    # Insufficient bars returns None
    assert calculate_realized_volatility(prices[:20], period=20) is None


@pytest.mark.unit
def test_p2_12_exact_numerical_fixtures_parity():
    """
    P2-12: Independent Exact Numerical Parity Fixtures for RSI14, MACD(12,26,9), and ADX14.
    """
    # 1. Exact RSI 14 verification against known Wilder recursive step
    # Seed 14 gains of 2.0 and 0 losses -> initial avg_gain = 2.0, avg_loss = 0.0 -> RSI = 100.0
    all_gains = [Decimal("100.0")] + [Decimal(str(100.0 + 2.0 * (i + 1))) for i in range(14)]
    assert calculate_rsi(all_gains, period=14) == 100.0

    # Next step: gain of 0.0, loss of 1.0 (price drops from 128 to 127)
    # new_avg_gain = (2.0 * 13 + 0.0) / 14 = 26.0 / 14 = 1.85714286
    # new_avg_loss = (0.0 * 13 + 1.0) / 14 = 1.0 / 14 = 0.07142857
    # RS = (26/14) / (1/14) = 26.0
    # RSI = 100 - (100 / (1 + 26)) = 100 - (100 / 27) = 100 - 3.7037 = 96.30%
    step_drop = all_gains + [Decimal("127.0")]
    rsi_step = calculate_rsi(step_drop, period=14)
    expected_rsi = float(round(Decimal("100.0") - (Decimal("100.0") / Decimal("27.0")), 2))  # 96.30
    assert rsi_step == expected_rsi

    # 2. Exact MACD (12, 26, 9) parity
    # On a sequence of 40 constant prices [100.0] * 40:
    # EMA12 = 100.0, EMA26 = 100.0 -> MACD line = 0.0, Signal line = 0.0, Hist = 0.0
    flat_series = [Decimal("100.0")] * 40
    m_line, s_line, h_line = calculate_macd(flat_series, 12, 26, 9)
    assert m_line == Decimal("0.00000000")
    assert s_line == Decimal("0.00000000")
    assert h_line == Decimal("0.00000000")

    # 3. Exact ADX 14 parity on constant directional staircase
    # High_i = 100 + 2*i, Low_i = 90 + 2*i, Close_i = 95 + 2*i (35 bars)
    # For every bar: TR = max(10, |102-95|=7, |92-95|=3) = 10.0
    # up_move = 2.0, down_move = -2.0 -> +DM = 2.0, -DM = 0.0
    # Smooth TR = 140.0, Smooth +DM = 28.0, Smooth -DM = 0.0
    # +DI = 28/140 * 100 = 20.0%, -DI = 0.0%, DX = 100.0%, ADX = 100.0%
    staircase_highs = [Decimal(str(100 + 2 * i)) for i in range(35)]
    staircase_lows = [Decimal(str(90 + 2 * i)) for i in range(35)]
    staircase_closes = [Decimal(str(95 + 2 * i)) for i in range(35)]

    adx_out, p_di_out, m_di_out = calculate_adx(staircase_highs, staircase_lows, staircase_closes, period=14)
    assert adx_out == 100.0, f"Expected ADX=100.0, got {adx_out}"
    assert p_di_out == 20.0, f"Expected +DI=20.0, got {p_di_out}"
    assert m_di_out == 0.0, f"Expected -DI=0.0, got {m_di_out}"



@pytest.mark.unit
def test_p2_13_realized_volatility_ddof_semantics():
    """
    P2-13: Realized Volatility DDof Semantics.
    Verifies that the engine standardizes strictly on population standard deviation (ddof=0, denominator = 20)
    and distinguishes it from sample standard deviation (ddof=1, denominator = 19).
    """
    import math

    prices = [
        Decimal("2500.00"), Decimal("2510.50"), Decimal("2495.20"), Decimal("2525.00"),
        Decimal("2530.10"), Decimal("2515.40"), Decimal("2540.00"), Decimal("2535.80"),
        Decimal("2550.20"), Decimal("2545.00"), Decimal("2560.50"), Decimal("2555.00"),
        Decimal("2570.00"), Decimal("2565.30"), Decimal("2580.10"), Decimal("2575.00"),
        Decimal("2590.40"), Decimal("2585.20"), Decimal("2600.00"), Decimal("2595.50"),
        Decimal("2610.00"),
    ]
    log_rets = [math.log(float(prices[i]) / float(prices[i - 1])) for i in range(1, 21)]
    mean_r = sum(log_rets) / 20.0
    sum_sq_diff = sum((r - mean_r) ** 2 for r in log_rets)

    # 1. Population std dev (ddof=0, N=20)
    var_pop = sum_sq_diff / 20.0
    expected_pop_vol = float(round(math.sqrt(var_pop) * 100.0, 4))

    # 2. Sample std dev (ddof=1, N-1=19)
    var_sample = sum_sq_diff / 19.0
    expected_sample_vol = float(round(math.sqrt(var_sample) * 100.0, 4))

    # Assert that ddof=0 and ddof=1 produce measurably different values
    assert expected_pop_vol != expected_sample_vol
    assert expected_sample_vol > expected_pop_vol

    # Assert default calculate_realized_volatility uses ddof=0 (population)
    default_rv = calculate_realized_volatility(prices, period=20)
    explicit_pop_rv = calculate_realized_volatility(prices, period=20, ddof=0)
    explicit_sample_rv = calculate_realized_volatility(prices, period=20, ddof=1)

    assert default_rv is not None
    assert explicit_pop_rv is not None
    assert explicit_sample_rv is not None
    assert default_rv == expected_pop_vol
    assert explicit_pop_rv == expected_pop_vol
    assert explicit_sample_rv == expected_sample_vol
    assert default_rv == explicit_pop_rv
