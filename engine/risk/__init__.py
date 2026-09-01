"""Risk planning, intrabar resolution, and causal entry execution engine (Phase 5)."""
from engine.risk.execution import EntryExecutionModel
from engine.risk.intrabar import IntrabarResolver
from engine.risk.planner import RiskPlanner
from engine.risk.stops import calculate_stops
from engine.risk.targets import calculate_targets

# Phase 5 XAUUSD Additive Exports
from engine.risk.xauusd_execution import (
    SideAwareEntryExecutionModel,
    validate_xauusd_candle,
    validate_xauusd_quote,
)
from engine.risk.xauusd_fingerprints import (
    canonical_utc_timestamp,
    compute_candle_evidence_fingerprint,
    compute_execution_fingerprint,
    compute_phase5_policy_fingerprint,
    compute_quote_evidence_fingerprint,
    compute_risk_plan_fingerprint,
    compute_zone_fingerprint,
)
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)
from engine.risk.xauusd_stops import (
    calculate_long_stops,
    calculate_short_stops,
)
from engine.risk.xauusd_targets import (
    calculate_long_targets,
    calculate_short_targets,
)

__all__ = [
    # Historical XAUT exports (preserved)
    "calculate_stops",
    "calculate_targets",
    "RiskPlanner",
    "IntrabarResolver",
    "EntryExecutionModel",
    # Additive XAUUSD Phase 5 exports
    "SideRiskPolicy",
    "XauUsdExecutionPolicy",
    "XauUsdRiskProfile",
    "uncalibrated_xauusd_risk_profile",
    "canonical_utc_timestamp",
    "compute_zone_fingerprint",
    "compute_phase5_policy_fingerprint",
    "compute_quote_evidence_fingerprint",
    "compute_candle_evidence_fingerprint",
    "compute_risk_plan_fingerprint",
    "compute_execution_fingerprint",
    "calculate_long_stops",
    "calculate_short_stops",
    "calculate_long_targets",
    "calculate_short_targets",
    "XauUsdRiskPlanner",
    "validate_xauusd_quote",
    "validate_xauusd_candle",
    "SideAwareEntryExecutionModel",
    "SideAwareIntrabarResolver",
]

