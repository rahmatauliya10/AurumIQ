"""Calendar seasonality analysis and rolling stability filter with empirical effect gate (P3A-10, P3A-16)."""
import calendar
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from engine.core.types import CalendarEffectEntry, CalendarSeasonalityContext, SampleQuality


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def calculate_calendar_seasonality(
    as_of: datetime,
    historical_fold_stabilities: Optional[Sequence[float]] = None,
    calendar_effect_table: Optional[Mapping[str, CalendarEffectEntry]] = None,
) -> CalendarSeasonalityContext:
    """
    Calculate calendar seasonality features and rolling stability score with empirical effect gating.

    Statistical No-Evidence & Empirical Effect Gate (P3A-10, P3A-16):
      - Stable folds alone without empirical directional effect/expectancy yield seasonality_score = 0.0.
      - If no calendar_effect_table, or bucket has effective_n < 30, or NOT is_statistically_significant,
        or expectancy_r <= 0, or stability < 0.60:
        seasonality_score strictly defaults to 0.0.
      - Positive score is only granted when empirical statistical evidence confirms an edge.

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

    # Calculate average stability across historical folds if provided
    stability = 0.0
    sample_quality = SampleQuality.INSUFFICIENT
    effective_n = 0.0

    if historical_fold_stabilities:
        valid_scores = [max(0.0, min(1.0, float(s))) for s in historical_fold_stabilities]
        n_folds = len(valid_scores)
        if n_folds > 0:
            stability = sum(valid_scores) / float(n_folds)
            stability = float(round(max(0.0, min(1.0, stability)), 4))

    # Evaluate against Empirical Calendar Effect Table (P3A-16)
    seasonality_score = 0.0
    if calendar_effect_table:
        bucket_key = "MONTH_END" if is_month_end else f"DOW_{dow}_HOUR_{hour}"
        entry = calendar_effect_table.get(bucket_key)
        if entry:
            effective_n = entry.effective_n
            entry_stability = entry.stability if entry.stability > 0 else stability

            # Guardrail: requires effective_n >= 30, statistical significance, and stability >= 0.60
            if (
                effective_n >= 30.0
                and entry.is_statistically_significant
                and entry.expectancy_r > 0
                and entry_stability >= 0.60
            ):
                if effective_n < 60.0:
                    sample_quality = SampleQuality.LOW
                    weight_mult = 0.5
                elif effective_n < 100.0:
                    sample_quality = SampleQuality.MEDIUM
                    weight_mult = 0.8
                else:
                    sample_quality = SampleQuality.HIGH
                    weight_mult = 1.0

                raw_score = min(5.0, entry.expectancy_r * 10.0 * entry_stability)
                seasonality_score = float(round(raw_score * weight_mult, 2))
            else:
                sample_quality = SampleQuality.INSUFFICIENT
                seasonality_score = 0.0

    return CalendarSeasonalityContext(
        day_of_week=dow,
        day_name=day_name,
        hour_utc=hour,
        month=month,
        is_month_end_flow=is_month_end,
        stability_score=stability,
        seasonality_score=seasonality_score,
        sample_quality=sample_quality,
        effective_n=effective_n,
    )
