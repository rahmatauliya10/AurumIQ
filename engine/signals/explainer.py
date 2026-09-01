"""Signal explainability and deterministic canonical analysis fingerprint generator (Phase 4)."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from engine.core.types import (
    CandidateGateResult,
    CandleData,
    Cycle3ASnapshot,
    Cycle3BExperimentalSnapshot,
    DirectionScoreResult,
    HardGateEvaluation,
    MarketContext,
    RegimeResult,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSnapshot,
    SignalState,
    StructureResult,
    TimingScoreResult,
    UserDecision,
    XauUsdHardGateEvaluation,
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


# --- Phase 4 XAUUSD Fingerprinting & Explainer Engine ---

def compute_xauusd_fingerprint(
    timestamp: datetime,
    instrument: str,
    timeframe: str,
    phase4_policy_fingerprint: str,
    closed_candle_15m_hash: str,
    closed_candle_1h_hash: str,
    closed_candle_4h_hash: str,
    closed_candle_1d_hash: str,
    long_direction: SideDirectionScoreResult,
    short_direction: SideDirectionScoreResult,
    long_timing: SideTimingScoreResult,
    short_timing: SideTimingScoreResult,
    runtime_health: RuntimeFeedHealth,
    published_state: SignalState,
    published_user_decision: UserDecision,
    candidate_state: SignalState,
    candidate_user_decision: UserDecision,
    candidate_resolution_reason: str,
    publication_reason: str,
    code_revision: str,
    cycle_3a_identity: Optional[str] = None,
    resolution_reason: Optional[str] = None,
) -> str:
    """
    Generate deterministic SHA-256 analysis fingerprint for Phase 4 XAUUSD signal evaluation.
    Strictly binds policy configuration, multi-timeframe candle hashes (15m, 1H, 4H, 1D),
    dual-side scores, runtime feed health, candidate reason, and publication reason.
    """
    def _format_comp(comps):
        return [
            {
                "name": c.name,
                "score": f"{float(c.score):.4f}" if c.score is not None else "NONE",
                "max_score": f"{float(c.max_score):.4f}" if c.max_score is not None else "NONE",
                "reason": c.reason,
                "is_available": c.is_available,
            }
            for c in comps
        ]

    effective_pub_reason = publication_reason or resolution_reason or ""

    payload: Dict[str, Any] = {
        "timestamp": timestamp.isoformat(),
        "instrument": instrument,
        "timeframe": timeframe,
        "phase4_policy_fingerprint": phase4_policy_fingerprint,
        "candle_hashes": {
            "15m": closed_candle_15m_hash,
            "1h": closed_candle_1h_hash,
            "4h": closed_candle_4h_hash,
            "1d": closed_candle_1d_hash,
        },
        "long_direction": {
            "total_score": f"{float(long_direction.total_score):.4f}" if long_direction.total_score is not None else "NONE",
            "is_valid": long_direction.is_valid,
            "is_ready": long_direction.is_direction_ready,
            "components": _format_comp(long_direction.components),
        },
        "short_direction": {
            "total_score": f"{float(short_direction.total_score):.4f}" if short_direction.total_score is not None else "NONE",
            "is_valid": short_direction.is_valid,
            "is_ready": short_direction.is_direction_ready,
            "components": _format_comp(short_direction.components),
        },
        "long_timing": {
            "total_score": f"{float(long_timing.total_score):.4f}" if long_timing.total_score is not None else "NONE",
            "is_valid": long_timing.is_valid,
            "is_ready": long_timing.is_timing_ready,
            "components": _format_comp(long_timing.components),
        },
        "short_timing": {
            "total_score": f"{float(short_timing.total_score):.4f}" if short_timing.total_score is not None else "NONE",
            "is_valid": short_timing.is_valid,
            "is_ready": short_timing.is_timing_ready,
            "components": _format_comp(short_timing.components),
        },
        "runtime_health": {
            "primary_15m": runtime_health.primary_15m.value,
            "primary_1h": runtime_health.primary_1h.value,
            "primary_4h": runtime_health.primary_4h.value,
            "primary_1d": runtime_health.primary_1d.value,
            "secondary_provider": runtime_health.secondary_provider.value,
            "secondary_provider_disagreement": runtime_health.secondary_provider_disagreement,
            "macro_blackout_feed": runtime_health.macro_blackout_feed.value,
            "is_macro_blackout": runtime_health.is_macro_blackout,
            "volume": runtime_health.volume.value,
            "phase3a": runtime_health.phase3a.value,
            "phase3b": runtime_health.phase3b.value,
            "is_unclosed_candle": runtime_health.is_unclosed_candle,
        },
        "published_state": published_state.value,
        "published_user_decision": published_user_decision.value,
        "candidate_state": candidate_state.value,
        "candidate_user_decision": candidate_user_decision.value,
        "candidate_resolution_reason": candidate_resolution_reason,
        "publication_reason": effective_pub_reason,
        "code_revision": code_revision,
        "cycle_3a_identity": cycle_3a_identity or "NONE",
    }

    canonical_json_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json_str.encode("utf-8")).hexdigest()


def explain_dual_side_signal(
    long_direction: SideDirectionScoreResult,
    short_direction: SideDirectionScoreResult,
    long_timing: SideTimingScoreResult,
    short_timing: SideTimingScoreResult,
    hard_gate: XauUsdHardGateEvaluation,
    candidate_result: CandidateGateResult,
    is_production_authorized: bool = False,
) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], str, str]:
    """
    Format structured positive and negative explanatory reasons for Long and Short setups.
    Returns (reasons_long_pos, reasons_long_neg, reasons_short_pos, reasons_short_neg, hard_gate_reasons, candidate_resolution_reason, publication_reason).
    """
    reasons_long_pos: List[str] = []
    reasons_long_neg: List[str] = []
    reasons_short_pos: List[str] = []
    reasons_short_neg: List[str] = []

    for comp in long_direction.components:
        if comp.score >= (comp.max_score * 0.60):
            reasons_long_pos.append(f"+ {comp.name}: {comp.reason} (+{comp.score}/{comp.max_score} pts)")
        else:
            reasons_long_neg.append(f"- {comp.name}: {comp.reason} ({comp.score}/{comp.max_score} pts)")

    for comp in long_timing.components:
        if comp.score >= (comp.max_score * 0.60):
            reasons_long_pos.append(f"+ {comp.name}: {comp.reason} (+{comp.score}/{comp.max_score} pts)")
        else:
            reasons_long_neg.append(f"- {comp.name}: {comp.reason} ({comp.score}/{comp.max_score} pts)")

    for comp in short_direction.components:
        if comp.score >= (comp.max_score * 0.60):
            reasons_short_pos.append(f"+ {comp.name}: {comp.reason} (+{comp.score}/{comp.max_score} pts)")
        else:
            reasons_short_neg.append(f"- {comp.name}: {comp.reason} ({comp.score}/{comp.max_score} pts)")

    for comp in short_timing.components:
        if comp.score >= (comp.max_score * 0.60):
            reasons_short_pos.append(f"+ {comp.name}: {comp.reason} (+{comp.score}/{comp.max_score} pts)")
        else:
            reasons_short_neg.append(f"- {comp.name}: {comp.reason} ({comp.score}/{comp.max_score} pts)")

    hard_gate_reasons = tuple(hard_gate.block_reasons)
    candidate_resolution_reason = candidate_result.resolution_reason

    if hard_gate.is_blocked:
        publication_reason = f"SYSTEM_SAFETY_HOLD: {'; '.join(hard_gate.block_reasons)}"
    elif not is_production_authorized:
        publication_reason = f"BLOCKED_PENDING_PHASE6_CALIBRATION (Candidate: {candidate_result.candidate_state.value} / {candidate_result.candidate_user_decision.value})"
    else:
        publication_reason = candidate_result.resolution_reason

    return (
        tuple(reasons_long_pos),
        tuple(reasons_long_neg),
        tuple(reasons_short_pos),
        tuple(reasons_short_neg),
        hard_gate_reasons,
        candidate_resolution_reason,
        publication_reason,
    )

