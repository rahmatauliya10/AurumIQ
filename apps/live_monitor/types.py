"""Typed domain dataclasses and enums for Phase 7 Live Monitoring."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class EntryZoneStatus(str, Enum):
    """Entry zone execution readiness states."""
    INSIDE_ZONE = "INSIDE_ZONE"
    ABOVE_ZONE = "ABOVE_ZONE"
    BELOW_ZONE = "BELOW_ZONE"
    NO_ACTIVE_ZONE = "NO_ACTIVE_ZONE"


class FeedStatus(str, Enum):
    """Critical feed operational health states."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DOWN = "DOWN"
    TRANSITION = "TRANSITION"


class OperationalHealthStatus(str, Enum):
    """System-wide operational health status (distinct from SignalState)."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    RECOVERING = "RECOVERING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class OperationalMetrics:
    """In-memory operational performance counters and latency metrics."""
    quote_age_seconds: float = 0.0
    analysis_latency_ms: float = 0.0
    event_latency_ms: float = 0.0
    duplicate_event_count: int = 0
    out_of_order_event_count: int = 0
    recovery_count: int = 0
    websocket_connections: int = 0
    operational_health: OperationalHealthStatus = OperationalHealthStatus.HEALTHY



@dataclass(frozen=True)
class LiveQuoteEvent:
    """Incoming real-time market quote event (Path A)."""
    event_id: str
    instrument: str
    provider: str
    bid: Decimal
    ask: Decimal
    source_timestamp: datetime
    received_timestamp: datetime
    sequence_number: Optional[int] = None

    @property
    def mid_price(self) -> Decimal:
        return ((self.bid + self.ask) / Decimal("2")).quantize(Decimal("0.01"))

    @property
    def spread(self) -> Decimal:
        return (self.ask - self.bid).quantize(Decimal("0.01"))

    @property
    def spread_pct(self) -> Decimal:
        if self.ask > 0:
            return ((self.ask - self.bid) / self.ask).quantize(Decimal("0.0001"))
        return Decimal("0")


@dataclass(frozen=True)
class CandleClosedEvent:
    """Incoming verified closed candle event (Path B)."""
    event_id: str
    instrument: str
    timeframe: str
    timestamp_open: datetime
    timestamp_close: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    quote_rate: Decimal = Decimal("1.0")
    source: str = "binance"
    sequence_number: Optional[int] = None
    is_closed: bool = True


@dataclass(frozen=True)
class LiveFeedHealthStatus:
    """Consolidated critical feed health assessment."""
    xaut_status: FeedStatus = FeedStatus.HEALTHY
    xau_status: FeedStatus = FeedStatus.HEALTHY
    usdt_norm_status: FeedStatus = FeedStatus.HEALTHY
    macro_status: FeedStatus = FeedStatus.HEALTHY
    provider_sync_status: FeedStatus = FeedStatus.HEALTHY
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_all_healthy(self) -> bool:
        return (
            self.xaut_status == FeedStatus.HEALTHY
            and self.xau_status == FeedStatus.HEALTHY
            and self.usdt_norm_status == FeedStatus.HEALTHY
            and self.macro_status == FeedStatus.HEALTHY
            and self.provider_sync_status in (FeedStatus.HEALTHY, FeedStatus.DEGRADED)
        )


@dataclass(frozen=True)
class LiveProjectionState:
    """
    Immutable snapshot of the live presentation projection.
    Strict separation between Phase 4 signal output and Phase 5 risk effective action (P7-C1).
    """
    instrument: str
    
    # Path A: Quote fields
    current_bid: Optional[Decimal]
    current_ask: Optional[Decimal]
    spread: Optional[Decimal]
    spread_pct: Optional[Decimal]
    quote_source_timestamp: Optional[datetime]
    quote_received_timestamp: Optional[datetime]
    quote_age_seconds: Optional[float]
    is_quote_stale: bool
    quote_sequence: Optional[int]
    entry_zone_status: EntryZoneStatus
    distance_to_entry_zone_pct: Optional[Decimal]

    # Path B: Decision fields (Phase 4 Signal)
    last_closed_candle_ts: Optional[datetime]
    last_analysis_timestamp: Optional[datetime]
    signal_fingerprint: Optional[str]
    signal_state: str                   # e.g., BUY_WINDOW, WAIT, AVOID, NO_TRADE
    signal_user_decision: str           # Phase 4 user decision: BUY, WAIT, AVOID
    direction_score: float
    timing_score: float

    # Path B: Decision fields (Phase 5 Risk)
    risk_plan_valid: bool
    execution_eligible: bool
    effective_action: str               # Primary user-facing action: BUY, WAIT, AVOID
    entry_min: Optional[Decimal]
    entry_mid: Optional[Decimal]
    entry_max: Optional[Decimal]
    stop_final: Optional[Decimal]
    tp1: Optional[Decimal]
    tp2: Optional[Decimal]
    rr_tp1: Optional[Decimal]
    rr_tp2: Optional[Decimal]

    # Explainability & Provenance
    reasons_positive: List[str]
    reasons_negative: List[str]
    hard_gate_reasons: List[str]
    feed_health: LiveFeedHealthStatus
    
    # Provenance Signatures (P7-C6)
    engine_version: str
    config_version: str
    feature_version: str
    cycle_version: str
    risk_version: str
    code_revision: str
    decision_sequence: int
