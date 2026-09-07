"""Production MT5 execution telemetry parser for empirical slippage derivation.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance (Directive 7):
- Parses MT5 execution telemetry fills with explicit schema validation.
- Fields supported:
    side, order_type, decision_timestamp, order_send_timestamp,
    reference_bid, reference_ask, executed_fill_price, fill_timestamp,
    volume_lots, latency_ms, symbol, account_tier, venue (or server),
    requested_price (optional).
- Directional adverse slippage calculation:
    BUY:  executed_fill_price - reference_ask
    SELL: reference_bid - executed_fill_price
- Normalized to basis points against reference executable quote:
    BUY:  slippage_bps = (adverse_slippage / reference_ask) * 10000
    SELL: slippage_bps = (adverse_slippage / reference_bid) * 10000
- Tracks both signed slippage and adverse-only distributions.
- Strict fail-closed validation on timestamps, scope, and numbers.
"""
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
from typing import Any, Dict, List, Optional, Tuple


SUPPORTED_DELIMITERS = [",", "\t", ";"]


def _parse_aware_datetime(val_str: str, row_idx: int, field_name: str, server_tz: Optional[timezone] = None) -> datetime:
    raw = val_str.strip()
    if not raw:
        raise ValueError(f"Row {row_idx}: Empty timestamp for '{field_name}'.")

    # Normalize date separator only in the date portion (first 10 chars, e.g. YYYY.MM.DD -> YYYY-MM-DD)
    if len(raw) >= 10:
        norm_raw = raw[:10].replace(".", "-") + raw[10:]
    else:
        norm_raw = raw

    dt: Optional[datetime] = None
    try:
        if "Z" in norm_raw or "+" in norm_raw or ("-" in norm_raw[10:] and len(norm_raw) > 19):
            dt = datetime.fromisoformat(norm_raw.replace("Z", "+00:00"))
        else:
            dt_naive = datetime.fromisoformat(norm_raw)
            if server_tz is None:
                raise ValueError(f"Row {row_idx}: Naive timestamp '{val_str}' for '{field_name}' rejected.")
            dt = dt_naive.replace(tzinfo=server_tz)
    except ValueError as ve:
        if "Naive timestamp" in str(ve):
            raise
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt_naive = datetime.strptime(norm_raw, fmt)
                if server_tz is None:
                    raise ValueError(f"Row {row_idx}: Naive timestamp '{val_str}' for '{field_name}' rejected.")
                dt = dt_naive.replace(tzinfo=server_tz)
                break
            except ValueError as ve2:
                if "Naive timestamp" in str(ve2):
                    raise
                continue

    if dt is None:
        raise ValueError(f"Row {row_idx}: Invalid timestamp '{val_str}' for '{field_name}'.")
    if dt.tzinfo is None:
        raise ValueError(f"Row {row_idx}: Naive timestamp '{val_str}' for '{field_name}' rejected.")
    return dt.astimezone(timezone.utc)


