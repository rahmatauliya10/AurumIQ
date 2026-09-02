"""Typed domain dataclasses, enums, and payload validation for Informational Alerts (Phase 7)."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class AlertEventType(str, Enum):
    """Canonical 13 approved informational event types for active XAUUSD monitoring."""
    WATCH_LONG_CREATED = "WATCH_LONG_CREATED"
    READY_LONG = "READY_LONG"
    BUY_WINDOW_CANDIDATE = "BUY_WINDOW_CANDIDATE"

    WATCH_SHORT_CREATED = "WATCH_SHORT_CREATED"
    READY_SHORT = "READY_SHORT"
    SELL_WINDOW_CANDIDATE = "SELL_WINDOW_CANDIDATE"

    CONFLICT = "CONFLICT"
    MACRO_BLACKOUT_ACTIVE = "MACRO_BLACKOUT_ACTIVE"
    SYSTEM_SAFETY_HOLD = "SYSTEM_SAFETY_HOLD"

    ENTRY_ZONE_REACHED = "ENTRY_ZONE_REACHED"
    INVALIDATION_TOUCHED = "INVALIDATION_TOUCHED"

    LIVE_DATA_STALE = "LIVE_DATA_STALE"
    PROVIDER_UNHEALTHY = "PROVIDER_UNHEALTHY"


FORBIDDEN_ALERT_PAYLOAD_FIELDS: Set[str] = {
    "order_id",
    "broker_order_id",
    "quantity",
    "lot_size",
    "leverage",
    "margin",
    "auto_execute",
    "exchange_order",
    "take_profit_order",
    "stop_order",
    "trade_now",
    "execute_order",
    "account_id",
}

CANONICAL_DISCLAIMER: str = "MANUAL DECISION SUPPORT ONLY — NO AUTO-ORDER EXECUTION."


@dataclass(frozen=True)
class AlertPayload:
    """
    Immutable canonical payload schema for all AurumIQ informational alerts.
    Strictly forbids trading/execution fields.
    """
    event_id: str
    event_type: AlertEventType
    instrument: str = "XAUUSD"
    display_symbol: str = "XAU/USD"

    # Candidate Layer A
    candidate_state: str = "NO_TRADE"
    candidate_user_decision: str = "WAIT"

    # Published Layer B
    published_state: str = "NO_TRADE"
    published_user_decision: str = "WAIT"

    # Setup Side & Prices
    side: Optional[str] = None  # "LONG" | "SHORT" | None
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None

    # Risk Geometry (None when invalid)
    entry_min: Optional[Decimal] = None
    entry_max: Optional[Decimal] = None
    stop_final: Optional[Decimal] = None
    tp1: Optional[Decimal] = None
    tp2: Optional[Decimal] = None
    planned_rr_tp1: Optional[Decimal] = None

    # Timestamps
    analysis_timestamp: Optional[datetime] = None
    quote_timestamp: Optional[datetime] = None

    # Cryptographic Provenance
    analysis_fingerprint: Optional[str] = None
    risk_plan_fingerprint: Optional[str] = None
    calibration_status: str = "CALIBRATION_REQUIRED"

    # Diagnostics
    hard_gate_reasons: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    # Mandatory Legal/Governance Disclaimer
    disclaimer: str = CANONICAL_DISCLAIMER

    def __post_init__(self):
        """Validate that no forbidden execution fields or structures exist."""
        payload_dict = self.to_dict()
        for forbidden in FORBIDDEN_ALERT_PAYLOAD_FIELDS:
            if forbidden in payload_dict:
                raise ValueError(
                    f"CRITICAL GOVERNANCE VIOLATION: Forbidden field '{forbidden}' found in alert payload."
                )

    def to_dict(self) -> Dict[str, Any]:
        """Convert payload to a clean JSON-serializable dictionary."""
        d: Dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type.value if isinstance(self.event_type, Enum) else str(self.event_type),
            "instrument": self.instrument,
            "display_symbol": self.display_symbol,
            "candidate_state": self.candidate_state,
            "candidate_user_decision": self.candidate_user_decision,
            "published_state": self.published_state,
            "published_user_decision": self.published_user_decision,
            "side": self.side,
            "bid": str(self.bid) if self.bid is not None else None,
            "ask": str(self.ask) if self.ask is not None else None,
            "entry_min": str(self.entry_min) if self.entry_min is not None else None,
            "entry_max": str(self.entry_max) if self.entry_max is not None else None,
            "stop_final": str(self.stop_final) if self.stop_final is not None else None,
            "tp1": str(self.tp1) if self.tp1 is not None else None,
            "tp2": str(self.tp2) if self.tp2 is not None else None,
            "planned_rr_tp1": str(self.planned_rr_tp1) if self.planned_rr_tp1 is not None else None,
            "analysis_timestamp": self.analysis_timestamp.isoformat() if self.analysis_timestamp else None,
            "quote_timestamp": self.quote_timestamp.isoformat() if self.quote_timestamp else None,
            "analysis_fingerprint": self.analysis_fingerprint,
            "risk_plan_fingerprint": self.risk_plan_fingerprint,
            "calibration_status": self.calibration_status,
            "hard_gate_reasons": list(self.hard_gate_reasons),
            "reasons": list(self.reasons),
            "disclaimer": self.disclaimer,
        }
        return d
