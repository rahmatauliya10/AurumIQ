"""Celery asynchronous tasks for candle ingestion and provider health monitoring."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from celery import shared_task
from django.db import transaction
import structlog
from apps.instruments.models import Instrument, MarketListing, ProviderHealthSnapshot, ListingStatus
from apps.market_data.models import MarketCandle, DataQualitySnapshot, CandleQualityFlag
from apps.market_data.providers.registry import registry
from apps.market_data.normalization import QuoteNormalizer
from apps.market_data.integrity import MarketIntegrityEngine

logger = structlog.get_logger(__name__)


@shared_task(queue="market_data")
def ingest_primary_candles(
    instrument_symbol: str = "XAUT/USDT",
    timeframes: list[str] = None,
    lookback_bars: int = 50,
) -> dict:
    """Ingest primary closed candles (15m, 1h, 4h, 1d) with quote normalization."""
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]

    parts = instrument_symbol.split("/")
    if len(parts) != 2:
        return {"status": "error", "message": f"Invalid symbol format: {instrument_symbol}"}

    instrument = Instrument.objects.filter(
        base_asset__code=parts[0], quote_asset__code=parts[1]
    ).first()
    if not instrument:
        return {"status": "error", "message": f"Instrument {instrument_symbol} not found."}

    # Fetch active market listing
    listing = (
        MarketListing.objects.filter(instrument=instrument, status=ListingStatus.ACTIVE)
        .order_by("fallback_priority")
        .first()
    )
    if not listing:
        return {"status": "error", "message": f"No active listing for {instrument_symbol}."}

    is_direct_usd = (instrument.quote_asset.code == "USD")
    provider = registry.get(listing.provider)
    usdt_rate_provider = registry.get("usdt_usd") if not is_direct_usd else None
    
    # Base fallback rate if historical query empty (legacy XAUT only)
    current_usdt_rate = getattr(usdt_rate_provider, "get_current_rate", lambda: None)() if usdt_rate_provider else None

    normalizer = QuoteNormalizer()
    integrity_engine = MarketIntegrityEngine()
    total_ingested = 0

    now_utc = datetime.now(timezone.utc)

    for tf in timeframes:
        # Approximate lookback window
        minutes_per_bar = {
            "15m": 15,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
        }.get(tf, 15)
        start_time = now_utc - timedelta(minutes=minutes_per_bar * lookback_bars)

        # Retrieve historical USDT rates for PIT normalization (legacy XAUT only)
        hist_usdt_rates = []
        if not is_direct_usd and usdt_rate_provider and hasattr(usdt_rate_provider, "fetch_historical_rates"):
            try:
                hist_usdt_rates = usdt_rate_provider.fetch_historical_rates(start=start_time, end=now_utc)
            except Exception as e:
                logger.warning("usdt_historical_rate_fetch_failed", error=str(e))

        try:
            raw_candles = provider.fetch_candles(
                symbol=listing.provider_symbol,
                timeframe=tf,
                start=start_time,
                end=now_utc,
            )
        except Exception as e:
            logger.error("primary_ingestion_fetch_error", provider=listing.provider, tf=tf, error=str(e))
            continue

        # Sort chronologically for deterministic gap & normalization analysis
        raw_candles = sorted(raw_candles, key=lambda c: c.timestamp_open)

        # Detect actual intervals and gap count
        gap_count = 0
        expected_delta = timedelta(minutes=minutes_per_bar)
        for i in range(1, len(raw_candles)):
            delta = raw_candles[i].timestamp_open - raw_candles[i-1].timestamp_open
            if delta > expected_delta:
                missing_bars = int(delta.total_seconds() // expected_delta.total_seconds()) - 1
                gap_count += max(0, missing_bars)

        violations = 0
        if is_direct_usd:
            norm_res = normalizer.normalize_direct_usd(Decimal("1.0"), now_utc)
        else:
            norm_res = normalizer.normalize_price(Decimal("1.0"), current_usdt_rate)

        with transaction.atomic():
            for raw in raw_candles:
                # OHLC logical validation
                is_valid_ohlc, ohlc_errs = integrity_engine.validate_candle_ohlc(
                    raw.open, raw.high, raw.low, raw.close, raw.volume
                )
                if not is_valid_ohlc:
                    violations += 1
                    quality_flag = CandleQualityFlag.SUSPECT
                else:
                    quality_flag = CandleQualityFlag.OK

                if is_direct_usd:
                    candle_norm = normalizer.normalize_direct_usd(raw.close, raw.timestamp_close)
                    norm_quote_rate = candle_norm.rate
                    norm_close_usd = candle_norm.normalized_price
                else:
                    # Point-in-time rate matching: find latest historical rate <= candle close timestamp
                    candle_rate = None
                    if hist_usdt_rates:
                        matching_rates = [
                            r[1] for r in hist_usdt_rates
                            if r[0] <= raw.timestamp_close
                        ]
                        if matching_rates:
                            candle_rate = matching_rates[-1]
                    elif not raw.is_closed:
                        # Forming / open bar only may use current ticker rate
                        candle_rate = current_usdt_rate

                    if candle_rate is not None:
                        candle_norm = normalizer.normalize_price(raw.close, candle_rate)
                        norm_quote_rate = candle_norm.rate
                        norm_close_usd = candle_norm.normalized_price
                    else:
                        norm_quote_rate = None
                        norm_close_usd = None
                        quality_flag = CandleQualityFlag.SUSPECT
                        violations += 1

                vol_evidence = getattr(raw, "volume_evidence", "UNAVAILABLE")

                MarketCandle.objects.update_or_create(
                    instrument=instrument,
                    source=listing.provider,
                    timeframe=tf,
                    timestamp_open=raw.timestamp_open,
                    defaults={
                        "timestamp_close": raw.timestamp_close,
                        "open": raw.open,
                        "high": raw.high,
                        "low": raw.low,
                        "close": raw.close,
                        "volume": raw.volume,
                        "volume_evidence": vol_evidence,
                        "quote_rate": norm_quote_rate,
                        "close_usd": norm_close_usd,
                        "is_closed": raw.is_closed,
                        "data_quality_flag": quality_flag,
                    },
                )
                total_ingested += 1

            # Point-in-time DataQualitySnapshot
            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=tf,
                timestamp=now_utc,
                quality_score=Decimal("100.00") if violations == 0 and not norm_res.hard_fail and gap_count == 0 else Decimal("50.00"),
                gap_count=gap_count,
                duplicate_count=0,
                violation_count=violations,
                is_stale=norm_res.is_stale,
                hard_fail=violations > 5 or norm_res.hard_fail,
                anomalies={"peg_warning": norm_res.is_warning, "rate_missing": current_usdt_rate is None, "gap_detected": gap_count > 0},
            )

    return {
        "status": "success",
        "instrument": instrument_symbol,
        "provider": listing.provider,
        "candles_ingested": total_ingested,
    }


@shared_task(queue="market_data")
def ingest_resolution_candles(
    instrument_symbol: str = "XAUT/USDT",
    timeframes: list[str] = None,
    lookback_bars: int = 60,
) -> dict:
    """Ingest lower-timeframe resolution candles (1m, 5m) for backtest simulator."""
    if timeframes is None:
        timeframes = ["1m", "5m"]
    return ingest_primary_candles(
        instrument_symbol=instrument_symbol,
        timeframes=timeframes,
        lookback_bars=lookback_bars,
    )


@shared_task(queue="maintenance")
def check_provider_health_task() -> dict:
    """Periodic health probe for all registered providers."""
    now = datetime.now(timezone.utc)
    results = {}

    for provider in registry.all_providers():
        health = provider.health_check()
        results[provider.provider_id] = health.status

        # Find matching listings to associate snapshot
        listings = MarketListing.objects.filter(provider=provider.provider_id)
        for listing in listings:
            ProviderHealthSnapshot.objects.create(
                listing=listing,
                status=health.status,
                checked_at=now,
                latency_ms=health.latency_ms,
                reason=health.error_message,
            )

    return {"status": "success", "checked_at": now.isoformat(), "providers": results}
