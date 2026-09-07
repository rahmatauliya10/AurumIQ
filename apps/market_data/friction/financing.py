"""Time-aware financing and overnight swap calculation for XAUUSD.

Adheres strictly to Pre-Phase-8 Calibration Governance:
- Rollover schedule: Summer 21:00 GMT+0 (US DST), Winter 22:00 GMT+0 (US Standard).
- Triple swap: Wednesday overnight rollover (covering Saturday and Sunday).
- Swap-free status: Evaluates actual_account_swap_free_status explicitly.
- Intra-session exemption: Trades closed before the rollover boundary incur exactly USD 0.00 swap.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def is_us_daylight_saving_time(dt: datetime) -> bool:
    """Determine if a UTC datetime falls within US Daylight Saving Time (DST).
    
    US DST starts on the second Sunday in March at 02:00 local time (07:00 UTC)
    and ends on the first Sunday in November at 02:00 local time (06:00 UTC).
    """
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware.")
    utc_dt = dt.astimezone(timezone.utc)
    year = utc_dt.year

    # Second Sunday in March
    # March 1 + days until Sunday + 7 days
    march_1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    days_to_first_sunday = (6 - march_1.weekday()) % 7
    first_sunday_march = march_1 + timedelta(days=days_to_first_sunday)
    second_sunday_march = first_sunday_march + timedelta(days=7)
    dst_start = second_sunday_march.replace(hour=7, minute=0, second=0, microsecond=0)

    # First Sunday in November
    nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    days_to_first_sunday_nov = (6 - nov_1.weekday()) % 7
    first_sunday_nov = nov_1 + timedelta(days=days_to_first_sunday_nov)
    dst_end = first_sunday_nov.replace(hour=6, minute=0, second=0, microsecond=0)

    return dst_start <= utc_dt < dst_end


def get_rollover_utc_hour(dt: datetime) -> int:
    """Get authoritative UTC rollover hour for the given date.
    
    Returns 21 during Summer (US DST), 22 during Winter (US Standard).
    """
    return 21 if is_us_daylight_saving_time(dt) else 22


def is_triple_swap_day(dt: datetime) -> bool:
    """Check if the rollover date is a triple-swap day (Wednesday in metals)."""
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware.")
    utc_dt = dt.astimezone(timezone.utc)
    # weekday: Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
    return utc_dt.weekday() == 2


def crosses_rollover_boundary(entry_dt: datetime, exit_dt: datetime) -> int:
    """Count how many daily rollover boundaries are crossed between entry and exit.
    
    Returns total number of rollover events crossed (0 for intra-session trades).
    """
    if entry_dt.tzinfo is None or exit_dt.tzinfo is None:
        raise ValueError("Datetimes must be timezone-aware.")
    if entry_dt > exit_dt:
        raise ValueError("entry_dt cannot be after exit_dt.")

    entry_utc = entry_dt.astimezone(timezone.utc)
    exit_utc = exit_dt.astimezone(timezone.utc)

    rollovers = 0
    # Walk from entry calendar date to exit calendar date
    curr_date = entry_utc.date()
    end_date = exit_utc.date() + timedelta(days=1)

    while curr_date <= end_date:
        # Construct candidate rollover datetime for this day
        dummy_dt = datetime(curr_date.year, curr_date.month, curr_date.day, 12, 0, tzinfo=timezone.utc)
        rollover_hr = get_rollover_utc_hour(dummy_dt)
        rollover_dt = datetime(
            curr_date.year, curr_date.month, curr_date.day,
            rollover_hr, 0, 0, tzinfo=timezone.utc
        )

        if entry_utc < rollover_dt <= exit_utc:
            # Check if this day is a weekday (rollovers don't happen on Sat/Sun)
            if rollover_dt.weekday() < 5:  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4
                rollovers += 1

        curr_date += timedelta(days=1)

    return rollovers


def calculate_overnight_swap_usd(
    volume_lots: Decimal,
    contract_size: Decimal,
    swap_points_per_day: Decimal,
    point_size: Decimal,
    rollover_dt: datetime,
    actual_account_swap_free_status: bool = False,
) -> Decimal:
    """Calculate overnight financing cost/credit in USD for a single rollover.
    
    Formula:
        multiplier = 3 if Wednesday else 1
        swap_usd = volume_lots * contract_size * (swap_points_per_day * point_size) * multiplier
    """
    if actual_account_swap_free_status:
        return Decimal("0.00")
    
    if volume_lots <= Decimal("0"):
        raise ValueError("volume_lots must be strictly positive.")
    if contract_size <= Decimal("0"):
        raise ValueError("contract_size must be strictly positive.")
    if point_size <= Decimal("0"):
        raise ValueError("point_size must be strictly positive.")

    multiplier = Decimal("3.0") if is_triple_swap_day(rollover_dt) else Decimal("1.0")
    point_amount = swap_points_per_day * point_size
    total_swap = volume_lots * contract_size * point_amount * multiplier
    return total_swap.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
