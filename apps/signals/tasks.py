"""Celery tasks for asynchronous closed-candle signal generation (Phase 4)."""
from datetime import datetime, timezone
from celery import shared_task
import structlog

from apps.instruments.models import Instrument
from apps.market_data.models import MarketCandle
from apps.signals.services import SignalPersistenceService
from engine.core.types import CandleData
from engine.signals.engine import XautSignalEngine

logger = structlog.get_logger(__name__)


@shared_task(queue="analysis", bind=True, max_retries=3)
def analyze_closed_candle(
    self,
    instrument_id: int,
    timeframe: str,
    candle_timestamp_iso: str,
    engine_version: str = "4.0.0",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    code_revision: str = "2795de04",
) -> dict:
    """
    Idempotently analyze closed candle up to candle_timestamp (A03, P4-21).

    Strict Version-Pinned Invariant (P4-21):
      Uses pinned engine/config/feature/cycle versions and code_revision from
      task invocation payload rather than global active runtime configuration.
      Guarantees exact retry reproducibility even across subsequent code deployments.
    """
    try:
        instrument = Instrument.objects.get(id=instrument_id)
        candle_ts = datetime.fromisoformat(candle_timestamp_iso)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        # Query closed candles <= candle_ts
        candle_qs = (
            MarketCandle.objects.filter(
                instrument=instrument,
                timeframe=timeframe,
                timestamp_close__lte=candle_ts,
                is_closed=True,
            )
            .order_by("-timestamp_close")[:128]
        )
        candles_db = list(reversed(list(candle_qs)))

        if not candles_db:
            return {"status": "SKIPPED", "reason": "No closed candles found"}

        # Convert to pure engine CandleData
        engine_candles = [
            CandleData(
                timestamp_open=c.timestamp_open,
                timestamp_close=c.timestamp_close,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed,
            )
            for c in candles_db
        ]

        engine = XautSignalEngine(
            engine_version=engine_version,
            config_version=config_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
            code_revision=code_revision,
        )

        snapshot = engine.analyze(
            candles_15m=engine_candles,
            as_of=candle_ts,
            instrument=instrument.symbol,
            timeframe=timeframe,
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
