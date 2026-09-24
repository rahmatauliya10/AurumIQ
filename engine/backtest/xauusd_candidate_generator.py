"""
Phase 6 / Phase 8 XAUUSD Signal Candidate Search-Space & Generation Governance.

Authoritative loader, validator, deterministic seed deriver, and generator
for XAUUSD signal parameter candidates on governed simplex and monotonic domains.

Availability-Aware Recalibration:
- Direction: 6 active components (trend_1h, trend_4h, trend_1d,
  structure_bos, pullback, momentum) sampled on 6-simplex summing to 100.0.
  weight_regime is uncalibrated DISABLED (0.0) and weight_volume is structurally DISABLED (0.0).
- Timing: 3 active components (entry_zone, reversal_confirmation_15m,
  momentum_turn_15m_1h) sampled on 3-simplex summing to 100.0.
  weight_phase3a is authority locked (0.0) and weight_volume_response
  is structurally DISABLED (0.0).
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4FeedPolicy,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    compute_phase4_policy_fingerprint,
    normalize_xauusd_target,
)

DEFAULT_CANDIDATE_POLICY_PATH = Path(
    "artifacts/calibration/xauusd_signal_candidate_generation_policy.json"
)

ACTIVE_DIRECTION_COMPONENTS = (
    "weight_trend_1h",
    "weight_trend_4h",
    "weight_trend_1d",
    "weight_structure_bos",
    "weight_pullback",
    "weight_momentum",
)
DISABLED_DIRECTION_COMPONENTS = (
    "weight_regime",
    "weight_volume",
)

ACTIVE_TIMING_COMPONENTS = (
    "weight_entry_zone",
    "weight_reversal_confirmation_15m",
    "weight_momentum_turn_15m_1h",
)
DISABLED_TIMING_COMPONENTS = (
    "weight_phase3a",
    "weight_volume_response",
)


def compute_candidate_generation_policy_fingerprint(
    policy_dict: Dict[str, Any],
) -> str:
    """
    Generate deterministic SHA-256 fingerprint from canonical JSON payload
    excluding any fingerprint fields.
    """
    payload = {
        k: v
        for k, v in policy_dict.items()
        if k not in ("policy_fingerprint", "artifact_fingerprint", "fingerprint")
    }
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def derive_deterministic_seed(
    selection_policy_fingerprint: str,
    code_revision: str,
    dataset_fingerprint: str,
) -> int:
    """
    Derive 64-bit integer seed deterministically from immutable governance material:
    SHA256(selection_policy_fingerprint + ":" + code_revision + ":" + dataset_fingerprint)
    """
    if not selection_policy_fingerprint or not code_revision or not dataset_fingerprint:
        raise ValueError("All governance seed inputs must be non-empty strings.")
    material = f"{selection_policy_fingerprint}:{code_revision}:{dataset_fingerprint}".encode(
        "utf-8"
    )
    seed_hash = hashlib.sha256(material).hexdigest()
    return int(seed_hash[:16], 16)


@dataclass(frozen=True)
class XauUsdCandidateGenerationPolicy:
    """
    Governed specification of parameter search space and deterministic candidate generation.
    """
    schema: str
    policy_id: str
    selection_policy_fingerprint_reference: str
    code_revision: str
    dataset_fingerprint_reference: str
    instrument: str
    timeframe: str
    candidate_cap: int
    auto_increase_permitted: bool
    buy_sell_independence: bool
    policy_fingerprint: str
    raw_payload: Dict[str, Any]
    feature_availability: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "XauUsdCandidateGenerationPolicy":
        """Construct and validate policy from raw dictionary."""
        schema = data.get("schema")
        if schema not in (
            "aurumiq.calibration.candidate_generation_policy.v1",
            "aurumiq.calibration.candidate_generation_policy.v2",
            "aurumiq.calibration.candidate_generation_policy.v3",
        ):
            raise ValueError(
                f"Invalid candidate generation policy schema: '{schema}'"
            )

        policy_id = data.get("policy_id")
        if not policy_id or not isinstance(policy_id, str):
            raise ValueError(f"Invalid policy_id: '{policy_id}'")

        inst = data.get("instrument")
        if inst != "XAUUSD":
            raise ValueError(f"Policy must target 'XAUUSD', got '{inst}'")

        expected_fp = data.get("policy_fingerprint")
        if not expected_fp:
            raise ValueError("policy_fingerprint is required.")
        computed_fp = compute_candidate_generation_policy_fingerprint(data)
        if expected_fp != computed_fp:
            raise ValueError(
                f"Candidate generation policy fingerprint mismatch: expected '{expected_fp}', computed '{computed_fp}'"
            )

        candidate_cap = int(data.get("candidate_cap", 0))
        if candidate_cap != 100:
            raise ValueError(f"candidate_cap must be exactly 100, got {candidate_cap}")

        if data.get("auto_increase_permitted") is not False:
            raise ValueError("auto_increase_permitted must be strictly false.")

        if data.get("buy_sell_independence") is not True:
            raise ValueError("buy_sell_independence must be strictly true.")

        feature_availability = data.get("feature_availability")
        if feature_availability:
            dir_avail = feature_availability.get("direction", {})
            if dir_avail.get("weight_regime") != "DISABLED_UNCALIBRATED":
                raise ValueError(
                    "direction.weight_regime must be 'DISABLED_UNCALIBRATED'"
                )
            if dir_avail.get("weight_volume") != "DISABLED_STRUCTURAL":
                raise ValueError(
                    "direction.weight_volume must be 'DISABLED_STRUCTURAL'"
                )
            for k in ACTIVE_DIRECTION_COMPONENTS:
                if dir_avail.get(k) != "ACTIVE":
                    raise ValueError(f"direction.{k} must be 'ACTIVE'")

            tim_avail = feature_availability.get("timing", {})
            if tim_avail.get("weight_phase3a") != "DISABLED_AUTHORITY_LOCK":
                raise ValueError(
                    "timing.weight_phase3a must be 'DISABLED_AUTHORITY_LOCK'"
                )
            if tim_avail.get("weight_volume_response") != "DISABLED_STRUCTURAL":
                raise ValueError(
                    "timing.weight_volume_response must be 'DISABLED_STRUCTURAL'"
                )
            for k in ACTIVE_TIMING_COMPONENTS:
                if tim_avail.get(k) != "ACTIVE":
                    raise ValueError(f"timing.{k} must be 'ACTIVE'")

        return cls(
            schema=schema,
            policy_id=policy_id,
            selection_policy_fingerprint_reference=data.get(
                "selection_policy_fingerprint_reference", ""
            ),
            code_revision=data.get("code_revision", ""),
            dataset_fingerprint_reference=data.get(
                "dataset_fingerprint_reference", ""
            ),
            instrument=inst,
            timeframe=data.get("timeframe", "15m"),
            candidate_cap=candidate_cap,
            auto_increase_permitted=False,
            buy_sell_independence=True,
            policy_fingerprint=expected_fp,
            raw_payload=data,
            feature_availability=feature_availability,
        )


class XauUsdCandidateGenerator:
    """
    Deterministic candidate profile generator on uniform simplex and monotonic domains.
    Guarantees:
      1. Determinism: Identical inputs yield bit-for-bit identical candidate profiles.
      2. No Result Leakage: Operates ex-ante before backtest execution; zero OOS data dependency.
      3. Availability-Aware Simplex:
         - Direction: 7 active weights sum to 100.0 (7-simplex), weight_volume == 0.0.
         - Timing: 3 active weights sum to 100.0 (3-simplex), weight_phase3a == 0.0,
           weight_volume_response == 0.0.
      4. Monotonic Gates: watch <= ready <= window (direction), ready <= window (timing).
      5. Independence: BUY (Long) and SELL (Short) parameters generated independently.
      6. Strict Cap: Generates at most 100 distinct candidates without duplication.
    """

    def __init__(
        self,
        policy: XauUsdCandidateGenerationPolicy,
        dataset_fingerprint: Optional[str] = None,
    ):
        self.policy = policy
        self.dataset_fingerprint = (
            dataset_fingerprint or policy.dataset_fingerprint_reference
        )
        self.base_seed = derive_deterministic_seed(
            selection_policy_fingerprint=policy.selection_policy_fingerprint_reference,
            code_revision=policy.code_revision,
            dataset_fingerprint=self.dataset_fingerprint,
        )

    @staticmethod
    def _sample_simplex(
        rng: random.Random, n_components: int, precision: int = 4
    ) -> List[float]:
        """
        Sample uniformly on the (n_components - 1)-dimensional simplex:
        all w_i >= 0.0, sum(w_i) == 100.0.
        Uses standard uniform partition / order statistics method.
        """
        if n_components <= 1:
            return [100.0]
        cuts = sorted(rng.random() for _ in range(n_components - 1))
        cuts = [0.0] + cuts + [1.0]
        weights = [
            round(100.0 * (cuts[i] - cuts[i - 1]), precision)
            for i in range(1, n_components + 1)
        ]
        drift = round(100.0 - sum(weights), precision)
        max_idx = weights.index(max(weights))
        weights[max_idx] = round(weights[max_idx] + drift, precision)
        return weights

    @staticmethod
    def _equal_weights(n_components: int, precision: int = 4) -> List[float]:
        """
        Generate deterministic equal-weight partition summing to exactly 100.0.
        Adjusts rounding drift on the first component.
        """
        if n_components <= 0:
            return []
        base = round(100.0 / n_components, precision)
        weights = [base] * n_components
        drift = round(100.0 - sum(weights), precision)
        weights[0] = round(weights[0] + drift, precision)
        return weights

    @staticmethod
    def _sample_monotonic_gates(
        rng: random.Random,
    ) -> Tuple[float, float, float, float, float]:
        """
        Sample valid monotonic thresholds in [0.0, 100.0]:
          watch_d <= ready_d <= window_d
          ready_t <= window_t
        """
        d_cuts = sorted(rng.random() for _ in range(3))
        watch_d = round(100.0 * d_cuts[0], 2)
        ready_d = round(100.0 * d_cuts[1], 2)
        window_d = round(100.0 * d_cuts[2], 2)

        t_cuts = sorted(rng.random() for _ in range(2))
        ready_t = round(100.0 * t_cuts[0], 2)
        window_t = round(100.0 * t_cuts[1], 2)

        return watch_d, ready_d, ready_t, window_d, window_t

    def generate_candidate(
        self,
        candidate_index: int,
        attempt: int = 0,
    ) -> Phase4SignalProfile:
        """Generate a single deterministic Phase4SignalProfile candidate."""
        cand_material = f"{self.base_seed}:cand:{candidate_index}:att:{attempt}".encode(
            "utf-8"
        )
        cand_seed = int(hashlib.sha256(cand_material).hexdigest()[:16], 16)
        rng = random.Random(cand_seed)

        # Availability-aware weights:
        # Direction: 6 active components + weight_regime (0.0) + weight_volume (0.0)
        # Timing: 3 active components + weight_phase3a (0.0) + weight_volume_response (0.0)
        if candidate_index == 0:
            l_dir_active = self._equal_weights(6)
            s_dir_active = self._equal_weights(6)
            l_tim_active = self._equal_weights(3)
            s_tim_active = self._equal_weights(3)
            # Candidate 000: Frozen Reference Baseline preserves independent sampling
            l_watch_d, l_ready_d, l_ready_t, l_window_d, l_window_t = (
                self._sample_monotonic_gates(rng)
            )
            s_watch_d, s_ready_d, s_ready_t, s_window_d, s_window_t = (
                self._sample_monotonic_gates(rng)
            )
        else:
            # Candidates 001-099:
            # 1. Direction: independently sampled on 6-simplex for Long and Short
            l_dir_active = self._sample_simplex(rng, 6)
            s_dir_active = self._sample_simplex(rng, 6)
            # 2. Timing: coupled on 3-simplex (long_timing == short_timing)
            tim_active = self._sample_simplex(rng, 3)
            l_tim_active = tim_active
            s_tim_active = tim_active
            # 3. Gates: coupled monotonic gate tuple across governed domain [0.0, 100.0] (long_gate == short_gate)
            watch_d, ready_d, ready_t, window_d, window_t = (
                self._sample_monotonic_gates(rng)
            )
            l_watch_d, l_ready_d, l_ready_t, l_window_d, l_window_t = (
                watch_d, ready_d, ready_t, window_d, window_t
            )
            s_watch_d, s_ready_d, s_ready_t, s_window_d, s_window_t = (
                watch_d, ready_d, ready_t, window_d, window_t
            )

        long_direction = SideDirectionPolicy(
            weight_regime=0.0,
            weight_trend_1h=l_dir_active[0],
            weight_trend_4h=l_dir_active[1],
            weight_trend_1d=l_dir_active[2],
            weight_structure_bos=l_dir_active[3],
            weight_pullback=l_dir_active[4],
            weight_momentum=l_dir_active[5],
            weight_volume=0.0,
        )
        short_direction = SideDirectionPolicy(
            weight_regime=0.0,
            weight_trend_1h=s_dir_active[0],
            weight_trend_4h=s_dir_active[1],
            weight_trend_1d=s_dir_active[2],
            weight_structure_bos=s_dir_active[3],
            weight_pullback=s_dir_active[4],
            weight_momentum=s_dir_active[5],
            weight_volume=0.0,
        )
        long_timing = SideTimingPolicy(
            weight_entry_zone=l_tim_active[0],
            weight_reversal_confirmation_15m=l_tim_active[1],
            weight_momentum_turn_15m_1h=l_tim_active[2],
            weight_phase3a=0.0,
            weight_volume_response=0.0,
        )
        short_timing = SideTimingPolicy(
            weight_entry_zone=s_tim_active[0],
            weight_reversal_confirmation_15m=s_tim_active[1],
            weight_momentum_turn_15m_1h=s_tim_active[2],
            weight_phase3a=0.0,
            weight_volume_response=0.0,
        )
        long_gate = SideGatePolicy(
            threshold_watch_direction=l_watch_d,
            threshold_ready_direction=l_ready_d,
            threshold_ready_timing=l_ready_t,
            threshold_window_direction=l_window_d,
            threshold_window_timing=l_window_t,
        )
        short_gate = SideGatePolicy(
            threshold_watch_direction=s_watch_d,
            threshold_ready_direction=s_ready_d,
            threshold_ready_timing=s_ready_t,
            threshold_window_direction=s_window_d,
            threshold_window_timing=s_window_t,
        )

        profile = Phase4SignalProfile(
            name=f"XAUUSD_CANDIDATE_{candidate_index:03d}",
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
            timeframe="15m",
            long_direction=long_direction,
            short_direction=short_direction,
            long_timing=long_timing,
            short_timing=short_timing,
            long_gate=long_gate,
            short_gate=short_gate,
            feed_policy=Phase4FeedPolicy(),
            details={
                "candidate_index": candidate_index,
                "is_baseline": candidate_index == 0,
                "generation_seed": cand_seed,
                "feature_availability": {
                    "direction_active_components": 6,
                    "direction_disabled_components": [
                        "weight_regime",
                        "weight_volume",
                    ],
                    "timing_active_components": 3,
                    "timing_disabled_components": [
                        "weight_phase3a",
                        "weight_volume_response",
                    ],
                },
            },
        )
        return profile

    def generate_all_candidates(
        self, count: Optional[int] = None
    ) -> List[Phase4SignalProfile]:
        """
        Generate exactly `count` unique candidates (default up to policy.candidate_cap).
        Enforces no duplicate fingerprints and strict validation.
        """
        target_count = (
            count if count is not None else self.policy.candidate_cap
        )
        if target_count > self.policy.candidate_cap:
            raise ValueError(
                f"Requested candidate count ({target_count}) exceeds governed cap ({self.policy.candidate_cap})"
            )
        if target_count <= 0:
            raise ValueError("Candidate count must be strictly positive.")

        candidates: List[Phase4SignalProfile] = []
        seen_fingerprints = set()

        for idx in range(target_count):
            attempt = 0
            while True:
                profile = self.generate_candidate(idx, attempt)
                if not profile.is_fully_configured:
                    raise ValueError(
                        f"Generated candidate {idx} failed is_fully_configured validation."
                    )
                fp = compute_phase4_policy_fingerprint(profile)
                if fp not in seen_fingerprints:
                    seen_fingerprints.add(fp)
                    candidates.append(profile)
                    break
                attempt += 1
                if attempt > 50:
                    raise RuntimeError(
                        f"Excessive fingerprint collision at candidate index {idx}"
                    )

        return candidates


def load_governed_candidate_generation_policy(
    policy_path: Optional[Path] = None,
) -> XauUsdCandidateGenerationPolicy:
    """Load and strictly validate authoritative candidate generation policy from disk."""
    path = policy_path or DEFAULT_CANDIDATE_POLICY_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Candidate generation policy artifact not found at: {path}"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return XauUsdCandidateGenerationPolicy.from_dict(data)
