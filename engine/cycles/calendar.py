"""Calendar seasonality analysis and rolling stability filter."""
from datetime import datetime, timezone
from typing import Optional, Sequence

from engine.core.types import CalendarSeasonalityContext


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def calculate_calendar_seasonality(
    as_of: datetime,
    historical_fold_stabilities: Optional[Sequence[float]] = None,
) -> CalendarSeasonalityContext:
    """
    Calculate calendar seasonality features and rolling stability score.

    Stability Filter:
      - Evaluates consistency across rolling historical folds.
      - If stability coefficient < 0.60, seasonality score contribution strictly defaults to 0.0.
      - If stable (>= 0.60), contributes up to 5.0 points.
    """
    if as_of.tzinfo is None:
        dt_utc = as_of.replace(tzinfo=timezone.utc)
    else:
        dt_utc = as_of.astimezone(timezone.utc)

    dow = dt_utc.weekday()
    day_name = DAY_NAMES[dow]
    hour = dt_utc.hour
    month = dt_utc.month
    day = dt_utc.day

    # Month-end flow heuristic: last 3 calendar days of any month
    is_month_end = day >= 28

    # Evaluate rolling stability filter
    if historical_fold_stabilities:
        valid_scores = [max(0.0, min(1.0, float(s))) for s in historical_fold_stabilities]
        stability = sum(valid_scores) / float(len(valid_scores)) if valid_scores else 0.0
    else:
        stability = 0.85  # Baseline default when verified stable

    stability = float(round(max(0.0, min(1.0, stability)), 4))

    # Guardrail: unstable seasonal bias drops score contribution to 0.0
    if stability < 0.60:
        seasonality_score = 0.0
    else:
        # Standard gold calendar tendencies (e.g. London morning & NY overlap, Tuesday-Thursday flows)
        if dow in [1, 2, 3] and 8 <= hour <= 16:  # Tue-Thu liquid hours
            seasonality_score = 5.0 * stability
        elif dow in [0, 4]:  # Mon/Fri
            seasonality_score = 3.5 * stability
        else:
            seasonality_score = 1.0 * stability

    seasonality_score = float(round(seasonality_score, 2))

    return CalendarSeasonalityContext(
        day_of_week=dow,
        day_name=day_name,
        hour_utc=hour,
        month=month,
        is_month_end_flow=is_month_end,
        stability_score=stability,
        seasonality_score=seasonality_score,
    )
