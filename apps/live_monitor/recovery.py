"""Explicit auditable recovery service for XAUUSD closed candles lacking SignalRecords (Spec §Stage 1)."""
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from django.conf import settings
from django.db import transaction

from apps.instruments.models import Instrument
from apps.signals.models import SignalRecord
from apps.market_data.models import MarketCandle

logger = logging.getLogger(__name__)


def catch_up_unprocessed_xauusd_candles(
    limit: int = 50,
    dry_run: bool = False,
    code_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scan for eligible closed 15m XAUUSD candles that lack a corresponding SignalRecord.
    Dispatches closed candle task only via explicit recovery execution, keeping
    routine live polling strictly isolated.
    """
    instrument = Instrument.get_canonical_xauusd()
    if instrument is None:
        return {
            "status": "error",
            "message": "Canonical XAUUSD instrument not found.",
            "recovered_count": 0,
            "recovered_candles": [],
        }

    candles_qs = MarketCandle.objects.filter(
        instrument=instrument,
        timeframe="15m",
        is_closed=True,
    ).order_by("-timestamp_close")[:limit * 2]

    existing_signal_timestamps = set(
        SignalRecord.objects.filter(
            instrument=instrument,
            timeframe="15m",
        ).values_list("timestamp", flat=True)
    )

    unprocessed_candles: List[MarketCandle] = []
    for candle in candles_qs:
        if candle.timestamp_close not in existing_signal_timestamps:
            unprocessed_candles.append(candle)
            if len(unprocessed_candles) >= limit:
                break

    if not unprocessed_candles:
        return {
            "status": "ok",
            "message": "No unprocessed closed 15m candles found.",
            "recovered_count": 0,
            "recovered_candles": [],
        }

    rev = code_revision or getattr(settings, "CODE_REVISION", "4a8a993af55ad433800e0bb8868e94063d62ff89")
    recovered: List[str] = []

    from apps.live_monitor.tasks import process_xauusd_closed_candle_task

    for c in unprocessed_candles:
        ts_close_str = c.timestamp_close.isoformat()
        recovered.append(ts_close_str)
        if not dry_run:
            logger.info(
                "recovering_unprocessed_closed_candle: open=%s close=%s source=%s",
                c.timestamp_open.isoformat(),
                ts_close_str,
                c.source,
            )
            process_xauusd_closed_candle_task.delay(
                instrument="XAUUSD",
                timeframe="15m",
                timestamp_open_iso=c.timestamp_open.isoformat(),
                timestamp_close_iso=ts_close_str,
                open_str=str(c.open),
                high_str=str(c.high),
                low_str=str(c.low),
                close_str=str(c.close),
                volume_str=str(c.volume),
                code_revision=rev,
                source=c.source,
            )

    return {
        "status": "success",
        "recovered_count": len(recovered),
        "recovered_candles": recovered,
        "dry_run": dry_run,
    }
