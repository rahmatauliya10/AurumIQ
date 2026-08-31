"""Phase 4 Direction Score, Timing Score, and Selective Gate Signals package."""
from engine.signals.direction import calculate_direction_score, calculate_xauusd_dual_direction
from engine.signals.timing import (
    calculate_timing_score,
    calculate_xauusd_dual_timing,
    extract_xauusd_phase3a_score,
)
from engine.signals.gate import evaluate_hard_gates, evaluate_selective_gate
from engine.signals.explainer import (
    compute_canonical_fingerprint,
    compute_research_fingerprint,
    explain_signal,
)
from engine.signals.engine import XautSignalEngine
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4FeedPolicy,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    compute_phase4_policy_fingerprint,
    normalize_xauusd_target,
    uncalibrated_xauusd_signal_profile,
)

__all__ = [
    "calculate_direction_score",
    "calculate_xauusd_dual_direction",
    "calculate_timing_score",
    "calculate_xauusd_dual_timing",
    "extract_xauusd_phase3a_score",
    "evaluate_hard_gates",
    "evaluate_selective_gate",
    "compute_canonical_fingerprint",
    "compute_research_fingerprint",
    "explain_signal",
    "XautSignalEngine",
    "Phase4CalibrationStatus",
    "Phase4FeedPolicy",
    "Phase4SignalProfile",
    "SideDirectionPolicy",
    "SideGatePolicy",
    "SideTimingPolicy",
    "compute_phase4_policy_fingerprint",
    "normalize_xauusd_target",
    "uncalibrated_xauusd_signal_profile",
]



