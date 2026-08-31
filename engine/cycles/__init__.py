"""Time cycle analysis package (Phase 3A: Robust Time Cycle & Phase 3B: Experimental Spectral)."""
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import calculate_swing_duration
from engine.cycles.events import evaluate_macro_event_risk
from engine.cycles.calendar import calculate_calendar_seasonality
from engine.cycles.engine import RobustTimeCycleEngine
from engine.cycles.profile import CalibrationStatus, Cycle3AProfile
from engine.cycles.calibration import (
    CalibrationProvenance,
    Cycle3ACalibrationArtifact,
    calibrate_session_expectancy,
    calibrate_swing_durations,
    build_profile_from_artifact,
)
from engine.cycles.experimental import (
    calculate_causal_acf,
    calculate_causal_fft,
    calculate_causal_wavelet,
    calculate_causal_hilbert,
    evaluate_cycle_reliability,
    evaluate_promotion_eligibility,
    ExperimentalTimeCycleEngine,
)

__all__ = [
    "classify_session",
    "calculate_swing_duration",
    "evaluate_macro_event_risk",
    "calculate_calendar_seasonality",
    "RobustTimeCycleEngine",
    "CalibrationStatus",
    "Cycle3AProfile",
    "CalibrationProvenance",
    "Cycle3ACalibrationArtifact",
    "calibrate_session_expectancy",
    "calibrate_swing_durations",
    "build_profile_from_artifact",
    "calculate_causal_acf",
    "calculate_causal_fft",
    "calculate_causal_wavelet",
    "calculate_causal_hilbert",
    "evaluate_cycle_reliability",
    "evaluate_promotion_eligibility",
    "ExperimentalTimeCycleEngine",
]
