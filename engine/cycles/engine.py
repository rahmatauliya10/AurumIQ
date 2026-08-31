"""Robust Time Cycle Engine (Phase 3A) consolidating deterministic timing features."""
from datetime import datetime
from typing import Mapping, Optional, Sequence, Tuple

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    CalendarEffectEntry,
    CandleData,
    Cycle3ASnapshot,
    MacroEvent,
    RegimeType,
    SampleEvaluation,
    SessionExpectancyEntry,
    SessionType,
    StructureResult,
)
from engine.cycles.calendar import calculate_calendar_seasonality
from engine.cycles.events import evaluate_macro_event_risk
from engine.cycles.profile import CalibrationStatus, Cycle3AProfile
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import calculate_swing_duration


class RobustTimeCycleEngine:
    """
    Pure Python consolidated Phase 3A Robust Time Cycle engine.

    Responsibilities:
      1. Session classification with DST awareness via zoneinfo (A02) and empirical expectancy (P3A-06, P3A-14).
      2. Swing duration & pullback age percentiles from causal swing timestamps and knowable age (P3A-07, P3A-08, P3A-09, P3A-15).
      3. Macroeconomic event blackout gate (A06), PiT revision safety (A26, P3A-11), and missing feed safety (P3A-12).
      4. Calendar seasonality flows with empirical effect gate and exact month length (P3A-10, P3A-16).
      5. Enforces closed candle analysis boundary (P3A-17).
      6. Consolidates into immutable Cycle3ASnapshot with explicit Profile and CalibrationStatus isolation.
    """

    def __init__(
        self,
        profile: Optional[Cycle3AProfile] = None,
        cycle_version: str = "3.0.0-3A",
        blackout_minutes: Optional[int] = None,
    ):
        if profile is not None:
            self.profile = profile
        else:
            if blackout_minutes is not None:
                # Historical legacy customization
                self.profile = Cycle3AProfile(
                    name="LEGACY_XAUT_REFERENCE",
                    calibration_status=CalibrationStatus.LEGACY_REFERENCE,
                    target_instrument="XAUT",
                    macro_blackout_pre_minutes=blackout_minutes,
                    macro_blackout_post_minutes=blackout_minutes,
                )
            else:
                self.profile = Cycle3AProfile.legacy_xaut_profile()

        self.cycle_version = cycle_version
        self.blackout_minutes = blackout_minutes

    @classmethod
    def for_legacy_xaut(cls) -> "RobustTimeCycleEngine":
        """Factory method for verified historical XAUT reference profile."""
        return cls(profile=Cycle3AProfile.legacy_xaut_profile())

    @classmethod
    def for_xauusd(
        cls,
        profile: Optional[Cycle3AProfile] = None,
        timeframe: Optional[str] = None,
    ) -> "RobustTimeCycleEngine":
        """
        Factory method for XAUUSD target profile.
        Strictly validates target instrument matches XAUUSD and enforces PENDING_DATA by default.
        """
        if profile is not None:
            target = profile.target_instrument.upper().replace("/", "")
            if target != "XAUUSD":
                raise ValueError(
                    f"Invalid profile for XAUUSD engine: target instrument is '{profile.target_instrument}', "
                    f"expected 'XAUUSD'."
                )
            if timeframe is not None and profile.timeframe is not None and profile.timeframe != timeframe:
                raise ValueError(
                    f"Profile timeframe '{profile.timeframe}' does not match requested timeframe '{timeframe}'."
                )
            return cls(profile=profile)
        return cls(profile=Cycle3AProfile.uncalibrated_xauusd_profile(timeframe=timeframe))

    def analyze(
        self,
        latest_candle: CandleData,
        structure: StructureResult,
        timeframe: str = "15m",
        regime: Optional[RegimeType] = None,
        session_expectancy_table: Optional[Mapping[Tuple[SessionType, RegimeType], SessionExpectancyEntry]] = None,
        macro_events: Optional[Sequence[MacroEvent]] = None,
        historical_durations: Optional[Sequence[int]] = None,
        swing_effective_n: Optional[float] = None,
        swing_sample_eval: Optional[SampleEvaluation] = None,
        historical_stabilities: Optional[Sequence[float]] = None,
        calendar_effect_table: Optional[Mapping[str, CalendarEffectEntry]] = None,
        instrument: Optional[str] = None,
        profile: Optional[Cycle3AProfile] = None,
    ) -> Cycle3ASnapshot:
        """
        Execute full Phase 3A time cycle analysis at the timestamp of the latest closed candle.
        Raises IncompleteCandleError if latest_candle is unclosed (P3A-17).
        """
        if not latest_candle.is_closed:
            raise IncompleteCandleError(
                f"Phase 3A robust time cycle analysis requires a completed (closed) candle. "
                f"Received candle with is_closed=False at open={latest_candle.timestamp_open}."
            )

        as_of = latest_candle.timestamp_close

        # Determine effective profile with strict instrument segregation
        eff_profile = profile
        if eff_profile is None:
            if instrument is not None:
                norm_inst = instrument.upper().replace("/", "")
                if norm_inst == "XAUUSD":
                    if self.profile.target_instrument.upper().replace("/", "") == "XAUUSD":
                        eff_profile = self.profile
                    else:
                        eff_profile = Cycle3AProfile.uncalibrated_xauusd_profile(timeframe=timeframe)
                elif norm_inst in ("XAUT", "XAUTUSD"):
                    if self.profile.target_instrument.upper().replace("/", "") == "XAUT":
                        eff_profile = self.profile
                    else:
                        eff_profile = Cycle3AProfile.legacy_xaut_profile()
                else:
                    eff_profile = self.profile
            else:
                eff_profile = self.profile

        # Timeframe integrity validation
        if eff_profile.timeframe is not None and eff_profile.timeframe != timeframe:
            raise ValueError(
                f"Profile timeframe '{eff_profile.timeframe}' does not match analysis timeframe '{timeframe}'."
            )

        # 1. Trading Session Cycle (A02, P3A-06, P3A-14)
        session_ctx = classify_session(
            timestamp=as_of,
            regime=regime,
            expectancy_table=session_expectancy_table,
            profile=eff_profile,
        )

        # 2. Swing Duration Maturity (P3A-07, P3A-08, P3A-09, P3A-15)
        swing_ctx = calculate_swing_duration(
            latest_candle=latest_candle,
            structure=structure,
            timeframe=timeframe,
            historical_durations=historical_durations,
            effective_n=swing_effective_n,
            sample_eval=swing_sample_eval,
            profile=eff_profile,
        )

        # 3. Macro Event Risk & Revision Gate (A06, A26, P3A-11, P3A-12)
        macro_ctx = evaluate_macro_event_risk(
            as_of=as_of,
            events=macro_events,
            blackout_pre_minutes=eff_profile.macro_blackout_pre_minutes,
            blackout_post_minutes=eff_profile.macro_blackout_post_minutes,
            profile=eff_profile,
        )

        # 4. Calendar Seasonality (P3A-10, P3A-16)
        calendar_ctx = calculate_calendar_seasonality(
            as_of=as_of,
            historical_fold_stabilities=historical_stabilities,
            calendar_effect_table=calendar_effect_table,
            profile=eff_profile,
        )

        # Hard Risk Gate: If high-impact event is in blackout, cycle score is blocked
        is_blocked = macro_ctx.is_in_blackout

        if is_blocked or not eff_profile.is_production_scoring_enabled:
            total_cycle_score = 0.0
        else:
            # Macro clear bonus: ONLY granted if feed is healthy, scoring is enabled, and event is verified far away
            if macro_ctx.is_feed_healthy:
                far_win = eff_profile.macro_clear_window_far_minutes
                near_win = eff_profile.macro_clear_window_near_minutes
                bonus_far = eff_profile.macro_clear_bonus_far
                bonus_near = eff_profile.macro_clear_bonus_near

                if (
                    far_win is not None
                    and bonus_far is not None
                    and macro_ctx.minutes_to_next_event is not None
                    and macro_ctx.minutes_to_next_event > far_win
                ):
                    macro_clear_bonus = bonus_far
                elif (
                    near_win is not None
                    and bonus_near is not None
                    and macro_ctx.minutes_to_next_event is not None
                    and macro_ctx.minutes_to_next_event > near_win
                ):
                    macro_clear_bonus = bonus_near
                else:
                    macro_clear_bonus = 0.0
            else:
                macro_clear_bonus = 0.0

            raw_score = (
                session_ctx.expectancy_score +
                swing_ctx.maturity_score +
                calendar_ctx.seasonality_score +
                macro_clear_bonus
            )
            total_cycle_score = float(round(raw_score, 2))

        return Cycle3ASnapshot(
            timestamp=as_of,
            session=session_ctx,
            swing_duration=swing_ctx,
            macro_event=macro_ctx,
            calendar=calendar_ctx,
            is_blocked_by_event=is_blocked,
            cycle_score_3a=total_cycle_score,
            cycle_version=self.cycle_version,
            profile_name=eff_profile.name,
            calibration_status=eff_profile.calibration_status.value,
            calibration_artifact_version=eff_profile.details.get("calibration_version"),
        )
