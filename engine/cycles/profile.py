"""
Phase 3A Robust Time Cycle Profile Architecture.

Provides explicit, immutable profile configurations for cycle intelligence.
Ensures clean segregation between historical frozen XAUT reference numbers
and target XAUUSD configuration, preventing uncalibrated instruments from
silently inheriting legacy numerical fallbacks.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from engine.core.types import (
    CalendarEffectEntry,
    RegimeType,
    SessionExpectancyEntry,
    SessionType,
)


@dataclass(frozen=True)
class Cycle3AProfile:
    """
    Immutable specification of numerical thresholds, weights, and empirical tables
    for Phase 3A Robust Time Cycle intelligence.
    """
    name: str = "LEGACY_XAUT_REFERENCE"
    is_calibrated: bool = True

    # 1. Session Cycle Parameters
    session_max_score: Optional[float] = 15.0
    session_min_effective_n: Optional[float] = 30.0
    session_expectancy_multiplier: Optional[float] = 30.0
    session_expectancy_table: Optional[Mapping[Tuple[SessionType, RegimeType], SessionExpectancyEntry]] = None

    # 2. Swing Duration Parameters
    swing_max_score: Optional[float] = 20.0
    swing_min_effective_n: Optional[float] = 30.0
    swing_maturity_bands: Optional[Mapping[str, float]] = field(
        default_factory=lambda: {
            "P75_90": 20.0,
            "P50_75": 15.0,
            "P25_50": 10.0,
            "P90_plus": 8.0,
            "default": 5.0,
        }
    )
    historical_durations: Optional[Sequence[int]] = None

    # 3. Calendar Seasonality Parameters
    calendar_max_score: Optional[float] = 5.0
    calendar_min_effective_n: Optional[float] = 30.0
    calendar_stability_threshold: Optional[float] = 0.60
    calendar_expectancy_multiplier: Optional[float] = 10.0
    calendar_effect_table: Optional[Mapping[str, CalendarEffectEntry]] = None

    # 4. Macroeconomic Event Parameters
    macro_blackout_minutes: Optional[int] = 30
    macro_clear_window_far_minutes: Optional[int] = 120
    macro_clear_window_near_minutes: Optional[int] = 60
    macro_clear_bonus_far: Optional[float] = 5.0
    macro_clear_bonus_near: Optional[float] = 2.0

    # 5. Metadata / Provenance
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def legacy_xaut_profile(cls) -> "Cycle3AProfile":
        """
        Historical XAUT frozen reference profile.
        Preserves historical Phase 3A behavior byte-for-byte.
        """
        return cls(
            name="LEGACY_XAUT_REFERENCE",
            is_calibrated=True,
            session_max_score=15.0,
            session_min_effective_n=30.0,
            session_expectancy_multiplier=30.0,
            session_expectancy_table=None,
            swing_max_score=20.0,
            swing_min_effective_n=30.0,
            swing_maturity_bands={
                "P75_90": 20.0,
                "P50_75": 15.0,
                "P25_50": 10.0,
                "P90_plus": 8.0,
                "default": 5.0,
            },
            historical_durations=None,
            calendar_max_score=5.0,
            calendar_min_effective_n=30.0,
            calendar_stability_threshold=0.60,
            calendar_expectancy_multiplier=10.0,
            calendar_effect_table=None,
            macro_blackout_minutes=30,
            macro_clear_window_far_minutes=120,
            macro_clear_window_near_minutes=60,
            macro_clear_bonus_far=5.0,
            macro_clear_bonus_near=2.0,
            details={
                "instrument": "XAUT",
                "calibration_status": "FROZEN",
                "description": "Historical frozen XAUT reference profile.",
            },
        )

    @classmethod
    def uncalibrated_xauusd_profile(cls) -> "Cycle3AProfile":
        """
        Explicitly uncalibrated profile for XAUUSD with NO configured empirical numerical boundaries.
        Guarantees zero hidden fallback to historical XAUT reference values.
        """
        return cls(
            name="XAUUSD_UNCALIBRATED",
            is_calibrated=False,
            session_max_score=None,
            session_min_effective_n=None,
            session_expectancy_multiplier=None,
            session_expectancy_table=None,
            swing_max_score=None,
            swing_min_effective_n=None,
            swing_maturity_bands=None,
            historical_durations=None,
            calendar_max_score=None,
            calendar_min_effective_n=None,
            calendar_stability_threshold=None,
            calendar_expectancy_multiplier=None,
            calendar_effect_table=None,
            macro_blackout_minutes=None,
            macro_clear_window_far_minutes=None,
            macro_clear_window_near_minutes=None,
            macro_clear_bonus_far=None,
            macro_clear_bonus_near=None,
            details={
                "instrument": "XAUUSD",
                "calibration_status": "CALIBRATION_REQUIRED",
                "reason": "XAUUSD empirical cycle parameters not configured.",
            },
        )
