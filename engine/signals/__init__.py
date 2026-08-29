"""Phase 4 Direction Score, Timing Score, and Selective Gate Signals package."""
from engine.signals.direction import calculate_direction_score
from engine.signals.timing import calculate_timing_score
from engine.signals.gate import evaluate_hard_gates, evaluate_selective_gate
from engine.signals.explainer import (
    compute_canonical_fingerprint,
    compute_research_fingerprint,
    explain_signal,
)
from engine.signals.engine import XautSignalEngine

__all__ = [
    "calculate_direction_score",
    "calculate_timing_score",
    "evaluate_hard_gates",
    "evaluate_selective_gate",
    "compute_canonical_fingerprint",
    "compute_research_fingerprint",
    "explain_signal",
    "XautSignalEngine",
]
