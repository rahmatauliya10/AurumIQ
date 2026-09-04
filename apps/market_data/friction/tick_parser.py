"""Production MT5 tick export parser for empirical spread derivation.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance (Directive 6):
- Explicit accepted schema detection, fail-closed on unknown schemas.
- Parses timestamp, bid, ask with Decimal precision.
- Derives mid, spread_price, spread_bps, trading session, trading date.
- Strict validation:
  * rejects missing bid or ask
  * rejects bid <= 0 or ask <= 0
  * rejects crossed/inverted quotes (ask <= bid)
  * rejects naive timestamps (unless timezone explicitly provided)
  * rejects future timestamps
  * rejects non-chronological tick ordering
  * rejects wrong symbol if symbol column present
  * rejects malformed rows and unsupported delimiters
"""
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
from typing import Any, Dict, List, Optional, Tuple

from apps.market_data.friction.distribution import get_trading_session


SUPPORTED_DELIMITERS = [",", "\t", ";"]


def parse_mt5_tick_export(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    server_tz: Optional[timezone] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse raw MT5 tick export bytes into normalized tick records and dataset metadata.
    
    Returns:
        (ticks_data, metadata_summary)
    Raises:
        ValueError if schema is unsupported, rows are malformed, or validation fails.
    """
    if not raw_content or len(raw_content.strip()) == 0:
        raise ValueError("Tick export payload is empty.")

    # Decode payload
    try:
        text = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw_content.decode("latin-1")
        except Exception as e:
            raise ValueError(f"Failed to decode tick export payload: {e}")

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Tick export file must contain a header and at least one data row.")

    header_line = lines[0]
    
    # Detect delimiter
    delimiter = None
    for d in SUPPORTED_DELIMITERS:
        if d in header_line:
            delimiter = d
            break
    if delimiter is None:
        raise ValueError("Unsupported delimiter in tick export. Expected comma, tab, or semicolon.")

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    raw_header = next(reader)
    header = [col.strip().lower().replace("<", "").replace(">", "") for col in raw_header]

    # Map column positions
    col_date: Optional[int] = None
    col_time: Optional[int] = None
    col_datetime: Optional[int] = None
    col_bid: Optional[int] = None
    col_ask: Optional[int] = None
    col_symbol: Optional[int] = None

    for idx, col in enumerate(header):
        if col in ("datetime", "timestamp", "date_time", "time_utc", "date time"):
            col_datetime = idx
        elif col == "date":
            col_date = idx
        elif col in ("time", "timestamp_time"):
            col_time = idx
        elif col == "bid":
            col_bid = idx
        elif col == "ask":
            col_ask = idx
        elif col in ("symbol", "instrument"):
            col_symbol = idx

    if col_bid is None or col_ask is None:
        raise ValueError(
            f"Unsupported tick export schema: Missing required bid/ask columns. Header observed: {raw_header}"
        )

    if col_datetime is None and col_date is None:
        raise ValueError(
            f"Unsupported tick export schema: Missing timestamp columns. Header observed: {raw_header}"
        )

    ticks: List[Dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)
    prev_ts: Optional[datetime] = None

    for row_idx, row in enumerate(reader, start=2):
        if not row or all(c.strip() == "" for c in row):
            continue

        if len(row) <= max(col_bid, col_ask):
            raise ValueError(f"Row {row_idx}: Malformed row has fewer columns than required ({len(row)} cols).")

        # Check symbol if present
        if col_symbol is not None and col_symbol < len(row):
            sym = row[col_symbol].strip().upper()
            if sym and sym != expected_symbol.upper():
                raise ValueError(
                    f"Row {row_idx}: Symbol mismatch. Expected '{expected_symbol}', observed '{sym}'."
                )

        # Parse timestamp string
        if col_datetime is not None:
            ts_str = row[col_datetime].strip()
        elif col_date is not None and col_time is not None:
            raw_date = row[col_date].strip()
            raw_time = row[col_time].strip()
            if " " in raw_date or "T" in raw_date:
                ts_str = raw_date
            else:
                ts_str = f"{raw_date} {raw_time}"
        elif col_date is not None:
            ts_str = row[col_date].strip()
        else:
            raise ValueError(f"Row {row_idx}: Missing timestamp value.")

        if not ts_str:
            raise ValueError(f"Row {row_idx}: Missing timestamp value.")

        # Normalize date portion only (e.g. YYYY.MM.DD -> YYYY-MM-DD)
        if len(ts_str) >= 10:
            norm_ts_str = ts_str[:10].replace(".", "-") + ts_str[10:]
        else:
            norm_ts_str = ts_str

        dt: Optional[datetime] = None
        
        # Try ISO format
        try:
            if "Z" in norm_ts_str or "+" in norm_ts_str or ("-" in norm_ts_str[10:] and len(norm_ts_str) > 19):
                dt = datetime.fromisoformat(norm_ts_str.replace("Z", "+00:00"))
            else:
                dt_naive = datetime.fromisoformat(norm_ts_str)
                if server_tz is None:
                    raise ValueError(
                        f"Row {row_idx}: Naive timestamp '{ts_str}' lacks explicit timezone offset."
                    )
                dt = dt_naive.replace(tzinfo=server_tz)
        except ValueError as ve:
            if "Naive timestamp" in str(ve):
                raise
            # Try strptime formats
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt_naive = datetime.strptime(norm_ts_str, fmt)
                    if server_tz is None:
                        raise ValueError(
                            f"Row {row_idx}: Naive timestamp '{ts_str}' lacks explicit timezone offset."
                        )
                    dt = dt_naive.replace(tzinfo=server_tz)
                    break
                except ValueError as ve2:
                    if "Naive timestamp" in str(ve2):
                        raise
                    continue

        if dt is None:
            raise ValueError(f"Row {row_idx}: Unable to parse timestamp '{ts_str}'.")

        if dt.tzinfo is None:
            raise ValueError(f"Row {row_idx}: Naive timestamp '{ts_str}' rejected.")

        utc_dt = dt.astimezone(timezone.utc)
        if utc_dt > now_utc:
            raise ValueError(f"Row {row_idx}: Future timestamp '{utc_dt.isoformat()}' rejected.")

        if prev_ts is not None and utc_dt < prev_ts:
            raise ValueError(
                f"Row {row_idx}: Non-chronological timestamp sequence ({utc_dt.isoformat()} < {prev_ts.isoformat()})."
            )
        prev_ts = utc_dt

        # Parse bid and ask
        raw_bid = row[col_bid].strip()
        raw_ask = row[col_ask].strip()
        if not raw_bid or not raw_ask:
            raise ValueError(f"Row {row_idx}: Missing bid or ask price.")

        try:
            bid = Decimal(raw_bid)
            ask = Decimal(raw_ask)
        except InvalidOperation:
            raise ValueError(f"Row {row_idx}: Invalid numeric quote values bid='{raw_bid}', ask='{raw_ask}'.")

        if bid <= Decimal("0") or ask <= Decimal("0"):
            raise ValueError(f"Row {row_idx}: Non-positive quote values bid={bid}, ask={ask}.")

        if ask <= bid:
            raise ValueError(f"Row {row_idx}: Crossed or inverted quote ask={ask} <= bid={bid}.")

        spread_price = ask - bid
        mid = (ask + bid) / Decimal("2")
        spread_bps = ((spread_price / mid) * Decimal("10000")).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        session = get_trading_session(utc_dt)

        ticks.append({
            "timestamp": utc_dt,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_price": spread_price,
            "spread_bps": spread_bps,
            "session": session,
            "trading_date": utc_dt.date(),
        })

    if not ticks:
        raise ValueError("No valid tick data rows parsed from file.")

    summary = {
        "sample_count": len(ticks),
        "sample_start": ticks[0]["timestamp"],
        "sample_end": ticks[-1]["timestamp"],
        "distinct_trading_days": len(set(t["trading_date"] for t in ticks)),
        "symbol": expected_symbol.upper(),
    }
    return ticks, summary
