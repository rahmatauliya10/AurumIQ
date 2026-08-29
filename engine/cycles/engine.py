"""Robust Time Cycle Engine (Phase 3A) consolidating deterministic timing features."""
from datetime import datetime
from typing import Optional, Sequence

from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    MacroEvent,
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
      1. Session classification with DST awareness via zoneinfo (A02).
      2. Swing duration & pullback age percentiles from causal swing timestamps.
      3. Macroeconomic event blackout gate (A06) and PiT revision safety (A26).
      4. Calendar seasonality flows with rolling stability filter.
      5. Consolidates into immutable Cycle3ASnapshot.
    """

    def __init__(self, cycle_version: str = "3.0.0-3A", blackout_minutes: int = 30):
        self.cycle_version = cycle_version
        self.blackout_minutes = blackout_minutes

    def analyze(
        self,
        latest_candle: CandleData,
        structure: StructureResult,
        macro_events: Optional[Sequence[MacroEvent]] = None,
        historical_durations: Optional[Sequence[int]] = None,
        historical_stabilities: Optional[Sequence[float]] = None,
    ) -> Cycle3ASnapshot:
        """
        Execute full Phase 3A time cycle analysis at the timestamp of the latest closed candle.
        """
        as_of = latest_candle.timestamp_close if latest_candle.is_closed else latest_candle.timestamp_open

        # 1. Trading Session Cycle (A02)
        session_ctx = classify_session(as_of)

        # 2. Swing Duration Maturity
        swing_ctx = calculate_swing_duration(
            latest_candle=latest_candle,
            structure=structure,
            historical_durations=historical_durations,
        )

        # 3. Macro Event Risk & Revision Gate (A06, A26)
        macro_ctx = evaluate_macro_event_risk(
            as_of=as_of,
            events=macro_events or [],
            blackout_minutes=self.blackout_minutes,
        )

        # 4. Calendar Seasonality
        calendar_ctx = calculate_calendar_seasonality(
            as_of=as_of,
            historical_fold_stabilities=historical_stabilities,
        )

        # Hard Risk Gate: If high-impact event is in blackout, cycle score is blocked
        is_blocked = macro_ctx.is_in_blackout

        if is_blocked:
            total_cycle_score = 0.0
        else:
            # Phase 3A Scoring Weights:
            # Session Expectancy: Max 15.0
            # Swing Maturity:     Max 20.0
            # Calendar Season:    Max 5.0
            # Macro Event Proximity bonus: Max 5.0 (if clear of all events for > 120m)
            macro_clear_bonus = 5.0 if (macro_ctx.minutes_to_next_event is None or macro_ctx.minutes_to_next_event > 120) else 2.0
            raw_score = (
                session_ctx.expectancy_score +
                swing_ctx.maturity_score +
                calendar_ctx.seasonality_score +
                macro_clear_bonus
            )
            # Normalized 3A timing score out of 45.0 max points
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
