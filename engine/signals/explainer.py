"""Signal explainability and deterministic canonical analysis fingerprint generator (Phase 4)."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    Cycle3BExperimentalSnapshot,
    DirectionScoreResult,
    HardGateEvaluation,
    MarketContext,
    RegimeResult,
    SignalSnapshot,
    SignalState,
    StructureResult,
    TimingScoreResult,
    UserDecision,
)


def compute_canonical_fingerprint(
    instrument: str,
    timeframe: str,
    as_of: datetime,
    closed_candles: List[CandleData],
    direction: DirectionScoreResult,
    timing: TimingScoreResult,
    state: SignalState,
    user_decision: UserDecision,
    xau_reference_val: Optional[str] = None,
    xau_reference_ts: Optional[str] = None,
    usdt_rate_val: Optional[str] = None,
    usdt_rate_ts: Optional[str] = None,
    provider_status: str = "HEALTHY",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    engine_version: str = "4.0.0",
    config_version: str = "cfg-2026-v1",
    code_revision: str = "2795de04",
) -> str:
    """
    Generate deterministic SHA-256 analysis fingerprint from canonical production inputs.

    Strict Rules:
      1. Deterministic JSON: Sorted keys, compact separators, ISO-8601 UTC timestamps, strings for Decimals.
      2. Excludes live quote/ticker ticks (preserves closed-candle immutability A23).
      3. Excludes Phase 3B experimental output (preserves production weight 0.0 independence).
    """
    as_of_iso = (
        as_of.astimezone(timezone.utc).isoformat()
        if as_of.tzinfo
        else as_of.replace(tzinfo=timezone.utc).isoformat()
    )

    # Hash trailing closed candles sequence (open, high, low, close, vol)
    candle_hashes = []
    for c in closed_candles[-64:]:  # last 64 evaluation candles
        c_str = f"{c.timestamp_close.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{c.volume}"
        candle_hashes.append(hashlib.sha256(c_str.encode("utf-8")).hexdigest()[:16])

    production_payload: Dict[str, Any] = {
        "instrument": instrument,
        "timeframe": timeframe,
        "as_of": as_of_iso,
        "closed_candle_hashes": candle_hashes,
        "direction_score": str(direction.total_score),
        "timing_score": str(timing.total_score),
        "state": state.value,
        "user_decision": user_decision.value,
        "xau_reference": {
            "value": str(xau_reference_val) if xau_reference_val else "NONE",
            "timestamp": str(xau_reference_ts) if xau_reference_ts else "NONE",
        },
        "usdt_normalization": {
            "rate": str(usdt_rate_val) if usdt_rate_val else "NONE",
            "timestamp": str(usdt_rate_ts) if usdt_rate_ts else "NONE",
        },
        "provider_status": provider_status,
        "feature_version": feature_version,
        "cycle_version": cycle_version,
        "engine_version": engine_version,
        "config_version": config_version,
        "code_revision": code_revision,
    }

    canonical_json_str = json.dumps(production_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json_str.encode("utf-8")).hexdigest()


def compute_research_fingerprint(
    production_fingerprint: str,
    cycle_3b: Optional[Cycle3BExperimentalSnapshot] = None,
) -> Optional[str]:
    """Generate separate research fingerprint including Phase 3B experimental output."""
    if cycle_3b is None:
        return None

    research_payload = {
        "production_fingerprint": production_fingerprint,
        "experimental_version": cycle_3b.experimental_version,
        "reliability_score": str(cycle_3b.reliability.reliability_score),
        "reliability_status": cycle_3b.reliability.reliability_status.value,
        "dominant_period": str(cycle_3b.reliability.dominant_period_bars),
        "fft_power_ratio": str(cycle_3b.fft.power_ratio),
        "wavelet_energy_ratio": str(cycle_3b.wavelet.energy_ratio),
        "hilbert_phase": str(cycle_3b.hilbert.instantaneous_phase),
    }

    canonical_json_str = json.dumps(research_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json_str.encode("utf-8")).hexdigest()


def explain_signal(
    direction: DirectionScoreResult,
    timing: TimingScoreResult,
    hard_gate: HardGateEvaluation,
    state: SignalState,
    user_decision: UserDecision,
) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    """
    Format structured, positive, negative, and hard-gate explanatory reasons.
    """
    positive_reasons: List[str] = []
    negative_reasons: List[str] = []

    # Direction components explanation
    for comp in direction.components:
        if comp.score >= (comp.max_score * 0.60):
            positive_reasons.append(f"+ {comp.name}: {comp.reason} (+{comp.score}/{comp.max_score} pts)")
        else:
            negative_reasons.append(f"- {comp.name}: {comp.reason} ({comp.score}/{comp.max_score} pts)")

    # Timing components explanation
    for comp in timing.components:
        if comp.score >= (comp.max_score * 0.60):
            positive_reasons.append(f"+ {comp.name}: {comp.reason} (+{comp.score}/{comp.max_score} pts)")
        else:
            negative_reasons.append(f"- {comp.name}: {comp.reason} ({comp.score}/{comp.max_score} pts)")

    hard_gate_reasons = tuple(hard_gate.block_reasons)

    return tuple(positive_reasons), tuple(negative_reasons), hard_gate_reasons
