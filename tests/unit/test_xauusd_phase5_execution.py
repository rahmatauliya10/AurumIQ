"""
Unit tests for XAUUSD Phase 5 side-aware entry execution simulation.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    QuoteData,
    RiskSide,
    VolumeEvidenceType,
)
from engine.risk.xauusd_execution import (
    SideAwareEntryExecutionModel,
    validate_xauusd_candle,
    validate_xauusd_quote,
)
from engine.risk.xauusd_policy import XauUsdExecutionPolicy


@pytest.fixture
def exec_model():
    policy = XauUsdExecutionPolicy(
        latency_seconds=1.0,
        synthetic_spread_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.01"),
    )
    return SideAwareEntryExecutionModel(
        code_revision="test_rev",
        execution_policy=policy,
        phase5_policy_fingerprint="test_exec_policy_fp",
    )


@pytest.mark.unit
def test_quote_validation():
    """Validates quotes for positive finite bid/ask, bid <= ask, and timezone awareness."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    assert validate_xauusd_quote(QuoteData(t, Decimal("2500.00"), Decimal("2500.20"))) is True

    # Crossed bid/ask fails
    assert validate_xauusd_quote(QuoteData(t, Decimal("2500.50"), Decimal("2500.20"))) is False

    # Negative / zero fails
    assert validate_xauusd_quote(QuoteData(t, Decimal("0.00"), Decimal("2500.20"))) is False
    assert validate_xauusd_quote(QuoteData(t, Decimal("-10.00"), Decimal("2500.20"))) is False

    # Naive datetime fails
    assert validate_xauusd_quote(QuoteData(datetime(2026, 9, 1, 8, 0, 0), Decimal("2500.00"), Decimal("2500.20"))) is False


