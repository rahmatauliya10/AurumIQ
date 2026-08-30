"""Core types, dataclasses, and enums for deterministic Phase 6 backtesting."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.core.types import (
    IntrabarPolicy,
    RegimeType,
    SessionType,
    SignalSnapshot,
    UserDecision,
)


class CostScenario(str, Enum):
    """Backtest cost friction scenarios."""
    IDEALIZED = "IDEALIZED"
    REALISTIC = "REALISTIC"


class TradeOutcome(str, Enum):
    """
    Primary terminal outcomes for simulated trades (P6-C2).
    Phase 6A enforces TP1 as the terminal profit target.
    TP2 does not alter baseline terminal P&L.
    """
    TP1_FIRST = "TP1_FIRST"
    SL_FIRST = "SL_FIRST"
    NO_FILL = "NO_FILL"
    SKIPPED = "SKIPPED"
    CONSERVATIVE_SL_FIRST = "CONSERVATIVE_SL_FIRST"
    UNRESOLVED = "UNRESOLVED"


class FoldType(str, Enum):
    """Chronological walk-forward fold partitions."""
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    OOS = "OOS"


class AblationType(str, Enum):
    """Component ablation variants for research-only evaluation."""
    BASELINE = "BASELINE"
    NO_PHASE3A_TIMING = "NO_PHASE3A_TIMING"
    NO_XAU_BASIS_COMPONENT = "NO_XAU_BASIS_COMPONENT"
    NO_STRUCTURE_COMPONENT = "NO_STRUCTURE_COMPONENT"
    NO_MOMENTUM_COMPONENT = "NO_MOMENTUM_COMPONENT"
    NO_VOLUME_COMPONENT = "NO_VOLUME_COMPONENT"
    NO_REVERSAL_COMPONENT = "NO_REVERSAL_COMPONENT"
    NO_MACRO_SAFETY = "NO_MACRO_SAFETY"


@dataclass(frozen=True)
class BacktestCostConfig:
    """
    Configurable trading friction and cost model parameters.
    Friction values are specified in basis points (1 bps = 0.01% = 0.0001).
    """
    entry_fee_bps: Decimal = Decimal("0.0")
    exit_fee_bps: Decimal = Decimal("0.0")
    synthetic_spread_bps: Decimal = Decimal("0.0")
    entry_slippage_bps: Decimal = Decimal("0.0")
    exit_slippage_bps: Decimal = Decimal("0.0")

    @classmethod
    def idealized(cls) -> "BacktestCostConfig":
        """Zero friction baseline scenario."""
        return cls(
            entry_fee_bps=Decimal("0.0"),
            exit_fee_bps=Decimal("0.0"),
            synthetic_spread_bps=Decimal("0.0"),
            entry_slippage_bps=Decimal("0.0"),
            exit_slippage_bps=Decimal("0.0"),
        )

    @classmethod
    def realistic(
        cls,
        entry_fee_bps: Decimal = Decimal("4.0"),
        exit_fee_bps: Decimal = Decimal("4.0"),
        synthetic_spread_bps: Decimal = Decimal("5.0"),
        entry_slippage_bps: Decimal = Decimal("2.0"),
        exit_slippage_bps: Decimal = Decimal("2.0"),
    ) -> "BacktestCostConfig":
        """Realistic institutional retail friction scenario."""
        return cls(
            entry_fee_bps=entry_fee_bps,
            exit_fee_bps=exit_fee_bps,
            synthetic_spread_bps=synthetic_spread_bps,
            entry_slippage_bps=entry_slippage_bps,
            exit_slippage_bps=exit_slippage_bps,
        )


@dataclass(frozen=True)
class SimulatedTrade:
    """
    Immutable record of a simulated point-in-time trade lifecycle.
    P6-C3: Denominator R is strictly frozen to planned_risk_amount.
    """
    trade_id: str
    source_signal_fingerprint: str
    signal_timestamp: datetime
    risk_plan_fingerprint: str
    planned_risk_amount: Decimal  # entry_max - stop_final > 0
    outcome: TradeOutcome
    fill_timestamp: Optional[datetime] = None
    fill_price: Optional[Decimal] = None
    exit_timestamp: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
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
    ambiguity_policy: IntrabarPolicy = IntrabarPolicy.CONSERVATIVE_SL_FIRST
    fold_id: Optional[int] = None
    run_fingerprint: str = ""
    dependency_window: Tuple[datetime, datetime] = field(
        default_factory=lambda: (datetime.min, datetime.min)
    )
    tp2_reached_after_tp1: bool = False
    max_favorable_extension_r: Optional[Decimal] = None

    @property
    def dependency_end_timestamp(self) -> datetime:
        """Explicit end of label outcome dependency window (P6-C1, Phase 6B)."""
        return self.dependency_window[1] if self.dependency_window else self.signal_timestamp


@dataclass(frozen=True)
class SubsystemPerformance:
    """Performance metrics partitioned across structural subsystems."""
    regime_breakdown: Dict[str, Any] = field(default_factory=dict)
    session_breakdown: Dict[str, Any] = field(default_factory=dict)
    cycle_breakdown: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestMetrics:
    """
    Comprehensive performance, payoff profile, friction, and robustness metrics.
    P6-C4: Drawdown is strictly normalized trade-sequence drawdown in R.
    """
    # Sample Size & Funnel
    signal_count: int
    buy_window_count: int
    valid_risk_plan_count: int
    execution_eligible_count: int
    fill_count: int
    no_fill_count: int
    fill_rate: float
    no_fill_rate: float
    trade_count: int

    # Payoff Profile
    win_count: int
    loss_count: int
    win_rate: float
    loss_rate: float
    avg_win_r: float
    avg_loss_r: float
    payoff_ratio: float

    # Expectancy & Profitability
    gross_expectancy_r: float
    net_expectancy_r: float
    average_r: float
    median_r: float
    profit_factor: float
    gross_return_pct: float
    net_return_pct: float

    # Downside Risk (Normalized Trade Sequence)
    max_trade_sequence_drawdown_r: float
    drawdown_duration_trades: int
    maximum_consecutive_losses: int

    # Execution Quality (Post-Fill MFE & MAE)
    average_mfe_r: float
    median_mfe_r: float
    average_mae_r: float
    median_mae_r: float
    average_holding_duration_seconds: float
    median_holding_duration_seconds: float

    # Terminal Outcome Counts
    tp1_first_count: int
    sl_first_count: int
    conservative_sl_first_count: int
    unresolved_count: int
    conservative_resolution_rate: float

    # Friction & Drag
    total_entry_fees: float
    total_exit_fees: float
    total_spread_cost: float
    total_slippage_cost: float
    cost_drag_r: float
    cost_drag_pct: float

    # Decision Distributions
    wait_count: int
    avoid_count: int

    # Normalized Risk-Adjusted Series (Optional)
    normalized_daily_sharpe: Optional[float] = None
    normalized_daily_sortino: Optional[float] = None

    # Subsystem Breakdown
    subsystems: Optional[SubsystemPerformance] = None


@dataclass(frozen=True)
class BacktestRunSpec:
    """Immutable specification for a deterministic backtest execution."""
    instrument: str
    start_time: datetime
    end_time: datetime
    timeframes: Tuple[str, ...]
    cost_config: BacktestCostConfig
    cost_scenario: CostScenario
    dataset_hash: str
    engine_version: str = "4.0.0"
    config_version: str = "cfg-2026-v1"
    feature_version: str = "feat-2026-v1"
    cycle_version: str = "3.0.0-3A"
    risk_version: str = "5.0.0"
    execution_model_version: str = "5.0.0-exec-v1"
    backtest_version: str = "6.0.0"
    code_revision: str = "6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee"
    ablation_type: AblationType = AblationType.BASELINE


@dataclass(frozen=True)
class BacktestRunResult:
    """Complete, immutable result of a deterministic point-in-time backtest run."""
    run_spec: BacktestRunSpec
    run_fingerprint: str
    metrics: BacktestMetrics
    trades: Tuple[SimulatedTrade, ...]
    signals: Tuple[SignalSnapshot, ...]


# --- Phase 6B: Walk-Forward, Purge & Embargo Contracts ---

class FoldRole(str, Enum):
    """Role classification for chronological fold partitions."""
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    OOS = "OOS"


@dataclass(frozen=True)
class FoldSpec:
    """
    Specification for a single chronological walk-forward fold.
    Half-open intervals [start, end) strictly enforced.
    """
    fold_id: int
    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime
    val_start: Optional[datetime] = None
    val_end: Optional[datetime] = None
    embargo_duration_seconds: float = 0.0


@dataclass(frozen=True)
class PurgeResult:
    """Outcome of dependency-window purge and embargo filtering for a partition."""
    eligible_trades: Tuple[SimulatedTrade, ...]
    purged_trades: Tuple[SimulatedTrade, ...]
    embargoed_trades: Tuple[SimulatedTrade, ...]
    total_input_count: int


@dataclass(frozen=True)
class FoldDataResult:
    """Immutable metrics and trade ledgers for a completed walk-forward fold."""
    fold_id: int
    spec: FoldSpec
    train_metrics: BacktestMetrics
    oos_metrics: BacktestMetrics
    train_trades: Tuple[SimulatedTrade, ...]
    oos_trades: Tuple[SimulatedTrade, ...]
    validation_metrics: Optional[BacktestMetrics] = None
    validation_trades: Tuple[SimulatedTrade, ...] = field(default_factory=tuple)
    purged_count: int = 0
    embargoed_count: int = 0
    total_samples_before_filter: int = 0


@dataclass(frozen=True)
class TemporalStabilityReport:
    """Descriptive cross-fold stability summary across OOS evaluations."""
    total_folds: int
    positive_expectancy_folds: int
    oos_expectancies_r: Tuple[float, ...]
    oos_profit_factors: Tuple[float, ...]
    oos_drawdowns_r: Tuple[float, ...]
    median_oos_expectancy_r: float
    worst_oos_expectancy_r: float
    best_oos_expectancy_r: float
    aggregate_oos_metrics: BacktestMetrics
    is_stable_positive: bool


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configuration specification for chronological walk-forward analysis."""
    total_folds: int = 3
    train_ratio: float = 0.60
    val_ratio: float = 0.20
    oos_ratio: float = 0.20
    embargo_seconds: float = 86400.0  # 24h default
    purge_overlapping_dependencies: bool = True
    rolling_window: bool = False


