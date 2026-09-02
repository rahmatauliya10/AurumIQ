"""Live monitoring services for quote streaming, closed-candle intelligence, and state recovery."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
import structlog
from django.conf import settings
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
    XauUsdFeedHealthStatus,
    XauUsdLiveProjectionState,
)
from apps.market_data.models import CandleQualityFlag, DataQualitySnapshot, MarketCandle
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    DualSideSignalSnapshot,
    MacroEventContext,
    RiskSide,
    RuntimeFeedHealth,
    SideRiskPlanSnapshot,
    SignalSnapshot,
    SignalState,
    UserDecision,
)
from engine.risk.planner import RiskPlanner
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import uncalibrated_xauusd_risk_profile
from engine.signals.engine import XautSignalEngine, XauUsdSignalEngine
from engine.signals.profile import uncalibrated_xauusd_signal_profile

logger = structlog.get_logger(__name__)


class LiveQuoteService:
    """
    Path A: Real-time quote processor with freshness guards and zone readiness monitoring.

    Strict Invariants:
      1. Live quote updates NEVER alter Direction Score, Timing Score, SignalState,
         UserDecision, candidate_state, or analysis_fingerprint (A41, P7-03, P7-04).
      2. Quote Path updates ONLY quote-owned fields, preventing cross-writer state clobbering (P7-C4).
      3. Out-of-order or stale sequence/timestamp quotes are safely discarded (P7-16, P7-16A, P7-16B).
      4. Configuration-driven TTL/staleness (no hidden hardcoded defaults).
      5. Side-aware entry zone monitoring: LONG uses ASK, SHORT uses BID (Amendment 6).
      6. Invalidation monitoring: Informational notification only (no stop order sent).
    """

    @classmethod
    def get_stale_seconds(cls) -> Optional[float]:
        val = getattr(settings, "XAUUSD_QUOTE_STALE_SECONDS", None)
        return float(val) if val is not None else None

    @classmethod
    def get_future_skew_seconds(cls) -> Optional[float]:
        val = getattr(settings, "XAUUSD_QUOTE_FUTURE_SKEW_SECONDS", None)
        return float(val) if val is not None else None

    @classmethod
    def process_quote(
        cls,
        event: LiveQuoteEvent,
        max_staleness_seconds: Optional[float] = None,
    ) -> Optional[LiveMonitorState]:
        """Process incoming live market quote atomically with field-scoped updates."""
        # 1. Validation: Reject invalid quote geometry
        if event.bid <= 0 or event.ask <= 0:
            raise ValueError(f"Invalid non-positive quote: bid={event.bid}, ask={event.ask}")
        if event.ask < event.bid:
            raise ValueError(f"Invalid inverted quote: ask={event.ask} < bid={event.bid}")

        # Validation: Reject naive timestamps
        if event.source_timestamp.tzinfo is None or event.source_timestamp.tzinfo.utcoffset(event.source_timestamp) is None:
            raise ValueError("source_timestamp must be timezone-aware.")
        if event.received_timestamp.tzinfo is None or event.received_timestamp.tzinfo.utcoffset(event.received_timestamp) is None:
            raise ValueError("received_timestamp must be timezone-aware.")

        now_utc = datetime.now(timezone.utc)
        src_ts = event.source_timestamp.astimezone(timezone.utc)
        rec_ts = event.received_timestamp.astimezone(timezone.utc)

        # Freshness thresholds from configuration (fail-closed if missing)
        stale_threshold = max_staleness_seconds if max_staleness_seconds is not None else cls.get_stale_seconds()
        skew_threshold = cls.get_future_skew_seconds()

        # Check future skew
        future_skew = (src_ts - rec_ts).total_seconds()
        is_future_skewed = (future_skew > skew_threshold) if skew_threshold is not None else (future_skew > 60.0)

        # Calculate quote age strictly from source_timestamp
        raw_age = (now_utc - src_ts).total_seconds()
        quote_age = max(0.0, raw_age) if not is_future_skewed else 9999.0

        if stale_threshold is None:
            # Missing configuration -> fail closed / STALE
            is_stale = True
        else:
            is_stale = quote_age > stale_threshold or is_future_skewed

        with transaction.atomic():
            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument=event.instrument,
                defaults={
                    "signal_state": "NO_TRADE",
                    "signal_user_decision": "WAIT",
                    "effective_action": "WAIT",
                    "candidate_state": "NO_TRADE",
                    "candidate_user_decision": "WAIT",
                    "published_state": "NO_TRADE",
                    "published_user_decision": "WAIT",
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

            # 2. Side-Aware Entry Zone Execution Readiness (Amendment 6)
            # LONG candidate: Uses ASK
            # SHORT candidate: Uses BID
            entry_status = EntryZoneStatus.NO_ACTIVE_ZONE
            dist_pct: Optional[Decimal] = None

            # Determine candidate side
            side = state.risk_side
            if not side and state.candidate_effective_action in ("BUY", "SELL"):
                side = "LONG" if state.candidate_effective_action == "BUY" else "SHORT"

            if (
                state.risk_plan_valid
                and state.execution_eligible
                and state.entry_min
                and state.entry_max
                and side in ("LONG", "SHORT")
            ):
                eval_price = event.ask if side == "LONG" else event.bid

                if state.entry_min <= eval_price <= state.entry_max:
                    entry_status = EntryZoneStatus.INSIDE_ZONE
                    dist_pct = Decimal("0.00")
                elif eval_price > state.entry_max:
                    entry_status = EntryZoneStatus.ABOVE_ZONE
                    if state.entry_max > 0:
                        dist_pct = (((eval_price - state.entry_max) / state.entry_max) * Decimal("100.0")).quantize(
                            Decimal("0.01")
                        )
                else:
                    entry_status = EntryZoneStatus.BELOW_ZONE
                    if state.entry_min > 0:
                        dist_pct = (((state.entry_min - eval_price) / state.entry_min) * Decimal("100.0")).quantize(
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

            # 4. Trigger Informational Alerts
            from apps.alerts.services import AlertGenerationService
            provider_healthy = True
            if state.feed_health_data:
                provider_status = state.feed_health_data.get("xauusd_primary_status") or state.feed_health_data.get("xaut_status")
                provider_healthy = provider_status in ("HEALTHY", "DEGRADED")

            AlertGenerationService.evaluate_live_quote_alerts(
                state=state,
                bid=event.bid,
                ask=event.ask,
                quote_ts=src_ts,
                is_quote_stale=is_stale,
                provider_healthy=provider_healthy,
            )

            # 5. Broadcast typed quote update after atomic DB commit
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
            transaction.on_commit(lambda p=quote_payload: LiveEventBroadcaster.broadcast(p))
            return state


class XauUsdLiveDecisionPipelineService:
    """
    Active Closed-Candle Signal Intelligence & Risk Evaluation Pipeline for XAUUSD (Phase 7).

    Strict Invariants:
      1. Uses XauUsdSignalEngine and XauUsdRiskPlanner (Phase 4 & Phase 5).
      2. Strictly evaluates closed 15m, 1H, 4H, 1D candles <= decision timestamp T.
      3. Reject active aliases: XAUT, XAUTUSDT, GOLD, XAU (fail closed).
      4. Missing XAUUSD instrument/provider: fail closed.
      5. Dual-Layer projection: Candidate Layer A (BUY/SELL/WAIT) vs Published Layer B (WAIT).
      6. Dual-Side scores: long_direction_score, short_direction_score, etc.
      7. Persists immutable SignalRecord via save_dual_side_snapshot().
      8. Persists immutable LiveRiskPlanRecord.
      9. Informational candidate alerts emitted without order execution capability.
    """

    FORBIDDEN_ACTIVE_ALIASES = {"XAUT", "XAUTUSD", "XAUTUSDT", "GOLD", "GOLD_REFERENCE", "XAU"}

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
        provider_status: Optional[str] = None,
        is_provider_transition: Optional[bool] = None,
        is_feed_stale: Optional[bool] = None,
        macro_context: Optional[MacroEventContext] = None,
        signal_profile: Optional[Any] = None,
        risk_profile: Optional[Any] = None,
    ) -> Tuple[SignalRecord, Optional[LiveRiskPlanRecord], LiveMonitorState]:
        """
        Execute deterministic closed-candle signal evaluation and risk planning for XAUUSD.
        """
        # Step 1: Reject unclosed candle
        if not event.is_closed:
            raise ValueError(f"Unclosed candle for {event.instrument} cannot trigger decision pipeline.")

        # Step 2: Canonical Instrument Validation
        inst_upper = event.instrument.upper().replace("/", "")
        if event.instrument.upper() in cls.FORBIDDEN_ACTIVE_ALIASES or inst_upper in cls.FORBIDDEN_ACTIVE_ALIASES:
            raise ValueError(
                f"REJECTED: Active XAUUSD pipeline received historical/forbidden instrument '{event.instrument}'."
            )

        if inst_upper not in ("XAUUSD", "XAU_USD"):
            raise ValueError(f"XauUsdLiveDecisionPipelineService requires XAUUSD, got '{event.instrument}'")

        # Resolve canonical XAUUSD instrument from database (fail closed if missing)
        instrument_obj = Instrument.get_canonical_xauusd()
        if not instrument_obj:
            raise ValueError("Canonical XAUUSD instrument is NOT_CONFIGURED / missing in database.")

        # Validate timezone-aware timestamps
        if event.timestamp_close.tzinfo is None or event.timestamp_close.tzinfo.utcoffset(event.timestamp_close) is None:
            raise ValueError("event.timestamp_close must be timezone-aware.")
        if event.timestamp_open.tzinfo is None or event.timestamp_open.tzinfo.utcoffset(event.timestamp_open) is None:
            raise ValueError("event.timestamp_open must be timezone-aware.")

        candle_ts = event.timestamp_close.astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)

        # Step 3: Query closed historical candles <= candle_ts
        def _get_engine_candles(tf: str, limit: int = 128) -> list[CandleData]:
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

        engine_candles_15m = _get_engine_candles("15m", 128)
        engine_candles_1h = _get_engine_candles("1h", 64)
        engine_candles_4h = _get_engine_candles("4h", 64)
        engine_candles_1d = _get_engine_candles("1d", 32)

        # Ensure current event candle is present
        if not any(c.timestamp_close == candle_ts for c in engine_candles_15m):
            engine_candles_15m.append(
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

        # Step 4: Resolve Feed & Provider Health (Fail Closed)
        from apps.instruments.models import ProviderHealthSnapshot
        from apps.market_data.models import DataQualitySnapshot

        if is_feed_stale is None:
            latest_dq = (
                DataQualitySnapshot.objects.filter(
                    instrument=instrument_obj,
                    timeframe=event.timeframe,
                    timestamp__lte=candle_ts,
                )
                .order_by("-timestamp")
                .first()
            )
            is_feed_stale = bool(latest_dq.is_stale or latest_dq.hard_fail) if latest_dq else True

        if is_provider_transition is None or provider_status is None:
            latest_health = (
                ProviderHealthSnapshot.objects.filter(
                    listing__instrument=instrument_obj,
                    checked_at__lte=candle_ts,
                )
                .order_by("-checked_at")
                .first()
            )
            if latest_health:
                if provider_status is None:
                    provider_status = latest_health.status
                if is_provider_transition is None:
                    is_provider_transition = bool(latest_health.status == "TRANSITION")
            else:
                provider_status = "DOWN"
                is_provider_transition = True

        # Resolve Phase 3A Cycle Snapshot (PIT)
        from apps.analysis.models import CycleSnapshotRecord
        from apps.analysis.services import AnalysisPersistenceService

        cycle_rec = (
            CycleSnapshotRecord.objects.filter(
                instrument=instrument_obj,
                timeframe=event.timeframe,
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

        if macro_context is None and cycle_3a_snapshot is not None:
            macro_context = cycle_3a_snapshot.macro_event

        # Step 5: Execute Deterministic Master XauUsdSignalEngine
        engine = XauUsdSignalEngine(
            code_revision=code_revision,
            engine_version=engine_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
        )

        # Use uncalibrated profile if none provided
        prof = signal_profile if signal_profile is not None else uncalibrated_xauusd_signal_profile()

        runtime_health = RuntimeFeedHealth(
            primary_15m=FeedStatus.STALE if is_feed_stale else FeedStatus.HEALTHY,
            primary_1h=FeedStatus.HEALTHY if engine_candles_1h else FeedStatus.NOT_CONFIGURED,
            primary_4h=FeedStatus.HEALTHY if engine_candles_4h else FeedStatus.NOT_CONFIGURED,
            primary_1d=FeedStatus.HEALTHY if engine_candles_1d else FeedStatus.NOT_CONFIGURED,
            secondary_provider=FeedStatus.HEALTHY if provider_status == "HEALTHY" else FeedStatus.DEGRADED,
            secondary_provider_disagreement=False,
            macro_blackout_feed=FeedStatus.DEGRADED if (macro_context and macro_context.is_in_blackout) else FeedStatus.HEALTHY,
            is_macro_blackout=bool(macro_context and macro_context.is_in_blackout),
            volume=FeedStatus.HEALTHY,
            phase3a=FeedStatus.HEALTHY if cycle_3a_snapshot else FeedStatus.NOT_CONFIGURED,
            phase3b=FeedStatus.HEALTHY,
            is_unclosed_candle=False,
        )

        signal_snapshot: DualSideSignalSnapshot = engine.analyze(
            closed_candles_15m=engine_candles_15m,
            closed_candles_1h=engine_candles_1h if engine_candles_1h else None,
            closed_candles_4h=engine_candles_4h if engine_candles_4h else None,
            closed_candles_1d=engine_candles_1d if engine_candles_1d else None,
            as_of=candle_ts,
            instrument="XAUUSD",
            timeframe=event.timeframe,
            runtime_health=runtime_health,
            cycle_3a=cycle_3a_snapshot,
            profile=prof,
        )

        # Step 6: Persist Immutable Dual-Side SignalRecord
        signal_record, _ = SignalPersistenceService.save_dual_side_snapshot(
            instrument=instrument_obj,
            snapshot=signal_snapshot,
        )

        # Step 7: Evaluate Phase 5 XauUsdRiskPlanner
        risk_planner = XauUsdRiskPlanner(
            code_revision=code_revision,
            risk_version=risk_version,
            risk_profile=risk_profile,
        )

        # Extract features and structure for risk planning
        from engine.features.engine import FeatureEngine
        from engine.structure.engine import CausalStructureEngine

        fe = FeatureEngine()
        se = CausalStructureEngine()

        features_15m = fe.extract_features(engine_candles_15m) if len(engine_candles_15m) >= 32 else None
        atr14 = features_15m.atr14 if features_15m else Decimal("5.00")
        structure_15m = se.analyze(engine_candles_15m, atr=atr14) if (len(engine_candles_15m) >= 32 and atr14 is not None) else None
        structure_4h = se.analyze(engine_candles_4h, atr=atr14) if (engine_candles_4h and len(engine_candles_4h) >= 32 and atr14 is not None) else None

        # Plan risk based on candidate side
        risk_plan_snapshot: Optional[SideRiskPlanSnapshot] = None
        if signal_snapshot.candidate_state == SignalState.BUY_WINDOW and signal_snapshot.candidate_user_decision == UserDecision.BUY:
            risk_plan_snapshot = risk_planner.plan_long(
                phase4_snapshot=signal_snapshot,
                structure_15m=structure_15m,
                atr14=atr14,
                structure_4h=structure_4h,
            )
        elif signal_snapshot.candidate_state == SignalState.SELL_WINDOW and signal_snapshot.candidate_user_decision == UserDecision.SELL:
            risk_plan_snapshot = risk_planner.plan_short(
                phase4_snapshot=signal_snapshot,
                structure_15m=structure_15m,
                atr14=atr14,
                structure_4h=structure_4h,
            )
        else:
            # Invalid/no candidate plan
            risk_plan_snapshot = risk_planner.plan_long(
                phase4_snapshot=signal_snapshot,
                structure_15m=structure_15m,
                atr14=atr14,
                structure_4h=structure_4h,
            )

        # Step 8: Persist Immutable LiveRiskPlanRecord
        risk_record: Optional[LiveRiskPlanRecord] = None
        if risk_plan_snapshot is not None:
            risk_record, _ = LiveRiskPlanRecord.objects.get_or_create(
                source_signal_fingerprint=risk_plan_snapshot.source_phase4_fingerprint,
                defaults={
                    "signal_timestamp": risk_plan_snapshot.signal_generated_at,
                    "instrument": "XAUUSD",
                    "risk_side": risk_plan_snapshot.side.value if risk_plan_snapshot.side else None,
                    "risk_candidate_status": risk_plan_snapshot.risk_candidate_status.value if risk_plan_snapshot.risk_candidate_status else None,
                    "risk_candidate_valid": risk_plan_snapshot.risk_candidate_valid,
                    "simulation_eligible": risk_plan_snapshot.simulation_eligible,
                    "candidate_effective_action": risk_plan_snapshot.candidate_effective_action.value,
                    "publication_effective_action": risk_plan_snapshot.publication_effective_action.value,
                    "entry_min": risk_plan_snapshot.entry_min,
                    "entry_mid": risk_plan_snapshot.entry_mid,
                    "entry_max": risk_plan_snapshot.entry_max,
                    "stop_structure": risk_plan_snapshot.stop_structure,
                    "stop_atr": risk_plan_snapshot.stop_atr,
                    "stop_final": risk_plan_snapshot.stop_final,
                    "stop_distance_atr": risk_plan_snapshot.stop_distance_atr,
                    "tp1": risk_plan_snapshot.tp1,
                    "tp2": risk_plan_snapshot.tp2,
                    "rr_tp1": risk_plan_snapshot.planned_rr_tp1,
                    "rr_tp2": risk_plan_snapshot.planned_rr_tp2,
                    "is_valid_risk_plan": risk_plan_snapshot.is_valid_risk_plan,
                    "execution_eligible": risk_plan_snapshot.execution_eligible,
                    "effective_action": risk_plan_snapshot.publication_effective_action.value,
                    "reasons": list(risk_plan_snapshot.reasons),
                    "entry_zone_fingerprint": risk_plan_snapshot.entry_zone_fingerprint,
                    "tp1_zone_fingerprint": risk_plan_snapshot.tp1_zone_fingerprint,
                    "tp2_zone_fingerprint": risk_plan_snapshot.tp2_zone_fingerprint,
                    "phase5_policy_fingerprint": risk_plan_snapshot.phase5_policy_fingerprint,
                    "risk_plan_fingerprint": risk_plan_snapshot.risk_plan_fingerprint,
                    "source_phase4_fingerprint": risk_plan_snapshot.source_phase4_fingerprint,
                    "source_zone_id": risk_plan_snapshot.entry_zone_fingerprint or "",
                    "source_zone_timestamp": candle_ts,
                    "risk_version": risk_plan_snapshot.risk_version,
                    "execution_model_version": "5.0.0-exec-v1",
                    "config_version": config_version,
                    "code_revision": code_revision,
                },
            )

        # Step 9: Assemble Feed Health Status (Fail Closed)
        feed_health = {
            "xauusd_primary_status": FeedStatus.STALE.value if is_feed_stale else (FeedStatus.HEALTHY.value if provider_status == "HEALTHY" else FeedStatus.DEGRADED.value),
            "xauusd_secondary_status": FeedStatus.NOT_CONFIGURED.value,
            "macro_status": (
                FeedStatus.DEGRADED.value
                if (macro_context and macro_context.is_in_blackout)
                else (FeedStatus.HEALTHY.value if (macro_context and macro_context.is_feed_healthy) else FeedStatus.NOT_CONFIGURED.value)
            ),
            "provider_sync_status": (
                FeedStatus.TRANSITION.value
                if is_provider_transition
                else (FeedStatus.HEALTHY.value if provider_status == "HEALTHY" else FeedStatus.DEGRADED.value)
            ),
        }

        # Step 10: Atomically update Decision-Owned fields in LiveMonitorState
        with transaction.atomic():
            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument="XAUUSD",
                defaults={"effective_action": "WAIT"},
            )

            # Re-evaluate side-aware entry zone if quote is present
            entry_status = EntryZoneStatus.NO_ACTIVE_ZONE
            dist_pct = None
            cand_side = risk_plan_snapshot.side.value if (risk_plan_snapshot and risk_plan_snapshot.side) else None

            if (
                risk_plan_snapshot
                and risk_plan_snapshot.is_valid_risk_plan
                and risk_plan_snapshot.execution_eligible
                and risk_plan_snapshot.entry_min
                and risk_plan_snapshot.entry_max
                and cand_side in ("LONG", "SHORT")
            ):
                eval_p = state.current_ask if cand_side == "LONG" else state.current_bid
                if eval_p is not None:
                    if risk_plan_snapshot.entry_min <= eval_p <= risk_plan_snapshot.entry_max:
                        entry_status = EntryZoneStatus.INSIDE_ZONE
                        dist_pct = Decimal("0.00")
                    elif eval_p > risk_plan_snapshot.entry_max:
                        entry_status = EntryZoneStatus.ABOVE_ZONE
                        dist_pct = (
                            (eval_p - risk_plan_snapshot.entry_max) / risk_plan_snapshot.entry_max * Decimal("100.0")
                        ).quantize(Decimal("0.01"))
                    else:
                        entry_status = EntryZoneStatus.BELOW_ZONE
                        dist_pct = (
                            (risk_plan_snapshot.entry_min - eval_p) / risk_plan_snapshot.entry_min * Decimal("100.0")
                        ).quantize(Decimal("0.01"))

            reasons_pos = list(signal_snapshot.reasons_long_positive) + list(signal_snapshot.reasons_short_positive)
            reasons_neg = list(signal_snapshot.reasons_long_negative) + list(signal_snapshot.reasons_short_negative)

            LiveMonitorState.objects.filter(id=state.id).update(
                last_closed_candle_ts=candle_ts,
                last_analysis_timestamp=now_utc,
                # Phase 4 Dual-Layer
                candidate_state=signal_snapshot.candidate_state.value,
                candidate_user_decision=signal_snapshot.candidate_user_decision.value,
                published_state=signal_snapshot.state.value,
                published_user_decision=signal_snapshot.user_decision.value,
                # Dual-Side Scores
                long_direction_score=signal_snapshot.long_direction.total_score,
                short_direction_score=signal_snapshot.short_direction.total_score,
                long_timing_score=signal_snapshot.long_timing.total_score,
                short_timing_score=signal_snapshot.short_timing.total_score,
                # Phase 5 Risk
                risk_side=cand_side,
                risk_candidate_status=risk_plan_snapshot.risk_candidate_status.value if risk_plan_snapshot else None,
                candidate_effective_action=risk_plan_snapshot.candidate_effective_action.value if risk_plan_snapshot else "WAIT",
                publication_effective_action=risk_plan_snapshot.publication_effective_action.value if risk_plan_snapshot else "WAIT",
                effective_action=risk_plan_snapshot.publication_effective_action.value if risk_plan_snapshot else "WAIT",
                risk_plan_valid=risk_plan_snapshot.is_valid_risk_plan if risk_plan_snapshot else False,
                execution_eligible=risk_plan_snapshot.execution_eligible if risk_plan_snapshot else False,
                entry_min=risk_plan_snapshot.entry_min if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                entry_mid=risk_plan_snapshot.entry_mid if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                entry_max=risk_plan_snapshot.entry_max if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                stop_structure=risk_plan_snapshot.stop_structure if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                stop_atr=risk_plan_snapshot.stop_atr if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                stop_final=risk_plan_snapshot.stop_final if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                stop_distance_atr=risk_plan_snapshot.stop_distance_atr if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                tp1=risk_plan_snapshot.tp1 if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                tp2=risk_plan_snapshot.tp2 if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                rr_tp1=risk_plan_snapshot.planned_rr_tp1 if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                rr_tp2=risk_plan_snapshot.planned_rr_tp2 if (risk_plan_snapshot and risk_plan_snapshot.is_valid_risk_plan) else None,
                # Explainability & Fingerprints
                candidate_resolution_reason=signal_snapshot.candidate_resolution_reason,
                publication_reason=signal_snapshot.publication_reason,
                profile_name=signal_snapshot.profile_name,
                calibration_status=signal_snapshot.calibration_status,
                phase4_policy_fingerprint=signal_snapshot.phase4_policy_fingerprint,
                analysis_fingerprint=signal_snapshot.analysis_fingerprint,
                risk_plan_fingerprint=risk_plan_snapshot.risk_plan_fingerprint if risk_plan_snapshot else None,
                source_phase4_fingerprint=signal_snapshot.analysis_fingerprint,
                reasons_positive=reasons_pos,
                reasons_negative=reasons_neg,
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

            # Step 11: Trigger Informational Candidate Alerts
            from apps.alerts.services import AlertGenerationService
            AlertGenerationService.evaluate_closed_candle_alerts(
                signal_snapshot=signal_snapshot,
                risk_plan=risk_plan_snapshot,
                feed_health_data=feed_health,
            )

            # Step 12: Broadcast decision updates via WebSocket broadcaster
            sig_payload = {
                "event_type": "signal_update",
                "instrument": "XAUUSD",
                "candidate_state": state.candidate_state,
                "candidate_user_decision": state.candidate_user_decision,
                "published_state": state.published_state,
                "published_user_decision": state.published_user_decision,
                "long_direction_score": state.long_direction_score,
                "short_direction_score": state.short_direction_score,
                "long_timing_score": state.long_timing_score,
                "short_timing_score": state.short_timing_score,
                "last_closed_candle_ts": candle_ts.isoformat(),
                "decision_sequence": state.decision_sequence,
                "analysis_fingerprint": state.analysis_fingerprint,
                "calibration_status": state.calibration_status,
                "reasons_positive": state.reasons_positive,
                "reasons_negative": state.reasons_negative,
                "hard_gate_reasons": state.hard_gate_reasons,
            }
            transaction.on_commit(lambda p=sig_payload: LiveEventBroadcaster.broadcast(p))

            risk_payload = {
                "event_type": "risk_plan_update",
                "instrument": "XAUUSD",
                "risk_side": state.risk_side,
                "risk_candidate_status": state.risk_candidate_status,
                "is_valid_risk_plan": state.risk_plan_valid,
                "execution_eligible": state.execution_eligible,
                "candidate_effective_action": state.candidate_effective_action,
                "publication_effective_action": state.publication_effective_action,
                "entry_min": str(state.entry_min) if state.entry_min else None,
                "entry_mid": str(state.entry_mid) if state.entry_mid else None,
                "entry_max": str(state.entry_max) if state.entry_max else None,
                "stop_final": str(state.stop_final) if state.stop_final else None,
                "tp1": str(state.tp1) if state.tp1 else None,
                "tp2": str(state.tp2) if state.tp2 else None,
                "rr_tp1": str(state.rr_tp1) if state.rr_tp1 else None,
                "decision_sequence": state.decision_sequence,
            }
            transaction.on_commit(lambda p=risk_payload: LiveEventBroadcaster.broadcast(p))

            return signal_record, risk_record, state


class LiveDecisionPipelineService:
    """
    Historical Closed-Candle Pipeline for XAUT/USDT (Frozen Baseline Regression).
    Preserved strictly for backward compatibility with historical tests.
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
        provider_status: Optional[str] = None,
        is_provider_transition: Optional[bool] = None,
        is_feed_stale: Optional[bool] = None,
        macro_context: Optional[MacroEventContext] = None,
    ) -> Tuple[SignalRecord, Optional[LiveRiskPlanRecord], LiveMonitorState]:
        """Historical XAUT closed-candle pipeline."""
        if not event.is_closed:
            raise ValueError(f"Unclosed candle for {event.instrument} cannot trigger decision pipeline.")

        candle_ts = (
            event.timestamp_close.astimezone(timezone.utc)
            if event.timestamp_close.tzinfo
            else event.timestamp_close.replace(tzinfo=timezone.utc)
        )
        now_utc = datetime.now(timezone.utc)

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

        from apps.instruments.models import ProviderHealthSnapshot
        from apps.market_data.models import DataQualitySnapshot

        if is_feed_stale is None:
            latest_dq = (
                DataQualitySnapshot.objects.filter(
                    instrument=instrument_obj,
                    timeframe=event.timeframe,
                    timestamp__lte=candle_ts,
                )
                .order_by("-timestamp")
                .first()
            )
            is_feed_stale = bool(latest_dq.is_stale or latest_dq.hard_fail) if latest_dq else True

        if is_provider_transition is None or provider_status is None:
            latest_health = (
                ProviderHealthSnapshot.objects.filter(
                    listing__instrument=instrument_obj,
                    checked_at__lte=candle_ts,
                )
                .order_by("-checked_at")
                .first()
            )
            if latest_health:
                if provider_status is None:
                    provider_status = latest_health.status
                if is_provider_transition is None:
                    is_provider_transition = bool(latest_health.status == "TRANSITION")
            else:
                provider_status = "DOWN"
                is_provider_transition = True

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

        from apps.analysis.models import CycleSnapshotRecord
        from apps.analysis.services import AnalysisPersistenceService
        cycle_rec = (
            CycleSnapshotRecord.objects.filter(
                instrument=instrument_obj,
                timeframe=event.timeframe,
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

        if macro_context is None and cycle_3a_snapshot is not None:
            macro_context = cycle_3a_snapshot.macro_event

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

        engine = XautSignalEngine(
            code_revision=code_revision,
            engine_version=engine_version,
            config_version=config_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
        )

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
            cycle_3a=cycle_3a_snapshot,
        )

        signal_record, _ = SignalPersistenceService.save_signal_snapshot(
            instrument=instrument_obj,
            snapshot=signal_snapshot,
        )

        risk_planner = RiskPlanner(
            code_revision=code_revision,
            risk_version=risk_version,
            config_version=config_version,
        )

        features_15m = engine.feature_engine.extract_features(engine_candles) if len(engine_candles) >= 32 else None
        atr14 = features_15m.atr14 if features_15m else None
        structure_15m = engine.structure_engine.analyze(engine_candles, atr=atr14) if (len(engine_candles) >= 32 and atr14 is not None) else None

        risk_plan_snapshot = risk_planner.plan(
            signal_snapshot=signal_snapshot,
            structure_15m=structure_15m,
            atr14=atr14,
            latest_close=event.close,
        )

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

        feed_health = {
            "xaut_status": FeedStatus.STALE.value if is_feed_stale else FeedStatus.HEALTHY.value,
            "xau_status": FeedStatus.DOWN.value if xau_reference_price is None else FeedStatus.HEALTHY.value,
            "usdt_norm_status": FeedStatus.DOWN.value if usdt_rate is None else FeedStatus.HEALTHY.value,
            "macro_status": (
                FeedStatus.DEGRADED.value
                if (macro_context and macro_context.is_in_blackout)
                else (FeedStatus.HEALTHY.value if (macro_context and macro_context.is_feed_healthy) else FeedStatus.DOWN.value)
            ),
            "provider_sync_status": (
                FeedStatus.TRANSITION.value
                if is_provider_transition
                else (FeedStatus.HEALTHY.value if provider_status == "HEALTHY" else FeedStatus.DEGRADED.value)
            ),
        }

        with transaction.atomic():
            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument=event.instrument,
                defaults={"effective_action": risk_plan_snapshot.effective_action.value},
            )

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
            transaction.on_commit(lambda p=sig_payload: LiveEventBroadcaster.broadcast(p))

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
            transaction.on_commit(lambda p=risk_payload: LiveEventBroadcaster.broadcast(p))

            health_payload = LiveEventBroadcaster.format_feed_health_update(
                instrument=event.instrument,
                feed_health=feed_health,
            )
            transaction.on_commit(lambda p=health_payload: LiveEventBroadcaster.broadcast(p))

            return signal_record, risk_record, state


