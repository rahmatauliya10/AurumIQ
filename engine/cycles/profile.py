"""
Phase 3A Robust Time Cycle Profile Architecture.

Provides explicit, immutable profile configurations and calibration governance
for cycle intelligence. Ensures strict segregation between historical frozen
XAUT reference numbers and target XAUUSD configuration, preventing uncalibrated
or candidate instruments from silently inheriting legacy numerical fallbacks.
"""
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from engine.core.types import (
    CalendarEffectEntry,
    RegimeType,
    SessionExpectancyEntry,
    SessionType,
)


class CalibrationStatus(str, Enum):
    """Authoritative lifecycle status for cycle calibration governance."""
    LEGACY_REFERENCE = "LEGACY_REFERENCE"          # Historical XAUT frozen reference (scoring enabled)
    PENDING_DATA = "PENDING_DATA"                  # Target instrument uncalibrated (scoring disabled)
    CANDIDATE_NOT_FROZEN = "CANDIDATE_NOT_FROZEN"  # Empirical candidate generated (scoring strictly disabled)
    PRODUCTION_FROZEN = "PRODUCTION_FROZEN"        # Reserved for post-Phase 6 governance (never produced in Phase 3A)


@dataclass(frozen=True)
class Cycle3AProfile:
    """
    Immutable specification of numerical thresholds, weights, and empirical tables
    for Phase 3A Robust Time Cycle intelligence.
    """
    name: str = "LEGACY_XAUT_REFERENCE"
    calibration_status: CalibrationStatus = CalibrationStatus.LEGACY_REFERENCE
    target_instrument: str = "XAUT"
    timeframe: Optional[str] = None

    # 1. Session Cycle Parameters (None in uncalibrated / candidate profiles)
    session_max_score: Optional[float] = 15.0
    session_min_effective_n: Optional[float] = 30.0
    session_expectancy_multiplier: Optional[float] = 30.0
    session_expectancy_table: Optional[Mapping[Tuple[SessionType, RegimeType], SessionExpectancyEntry]] = None

    # 2. Swing Duration Parameters (None in uncalibrated / candidate profiles)
    swing_max_score: Optional[float] = 20.0
    swing_min_effective_n: Optional[float] = 30.0
    swing_maturity_bands: Optional[Mapping[str, float]] = None
    historical_durations: Optional[Tuple[int, ...]] = None
    swing_duration_percentiles: Optional[Mapping[str, float]] = None

    # 3. Calendar Seasonality Parameters (None in uncalibrated / candidate profiles)
    calendar_max_score: Optional[float] = 5.0
    calendar_min_effective_n: Optional[float] = 30.0
    calendar_stability_threshold: Optional[float] = 0.60
    calendar_expectancy_multiplier: Optional[float] = 10.0
    calendar_effect_table: Optional[Mapping[str, CalendarEffectEntry]] = None

    # 4. Macroeconomic Event Parameters (None in uncalibrated profiles)
    macro_blackout_pre_minutes: Optional[int] = 30
    macro_blackout_post_minutes: Optional[int] = 30
    macro_clear_window_far_minutes: Optional[int] = 120
    macro_clear_window_near_minutes: Optional[int] = 60
    macro_clear_bonus_far: Optional[float] = 5.0
    macro_clear_bonus_near: Optional[float] = 2.0

    # 5. Metadata / Provenance (Strictly immutable mapping)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Enforce strict defensive immutability across all mapping and sequence attributes."""
        # Defensive immutable details
        object.__setattr__(
            self,
            "details",
            MappingProxyType(dict(self.details)) if self.details else MappingProxyType({}),
        )

        # Defensive immutable session table
        if self.session_expectancy_table is not None:
            object.__setattr__(
                self,
                "session_expectancy_table",
                MappingProxyType(dict(self.session_expectancy_table)),
            )

        # Defensive immutable swing maturity bands
        if self.swing_maturity_bands is not None:
            object.__setattr__(
                self,
                "swing_maturity_bands",
                MappingProxyType(dict(self.swing_maturity_bands)),
            )

        # Defensive immutable historical durations tuple
        if self.historical_durations is not None:
            object.__setattr__(
                self,
                "historical_durations",
                tuple(self.historical_durations),
            )

        # Defensive immutable swing percentiles
        if self.swing_duration_percentiles is not None:
            object.__setattr__(
                self,
                "swing_duration_percentiles",
                MappingProxyType(dict(self.swing_duration_percentiles)),
            )

        # Defensive immutable calendar table
        if self.calendar_effect_table is not None:
            object.__setattr__(
                self,
                "calendar_effect_table",
                MappingProxyType(dict(self.calendar_effect_table)),
            )

    @property
    def is_calibrated(self) -> bool:
        """
        Legacy compatibility property.
        In Phase 3A, only LEGACY_REFERENCE has historical empirical scoring active.
        CANDIDATE_NOT_FROZEN and PENDING_DATA are strictly not production-enabled.
        """
        return self.calibration_status == CalibrationStatus.LEGACY_REFERENCE

    @property
    def is_production_scoring_enabled(self) -> bool:
        """Production scoring is ONLY enabled for historical reference or frozen production profiles."""
        return self.calibration_status in (
            CalibrationStatus.LEGACY_REFERENCE,
            CalibrationStatus.PRODUCTION_FROZEN,
        )

    @classmethod
    def legacy_xaut_profile(cls) -> "Cycle3AProfile":
        """
        Historical XAUT frozen reference profile.
        Preserves historical Phase 3A behavior byte-for-byte.
        """
        return cls(
            name="LEGACY_XAUT_REFERENCE",
            calibration_status=CalibrationStatus.LEGACY_REFERENCE,
            target_instrument="XAUT",
            timeframe=None,
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
            swing_duration_percentiles=None,
            calendar_max_score=5.0,
            calendar_min_effective_n=30.0,
            calendar_stability_threshold=0.60,
            calendar_expectancy_multiplier=10.0,
            calendar_effect_table=None,
            macro_blackout_pre_minutes=30,
            macro_blackout_post_minutes=30,
            macro_clear_window_far_minutes=120,
            macro_clear_window_near_minutes=60,
            macro_clear_bonus_far=5.0,
            macro_clear_bonus_near=2.0,
            details={
                "instrument": "XAUT",
                "calibration_status": CalibrationStatus.LEGACY_REFERENCE.value,
                "description": "Historical frozen XAUT reference profile.",
            },
        )

    @classmethod
    def uncalibrated_xauusd_profile(cls, timeframe: Optional[str] = None) -> "Cycle3AProfile":
        """
        Explicitly uncalibrated profile for XAUUSD with NO configured empirical numerical boundaries.
        Guarantees zero hidden fallback to historical XAUT reference values.
        """
        return cls(
            name="XAUUSD_UNCALIBRATED",
            calibration_status=CalibrationStatus.PENDING_DATA,
            target_instrument="XAUUSD",
            timeframe=timeframe,
            session_max_score=None,
            session_min_effective_n=None,
            session_expectancy_multiplier=None,
            session_expectancy_table=None,
            swing_max_score=None,
            swing_min_effective_n=None,
            swing_maturity_bands=None,
            historical_durations=None,
            swing_duration_percentiles=None,
            calendar_max_score=None,
            calendar_min_effective_n=None,
            calendar_stability_threshold=None,
            calendar_expectancy_multiplier=None,
            calendar_effect_table=None,
            macro_blackout_pre_minutes=None,
            macro_blackout_post_minutes=None,
            macro_clear_window_far_minutes=None,
            macro_clear_window_near_minutes=None,
            macro_clear_bonus_far=None,
            macro_clear_bonus_near=None,
            details={
                "instrument": "XAUUSD",
                "calibration_status": CalibrationStatus.PENDING_DATA.value,
                "reason": "XAUUSD empirical cycle parameters not configured.",
            },
        )
