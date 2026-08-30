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
    code_revision: str,
    closed_candles_4h: Optional[List[CandleData]] = None,
    closed_candles_1d: Optional[List[CandleData]] = None,
    closed_candles_xau: Optional[List[CandleData]] = None,
    cycle_3a: Optional[Cycle3ASnapshot] = None,
    xau_reference_val: Optional[str] = None,
    xau_reference_ts: Optional[str] = None,
    usdt_rate_val: Optional[str] = None,
    usdt_rate_ts: Optional[str] = None,
    macro_state: Optional[str] = None,
    is_feed_stale: bool = False,
    is_provider_transition: bool = False,
    provider_status: str = "HEALTHY",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    engine_version: str = "4.0.0",
    config_version: str = "cfg-2026-v1",
) -> str:
    """
    Generate deterministic SHA-256 analysis fingerprint from canonical production inputs.

    Strict Rules:
      1. Deterministic JSON: Sorted keys, compact separators, ISO-8601 UTC timestamps, strings for Decimals.
      2. Excludes live quote/ticker ticks (preserves closed-candle immutability A23).
      3. Excludes Phase 3B experimental output (preserves production weight 0.0 independence).
      4. Includes multi-timeframe inputs, historical XAU series, Phase 3A canonical identity,
         and individual component breakdowns for complete material provenance.
    """
    as_of_iso = (
        as_of.astimezone(timezone.utc).isoformat()
        if as_of.tzinfo
        else as_of.replace(tzinfo=timezone.utc).isoformat()
    )

    def _hash_candles(candles: Optional[List[CandleData]], limit: int = 64) -> List[str]:
        if not candles:
            return []
        hashes = []
        for c in candles[-limit:]:
            c_str = f"{c.timestamp_close.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{c.volume}"
            hashes.append(hashlib.sha256(c_str.encode("utf-8")).hexdigest()[:16])
        return hashes

    candle_hashes = _hash_candles(closed_candles, 64)
    candle_4h_hashes = _hash_candles(closed_candles_4h, 32)
    candle_1d_hashes = _hash_candles(closed_candles_1d, 16)
    candle_xau_hashes = _hash_candles(closed_candles_xau, 64)

    dir_components = [
        {"name": c.name, "score": str(round(c.score, 2)), "max": str(round(c.max_score, 2))}
        for c in direction.components
    ] if direction.components else []

    tim_components = [
        {"name": c.name, "score": str(round(c.score, 2)), "max": str(round(c.max_score, 2))}
        for c in timing.components
    ] if timing.components else []

    phase3a_dict: Any = "NONE"
    if cycle_3a is not None:
        phase3a_dict = {
            "timestamp": cycle_3a.timestamp.isoformat() if cycle_3a.timestamp else "NONE",
            "cycle_version": cycle_3a.cycle_version,
            "cycle_score_3a": str(round(cycle_3a.cycle_score_3a, 2)),
            "session": (
                cycle_3a.session.session.value
                if (cycle_3a.session and hasattr(cycle_3a.session.session, "value"))
                else str(cycle_3a.session)
            ),
            "is_blocked_by_event": bool(cycle_3a.is_blocked_by_event),
        }

    production_payload: Dict[str, Any] = {
        "instrument": instrument,
        "timeframe": timeframe,
        "as_of": as_of_iso,
        "closed_candle_hashes": candle_hashes,
        "closed_candle_4h_hashes": candle_4h_hashes,
        "closed_candle_1d_hashes": candle_1d_hashes,
        "closed_candle_xau_hashes": candle_xau_hashes,
        "direction_score": str(direction.total_score),
        "direction_components": dir_components,
        "timing_score": str(timing.total_score),
        "timing_components": tim_components,
        "state": state.value,
        "user_decision": user_decision.value,
        "macro_state": macro_state or "MISSING",
        "hard_gate_inputs": {
            "is_feed_stale": is_feed_stale,
            "is_provider_transition": is_provider_transition,
        },
        "phase3a_identity": phase3a_dict,
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
