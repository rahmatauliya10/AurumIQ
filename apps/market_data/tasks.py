"""Celery asynchronous tasks for candle ingestion and provider health monitoring."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from celery import shared_task
from django.db import transaction
import structlog
from apps.instruments.models import (
    Instrument,
    InstrumentType,
    MarketListing,
    ProviderHealthSnapshot,
    ProviderHealthStatus,
    ListingStatus,
    ListingRole,
)
from apps.market_data.models import MarketCandle, DataQualitySnapshot, CandleQualityFlag
from apps.market_data.providers.registry import registry
from apps.market_data.normalization import QuoteNormalizer
from apps.market_data.integrity import MarketIntegrityEngine

logger = structlog.get_logger(__name__)


def _get_setting(key: str, default=None):
    try:
        from django.conf import settings
        return getattr(settings, key, default)
    except Exception:
        return default


@shared_task(queue="market_data")
def ingest_primary_candles(
    instrument_symbol: str = "XAUT/USDT",
    timeframes: list[str] = None,
    lookback_bars: int = 50,
    xauusd_max_divergence_pct: Optional[Decimal] = None,
    is_secondary_critical: bool = True,
) -> dict:
    """
    Ingest primary closed candles (15m, 1h, 4h, 1d) with quote normalization
    and integrated multi-source provider integrity evaluation (XAU-P1-02).
    """
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

    # PATCH B: Separate is_direct_usd (pricing normalization) from is_xauusd (feed integrity / source role)
    is_direct_usd = (instrument.quote_asset.code == "USD")
    is_xauusd = (
        instrument.base_asset.code == "XAU"
        and instrument.quote_asset.code == "USD"
        and instrument.instrument_type == InstrumentType.SPOT
    )
    now_utc = datetime.now(timezone.utc)

    # 1. Deterministic Listing Resolution (PATCH A)
    if is_xauusd:
        listing = MarketListing.objects.filter(
            instrument=instrument,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        ).first()
        sec_listing = MarketListing.objects.filter(
            instrument=instrument,
            listing_role=ListingRole.SECONDARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        ).first()

        if not listing:
            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=timeframes[0] if timeframes else "15m",
                timestamp=now_utc,
                quality_score=Decimal("0.00"),
                gap_count=0,
                duplicate_count=0,
                violation_count=1,
                is_stale=True,
                hard_fail=True,
                anomalies={"error": f"PRIMARY_XAUUSD_LISTING_NOT_CONFIGURED: No active listing with role PRIMARY_XAUUSD_SPOT for {instrument_symbol}."},
            )
            return {
                "status": "hard_fail",
                "reason": "PRIMARY_XAUUSD_LISTING_NOT_CONFIGURED",
                "instrument": instrument_symbol,
                "candles_ingested": 0,
            }

        if is_secondary_critical and not sec_listing:
            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=timeframes[0] if timeframes else "15m",
                timestamp=now_utc,
                quality_score=Decimal("0.00"),
                gap_count=0,
                duplicate_count=0,
                violation_count=1,
                is_stale=True,
                hard_fail=True,
                anomalies={"error": f"SECONDARY_XAUUSD_LISTING_NOT_CONFIGURED: Critical secondary listing SECONDARY_XAUUSD_SPOT is missing for {instrument_symbol}."},
            )
            return {
                "status": "hard_fail",
                "reason": "SECONDARY_XAUUSD_LISTING_NOT_CONFIGURED",
                "instrument": instrument_symbol,
                "candles_ingested": 0,
            }
    else:
        # Non-XAUUSD instruments (e.g. legacy XAUT/USDT or generic USD instruments)
        listing = (
            MarketListing.objects.filter(
                instrument=instrument,
                listing_role=ListingRole.LEGACY_EXECUTION,
                status=ListingStatus.ACTIVE,
            ).order_by("fallback_priority").first()
            or MarketListing.objects.filter(
                instrument=instrument,
                status=ListingStatus.ACTIVE,
            ).order_by("fallback_priority").first()
        )
        sec_listing = None
        if not listing:
            return {"status": "error", "message": f"No active listing for {instrument_symbol}."}

    # Provider Resolution & Health Check (PATCH C)
    provider = registry.get(listing.provider)
    if is_xauusd:
        if not provider or not provider.is_configured():
            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=timeframes[0] if timeframes else "15m",
                timestamp=now_utc,
                quality_score=Decimal("0.00"),
                gap_count=0,
                duplicate_count=0,
                violation_count=1,
                is_stale=True,
                hard_fail=True,
                anomalies={"error": "PRIMARY_XAUUSD_NOT_CONFIGURED: Primary spot provider is not configured (fail-closed)."},
            )
            return {
                "status": "hard_fail",
                "reason": "PRIMARY_XAUUSD_NOT_CONFIGURED",
                "instrument": instrument_symbol,
                "candles_ingested": 0,
            }

        health = provider.health_check()
        if health.status in [
            ProviderHealthStatus.NOT_CONFIGURED,
            ProviderHealthStatus.UNHEALTHY,
            ProviderHealthStatus.QUARANTINED,
            ProviderHealthStatus.UNKNOWN,
            ProviderHealthStatus.DEGRADED,
        ]:
            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=timeframes[0] if timeframes else "15m",
                timestamp=now_utc,
                quality_score=Decimal("0.00"),
                gap_count=0,
                duplicate_count=0,
                violation_count=1,
                is_stale=True,
                hard_fail=True,
                anomalies={"error": f"PRIMARY_XAUUSD_HEALTH_{health.status}: Primary spot provider health status is {health.status} ({health.error_message or 'No details'})."},
            )
            return {
                "status": "hard_fail",
                "reason": f"PRIMARY_XAUUSD_HEALTH_{health.status}",
                "instrument": instrument_symbol,
                "candles_ingested": 0,
            }
    else:
        if not provider:
            return {"status": "error", "message": f"Provider {listing.provider} not found in registry."}

    # PATCH E: XAUUSD Divergence Threshold Check (Fail-closed if threshold is None)
    configured_threshold = xauusd_max_divergence_pct or _get_setting("XAUUSD_MAX_DIVERGENCE_PCT", None)
    if is_xauusd and configured_threshold is None:
        DataQualitySnapshot.objects.create(
            instrument=instrument,
            timeframe=timeframes[0] if timeframes else "15m",
            timestamp=now_utc,
            quality_score=Decimal("0.00"),
            gap_count=0,
            duplicate_count=0,
            violation_count=1,
            is_stale=True,
            hard_fail=True,
            anomalies={"error": "INTEGRITY_THRESHOLD_NOT_CONFIGURED: XAUUSD integrity divergence threshold is not configured."},
        )
        return {
            "status": "hard_fail",
            "reason": "INTEGRITY_THRESHOLD_NOT_CONFIGURED",
            "instrument": instrument_symbol,
            "candles_ingested": 0,
        }

    usdt_rate_provider = registry.get("usdt_usd") if not is_direct_usd else None
    current_usdt_rate = getattr(usdt_rate_provider, "get_current_rate", lambda: None)() if usdt_rate_provider else None

    normalizer = QuoteNormalizer()
    integrity_engine = MarketIntegrityEngine()
    total_ingested = 0
    task_hard_fail = False
    task_hard_fail_reasons = []

    for tf in timeframes:
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

        # 2. Primary Provider Fetch
        try:
            raw_candles = provider.fetch_candles(
                symbol=listing.provider_symbol,
                timeframe=tf,
                start=start_time,
                end=now_utc,
            )
        except Exception as e:
            logger.error("primary_ingestion_fetch_error", provider=listing.provider, tf=tf, error=str(e))
            if is_xauusd:
                DataQualitySnapshot.objects.create(
                    instrument=instrument,
                    timeframe=tf,
                    timestamp=now_utc,
                    quality_score=Decimal("0.00"),
                    gap_count=0,
                    duplicate_count=0,
                    violation_count=1,
                    is_stale=True,
                    hard_fail=True,
                    anomalies={"error": f"PRIMARY_XAUUSD_FETCH_ERROR: {e}"},
                )
                task_hard_fail = True
                task_hard_fail_reasons.append(f"PRIMARY_XAUUSD_FETCH_ERROR_{tf}: {e}")
            continue

        # PATCH D: Empty Primary Data Fail Closed for XAUUSD
        usable_closed_candles = [c for c in raw_candles if c.is_closed]
        if is_xauusd and len(usable_closed_candles) == 0:
            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=tf,
                timestamp=now_utc,
                quality_score=Decimal("0.00"),
                gap_count=0,
                duplicate_count=0,
                violation_count=1,
                is_stale=True,
                hard_fail=True,
                anomalies={"error": f"PRIMARY_XAUUSD_NO_USABLE_CLOSED_DATA: Zero usable closed candles returned for {tf}."},
            )
            task_hard_fail = True
            task_hard_fail_reasons.append(f"PRIMARY_XAUUSD_NO_USABLE_CLOSED_DATA_{tf}")
            continue

        # 3. Secondary Provider Fetch (for XAUUSD integrity verification XAU-P1-02)
        sec_lookup = {}
        sec_fetch_error = None
        if is_xauusd and sec_listing:
            sec_provider = registry.get(sec_listing.provider)
            if not sec_provider or not sec_provider.is_configured():
                sec_fetch_error = "SECONDARY_XAUUSD_NOT_CONFIGURED"
                if is_secondary_critical:
                    task_hard_fail = True
                    task_hard_fail_reasons.append(f"SECONDARY_XAUUSD_UNAVAILABLE_{tf}")
            else:
                try:
                    sec_candles = sec_provider.fetch_candles(
                        symbol=sec_listing.provider_symbol,
                        timeframe=tf,
                        start=start_time,
                        end=now_utc,
                    )
                    sec_lookup = {c.timestamp_open: c.close for c in sec_candles if c.is_closed}
                    if is_secondary_critical and len(sec_lookup) == 0 and len(usable_closed_candles) > 0:
                        sec_fetch_error = "SECONDARY_XAUUSD_NO_DATA"
                        task_hard_fail = True
                        task_hard_fail_reasons.append(f"SECONDARY_XAUUSD_NO_DATA_{tf}")
                except Exception as e:
                    sec_fetch_error = str(e)
                    logger.warning("secondary_xauusd_fetch_error", provider=sec_listing.provider, error=str(e))
                    if is_secondary_critical:
                        task_hard_fail = True
                        task_hard_fail_reasons.append(f"SECONDARY_XAUUSD_FETCH_ERROR_{tf}: {e}")

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
        integrity_failed = False
        anomalies_dict = {}

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

                    if is_xauusd:
                        # Integrated Multi-Source Provider Integrity Check (XAU-P1-02)
                        sec_price = sec_lookup.get(raw.timestamp_open)
                        integrity_res = integrity_engine.verify_xauusd_multi_provider_integrity(
                            primary_price=raw.close,
                            secondary_price=sec_price,
                            max_divergence_pct=configured_threshold,
                            is_secondary_critical=is_secondary_critical,
                        )
                        if integrity_res.hard_fail:
                            integrity_failed = True
                            violations += 1
                            quality_flag = CandleQualityFlag.SUSPECT
                            anomalies_dict["integrity_disagreement"] = integrity_res.message
                            task_hard_fail = True
                            if integrity_res.message not in task_hard_fail_reasons:
                                task_hard_fail_reasons.append(integrity_res.message)
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
            if is_xauusd:
                anomalies_dict.update({
                    "gap_detected": gap_count > 0,
                    "secondary_fetch_error": sec_fetch_error,
                })
                dq_hard_fail = violations > 0 or integrity_failed or (is_secondary_critical and sec_fetch_error is not None)
                score = Decimal("100.00") if not dq_hard_fail and gap_count == 0 else Decimal("0.00") if dq_hard_fail else Decimal("50.00")
            elif is_direct_usd:
                anomalies_dict.update({
                    "gap_detected": gap_count > 0,
                })
                dq_hard_fail = violations > 5
                score = Decimal("100.00") if violations == 0 and gap_count == 0 else Decimal("50.00")
            else:
                anomalies_dict.update({
                    "peg_warning": norm_res.is_warning,
                    "rate_missing": current_usdt_rate is None,
                    "gap_detected": gap_count > 0,
                })
                dq_hard_fail = violations > 5 or norm_res.hard_fail
                score = Decimal("100.00") if violations == 0 and not norm_res.hard_fail and gap_count == 0 else Decimal("50.00")

            DataQualitySnapshot.objects.create(
                instrument=instrument,
                timeframe=tf,
                timestamp=now_utc,
                quality_score=score,
                gap_count=gap_count,
                duplicate_count=0,
                violation_count=violations,
                is_stale=norm_res.is_stale if hasattr(norm_res, 'is_stale') else False,
                hard_fail=dq_hard_fail,
                anomalies=anomalies_dict,
            )
            if dq_hard_fail and is_xauusd:
                task_hard_fail = True

    # PATCH E: Propagate Integrity Failure to Task Result
    if is_xauusd and task_hard_fail:
        return {
            "status": "hard_fail",
            "reason": "; ".join(task_hard_fail_reasons) if task_hard_fail_reasons else "XAUUSD_INTEGRITY_OR_QUALITY_FAILURE",
            "instrument": instrument_symbol,
            "provider": listing.provider,
            "candles_ingested": total_ingested,
        }

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