@pytest.mark.unit
def test_candle_validation():
    """Validates candles for positive finite OHLC, geometric relations, and aware datetimes."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)
    valid_c = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2510.00"), Decimal("2495.00"), Decimal("2505.00"),
        Decimal("100.0"), True
    )
    assert validate_xauusd_candle(valid_c) is True

    # High < Low fails
    invalid_c1 = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2490.00"), Decimal("2495.00"), Decimal("2505.00"),
        Decimal("100.0"), True
    )
    assert validate_xauusd_candle(invalid_c1) is False

    # High < Open fails
    invalid_c2 = CandleData(
        t_open, t_close,
        Decimal("2515.00"), Decimal("2510.00"), Decimal("2495.00"), Decimal("2505.00"),
        Decimal("100.0"), True
    )
    assert validate_xauusd_candle(invalid_c2) is False


@pytest.mark.unit
def test_market_execution_long_and_short(exec_model):
    """LONG uses ASK and adds slippage; SHORT uses BID and subtracts slippage."""
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    # latency is 1.0s -> earliest_exec_ts = 8:00:01
    q_pre = QuoteData(sig_t + timedelta(milliseconds=500), Decimal("2499.00"), Decimal("2499.20"))
    q_post = QuoteData(sig_t + timedelta(seconds=2), Decimal("2500.00"), Decimal("2500.40"))

    # LONG: raw = 2500.40, slippage 0.01% = 0.25004 -> fill = 2500.65004
    res_long = exec_model.simulate_market_after_signal(
        side=RiskSide.LONG,
        signal_generated_at=sig_t,
        quotes=[q_pre, q_post],
        source_phase4_fingerprint="test_sig_fp",
    )
    assert res_long.is_filled is True
    assert res_long.raw_executable_price == Decimal("2500.40")
    assert res_long.fill_price == Decimal("2500.40") + (Decimal("2500.40") * Decimal("0.0001"))
    assert res_long.observed_spread == Decimal("0.40")
    assert res_long.synthetic_spread == Decimal("0.00")

    # SHORT: raw = 2500.00, slippage 0.01% = 0.25 -> fill = 2499.75
    res_short = exec_model.simulate_market_after_signal(
        side=RiskSide.SHORT,
        signal_generated_at=sig_t,
        quotes=[q_pre, q_post],
        source_phase4_fingerprint="test_sig_fp",
    )
    assert res_short.is_filled is True
    assert res_short.raw_executable_price == Decimal("2500.00")
    assert res_short.fill_price == Decimal("2499.75")
    assert res_short.adverse_slippage == Decimal("0.25")


@pytest.mark.unit
def test_next_bar_open_execution(exec_model):
    """Next bar open adds spread+slippage for LONG, subtracts for SHORT."""
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_bar_open = sig_t + timedelta(minutes=15)
    t_bar_close = t_bar_open + timedelta(minutes=15)
    bar = CandleData(
        t_bar_open, t_bar_close,
        Decimal("2500.00"), Decimal("2510.00"), Decimal("2495.00"), Decimal("2505.00"),
        Decimal("100.0"), True
    )

    # spread 0.02% of 2500 = 0.50; slippage 0.01% of 2500 = 0.25
    # LONG: 2500.00 + 0.50 + 0.25 = 2500.75
    res_long = exec_model.simulate_next_bar_open(
        side=RiskSide.LONG,
        signal_generated_at=sig_t,
        candles=[bar],
        source_phase4_fingerprint="test_sig_fp",
    )
    assert res_long.is_filled is True
    assert res_long.fill_price == Decimal("2500.75")
    assert res_long.synthetic_spread == Decimal("0.50")
    assert res_long.adverse_slippage == Decimal("0.25")

    # SHORT: 2500.00 - 0.50 - 0.25 = 2499.25
    res_short = exec_model.simulate_next_bar_open(
        side=RiskSide.SHORT,
        signal_generated_at=sig_t,
        candles=[bar],
        source_phase4_fingerprint="test_sig_fp",
    )
    assert res_short.is_filled is True
    assert res_short.fill_price == Decimal("2499.25")
    assert res_short.synthetic_spread == Decimal("0.50")
    assert res_short.adverse_slippage == Decimal("0.25")


@pytest.mark.unit
def test_limit_zone_execution(exec_model):
    """LIMIT LONG triggers on ASK <= limit; SHORT on BID >= limit. Fill never worse than limit."""
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    limit = Decimal("2500.00")

    # Quotes after latency (1.0s)
    # Quote 1: ask=2500.50 > limit (no long trigger)
    q1 = QuoteData(sig_t + timedelta(seconds=2), Decimal("2500.10"), Decimal("2500.50"))
    # Quote 2: ask=2499.80 <= limit (triggers long); raw=2499.80, slippage 0.01%=0.24998 -> fill capped at limit 2500.00
    q2 = QuoteData(sig_t + timedelta(seconds=3), Decimal("2499.50"), Decimal("2499.80"))

    res_long = exec_model.simulate_limit_zone(
        side=RiskSide.LONG,
        signal_generated_at=sig_t,
        limit_price=limit,
        source_phase4_fingerprint="test_sig_fp",
        quotes=[q1, q2],
    )
    assert res_long.is_filled is True
    assert res_long.fill_price <= limit

    # SHORT limit: bid >= limit
    # Quote 3: bid=2500.20 >= limit (triggers short); raw=2500.20, slippage=0.25 -> 2499.95 floored at limit 2500.00
    q3 = QuoteData(sig_t + timedelta(seconds=4), Decimal("2500.20"), Decimal("2500.60"))
    res_short = exec_model.simulate_limit_zone(
        side=RiskSide.SHORT,
        signal_generated_at=sig_t,
        limit_price=limit,
        source_phase4_fingerprint="test_sig_fp",
        quotes=[q3],
    )
    assert res_short.is_filled is True
    assert res_short.fill_price >= limit


@pytest.mark.unit
def test_market_execution_exact_earliest_exec_ts_boundary(exec_model):
    """
    Explicit contract proof: MARKET_AFTER_SIGNAL accepts quote with timestamp == earliest_exec_ts,
    while rejecting any quote strictly before earliest_exec_ts.
    """
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    earliest_exec_ts = sig_t + timedelta(seconds=1)  # latency = 1.0s

    # 1. Quote strictly before earliest_exec_ts (8:00:00.999999) is rejected
    q_pre = QuoteData(earliest_exec_ts - timedelta(microseconds=1), Decimal("2499.00"), Decimal("2499.20"))
    res_pre = exec_model.simulate_market_after_signal(RiskSide.LONG, sig_t, [q_pre], "fp")
    assert res_pre.is_filled is False
    assert res_pre.fill_price is None

    # 2. Quote at EXACT earliest_exec_ts (8:00:01.000000) is accepted and filled
    q_exact = QuoteData(earliest_exec_ts, Decimal("2500.00"), Decimal("2500.40"))
    res_exact = exec_model.simulate_market_after_signal(RiskSide.LONG, sig_t, [q_exact], "fp")
    assert res_exact.is_filled is True
    assert res_exact.fill_timestamp == earliest_exec_ts
    assert res_exact.raw_executable_price == Decimal("2500.40")


@pytest.mark.unit
def test_next_bar_open_exact_earliest_exec_ts_boundary(exec_model):
    """
    Explicit contract proof: NEXT_BAR_OPEN accepts candle with timestamp_open == earliest_exec_ts,
    while rejecting candle with timestamp_open < earliest_exec_ts.
    """
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    earliest_exec_ts = sig_t + timedelta(seconds=1)

    # 1. Bar with open < earliest_exec_ts is rejected
    bar_pre = CandleData(
        sig_t, sig_t + timedelta(minutes=15),
        Decimal("2500.00"), Decimal("2510.00"), Decimal("2495.00"), Decimal("2505.00"),
        Decimal("100.0"), True
    )
    res_pre = exec_model.simulate_next_bar_open(RiskSide.LONG, sig_t, [bar_pre], "fp")
    assert res_pre.is_filled is False
    assert res_pre.fill_price is None

    # 2. Bar with open == earliest_exec_ts is accepted and filled on open
    bar_exact = CandleData(
        earliest_exec_ts, earliest_exec_ts + timedelta(minutes=15),
        Decimal("2500.00"), Decimal("2510.00"), Decimal("2495.00"), Decimal("2505.00"),
        Decimal("100.0"), True
    )
    res_exact = exec_model.simulate_next_bar_open(RiskSide.LONG, sig_t, [bar_exact], "fp")
    assert res_exact.is_filled is True
    assert res_exact.fill_timestamp == earliest_exec_ts
    assert res_exact.raw_executable_price == Decimal("2500.00")


@pytest.mark.unit
def test_limit_zone_exact_earliest_exec_ts_boundary(exec_model):
    """
    Explicit contract proof: LIMIT_ZONE execution accepts quote and candle activation at exactly earliest_exec_ts,
    while ignoring pre-activation touches before earliest_exec_ts.
    """
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    earliest_exec_ts = sig_t + timedelta(seconds=1)
    limit = Decimal("2500.00")

    # Quote touch strictly before earliest_exec_ts is ignored
    q_pre = QuoteData(earliest_exec_ts - timedelta(microseconds=1), Decimal("2499.00"), Decimal("2499.50"))
    res_pre = exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, limit, "fp", quotes=[q_pre])
    assert res_pre.is_filled is False

    # Quote touch at EXACT earliest_exec_ts is accepted and filled
    q_exact = QuoteData(earliest_exec_ts, Decimal("2499.50"), Decimal("2499.80"))
    res_exact = exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, limit, "fp", quotes=[q_exact])
    assert res_exact.is_filled is True
    assert res_exact.fill_timestamp == earliest_exec_ts

    # Candle touch at EXACT earliest_exec_ts open is accepted and filled
    bar_exact = CandleData(
        earliest_exec_ts, earliest_exec_ts + timedelta(minutes=15),
        Decimal("2502.00"), Decimal("2505.00"), Decimal("2498.00"), Decimal("2501.00"),
        Decimal("100.0"), True
    )
    res_bar = exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, limit, "fp", candles=[bar_exact])
    assert res_bar.is_filled is True
    assert res_bar.fill_timestamp == earliest_exec_ts


@pytest.mark.unit
def test_execution_provenance_and_policy_fingerprint_pinning():
    """
    Tests proving:
      A. Blank / 'NONE' policy fingerprint is rejected on construction.
      B. Changed execution policy changes execution fingerprint.
      C. Execution fingerprint binds the required Phase 5 policy fingerprint.
    """
    policy = XauUsdExecutionPolicy(latency_seconds=1.0, slippage_pct=Decimal("0.01"))

    # A: Blank or 'NONE' policy fingerprint is rejected
    with pytest.raises(ValueError, match="phase5_policy_fingerprint is required"):
        SideAwareEntryExecutionModel("rev1", policy, phase5_policy_fingerprint="")

    with pytest.raises(ValueError, match="phase5_policy_fingerprint is required"):
        SideAwareEntryExecutionModel("rev1", policy, phase5_policy_fingerprint="NONE")

    # B & C: Changed policy fingerprint binds and changes execution fingerprint
    m1 = SideAwareEntryExecutionModel("rev1", policy, phase5_policy_fingerprint="pol_fp_1")
    m2 = SideAwareEntryExecutionModel("rev1", policy, phase5_policy_fingerprint="pol_fp_2")

    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    q = QuoteData(sig_t + timedelta(seconds=2), Decimal("2500.00"), Decimal("2500.40"))

    res1 = m1.simulate_market_after_signal(RiskSide.LONG, sig_t, [q], "sig_fp")
    res2 = m2.simulate_market_after_signal(RiskSide.LONG, sig_t, [q], "sig_fp")

    assert res1.execution_fingerprint != res2.execution_fingerprint


@pytest.mark.unit
def test_execution_requires_aware_signal_timestamp(exec_model):
    """Execution simulation raises ValueError when signal_generated_at is naive."""
    naive_t = datetime(2026, 9, 1, 8, 0, 0)
    with pytest.raises(ValueError, match="must be timezone aware"):
        exec_model.simulate_market_after_signal(RiskSide.LONG, naive_t, [], "fp")

    with pytest.raises(ValueError, match="must be timezone aware"):
        exec_model.simulate_next_bar_open(RiskSide.LONG, naive_t, [], "fp")

    with pytest.raises(ValueError, match="must be timezone aware"):
        exec_model.simulate_limit_zone(RiskSide.LONG, naive_t, Decimal("2500.00"), "fp")


@pytest.mark.unit
def test_limit_price_validation(exec_model):
    """LIMIT_ZONE rejects non-positive, non-finite, or non-Decimal limit prices."""
    sig_t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="must be a positive finite Decimal"):
        exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, Decimal("0.00"), "fp")

    with pytest.raises(ValueError, match="must be a positive finite Decimal"):
        exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, Decimal("-100.00"), "fp")

    with pytest.raises(ValueError, match="must be a positive finite Decimal"):
        exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, Decimal("NaN"), "fp")

    with pytest.raises(ValueError, match="must be a positive finite Decimal"):
        exec_model.simulate_limit_zone(RiskSide.LONG, sig_t, Decimal("Infinity"), "fp")
