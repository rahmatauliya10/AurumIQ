"""
Comprehensive deterministic test suite for XAUUSD Data Quality and Readiness Pipeline.
Covers the 12 non-negotiable governance requirements defined in the calibration protocol.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentType,
    InstrumentRole,
    MarketListing,
    ListingRole,
    ListingStatus,
)
from apps.market_data.models import MarketCandle, CandleQualityFlag, VolumeEvidenceType
from apps.market_data.readiness import XauUsdDataReadinessEvaluator, XauUsdDataReadinessReport
from apps.market_data.providers.xauusd_spot import XauUsdSpotProvider
from apps.market_data.providers.base import RawCandle


@pytest.fixture
def xauusd_setup(db):
    """Seed standard assets, instruments, and primary XAUUSD spot listing."""
    call_command("seed_instruments")
    instrument = Instrument.get_canonical_xauusd()
    primary_listing = MarketListing.objects.filter(
        instrument=instrument,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    ).first()
    return instrument, primary_listing


def _create_clean_candles(instrument, count=25, tf="15m", source="xauusd_primary"):
    """Helper to create N valid chronological UTC candles."""
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    delta = timedelta(minutes=15)
    candles = []
    for i in range(count):
        t_open = base_time + i * delta
        t_close = t_open + delta
        c = MarketCandle.objects.create(
            instrument=instrument,
            source=source,
            timeframe=tf,
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2000.00"),
            high=Decimal("2005.00"),
            low=Decimal("1995.00"),
            close=Decimal("2002.00"),
            volume=Decimal("150.0"),
            volume_evidence=VolumeEvidenceType.TICK_VOLUME,
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal("2002.00000000"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )
        candles.append(c)
    return candles


@pytest.mark.django_db
def test_01_empty_database_calibration_blocked(xauusd_setup):
    """Scenario 1: Empty database must strictly block calibration and produce canonical empty hash."""
    instrument, _ = xauusd_setup
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)

    assert report.passed is False
    assert report.decision == "CALIBRATION_DATA_NOT_READY"
    assert report.total_candles == 0
    assert report.dataset_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert any("Zero historical spot XAUUSD candles" in r for r in report.reasons)
    assert any("Empty dataset hash" in r for r in report.reasons)


@pytest.mark.django_db
def test_02_insufficient_history_calibration_blocked(xauusd_setup):
    """Scenario 2: Insufficient history (< 20 bars of 15m) blocks calibration."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=10, tf="15m")

    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.passed is False
    assert report.decision == "CALIBRATION_DATA_NOT_READY"
    assert report.is_warmup_satisfied is False
    assert any("Insufficient 15m feature warm-up bars" in r for r in report.reasons)


@pytest.mark.django_db
def test_03_duplicate_data_rejected_or_reported(xauusd_setup):
    """Scenario 3: Duplicate candle timestamps are detected and block calibration."""
    instrument, _ = xauusd_setup
    candles = _create_clean_candles(instrument, count=25, tf="15m")

    # In database, unique_together strictly rejects duplicates
    from django.db import transaction
    from django.db.utils import IntegrityError
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MarketCandle.objects.create(
                instrument=instrument,
                source="xauusd_primary",
                timeframe="15m",
                timestamp_open=candles[0].timestamp_open,
                timestamp_close=candles[0].timestamp_close,
                open=Decimal("2000.00"),
                high=Decimal("2005.00"),
                low=Decimal("1995.00"),
                close=Decimal("2002.00"),
                volume=Decimal("100.0"),
                is_closed=True,
            )

    # In evaluator, duplicate candles in input list are detected and block gate
    dupe_list = list(candles) + [candles[0]]
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_candles=dupe_list)
    assert report.duplicate_count == 1
    assert report.passed is False
    assert any("duplicate candle timestamps" in r for r in report.reasons)


