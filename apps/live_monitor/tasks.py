"""Celery tasks for asynchronous live quote processing and closed candle intelligence."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from celery import shared_task
import structlog

from apps.live_monitor.adapter import PublicMarketDataAdapter
from apps.live_monitor.services import LiveDecisionPipelineService, LiveQuoteService, StateRecoveryService

logger = structlog.get_logger(__name__)


@shared_task(queue="market_data", bind=True, max_retries=3)
def process_live_quote_task(
    self,
    instrument: str,
    provider: str,
    bid_str: str,
    ask_str: str,
    source_timestamp_iso: str,
    sequence_number: Optional[int] = None,
) -> dict:
    """Process incoming live market quote asynchronously."""
    try:
        src_ts = datetime.fromisoformat(source_timestamp_iso)
        if src_ts.tzinfo is None:
            src_ts = src_ts.replace(tzinfo=timezone.utc)

        event = PublicMarketDataAdapter.create_quote_event(
            instrument=instrument,
            provider=provider,
            bid=Decimal(bid_str),
            ask=Decimal(ask_str),
            source_timestamp=src_ts,
            sequence_number=sequence_number,
        )

        state = LiveQuoteService.process_quote(event)
        if not state:
            return {"status": "SKIPPED", "instrument": instrument}

        return {
            "status": "SUCCESS",
            "instrument": state.instrument,
            "current_ask": str(state.current_ask),
            "spread": str(state.spread),
            "entry_zone_status": state.entry_zone_status,
            "is_quote_stale": state.is_quote_stale,
        }
    except Exception as exc:
        logger.error("process_live_quote_task_failed", exc_info=True, instrument=instrument)
        raise self.retry(exc=exc, countdown=2)


@shared_task(queue="analysis", bind=True, max_retries=3)
def process_closed_candle_task(
    self,
    instrument: str,
    timeframe: str,
    timestamp_open_iso: str,
    timestamp_close_iso: str,
    open_str: str,
    high_str: str,
    low_str: str,
    close_str: str,
    code_revision: str,
    volume_str: str = "0",
    quote_rate_str: Optional[str] = None,
    source: str = "binance",
    engine_version: str = "4.0.0",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "5.0.0",
    xau_price_str: Optional[str] = None,
    xau_bullish: Optional[bool] = None,
    usdt_rate_str: Optional[str] = None,
    provider_status: Optional[str] = None,
    is_provider_transition: Optional[bool] = None,
    is_feed_stale: Optional[bool] = None,
) -> dict:
    """Execute closed candle signal and risk evaluation with version-pinned provenance (P7-C6)."""
    if not code_revision:
        raise ValueError("Explicit code_revision is strictly required for live signal provenance.")

    try:
        ts_open = datetime.fromisoformat(timestamp_open_iso)
        ts_close = datetime.fromisoformat(timestamp_close_iso)
        if ts_open.tzinfo is None:
            ts_open = ts_open.replace(tzinfo=timezone.utc)
        if ts_close.tzinfo is None:
            ts_close = ts_close.replace(tzinfo=timezone.utc)

        event = PublicMarketDataAdapter.create_candle_closed_event(
            instrument=instrument,
            timeframe=timeframe,
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open_price=Decimal(open_str),
            high_price=Decimal(high_str),
            low_price=Decimal(low_str),
            close_price=Decimal(close_str),
            volume=Decimal(volume_str),
            quote_rate=Decimal(quote_rate_str) if quote_rate_str else None,
            source=source,
            is_closed=True,
        )

        xau_p = Decimal(xau_price_str) if xau_price_str else None
        xau_bull = xau_bullish
        xau_ts = None
        if xau_p is None:
            from apps.market_data.models import MarketCandle
            latest_xau = (
                MarketCandle.objects.filter(
                    instrument__base_asset__code="XAU",
                    instrument__quote_asset__code="USD",
                    timestamp_close__lte=ts_close,
                    is_closed=True,
                )
                .order_by("-timestamp_close")
                .first()
            )
            if latest_xau:
                xau_p = latest_xau.close
                xau_bull = bool(latest_xau.close >= latest_xau.open)
                xau_ts = latest_xau.timestamp_close

        usdt_r = Decimal(usdt_rate_str) if usdt_rate_str else None
        usdt_ts = None
        if usdt_r is None:
            from apps.market_data.models import MarketCandle
            latest_norm = (
                MarketCandle.objects.filter(
                    instrument__base_asset__code="XAUT",
                    instrument__quote_asset__code="USDT",
                    timeframe=timeframe,
                    timestamp_close__lte=ts_close,
                    is_closed=True,
                )
                .exclude(quote_rate__isnull=True)
                .order_by("-timestamp_close")
                .first()
            )
            if latest_norm and latest_norm.quote_rate:
                usdt_r = latest_norm.quote_rate
                usdt_ts = latest_norm.timestamp_close

        eff_provider_transition = is_provider_transition
        eff_provider_status = provider_status
        if eff_provider_transition is None or eff_provider_status is None:
            from apps.instruments.models import ProviderHealthSnapshot
            latest_health = (
                ProviderHealthSnapshot.objects.filter(
                    listing__instrument__base_asset__code="XAUT",
                    checked_at__lte=ts_close,
                )
                .order_by("-checked_at")
                .first()
            )
            if latest_health:
                if eff_provider_status is None:
                    eff_provider_status = latest_health.status
                if eff_provider_transition is None:
                    eff_provider_transition = bool(latest_health.status == "TRANSITION")
            else:
                eff_provider_status = "DOWN"
                eff_provider_transition = True

        eff_feed_stale = is_feed_stale
        if eff_feed_stale is None:
            from apps.market_data.models import DataQualitySnapshot
            latest_dq = (
                DataQualitySnapshot.objects.filter(
                    instrument__base_asset__code="XAUT",
                    timeframe=timeframe,
                    timestamp__lte=ts_close,
                )
                .order_by("-timestamp")
                .first()
            )
            if latest_dq:
                eff_feed_stale = bool(latest_dq.is_stale or latest_dq.hard_fail)
            else:
                eff_feed_stale = True

        # Phase 3A Cycle & PIT Macro Context Resolution
        from apps.analysis.models import CycleSnapshotRecord
        from apps.analysis.services import AnalysisPersistenceService
        from engine.core.types import MacroEventContext
        latest_cycle = (
            CycleSnapshotRecord.objects.filter(
                instrument__base_asset__code="XAUT",
                instrument__quote_asset__code="USDT",
                timeframe=timeframe,
                timestamp__lte=ts_close,
                cycle_version=cycle_version,
            )
            .order_by("-timestamp")
            .first()
        )
        macro_ctx = None
        if latest_cycle:
            cycle_snap = AnalysisPersistenceService.rehydrate_cycle_3a_snapshot(latest_cycle)
            macro_ctx = cycle_snap.macro_event

        signal_record, risk_record, state = LiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision=code_revision,
            engine_version=engine_version,
            config_version=config_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
            risk_version=risk_version,
            xau_reference_price=xau_p,
            xau_reference_is_bullish=xau_bull,
            xau_reference_ts=xau_ts,
            usdt_rate=usdt_r,
            usdt_rate_ts=usdt_ts,
            provider_status=eff_provider_status,
            is_provider_transition=eff_provider_transition,
            is_feed_stale=eff_feed_stale,
            macro_context=macro_ctx,
        )

        return {
            "status": "SUCCESS",
            "fingerprint": signal_record.analysis_fingerprint,
            "signal_state": signal_record.state,
            "signal_user_decision": signal_record.user_decision,
            "effective_action": state.effective_action,
            "risk_plan_valid": state.risk_plan_valid,
            "direction_score": signal_record.direction_score,
            "timing_score": signal_record.timing_score,
            "code_revision": code_revision,
        }
    except Exception as exc:
        logger.error("process_closed_candle_task_failed", exc_info=True, instrument=instrument)
        raise self.retry(exc=exc, countdown=5)


@shared_task(queue="maintenance")
def reconstruct_live_state_task(instrument_symbol: str = "XAUT/USDT") -> dict:
    """Reconstruct live monitor presentation projection from database historical records."""
    state = StateRecoveryService.reconstruct_state(instrument_symbol)
    return {
        "status": "SUCCESS",
        "instrument": state.instrument,
        "signal_fingerprint": state.signal_fingerprint,
        "effective_action": state.effective_action,
        "current_ask": str(state.current_ask) if state.current_ask else None,
    }
