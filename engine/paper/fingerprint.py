"""Deterministic reproducibility fingerprint computation for Phase 8 paper observations."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Optional


def compute_observation_fingerprint(
    code_revision: str,
    engine_version: str,
    config_version: str,
    market_data_hash: str,
    friction_model_version_id: str,
    decision_timestamp: datetime,
    decision_timeframe: str,
    side: str,
    source_signal_fingerprint: Optional[str] = None,
) -> str:
    """
    Compute a deterministic canonical SHA-256 fingerprint for a Phase 8 observation.

    Strict Invariants:
      1. Binds code revision, engine version, config version, market data hash,
         friction model version ID, normalized UTC decision timestamp, timeframe, and side.
      2. Exactly identical inputs produce the identical 64-character hex hash.
      3. Changing any component (e.g. BUY vs SELL, different friction version) alters the hash.
    """
    if decision_timestamp.tzinfo is None:
        raise ValueError(f"decision_timestamp must be timezone-aware UTC, got naive: {decision_timestamp}")

    utc_ts = decision_timestamp.astimezone(timezone.utc).isoformat()
    norm_side = side.upper().strip()
    if norm_side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got: '{side}'")

    payload = {
        "code_revision": str(code_revision).strip(),
        "config_version": str(config_version).strip(),
        "decision_timeframe": str(decision_timeframe).strip().lower(),
        "decision_timestamp": utc_ts,
        "engine_version": str(engine_version).strip(),
        "friction_model_version_id": str(friction_model_version_id).strip(),
        "market_data_hash": str(market_data_hash).strip(),
        "side": norm_side,
        "source_signal_fingerprint": str(source_signal_fingerprint or "").strip(),
    }

    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