@pytest.mark.django_db
def test_04_invalid_ohlc_rejected_or_reported(xauusd_setup):
    """Scenario 4: Invalid OHLC relationships (e.g. High < Low) block calibration."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=25, tf="15m")

    # Mutate one candle to have High < Low
    bad_c = MarketCandle.objects.first()
    bad_c.high = Decimal("1990.00")
    bad_c.low = Decimal("2010.00")
    bad_c.save()

    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.ohlc_error_count >= 1
    assert report.passed is False
    assert any("invalid OHLC relationship violations" in r for r in report.reasons)


@pytest.mark.django_db
def test_05_naive_timestamps_rejected_or_reported(xauusd_setup):
    """Scenario 5: Naive timestamps without explicit timezone awareness are rejected."""
    instrument, _ = xauusd_setup
    candles = _create_clean_candles(instrument, count=25, tf="15m")

    # Construct a candle with naive timestamp_open
    naive_candle = RawCandle(
        symbol="XAUUSD",
        timeframe="15m",
        timestamp_open=datetime(2026, 1, 1, 0, 0),  # Naive (tzinfo=None)
        timestamp_close=datetime(2026, 1, 1, 0, 15),  # Naive
        open=Decimal("2000.00"),
        high=Decimal("2005.00"),
        low=Decimal("1995.00"),
        close=Decimal("2002.00"),
        volume=Decimal("100.0"),
        is_closed=True,
        source="xauusd_primary",
    )

    override_list = list(candles) + [naive_candle]
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_candles=override_list)
    assert report.naive_timestamp_count >= 1
    assert report.passed is False
    assert any("naive timezone timestamps" in r for r in report.reasons)


@pytest.mark.django_db
def test_06_wrong_instrument_or_source_contamination_rejected(xauusd_setup):
    """Scenario 6: Cross-asset or foreign source contamination is detected and rejected."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=25, tf="15m")

    # Create a contaminated candle under XAU/USD with a foreign provider (e.g. binance)
    MarketCandle.objects.create(
        instrument=instrument,
        source="binance_foreign_feed",
        timeframe="15m",
        timestamp_open=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 1, 1, 10, 15, tzinfo=timezone.utc),
        open=Decimal("2000.00"),
        high=Decimal("2005.00"),
        low=Decimal("1995.00"),
        close=Decimal("2002.00"),
        volume=Decimal("10.0"),
        volume_evidence=VolumeEvidenceType.TICK_VOLUME,
        quote_rate=Decimal("1.0"),
        close_usd=Decimal("2002.0"),
        is_closed=True,
    )

    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.source_contamination_count >= 1
    assert report.passed is False
    assert any("CONTAMINATION: Found" in r for r in report.reasons)


@pytest.mark.django_db
def test_07_insufficient_warmup_blocked(xauusd_setup):
    """Scenario 7: Exactly 19 bars of 15m (1 bar short of 20) fails the warm-up check."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=19, tf="15m")

    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.warmup_15m_bars == 19
    assert report.is_warmup_satisfied is False
    assert report.passed is False
    assert report.decision == "CALIBRATION_DATA_NOT_READY"


@pytest.mark.django_db
def test_08_valid_xauusd_dataset_readiness_eligible_to_open(xauusd_setup):
    """Scenario 8: A fully valid, non-contaminated XAUUSD candle dataset satisfies technical candle gate but returns CANDLES_READY_MACRO_MISSING in full gate."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    # Technical-only check passes feature calculation gate
    report_tech = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, technical_only=True)
    assert report_tech.candle_gate_passed is True
    assert report_tech.passed is True
    assert report_tech.decision == "READY_FOR_EMPIRICAL_CALIBRATION"

    # Full governance gate prevents generic PASS and accurately reports missing macro evidence
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.candle_gate_passed is True
    assert report.passed is False
    assert report.decision == "CANDLES_READY_MACRO_MISSING"
    assert report.warmup_15m_bars == 30
    assert report.ohlc_error_count == 0
    assert report.duplicate_count == 0
    assert report.source_contamination_count == 0
    assert len(report.dataset_hash) == 64


