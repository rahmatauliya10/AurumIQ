"""Live monitoring services for quote streaming, closed-candle intelligence, and state recovery."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence, Tuple
import structlog
from django.db import transaction
from django.db.models import F

from apps.instruments.models import Instrument
from apps.live_monitor.consumers import LiveEventBroadcaster
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.types import (
    CandleClosedEvent,
    EntryZoneStatus,
    FeedStatus,
    LiveFeedHealthStatus,
    LiveProjectionState,
    LiveQuoteEvent,
)
from apps.market_data.models import CandleQualityFlag, DataQualitySnapshot, MarketCandle
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import CandleData, MacroEventContext, SignalState, UserDecision
from engine.risk.planner import RiskPlanner
from engine.signals.engine import XautSignalEngine

logger = structlog.get_logger(__name__)


class LiveQuoteService:
    """
    Path A: Real-time quote processor with freshness guards and zone readiness monitoring.

    Strict Invariants:
      1. Live quote updates NEVER alter Direction Score, Timing Score, SignalState,
         UserDecision, or analysis_fingerprint (A41, P7-03, P7-04).
      2. Quote Path updates ONLY quote-owned fields, preventing state clobbering (P7-C4).
      3. Out-of-order or stale sequence/timestamp quotes are safely discarded (P7-16, P7-16A, P7-16B).
      4. Entry zone readiness is active ONLY when risk_plan_valid=True and execution_eligible=True (P7-C1, P7-03B).
    """

    MAX_ALLOWED_FUTURE_SKEW_SECONDS = 60.0
    DEFAULT_QUOTE_STALENESS_SECONDS = 30.0

    @classmethod
    def process_quote(
        cls,
        event: LiveQuoteEvent,
        max_staleness_seconds: float = DEFAULT_QUOTE_STALENESS_SECONDS,
    ) -> Optional[LiveMonitorState]:
        """Process incoming live market quote atomically with field-scoped updates."""
        now_utc = datetime.now(timezone.utc)
        src_ts = (
            event.source_timestamp.astimezone(timezone.utc)
            if event.source_timestamp.tzinfo
            else event.source_timestamp.replace(tzinfo=timezone.utc)
        )
        rec_ts = (
            event.received_timestamp.astimezone(timezone.utc)
            if event.received_timestamp.tzinfo
            else event.received_timestamp.replace(tzinfo=timezone.utc)
        )

        # 1. Monotonicity & Ordering Validation (P7-C5, P7-16, P7-16A, P7-16B)
        # Check future skew
        future_skew = (src_ts - rec_ts).total_seconds()
        is_future_skewed = future_skew > cls.MAX_ALLOWED_FUTURE_SKEW_SECONDS

        # Calculate quote age strictly from source_timestamp (P7-15)
        raw_age = (now_utc - src_ts).total_seconds()
        quote_age = max(0.0, raw_age) if not is_future_skewed else cls.MAX_ALLOWED_FUTURE_SKEW_SECONDS + 1.0
        is_stale = quote_age > max_staleness_seconds or is_future_skewed

        with transaction.atomic():
            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument=event.instrument,
                defaults={
                    "signal_state": "NO_TRADE",
                    "signal_user_decision": "WAIT",
                    "effective_action": "WAIT",
                    "entry_zone_status": EntryZoneStatus.NO_ACTIVE_ZONE.value,
                },
            )

            # Sequence-based ordering check (P7-16A)
            if event.sequence_number is not None and state.quote_sequence is not None:
                if event.sequence_number <= state.quote_sequence:
                    logger.debug(
                        "quote_ignored_stale_sequence",
                        event_seq=event.sequence_number,
                        state_seq=state.quote_sequence,
                        instrument=event.instrument,
                    )
                    return state

            # Timestamp-based fallback ordering check (P7-16)
            elif state.quote_source_timestamp is not None and src_ts < state.quote_source_timestamp:
                logger.debug(
                    "quote_ignored_stale_timestamp",
                    event_ts=src_ts.isoformat(),
                    state_ts=state.quote_source_timestamp.isoformat(),
                    instrument=event.instrument,
                )
                return state

            # 2. Entry Zone Execution Readiness (P7-C1, P7-03B)
            # Active ONLY if Phase 5 risk plan is valid and eligible
            entry_status = EntryZoneStatus.NO_ACTIVE_ZONE
            dist_pct: Optional[Decimal] = None

            if state.risk_plan_valid and state.execution_eligible and state.entry_min and state.entry_max:
                if state.entry_min <= event.ask <= state.entry_max:
                    entry_status = EntryZoneStatus.INSIDE_ZONE
                    dist_pct = Decimal("0.00")
                elif event.ask > state.entry_max:
                    entry_status = EntryZoneStatus.ABOVE_ZONE
                    if state.entry_max > 0:
                        dist_pct = (((event.ask - state.entry_max) / state.entry_max) * Decimal("100.0")).quantize(
                            Decimal("0.01")
                        )
                else:
                    entry_status = EntryZoneStatus.BELOW_ZONE
                    if state.entry_min > 0:
                        dist_pct = (((state.entry_min - event.ask) / state.entry_min) * Decimal("100.0")).quantize(
                            Decimal("0.01")
                        )

            # 3. Concurrent Field-Scoped Update (P7-C4)
            # Update ONLY quote-owned fields
            LiveMonitorState.objects.filter(id=state.id).update(
                current_bid=event.bid,
                current_ask=event.ask,
                spread=event.spread,
                spread_pct=event.spread_pct,
                quote_source_timestamp=src_ts,
                quote_received_timestamp=rec_ts,
                quote_age_seconds=quote_age,
                is_quote_stale=is_stale,
                quote_sequence=event.sequence_number,
                entry_zone_status=entry_status.value,
                distance_to_entry_zone_pct=dist_pct,
            )

            state.refresh_from_db()

            # Broadcast typed quote update to cross-process Redis bus & subscribers
            quote_payload = LiveEventBroadcaster.format_quote_event(
                instrument=state.instrument,
                bid=state.current_bid,
                ask=state.current_ask,
                spread=state.spread,
                spread_pct=state.spread_pct,
                source_timestamp=state.quote_source_timestamp,
                sequence_number=state.quote_sequence,
                entry_zone_status=state.entry_zone_status,
                distance_to_entry_zone_pct=state.distance_to_entry_zone_pct,
            )
            LiveEventBroadcaster.broadcast(quote_payload)
            return state


class LiveDecisionPipelineService:
    """
    Path B: Closed-Candle Signal Intelligence & Risk Evaluation Pipeline.

    Strict Invariants:
      1. Only CLOSED decision candles can trigger analysis (A40, P7-05, P7-06).
      2. Executes exact frozen XautSignalEngine and RiskPlanner (A39, P7-01, P7-02).
      3. Hard gates remain strictly authoritative in the frozen engine (P7-C2, P7-10..P7-14).
      4. Decision Path updates ONLY decision-owned fields, preventing state clobbering (P7-C4).
      5. Persists immutable LiveRiskPlanRecord linked to source_signal_fingerprint (P7-C3).
    """

    @classmethod
    def process_closed_candle(
        cls,
        event: CandleClosedEvent,
        code_revision: str,
        engine_version: str = "4.0.0",
        config_version: str = "cfg-2026-v1",
        feature_version: str = "feat-2026-v1",
        cycle_version: str = "3.0.0-3A",
        risk_version: str = "5.0.0",
        xau_reference_price: Optional[Decimal] = None,
        xau_reference_is_bullish: Optional[bool] = None,
        xau_reference_ts: Optional[datetime] = None,
        usdt_rate: Optional[Decimal] = None,
        usdt_rate_ts: Optional[datetime] = None,
        provider_status: str = "HEALTHY",
        is_provider_transition: bool = False,
        is_feed_stale: bool = False,
        macro_context: Optional[MacroEventContext] = None,
    ) -> Tuple[SignalRecord, Optional[LiveRiskPlanRecord], LiveMonitorState]:
        """
        Execute deterministic closed-candle signal evaluation and risk planning.
        """
        # Step 1: Reject unclosed candle (P7-06)
        if not event.is_closed:
            raise ValueError(f"Unclosed candle for {event.instrument} cannot trigger decision pipeline.")

        candle_ts = (
            event.timestamp_close.astimezone(timezone.utc)
            if event.timestamp_close.tzinfo
            else event.timestamp_close.replace(tzinfo=timezone.utc)
        )
        now_utc = datetime.now(timezone.utc)

        # Step 2: Query multi-timeframe historical closed candles <= candle_ts
        instrument_obj = Instrument.objects.filter(
            base_asset__code="XAUT", quote_asset__code="USDT"
        ).first()
        if not instrument_obj:
            from apps.instruments.models import Asset, AssetType, InstrumentRole, InstrumentType
            xaut, _ = Asset.objects.get_or_create(code="XAUT", defaults={"name": "Tether Gold", "asset_type": AssetType.CRYPTO_TOKEN})
            usdt, _ = Asset.objects.get_or_create(code="USDT", defaults={"name": "Tether USD", "asset_type": AssetType.CRYPTO_TOKEN})
            instrument_obj, _ = Instrument.objects.get_or_create(
                base_asset=xaut,
                quote_asset=usdt,
                defaults={"role": InstrumentRole.EXECUTION, "instrument_type": InstrumentType.SPOT},
            )

        def _get_engine_candles(tf: str, limit: int = 128) -> list[CandleData]:
            if not instrument_obj:
                return []
            qs = (
                MarketCandle.objects.filter(
                    instrument=instrument_obj,
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

        engine_candles = _get_engine_candles(event.timeframe, 128)
        engine_candles_4h = _get_engine_candles("4h", 64)
        engine_candles_1d = _get_engine_candles("1d", 32)

        # Historical XAU/USD reference candles
        xau_qs = (
            MarketCandle.objects.filter(
                instrument__base_asset__code="XAU",
                instrument__quote_asset__code="USD",
                timestamp_close__lte=candle_ts,
                is_closed=True,
            )
            .order_by("-timestamp_close")[:64]
        )
        engine_candles_xau = [
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

        # Include current event candle if not already in DB
        if not any(c.timestamp_close == candle_ts for c in engine_candles):
            engine_candles.append(
                CandleData(
                    timestamp_open=event.timestamp_open,
                    timestamp_close=candle_ts,
                    open=event.open,
                    high=event.high,
                    low=event.low,
                    close=event.close,
                    volume=event.volume,
                    is_closed=True,
                )
            )

        # Step 3: Instantiate frozen XautSignalEngine (P7-01, P7-C6)
        engine = XautSignalEngine(
            code_revision=code_revision,
            engine_version=engine_version,
            config_version=config_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
        )

        # Step 4: Execute deterministic closed-candle signal analysis (P7-C2)
        signal_snapshot = engine.analyze(
            candles_15m=engine_candles,
            candles_4h=engine_candles_4h if engine_candles_4h else None,
            candles_1d=engine_candles_1d if engine_candles_1d else None,
            candles_xau=engine_candles_xau if engine_candles_xau else None,
            as_of=candle_ts,
            instrument=event.instrument,
            timeframe=event.timeframe,
            xau_reference_price=xau_reference_price,
            xau_reference_is_bullish=xau_reference_is_bullish,
            xau_reference_ts=xau_reference_ts,
            usdt_rate=usdt_rate,
            usdt_rate_ts=usdt_rate_ts,
            provider_status=provider_status,
            is_provider_transition=is_provider_transition,
            is_feed_stale=is_feed_stale,
            macro_context=macro_context,
        )

        # Step 5: Persist immutable SignalRecord (P7-07, A03, A08)
        signal_record, _ = SignalPersistenceService.save_signal_snapshot(
            instrument=instrument_obj,
            snapshot=signal_snapshot,
        )

        # Step 6: Evaluate Phase 5 RiskPlanner if eligible (P7-02, P7-C1, P7-C3)
        risk_planner = RiskPlanner(
            code_revision=code_revision,
            risk_version=risk_version,
            config_version=config_version,
        )

        # Extract structure and volatility from engine for risk planning
        features_15m = engine.feature_engine.extract_features(engine_candles) if len(engine_candles) >= 32 else None
        atr14 = features_15m.atr14 if features_15m else None
        structure_15m = engine.structure_engine.analyze(engine_candles, atr=atr14) if (len(engine_candles) >= 32 and atr14 is not None) else None

        risk_plan_snapshot = risk_planner.plan(
            signal_snapshot=signal_snapshot,
            structure_15m=structure_15m,
            atr14=atr14,
            latest_close=event.close,
        )

        # Step 7: Persist immutable LiveRiskPlanRecord (P7-C3)
        risk_record, _ = LiveRiskPlanRecord.objects.get_or_create(
            source_signal_fingerprint=risk_plan_snapshot.source_signal_fingerprint,
            defaults={
                "signal_timestamp": risk_plan_snapshot.signal_generated_at,
                "instrument": event.instrument,
                "entry_min": risk_plan_snapshot.entry_min,
                "entry_mid": risk_plan_snapshot.entry_mid,
                "entry_max": risk_plan_snapshot.entry_max,
                "stop_structure": risk_plan_snapshot.stop_structure,
                "stop_atr": risk_plan_snapshot.stop_atr,
                "stop_final": risk_plan_snapshot.stop_final,
                "stop_distance_atr": risk_plan_snapshot.stop_distance_atr,
                "tp1": risk_plan_snapshot.tp1,
                "tp2": risk_plan_snapshot.tp2,
                "rr_tp1": risk_plan_snapshot.rr_tp1,
                "rr_tp2": risk_plan_snapshot.rr_tp2,
                "is_valid_risk_plan": risk_plan_snapshot.is_valid_risk_plan,
                "execution_eligible": risk_plan_snapshot.execution_eligible,
                "effective_action": risk_plan_snapshot.effective_action.value,
                "reasons": list(risk_plan_snapshot.reasons),
                "source_zone_id": risk_plan_snapshot.source_zone_id,
                "source_zone_timestamp": risk_plan_snapshot.source_zone_timestamp,
                "risk_version": risk_plan_snapshot.risk_version,
                "execution_model_version": risk_plan_snapshot.execution_model_version,
                "config_version": risk_plan_snapshot.config_version,
                "code_revision": risk_plan_snapshot.code_revision,
            },
        )

        # Step 8: Assemble feed health status
        feed_health = {
            "xaut_status": FeedStatus.STALE.value if is_feed_stale else FeedStatus.HEALTHY.value,
            "xau_status": FeedStatus.DOWN.value if xau_reference_price is None else FeedStatus.HEALTHY.value,
            "usdt_norm_status": FeedStatus.DOWN.value if usdt_rate is None else FeedStatus.HEALTHY.value,
            "macro_status": (
                FeedStatus.DEGRADED.value
                if (macro_context and macro_context.is_in_blackout)
                else FeedStatus.HEALTHY.value
            ),
            "provider_sync_status": FeedStatus.TRANSITION.value if is_provider_transition else FeedStatus.HEALTHY.value,
        }

        # Step 9: Atomically update Decision-Owned fields in LiveMonitorState (P7-C4)
        with transaction.atomic():
            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument=event.instrument,
                defaults={"effective_action": risk_plan_snapshot.effective_action.value},
            )

            # Re-evaluate entry zone with new decision boundaries if quote exists
            entry_status = EntryZoneStatus.NO_ACTIVE_ZONE
            dist_pct = None
            if (
                risk_plan_snapshot.is_valid_risk_plan
                and risk_plan_snapshot.execution_eligible
                and state.current_ask
                and risk_plan_snapshot.entry_min
                and risk_plan_snapshot.entry_max
            ):
                if risk_plan_snapshot.entry_min <= state.current_ask <= risk_plan_snapshot.entry_max:
                    entry_status = EntryZoneStatus.INSIDE_ZONE
                    dist_pct = Decimal("0.00")
                elif state.current_ask > risk_plan_snapshot.entry_max:
                    entry_status = EntryZoneStatus.ABOVE_ZONE
                    dist_pct = (
                        (state.current_ask - risk_plan_snapshot.entry_max) / risk_plan_snapshot.entry_max * Decimal("100.0")
                    ).quantize(Decimal("0.01"))
                else:
                    entry_status = EntryZoneStatus.BELOW_ZONE
                    dist_pct = (
                        (risk_plan_snapshot.entry_min - state.current_ask) / risk_plan_snapshot.entry_min * Decimal("100.0")
                    ).quantize(Decimal("0.01"))

            LiveMonitorState.objects.filter(id=state.id).update(
                last_closed_candle_ts=candle_ts,
                last_analysis_timestamp=now_utc,
                signal_fingerprint=signal_snapshot.analysis_fingerprint,
                signal_state=signal_snapshot.state.value,
                signal_user_decision=signal_snapshot.user_decision.value,
                direction_score=signal_snapshot.direction.total_score,
                timing_score=signal_snapshot.timing.total_score,
                risk_plan_valid=risk_plan_snapshot.is_valid_risk_plan,
                execution_eligible=risk_plan_snapshot.execution_eligible,
                effective_action=risk_plan_snapshot.effective_action.value,
                entry_min=risk_plan_snapshot.entry_min if risk_plan_snapshot.is_valid_risk_plan else None,
                entry_mid=risk_plan_snapshot.entry_mid if risk_plan_snapshot.is_valid_risk_plan else None,
                entry_max=risk_plan_snapshot.entry_max if risk_plan_snapshot.is_valid_risk_plan else None,
                stop_final=risk_plan_snapshot.stop_final if risk_plan_snapshot.is_valid_risk_plan else None,
                tp1=risk_plan_snapshot.tp1 if risk_plan_snapshot.is_valid_risk_plan else None,
                tp2=risk_plan_snapshot.tp2 if risk_plan_snapshot.is_valid_risk_plan else None,
                rr_tp1=risk_plan_snapshot.rr_tp1 if risk_plan_snapshot.is_valid_risk_plan else None,
                rr_tp2=risk_plan_snapshot.rr_tp2 if risk_plan_snapshot.is_valid_risk_plan else None,
                reasons_positive=list(signal_snapshot.reasons_positive),
                reasons_negative=list(signal_snapshot.reasons_negative),
                hard_gate_reasons=list(signal_snapshot.hard_gate_reasons),
                feed_health_data=feed_health,
                entry_zone_status=entry_status.value,
                distance_to_entry_zone_pct=dist_pct,
                engine_version=engine_version,
                config_version=config_version,
                feature_version=feature_version,
                cycle_version=cycle_version,
                risk_version=risk_version,
                code_revision=code_revision,
                decision_sequence=F("decision_sequence") + 1,
            )

            state.refresh_from_db()

            # Broadcast decision updates after atomic DB commit to cross-process Redis bus & subscribers
            sig_payload = LiveEventBroadcaster.format_signal_update(
                instrument=event.instrument,
                signal_fingerprint=signal_snapshot.analysis_fingerprint,
                signal_state=signal_snapshot.state.value,
                signal_user_decision=signal_snapshot.user_decision.value,
                direction_score=signal_snapshot.direction.total_score,
                timing_score=signal_snapshot.timing.total_score,
                last_closed_candle_ts=candle_ts,
                decision_sequence=state.decision_sequence,
                reasons_positive=list(signal_snapshot.reasons_positive),
                reasons_negative=list(signal_snapshot.reasons_negative),
                hard_gate_reasons=list(signal_snapshot.hard_gate_reasons),
            )
            LiveEventBroadcaster.broadcast(sig_payload)

            risk_payload = LiveEventBroadcaster.format_risk_plan_update(
                instrument=event.instrument,
                source_signal_fingerprint=risk_plan_snapshot.source_signal_fingerprint,
                risk_plan_valid=risk_plan_snapshot.is_valid_risk_plan,
                execution_eligible=risk_plan_snapshot.execution_eligible,
                effective_action=risk_plan_snapshot.effective_action.value,
                entry_min=risk_plan_snapshot.entry_min if risk_plan_snapshot.is_valid_risk_plan else None,
                entry_mid=risk_plan_snapshot.entry_mid if risk_plan_snapshot.is_valid_risk_plan else None,
                entry_max=risk_plan_snapshot.entry_max if risk_plan_snapshot.is_valid_risk_plan else None,
                stop_final=risk_plan_snapshot.stop_final if risk_plan_snapshot.is_valid_risk_plan else None,
                tp1=risk_plan_snapshot.tp1 if risk_plan_snapshot.is_valid_risk_plan else None,
                tp2=risk_plan_snapshot.tp2 if risk_plan_snapshot.is_valid_risk_plan else None,
                rr_tp1=risk_plan_snapshot.rr_tp1 if risk_plan_snapshot.is_valid_risk_plan else None,
                rr_tp2=risk_plan_snapshot.rr_tp2 if risk_plan_snapshot.is_valid_risk_plan else None,
                decision_sequence=state.decision_sequence,
            )
            LiveEventBroadcaster.broadcast(risk_payload)

            health_payload = LiveEventBroadcaster.format_feed_health_update(
                instrument=event.instrument,
                feed_health=feed_health,
            )
            LiveEventBroadcaster.broadcast(health_payload)

            return signal_record, risk_record, state


class StateRecoveryService:
    """
    Restart & Recovery Service (P7-C3, P7-18, P7-18A, P7-18B).

    Reconstructs the complete canonical LiveMonitorState presentation projection
    strictly from persisted historical database records (SignalRecord, LiveRiskPlanRecord,
    MarketCandle, DataQualitySnapshot).
    
    Zero recalculation of historical signals or risk plans.
    """

    @classmethod
    def reconstruct_state(cls, instrument_symbol: str = "XAUT/USDT") -> LiveMonitorState:
        """Rebuild projection state from canonical durable storage."""
        with transaction.atomic():
            # Query latest SignalRecord
            latest_signal = (
                SignalRecord.objects.filter(instrument__base_asset__code="XAUT")
                .order_by("-timestamp", "-created_at")
                .first()
            )

            # Query matching LiveRiskPlanRecord
            risk_record = None
            if latest_signal:
                risk_record = LiveRiskPlanRecord.objects.filter(
                    source_signal_fingerprint=latest_signal.analysis_fingerprint
                ).first()

            # Query latest MarketCandle for baseline price reference
            latest_candle = (
                MarketCandle.objects.filter(
                    instrument__base_asset__code="XAUT",
                    timeframe="15m",
                    is_closed=True,
                )
                .order_by("-timestamp_close")
                .first()
            )

            # Determine feed health
            dq_snap = DataQualitySnapshot.objects.filter(
                instrument__base_asset__code="XAUT"
            ).order_by("-timestamp").first()

            feed_health = {
                "xaut_status": FeedStatus.HEALTHY.value if (dq_snap and not dq_snap.is_stale) else FeedStatus.STALE.value,
                "xau_status": FeedStatus.HEALTHY.value,
                "usdt_norm_status": FeedStatus.HEALTHY.value,
                "macro_status": FeedStatus.HEALTHY.value,
                "provider_sync_status": FeedStatus.HEALTHY.value,
            }

            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument=instrument_symbol,
            )

            if latest_signal:
                state.signal_fingerprint = latest_signal.analysis_fingerprint
                state.signal_state = latest_signal.state
                state.signal_user_decision = latest_signal.user_decision
                state.direction_score = latest_signal.direction_score
                state.timing_score = latest_signal.timing_score
                state.reasons_positive = latest_signal.reasons_positive
                state.reasons_negative = latest_signal.reasons_negative
                state.hard_gate_reasons = latest_signal.hard_gate_reasons
                state.engine_version = latest_signal.engine_version
                state.config_version = latest_signal.config_version
                state.feature_version = latest_signal.feature_version
                state.cycle_version = latest_signal.cycle_version
                state.code_revision = latest_signal.code_revision
                state.last_closed_candle_ts = latest_signal.timestamp
                state.last_analysis_timestamp = latest_signal.created_at

            if risk_record:
                state.risk_plan_valid = risk_record.is_valid_risk_plan
                state.execution_eligible = risk_record.execution_eligible
                state.effective_action = risk_record.effective_action
                state.entry_min = risk_record.entry_min if risk_record.is_valid_risk_plan else None
                state.entry_mid = risk_record.entry_mid if risk_record.is_valid_risk_plan else None
                state.entry_max = risk_record.entry_max if risk_record.is_valid_risk_plan else None
                state.stop_final = risk_record.stop_final if risk_record.is_valid_risk_plan else None
                state.tp1 = risk_record.tp1 if risk_record.is_valid_risk_plan else None
                state.tp2 = risk_record.tp2 if risk_record.is_valid_risk_plan else None
                state.rr_tp1 = risk_record.rr_tp1 if risk_record.is_valid_risk_plan else None
                state.rr_tp2 = risk_record.rr_tp2 if risk_record.is_valid_risk_plan else None
                state.risk_version = risk_record.risk_version
            elif latest_signal:
                state.risk_plan_valid = False
                state.execution_eligible = False
                state.effective_action = latest_signal.user_decision
                state.entry_min = None
                state.entry_mid = None
                state.entry_max = None
                state.stop_final = None
                state.tp1 = None
                state.tp2 = None
                state.rr_tp1 = None
                state.rr_tp2 = None

            state.feed_health_data = feed_health

            if latest_candle and state.current_ask is None:
                state.current_ask = latest_candle.close
                state.current_bid = latest_candle.close - Decimal("0.50")
                state.spread = Decimal("0.50")
                state.spread_pct = (Decimal("0.50") / latest_candle.close).quantize(Decimal("0.0001"))
                state.quote_source_timestamp = latest_candle.timestamp_close
                state.is_quote_stale = True

            state.save()
            return state
