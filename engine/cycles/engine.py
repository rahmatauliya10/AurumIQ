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
from engine.cycles.profile import Cycle3AProfile
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
      6. Consolidates into immutable Cycle3ASnapshot with explicit Profile isolation.
    """

    def __init__(
        self,
        profile: Optional[Cycle3AProfile] = None,
        cycle_version: str = "3.0.0-3A",
        blackout_minutes: Optional[int] = None,
    ):
        self.profile = profile if profile is not None else Cycle3AProfile.legacy_xaut_profile()
        self.cycle_version = cycle_version
        self.blackout_minutes = blackout_minutes if blackout_minutes is not None else (self.profile.macro_blackout_minutes or 30)

    @classmethod
    def for_legacy_xaut(cls) -> "RobustTimeCycleEngine":
        """Factory method for verified historical XAUT reference profile."""
        return cls(profile=Cycle3AProfile.legacy_xaut_profile())

    @classmethod
    def for_xauusd(cls, profile: Optional[Cycle3AProfile] = None) -> "RobustTimeCycleEngine":
        """Factory method for XAUUSD target profile (uncalibrated fail-safe by default)."""
        return cls(profile=profile or Cycle3AProfile.uncalibrated_xauusd_profile())

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

        # Determine effective profile
        eff_profile = profile
        if eff_profile is None:
            if instrument is not None and instrument.upper().replace("/", "") in ("XAUUSD", "XAU"):
                if self.profile.is_calibrated and self.profile.name != "LEGACY_XAUT_REFERENCE":
                    eff_profile = self.profile
                else:
                    eff_profile = Cycle3AProfile.uncalibrated_xauusd_profile()
            else:
                eff_profile = self.profile

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
        macro_blackout = eff_profile.macro_blackout_minutes if (eff_profile and eff_profile.macro_blackout_minutes is not None) else self.blackout_minutes
        macro_ctx = evaluate_macro_event_risk(
            as_of=as_of,
            events=macro_events,
            blackout_minutes=macro_blackout,
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

        if is_blocked or not eff_profile.is_calibrated:
            total_cycle_score = 0.0
        else:
            # Macro clear bonus: ONLY granted if feed is healthy and event is verified far away (P3A-12)
            if macro_ctx.is_feed_healthy:
                far_win = eff_profile.macro_clear_window_far_minutes if eff_profile.macro_clear_window_far_minutes is not None else 120
                near_win = eff_profile.macro_clear_window_near_minutes if eff_profile.macro_clear_window_near_minutes is not None else 60
                bonus_far = eff_profile.macro_clear_bonus_far if eff_profile.macro_clear_bonus_far is not None else 5.0
                bonus_near = eff_profile.macro_clear_bonus_near if eff_profile.macro_clear_bonus_near is not None else 2.0

                if macro_ctx.minutes_to_next_event is not None and macro_ctx.minutes_to_next_event > far_win:
                    macro_clear_bonus = bonus_far
                elif macro_ctx.minutes_to_next_event is not None and macro_ctx.minutes_to_next_event > near_win:
                    macro_clear_bonus = bonus_near
                else:
                    macro_clear_bonus = 0.0
            else:
                macro_clear_bonus = 0.0  # Zero bonus if macro feed is missing or unverified

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
        )