@pytest.mark.django_db
def test_08a_candles_ready_empirical_friction_missing(xauusd_setup):
    """Scenario 8A: When candles and macro are present, reports CANDLES_READY_EMPIRICAL_FRICTION_MISSING."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_macro_count=5,
    )
    assert report.candle_gate_passed is True
    assert report.passed is False
    assert report.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


@pytest.mark.django_db
def test_08b_candles_ready_quote_evidence_missing(xauusd_setup):
    """Scenario 8B: When candles, macro, and friction are configured, reports CANDLES_READY_QUOTE_EVIDENCE_MISSING."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_macro_count=5,
        override_friction_status="EMPIRICAL_FRICTION_CONFIGURED",
    )
    assert report.candle_gate_passed is True
    assert report.passed is False
    assert report.decision == "CANDLES_READY_QUOTE_EVIDENCE_MISSING"


@pytest.mark.django_db
def test_08c_full_evidence_data_ready_for_calibration_review(xauusd_setup):
    """Scenario 8C: When all evidence streams exist defensibly, returns DATA_READY_FOR_CALIBRATION_REVIEW."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_macro_count=5,
        override_friction_status="EMPIRICAL_FRICTION_CONFIGURED",
        override_quote_count=100,
    )
    assert report.candle_gate_passed is True
    assert report.passed is True
    assert report.decision == "DATA_READY_FOR_CALIBRATION_REVIEW"


@pytest.mark.django_db
def test_09_rerunning_backfill_no_duplicate_corruption(xauusd_setup):
    """Scenario 9: Re-running candle backfill is idempotent with update_or_create."""
    instrument, listing = xauusd_setup
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    # Upsert single candle
    c1, created1 = MarketCandle.objects.update_or_create(
        instrument=instrument,
        source=listing.provider,
        timeframe="15m",
        timestamp_open=now,
        defaults={
            "timestamp_close": now + timedelta(minutes=15),
            "open": Decimal("2050.00"),
            "high": Decimal("2055.00"),
            "low": Decimal("2048.00"),
            "close": Decimal("2052.00"),
            "volume": Decimal("100"),
            "volume_evidence": VolumeEvidenceType.TICK_VOLUME,
            "quote_rate": Decimal("1.0"),
            "close_usd": Decimal("2052.0"),
            "is_closed": True,
        },
    )
    assert created1 is True

    # Re-run identical upsert
    c2, created2 = MarketCandle.objects.update_or_create(
        instrument=instrument,
        source=listing.provider,
        timeframe="15m",
        timestamp_open=now,
        defaults={
            "timestamp_close": now + timedelta(minutes=15),
            "open": Decimal("2050.00"),
            "high": Decimal("2055.00"),
            "low": Decimal("2048.00"),
            "close": Decimal("2052.00"),
            "volume": Decimal("100"),
            "volume_evidence": VolumeEvidenceType.TICK_VOLUME,
            "quote_rate": Decimal("1.0"),
            "close_usd": Decimal("2052.0"),
            "is_closed": True,
        },
    )
    assert created2 is False
    assert c1.id == c2.id
    assert MarketCandle.objects.filter(instrument=instrument, timestamp_open=now).count() == 1


@pytest.mark.django_db
def test_10_manifest_hash_deterministic(xauusd_setup):
    """Scenario 10: Manifest dataset hash is 100% deterministic and sensitive to mutations."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=25, tf="15m")

    report1 = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    report2 = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report1.dataset_hash == report2.dataset_hash

    # Mutate a price slightly
    c = MarketCandle.objects.last()
    c.close = Decimal("2003.50")
    c.save()

    report3 = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report3.dataset_hash != report1.dataset_hash


