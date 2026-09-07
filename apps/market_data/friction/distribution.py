"""Empirical distribution calculations and temporal sample sufficiency validation.

Adheres strictly to Pre-Phase-8 Calibration Governance:
- Computes robust percentiles: min, p50, p75, p90, p95, p99, max, mean, std.
- Evaluates temporal sample sufficiency: N >= 1,000, >= 5 distinct trading dates,
  session coverage (Asian, London, New York, Rollover), per-session thresholds.
- Strictly rejects naive timestamps, future timestamps, and non-positive spreads.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any, Dict, List, Tuple


def get_trading_session(dt: datetime) -> str:
    """Classify UTC datetime into primary market trading session."""
    if dt.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware.")
    utc_dt = dt.astimezone(timezone.utc)
    hour = utc_dt.hour

    if 0 <= hour < 8:
        return "ASIAN"
    elif 8 <= hour < 13:
        return "LONDON"
    elif 13 <= hour < 21:
        return "NEW_YORK"
    else:
        return "ROLLOVER"


def compute_percentile(sorted_values: List[Decimal], percentile: Decimal) -> Decimal:
    """Compute exact percentile from sorted Decimal values using linear interpolation."""
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty list.")
    if percentile < Decimal("0") or percentile > Decimal("100"):
        raise ValueError("Percentile must be between 0 and 100.")

    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]

    # Rank between 0 and n - 1
    rank = (percentile / Decimal("100")) * Decimal(n - 1)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))

    if lower_idx == upper_idx:
        return sorted_values[lower_idx]

    weight = rank - Decimal(lower_idx)
    interpolated = (
        sorted_values[lower_idx] * (Decimal("1") - weight)
        + sorted_values[upper_idx] * weight
    )
    return interpolated.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def compute_distribution_statistics(values: List[Decimal]) -> Dict[str, Decimal]:
    """Calculate comprehensive empirical distribution statistics from numeric values."""
    if not values:
        raise ValueError("Cannot calculate statistics on empty sample list.")

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    val_min = sorted_vals[0]
    val_max = sorted_vals[-1]

    sum_vals = sum(sorted_vals)
    mean_val = (sum_vals / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    if n > 1:
        variance = sum((x - mean_val) ** 2 for x in sorted_vals) / Decimal(n - 1)
        std_val = Decimal(str(math.sqrt(float(variance)))).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    else:
        std_val = Decimal("0.000000")

    p50 = compute_percentile(sorted_vals, Decimal("50"))
    p75 = compute_percentile(sorted_vals, Decimal("75"))
    p90 = compute_percentile(sorted_vals, Decimal("90"))
    p95 = compute_percentile(sorted_vals, Decimal("95"))
    p99 = compute_percentile(sorted_vals, Decimal("99"))

    return {
        "sample_count": Decimal(n),
        "stat_min": val_min.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        "stat_p50": p50,
        "stat_p75": p75,
        "stat_p90": p90,
        "stat_p95": p95,
        "stat_p99": p99,
        "stat_max": val_max.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        "stat_mean": mean_val,
        "stat_std": std_val,
    }


def validate_spread_dataset_sufficiency(
    ticks: List[Dict[str, Any]],
    min_samples: int = 1000,
    min_distinct_days: int = 5,
    min_major_session_count: int = 100,
    min_rollover_count: int = 30,
) -> Tuple[bool, List[str]]:
    """Validate empirical bid/ask spread dataset against objective coverage thresholds.
    
    Each tick item must contain:
      - 'timestamp': timezone-aware datetime (UTC)
      - 'bid': Decimal (> 0)
      - 'ask': Decimal (> 0, > bid)
      - 'spread_bps': Decimal (> 0)
    """
    errors: List[str] = []
    total_count = len(ticks)

    if total_count < min_samples:
        errors.append(
            f"Insufficient spread sample count: observed {total_count}, minimum required {min_samples}."
        )

    distinct_dates = set()
    session_counts: Dict[str, int] = {
        "ASIAN": 0,
        "LONDON": 0,
        "NEW_YORK": 0,
        "ROLLOVER": 0,
    }
    prev_ts: Optional[datetime] = None
    now_utc = datetime.now(timezone.utc)

    for i, tick in enumerate(ticks):
        ts = tick.get("timestamp")
        bid = tick.get("bid")
        ask = tick.get("ask")
        spread_bps = tick.get("spread_bps")

        if ts is None:
            errors.append(f"Row {i}: Missing timestamp.")
            break
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            errors.append(f"Row {i}: Naive or non-datetime timestamp '{ts}'. Timezone-aware UTC required.")
            break
        if ts > now_utc:
            errors.append(f"Row {i}: Future timestamp '{ts.isoformat()}' rejected.")
            break

        utc_ts = ts.astimezone(timezone.utc)
        if prev_ts and utc_ts < prev_ts:
            errors.append(f"Row {i}: Non-chronological timestamp sequence ({utc_ts} < {prev_ts}).")
            break
        prev_ts = utc_ts

        distinct_dates.add(utc_ts.date())
        session = get_trading_session(utc_ts)
        session_counts[session] = session_counts.get(session, 0) + 1

        if bid is None or ask is None:
            errors.append(f"Row {i}: Missing bid or ask price.")
            break
        if Decimal(str(bid)) <= Decimal("0") or Decimal(str(ask)) <= Decimal("0"):
            errors.append(f"Row {i}: Zero or negative quote prices (bid={bid}, ask={ask}).")
            break
        if Decimal(str(ask)) <= Decimal(str(bid)):
            errors.append(f"Row {i}: Inverted or crossed spread (ask {ask} <= bid {bid}).")
            break
        if spread_bps is not None and Decimal(str(spread_bps)) <= Decimal("0"):
            errors.append(f"Row {i}: Non-positive spread in bps: {spread_bps}.")
            break

    if len(distinct_dates) < min_distinct_days:
        errors.append(
            f"Insufficient temporal span: observed {len(distinct_dates)} distinct trading dates, minimum required {min_distinct_days}."
        )

    for major_session in ["ASIAN", "LONDON", "NEW_YORK"]:
        c = session_counts.get(major_session, 0)
        if c < min_major_session_count:
            errors.append(
                f"Insufficient session coverage for {major_session}: observed {c}, minimum required {min_major_session_count}."
            )

    rollover_c = session_counts.get("ROLLOVER", 0)
    if rollover_c < min_rollover_count:
        errors.append(
            f"Insufficient rollover session coverage: observed {rollover_c}, minimum required {min_rollover_count}."
        )

    is_valid = len(errors) == 0
    return is_valid, errors


def calculate_directional_slippage(
    side: str,
    executed_price: Decimal,
    reference_bid: Decimal,
    reference_ask: Decimal,
) -> Tuple[Decimal, Decimal]:
    """Calculate adverse price slippage in price units and basis points.
    
    Formula:
        For BUY: adverse_slippage = executed_price - reference_ask
        For SELL: adverse_slippage = reference_bid - executed_price
        slippage_bps = (adverse_slippage / reference_price) * 10000
    """
    side_upper = side.upper()
    if side_upper == "BUY" or side_upper == "LONG":
        reference_price = reference_ask
        adverse_disp = executed_price - reference_price
    elif side_upper == "SELL" or side_upper == "SHORT":
        reference_price = reference_bid
        adverse_disp = reference_price - executed_price
    else:
        raise ValueError(f"Unknown side '{side}'. Must be BUY/LONG or SELL/SHORT.")

    if reference_price <= Decimal("0"):
        raise ValueError("Reference quote price must be strictly positive.")

    slippage_bps = (adverse_disp / reference_price) * Decimal("10000")
    return (
        adverse_disp.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        slippage_bps.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
    )


def validate_slippage_telemetry_sufficiency(
    telemetry_records: List[Dict[str, Any]],
    min_fills: int = 30,
) -> Tuple[bool, List[str]]:
    """Validate execution telemetry fills for empirical slippage modeling."""
    errors: List[str] = []
    if len(telemetry_records) < min_fills:
        errors.append(
            f"Insufficient slippage telemetry fills: observed {len(telemetry_records)}, minimum required {min_fills}."
        )
        return False, errors

    required_fields = [
        "side", "decision_timestamp", "reference_bid", "reference_ask",
        "executed_fill_price", "fill_timestamp", "volume_lots", "latency_ms"
    ]

    for i, rec in enumerate(telemetry_records):
        for f in required_fields:
            if f not in rec or rec[f] is None:
                errors.append(f"Fill {i}: Missing required telemetry field '{f}'.")
                break
        fill_ts = rec.get("fill_timestamp")
        if fill_ts and (not isinstance(fill_ts, datetime) or fill_ts.tzinfo is None):
            errors.append(f"Fill {i}: Naive fill_timestamp. Timezone-aware UTC required.")
            break

    return len(errors) == 0, errors
