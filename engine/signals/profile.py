"""
Phase 4 XAUUSD Signal Profile Architecture, Feed Policy & Calibration Governance.

Provides explicit, immutable profile configurations and calibration governance
for XAUUSD dual-side signal intelligence. Ensures strict segregation between
historical frozen XAUT reference numbers and target XAUUSD configuration, preventing
uncalibrated or candidate instruments from silently inheriting legacy numerical fallbacks.
"""
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

from engine.core.types import FeedCriticality


def normalize_xauusd_target(target: Optional[str]) -> str:
    """
    Authoritative canonical instrument normalizer for Phase 4 XAUUSD engine.
    Strictly accepts 'XAUUSD' or 'XAU/USD' -> 'XAUUSD'.
    Rejects XAUT, XAUTUSD, XAUT/USDT, XAU, GOLD, GOLD_REFERENCE, and all other aliases.
    """
    if not target or not isinstance(target, str):
        raise ValueError(f"Invalid target instrument: {target}")
    cleaned = target.strip().upper().replace("/", "").replace("_", "").replace("-", "")
    if cleaned in ("XAUUSD", "XAUUSDSPOT"):
        return "XAUUSD"
    raise ValueError(f"Non-XAUUSD target instrument rejected: {target}")


def _deep_freeze(val: Any) -> Any:
    """Recursively convert nested dicts to MappingProxyType and collections to tuples."""
    if isinstance(val, (dict, MappingProxyType, Mapping)):
        return MappingProxyType({k: _deep_freeze(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple, set)):
        return tuple(_deep_freeze(x) for x in val)
    return val


class Phase4CalibrationStatus(str, Enum):
    """Authoritative lifecycle status for Phase 4 signal calibration governance."""
    LEGACY_REFERENCE = "LEGACY_REFERENCE"          # Historical XAUT only; invalid for XAUUSD
    PENDING_PHASE6 = "PENDING_PHASE6"              # Uncalibrated XAUUSD (production authority blocked)
    CANDIDATE_NOT_FROZEN = "CANDIDATE_NOT_FROZEN"  # Empirical candidate generated (production authority blocked)
    REVALIDATED_RESEARCH = "REVALIDATED_RESEARCH"  # Research backtest artifact (production authority blocked)


@dataclass(frozen=True)
class SideDirectionPolicy:
    """
    Independent Direction scoring weights for one side (Long or Short).
    All weights must be finite, non-negative, and sum to exactly 100.0.
    """
    weight_regime: Optional[float] = None
    weight_trend_1h: Optional[float] = None
    weight_trend_4h: Optional[float] = None
    weight_trend_1d: Optional[float] = None
    weight_structure_bos: Optional[float] = None
    weight_pullback: Optional[float] = None
    weight_momentum: Optional[float] = None
    weight_volume: Optional[float] = None

    @property
    def is_configured(self) -> bool:
        """Check completeness and mathematical validity of direction weights."""
        weights = [
            self.weight_regime, self.weight_trend_1h, self.weight_trend_4h,
            self.weight_trend_1d, self.weight_structure_bos, self.weight_pullback,
            self.weight_momentum, self.weight_volume
        ]
        if any(w is None for w in weights):
            return False
        for w in weights:
            if not math.isfinite(w):
                raise ValueError(f"Non-finite direction weight rejected: {w}")
            if w < 0.0:
                raise ValueError(f"Negative direction weight rejected: {w}")
        total = sum(weights)
        if abs(total - 100.0) > 1e-4:
            raise ValueError(f"Direction weights must sum to exactly 100.0, got {total}")
        return True


@dataclass(frozen=True)
class SideTimingPolicy:
    """
    Independent Timing scoring weights for one side (Long or Short).
    Macro safety is strictly excluded (handled outside scoring via Hard Safety Gate).
    All weights must be finite, non-negative, and sum to exactly 100.0 across 5 components.
    """
    weight_entry_zone: Optional[float] = None
    weight_reversal_confirmation_15m: Optional[float] = None
    weight_momentum_turn_15m_1h: Optional[float] = None
    weight_phase3a: Optional[float] = None
    weight_volume_response: Optional[float] = None

    @property
    def is_configured(self) -> bool:
        """Check completeness and mathematical validity of timing weights."""
        weights = [
            self.weight_entry_zone, self.weight_reversal_confirmation_15m,
            self.weight_momentum_turn_15m_1h, self.weight_phase3a,
            self.weight_volume_response
        ]
        if any(w is None for w in weights):
            return False
        for w in weights:
            if not math.isfinite(w):
                raise ValueError(f"Non-finite timing weight rejected: {w}")
            if w < 0.0:
                raise ValueError(f"Negative timing weight rejected: {w}")
        total = sum(weights)
        if abs(total - 100.0) > 1e-4:
            raise ValueError(f"Timing weights must sum to exactly 100.0, got {total}")
        return True


@dataclass(frozen=True)
class SideGatePolicy:
    """
    Independent State Machine thresholds for one side (Long or Short).
    Thresholds must be in [0.0, 100.0] and strictly monotonic.
    """
    threshold_watch_direction: Optional[float] = None
    threshold_ready_direction: Optional[float] = None
    threshold_ready_timing: Optional[float] = None
    threshold_window_direction: Optional[float] = None
    threshold_window_timing: Optional[float] = None

    @property
    def is_configured(self) -> bool:
        """Check completeness and monotonicity of gate thresholds."""
        th = [
            self.threshold_watch_direction, self.threshold_ready_direction,
            self.threshold_ready_timing, self.threshold_window_direction,
            self.threshold_window_timing
        ]
        if any(t is None for t in th):
            return False
        for t in th:
            if not math.isfinite(t):
                raise ValueError(f"Non-finite gate threshold rejected: {t}")
            if not (0.0 <= t <= 100.0):
                raise ValueError(f"Thresholds must be within [0.0, 100.0]: {th}")
        if not (self.threshold_watch_direction <= self.threshold_ready_direction <= self.threshold_window_direction):
            raise ValueError("Direction thresholds must be monotonic: watch <= ready <= window")
        if not (self.threshold_ready_timing <= self.threshold_window_timing):
            raise ValueError("Timing thresholds must be monotonic: ready <= window")
        return True


@dataclass(frozen=True)
class Phase4FeedPolicy:
    """Explicit requirement contract for market and analytical feeds."""
    primary_15m: FeedCriticality = FeedCriticality.CRITICAL
    primary_1h: FeedCriticality = FeedCriticality.OPTIONAL
    primary_4h: FeedCriticality = FeedCriticality.OPTIONAL
    primary_1d: FeedCriticality = FeedCriticality.OPTIONAL
    secondary_provider: FeedCriticality = FeedCriticality.OPTIONAL
    macro_blackout: FeedCriticality = FeedCriticality.CRITICAL
    volume: FeedCriticality = FeedCriticality.OPTIONAL
    phase3a: FeedCriticality = FeedCriticality.OPTIONAL
    phase3b: FeedCriticality = FeedCriticality.INFORMATIONAL
    dxy_yields_futures: FeedCriticality = FeedCriticality.INFORMATIONAL


@dataclass(frozen=True)
class Phase4SignalProfile:
    """
    Immutable specification of numerical thresholds, weights, and rules for XAUUSD Phase 4.
    Zero Empirical Default Numbers: All weights and thresholds strictly default to None.
    """
    name: str = "XAUUSD_UNCALIBRATED"
    target_instrument: str = "XAUUSD"
    calibration_status: Phase4CalibrationStatus = Phase4CalibrationStatus.PENDING_PHASE6
    timeframe: str = "15m"
    long_direction: SideDirectionPolicy = field(default_factory=SideDirectionPolicy)
    short_direction: SideDirectionPolicy = field(default_factory=SideDirectionPolicy)
    long_timing: SideTimingPolicy = field(default_factory=SideTimingPolicy)
    short_timing: SideTimingPolicy = field(default_factory=SideTimingPolicy)
    long_gate: SideGatePolicy = field(default_factory=SideGatePolicy)
    short_gate: SideGatePolicy = field(default_factory=SideGatePolicy)
    feed_policy: Phase4FeedPolicy = field(default_factory=Phase4FeedPolicy)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        norm_target = normalize_xauusd_target(self.target_instrument)
        object.__setattr__(self, "target_instrument", norm_target)
        object.__setattr__(self, "details", _deep_freeze(dict(self.details)) if self.details else MappingProxyType({}))

        # Target Invariant Guard
        if self.calibration_status == Phase4CalibrationStatus.LEGACY_REFERENCE:
            raise ValueError("XAUUSD target cannot use LEGACY_REFERENCE calibration status.")

    @property
    def is_fully_configured(self) -> bool:
        """Verify that all direction, timing, and gate sub-policies are configured."""
        return (
            self.long_direction.is_configured and self.short_direction.is_configured and
            self.long_timing.is_configured and self.short_timing.is_configured and
            self.long_gate.is_configured and self.short_gate.is_configured
        )

    @property
    def is_production_authorized(self) -> bool:
        """Production decision authority is strictly blocked pending Phase 6 validation."""
        return False


def uncalibrated_xauusd_signal_profile() -> Phase4SignalProfile:
    """Factory for default uncalibrated XAUUSD profile with zero empirical numbers."""
    return Phase4SignalProfile(
        name="XAUUSD_UNCALIBRATED",
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.PENDING_PHASE6,
        timeframe="15m",
        long_direction=SideDirectionPolicy(),
        short_direction=SideDirectionPolicy(),
        long_timing=SideTimingPolicy(),
        short_timing=SideTimingPolicy(),
        long_gate=SideGatePolicy(),
        short_gate=SideGatePolicy(),
        feed_policy=Phase4FeedPolicy(),
        details={"status": "UNCALIBRATED", "reason": "Awaiting Phase 6 empirical calibration"},
    )


def compute_phase4_policy_fingerprint(profile: Phase4SignalProfile) -> str:
    """
    Generate deterministic SHA-256 fingerprint derived strictly from immutable policy inputs.
    """
    def _encode_val(v: Any) -> Any:
        if v is None:
            return "NONE"
        if isinstance(v, (int, float)):
            if not math.isfinite(v):
                raise ValueError(f"Non-finite float in policy fingerprint: {v}")
            return str(round(float(v), 4))
        if isinstance(v, Enum):
            return v.value
        if isinstance(v, (dict, MappingProxyType, Mapping)):
            return {k: _encode_val(val) for k, val in sorted(v.items())}
        if isinstance(v, (list, tuple)):
            return [_encode_val(item) for item in v]
        return str(v)

    policy_payload: Dict[str, Any] = {
        "name": profile.name,
        "target_instrument": profile.target_instrument,
        "calibration_status": profile.calibration_status.value,
        "timeframe": profile.timeframe,
        "long_direction": {
            "weight_regime": _encode_val(profile.long_direction.weight_regime),
            "weight_trend_1h": _encode_val(profile.long_direction.weight_trend_1h),
            "weight_trend_4h": _encode_val(profile.long_direction.weight_trend_4h),
            "weight_trend_1d": _encode_val(profile.long_direction.weight_trend_1d),
            "weight_structure_bos": _encode_val(profile.long_direction.weight_structure_bos),
            "weight_pullback": _encode_val(profile.long_direction.weight_pullback),
            "weight_momentum": _encode_val(profile.long_direction.weight_momentum),
            "weight_volume": _encode_val(profile.long_direction.weight_volume),
        },
        "short_direction": {
            "weight_regime": _encode_val(profile.short_direction.weight_regime),
            "weight_trend_1h": _encode_val(profile.short_direction.weight_trend_1h),
            "weight_trend_4h": _encode_val(profile.short_direction.weight_trend_4h),
            "weight_trend_1d": _encode_val(profile.short_direction.weight_trend_1d),
            "weight_structure_bos": _encode_val(profile.short_direction.weight_structure_bos),
            "weight_pullback": _encode_val(profile.short_direction.weight_pullback),
            "weight_momentum": _encode_val(profile.short_direction.weight_momentum),
            "weight_volume": _encode_val(profile.short_direction.weight_volume),
        },
        "long_timing": {
            "weight_entry_zone": _encode_val(profile.long_timing.weight_entry_zone),
            "weight_reversal_confirmation_15m": _encode_val(profile.long_timing.weight_reversal_confirmation_15m),
            "weight_momentum_turn_15m_1h": _encode_val(profile.long_timing.weight_momentum_turn_15m_1h),
            "weight_phase3a": _encode_val(profile.long_timing.weight_phase3a),
            "weight_volume_response": _encode_val(profile.long_timing.weight_volume_response),
        },
        "short_timing": {
            "weight_entry_zone": _encode_val(profile.short_timing.weight_entry_zone),
            "weight_reversal_confirmation_15m": _encode_val(profile.short_timing.weight_reversal_confirmation_15m),
            "weight_momentum_turn_15m_1h": _encode_val(profile.short_timing.weight_momentum_turn_15m_1h),
            "weight_phase3a": _encode_val(profile.short_timing.weight_phase3a),
            "weight_volume_response": _encode_val(profile.short_timing.weight_volume_response),
        },
        "long_gate": {
            "threshold_watch_direction": _encode_val(profile.long_gate.threshold_watch_direction),
            "threshold_ready_direction": _encode_val(profile.long_gate.threshold_ready_direction),
            "threshold_ready_timing": _encode_val(profile.long_gate.threshold_ready_timing),
            "threshold_window_direction": _encode_val(profile.long_gate.threshold_window_direction),
            "threshold_window_timing": _encode_val(profile.long_gate.threshold_window_timing),
        },
        "short_gate": {
            "threshold_watch_direction": _encode_val(profile.short_gate.threshold_watch_direction),
            "threshold_ready_direction": _encode_val(profile.short_gate.threshold_ready_direction),
            "threshold_ready_timing": _encode_val(profile.short_gate.threshold_ready_timing),
            "threshold_window_direction": _encode_val(profile.short_gate.threshold_window_direction),
            "threshold_window_timing": _encode_val(profile.short_gate.threshold_window_timing),
        },
        "feed_policy": {
            "primary_15m": profile.feed_policy.primary_15m.value,
            "primary_1h": profile.feed_policy.primary_1h.value,
            "primary_4h": profile.feed_policy.primary_4h.value,
            "primary_1d": profile.feed_policy.primary_1d.value,
            "secondary_provider": profile.feed_policy.secondary_provider.value,
            "macro_blackout": profile.feed_policy.macro_blackout.value,
            "volume": profile.feed_policy.volume.value,
            "phase3a": profile.feed_policy.phase3a.value,
            "phase3b": profile.feed_policy.phase3b.value,
            "dxy_yields_futures": profile.feed_policy.dxy_yields_futures.value,
        },
        "details": _encode_val(profile.details),
    }

    canonical_json_str = json.dumps(policy_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json_str.encode("utf-8")).hexdigest()
