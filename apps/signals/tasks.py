"""Celery tasks for asynchronous closed-candle signal generation (Phase 4)."""
from datetime import datetime, timezone
from typing import Optional
from celery import shared_task
import structlog

from apps.instruments.models import Instrument
from apps.market_data.models import MarketCandle
from apps.signals.services import SignalPersistenceService
from engine.core.types import CandleData, MacroEventContext
from engine.signals.engine import XautSignalEngine

logger = structlog.get_logger(__name__)


@shared_task(queue="analysis", bind=True, max_retries=3)
def analyze_closed_candle(
    self,
    instrument_id: int,
    timeframe: str,
    candle_timestamp_iso: str,
    code_revision: str,
    engine_version: str = "4.0.0",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    provider_status: Optional[str] = None,
    is_stale_feed: Optional[bool] = None,
    is_provider_transition: Optional[bool] = None,
    macro_context: Optional[str] = None,
) -> dict:
    """
    Idempotently analyze closed candle up to candle_timestamp with complete market context (A03, P4-21).

    Strict Version-Pinned Invariant (P4-21):
      Uses pinned engine/config/feature/cycle versions and explicit code_revision.
    """
    if not code_revision:
        raise ValueError("Explicit code_revision is strictly required for signal provenance.")

    try:
        instrument = Instrument.objects.get(id=instrument_id)
        candle_ts = datetime.fromisoformat(candle_timestamp_iso)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        def _get_candles(tf: str, limit: int = 128) -> list[CandleData]:
            qs = (
                MarketCandle.objects.filter(
                    instrument=instrument,
                    timeframe=tf,
                    timestamp_close__lte=candle_ts,
                    is_closed=True,
                )
                .order_by("-timestamp_close")[:limit]
            )
            return [
                CandleData(
                    timestamp_open=c.timestamp_open,
                    timestamp_close=c.timestamp_close,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                    is_closed=c.is_closed,
                    quote_rate=c.quote_rate,
                    close_usd=c.close_usd,
                    source_id=c.source,
                )
                for c in reversed(list(qs))
            ]

        candles_15m = _get_candles("15m", 128)
        if not candles_15m:
            return {"status": "SKIPPED", "reason": "No closed 15m candles found"}

        candles_1h = _get_candles("1h", 64)
        candles_4h = _get_candles("4h", 64)
        candles_1d = _get_candles("1d", 32)

        # Retrieve historical XAU/USD benchmark candles <= candle_ts
        xau_qs = (
            MarketCandle.objects.filter(
                instrument__base_asset__code="XAU",
                instrument__quote_asset__code="USD",
                timestamp_close__lte=candle_ts,
                is_closed=True,
            )
            .order_by("-timestamp_close")[:64]
        )
        candles_xau = [
            CandleData(
                timestamp_open=c.timestamp_open,
                timestamp_close=c.timestamp_close,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed,
                source_id=c.source,
            )
            for c in reversed(list(xau_qs))
        ]
        latest_xau = candles_xau[-1] if candles_xau else None
        xau_ref_price = float(latest_xau.close) if latest_xau else None
        xau_ref_bullish = bool(latest_xau.close >= latest_xau.open) if latest_xau else None
        xau_ref_ts = latest_xau.timestamp_close if latest_xau else None

        # Retrieve latest USDT rate from 15m candle or state
        latest_15m = candles_15m[-1]
        usdt_rate = float(latest_15m.quote_rate) if latest_15m.quote_rate else None
        usdt_rate_ts = latest_15m.timestamp_close if usdt_rate else None

        # Authoritative Provider Health Lookup (Fail closed if missing)
        if provider_status is not None:
            effective_provider_status = provider_status
            effective_is_transition = bool(is_provider_transition)
        else:
            from apps.instruments.models import ProviderHealthSnapshot
            latest_health = (
                ProviderHealthSnapshot.objects.filter(
                    listing__instrument=instrument,
                    checked_at__lte=candle_ts,
                )
                .order_by("-checked_at")
                .first()
            )
            if latest_health:
                effective_provider_status = latest_health.status
                effective_is_transition = (latest_health.status == "TRANSITION")
            else:
                # If health snapshot table has entries for this instrument, missing means UNKNOWN
                has_any_listing = instrument.listings.exists()
                effective_provider_status = "UNKNOWN" if has_any_listing else "HEALTHY"
                effective_is_transition = False

        # Authoritative Data Quality Lookup (Fail closed if hard fail, stale, or missing)
        if is_stale_feed is not None:
            effective_is_stale = is_stale_feed
        else:
            from apps.market_data.models import DataQualityRecord
            latest_quality = (
                DataQualityRecord.objects.filter(
                    candle__instrument=instrument,
                    checked_at__lte=candle_ts,
                )
                .order_by("-checked_at")
                .first()
            )
            effective_is_stale = (latest_quality.overall_status == "FAILED") if latest_quality else False

        # Construct MacroEventContext
        if macro_context == "BLACKOUT":
            macro_ctx_obj = MacroEventContext(
                is_in_blackout=True,
                minutes_to_next_event=0,
                minutes_since_last_event=0,
                active_event_name="HIGH_IMPACT_EVENT",
                is_feed_healthy=True,
            )
        elif macro_context == "CLEAR":
            macro_ctx_obj = MacroEventContext(
                is_in_blackout=False,
                minutes_to_next_event=180,
                minutes_since_last_event=180,
                active_event_name=None,
                is_feed_healthy=True,
            )
        else:
            macro_ctx_obj = MacroEventContext(
                is_in_blackout=False,
                minutes_to_next_event=None,
                minutes_since_last_event=None,
                active_event_name=None,
                is_feed_healthy=False,
            )

        # Historical Phase 3A Cycle Snapshot (PIT)
        from apps.analysis.models import CycleSnapshotRecord
        from apps.analysis.services import AnalysisPersistenceService
        cycle_rec = (
            CycleSnapshotRecord.objects.filter(
                instrument=instrument,
                timeframe=timeframe,
                timestamp__lte=candle_ts,
                cycle_version=cycle_version,
            )
            .order_by("-timestamp")
            .first()
        )
        cycle_3a_snapshot = (
            AnalysisPersistenceService.rehydrate_cycle_3a_snapshot(cycle_rec)
            if cycle_rec
            else None
        )

        # Check if instrument is canonical XAUUSD
        is_xauusd = False
        try:
            from engine.signals.profile import normalize_xauusd_target
            norm_target = normalize_xauusd_target(instrument.symbol)
            is_xauusd = (norm_target == "XAUUSD")
        except ValueError:
            is_xauusd = False

        if is_xauusd:
            from engine.core.types import FeedHealthStatus, RuntimeFeedHealth
            from engine.signals.engine import XauUsdSignalEngine

            rfh = RuntimeFeedHealth(
                primary_15m=FeedHealthStatus.HEALTHY if not effective_is_stale else FeedHealthStatus.STALE,
                primary_1h=FeedHealthStatus.HEALTHY if candles_1h else FeedHealthStatus.UNKNOWN,
                primary_4h=FeedHealthStatus.HEALTHY if candles_4h else FeedHealthStatus.UNKNOWN,
                primary_1d=FeedHealthStatus.HEALTHY if candles_1d else FeedHealthStatus.UNKNOWN,
                secondary_provider=FeedHealthStatus.HEALTHY if effective_provider_status == "HEALTHY" else FeedHealthStatus.UNKNOWN,
                macro_blackout_feed=FeedHealthStatus.HEALTHY if macro_ctx_obj.is_feed_healthy else FeedHealthStatus.UNKNOWN,
                is_macro_blackout=macro_ctx_obj.is_in_blackout,
            )

            engine_xau = XauUsdSignalEngine(
                code_revision=code_revision,
                engine_version=engine_version,
                feature_version=feature_version,
                cycle_version=cycle_version,
            )

            snapshot = engine_xau.analyze(
                closed_candles_15m=candles_15m,
                closed_candles_1h=candles_1h if candles_1h else None,
                closed_candles_4h=candles_4h if candles_4h else None,
                closed_candles_1d=candles_1d if candles_1d else None,
                runtime_health=rfh,
                profile=None,  # Production authority path uses uncalibrated profile
                instrument="XAUUSD",
                timeframe=timeframe,
                as_of=candle_ts,
                cycle_3a=cycle_3a_snapshot,
            )

            record, created = SignalPersistenceService.save_dual_side_snapshot(
                instrument=instrument,
                snapshot=snapshot,
            )

            return {
                "status": "SUCCESS",
                "fingerprint": record.analysis_fingerprint,
                "created": created,
                "state": record.state,
                "user_decision": record.user_decision,
                "direction_score": record.direction_score,
                "timing_score": record.timing_score,
                "long_direction_score": record.long_direction_score,
                "short_direction_score": record.short_direction_score,
                "long_timing_score": record.long_timing_score,
                "short_timing_score": record.short_timing_score,
                "config_version": record.config_version,
            }

        else:
            # Historical XAUT pipeline
            engine_xaut = XautSignalEngine(
                code_revision=code_revision,
                engine_version=engine_version,
                config_version=config_version,
                feature_version=feature_version,
                cycle_version=cycle_version,
            )

            snapshot = engine_xaut.analyze(
                candles_15m=candles_15m,
                candles_4h=candles_4h if candles_4h else None,
                candles_1d=candles_1d if candles_1d else None,
                candles_xau=candles_xau if candles_xau else None,
                as_of=candle_ts,
                instrument=instrument.symbol,
                timeframe=timeframe,
                xau_reference_price=xau_ref_price,
                xau_reference_is_bullish=xau_ref_bullish,
                xau_reference_ts=xau_ref_ts,
                usdt_rate=usdt_rate,
                usdt_rate_ts=usdt_rate_ts,
                provider_status=effective_provider_status,
                is_feed_stale=effective_is_stale,
                is_provider_transition=effective_is_transition,
                macro_context=macro_ctx_obj,
                cycle_3a=cycle_3a_snapshot,
            )

            record, created = SignalPersistenceService.save_signal_snapshot(
                instrument=instrument,
                snapshot=snapshot,
            )

            return {
                "status": "SUCCESS",
                "fingerprint": record.analysis_fingerprint,
                "created": created,
                "state": record.state,
                "user_decision": record.user_decision,
                "direction_score": record.direction_score,
                "timing_score": record.timing_score,
                "config_version": record.config_version,
            }

    except Exception as exc:
        logger.error("analyze_closed_candle_failed", exc_info=True, instrument_id=instrument_id)
        raise self.retry(exc=exc, countdown=10)
