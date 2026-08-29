"""End-to-end integration test: raw provider fetch -> validation -> normalization -> persistence -> repository loading."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
import pytest
from django.core.management import call_command
from engine.core.types import CandleData
from apps.instruments.models import Instrument, MarketListing
from apps.market_data.models import MarketCandle, DataQualitySnapshot
from apps.market_data.tasks import ingest_primary_candles
from apps.market_data.repositories import DjangoCandleRepository


@pytest.mark.integration
@pytest.mark.django_db
def test_end_to_end_ingestion_and_repository_pipeline():
    """
    End-to-End Ingestion Integration Test:
    1. Seed baseline instruments (XAUT/USDT, etc.)
    2. Mock Binance public klines response (including closed & open bars) and USDT/USD rate
    3. Run Celery ingestion task (ingest_primary_candles)
    4. Verify MarketCandle and DataQualitySnapshot in DB
    5. Load causal analysis window via pure DjangoCandleRepository
    6. Assert returned CandleData is closed, point-in-time normalized, and perfectly ordered.
    """
    # 1. Seed instruments
    call_command("seed_instruments")
    inst = Instrument.objects.get(base_asset__code="XAUT", quote_asset__code="USDT")
    assert inst is not None

    # 3 closed bars in past, 1 open bar ending in future
    now_real = datetime.now(timezone.utc)
    t0 = now_real - timedelta(minutes=45)
    t1 = now_real - timedelta(minutes=30)
    t2 = now_real - timedelta(minutes=15)
    t3 = now_real
    t4 = now_real + timedelta(minutes=15)

    mock_klines = [
        # Bar 1 (closed)
        [int(t0.timestamp() * 1000), "2500.00", "2505.00", "2498.00", "2502.00", "10.0", int(t1.timestamp() * 1000) - 1, "0", 1, "0", "0", "0"],
        # Bar 2 (closed)
        [int(t1.timestamp() * 1000), "2502.00", "2508.00", "2501.00", "2506.00", "15.0", int(t2.timestamp() * 1000) - 1, "0", 1, "0", "0", "0"],
        # Bar 3 (closed)
        [int(t2.timestamp() * 1000), "2506.00", "2512.00", "2504.00", "2510.00", "20.0", int(t3.timestamp() * 1000) - 1, "0", 1, "0", "0", "0"],
        # Bar 4 (forming / open bar: close time is in future)
        [int(t3.timestamp() * 1000), "2510.00", "2515.00", "2509.00", "2514.00", "5.0", int(t4.timestamp() * 1000) - 1, "0", 1, "0", "0", "0"],
    ]

    # Mock Binance klines and USDCUSDT rate (0.999800)
    with patch("requests.get") as mock_get:
        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "klines" in url:
                resp.json.return_value = mock_klines
            elif "ticker/price" in url:
                # 1 USDC = 1.00020004 USDT -> rate = 0.999800
                resp.json.return_value = {"price": "1.00020004"}
            return resp

        mock_get.side_effect = side_effect

        # 3. Execute ingestion task
        result = ingest_primary_candles(
            instrument_symbol="XAUT/USDT",
            timeframes=["15m"],
            lookback_bars=10,
        )

        assert result["status"] == "success"
        assert result["candles_ingested"] == 4

    # 4. Verify Database Records
    candles_in_db = MarketCandle.objects.filter(instrument=inst, timeframe="15m").order_by("timestamp_open")
    assert candles_in_db.count() == 4
    
    # 3 closed, 1 unclosed
    assert candles_in_db.filter(is_closed=True).count() == 3
    assert candles_in_db.filter(is_closed=False).count() == 1

    # Verify quote normalization in DB
    bar1 = candles_in_db.first()
    expected_bar1_usd = (Decimal("2502.00") * Decimal("0.999800")).quantize(Decimal("0.00000001"))
    assert bar1.close_usd == expected_bar1_usd

    # Verify DataQualitySnapshot created
    snapshot = DataQualitySnapshot.objects.filter(instrument=inst, timeframe="15m").first()
    assert snapshot is not None
    assert snapshot.hard_fail is False
    assert snapshot.quality_score == Decimal("100.00")

    # 5. Load through decoupled pure CandleRepository
    repo = DjangoCandleRepository()
    window = repo.load_window(instrument="XAUT/USDT", timeframe="15m", end_at=t3 + timedelta(minutes=15), bars=10)

    # 6. Assert pure CandleData
    assert len(window) == 3  # Unclosed bar strictly excluded!
    for c in window:
        assert isinstance(c, CandleData)
        assert c.is_closed is True
        assert c.source_id == "binance"
        assert c.close_usd is not None

    # Assert correct bar ordering and values
    assert window[0].close == Decimal("2502.00")
    assert window[1].close == Decimal("2506.00")
    assert window[2].close == Decimal("2510.00")
    assert window[2].close_usd == (Decimal("2510.00") * Decimal("0.999800")).quantize(Decimal("0.00000001"))
