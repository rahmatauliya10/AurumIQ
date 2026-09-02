"""Core types, dataclasses, and enums for deterministic Phase 6 XAUUSD backtesting."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.core.types import (
    EntryExecutionPolicy,
    IntrabarPolicy,
    RegimeType,
    RiskSide,
    SessionType,
    SignalSide,
    SignalSnapshot,
    SignalState,
    UserDecision,
)
from engine.risk.xauusd_policy import XauUsdRiskProfile
from engine.signals.profile import Phase4SignalProfile


def _require_utc(dt: datetime, param_name: str = "timestamp") -> datetime:
    """Validate that datetime is explicitly timezone aware and convert to UTC."""
    if dt is None or dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{param_name} must be timezone-aware with non-None utcoffset (naive timestamps forbidden).")
    return dt.astimezone(timezone.utc)


class XauUsdCostScenario(str, Enum):
    """Backtest cost friction scenarios for XAUUSD."""
    IDEALIZED = "IDEALIZED"
    EMPIRICAL = "EMPIRICAL"


class XauUsdTradeOutcome(str, Enum):
    """
    Primary terminal outcomes for simulated XAUUSD trades.
    Phase 6 enforces TP1 as the terminal profit target.
    TP2 is recorded strictly as observational extension.
    """
    TP1_FIRST = "TP1_FIRST"
    SL_FIRST = "SL_FIRST"
    NO_FILL = "NO_FILL"
    TIMEOUT = "TIMEOUT"
    UNRESOLVED = "UNRESOLVED"
    SKIPPED = "SKIPPED"
    CONSERVATIVE_SL_FIRST = "CONSERVATIVE_SL_FIRST"


class XauUsdAblationType(str, Enum):
    """Component ablation variants for XAUUSD research-only evaluation."""
    BASELINE = "BASELINE"
    NO_REGIME_FILTER = "NO_REGIME_FILTER"
    NO_STRUCTURE_COMPONENT = "NO_STRUCTURE_COMPONENT"
    NO_MTF_TREND = "NO_MTF_TREND"
    NO_PHASE3A_SESSION = "NO_PHASE3A_SESSION"
    NO_PHASE3A_SWING_MATURITY = "NO_PHASE3A_SWING_MATURITY"
    NO_MACRO_BLACKOUT = "NO_MACRO_BLACKOUT"


@dataclass(frozen=True)
class XauUsdCostConfig:
    """
    Configurable trading friction and cost model parameters for XAUUSD.
    Friction values are specified in basis points (1 bps = 0.01% = 0.0001) or explicit amounts.
    """
    entry_fee_bps: Decimal = Decimal("0.0")
    exit_fee_bps: Decimal = Decimal("0.0")
    synthetic_spread_bps: Decimal = Decimal("0.0")
    entry_slippage_bps: Decimal = Decimal("0.0")
    exit_slippage_bps: Decimal = Decimal("0.0")

    @classmethod
    def idealized(cls) -> "XauUsdCostConfig":
        """Zero friction scenario for baseline strategy signal validation."""
        return cls(
            entry_fee_bps=Decimal("0.0"),
            exit_fee_bps=Decimal("0.0"),
            synthetic_spread_bps=Decimal("0.0"),
            entry_slippage_bps=Decimal("0.0"),
            exit_slippage_bps=Decimal("0.0"),
        )

    @classmethod
    def empirical(
        cls,
        entry_fee_bps: Decimal = Decimal("0.0"),
        exit_fee_bps: Decimal = Decimal("0.0"),
        synthetic_spread_bps: Decimal = Decimal("0.0"),
        entry_slippage_bps: Decimal = Decimal("0.0"),
        exit_slippage_bps: Decimal = Decimal("0.0"),
    ) -> "XauUsdCostConfig":
        """
        Explicit caller-supplied empirical friction scenario.
        Requires explicit non-zero configuration when in empirical mode.
        """
        if (
            entry_fee_bps == Decimal("0.0")
            and exit_fee_bps == Decimal("0.0")
            and synthetic_spread_bps == Decimal("0.0")
            and entry_slippage_bps == Decimal("0.0")
            and exit_slippage_bps == Decimal("0.0")
        ):
            raise ValueError("EMPIRICAL cost configuration requires explicit non-zero friction parameters.")
        return cls(
            entry_fee_bps=entry_fee_bps,
            exit_fee_bps=exit_fee_bps,
            synthetic_spread_bps=synthetic_spread_bps,
            entry_slippage_bps=entry_slippage_bps,
            exit_slippage_bps=exit_slippage_bps,
        )


@dataclass(frozen=True)
class XauUsdSimulatedTrade:
    """
    Immutable record of a simulated point-in-time XAUUSD trade lifecycle.
    Denominator R is strictly frozen to planned_risk_amount (LONG: entry_max - stop_final, SHORT: stop_final - entry_min).
    """
    trade_id: str
    side: SignalSide
    candidate_state: SignalState
    candidate_user_decision: UserDecision
    source_signal_fingerprint: str
    signal_timestamp: datetime
    risk_plan_fingerprint: str
    planned_risk_amount: Decimal  # LONG: entry_max - stop_final > 0, SHORT: stop_final - entry_min > 0
    outcome: XauUsdTradeOutcome
    fill_timestamp: Optional[datetime] = None
    fill_price: Optional[Decimal] = None
    exit_timestamp: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    dependency_end_timestamp: Optional[datetime] = None
    gross_pnl_per_unit: Optional[Decimal] = None
    net_pnl_per_unit: Optional[Decimal] = None
    gross_r: Optional[Decimal] = None
    net_r: Optional[Decimal] = None
    gross_return_pct: Optional[Decimal] = None
    net_return_pct: Optional[Decimal] = None
    mfe_r: Optional[Decimal] = None
    mae_r: Optional[Decimal] = None
    holding_duration_seconds: Optional[float] = None
    entry_fee: Decimal = Decimal("0.0")
    exit_fee: Decimal = Decimal("0.0")
    entry_spread: Decimal = Decimal("0.0")
    exit_spread: Decimal = Decimal("0.0")
    entry_slippage: Decimal = Decimal("0.0")
    exit_slippage: Decimal = Decimal("0.0")
    regime: RegimeType = RegimeType.UNKNOWN
    session: SessionType = SessionType.LONDON
    cycle_phase: Optional[str] = None
    ambiguity_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY
    fold_id: Optional[int] = None
    run_fingerprint: str = ""
    execution_evidence_fingerprint: str = ""
    dependency_window: Tuple[datetime, datetime] = field(
        default_factory=lambda: (datetime.min.replace(tzinfo=timezone.utc), datetime.min.replace(tzinfo=timezone.utc))
    )
    tp2_reached_after_tp1: bool = False
    max_favorable_extension_r: Optional[Decimal] = None

    def __post_init__(self):
        _require_utc(self.signal_timestamp, "signal_timestamp")
        if self.fill_timestamp is not None:
            _require_utc(self.fill_timestamp, "fill_timestamp")
        if self.exit_timestamp is not None:
            _require_utc(self.exit_timestamp, "exit_timestamp")
        if self.dependency_end_timestamp is not None:
            _require_utc(self.dependency_end_timestamp, "dependency_end_timestamp")


@dataclass(frozen=True)
class XauUsdBacktestMetrics:
    """Lossless, deterministic metrics for XAUUSD historical backtest run."""
    candidate_count: int = 0
    long_candidate_count: int = 0
    short_candidate_count: int = 0
    valid_risk_count: int = 0
    long_valid_risk_count: int = 0
    short_valid_risk_count: int = 0
    signal_count: int = 0
    execution_eligible_count: int = 0
    fill_count: int = 0
    no_fill_count: int = 0
    fill_rate: float = 0.0
    no_fill_rate: float = 0.0
    trade_count: int = 0
    long_trade_count: int = 0
    short_trade_count: int = 0

    # Payoff Profile
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    payoff_ratio: float = 0.0

    # Expectancy & Profitability
    gross_expectancy_r: float = 0.0
    net_expectancy_r: float = 0.0
    average_r: float = 0.0
    median_r: float = 0.0
    profit_factor: float = 0.0
    gross_return_pct: float = 0.0
    net_return_pct: float = 0.0

    # Downside Risk (Normalized Trade Sequence in R)
    max_drawdown_r: float = 0.0
    drawdown_duration_trades: int = 0
    maximum_consecutive_losses: int = 0

    # Execution Quality (Post-Fill MFE & MAE in R)
    average_mfe_r: float = 0.0
    median_mfe_r: float = 0.0
    average_mae_r: float = 0.0
    median_mae_r: float = 0.0
    average_holding_duration_seconds: float = 0.0
    median_holding_duration_seconds: float = 0.0

    # Terminal Outcome Counts
    tp1_first_count: int = 0
    sl_first_count: int = 0
    conservative_sl_first_count: int = 0
    unresolved_count: int = 0
    timeout_count: int = 0
    conservative_resolution_rate: float = 0.0

    # Friction & Drag
    total_entry_fees: float = 0.0
    total_exit_fees: float = 0.0
    total_spread_cost: float = 0.0
    total_slippage_cost: float = 0.0
    cost_drag_r: float = 0.0
    cost_drag_pct: float = 0.0
    wait_count: int = 0
    conflict_count: int = 0

    # Subsystems & Distributions
    subsystems: Optional["XauUsdSubsystemBreakdown"] = None
    regime_distribution: Dict[str, int] = field(default_factory=dict)
    session_distribution: Dict[str, int] = field(default_factory=dict)
    rejection_reasons_distribution: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class XauUsdSubsystemBreakdown:
    """Subsystem performance breakdown by regime, session, and side."""
    regime_breakdown: Dict[str, Any] = field(default_factory=dict)
    session_breakdown: Dict[str, Any] = field(default_factory=dict)
    side_breakdown: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class XauUsdBacktestRunSpec:
    """Specification and immutable configuration for an XAUUSD backtest execution."""
    instrument: str
    start_time: datetime
    end_time: datetime
    timeframes: Tuple[str, ...]
    cost_config: XauUsdCostConfig
    cost_scenario: XauUsdCostScenario
    dataset_hash: str
    holding_horizon_bars_15m: Optional[int] = None
    holding_horizon_seconds: Optional[float] = None
    max_fill_wait_bars_15m: Optional[int] = None
    max_fill_wait_seconds: Optional[float] = None
    execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY
    engine_version: str = "4.0.0-xauusd"
    config_version: str = "cfg-xauusd-2026-v1"
    feature_version: str = "feat-xauusd-2026-v1"
    cycle_version: str = "3.0.0-3A"
    risk_version: str = "5.0.0-xauusd"
    execution_model_version: str = "5.0.0-exec-v1"
    backtest_version: str = "6.0.0-xauusd"
    code_revision: str = ""  # REQUIRED caller-injected
    ablation_type: XauUsdAblationType = XauUsdAblationType.BASELINE
    signal_profile: Optional[Phase4SignalProfile] = None
    risk_profile: Optional[XauUsdRiskProfile] = None
    phase4_policy_fingerprint: str = ""
    phase5_risk_policy_fingerprint: str = ""
    phase5_execution_policy_fingerprint: str = ""
    phase5_long_execution_policy_fingerprint: str = ""
    phase5_short_execution_policy_fingerprint: str = ""

    def __post_init__(self):
        _require_utc(self.start_time, "start_time")
        _require_utc(self.end_time, "end_time")
        if self.start_time >= self.end_time:
            raise ValueError(f"start_time ({self.start_time}) must be strictly before end_time ({self.end_time})")
        if not self.code_revision or not self.code_revision.strip():
            raise ValueError("Backtest run spec requires an explicit non-empty code_revision.")
        if self.holding_horizon_bars_15m is None and self.holding_horizon_seconds is None:
            raise ValueError("Explicit holding horizon (holding_horizon_bars_15m or holding_horizon_seconds) is required.")
        if self.max_fill_wait_bars_15m is None and self.max_fill_wait_seconds is None:
            raise ValueError("Explicit fill-search horizon (max_fill_wait_bars_15m or max_fill_wait_seconds) is required.")

        if self.signal_profile is not None:
            from engine.signals.profile import compute_phase4_policy_fingerprint
            actual_p4_fp = compute_phase4_policy_fingerprint(self.signal_profile)
            if self.phase4_policy_fingerprint and self.phase4_policy_fingerprint != actual_p4_fp:
                raise ValueError(f"phase4_policy_fingerprint mismatch: expected '{self.phase4_policy_fingerprint}', actual '{actual_p4_fp}'")
            object.__setattr__(self, "phase4_policy_fingerprint", actual_p4_fp)

        if self.risk_profile is not None:
            import hashlib
            from engine.risk.xauusd_fingerprints import compute_phase5_policy_fingerprint
            actual_p5_fp = compute_phase5_policy_fingerprint(self.risk_profile)
            if self.phase5_risk_policy_fingerprint and self.phase5_risk_policy_fingerprint != actual_p5_fp:
                raise ValueError(f"phase5_risk_policy_fingerprint mismatch: expected '{self.phase5_risk_policy_fingerprint}', actual '{actual_p5_fp}'")
            object.__setattr__(self, "phase5_risk_policy_fingerprint", actual_p5_fp)

            l_exec = self.risk_profile.long_execution_policy
            s_exec = self.risk_profile.short_execution_policy
            l_exec_fp = hashlib.sha256(f"LONG:{l_exec.latency_seconds}:{l_exec.synthetic_spread_pct}:{l_exec.slippage_pct}".encode("utf-8")).hexdigest()
            s_exec_fp = hashlib.sha256(f"SHORT:{s_exec.latency_seconds}:{s_exec.synthetic_spread_pct}:{s_exec.slippage_pct}".encode("utf-8")).hexdigest()
            combined_exec_fp = hashlib.sha256(f"{l_exec_fp}:{s_exec_fp}".encode("utf-8")).hexdigest()

            if self.phase5_long_execution_policy_fingerprint and self.phase5_long_execution_policy_fingerprint != l_exec_fp:
                raise ValueError(f"phase5_long_execution_policy_fingerprint mismatch: expected '{self.phase5_long_execution_policy_fingerprint}', actual '{l_exec_fp}'")
            if self.phase5_short_execution_policy_fingerprint and self.phase5_short_execution_policy_fingerprint != s_exec_fp:
                raise ValueError(f"phase5_short_execution_policy_fingerprint mismatch: expected '{self.phase5_short_execution_policy_fingerprint}', actual '{s_exec_fp}'")
            if self.phase5_execution_policy_fingerprint and self.phase5_execution_policy_fingerprint != combined_exec_fp:
                raise ValueError(f"phase5_execution_policy_fingerprint mismatch: expected '{self.phase5_execution_policy_fingerprint}', actual '{combined_exec_fp}'")

            object.__setattr__(self, "phase5_execution_policy_fingerprint", combined_exec_fp)
            object.__setattr__(self, "phase5_long_execution_policy_fingerprint", l_exec_fp)
            object.__setattr__(self, "phase5_short_execution_policy_fingerprint", s_exec_fp)


@dataclass(frozen=True)
class XauUsdFoldSpec:
    """Chronological fold boundary specification."""
    fold_id: int
    train_start: datetime
    train_end: datetime
    val_start: Optional[datetime]
    val_end: Optional[datetime]
    oos_start: datetime
    oos_end: datetime
    embargo_duration_seconds: float = 0.0

    def __post_init__(self):
        _require_utc(self.train_start, "train_start")
        _require_utc(self.train_end, "train_end")
        _require_utc(self.oos_start, "oos_start")
        _require_utc(self.oos_end, "oos_end")
        if self.val_start:
            _require_utc(self.val_start, "val_start")
        if self.val_end:
            _require_utc(self.val_end, "val_end")


@dataclass(frozen=True)
class XauUsdWalkForwardConfig:
    """Configuration for chronological walk-forward validation (all ratios and fold counts required)."""
    total_folds: int
    train_ratio: float
    val_ratio: float
    oos_ratio: float
    embargo_seconds: float = 0.0
    purge_overlapping: bool = True
    rolling_window: bool = False

    def __post_init__(self):
        if self.total_folds < 1:
            raise ValueError(f"total_folds must be >= 1, got {self.total_folds}")
        if self.train_ratio <= 0.0:
            raise ValueError(f"train_ratio must be > 0.0, got {self.train_ratio}")
        if self.val_ratio < 0.0:
            raise ValueError(f"val_ratio cannot be negative, got {self.val_ratio}")
        if self.oos_ratio <= 0.0:
            raise ValueError(f"oos_ratio must be > 0.0, got {self.oos_ratio}")
        ratio_sum = round(self.train_ratio + self.val_ratio + self.oos_ratio, 4)
        if abs(ratio_sum - 1.0) > 1e-4:
            raise ValueError(f"Sum of train ({self.train_ratio}), val ({self.val_ratio}), and oos ({self.oos_ratio}) ratios must equal 1.0 (got {ratio_sum})")
        if self.embargo_seconds < 0.0:
            raise ValueError(f"embargo_seconds cannot be negative, got {self.embargo_seconds}")


@dataclass(frozen=True)
class XauUsdFoldResult:
    """Evaluation output for a single chronological walk-forward fold."""
    fold_id: int
    spec: XauUsdFoldSpec
    train_metrics: XauUsdBacktestMetrics
    val_metrics: Optional[XauUsdBacktestMetrics]
    oos_metrics: XauUsdBacktestMetrics
    train_trade_count: int
    val_trade_count: int
    oos_trade_count: int
    train_trades: Tuple[XauUsdSimulatedTrade, ...] = ()
    val_trades: Tuple[XauUsdSimulatedTrade, ...] = ()
    oos_trades: Tuple[XauUsdSimulatedTrade, ...] = ()


@dataclass(frozen=True)
class XauUsdWalkForwardResult:
    """Consolidated chronological walk-forward validation report across all folds."""
    wf_config: XauUsdWalkForwardConfig
    run_fingerprint: str
    folds: Tuple[XauUsdFoldResult, ...]
    oos_aggregated_metrics: XauUsdBacktestMetrics
    temporal_stability_score: float
    fold_expectancies_r: Tuple[float, ...]


@dataclass(frozen=True)
class XauUsdAblationDelta:
    """Expectancy, profit factor, and cost differences between ablated variant and baseline."""
    delta_expectancy_r: float
    delta_profit_factor: float
    delta_win_rate: float
    delta_trade_count: int
    delta_cost_drag_r: float


@dataclass(frozen=True)
class XauUsdAblationComparison:
    """Paired factor comparison against the production-calibrated baseline."""
    ablation_type: XauUsdAblationType
    baseline_metrics: XauUsdBacktestMetrics
    ablated_metrics: XauUsdBacktestMetrics
    delta: XauUsdAblationDelta


@dataclass(frozen=True)
class XauUsdAblationReport:
    """Consolidated paired factor ablation report with immutability proof."""
    baseline_run_spec: XauUsdBacktestRunSpec
    baseline_metrics: XauUsdBacktestMetrics
    comparisons: Tuple[XauUsdAblationComparison, ...]
    baseline_hash: str
    immutability_verified: bool
