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

    provider = registry.get(listing.provider)
    usdt_rate_provider = registry.get("usdt_usd")
    
    # Never fallback to 1.0 (P1-09)
    current_usdt_rate = getattr(usdt_rate_provider, "get_current_rate", lambda: None)()

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

        violations = 0
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

                candle_norm = normalizer.normalize_price(raw.close, current_usdt_rate)

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
                        "quote_rate": candle_norm.rate,
                        "close_usd": candle_norm.normalized_price,
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
                quality_score=Decimal("100.00") if violations == 0 and not norm_res.hard_fail else Decimal("50.00"),
                gap_count=0,
                duplicate_count=0,
                violation_count=violations,
                is_stale=norm_res.is_stale,
                hard_fail=violations > 5 or norm_res.hard_fail,
                anomalies={"peg_warning": norm_res.is_warning, "rate_missing": current_usdt_rate is None},
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
