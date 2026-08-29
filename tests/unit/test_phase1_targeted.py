"""Targeted verification tests (P1-01 to P1-09) for Phase 1 Stop & Review Gate."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
import pytest
from apps.instruments.models import Asset, Instrument, InstrumentRole, InstrumentType, MarketListing, ListingStatus
from apps.market_data.models import MarketCandle, CandleQualityFlag
from apps.market_data.providers.binance import BinanceProvider
from apps.market_data.providers.okx import OKXProvider
from apps.market_data.providers.gold_reference import GoldReferenceProvider, PaxgConfirmationProvider
from apps.market_data.providers.usdt_usd import UsdtUsdRateProvider
from apps.market_data.normalization import QuoteNormalizer
from apps.market_data.integrity import MarketIntegrityEngine, ProviderContinuityVerifier
from apps.market_data.repositories import DjangoCandleRepository


# --- P1-01: OKX confirm=0 -> candle not closed ---
@pytest.mark.unit
def test_p1_01_okx_unconfirmed_candle_not_marked_closed():
    """P1-01: Verify that OKX candles with confirm='0' are flagged as is_closed=False."""
    provider = OKXProvider()
    mock_unconfirmed_candle = [
        "1724900000000", "2510.00", "2520.00", "2505.00", "2518.00",
        "50.5", "127000.0", "127000.0", "0"  # confirm = "0" (incomplete)
    ]

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": "0", "data": [mock_unconfirmed_candle]}
        mock_get.return_value = mock_resp

        start = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
        candles = provider.fetch_candles("XAUT-USDT", "15m", start, end)

        assert len(candles) == 1
        assert candles[0].is_closed is False  # Incomplete candle strictly NOT closed


# --- P1-02: Open candle excluded from analysis window ---
@pytest.mark.unit
@pytest.mark.django_db
def test_p1_02_open_candle_excluded_from_analysis_window():
    """P1-02: Verify that repository.load_window strictly excludes unclosed/open candles."""
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    t0 = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=15)
    t2 = t1 + timedelta(minutes=15)

    # Closed candle 1
    MarketCandle.objects.create(
        instrument=inst, source="binance", timeframe="15m",
        timestamp_open=t0, timestamp_close=t1,
        open=Decimal("2500"), high=Decimal("2505"), low=Decimal("2495"), close=Decimal("2502"),
        volume=Decimal("10"), is_closed=True,
    )
    # Open / Incomplete candle 2
    MarketCandle.objects.create(
        instrument=inst, source="binance", timeframe="15m",
        timestamp_open=t1, timestamp_close=t2,
        open=Decimal("2502"), high=Decimal("2510"), low=Decimal("2500"), close=Decimal("2508"),
        volume=Decimal("5"), is_closed=False,  # Still forming!
    )

    repo = DjangoCandleRepository()
    window = repo.load_window("XAUT/USDT", "15m", end_at=t2, bars=10)

    # Only closed candle should be present
    assert len(window) == 1
    assert window[0].timestamp_open == t0
    assert window[0].is_closed is True


# --- P1-03: USDT/USD normalization uses rate timestamp <= candle timestamp ---
@pytest.mark.unit
def test_p1_03_usdt_normalization_point_in_time_alignment():
    """
    P1-03 / A21-PIT: XAUT candle at 10:15 must select latest rate <= 10:15 (e.g. 10:14),
    and NEVER use future rate at 10:16.
    """
    normalizer = QuoteNormalizer(max_staleness_seconds=3600)
    candle_time = datetime(2026, 8, 29, 10, 15, tzinfo=timezone.utc)

    rate_history = [
        (datetime(2026, 8, 29, 10, 10, tzinfo=timezone.utc), Decimal("0.999000")),
        (datetime(2026, 8, 29, 10, 14, tzinfo=timezone.utc), Decimal("0.999500")),  # Expected PIT rate
        (datetime(2026, 8, 29, 10, 16, tzinfo=timezone.utc), Decimal("0.998000")),  # Future rate (lookahead!)
    ]

    result = normalizer.normalize_price_pit(
        raw_price_usdt=Decimal("2500.00"),
        candle_timestamp=candle_time,
        rate_history=rate_history,
    )

    # Must select 10:14 rate (0.9995), NOT 10:16 rate (0.9980)
    assert result.rate == Decimal("0.999500")
    assert result.rate_timestamp == datetime(2026, 8, 29, 10, 14, tzinfo=timezone.utc)
    assert result.normalized_price == Decimal("2498.75000000")  # 2500 * 0.9995
    assert result.is_stale is False
    assert result.hard_fail is False


# --- P1-04: Stale USDT/USD rate -> quality failure / downgrade ---
@pytest.mark.unit
def test_p1_04_stale_usdt_rate_triggers_warning_and_hard_fail():
    """P1-04: Verify that stale rate (> 1h) warns and severely stale rate (> 24h) hard fails."""
    normalizer = QuoteNormalizer(max_staleness_seconds=3600)
    candle_time = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

    # Case A: 2 hours old -> Stale Warning (quality downgrade)
    rate_2h_old = [
        (datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc), Decimal("1.000000")),
    ]
    res_a = normalizer.normalize_price_pit(Decimal("2500"), candle_time, rate_2h_old)
    assert res_a.is_stale is True
    assert res_a.is_warning is True
    assert res_a.hard_fail is False
    assert "STALE" in res_a.message

    # Case B: 30 hours old -> Critical Hard Fail
    rate_30h_old = [
        (datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc), Decimal("1.000000")),
    ]
    res_b = normalizer.normalize_price_pit(Decimal("2500"), candle_time, rate_30h_old)
    assert res_b.is_stale is True
    assert res_b.hard_fail is True
    assert "CRITICAL" in res_b.message


# --- P1-05: Two-provider disagreement -> FORCE_WAIT, not arbitrary quarantine ---
@pytest.mark.unit
def test_p1_05_two_provider_disagreement_forces_wait():
    """
    P1-05: With exactly 2 sources diverging > 0.50%, do NOT arbitrarily quarantine one;
    enforce TWO_SOURCE_DISAGREEMENT and force wait until consensus is available.
    """
    engine = MarketIntegrityEngine(outlier_threshold_pct=Decimal("0.0050"))

    # Binance = 2500.00, OKX = 2525.00 (Divergence = 25 / 2500 = 1.00% > 0.50%)
    two_sources = {
        "binance": Decimal("2500.00"),
        "okx": Decimal("2525.00"),
    }

    result = engine.evaluate_provider_outliers(two_sources)

    assert result.is_two_source_disagreement is True
    assert result.force_wait is True
    # Zero arbitrarily quarantined providers
    assert len(result.quarantined_providers) == 0
    assert len(result.valid_providers) == 0
    assert "DISAGREEMENT" in result.message


# --- P1-06: A20 transition requires secondary consensus ---
@pytest.mark.unit
def test_p1_06_a20_transition_requires_secondary_consensus():
    """
    P1-06: Verify 5th criterion: If secondary source consensus disagrees with new provider,
    transition is rejected and FORCE_WAIT is enforced.
    """
    verifier = ProviderContinuityVerifier()

    # OKX = 2500.00, Secondary reference = 2450.00 (2.04% divergence > 0.35%)
    res_rejected = verifier.verify_transition(
        old_provider_price=Decimal("2498.00"),
        new_provider_price=Decimal("2500.00"),
        consecutive_healthy_candles=3,
        bid=Decimal("2499.50"),
        ask=Decimal("2500.50"),
        has_bad_ticks=False,
        secondary_reference_price=Decimal("2450.00"),  # Disagreement!
        is_source_switch=False,
    )
    assert res_rejected.secondary_consensus_passed is False
    assert res_rejected.force_wait is True
    assert res_rejected.is_verified is False
    assert any("Secondary reference consensus divergence" in r for r in res_rejected.reasons)

    # OKX = 2500.00, Secondary reference = 2502.00 (0.08% divergence <= 0.35%) -> Approved!
    res_approved = verifier.verify_transition(
        old_provider_price=Decimal("2498.00"),
        new_provider_price=Decimal("2500.00"),
        consecutive_healthy_candles=3,
        bid=Decimal("2499.50"),
        ask=Decimal("2500.50"),
        has_bad_ticks=False,
        secondary_reference_price=Decimal("2502.00"),  # Agrees!
        is_source_switch=False,
    )
    assert res_approved.secondary_consensus_passed is True
    assert res_approved.force_wait is False
    assert res_approved.is_verified is True


# --- P1-07: Provider symbol unavailable/HALT -> provider cannot be selected healthy ---
@pytest.mark.unit
def test_p1_07_provider_symbol_halt_cannot_be_healthy():
    """P1-07: Verify that exchangeInfo status HALT/BREAK downgrades health check."""
    binance = BinanceProvider()

    with patch("requests.get") as mock_get:
        # Mock ping 200 OK, but exchangeInfo status is HALT
        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "ping" in url:
                resp.json.return_value = {}
            elif "exchangeInfo" in url:
                resp.json.return_value = {
                    "symbols": [{"symbol": "XAUTUSDT", "status": "HALT"}]
                }
            return resp

        mock_get.side_effect = side_effect
        health = binance.health_check()

        assert health.status == "DEGRADED"
        assert "HALT" in health.error_message


# --- P1-08A: PAXG cannot be registered as canonical GOLD_REFERENCE ---
@pytest.mark.unit
def test_p1_08a_paxg_cannot_be_canonical_gold_reference():
    """P1-08A: PAXG is strictly a secondary confirmation proxy, not canonical XAU/USD."""
    paxg_provider = PaxgConfirmationProvider()
    assert paxg_provider.provider_id == "paxg_confirmation"
    assert paxg_provider.provider_id != "gold_reference"

    gold_provider = GoldReferenceProvider(canonical_url=None)
    assert gold_provider.is_configured() is False
    health = gold_provider.health_check()
    assert health.status == "NOT_CONFIGURED"

    # Attempting to fetch canonical candles without canonical source must raise error
    with pytest.raises(RuntimeError, match="proxy substitution is strictly prohibited"):
        gold_provider.fetch_candles("XAU/USD", "15m", datetime.now(timezone.utc), datetime.now(timezone.utc))


# --- P1-08B: Missing XAU/USD reference -> integrity incomplete / BUY gate blocked ---
@pytest.mark.unit
def test_p1_08b_missing_xau_reference_blocks_buy_gate():
    """P1-08B: Missing canonical spot XAU/USD price triggers hard fail in integrity engine."""
    engine = MarketIntegrityEngine()

    result = engine.verify_xaut_xau_basis(
        xaut_usd_price=Decimal("2500.00"),
        xau_usd_price=None,  # Canonical reference unavailable
    )

    assert result.is_valid is False
    assert result.hard_fail is True
    assert "GOLD_REFERENCE_UNAVAILABLE" in result.message


# --- P1-09A: Missing USDT normalization feed never defaults to 1.0 ---
@pytest.mark.unit
def test_p1_09a_missing_usdt_rate_never_defaults_to_one():
    """P1-09A: Missing quote normalization rate returns None and activates hard fail."""
    normalizer = QuoteNormalizer()

    # Pass None as rate
    res = normalizer.normalize_price(raw_price_usdt=Decimal("2500.00"), usdt_usd_rate=None)

    assert res.rate is None
    assert res.normalized_price is None
    assert res.hard_fail is True
    assert "Never default to 1.0" in res.message


# --- P1-09B: Historical normalization cannot use current ticker as historical rate ---
@pytest.mark.unit
def test_p1_09b_historical_normalization_requires_historical_rate():
    """
    P1-09B: When normalizing historical candle from 10 days ago, if only current rate
    is provided (rate timestamp in future relative to candle), normalizer must reject lookahead.
    """
    normalizer = QuoteNormalizer()
    historical_candle_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    current_live_time = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)

    # Only current live rate available (no historical rates on 2026-08-01)
    rate_history = [
        (current_live_time, Decimal("0.999800")),
    ]

    res = normalizer.normalize_price_pit(
        raw_price_usdt=Decimal("2500.00"),
        candle_timestamp=historical_candle_time,
        rate_history=rate_history,
    )

    # Must reject live rate because rate_timestamp (Aug 29) > candle_timestamp (Aug 1)
    assert res.rate is None
    assert res.normalized_price is None
    assert res.hard_fail is True
    assert "No historical USDT/USD rate available" in res.message