@pytest.mark.django_db
def test_11_missing_provider_configuration_fails_closed(xauusd_setup):
    """Scenario 11: Unconfigured provider reports is_configured()=False and fails closed."""
    provider = XauUsdSpotProvider(feed_url=None, api_key=None)
    assert provider.is_configured() is False

    with pytest.raises(RuntimeError) as exc_info:
        provider.fetch_candles("XAUUSD", "15m", datetime.now(timezone.utc), datetime.now(timezone.utc))

    assert "PRIMARY_XAUUSD_UNAVAILABLE" in str(exc_info.value)

    # Also test backfill_candles command raises CommandError when unconfigured
    with pytest.raises(CommandError) as cmd_exc:
        call_command("backfill_candles", symbol="XAU/USD", provider="xauusd_primary")

    assert "PRIMARY_XAUUSD_UNCONFIGURED" in str(cmd_exc.value) or "NOT_CONFIGURED" in str(cmd_exc.value)


@pytest.mark.django_db
def test_12_no_xaut_contamination_enforced(xauusd_setup):
    """Scenario 12: XAUT data cannot be substituted or mixed into canonical XAUUSD."""
    instrument, _ = xauusd_setup

    xaut_asset = Asset.objects.filter(code="XAUT").first()
    usdt_asset = Asset.objects.filter(code="USDT").first()
    xaut_inst = Instrument.objects.filter(base_asset=xaut_asset, quote_asset=usdt_asset).first()

    # Create an XAUT candle
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    MarketCandle.objects.create(
        instrument=xaut_inst,
        source="binance",
        timeframe="15m",
        timestamp_open=t,
        timestamp_close=t + timedelta(minutes=15),
        open=Decimal("2000.00"),
        high=Decimal("2005.00"),
        low=Decimal("1995.00"),
        close=Decimal("2002.00"),
        volume=Decimal("50.0"),
        quote_rate=Decimal("1.00"),
        close_usd=Decimal("2002.00"),
        is_closed=True,
    )

    # Evaluate XAUUSD dataset — XAUT candle must not be counted or used
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.total_candles == 0
    assert report.passed is False
    assert report.decision == "CALIBRATION_DATA_NOT_READY"


@pytest.mark.django_db
def test_13_empty_dataset_hash_strictly_blocked_from_calibration_and_runner(xauusd_setup):
    """Scenario 13: Empty dataset hash (e3b0c442...) strictly causes CALIBRATION_DATA_NOT_READY and blocks backtest runner."""
    instrument, _ = xauusd_setup
    EMPTY_DATASET_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.dataset_hash == EMPTY_DATASET_HASH
    assert report.decision == "CALIBRATION_DATA_NOT_READY"
    assert report.passed is False

    # Attempting to run backtest runner with empty dataset hash raises ValueError
    from engine.backtest.repository import PointInTimeDataset
    from engine.backtest.xauusd_runner import XauUsdBacktestRunner
    from engine.backtest.xauusd_types import XauUsdBacktestRunSpec, XauUsdCostScenario, XauUsdCostConfig
    from engine.signals.profile import uncalibrated_xauusd_signal_profile
    from engine.risk.xauusd_policy import uncalibrated_xauusd_risk_profile

    empty_ds = PointInTimeDataset()
    t_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=t_start,
        end_time=t_end,
        timeframes=("15m",),
        cost_config=XauUsdCostConfig(
            entry_fee_bps=Decimal("0.0"),
            exit_fee_bps=Decimal("0.0"),
            synthetic_spread_bps=Decimal("1.5"),
            entry_slippage_bps=Decimal("0.5"),
            exit_slippage_bps=Decimal("0.5"),
        ),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        holding_horizon_bars_15m=4,
        holding_horizon_seconds=3600,
        max_fill_wait_bars_15m=1,
        max_fill_wait_seconds=900,
        dataset_hash=EMPTY_DATASET_HASH,
        code_revision="HEAD",
        signal_profile=uncalibrated_xauusd_signal_profile(),
        risk_profile=uncalibrated_xauusd_risk_profile(),
    )

    runner = XauUsdBacktestRunner()
    with pytest.raises(ValueError) as exc_info:
        runner.run_point_in_time(empty_ds, spec)

    assert "cannot be calibrated" in str(exc_info.value)
