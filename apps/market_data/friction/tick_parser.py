"""Production tick parsers for empirical spread derivation.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance (Directive 6) and Stage D1:
- Production MT5 tick export parser (parse_mt5_tick_export)
- Production Official Exness tick history parser (parse_exness_official_tick_history)
- Explicit accepted schema detection, fail-closed on unknown schemas.
- Parses timestamp, bid, ask with Decimal precision.
- Derives mid, spread_price, spread_bps, trading session, trading date.
- Strict validation:
  * rejects missing bid or ask
  * rejects bid <= 0 or ask <= 0
  * rejects crossed/inverted quotes (ask <= bid)
  * rejects naive timestamps (unless timezone explicitly provided / required UTC Z)
  * rejects future timestamps
  * rejects non-chronological tick ordering
  * rejects wrong symbol if symbol column present
  * rejects wrong venue for official broker files
  * rejects malformed rows and unsupported delimiters
"""
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
from typing import Any, Dict, List, Optional, Tuple

from apps.market_data.friction.artifact_parsers import _matches_expected_symbol, normalize_account_tier
from apps.market_data.friction.distribution import get_trading_session


SUPPORTED_DELIMITERS = [",", "\t", ";"]


def _decode_tick_payload(raw_content: bytes, error_prefix: str = "TICK_PARSER_ERROR") -> List[str]:
    """Decode raw tick export bytes and return non-empty lines."""
    if not isinstance(raw_content, (bytes, bytearray)):
        raise TypeError(f"{error_prefix}: Expected raw artifact bytes, got {type(raw_content).__name__}.")

    if not raw_content or len(raw_content.strip()) == 0:
        raise ValueError(f"{error_prefix}: Tick export payload is empty.")

    try:
        text = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw_content.decode("latin-1")
        except Exception as e:
            raise ValueError(f"{error_prefix}: Failed to decode tick export payload: {e}")

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"{error_prefix}: Tick export file must contain a header and at least one data row.")
    return lines


def _normalize_tick_record(
    row_idx: int,
    utc_dt: datetime,
    raw_bid: str,
    raw_ask: str,
    prev_ts: Optional[datetime],
    now_utc: datetime,
) -> Tuple[Dict[str, Any], datetime]:
    """Validate quote economics, chronological sequence, and compute spread metrics."""
    if utc_dt > now_utc:
        raise ValueError(f"Row {row_idx}: Future timestamp '{utc_dt.isoformat()}' rejected.")

    if prev_ts is not None and utc_dt < prev_ts:
        raise ValueError(
            f"Row {row_idx}: Non-chronological timestamp sequence ({utc_dt.isoformat()} < {prev_ts.isoformat()})."
        )

    # Parse bid and ask
    b_str = raw_bid.strip() if raw_bid else ""
    a_str = raw_ask.strip() if raw_ask else ""
    if not b_str or not a_str:
        raise ValueError(f"Row {row_idx}: Missing bid or ask price.")

    try:
        bid = Decimal(b_str)
        ask = Decimal(a_str)
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

    record = {
        "timestamp": utc_dt,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_price": spread_price,
        "spread_bps": spread_bps,
        "session": session,
        "trading_date": utc_dt.date(),
    }
    return record, utc_dt


