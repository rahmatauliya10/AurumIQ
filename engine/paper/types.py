"""Phase 8 Live Paper Observation domain types, enums, and data transfer structures."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Phase8Status(str, Enum):
    """Authoritative Phase 8 operational observation statuses."""
    NOT_STARTED = "NOT_STARTED"
    OBSERVING = "OBSERVING"
    INTERRUPTED = "INTERRUPTED"
    COMPLETE = "COMPLETE"


class InterruptionCategory(str, Enum):
    """Categories of operational interruptions for Phase 8 continuity tracking."""
    EXPECTED_MARKET_CLOSURE = "EXPECTED_MARKET_CLOSURE"  # Weekends / holidays: non-failure
    COLLECTOR_OUTAGE = "COLLECTOR_OUTAGE"
    EVALUATION_JOB_FAILURE = "EVALUATION_JOB_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    ENGINE_EXCEPTION = "ENGINE_EXCEPTION"
    FRICTION_RESOLUTION_FAILURE = "FRICTION_RESOLUTION_FAILURE"
    MISSED_ELIGIBLE_INTERVAL = "MISSED_ELIGIBLE_INTERVAL"


class ParityDimension(str, Enum):
    """Reporting dimensions for live-vs-replay parity audits."""
    BUY = "BUY"
    SELL = "SELL"
    COMBINED = "COMBINED"


class PaperOutcomeType(str, Enum):
    """Terminal outcome classifications for paper positions."""
    TP1_FIRST = "TP1_FIRST"
    SL_FIRST = "SL_FIRST"
    TIMEOUT = "TIMEOUT"
    NO_FILL = "NO_FILL"
    UNRESOLVED = "UNRESOLVED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class PaperObservationSnapshot:
    """In-memory snapshot of a Phase 8 paper observation."""
    observation_id: str
    observation_fingerprint: str
    source_signal_fingerprint: str
    source_risk_plan_fingerprint: Optional[str]
    side: str  # "BUY" or "SELL"
    decision_timeframe: str  # "15m", "1h", "4h", "1d"
    decision_timestamp: datetime
    decision_candle_close: datetime
    instrument: str = "XAUUSD"
    broker_symbol: str = "XAUUSDc"
    dataset_readiness_fingerprint: str = ""
    friction_model_version_id: str = "EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1"
    friction_evidence_fingerprint: Optional[str] = None
    signal_score: Optional[float] = None
    signal_decision: str = "WAIT"
    paper_entry_timestamp: Optional[datetime] = None
    paper_entry_price_basis: Optional[Decimal] = None
    spread_friction_applied: Decimal = Decimal("0")
    commission_friction_applied: Decimal = Decimal("0")
    financing_friction_applied: Decimal = Decimal("0")
    paper_exit_timestamp: Optional[datetime] = None
    paper_exit_price: Optional[Decimal] = None
    exit_reason: Optional[str] = None
    outcome: str = PaperOutcomeType.UNRESOLVED.value
    mfe_r: Optional[Decimal] = None
    mae_r: Optional[Decimal] = None
    gross_r: Optional[Decimal] = None
    net_r: Optional[Decimal] = None
    holding_duration_seconds: Optional[float] = None
    paper_volume_lots: Optional[Decimal] = None
    pnl_currency: Optional[str] = None
    gross_pnl: Optional[Decimal] = None
    net_pnl: Optional[Decimal] = None
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class PaperParityItemComparison:
    """Comparison item between a live paper observation and its deterministic replay."""
    observation_id: str
    side: str
    decision_timestamp: datetime
    live_decision: str
    replay_decision: str
    decision_match: bool
    live_score: Optional[float]
    replay_score: Optional[float]
    score_discrepancy: float
    live_entry_eligible: bool
    replay_entry_eligible: bool
    eligibility_match: bool
    live_friction_model_id: str
    replay_friction_model_id: str
    friction_model_match: bool
    live_outcome: str
    replay_outcome: str
    outcome_match: bool
    fill_difference: Optional[Decimal] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DimensionParitySummary:
    """Parity summary metrics for a specific dimension (BUY, SELL, or COMBINED)."""
    dimension: ParityDimension
    total_evaluated: int
    decision_matches: int
    decision_match_rate: float
    score_matches: int
    score_match_rate: float
    eligibility_matches: int
    eligibility_match_rate: float
    friction_model_matches: int
    friction_model_match_rate: float
    outcome_matches: int
    outcome_match_rate: float
    max_fill_difference: Decimal = Decimal("0")
    is_parity_passed: bool = True


@dataclass(frozen=True)
class LiveReplayParityReport:
    """Consolidated 3-tier live-vs-replay parity audit report."""
    buy_parity: DimensionParitySummary
    sell_parity: DimensionParitySummary
    combined_parity: DimensionParitySummary
    overall_status: str  # "PASS", "FAIL", "PENDING"
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class Phase8OperationalMetrics:
    """Operational counters and telemetry for Phase 8 observability."""
    observation_window_start: Optional[datetime]
    observation_window_age_seconds: float
    observation_day: int
    completed_calendar_days: int
    last_successful_evaluation_timestamp: Optional[datetime]
    total_signals_evaluated: int
    buy_count: int
    sell_count: int
    no_trade_count: int
    paper_positions_opened: int
    paper_positions_closed: int
    outcomes_pending: int
    outcomes_resolved: int
    tp1_count: int
    sl_count: int
    timeout_count: int
    no_fill_count: int
    data_freshness_status: str
    runtime_errors_count: int
    unresolved_integrity_failures: int
    paper_only: bool = True
    real_order_execution: str = "disabled"
