from engine.backtest.ablation import AblatedSignalEngine, AblationLab
from engine.backtest.folds import ChronologicalFoldGenerator
from engine.backtest.metrics import BacktestMetricsCalculator
from engine.backtest.outcomes import OutcomeEngine
from engine.backtest.purge import PurgeEngine
from engine.backtest.replay import PointInTimeReplay
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import BacktestRunner
from engine.backtest.types import (
    AblationComparison,
    AblationDelta,
    AblationReport,
    AblationSpec,
    AblationType,
    BacktestCostConfig,
    BacktestMetrics,
    BacktestRunResult,
    BacktestRunSpec,
    CostScenario,
    FoldDataResult,
    FoldRole,
    FoldSpec,
    FoldType,
    PurgeResult,
    SelectionPolicy,
    SimulatedTrade,
    SubsystemPerformance,
    TemporalStabilityReport,
    TradeOutcome,
    WalkForwardConfig,
    WalkForwardResult,
)
from engine.backtest.walkforward import WalkForwardEngine
from engine.backtest.xauusd_ablation import XauUsdAblationEngine
from engine.backtest.xauusd_fingerprint import (
    compute_xauusd_backtest_fingerprint,
    compute_xauusd_dataset_identity,
)
from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdAblationComparison,
    XauUsdAblationDelta,
    XauUsdAblationReport,
    XauUsdAblationType,
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdFoldResult,
    XauUsdFoldSpec,
    XauUsdSimulatedTrade,
    XauUsdSubsystemBreakdown,
    XauUsdTradeOutcome,
    XauUsdWalkForwardConfig,
    XauUsdWalkForwardResult,
)
from engine.backtest.xauusd_walkforward import XauUsdWalkForwardEngine

__all__ = [
    "AblatedSignalEngine",
    "AblationComparison",
    "AblationDelta",
    "AblationLab",
    "AblationReport",
    "AblationSpec",
    "AblationType",
    "BacktestCostConfig",
    "BacktestMetrics",
    "BacktestMetricsCalculator",
    "BacktestRunResult",
    "BacktestRunSpec",
    "BacktestRunner",
    "ChronologicalFoldGenerator",
    "CostModel",
    "CostScenario",
    "FoldDataResult",
    "FoldRole",
    "FoldSpec",
    "FoldType",
    "OutcomeEngine",
    "PointInTimeDataset",
    "PointInTimeReplay",
    "PurgeEngine",
    "PurgeResult",
    "ReplayClock",
    "SelectionPolicy",
    "SimulatedTrade",
    "SubsystemPerformance",
    "TemporalStabilityReport",
    "TradeOutcome",
    "WalkForwardConfig",
    "WalkForwardEngine",
    "WalkForwardResult",
    "compute_backtest_fingerprint",
    # XAUUSD Phase 6 Additions
    "XauUsdAblationComparison",
    "XauUsdAblationDelta",
    "XauUsdAblationEngine",
    "XauUsdAblationReport",
    "XauUsdAblationType",
    "XauUsdBacktestMetrics",
    "XauUsdBacktestRunSpec",
    "XauUsdBacktestRunner",
    "XauUsdCostConfig",
    "XauUsdCostScenario",
    "XauUsdFoldResult",
    "XauUsdFoldSpec",
    "XauUsdMetricsCalculator",
    "XauUsdOutcomeEngine",
    "XauUsdPointInTimeReplay",
    "XauUsdSimulatedTrade",
    "XauUsdSubsystemBreakdown",
    "XauUsdTradeOutcome",
    "XauUsdWalkForwardConfig",
    "XauUsdWalkForwardEngine",
    "XauUsdWalkForwardResult",
    "compute_xauusd_backtest_fingerprint",
    "compute_xauusd_dataset_identity",
]
