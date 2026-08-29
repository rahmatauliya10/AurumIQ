"""Robust Time Cycle Engine (Phase 3A) consolidating deterministic timing features."""
from datetime import datetime
from typing import Mapping, Optional, Sequence, Tuple

from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    MacroEvent,
    RegimeType,
    SessionExpectancyEntry,
    SessionType,
    StructureResult,
)
from engine.cycles.calendar import calculate_calendar_seasonality
from engine.cycles.events import evaluate_macro_event_risk
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import calculate_swing_duration


class RobustTimeCycleEngine:
    """
    Pure Python consolidated Phase 3A Robust Time Cycle engine.

    Responsibilities:
      1. Session classification with DST awareness via zoneinfo (A02) and empirical expectancy.
      2. Swing duration & pullback age percentiles from causal swing timestamps and knowable age.
      3. Macroeconomic event blackout gate (A06), PiT revision safety (A26, P3A-11), and missing feed safety (P3A-12).
      4. Calendar seasonality flows with rolling stability filter and no-evidence gate (P3A-10).
      5. Consolidates into immutable Cycle3ASnapshot.
    """

    def __init__(self, cycle_version: str = "3.0.0-3A", blackout_minutes: int = 30):
        self.cycle_version = cycle_version
        self.blackout_minutes = blackout_minutes

    def analyze(
        self,
        latest_candle: CandleData,
        structure: StructureResult,
        timeframe: str = "15m",
        regime: Optional[RegimeType] = None,
        session_expectancy_table: Optional[Mapping[Tuple[SessionType, RegimeType], SessionExpectancyEntry]] = None,
        macro_events: Optional[Sequence[MacroEvent]] = None,
        historical_durations: Optional[Sequence[int]] = None,
        historical_stabilities: Optional[Sequence[float]] = None,
    ) -> Cycle3ASnapshot:
        """
        Execute full Phase 3A time cycle analysis at the timestamp of the latest closed candle.
        """
        as_of = latest_candle.timestamp_close if latest_candle.is_closed else latest_candle.timestamp_open

        # 1. Trading Session Cycle (A02 & P3A-06)
        session_ctx = classify_session(
            timestamp=as_of,
            regime=regime,
            expectancy_table=session_expectancy_table,
        )

        # 2. Swing Duration Maturity (P3A-07, P3A-08, P3A-09)
        swing_ctx = calculate_swing_duration(
            latest_candle=latest_candle,
            structure=structure,
            timeframe=timeframe,
            historical_durations=historical_durations,
        )

        # 3. Macro Event Risk & Revision Gate (A06, A26, P3A-11, P3A-12)
        macro_ctx = evaluate_macro_event_risk(
            as_of=as_of,
            events=macro_events,
            blackout_minutes=self.blackout_minutes,
        )

        # 4. Calendar Seasonality (P3A-10)
        calendar_ctx = calculate_calendar_seasonality(
            as_of=as_of,
            historical_fold_stabilities=historical_stabilities,
        )

        # Hard Risk Gate: If high-impact event is in blackout, cycle score is blocked
        is_blocked = macro_ctx.is_in_blackout

        if is_blocked:
            total_cycle_score = 0.0
        else:
            # Macro clear bonus: ONLY granted if feed is healthy and event is verified far away (P3A-12)
            if macro_ctx.is_feed_healthy:
                if macro_ctx.minutes_to_next_event is not None and macro_ctx.minutes_to_next_event > 120:
                    macro_clear_bonus = 5.0
                elif macro_ctx.minutes_to_next_event is not None and macro_ctx.minutes_to_next_event > 60:
                    macro_clear_bonus = 2.0
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
