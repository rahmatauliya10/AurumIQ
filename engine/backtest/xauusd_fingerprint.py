"""Deterministic SHA-256 fingerprinting for XAUUSD backtest configurations and datasets."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Sequence, Tuple

from engine.backtest.xauusd_types import XauUsdBacktestRunSpec
from engine.core.types import CandleData


def _to_utc_iso(dt: datetime) -> str:
    """Format datetime as canonical UTC ISO-8601 string with microsecond precision and Z suffix."""
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp {dt} must be explicitly timezone aware.")
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_xauusd_dataset_identity(
    candles_15m: Sequence[CandleData],
    start_time: datetime,
    end_time: datetime,
    candles_4h: Sequence[CandleData] = (),
    candles_1d: Sequence[CandleData] = (),
    candles_5m: Sequence[CandleData] = (),
    candles_1m: Sequence[CandleData] = (),
) -> str:
    """
    Compute canonical SHA-256 hash of market data strictly within [start_time, end_time).
    Candles outside the evaluation window do not alter the identity hash.
    """
    start_utc = start_time.astimezone(timezone.utc) if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    end_utc = end_time.astimezone(timezone.utc) if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)

    records: List[Dict[str, Any]] = []

    def _process_series(series: Sequence[CandleData], tf_label: str) -> None:
        for c in series:
            c_close = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)
            if start_utc <= c_close < end_utc:
                records.append({
                    "tf": tf_label,
                    "open_ts": _to_utc_iso(c.timestamp_open),
                    "close_ts": _to_utc_iso(c.timestamp_close),
                    "open": str(c.open),
                    "high": str(c.high),
                    "low": str(c.low),
                    "close": str(c.close),
                    "volume": str(c.volume),
                    "volume_evidence": getattr(c, "volume_evidence", "UNAVAILABLE") or "UNAVAILABLE",
                    "is_closed": bool(c.is_closed),
                })

    _process_series(candles_15m, "15m")
    _process_series(candles_4h, "4h")
    _process_series(candles_1d, "1d")
    _process_series(candles_5m, "5m")
    _process_series(candles_1m, "1m")

    # Sort records deterministically by timeframe and close_ts
    records.sort(key=lambda r: (r["tf"], r["close_ts"]))

    serialized = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_xauusd_backtest_fingerprint(spec: XauUsdBacktestRunSpec) -> str:
    """
    Generate canonical deterministic SHA-256 hash binding all parameters of a run specification.
    """
    if not spec.code_revision or not spec.code_revision.strip():
        raise ValueError("Backtest run spec requires an explicit non-empty code_revision.")

    payload = {
        "instrument": "XAUUSD",
        "dataset_hash": spec.dataset_hash,
        "start_time": _to_utc_iso(spec.start_time),
        "end_time": _to_utc_iso(spec.end_time),
        "timeframes": list(spec.timeframes),
        "cost_config": {
            "entry_fee_bps": str(spec.cost_config.entry_fee_bps),
            "exit_fee_bps": str(spec.cost_config.exit_fee_bps),
            "synthetic_spread_bps": str(spec.cost_config.synthetic_spread_bps),
            "entry_slippage_bps": str(spec.cost_config.entry_slippage_bps),
            "exit_slippage_bps": str(spec.cost_config.exit_slippage_bps),
        },
        "cost_scenario": str(spec.cost_scenario.value if hasattr(spec.cost_scenario, "value") else spec.cost_scenario),
        "holding_horizon_bars_15m": spec.holding_horizon_bars_15m,
        "holding_horizon_seconds": spec.holding_horizon_seconds,
        "execution_policy": str(spec.execution_policy),
        "intrabar_policy": str(spec.intrabar_policy),
        "ablation_type": str(spec.ablation_type.value if hasattr(spec.ablation_type, "value") else spec.ablation_type),
        "engine_version": str(spec.engine_version),
        "config_version": str(spec.config_version),
        "feature_version": str(spec.feature_version),
        "cycle_version": str(spec.cycle_version),
        "risk_version": str(spec.risk_version),
        "execution_model_version": str(spec.execution_model_version),
        "backtest_version": str(spec.backtest_version),
        "code_revision": str(spec.code_revision.strip()),
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
