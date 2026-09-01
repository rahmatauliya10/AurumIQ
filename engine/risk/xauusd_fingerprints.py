"""
Phase 5 XAUUSD Lossless Cryptographic Fingerprints & Provenance Serializers.

Provides canonical serialization and SHA-256 hashing for:
- StructureZone fingerprints (lossless, microsecond UTC, touches included)
- Phase 5 Risk Profile / Policy fingerprints
- Quote and Candle execution source evidence fingerprints
- Side-Aware Risk Plan snapshots
- Side-Aware Fill results
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from typing import Any, Optional

from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    QuoteData,
    RiskSide,
    SignalState,
    StructureZone,
    UserDecision,
)
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)


def canonical_utc_timestamp(dt: datetime) -> str:
    """
    Authoritative timestamp normalizer.
    Converts aware datetime to UTC ISO string with microseconds and 'Z' suffix.
    Rejects naive datetime.
    """
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required for canonical serialization.")
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def compute_zone_fingerprint(zone: StructureZone) -> str:
    """
    Lossless SHA-256 fingerprint for StructureZone.
    Binds all 6 authoritative fields including touches.
    """
    payload = {
        "zone_type": str(zone.zone_type),
        "price_low": str(zone.price_low),
        "price_high": str(zone.price_high),
        "created_at": canonical_utc_timestamp(zone.created_at),
        "touches": str(zone.touches),
        "is_active": "TRUE" if zone.is_active else "FALSE",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_quote_evidence_fingerprint(quote: QuoteData) -> str:
    """Lossless SHA-256 fingerprint for quote execution evidence."""
    payload = {
        "evidence_type": "QUOTE",
        "timestamp": canonical_utc_timestamp(quote.timestamp),
        "bid": str(quote.bid),
        "ask": str(quote.ask),
        "source": quote.source,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_candle_evidence_fingerprint(candle: CandleData) -> str:
    """Lossless SHA-256 fingerprint for complete canonical CandleData evidence."""
    payload = {
        "evidence_type": "CANDLE",
        "timestamp_open": canonical_utc_timestamp(candle.timestamp_open),
        "timestamp_close": canonical_utc_timestamp(candle.timestamp_close),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
        "is_closed": "TRUE" if candle.is_closed else "FALSE",
        "source_id": candle.source_id,
        "quote_rate": str(candle.quote_rate) if candle.quote_rate is not None else "NONE",
        "close_usd": str(candle.close_usd) if candle.close_usd is not None else "NONE",
        "volume_evidence": candle.volume_evidence.value,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_phase5_policy_fingerprint(profile: XauUsdRiskProfile) -> str:
    """Lossless SHA-256 fingerprint for XauUsdRiskProfile."""
    def _enc_dec(d: Optional[Decimal]) -> str:
        return str(d) if d is not None else "NONE"

    def _enc_flt(f: Optional[float]) -> str:
        if f is None:
            return "NONE"
        if not math.isfinite(f):
            raise ValueError(f"Non-finite float in policy: {f}")
        return float(f).hex()

    def _enc_side_risk(p: SideRiskPolicy) -> dict:
        return {
            "structure_buffer": _enc_dec(p.structure_buffer),
            "atr_multiplier": _enc_dec(p.atr_multiplier),
            "max_stop_distance_atr": _enc_dec(p.max_stop_distance_atr),
            "min_rr_tp1": _enc_dec(p.min_rr_tp1),
            "tp2_atr_multiplier": _enc_dec(p.tp2_atr_multiplier),
        }

    def _enc_exec(e: XauUsdExecutionPolicy) -> dict:
        return {
            "latency_seconds": _enc_flt(e.latency_seconds),
            "synthetic_spread_pct": _enc_dec(e.synthetic_spread_pct),
            "slippage_pct": _enc_dec(e.slippage_pct),
        }

    payload = {
        "name": profile.name,
        "target": profile.target_instrument,
        "calibration": profile.calibration_status.value,
        "long_risk": _enc_side_risk(profile.long_risk_policy),
        "short_risk": _enc_side_risk(profile.short_risk_policy),
        "long_exec": _enc_exec(profile.long_execution_policy),
        "short_exec": _enc_exec(profile.short_execution_policy),
        "is_production_authorized": "TRUE" if profile.is_production_authorized else "FALSE",
        "risk_version": profile.risk_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_risk_plan_fingerprint(
    source_phase4_fingerprint: str,
    source_candidate_state: SignalState,
    source_candidate_decision: UserDecision,
    side: RiskSide,
    authoritative_timestamp: datetime,
    entry_min: Optional[Decimal],
    entry_mid: Optional[Decimal],
    entry_max: Optional[Decimal],
    stop_structure: Optional[Decimal],
    stop_atr: Optional[Decimal],
    stop_final: Optional[Decimal],
    stop_distance_atr: Optional[Decimal],
    tp1: Optional[Decimal],
    tp2: Optional[Decimal],
    planned_rr_tp1: Optional[Decimal],
    planned_rr_tp2: Optional[Decimal],
    entry_zone_fingerprint: Optional[str],
    tp1_zone_fingerprint: Optional[str],
    tp2_zone_fingerprint: Optional[str],
    atr_value: Decimal,
    phase5_policy_fingerprint: str,
    risk_version: str,
    code_revision: str,
) -> str:
    """Deterministic SHA-256 fingerprint derived strictly from point-in-time risk plan inputs."""
    def _v(val: Any) -> str:
        return str(val) if val is not None else "NONE"

    payload = {
        "source_phase4_fingerprint": source_phase4_fingerprint,
        "source_candidate_state": source_candidate_state.value,
        "source_candidate_decision": source_candidate_decision.value,
        "side": side.value,
        "authoritative_timestamp": canonical_utc_timestamp(authoritative_timestamp),
        "entry_min": _v(entry_min),
        "entry_mid": _v(entry_mid),
        "entry_max": _v(entry_max),
        "stop_structure": _v(stop_structure),
        "stop_atr": _v(stop_atr),
        "stop_final": _v(stop_final),
        "stop_distance_atr": _v(stop_distance_atr),
        "tp1": _v(tp1),
        "tp2": _v(tp2),
        "planned_rr_tp1": _v(planned_rr_tp1),
        "planned_rr_tp2": _v(planned_rr_tp2),
        "entry_zone_fingerprint": _v(entry_zone_fingerprint),
        "tp1_zone_fingerprint": _v(tp1_zone_fingerprint),
        "tp2_zone_fingerprint": _v(tp2_zone_fingerprint),
        "atr_value": str(atr_value),
        "phase5_policy_fingerprint": phase5_policy_fingerprint,
        "risk_version": risk_version,
        "code_revision": code_revision,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_execution_fingerprint(
    source_phase4_fingerprint: str,
    side: RiskSide,
    execution_policy: EntryExecutionPolicy,
    signal_timestamp: datetime,
    earliest_exec_ts: datetime,
    is_filled: bool,
    raw_executable_price: Optional[Decimal],
    fill_price: Optional[Decimal],
    fill_timestamp: Optional[datetime],
    observed_spread: Decimal,
    synthetic_spread: Decimal,
    adverse_slippage: Decimal,
    source_evidence_type: Optional[str],
    source_evidence_fingerprint: Optional[str],
    phase5_policy_fingerprint: str,
    code_revision: str,
) -> str:
    """Deterministic SHA-256 fingerprint derived strictly from execution simulation inputs."""
    def _v(val: Any) -> str:
        return str(val) if val is not None else "NONE"

    def _ts(dt: Optional[datetime]) -> str:
        return canonical_utc_timestamp(dt) if dt is not None else "NONE"

    payload = {
        "source_phase4_fingerprint": source_phase4_fingerprint,
        "side": side.value,
        "execution_policy": execution_policy.value,
        "signal_timestamp": canonical_utc_timestamp(signal_timestamp),
        "earliest_exec_ts": canonical_utc_timestamp(earliest_exec_ts),
        "is_filled": "TRUE" if is_filled else "FALSE",
        "raw_executable_price": _v(raw_executable_price),
        "fill_price": _v(fill_price),
        "fill_timestamp": _ts(fill_timestamp),
        "observed_spread": str(observed_spread),
        "synthetic_spread": str(synthetic_spread),
        "adverse_slippage": str(adverse_slippage),
        "source_evidence_type": _v(source_evidence_type),
        "source_evidence_fingerprint": _v(source_evidence_fingerprint),
        "phase5_policy_fingerprint": phase5_policy_fingerprint,
        "code_revision": code_revision,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