def parse_mt5_execution_telemetry(
    raw_content: bytes,
    expected_venue: Optional[str] = "EXNESS",
    expected_symbol: Optional[str] = "XAUUSD",
    expected_account_tier: Optional[str] = "STANDARD",
    server_tz: Optional[timezone] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse raw MT5 execution telemetry bytes into normalized telemetry fills.
    
    Returns:
        (telemetry_records, summary_metadata)
    Raises:
        ValueError if schema or rows are invalid.
    """
    if not raw_content or len(raw_content.strip()) == 0:
        raise ValueError("Execution telemetry payload is empty.")

    try:
        text = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw_content.decode("latin-1")
        except Exception as e:
            raise ValueError(f"Failed to decode execution telemetry payload: {e}")

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Execution telemetry file must contain a header and at least one data row.")

    header_line = lines[0]
    delimiter = None
    for d in SUPPORTED_DELIMITERS:
        if d in header_line:
            delimiter = d
            break
    if delimiter is None:
        raise ValueError("Unsupported delimiter in execution telemetry. Expected comma, tab, or semicolon.")

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    raw_header = next(reader)
    header = [col.strip().lower().replace("<", "").replace(">", "").replace(" ", "_") for col in raw_header]

    # Required column mappings
    field_aliases = {
        "side": ["side", "direction", "order_side"],
        "order_type": ["order_type", "type"],
        "decision_timestamp": ["decision_timestamp", "decision_time", "signal_timestamp"],
        "order_send_timestamp": ["order_send_timestamp", "send_timestamp", "send_time", "placed_time"],
        "reference_bid": ["reference_bid", "ref_bid", "bid_at_send", "bid"],
        "reference_ask": ["reference_ask", "ref_ask", "ask_at_send", "ask"],
        "executed_fill_price": ["executed_fill_price", "fill_price", "execution_price", "price"],
        "fill_timestamp": ["fill_timestamp", "execution_timestamp", "fill_time", "done_time"],
        "volume_lots": ["volume_lots", "volume", "lots", "qty"],
        "latency_ms": ["latency_ms", "execution_latency_ms", "latency"],
        "symbol": ["symbol", "instrument"],
        "account_tier": ["account_tier", "tier", "account_type"],
        "venue": ["venue", "server", "broker"],
    }

    col_map: Dict[str, int] = {}
    for target_field, aliases in field_aliases.items():
        found = False
        for alias in aliases:
            if alias in header:
                col_map[target_field] = header.index(alias)
                found = True
                break
        if not found:
            raise ValueError(
                f"Unsupported execution telemetry schema: Missing required field '{target_field}'. "
                f"Header observed: {raw_header}"
            )

    # Optional requested_price
    col_req_price: Optional[int] = None
    for alias in ["requested_price", "order_price", "target_price"]:
        if alias in header:
            col_req_price = header.index(alias)
            break

    records: List[Dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)

    for row_idx, row in enumerate(reader, start=2):
        if not row or all(c.strip() == "" for c in row):
            continue

        for fld, idx in col_map.items():
            if idx >= len(row):
                raise ValueError(f"Row {row_idx}: Missing column '{fld}'. Malformed row.")

        # Scope validation
        row_venue = row[col_map["venue"]].strip().upper()
        row_symbol = row[col_map["symbol"]].strip().upper()
        row_tier = row[col_map["account_tier"]].strip().upper()

        if expected_venue and row_venue != expected_venue.upper():
            raise ValueError(
                f"Row {row_idx}: Telemetry venue mismatch. Expected '{expected_venue}', observed '{row_venue}'."
            )
        if expected_symbol and row_symbol != expected_symbol.upper():
            raise ValueError(
                f"Row {row_idx}: Telemetry symbol mismatch. Expected '{expected_symbol}', observed '{row_symbol}'."
            )
        if expected_account_tier and row_tier != expected_account_tier.upper():
            raise ValueError(
                f"Row {row_idx}: Telemetry account tier mismatch. Expected '{expected_account_tier}', observed '{row_tier}'."
            )

        side_raw = row[col_map["side"]].strip().upper()
        if side_raw in ("BUY", "LONG"):
            side = "BUY"
        elif side_raw in ("SELL", "SHORT"):
            side = "SELL"
        else:
            raise ValueError(f"Row {row_idx}: Unknown order side '{side_raw}'. Expected BUY or SELL.")

        order_type = row[col_map["order_type"]].strip().upper()

        # Parse timestamps
        decision_ts = _parse_aware_datetime(row[col_map["decision_timestamp"]], row_idx, "decision_timestamp", server_tz)
        send_ts = _parse_aware_datetime(row[col_map["order_send_timestamp"]], row_idx, "order_send_timestamp", server_tz)
        fill_ts = _parse_aware_datetime(row[col_map["fill_timestamp"]], row_idx, "fill_timestamp", server_tz)

        if decision_ts > now_utc or send_ts > now_utc or fill_ts > now_utc:
            raise ValueError(f"Row {row_idx}: Future timestamp rejected in telemetry.")

        if send_ts < decision_ts:
            raise ValueError(
                f"Row {row_idx}: Chronological sequence invalid: order_send ({send_ts}) < decision ({decision_ts})."
            )
        if fill_ts < send_ts:
            raise ValueError(
                f"Row {row_idx}: Chronological sequence invalid: fill ({fill_ts}) < order_send ({send_ts})."
            )

        # Parse numeric fields
        try:
            ref_bid = Decimal(row[col_map["reference_bid"]].strip())
            ref_ask = Decimal(row[col_map["reference_ask"]].strip())
            fill_price = Decimal(row[col_map["executed_fill_price"]].strip())
            volume_lots = Decimal(row[col_map["volume_lots"]].strip())
            latency_ms = Decimal(row[col_map["latency_ms"]].strip())
        except InvalidOperation as e:
            raise ValueError(f"Row {row_idx}: Invalid numeric value in telemetry: {e}")

        if ref_bid <= Decimal("0") or ref_ask <= Decimal("0") or fill_price <= Decimal("0"):
            raise ValueError(f"Row {row_idx}: Non-positive quote or execution prices.")

        if ref_ask <= ref_bid:
            raise ValueError(f"Row {row_idx}: Crossed reference quotes ref_ask={ref_ask} <= ref_bid={ref_bid}.")

        if volume_lots <= Decimal("0"):
            raise ValueError(f"Row {row_idx}: Non-positive volume_lots={volume_lots}.")

        if latency_ms < Decimal("0"):
            raise ValueError(f"Row {row_idx}: Negative latency_ms={latency_ms}.")

        req_price: Optional[Decimal] = None
        if col_req_price is not None and col_req_price < len(row):
            raw_req = row[col_req_price].strip()
            if raw_req:
                try:
                    req_price = Decimal(raw_req)
                except InvalidOperation:
                    raise ValueError(f"Row {row_idx}: Invalid requested_price '{raw_req}'.")

        # Directional adverse slippage
        # BUY:  executed_fill_price - reference_ask
        # SELL: reference_bid - executed_fill_price
        if side == "BUY":
            ref_price = ref_ask
            adverse_slippage_price = fill_price - ref_ask
        else:
            ref_price = ref_bid
            adverse_slippage_price = ref_bid - fill_price

        signed_slippage_bps = ((adverse_slippage_price / ref_price) * Decimal("10000")).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        adverse_only_bps = max(Decimal("0.0000"), signed_slippage_bps)

        records.append({
            "venue": row_venue,
            "symbol": row_symbol,
            "account_tier": row_tier,
            "side": side,
            "order_type": order_type,
            "decision_timestamp": decision_ts,
            "order_send_timestamp": send_ts,
            "fill_timestamp": fill_ts,
            "reference_bid": ref_bid,
            "reference_ask": ref_ask,
            "executed_fill_price": fill_price,
            "requested_price": req_price,
            "volume_lots": volume_lots,
            "latency_ms": latency_ms,
            "adverse_slippage_price": adverse_slippage_price,
            "signed_slippage_bps": signed_slippage_bps,
            "adverse_only_bps": adverse_only_bps,
        })

    if not records:
        raise ValueError("No valid execution telemetry rows parsed.")

    summary = {
        "sample_count": len(records),
        "sample_start": min(r["decision_timestamp"] for r in records),
        "sample_end": max(r["fill_timestamp"] for r in records),
        "venue": expected_venue.upper() if expected_venue else records[0]["venue"],
        "symbol": expected_symbol.upper() if expected_symbol else records[0]["symbol"],
        "account_tier": expected_account_tier.upper() if expected_account_tier else records[0]["account_tier"],
    }
    return records, summary
