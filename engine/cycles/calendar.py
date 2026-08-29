"""Calendar seasonality analysis and rolling stability filter with no-evidence gate."""
import calendar
from datetime import datetime, timezone
from typing import Optional, Sequence

from engine.core.types import CalendarSeasonalityContext, SampleQuality


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def calculate_calendar_seasonality(
    as_of: datetime,
    historical_fold_stabilities: Optional[Sequence[float]] = None,
) -> CalendarSeasonalityContext:
    """
    Calculate calendar seasonality features and rolling stability score.

    Statistical No-Evidence Gate (P3A-10):
      - If historical_fold_stabilities is None or empty:
        stability_score = 0.0, seasonality_score = 0.0, sample_quality = INSUFFICIENT.
      - If stability coefficient < 0.60, seasonality score contribution strictly defaults to 0.0.

    Precise Month-End Flow:
      - Uses calendar.monthrange to calculate exact month length.
      - is_month_end_flow is True only for the final 3 calendar days of the month.
    """
    if as_of.tzinfo is None:
        dt_utc = as_of.replace(tzinfo=timezone.utc)
    else:
        dt_utc = as_of.astimezone(timezone.utc)

    dow = dt_utc.weekday()
    day_name = DAY_NAMES[dow]
    hour = dt_utc.hour
    year = dt_utc.year
    month = dt_utc.month
    day = dt_utc.day

    # Accurate month-end flow based on exact days in month
    _, total_days_in_month = calendar.monthrange(year, month)
    is_month_end = (total_days_in_month - day) < 3

    # Fail-safe zero-evidence check
    if not historical_fold_stabilities:
        return CalendarSeasonalityContext(
            day_of_week=dow,
            day_name=day_name,
            hour_utc=hour,
            month=month,
            is_month_end_flow=is_month_end,
            stability_score=0.0,
            seasonality_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    valid_scores = [max(0.0, min(1.0, float(s))) for s in historical_fold_stabilities]
    n_folds = len(valid_scores)
    stability = sum(valid_scores) / float(n_folds) if n_folds > 0 else 0.0
    stability = float(round(max(0.0, min(1.0, stability)), 4))

    if n_folds < 3:
        sample_quality = SampleQuality.LOW
    elif n_folds < 6:
        sample_quality = SampleQuality.MEDIUM
    else:
        sample_quality = SampleQuality.HIGH

    # Guardrail: unstable seasonal bias drops score contribution to 0.0
    if stability < 0.60:
        seasonality_score = 0.0
    else:
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
        sample_quality=sample_quality,
    )
