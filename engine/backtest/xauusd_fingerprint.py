"""Deterministic SHA-256 fingerprinting for XAUUSD backtest configurations and datasets."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdFoldSpec,
    XauUsdWalkForwardConfig,
)
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    MacroEventContext,
    QuoteData,
)
from engine.risk.xauusd_fingerprints import (
    compute_phase5_policy_fingerprint,
)
from engine.signals.profile import compute_phase4_policy_fingerprint


def _to_utc_iso(dt: datetime, param_name: str = "timestamp") -> str:
    """Format datetime as canonical UTC ISO-8601 string with microsecond precision and Z suffix."""
    if dt is None or dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{param_name} ({dt}) must be explicitly timezone aware with non-None utcoffset.")
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_xauusd_dataset_identity(
    candles_15m: Sequence[CandleData],
    start_time: datetime,
    end_time: datetime,
    candles_1h: Sequence[CandleData] = (),
    candles_4h: Sequence[CandleData] = (),
    candles_1d: Sequence[CandleData] = (),
    candles_5m: Sequence[CandleData] = (),
    candles_1m: Sequence[CandleData] = (),
    quotes: Sequence[QuoteData] = (),
    macro_evidence: Sequence[Any] = (),
    phase3a_evidence: Sequence[Any] = (),
    cycle_3a: Sequence[Cycle3ASnapshot] = (),
) -> str:
    """
    Compute canonical SHA-256 hash of all market evidence strictly within [start_time, end_time).
    Binds all material OHLCV (15m, 1h, 4h, 1d, 5m, 1m), quotes, volume evidence, source ID, macro, and Phase 3A fields.
    Strictly validates timezone awareness on every evidence record.
    """
    if start_time.tzinfo is None or start_time.tzinfo.utcoffset(start_time) is None:
        raise ValueError("start_time must be timezone-aware.")
    if end_time.tzinfo is None or end_time.tzinfo.utcoffset(end_time) is None:
        raise ValueError("end_time must be timezone-aware.")

    start_utc = start_time.astimezone(timezone.utc)
    end_utc = end_time.astimezone(timezone.utc)

    candle_records: List[Dict[str, Any]] = []

    def _process_candles(series: Sequence[CandleData], tf_label: str) -> None:
        for c in series:
            if c.timestamp_open.tzinfo is None or c.timestamp_open.tzinfo.utcoffset(c.timestamp_open) is None:
                raise ValueError(f"Candle open at {c.timestamp_open} must be timezone aware.")
            if c.timestamp_close.tzinfo is None or c.timestamp_close.tzinfo.utcoffset(c.timestamp_close) is None:
                raise ValueError(f"Candle close at {c.timestamp_close} must be timezone aware.")
            c_close = c.timestamp_close.astimezone(timezone.utc)
            if start_utc <= c_close < end_utc:
                candle_records.append({
                    "tf": tf_label,
                    "open_ts": _to_utc_iso(c.timestamp_open, "candle.timestamp_open"),
                    "close_ts": _to_utc_iso(c.timestamp_close, "candle.timestamp_close"),
                    "open": str(c.open),
                    "high": str(c.high),
                    "low": str(c.low),
                    "close": str(c.close),
                    "volume": str(c.volume),
                    "volume_evidence": getattr(c, "volume_evidence", "UNAVAILABLE") or "UNAVAILABLE",
                    "is_closed": bool(c.is_closed),
                    "source_id": str(getattr(c, "source_id", "UNKNOWN")),
                    "quote_rate": str(getattr(c, "quote_rate", "")),
                    "close_usd": str(getattr(c, "close_usd", "")),
                })

    _process_candles(candles_15m, "15m")
    _process_candles(candles_1h, "1h")
    _process_candles(candles_4h, "4h")
    _process_candles(candles_1d, "1d")
    _process_candles(candles_5m, "5m")
    _process_candles(candles_1m, "1m")

    candle_records.sort(key=lambda r: (r["tf"], r["close_ts"], r["open_ts"], r["source_id"]))

    quote_records: List[Dict[str, Any]] = []
    for q in quotes:
        if q.timestamp.tzinfo is None or q.timestamp.tzinfo.utcoffset(q.timestamp) is None:
            raise ValueError(f"Quote at {q.timestamp} must be timezone aware.")
        q_ts = q.timestamp.astimezone(timezone.utc)
        if start_utc <= q_ts < end_utc:
            quote_records.append({
                "ts": _to_utc_iso(q.timestamp, "quote.timestamp"),
                "bid": str(q.bid),
                "ask": str(q.ask),
                "source": str(getattr(q, "source", "orderbook")),
            })

    quote_records.sort(key=lambda r: (r["ts"], r["source"], r["bid"], r["ask"]))

    macro_records: List[Dict[str, Any]] = []
    for m in macro_evidence:
        if isinstance(m, tuple) and len(m) == 2 and isinstance(m[0], datetime) and isinstance(m[1], MacroEventContext):
            t, ctx = m
            if t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
                raise ValueError(f"Macro event timestamp at {t} must be timezone aware.")
            t_utc = t.astimezone(timezone.utc)
            if start_utc <= t_utc < end_utc:
                macro_records.append({
                    "ts": _to_utc_iso(t, "macro_event.timestamp"),
                    "is_in_blackout": bool(ctx.is_in_blackout),
                    "minutes_to_next_event": ctx.minutes_to_next_event,
                    "minutes_since_last_event": ctx.minutes_since_last_event,
                    "active_event_name": str(ctx.active_event_name or ""),
                    "point_in_time_value": str(ctx.point_in_time_value or ""),
                    "is_feed_healthy": bool(ctx.is_feed_healthy),
                })
        elif isinstance(m, dict):
            # Dict representation: check for timestamp filtering if timestamp is present
            ts_val = m.get("timestamp") or m.get("ts")
            if ts_val:
                if isinstance(ts_val, str):
                    dt_val = datetime.fromisoformat(ts_val)
                else:
                    dt_val = ts_val
                if dt_val.tzinfo is None or dt_val.tzinfo.utcoffset(dt_val) is None:
                    raise ValueError(f"Macro evidence timestamp at {dt_val} must be timezone aware.")
                dt_utc = dt_val.astimezone(timezone.utc)
                if start_utc <= dt_utc < end_utc:
                    macro_records.append({k: str(v) for k, v in sorted(m.items())})
            else:
                macro_records.append({k: str(v) for k, v in sorted(m.items())})

    macro_records.sort(key=lambda r: json.dumps(r, sort_keys=True))

    p3a_records: List[Dict[str, Any]] = []
    # Process Cycle3ASnapshot objects
    for s in cycle_3a:
        if s.timestamp.tzinfo is None or s.timestamp.tzinfo.utcoffset(s.timestamp) is None:
            raise ValueError(f"Cycle3A timestamp at {s.timestamp} must be timezone aware.")
        s_ts = s.timestamp.astimezone(timezone.utc)
        if start_utc <= s_ts < end_utc:
            p3a_records.append({
                "timestamp": _to_utc_iso(s.timestamp, "cycle_3a.timestamp"),
                "session": {
                    "session": s.session.session.value if hasattr(s.session.session, "value") else str(s.session.session),
                    "progress_pct": float(s.session.progress_pct),
                    "is_high_liquidity": bool(s.session.is_high_liquidity),
                    "expectancy_score": float(s.session.expectancy_score),
                } if s.session else None,
                "swing_duration": {
                    "market_age_bars": s.swing_duration.market_age_bars,
                    "market_age_hours": float(s.swing_duration.market_age_hours),
                    "known_age_bars": s.swing_duration.known_age_bars,
                    "known_age_hours": float(s.swing_duration.known_age_hours),
                    "pullback_age_percentile": float(s.swing_duration.pullback_age_percentile) if s.swing_duration.pullback_age_percentile is not None else None,
                    "is_mature": bool(s.swing_duration.is_mature),
                    "maturity_score": float(s.swing_duration.maturity_score),
                } if s.swing_duration else None,
                "macro_event": {
                    "is_in_blackout": bool(s.macro_event.is_in_blackout),
                    "minutes_to_next_event": s.macro_event.minutes_to_next_event,
                    "minutes_since_last_event": s.macro_event.minutes_since_last_event,
                    "active_event_name": str(s.macro_event.active_event_name or ""),
                    "point_in_time_value": str(s.macro_event.point_in_time_value or ""),
                    "is_feed_healthy": bool(s.macro_event.is_feed_healthy),
                } if s.macro_event else None,
                "calendar": {
                    "day_of_week": s.calendar.day_of_week,
                    "day_name": s.calendar.day_name,
                    "hour_utc": s.calendar.hour_utc,
                    "month": s.calendar.month,
                    "is_month_end_flow": bool(s.calendar.is_month_end_flow),
                    "stability_score": float(s.calendar.stability_score),
                    "seasonality_score": float(s.calendar.seasonality_score),
                } if s.calendar else None,
                "is_blocked_by_event": bool(s.is_blocked_by_event),
                "cycle_score_3a": float(s.cycle_score_3a),
                "cycle_version": str(s.cycle_version or ""),
                "profile_name": str(s.profile_name or ""),
                "calibration_status": str(s.calibration_status or ""),
                "calibration_artifact_version": str(s.calibration_artifact_version or ""),
            })

    for p in phase3a_evidence:
        if isinstance(p, dict):
            ts_val = p.get("timestamp") or p.get("ts")
            if ts_val:
                if isinstance(ts_val, str):
                    dt_val = datetime.fromisoformat(ts_val)
                else:
                    dt_val = ts_val
                if dt_val.tzinfo is None or dt_val.tzinfo.utcoffset(dt_val) is None:
                    raise ValueError(f"Phase3A evidence timestamp at {dt_val} must be timezone aware.")
                dt_utc = dt_val.astimezone(timezone.utc)
                if start_utc <= dt_utc < end_utc:
                    p3a_records.append({k: str(v) for k, v in sorted(p.items())})
            else:
                p3a_records.append({k: str(v) for k, v in sorted(p.items())})

    p3a_records.sort(key=lambda r: json.dumps(r, sort_keys=True))

    payload = {
        "candles": candle_records,
        "quotes": quote_records,
        "macro": macro_records,
        "phase3a": p3a_records,
        "window_start": _to_utc_iso(start_utc, "window_start"),
        "window_end": _to_utc_iso(end_utc, "window_end"),
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_xauusd_dataset_identity_from_dataset(
    dataset: PointInTimeDataset,
    start_time: datetime,
    end_time: datetime,
) -> str:
    """
    Canonical helper computing dataset identity directly from a PointInTimeDataset repository instance.
    Extracts closed candles (15m, 1h, 4h, 1d, 5m, 1m), quotes, Cycle3A snapshots, and macro events within [start_time, end_time).
    """
    return compute_xauusd_dataset_identity(
        candles_15m=getattr(dataset, "_candles", {}).get("15m", []),
        start_time=start_time,
        end_time=end_time,
        candles_1h=getattr(dataset, "_candles", {}).get("1h", []),
        candles_4h=getattr(dataset, "_candles", {}).get("4h", []),
        candles_1d=getattr(dataset, "_candles", {}).get("1d", []),
        candles_5m=getattr(dataset, "_candles", {}).get("5m", []),
        candles_1m=getattr(dataset, "_candles", {}).get("1m", []),
        quotes=getattr(dataset, "_quotes", []),
        macro_evidence=getattr(dataset, "_macro_events", []),
        cycle_3a=getattr(dataset, "_cycle_3a", []),
    )


def compute_xauusd_backtest_fingerprint(spec: XauUsdBacktestRunSpec) -> str:
    """
    Generate canonical deterministic SHA-256 hash binding all parameters and policy provenance of a run spec.
    """
    if not spec.code_revision or not spec.code_revision.strip():
        raise ValueError("Backtest run spec requires an explicit non-empty code_revision.")

    p4_fp = spec.phase4_policy_fingerprint
    if not p4_fp and spec.signal_profile is not None:
        p4_fp = compute_phase4_policy_fingerprint(spec.signal_profile)

    p5_risk_fp = spec.phase5_risk_policy_fingerprint
    p5_exec_fp = spec.phase5_execution_policy_fingerprint
    p5_long_exec_fp = spec.phase5_long_execution_policy_fingerprint
    p5_short_exec_fp = spec.phase5_short_execution_policy_fingerprint

    if spec.risk_profile is not None:
        if not p5_risk_fp:
            p5_risk_fp = compute_phase5_policy_fingerprint(spec.risk_profile)
        l_exec = spec.risk_profile.long_execution_policy
        s_exec = spec.risk_profile.short_execution_policy
        if not p5_long_exec_fp:
            p5_long_exec_fp = hashlib.sha256(
                f"LONG:{l_exec.latency_seconds}:{l_exec.synthetic_spread_pct}:{l_exec.slippage_pct}".encode("utf-8")
            ).hexdigest()
        if not p5_short_exec_fp:
            p5_short_exec_fp = hashlib.sha256(
                f"SHORT:{s_exec.latency_seconds}:{s_exec.synthetic_spread_pct}:{s_exec.slippage_pct}".encode("utf-8")
            ).hexdigest()
        if not p5_exec_fp:
            p5_exec_fp = hashlib.sha256(f"{p5_long_exec_fp}:{p5_short_exec_fp}".encode("utf-8")).hexdigest()

    payload = {
        "instrument": "XAUUSD",
        "dataset_hash": spec.dataset_hash,
        "start_time": _to_utc_iso(spec.start_time, "spec.start_time"),
        "end_time": _to_utc_iso(spec.end_time, "spec.end_time"),
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
        "max_fill_wait_bars_15m": spec.max_fill_wait_bars_15m,
        "max_fill_wait_seconds": spec.max_fill_wait_seconds,
        "execution_policy": str(spec.execution_policy.value if hasattr(spec.execution_policy, "value") else spec.execution_policy),
        "intrabar_policy": str(spec.intrabar_policy.value if hasattr(spec.intrabar_policy, "value") else spec.intrabar_policy),
        "ablation_type": str(spec.ablation_type.value if hasattr(spec.ablation_type, "value") else spec.ablation_type),
        "phase4_policy_fingerprint": p4_fp,
        "phase5_risk_policy_fingerprint": p5_risk_fp,
        "phase5_execution_policy_fingerprint": p5_exec_fp,
        "phase5_long_execution_policy_fingerprint": p5_long_exec_fp,
        "phase5_short_execution_policy_fingerprint": p5_short_exec_fp,
        "engine_version": str(spec.engine_version),
        "config_version": str(spec.config_version),
        "feature_version": str(spec.feature_version),
        "cycle_version": str(spec.cycle_version),
        "risk_version": str(spec.risk_version),
        "execution_model_version": str(spec.execution_model_version),
        "backtest_version": str(spec.backtest_version),
        "code_revision": str(spec.code_revision),
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_xauusd_walkforward_fingerprint(
    spec: XauUsdBacktestRunSpec,
    wf_config: XauUsdWalkForwardConfig,
    fold_specs: Sequence[XauUsdFoldSpec] = (),
) -> str:
    """
    Generate canonical deterministic SHA-256 hash binding backtest spec and walkforward configuration.
    """
    base_fp = compute_xauusd_backtest_fingerprint(spec)
    folds_data = []
    for f in fold_specs:
        folds_data.append({
            "fold_id": f.fold_id,
            "train_start": _to_utc_iso(f.train_start, "fold.train_start"),
            "train_end": _to_utc_iso(f.train_end, "fold.train_end"),
            "val_start": _to_utc_iso(f.val_start, "fold.val_start") if f.val_start else None,
            "val_end": _to_utc_iso(f.val_end, "fold.val_end") if f.val_end else None,
            "oos_start": _to_utc_iso(f.oos_start, "fold.oos_start"),
            "oos_end": _to_utc_iso(f.oos_end, "fold.oos_end"),
            "embargo_duration_seconds": f.embargo_duration_seconds,
        })

    payload = {
        "base_backtest_fingerprint": base_fp,
        "total_folds": wf_config.total_folds,
        "train_ratio": wf_config.train_ratio,
        "val_ratio": wf_config.val_ratio,
        "oos_ratio": wf_config.oos_ratio,
        "embargo_seconds": wf_config.embargo_seconds,
        "purge_overlapping": wf_config.purge_overlapping,
        "rolling_window": wf_config.rolling_window,
        "folds": folds_data,
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