@dataclass(frozen=True)
class WalkForwardResult:
    """Complete immutable result of chronological walk-forward analysis."""
    config: WalkForwardConfig
    folds: Tuple[FoldDataResult, ...]
    stability_report: TemporalStabilityReport
    walkforward_fingerprint: str


# --- Phase 6C: Research Ablation & Robustness Contracts ---

class SelectionPolicy(str, Enum):
    """Explicit candidate selection policy for walk-forward validation (no hidden defaults)."""
    VALIDATION_EXPECTANCY_R = "VALIDATION_EXPECTANCY_R"
    VALIDATION_PROFIT_FACTOR = "VALIDATION_PROFIT_FACTOR"
    TRAIN_EXPECTANCY_R = "TRAIN_EXPECTANCY_R"


@dataclass(frozen=True)
class AblationSpec:
    """Specification of a component ablation experiment."""
    ablation_type: AblationType
    description: str
    is_safety_critical: bool = False


@dataclass(frozen=True)
class AblationDelta:
    """Mathematical deltas (ablation - baseline) across paired historical folds."""
    delta_expectancy_r: float
    delta_profit_factor: float
    delta_drawdown_r: float
    delta_trade_count: int
    delta_fill_rate: float
    delta_cost_drag_r: float


@dataclass(frozen=True)
class AblationComparison:
    """
    Paired comparison of an ablated component variant against the frozen baseline.
    Evaluated across identical historical folds, datasets, cost models, and code revision.
    """
    ablation_type: AblationType
    baseline_run_fingerprint: str
    ablation_run_fingerprint: str
    baseline_metrics: BacktestMetrics
    ablation_metrics: BacktestMetrics
    delta: AblationDelta
    assessment: str  # IMPROVES, NEUTRAL, DEGRADES, UNSTABLE
    paired_fold_comparisons: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AblationReport:
    """Complete immutable research report containing all paired ablation comparisons."""
    baseline_spec: BacktestRunSpec
    baseline_metrics: BacktestMetrics
    comparisons: Tuple[AblationComparison, ...]
    report_fingerprint: str