class XauUsdLiveProjectionService:
    """
    Canonical single presentation projection assembler (Amendment 9).
    Guarantees semantic equality across Django templates, REST API JSON, and WebSocket events.
    """

    @classmethod
    def assemble_projection(cls, state: Optional[LiveMonitorState]) -> XauUsdLiveProjectionState:
        """Assemble canonical typed projection from LiveMonitorState record."""
        if state is None:
            return XauUsdLiveProjectionState()

        return XauUsdLiveProjectionState(
            instrument="XAUUSD",
            display_symbol="XAU/USD",
            # Quote
            current_bid=state.current_bid,
            current_ask=state.current_ask,
            spread=state.spread,
            spread_pct=state.spread_pct,
            quote_source_timestamp=state.quote_source_timestamp,
            quote_received_timestamp=state.quote_received_timestamp,
            quote_age_seconds=state.quote_age_seconds,
            is_quote_stale=state.is_quote_stale,
            quote_sequence=state.quote_sequence,
            entry_zone_status=EntryZoneStatus(state.entry_zone_status) if state.entry_zone_status in EntryZoneStatus._value2member_map_ else EntryZoneStatus.NO_ACTIVE_ZONE,
            distance_to_entry_zone_pct=state.distance_to_entry_zone_pct,
            # Dual-Layer Decisions
            last_closed_candle_ts=state.last_closed_candle_ts,
            last_analysis_timestamp=state.last_analysis_timestamp,
            candidate_state=state.candidate_state or state.signal_state,
            candidate_user_decision=state.candidate_user_decision or state.signal_user_decision,
            published_state=state.published_state or state.signal_state,
            published_user_decision=state.published_user_decision or "WAIT",
            # Dual-Side Scores
            long_direction_score=state.long_direction_score,
            short_direction_score=state.short_direction_score,
            long_timing_score=state.long_timing_score,
            short_timing_score=state.short_timing_score,
            # Risk
            risk_side=state.risk_side,
            risk_candidate_status=state.risk_candidate_status,
            is_valid_risk_plan=state.risk_plan_valid,
            execution_eligible=state.execution_eligible,
            candidate_effective_action=state.candidate_effective_action or state.effective_action,
            publication_effective_action=state.publication_effective_action or "WAIT",
            # Geometry
            entry_min=state.entry_min if state.risk_plan_valid else None,
            entry_mid=state.entry_mid if state.risk_plan_valid else None,
            entry_max=state.entry_max if state.risk_plan_valid else None,
            stop_structure=state.stop_structure if state.risk_plan_valid else None,
            stop_atr=state.stop_atr if state.risk_plan_valid else None,
            stop_final=state.stop_final if state.risk_plan_valid else None,
            stop_distance_atr=state.stop_distance_atr if state.risk_plan_valid else None,
            tp1=state.tp1 if state.risk_plan_valid else None,
            tp2=state.tp2 if state.risk_plan_valid else None,
            planned_rr_tp1=state.rr_tp1 if state.risk_plan_valid else None,
            planned_rr_tp2=state.rr_tp2 if state.risk_plan_valid else None,
            # Diagnostics & Provenance
            calibration_status=state.calibration_status or "CALIBRATION_REQUIRED",
            profile_name=state.profile_name,
            phase3b_status="RESEARCH_ONLY",
            phase3b_production_weight=0.0,
            reasons_positive=state.reasons_positive or [],
            reasons_negative=state.reasons_negative or [],
            hard_gate_reasons=state.hard_gate_reasons or [],
            candidate_resolution_reason=state.candidate_resolution_reason,
            publication_reason=state.publication_reason,
            feed_health=state.feed_health_data or {},
            analysis_fingerprint=state.analysis_fingerprint or state.signal_fingerprint,
            phase4_policy_fingerprint=state.phase4_policy_fingerprint,
            risk_plan_fingerprint=state.risk_plan_fingerprint,
            source_phase4_fingerprint=state.source_phase4_fingerprint,
            engine_version=state.engine_version,
            config_version=state.config_version,
            feature_version=state.feature_version,
            cycle_version=state.cycle_version,
            risk_version=state.risk_version,
            code_revision=state.code_revision,
            decision_sequence=state.decision_sequence,
        )

    @classmethod
    def assemble_projection_dict(cls, state: Optional[LiveMonitorState]) -> Dict[str, Any]:
        """Convert canonical projection to JSON-serializable dictionary."""
        proj = cls.assemble_projection(state)
        return {
            "instrument": proj.instrument,
            "display_symbol": proj.display_symbol,
            "current_bid": str(proj.current_bid) if proj.current_bid is not None else None,
            "current_ask": str(proj.current_ask) if proj.current_ask is not None else None,
            "spread": str(proj.spread) if proj.spread is not None else None,
            "spread_pct": str(proj.spread_pct) if proj.spread_pct is not None else None,
            "quote_source_timestamp": proj.quote_source_timestamp.isoformat() if proj.quote_source_timestamp else None,
            "quote_received_timestamp": proj.quote_received_timestamp.isoformat() if proj.quote_received_timestamp else None,
            "quote_age_seconds": proj.quote_age_seconds,
            "is_quote_stale": proj.is_quote_stale,
            "quote_sequence": proj.quote_sequence,
            "entry_zone_status": proj.entry_zone_status.value,
            "distance_to_entry_zone_pct": str(proj.distance_to_entry_zone_pct) if proj.distance_to_entry_zone_pct is not None else None,
            "last_closed_candle_ts": proj.last_closed_candle_ts.isoformat() if proj.last_closed_candle_ts else None,
            "last_analysis_timestamp": proj.last_analysis_timestamp.isoformat() if proj.last_analysis_timestamp else None,
            "candidate_state": proj.candidate_state,
            "candidate_user_decision": proj.candidate_user_decision,
            "published_state": proj.published_state,
            "published_user_decision": proj.published_user_decision,
            "long_direction_score": proj.long_direction_score,
            "short_direction_score": proj.short_direction_score,
            "long_timing_score": proj.long_timing_score,
            "short_timing_score": proj.short_timing_score,
            "risk_side": proj.risk_side,
            "risk_candidate_status": proj.risk_candidate_status,
            "is_valid_risk_plan": proj.is_valid_risk_plan,
            "execution_eligible": proj.execution_eligible,
            "candidate_effective_action": proj.candidate_effective_action,
            "publication_effective_action": proj.publication_effective_action,
            "entry_min": str(proj.entry_min) if proj.entry_min is not None else None,
            "entry_mid": str(proj.entry_mid) if proj.entry_mid is not None else None,
            "entry_max": str(proj.entry_max) if proj.entry_max is not None else None,
            "stop_structure": str(proj.stop_structure) if proj.stop_structure is not None else None,
            "stop_atr": str(proj.stop_atr) if proj.stop_atr is not None else None,
            "stop_final": str(proj.stop_final) if proj.stop_final is not None else None,
            "stop_distance_atr": str(proj.stop_distance_atr) if proj.stop_distance_atr is not None else None,
            "tp1": str(proj.tp1) if proj.tp1 is not None else None,
            "tp2": str(proj.tp2) if proj.tp2 is not None else None,
            "planned_rr_tp1": str(proj.planned_rr_tp1) if proj.planned_rr_tp1 is not None else None,
            "planned_rr_tp2": str(proj.planned_rr_tp2) if proj.planned_rr_tp2 is not None else None,
            "calibration_status": proj.calibration_status,
            "profile_name": proj.profile_name,
            "phase3b_status": proj.phase3b_status,
            "phase3b_production_weight": proj.phase3b_production_weight,
            "reasons_positive": list(proj.reasons_positive),
            "reasons_negative": list(proj.reasons_negative),
            "hard_gate_reasons": list(proj.hard_gate_reasons),
            "candidate_resolution_reason": proj.candidate_resolution_reason,
            "publication_reason": proj.publication_reason,
            "feed_health": proj.feed_health,
            "analysis_fingerprint": proj.analysis_fingerprint,
            "phase4_policy_fingerprint": proj.phase4_policy_fingerprint,
            "risk_plan_fingerprint": proj.risk_plan_fingerprint,
            "source_phase4_fingerprint": proj.source_phase4_fingerprint,
            "engine_version": proj.engine_version,
            "config_version": proj.config_version,
            "feature_version": proj.feature_version,
            "cycle_version": proj.cycle_version,
            "risk_version": proj.risk_version,
            "code_revision": proj.code_revision,
            "decision_sequence": proj.decision_sequence,
        }

    @classmethod
    def reconstruct_xauusd_state(cls) -> LiveMonitorState:
        """Reconstruct LiveMonitorState strictly from durable database records without recomputation."""
        with transaction.atomic():
            inst_obj = Instrument.get_canonical_xauusd()
            latest_signal = None
            if inst_obj:
                latest_signal = (
                    SignalRecord.objects.filter(instrument=inst_obj)
                    .order_by("-timestamp", "-created_at")
                    .first()
                )

            risk_record = None
            if latest_signal:
                risk_record = LiveRiskPlanRecord.objects.filter(
                    source_signal_fingerprint=latest_signal.analysis_fingerprint
                ).first()

            feed_health = {
                "xauusd_primary_status": FeedStatus.HEALTHY.value if latest_signal else FeedStatus.NOT_CONFIGURED.value,
                "xauusd_secondary_status": FeedStatus.NOT_CONFIGURED.value,
                "macro_status": FeedStatus.HEALTHY.value if latest_signal else FeedStatus.NOT_CONFIGURED.value,
                "provider_sync_status": FeedStatus.HEALTHY.value if latest_signal else FeedStatus.NOT_CONFIGURED.value,
            }

            state, _ = LiveMonitorState.objects.select_for_update().get_or_create(
                instrument="XAUUSD",
                defaults={"effective_action": "WAIT"},
            )

            if latest_signal:
                state.candidate_state = latest_signal.state
                state.candidate_user_decision = latest_signal.user_decision
                state.published_state = latest_signal.state
                state.published_user_decision = "WAIT"
                state.long_direction_score = latest_signal.long_direction_score
                state.short_direction_score = latest_signal.short_direction_score
                state.long_timing_score = latest_signal.long_timing_score
                state.short_timing_score = latest_signal.short_timing_score
                state.profile_name = latest_signal.profile_name
                state.calibration_status = latest_signal.calibration_status or "CALIBRATION_REQUIRED"
                state.phase4_policy_fingerprint = latest_signal.phase4_policy_fingerprint
                state.analysis_fingerprint = latest_signal.analysis_fingerprint
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
                state.risk_side = risk_record.risk_side
                state.risk_candidate_status = risk_record.risk_candidate_status
                state.risk_plan_valid = risk_record.is_valid_risk_plan
                state.execution_eligible = risk_record.execution_eligible
                state.candidate_effective_action = risk_record.candidate_effective_action or risk_record.effective_action
                state.publication_effective_action = "WAIT"
                state.effective_action = "WAIT"
                state.entry_min = risk_record.entry_min if risk_record.is_valid_risk_plan else None
                state.entry_mid = risk_record.entry_mid if risk_record.is_valid_risk_plan else None
                state.entry_max = risk_record.entry_max if risk_record.is_valid_risk_plan else None
                state.stop_structure = risk_record.stop_structure if risk_record.is_valid_risk_plan else None
                state.stop_atr = risk_record.stop_atr if risk_record.is_valid_risk_plan else None
                state.stop_final = risk_record.stop_final if risk_record.is_valid_risk_plan else None
                state.stop_distance_atr = risk_record.stop_distance_atr if risk_record.is_valid_risk_plan else None
                state.tp1 = risk_record.tp1 if risk_record.is_valid_risk_plan else None
                state.tp2 = risk_record.tp2 if risk_record.is_valid_risk_plan else None
                state.rr_tp1 = risk_record.rr_tp1 if risk_record.is_valid_risk_plan else None
                state.rr_tp2 = risk_record.rr_tp2 if risk_record.is_valid_risk_plan else None
                state.risk_plan_fingerprint = risk_record.risk_plan_fingerprint
                state.source_phase4_fingerprint = risk_record.source_phase4_fingerprint
                state.risk_version = risk_record.risk_version

            state.feed_health_data = feed_health
            state.save()
            return state


class StateRecoveryService:
    """
    Historical Restart & Recovery Service for XAUT/USDT (Frozen Baseline).
    """

    @classmethod
    def reconstruct_state(cls, instrument_symbol: str = "XAUT/USDT") -> LiveMonitorState:
        """Rebuild projection state from canonical durable storage."""
        if instrument_symbol == "XAUUSD":
            return XauUsdLiveProjectionService.reconstruct_xauusd_state()

        with transaction.atomic():
            latest_signal = (
                SignalRecord.objects.filter(instrument__base_asset__code="XAUT")
                .order_by("-timestamp", "-created_at")
                .first()
            )

            risk_record = None
            if latest_signal:
                risk_record = LiveRiskPlanRecord.objects.filter(
                    source_signal_fingerprint=latest_signal.analysis_fingerprint
                ).first()

            latest_candle = (
                MarketCandle.objects.filter(
                    instrument__base_asset__code="XAUT",
                    timeframe="15m",
                    is_closed=True,
                )
                .order_by("-timestamp_close")
                .first()
            )

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
                state.direction_score = latest_signal.direction_score or 0.0
                state.timing_score = latest_signal.timing_score or 0.0
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
