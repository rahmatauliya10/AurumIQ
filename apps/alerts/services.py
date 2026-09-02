"""Alert generation, idempotency, suppression, and outbox dispatch service (Phase 7)."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import structlog
from django.db import transaction

from apps.alerts.models import AlertEvent, AlertStatus
from apps.alerts.transport import AlertTransportDispatcher
from apps.alerts.types import (
    AlertEventType,
    AlertPayload,
    CANONICAL_DISCLAIMER,
)
from apps.live_monitor.models import LiveMonitorState
from apps.live_monitor.types import EntryZoneStatus, FeedStatus
from engine.core.types import DualSideSignalSnapshot, SideRiskPlanSnapshot, SignalState, UserDecision

logger = structlog.get_logger(__name__)


class AlertGenerationService:
    """
    Evaluates market intelligence and quote events to emit typed informational alerts.
    
    Strict Invariants:
      1. Zero order placement / execution instructions.
      2. Deterministic idempotency based on material context and state transition (P7-20).
      3. Candidate alerts work while published_user_decision is WAIT (Amendment 6).
      4. Suppression rules: Stale/unhealthy/not_configured quote suppresses proximity/zone alerts (P7-18).
      5. SYSTEM_SAFETY_HOLD suppresses proximity alerts.
    """

    @classmethod
    def generate_event_id(
        cls,
        instrument: str,
        event_type: str,
        context_key: str,
    ) -> str:
        """Generate deterministic SHA-256 event ID for idempotency."""
        raw = f"{instrument}:{event_type}:{context_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def evaluate_closed_candle_alerts(
        cls,
        signal_snapshot: DualSideSignalSnapshot,
        risk_plan: Optional[SideRiskPlanSnapshot],
        feed_health_data: Optional[Dict[str, Any]] = None,
    ) -> List[AlertEvent]:
        """
        Evaluate closed-candle intelligence and generate informational candidate alerts.
        """
        emitted_alerts: List[AlertEvent] = []
        inst = signal_snapshot.instrument
        sig_fp = signal_snapshot.analysis_fingerprint
        risk_fp = risk_plan.risk_plan_fingerprint if risk_plan else "NO_RISK_PLAN"

        # Check macro blackout
        if signal_snapshot.hard_gate.runtime_health.is_macro_blackout:
            event_id = cls.generate_event_id(inst, AlertEventType.MACRO_BLACKOUT_ACTIVE.value, sig_fp)
            alert = cls._create_or_get_alert(
                event_id=event_id,
                event_type=AlertEventType.MACRO_BLACKOUT_ACTIVE,
                signal_snapshot=signal_snapshot,
                risk_plan=risk_plan,
            )
            if alert:
                emitted_alerts.append(alert)

        # Check safety hold (hard gates active / unclosed candle / missing data)
        if signal_snapshot.hard_gate.is_blocked or signal_snapshot.hard_gate.runtime_health.is_unclosed_candle:
            event_id = cls.generate_event_id(inst, AlertEventType.SYSTEM_SAFETY_HOLD.value, sig_fp)
            alert = cls._create_or_get_alert(
                event_id=event_id,
                event_type=AlertEventType.SYSTEM_SAFETY_HOLD,
                signal_snapshot=signal_snapshot,
                risk_plan=risk_plan,
            )
            if alert:
                emitted_alerts.append(alert)

        # Check candidate setups (Amendment 6: Candidate alerts work while published is WAIT)
        cand_state = signal_snapshot.candidate_state
        cand_decision = signal_snapshot.candidate_user_decision

        if cand_state == SignalState.CONFLICT:
            event_id = cls.generate_event_id(inst, AlertEventType.CONFLICT.value, sig_fp)
            alert = cls._create_or_get_alert(
                event_id=event_id,
                event_type=AlertEventType.CONFLICT,
                signal_snapshot=signal_snapshot,
                risk_plan=risk_plan,
            )
            if alert:
                emitted_alerts.append(alert)

        elif cand_state == SignalState.BUY_WINDOW and cand_decision == UserDecision.BUY:
            event_id = cls.generate_event_id(inst, AlertEventType.BUY_WINDOW_CANDIDATE.value, f"{sig_fp}:{risk_fp}")
            alert = cls._create_or_get_alert(
                event_id=event_id,
                event_type=AlertEventType.BUY_WINDOW_CANDIDATE,
                signal_snapshot=signal_snapshot,
                risk_plan=risk_plan,
                side="LONG",
            )
            if alert:
                emitted_alerts.append(alert)

        elif cand_state == SignalState.SELL_WINDOW and cand_decision == UserDecision.SELL:
            event_id = cls.generate_event_id(inst, AlertEventType.SELL_WINDOW_CANDIDATE.value, f"{sig_fp}:{risk_fp}")
            alert = cls._create_or_get_alert(
                event_id=event_id,
                event_type=AlertEventType.SELL_WINDOW_CANDIDATE,
                signal_snapshot=signal_snapshot,
                risk_plan=risk_plan,
                side="SHORT",
            )
            if alert:
                emitted_alerts.append(alert)

        elif cand_state == SignalState.READY:
            # Check which side is ready
            long_dir = signal_snapshot.long_direction.total_score
            short_dir = signal_snapshot.short_direction.total_score
            if long_dir > short_dir:
                event_id = cls.generate_event_id(inst, AlertEventType.READY_LONG.value, sig_fp)
                alert = cls._create_or_get_alert(
                    event_id=event_id,
                    event_type=AlertEventType.READY_LONG,
                    signal_snapshot=signal_snapshot,
                    risk_plan=risk_plan,
                    side="LONG",
                )
            else:
                event_id = cls.generate_event_id(inst, AlertEventType.READY_SHORT.value, sig_fp)
                alert = cls._create_or_get_alert(
                    event_id=event_id,
                    event_type=AlertEventType.READY_SHORT,
                    signal_snapshot=signal_snapshot,
                    risk_plan=risk_plan,
                    side="SHORT",
                )
            if alert:
                emitted_alerts.append(alert)

        elif cand_state == SignalState.WATCH:
            long_dir = signal_snapshot.long_direction.total_score
            short_dir = signal_snapshot.short_direction.total_score
            if long_dir >= short_dir:
                event_id = cls.generate_event_id(inst, AlertEventType.WATCH_LONG_CREATED.value, sig_fp)
                alert = cls._create_or_get_alert(
                    event_id=event_id,
                    event_type=AlertEventType.WATCH_LONG_CREATED,
                    signal_snapshot=signal_snapshot,
                    risk_plan=risk_plan,
                    side="LONG",
                )
            else:
                event_id = cls.generate_event_id(inst, AlertEventType.WATCH_SHORT_CREATED.value, sig_fp)
                alert = cls._create_or_get_alert(
                    event_id=event_id,
                    event_type=AlertEventType.WATCH_SHORT_CREATED,
                    signal_snapshot=signal_snapshot,
                    risk_plan=risk_plan,
                    side="SHORT",
                )
            if alert:
                emitted_alerts.append(alert)

        return emitted_alerts

    @classmethod
    def evaluate_live_quote_alerts(
        cls,
        state: LiveMonitorState,
        bid: Decimal,
        ask: Decimal,
        quote_ts: datetime,
        is_quote_stale: bool,
        provider_healthy: bool,
    ) -> List[AlertEvent]:
        """
        Evaluate real-time quote against current state for proximity and invalidation alerts.
        Applies strict fail-closed suppression rules.
        """
        emitted: List[AlertEvent] = []
        inst = state.instrument

        # 1. Check Infrastructure & Freshness Alerts
        if is_quote_stale:
            # Emit LIVE_DATA_STALE (deduplicated by minute or analysis ts)
            ts_key = quote_ts.strftime("%Y-%m-%d-%H-%M")
            event_id = cls.generate_event_id(inst, AlertEventType.LIVE_DATA_STALE.value, ts_key)
            alert = cls._create_or_get_quote_alert(
                event_id=event_id,
                event_type=AlertEventType.LIVE_DATA_STALE,
                state=state,
                bid=bid,
                ask=ask,
                quote_ts=quote_ts,
            )
            if alert:
                emitted.append(alert)

        if not provider_healthy:
            ts_key = quote_ts.strftime("%Y-%m-%d-%H-%M")
            event_id = cls.generate_event_id(inst, AlertEventType.PROVIDER_UNHEALTHY.value, ts_key)
            alert = cls._create_or_get_quote_alert(
                event_id=event_id,
                event_type=AlertEventType.PROVIDER_UNHEALTHY,
                state=state,
                bid=bid,
                ask=ask,
                quote_ts=quote_ts,
            )
            if alert:
                emitted.append(alert)

        # 2. Strict Suppression Gate: If quote stale or provider unhealthy or safety hold, suppress zone alerts
        if is_quote_stale or not provider_healthy:
            logger.debug(
                "zone_alerts_suppressed_feed_unhealthy",
                is_stale=is_quote_stale,
                provider_healthy=provider_healthy,
            )
            return emitted

        # If risk plan is not valid or not execution eligible, suppress proximity alerts
        if not state.risk_plan_valid or not state.execution_eligible:
            return emitted

        if not state.entry_min or not state.entry_max:
            return emitted

        # 3. Side-Aware Entry Zone Monitoring (Amendment 6: LONG uses ASK, SHORT uses BID)
        side = state.risk_side or ("LONG" if state.candidate_effective_action == "BUY" else ("SHORT" if state.candidate_effective_action == "SELL" else None))
        if not side:
            return emitted

        eval_price = ask if side == "LONG" else bid
        inside_zone = state.entry_min <= eval_price <= state.entry_max

        if inside_zone:
            # Emit ENTRY_ZONE_REACHED keyed by signal_fingerprint + risk_plan_fingerprint
            sig_key = state.signal_fingerprint or state.analysis_fingerprint or "P4"
            risk_key = state.risk_plan_fingerprint or "P5"
            event_id = cls.generate_event_id(inst, AlertEventType.ENTRY_ZONE_REACHED.value, f"{sig_key}:{risk_key}:{side}")
            alert = cls._create_or_get_quote_alert(
                event_id=event_id,
                event_type=AlertEventType.ENTRY_ZONE_REACHED,
                state=state,
                bid=bid,
                ask=ask,
                quote_ts=quote_ts,
                side=side,
            )
            if alert:
                emitted.append(alert)

        # 4. Invalidation Monitoring (Informational Only — NO STOP ORDER PLACED)
        if state.stop_final:
            is_invalidated = False
            if side == "LONG" and bid <= state.stop_final:
                is_invalidated = True
            elif side == "SHORT" and ask >= state.stop_final:
                is_invalidated = True

            if is_invalidated:
                sig_key = state.signal_fingerprint or state.analysis_fingerprint or "P4"
                risk_key = state.risk_plan_fingerprint or "P5"
                event_id = cls.generate_event_id(inst, AlertEventType.INVALIDATION_TOUCHED.value, f"{sig_key}:{risk_key}:{side}")
                alert = cls._create_or_get_quote_alert(
                    event_id=event_id,
                    event_type=AlertEventType.INVALIDATION_TOUCHED,
                    state=state,
                    bid=bid,
                    ask=ask,
                    quote_ts=quote_ts,
                    side=side,
                )
                if alert:
                    emitted.append(alert)

        return emitted

    @classmethod
    def _create_or_get_alert(
        cls,
        event_id: str,
        event_type: AlertEventType,
        signal_snapshot: DualSideSignalSnapshot,
        risk_plan: Optional[SideRiskPlanSnapshot],
        side: Optional[str] = None,
    ) -> Optional[AlertEvent]:
        """Idempotently create and persist AlertEvent from closed candle."""
        with transaction.atomic():
            existing = AlertEvent.objects.filter(event_id=event_id).first()
            if existing:
                return None  # Idempotent skip

            # Build canonical AlertPayload
            payload = AlertPayload(
                event_id=event_id,
                event_type=event_type,
                instrument=signal_snapshot.instrument,
                display_symbol="XAU/USD" if signal_snapshot.instrument == "XAUUSD" else signal_snapshot.instrument,
                candidate_state=signal_snapshot.candidate_state.value,
                candidate_user_decision=signal_snapshot.candidate_user_decision.value,
                published_state=signal_snapshot.state.value,
                published_user_decision=signal_snapshot.user_decision.value,
                side=side or (risk_plan.side.value if risk_plan else None),
                entry_min=risk_plan.entry_min if risk_plan else None,
                entry_max=risk_plan.entry_max if risk_plan else None,
                stop_final=risk_plan.stop_final if risk_plan else None,
                tp1=risk_plan.tp1 if risk_plan else None,
                tp2=risk_plan.tp2 if risk_plan else None,
                planned_rr_tp1=risk_plan.planned_rr_tp1 if risk_plan else None,
                analysis_timestamp=signal_snapshot.timestamp,
                analysis_fingerprint=signal_snapshot.analysis_fingerprint,
                risk_plan_fingerprint=risk_plan.risk_plan_fingerprint if risk_plan else None,
                calibration_status=signal_snapshot.calibration_status,
                hard_gate_reasons=list(signal_snapshot.hard_gate_reasons),
                reasons=list(signal_snapshot.reasons_long_positive) + list(signal_snapshot.reasons_short_positive),
                disclaimer=CANONICAL_DISCLAIMER,
            )

            record = AlertEvent.objects.create(
                event_id=event_id,
                event_type=event_type.value,
                instrument=payload.instrument,
                display_symbol=payload.display_symbol,
                candidate_state=payload.candidate_state,
                candidate_user_decision=payload.candidate_user_decision,
                published_state=payload.published_state,
                published_user_decision=payload.published_user_decision,
                side=payload.side,
                entry_min=payload.entry_min,
                entry_max=payload.entry_max,
                stop_final=payload.stop_final,
                tp1=payload.tp1,
                tp2=payload.tp2,
                planned_rr_tp1=payload.planned_rr_tp1,
                analysis_timestamp=payload.analysis_timestamp,
                analysis_fingerprint=payload.analysis_fingerprint,
                risk_plan_fingerprint=payload.risk_plan_fingerprint,
                calibration_status=payload.calibration_status,
                hard_gate_reasons=payload.hard_gate_reasons,
                reasons=payload.reasons,
                payload=payload.to_dict(),
                disclaimer=payload.disclaimer,
                status=AlertStatus.PENDING,
            )

            # Trigger transport dispatch
            transaction.on_commit(lambda: AlertTransportDispatcher.dispatch_alert(record))
            return record

    @classmethod
    def _create_or_get_quote_alert(
        cls,
        event_id: str,
        event_type: AlertEventType,
        state: LiveMonitorState,
        bid: Decimal,
        ask: Decimal,
        quote_ts: datetime,
        side: Optional[str] = None,
    ) -> Optional[AlertEvent]:
        """Idempotently create and persist AlertEvent from live quote."""
        with transaction.atomic():
            existing = AlertEvent.objects.filter(event_id=event_id).first()
            if existing:
                return None  # Idempotent skip

            payload = AlertPayload(
                event_id=event_id,
                event_type=event_type,
                instrument=state.instrument,
                display_symbol="XAU/USD" if state.instrument == "XAUUSD" else state.instrument,
                candidate_state=state.candidate_state or state.signal_state,
                candidate_user_decision=state.candidate_user_decision or state.signal_user_decision,
                published_state=state.published_state or state.signal_state,
                published_user_decision=state.published_user_decision or "WAIT",
                side=side or state.risk_side,
                bid=bid,
                ask=ask,
                entry_min=state.entry_min,
                entry_max=state.entry_max,
                stop_final=state.stop_final,
                tp1=state.tp1,
                tp2=state.tp2,
                planned_rr_tp1=state.rr_tp1,
                analysis_timestamp=state.last_closed_candle_ts,
                quote_timestamp=quote_ts,
                analysis_fingerprint=state.analysis_fingerprint or state.signal_fingerprint,
                risk_plan_fingerprint=state.risk_plan_fingerprint,
                calibration_status=state.calibration_status or "CALIBRATION_REQUIRED",
                hard_gate_reasons=state.hard_gate_reasons or [],
                reasons=state.reasons_positive or [],
                disclaimer=CANONICAL_DISCLAIMER,
            )

            record = AlertEvent.objects.create(
                event_id=event_id,
                event_type=event_type.value,
                instrument=payload.instrument,
                display_symbol=payload.display_symbol,
                candidate_state=payload.candidate_state,
                candidate_user_decision=payload.candidate_user_decision,
                published_state=payload.published_state,
                published_user_decision=payload.published_user_decision,
                side=payload.side,
                bid=payload.bid,
                ask=payload.ask,
                entry_min=payload.entry_min,
                entry_max=payload.entry_max,
                stop_final=payload.stop_final,
                tp1=payload.tp1,
                tp2=payload.tp2,
                planned_rr_tp1=payload.planned_rr_tp1,
                analysis_timestamp=payload.analysis_timestamp,
                quote_timestamp=payload.quote_timestamp,
                analysis_fingerprint=payload.analysis_fingerprint,
                risk_plan_fingerprint=payload.risk_plan_fingerprint,
                calibration_status=payload.calibration_status,
                hard_gate_reasons=payload.hard_gate_reasons,
                reasons=payload.reasons,
                payload=payload.to_dict(),
                disclaimer=payload.disclaimer,
                status=AlertStatus.PENDING,
            )

            transaction.on_commit(lambda: AlertTransportDispatcher.dispatch_alert(record))
            return record