def parse_mt5_tick_export(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    server_tz: Optional[timezone] = None,
    expected_broker_symbol: Optional[str] = None,
    expected_account_tier: str = "STANDARD",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse raw MT5 tick export bytes into normalized tick records and dataset metadata.
    Returns:
        (ticks_data, metadata_summary)
    Raises:
        ValueError if schema is unsupported, rows are malformed, or validation fails.
    """
    lines = _decode_tick_payload(raw_content, error_prefix="TICK_PARSER_ERROR")
    header_line = lines[0]
    
    # Detect delimiter
    delimiter = None
    for d in SUPPORTED_DELIMITERS:
        if d in header_line:
            delimiter = d
            break
    if delimiter is None:
        raise ValueError("Unsupported delimiter in tick export. Expected comma, tab, or semicolon.")

    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    raw_header = next(reader)
    header = [col.strip().lower().replace("<", "").replace(">", "").replace('"', '').replace("'", "") for col in raw_header]

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

    norm_tier = normalize_account_tier(expected_account_tier)
    if norm_tier == "STANDARD_CENT" and (not expected_broker_symbol or not str(expected_broker_symbol).strip()):
        raise ValueError(
            "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
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
            if sym and not _matches_expected_symbol(
                sym, expected_symbol, expected_broker_symbol=expected_broker_symbol, expected_account_tier=norm_tier
            ):
                raise ValueError(
                    f"Row {row_idx}: Symbol mismatch. Expected '{expected_symbol}' (or broker symbol '{expected_broker_symbol}'), observed '{sym}'."
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
        record, prev_ts = _normalize_tick_record(
            row_idx=row_idx,
            utc_dt=utc_dt,
            raw_bid=row[col_bid],
            raw_ask=row[col_ask],
            prev_ts=prev_ts,
            now_utc=now_utc,
        )
        ticks.append(record)

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


def parse_exness_official_tick_history(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    server_tz: Optional[timezone] = None,
    expected_broker_symbol: Optional[str] = None,
    expected_account_tier: str = "STANDARD",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse raw official Exness tick archive bytes into normalized tick records and dataset metadata.

    Accepts exact official Exness CSV schema:
        "Exness","Symbol","Timestamp","Bid","Ask"

    Expected properties:
        - Exness venue column: must be "exness"
        - Symbol: must match expected broker symbol (e.g. XAUUSDc)
        - Timestamp: explicit UTC Z / aware timestamp required (naive rejected)
        - Bid & Ask: valid positive Decimals, ask > bid
        - Monotonic chronological sequence, non-future timestamps

    Returns:
        (ticks_data, metadata_summary)
    Raises:
        ValueError if schema is unsupported, venue is wrong, timestamps are naive/future/non-chronological,
        or quotes are invalid/crossed.
    """
    lines = _decode_tick_payload(raw_content, error_prefix="EXNESS_TICK_PARSER_ERROR")
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=",")
    raw_header = next(reader)
    header = [col.strip().lower().replace('"', '').replace("'", "") for col in raw_header]
    expected_header = ["exness", "symbol", "timestamp", "bid", "ask"]
    if header != expected_header:
        raise ValueError(
            f"Unsupported Exness tick export schema: Expected ['Exness', 'Symbol', 'Timestamp', 'Bid', 'Ask'], observed {raw_header}"
        )

    norm_tier = normalize_account_tier(expected_account_tier)
    if norm_tier == "STANDARD_CENT" and (not expected_broker_symbol or not str(expected_broker_symbol).strip()):
        raise ValueError(
            "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
        )

    ticks: List[Dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)
    prev_ts: Optional[datetime] = None

    for row_idx, row in enumerate(reader, start=2):
        if not row or all(c.strip() == "" for c in row):
            continue
        if len(row) < 5:
            raise ValueError(f"Row {row_idx}: Malformed row has fewer columns than required (expected 5, got {len(row)}).")

        # 1. Venue verification
        venue_val = row[0].strip().lower()
        if venue_val != "exness":
            raise ValueError(f"Row {row_idx}: Wrong venue identity '{row[0].strip()}'. Expected 'exness'.")

        # 2. Symbol verification
        sym = row[1].strip()
        if not sym:
            raise ValueError(f"Row {row_idx}: Missing symbol.")
        if not _matches_expected_symbol(
            sym, expected_symbol, expected_broker_symbol=expected_broker_symbol, expected_account_tier=norm_tier
        ):
            raise ValueError(
                f"Row {row_idx}: Symbol mismatch. Expected '{expected_symbol}' (or broker symbol '{expected_broker_symbol}'), observed '{sym}'."
            )

        # 3. Timestamp verification: must be explicit UTC / aware (e.g. trailing 'Z' or offset)
        ts_str = row[2].strip()
        if not ts_str:
            raise ValueError(f"Row {row_idx}: Missing timestamp value.")
        if not (ts_str.endswith("Z") or ts_str.endswith("z") or "+" in ts_str or (ts_str.count("-") >= 3 and len(ts_str) > 19)):
            raise ValueError(f"Row {row_idx}: Naive timestamp '{ts_str}' rejected (explicit UTC Z required).")

        norm_ts_str = ts_str.replace("Z", "+00:00").replace("z", "+00:00")
        dt: Optional[datetime] = None
        try:
            dt = datetime.fromisoformat(norm_ts_str)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    dt = datetime.strptime(norm_ts_str, fmt)
                    break
                except ValueError:
                    continue

        if dt is None:
            raise ValueError(f"Row {row_idx}: Unable to parse Exness timestamp '{ts_str}'.")
        if dt.tzinfo is None:
            raise ValueError(f"Row {row_idx}: Naive timestamp '{ts_str}' rejected (explicit UTC Z required).")

        utc_dt = dt.astimezone(timezone.utc)

        # 4. Quote economics & chronological checks
        record, prev_ts = _normalize_tick_record(
            row_idx=row_idx,
            utc_dt=utc_dt,
            raw_bid=row[3],
            raw_ask=row[4],
            prev_ts=prev_ts,
            now_utc=now_utc,
        )
        ticks.append(record)

    if not ticks:
        raise ValueError("No valid tick data rows parsed from Exness tick file.")

    summary = {
        "sample_count": len(ticks),
        "sample_start": ticks[0]["timestamp"],
        "sample_end": ticks[-1]["timestamp"],
        "distinct_trading_days": len(set(t["trading_date"] for t in ticks)),
        "symbol": expected_symbol.upper(),
        "broker_symbol": expected_broker_symbol,
        "venue": "EXNESS",
    }
    return ticks, summary
