"""WebSocket consumers and real-time event broadcasting for Live Monitor (Phase 7B)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Set
import json
import structlog

logger = structlog.get_logger(__name__)


class LiveEventBroadcaster:
    """
    In-memory / Redis pub-sub event broadcaster for typed incremental live events.
    Supports: quote_update, signal_update, risk_plan_update, feed_health_update, candle_closed.
    """

    _subscribers: Set[Any] = set()

    @classmethod
    def subscribe(cls, subscriber: Any) -> None:
        cls._subscribers.add(subscriber)

    @classmethod
    def unsubscribe(cls, subscriber: Any) -> None:
        cls._subscribers.discard(subscriber)

    @classmethod
    def broadcast(cls, event_payload: Dict[str, Any]) -> None:
        """Broadcast typed incremental update to all authenticated subscribers."""
        dead_subscribers = set()
        for sub in list(cls._subscribers):
            try:
                sub.send_event(event_payload)
            except Exception as e:
                logger.warning("broadcast_failed_for_subscriber", error=str(e))
                dead_subscribers.add(sub)
        cls._subscribers.difference_update(dead_subscribers)

    @classmethod
    def format_quote_event(
        cls,
        instrument: str,
        bid: Decimal,
        ask: Decimal,
        spread: Decimal,
        spread_pct: Decimal,
        source_timestamp: datetime,
        sequence_number: Optional[int],
        entry_zone_status: str,
        distance_to_entry_zone_pct: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Format typed quote_update payload (P7-19)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"QU_{instrument}_{now_utc.timestamp()}",
            "event_type": "quote_update",
            "instrument": instrument,
            "source_timestamp": source_timestamp.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "sequence_number": sequence_number,
            "data": {
                "bid": str(bid),
                "ask": str(ask),
                "spread": str(spread),
                "spread_pct": str(spread_pct),
                "entry_zone_status": entry_zone_status,
                "distance_to_entry_zone_pct": str(distance_to_entry_zone_pct) if distance_to_entry_zone_pct else None,
            },
        }

    @classmethod
    def format_signal_update(
        cls,
        instrument: str,
        signal_fingerprint: str,
        signal_state: str,
        signal_user_decision: str,
        direction_score: float,
        timing_score: float,
        last_closed_candle_ts: datetime,
        decision_sequence: int,
        reasons_positive: list,
        reasons_negative: list,
        hard_gate_reasons: list,
    ) -> Dict[str, Any]:
        """Format typed signal_update payload (P7-20)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"SU_{instrument}_{now_utc.timestamp()}",
            "event_type": "signal_update",
            "instrument": instrument,
            "source_timestamp": last_closed_candle_ts.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "decision_sequence": decision_sequence,
            "data": {
                "signal_fingerprint": signal_fingerprint,
                "signal_state": signal_state,
                "signal_user_decision": signal_user_decision,
                "direction_score": direction_score,
                "timing_score": timing_score,
                "reasons_positive": reasons_positive,
                "reasons_negative": reasons_negative,
                "hard_gate_reasons": hard_gate_reasons,
            },
        }

    @classmethod
    def format_risk_plan_update(
        cls,
        instrument: str,
        source_signal_fingerprint: str,
        risk_plan_valid: bool,
        execution_eligible: bool,
        effective_action: str,
        entry_min: Optional[Decimal],
        entry_mid: Optional[Decimal],
        entry_max: Optional[Decimal],
        stop_final: Optional[Decimal],
        tp1: Optional[Decimal],
        tp2: Optional[Decimal],
        rr_tp1: Optional[Decimal],
        rr_tp2: Optional[Decimal],
        decision_sequence: int,
    ) -> Dict[str, Any]:
        """Format typed risk_plan_update payload (P7-21)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"RPU_{instrument}_{now_utc.timestamp()}",
            "event_type": "risk_plan_update",
            "instrument": instrument,
            "source_timestamp": now_utc.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "decision_sequence": decision_sequence,
            "data": {
                "source_signal_fingerprint": source_signal_fingerprint,
                "risk_plan_valid": risk_plan_valid,
                "execution_eligible": execution_eligible,
                "effective_action": effective_action,
                "entry_min": str(entry_min) if entry_min else None,
                "entry_mid": str(entry_mid) if entry_mid else None,
                "entry_max": str(entry_max) if entry_max else None,
                "stop_final": str(stop_final) if stop_final else None,
                "tp1": str(tp1) if tp1 else None,
                "tp2": str(tp2) if tp2 else None,
                "rr_tp1": str(rr_tp1) if rr_tp1 else None,
                "rr_tp2": str(rr_tp2) if rr_tp2 else None,
            },
        }

    @classmethod
    def format_feed_health_update(
        cls,
        instrument: str,
        feed_health: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format typed feed_health_update payload (P7-22)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"FHU_{instrument}_{now_utc.timestamp()}",
            "event_type": "feed_health_update",
            "instrument": instrument,
            "source_timestamp": now_utc.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "data": feed_health,
        }


class LiveMonitorWebSocketHandler:
    """
    ASGI-compliant WebSocket session handler for authenticated streaming (P7-25).
    """

    def __init__(self, user: Any, instrument: str = "XAUT/USDT"):
        self.user = user
        self.instrument = instrument
        self.is_connected = False
        self.last_quote_sequence: Optional[int] = None
        self.last_decision_sequence: Optional[int] = None
        self.message_queue: list[str] = []

    def connect(self) -> bool:
        """Enforce authentication check on connection (P7-25)."""
        if not self.user or not getattr(self.user, "is_authenticated", False):
            logger.warning("unauthorized_websocket_connection_rejected")
            self.is_connected = False
            return False

        self.is_connected = True
        LiveEventBroadcaster.subscribe(self)
        logger.info("websocket_connected", user=str(self.user), instrument=self.instrument)
        return True

    def disconnect(self) -> None:
        self.is_connected = False
        LiveEventBroadcaster.unsubscribe(self)
        logger.info("websocket_disconnected", user=str(self.user))

    def send_event(self, event_payload: Dict[str, Any]) -> None:
        """Send formatted event JSON to client if connected."""
        if not self.is_connected:
            return
        if event_payload.get("instrument") != self.instrument:
            return

        msg_str = json.dumps(event_payload)
        self.message_queue.append(msg_str)
