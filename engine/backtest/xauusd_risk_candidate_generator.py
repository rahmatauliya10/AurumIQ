"""
Phase 5/6 XAUUSD Risk & Joint Candidate Search-Space & Generation Governance.

Authoritative loader, validator, deterministic seed deriver, and generator
for XAUUSD risk profiles and joint (signal + risk) candidates.
Implements deterministic empirical-rank mapping from TRAIN-only causal geometry,
independent LONG/SHORT parameterization, and strict joint search budget (<= 100).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Tuple

FOLD1_TRAIN_START = datetime(2020, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
FOLD1_TRAIN_END_EXCLUSIVE = datetime(2024, 2, 8, 19, 12, 0, tzinfo=timezone.utc)


def validate_risk_geometry_timestamp(ts: datetime) -> None:
    """
    Ensure PIT structural geometry observation timestamp falls strictly within Fold 1 TRAIN:
    2020-04-07T00:00:00Z <= ts < 2024-02-08T19:12:00Z.
    Rejects any geometry at or after Fold 1 train_end (validation leakage).
    """
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError("Geometry timestamp must be timezone-aware UTC.")
    ts_utc = ts.astimezone(timezone.utc)
    if ts_utc < FOLD1_TRAIN_START:
        raise ValueError(
            f"Geometry timestamp {ts_utc.isoformat()} precedes Fold 1 train_start ({FOLD1_TRAIN_START.isoformat()})."
        )
    if ts_utc >= FOLD1_TRAIN_END_EXCLUSIVE:
        raise ValueError(
            f"Validation leakage detected: geometry timestamp {ts_utc.isoformat()} "
            f"is at or after Fold 1 train_end ({FOLD1_TRAIN_END_EXCLUSIVE.isoformat()})."
        )

from engine.backtest.xauusd_candidate_generator import (
    XauUsdCandidateGenerator,
    load_governed_candidate_generation_policy,
)
from engine.core.types import EntryExecutionPolicy, Phase5CalibrationStatus
from engine.risk.xauusd_fingerprints import compute_phase5_policy_fingerprint
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)
from engine.signals.profile import Phase4SignalProfile

DEFAULT_RISK_CANDIDATE_POLICY_PATH = Path("artifacts/calibration/xauusd_risk_candidate_generation_policy.json")


def compute_risk_candidate_generation_policy_fingerprint(policy_dict: Dict[str, Any]) -> str:
    """
    Generate deterministic SHA-256 fingerprint from canonical JSON payload
    excluding any fingerprint fields.
    """
    payload = {
        k: v for k, v in policy_dict.items()
        if k not in ("policy_fingerprint", "artifact_fingerprint", "fingerprint")
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def derive_risk_deterministic_seed(
    selection_policy_fingerprint: str,
    signal_candidate_policy_fingerprint: str,
    dataset_fingerprint: str,
    code_revision: str,
) -> int:
    """
    Derive 64-bit integer seed deterministically from immutable governance material:
    SHA256(selection_policy_fp + ":" + signal_candidate_policy_fp + ":" + dataset_fp + ":" + code_rev)
    """
    if not (selection_policy_fingerprint and signal_candidate_policy_fingerprint and dataset_fingerprint and code_revision):
        raise ValueError("All governance seed inputs must be non-empty strings.")
    material = f"{selection_policy_fingerprint}:{signal_candidate_policy_fingerprint}:{dataset_fingerprint}:{code_revision}".encode("utf-8")
    seed_hash = hashlib.sha256(material).hexdigest()
    return int(seed_hash[:16], 16)


@dataclass(frozen=True)
class XauUsdRiskCandidateGenerationPolicy:
    """
    Governed specification of risk parameter search space and deterministic candidate generation.
    """
    schema: str
    policy_id: str
    selection_policy_fingerprint_reference: str
    signal_candidate_policy_fingerprint_reference: str
    dataset_fingerprint_reference: str
    code_revision: str
    instrument: str
    timeframe: str
    candidate_cap: int
    auto_increase_permitted: bool
    buy_sell_independence: bool
    policy_fingerprint: str
    raw_payload: Dict[str, Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "XauUsdRiskCandidateGenerationPolicy":
        """Construct and validate risk policy from raw dictionary."""
        schema = data.get("schema")
        if schema != "aurumiq.calibration.risk_candidate_generation_policy.v1":
            raise ValueError(f"Invalid risk candidate generation policy schema: '{schema}'")

        policy_id = data.get("policy_id")
        if not policy_id or not isinstance(policy_id, str):
            raise ValueError(f"Invalid policy_id: '{policy_id}'")

        inst = data.get("instrument")
        if inst != "XAUUSD":
            raise ValueError(f"Policy must target 'XAUUSD', got '{inst}'")

        expected_fp = data.get("policy_fingerprint")
        if not expected_fp:
            raise ValueError("policy_fingerprint is required.")
        computed_fp = compute_risk_candidate_generation_policy_fingerprint(data)
        if expected_fp != computed_fp:
            raise ValueError(
                f"Risk candidate generation policy fingerprint mismatch: expected '{expected_fp}', computed '{computed_fp}'"
            )

        candidate_cap = int(data.get("candidate_cap", 0))
        if candidate_cap != 100:
            raise ValueError(f"candidate_cap must be strictly 100, got {candidate_cap}")

        if data.get("auto_increase_permitted") is not False:
            raise ValueError("auto_increase_permitted must be strictly False")

        if data.get("buy_sell_independence") is not True:
            raise ValueError("buy_sell_independence must be strictly True")

        # Verify production authority status
        prod_status = data.get("production_authority_status", {})
        if prod_status.get("is_production_authorized") is not False:
            raise ValueError("is_production_authorized must be strictly False in risk candidate policy.")
        if prod_status.get("paper_only") is not True:
            raise ValueError("paper_only must be strictly True in risk candidate policy.")
        if prod_status.get("real_order_execution") != "disabled":
            raise ValueError("real_order_execution must be strictly 'disabled' in risk candidate policy.")

        # Verify core parameters and tp2 synthetic fallback decision
        core_params = data.get("core_risk_parameters", [])
        expected_params = ["structure_buffer", "atr_multiplier", "max_stop_distance_atr", "min_rr_tp1"]
        if core_params != expected_params:
            raise ValueError(f"core_risk_parameters must be {expected_params}, got {core_params}")

        tp2_policy = data.get("tp2_synthetic_fallback_policy", {})
        if tp2_policy.get("tp2_atr_multiplier") is not None:
            raise ValueError("tp2_atr_multiplier must be strictly None for first calibration.")

        # Verify execution semantics
        exec_sem = data.get("execution_model_semantics", {})
        if exec_sem.get("true_requested_price_slippage") != "UNOBSERVABLE":
            raise ValueError("true_requested_price_slippage must be strictly UNOBSERVABLE.")
        if exec_sem.get("entry_execution_policy") != "NEXT_BAR_OPEN":
            raise ValueError("entry_execution_policy must be NEXT_BAR_OPEN.")

        # Verify historical bounds and strict Fold 1 TRAIN-only rule
        bounds = data.get("historical_bounds", {})
        if bounds.get("common_risk_domain_source") != "FOLD_1_TRAIN_ONLY":
            raise ValueError(
                f"common_risk_domain_source must be 'FOLD_1_TRAIN_ONLY', got '{bounds.get('common_risk_domain_source')}'"
            )
        if bounds.get("train_start") != "2020-04-07T00:00:00+00:00":
            raise ValueError(f"train_start must be '2020-04-07T00:00:00+00:00', got '{bounds.get('train_start')}'")
        if bounds.get("train_end") != "2024-02-08T19:12:00+00:00":
            raise ValueError(f"train_end must be '2024-02-08T19:12:00+00:00', got '{bounds.get('train_end')}'")
        if bounds.get("val_start") != "2024-02-08T19:12:00+00:00":
            raise ValueError(f"val_start must be '2024-02-08T19:12:00+00:00', got '{bounds.get('val_start')}'")
        if bounds.get("val_end") != "2025-05-21T09:36:00+00:00":
            raise ValueError(f"val_end must be '2025-05-21T09:36:00+00:00', got '{bounds.get('val_end')}'")
        if bounds.get("oos_start") != "2025-05-21T09:36:00+00:00":
            raise ValueError(f"oos_start must be '2025-05-21T09:36:00+00:00', got '{bounds.get('oos_start')}'")
        if bounds.get("oos_end") != "2025-08-22T22:04:48+00:00":
            raise ValueError(f"oos_end must be '2025-08-22T22:04:48+00:00', got '{bounds.get('oos_end')}'")

        # Explicit rejection of stale / false 2025-04-06 / 2025-04-07 boundaries
        bounds_str = json.dumps(bounds)
        if "2025-04-06" in bounds_str or "2025-04-07" in bounds_str:
            raise ValueError("Stale fold boundaries (2025-04-06 / 2025-04-07) are strictly forbidden.")

        # Verify geometry artifact reference exists
        geom_ref = data.get("geometry_artifact_fingerprint_reference")
        if not geom_ref or not isinstance(geom_ref, str) or len(geom_ref) != 64:
            raise ValueError(f"Valid geometry_artifact_fingerprint_reference is required, got '{geom_ref}'")

        # Verify reference_candidate_0 p50 equality with geometric_observations
        geom_obs = data.get("train_only_causal_geometry", {}).get("geometric_observations", {})
        ref_params = data.get("reference_candidate_0", {}).get("risk_parameters", {})
        for side in ["long", "short"]:
            side_support_key = f"{side}_support"
            for param in ["structure_buffer", "atr_multiplier", "max_stop_distance_atr", "min_rr_tp1"]:
                expected_p50 = geom_obs.get(param, {}).get(side_support_key, {}).get("p50")
                actual_p50 = ref_params.get(side, {}).get(param)
                if expected_p50 is None or actual_p50 is None or float(expected_p50) != float(actual_p50):
                    raise ValueError(
                        f"REFERENCE_CANDIDATE_0 {side}.{param} ({actual_p50}) does not equal computed p50 ({expected_p50})"
                    )

        return cls(
            schema=schema,
            policy_id=policy_id,
            selection_policy_fingerprint_reference=data["selection_policy_fingerprint_reference"],
            signal_candidate_policy_fingerprint_reference=data["signal_candidate_policy_fingerprint_reference"],
            dataset_fingerprint_reference=data["dataset_fingerprint_reference"],
            code_revision=data["code_revision"],
            instrument=inst,
            timeframe=data.get("timeframe", "15m"),
            candidate_cap=candidate_cap,
            auto_increase_permitted=False,
            buy_sell_independence=True,
            policy_fingerprint=expected_fp,
            raw_payload=data,
        )


def _interpolate_quantile(u: float, support: Dict[str, float]) -> Decimal:
    """
    Map uniform draw u in (0, 1) piecewise linearly through empirical quantiles:
    p10 -> p25 -> p50 -> p75 -> p90.
    Values below 0.10 clamp to p10, values above 0.90 clamp to p90.
    """
    p10 = support["p10"]
    p25 = support["p25"]
    p50 = support["p50"]
    p75 = support["p75"]
    p90 = support["p90"]

    if u <= 0.10:
        val = p10
    elif u <= 0.25:
        frac = (u - 0.10) / 0.15
        val = p10 + frac * (p25 - p10)
    elif u <= 0.50:
        frac = (u - 0.25) / 0.25
        val = p25 + frac * (p50 - p25)
    elif u <= 0.75:
        frac = (u - 0.50) / 0.25
        val = p50 + frac * (p75 - p50)
    elif u <= 0.90:
        frac = (u - 0.75) / 0.15
        val = p75 + frac * (p90 - p75)
    else:
        val = p90

    return Decimal(str(round(val, 2)))


class XauUsdRiskCandidateGenerator:
    """
    Deterministic candidate generator for XAUUSD Phase 5 risk profiles.
    Candidate 0 is frozen ex-ante as REFERENCE_CANDIDATE_0 (central representative TRAIN geometry).
    Candidates 1..99 are mapped deterministically from TRAIN empirical geometry quantiles.
    """

    def __init__(self, policy: XauUsdRiskCandidateGenerationPolicy):
        self.policy = policy
        self.master_seed = derive_risk_deterministic_seed(
            selection_policy_fingerprint=policy.selection_policy_fingerprint_reference,
            signal_candidate_policy_fingerprint=policy.signal_candidate_policy_fingerprint_reference,
            dataset_fingerprint=policy.dataset_fingerprint_reference,
            code_revision=policy.code_revision,
        )
        geo = policy.raw_payload.get("train_only_causal_geometry", {}).get("geometric_observations", {})
        self.geom_support = geo

    def _governed_execution_policy(self) -> XauUsdExecutionPolicy:
        """Construct governed NEXT_BAR_OPEN execution policy with Phase 6 frozen values."""
        return XauUsdExecutionPolicy(
            latency_seconds=0.0,
            synthetic_spread_points=Decimal("260.0"),
            point_size=Decimal("0.001"),
            modeled_execution_gap_points=Decimal("0.0"),
            slippage_pct=Decimal("0.0"),
        )

    def generate_candidate(self, candidate_index: int, attempt: int = 0) -> XauUsdRiskProfile:
        """Generate deterministic risk profile for a given candidate index."""
        if candidate_index < 0:
            raise ValueError("Candidate index must be non-negative.")
        if candidate_index >= self.policy.candidate_cap:
            raise ValueError(
                f"Candidate index ({candidate_index}) exceeds governed cap ({self.policy.candidate_cap})"
            )

        exec_pol = self._governed_execution_policy()

        # Candidate 0: REFERENCE_CANDIDATE_0 (Central Representative TRAIN Geometry Anchor)
        if candidate_index == 0:
            ref_params = self.policy.raw_payload.get("reference_candidate_0", {}).get("risk_parameters", {})
            ref_l = ref_params.get("long", {})
            ref_s = ref_params.get("short", {})
            ref_lr = SideRiskPolicy(
                structure_buffer=Decimal(str(ref_l.get("structure_buffer", "1.37"))),
                atr_multiplier=Decimal(str(ref_l.get("atr_multiplier", "1.26"))),
                max_stop_distance_atr=Decimal(str(ref_l.get("max_stop_distance_atr", "3.06"))),
                min_rr_tp1=Decimal(str(ref_l.get("min_rr_tp1", "0.85"))),
                tp2_atr_multiplier=None,
            )
            ref_sr = SideRiskPolicy(
                structure_buffer=Decimal(str(ref_s.get("structure_buffer", "1.36"))),
                atr_multiplier=Decimal(str(ref_s.get("atr_multiplier", "1.26"))),
                max_stop_distance_atr=Decimal(str(ref_s.get("max_stop_distance_atr", "2.79"))),
                min_rr_tp1=Decimal(str(ref_s.get("min_rr_tp1", "0.91"))),
                tp2_atr_multiplier=None,
            )
            return XauUsdRiskProfile(
                name="XAUUSD_REFERENCE_CANDIDATE_000",
                target_instrument="XAUUSD",
                calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
                long_risk_policy=ref_lr,
                short_risk_policy=ref_sr,
                long_execution_policy=exec_pol,
                short_execution_policy=exec_pol,
                is_production_authorized=False,
                risk_version="5.0.0-xauusd-v1",
            )

        # Candidates 1..99: Deterministic empirical-rank mapping from TRAIN geometry
        cand_seed = (self.master_seed + candidate_index * 0x9E3779B97F4A7C15 + attempt * 0x517CC1B727220A95) & 0xFFFFFFFFFFFFFFFF

        # Independent RNG streams for LONG and SHORT
        rng_long = random.Random(cand_seed ^ 0x4C4F4E475F524953)  # HASH("LONG_RIS")
        rng_short = random.Random(cand_seed ^ 0x53484F52545F5249) # HASH("SHORT_R")

        geo = self.geom_support
        sb_geo = geo["structure_buffer"]
        atr_geo = geo["atr_multiplier"]
        max_stop_geo = geo["max_stop_distance_atr"]
        rr_geo = geo["min_rr_tp1"]

        # Sample LONG side
        l_sb = _interpolate_quantile(rng_long.random(), sb_geo["long_support"])
        l_atr = _interpolate_quantile(rng_long.random(), atr_geo["long_support"])
        l_max_stop = _interpolate_quantile(rng_long.random(), max_stop_geo["long_support"])
        l_rr = _interpolate_quantile(rng_long.random(), rr_geo["long_support"])

        # Sample SHORT side (independent)
        s_sb = _interpolate_quantile(rng_short.random(), sb_geo["short_support"])
        s_atr = _interpolate_quantile(rng_short.random(), atr_geo["short_support"])
        s_max_stop = _interpolate_quantile(rng_short.random(), max_stop_geo["short_support"])
        s_rr = _interpolate_quantile(rng_short.random(), rr_geo["short_support"])

        long_policy = SideRiskPolicy(
            structure_buffer=l_sb,
            atr_multiplier=l_atr,
            max_stop_distance_atr=l_max_stop,
            min_rr_tp1=l_rr,
            tp2_atr_multiplier=None,
        )
        short_policy = SideRiskPolicy(
            structure_buffer=s_sb,
            atr_multiplier=s_atr,
            max_stop_distance_atr=s_max_stop,
            min_rr_tp1=s_rr,
            tp2_atr_multiplier=None,
        )

        return XauUsdRiskProfile(
            name=f"XAUUSD_RISK_CANDIDATE_{candidate_index:03d}",
            target_instrument="XAUUSD",
            calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_risk_policy=long_policy,
            short_risk_policy=short_policy,
            long_execution_policy=exec_pol,
            short_execution_policy=exec_pol,
            is_production_authorized=False,
            risk_version="5.0.0-xauusd-v1",
        )

    def generate_all_candidates(self, count: Optional[int] = None) -> List[XauUsdRiskProfile]:
        """
        Generate exactly `count` unique candidates (default up to policy.candidate_cap).
        Enforces no duplicate fingerprints and strict validation.
        """
        target_count = count if count is not None else self.policy.candidate_cap
        if target_count > self.policy.candidate_cap:
            raise ValueError(
                f"Requested candidate count ({target_count}) exceeds governed cap ({self.policy.candidate_cap})"
            )
        if target_count <= 0:
            raise ValueError("Candidate count must be strictly positive.")

        candidates: List[XauUsdRiskProfile] = []
        seen_fingerprints = set()

        for idx in range(target_count):
            attempt = 0
            while True:
                profile = self.generate_candidate(idx, attempt)
                if not (profile.long_risk_policy.is_configured and profile.short_risk_policy.is_configured):
                    raise ValueError(f"Generated risk candidate {idx} failed is_configured validation.")
                if not (profile.long_execution_policy.is_configured_for(EntryExecutionPolicy.NEXT_BAR_OPEN) and
                        profile.short_execution_policy.is_configured_for(EntryExecutionPolicy.NEXT_BAR_OPEN)):
                    raise ValueError(f"Generated risk candidate {idx} failed execution policy validation.")
                fp = compute_phase5_policy_fingerprint(profile)
                if fp not in seen_fingerprints:
                    seen_fingerprints.add(fp)
                    candidates.append(profile)
                    break
                attempt += 1
                if attempt > 50:
                    raise RuntimeError(f"Excessive fingerprint collision at risk candidate index {idx}")

        return candidates


@dataclass(frozen=True)
class XauUsdJointCandidate:
    """
    Immutable pair of SignalCandidate[i] and RiskCandidate[i].
    Enforces total joint search budget <= 100 (no Cartesian product).
    """
    index: int
    signal_profile: Phase4SignalProfile
    risk_profile: XauUsdRiskProfile
    is_reference: bool


class XauUsdJointCandidateGenerator:
    """
    Authoritative joint candidate generator creating at most 100 paired profiles:
    Candidate[i] = (SignalCandidate[i], RiskCandidate[i]).
    """

    def __init__(
        self,
        signal_generator: Optional[XauUsdCandidateGenerator] = None,
        risk_generator: Optional[XauUsdRiskCandidateGenerator] = None,
    ):
        if signal_generator is None:
            sig_pol = load_governed_candidate_generation_policy()
            signal_generator = XauUsdCandidateGenerator(sig_pol)
        if risk_generator is None:
            risk_pol = load_governed_risk_candidate_generation_policy()
            risk_generator = XauUsdRiskCandidateGenerator(risk_pol)

        self.signal_generator = signal_generator
        self.risk_generator = risk_generator
        self.max_joint_candidates = min(
            self.signal_generator.policy.candidate_cap,
            self.risk_generator.policy.candidate_cap,
            100,
        )

    def generate_joint_candidate(self, index: int) -> XauUsdJointCandidate:
        """Generate paired (SignalCandidate[i], RiskCandidate[i])."""
        if index < 0 or index >= self.max_joint_candidates:
            raise ValueError(f"Joint candidate index must be in [0, {self.max_joint_candidates - 1}], got {index}")

        sig = self.signal_generator.generate_candidate(index)
        risk = self.risk_generator.generate_candidate(index)

        return XauUsdJointCandidate(
            index=index,
            signal_profile=sig,
            risk_profile=risk,
            is_reference=(index == 0),
        )

    def generate_all_joint_candidates(self, count: Optional[int] = None) -> List[XauUsdJointCandidate]:
        """
        Generate all joint candidates up to count (default 100).
        Total count strictly <= 100.
        """
        target_count = count if count is not None else self.max_joint_candidates
        if target_count > self.max_joint_candidates:
            raise ValueError(
                f"Requested joint count ({target_count}) exceeds governed maximum ({self.max_joint_candidates})"
            )
        if target_count <= 0:
            raise ValueError("Joint candidate count must be strictly positive.")

        joint_candidates = []
        for idx in range(target_count):
            joint_candidates.append(self.generate_joint_candidate(idx))

        return joint_candidates


def load_governed_risk_candidate_generation_policy(
    policy_path: Optional[Path] = None,
) -> XauUsdRiskCandidateGenerationPolicy:
    """Load and strictly validate authoritative risk candidate generation policy from disk."""
    path = policy_path or DEFAULT_RISK_CANDIDATE_POLICY_PATH
    if not path.exists():
        raise FileNotFoundError(f"Risk candidate generation policy artifact not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return XauUsdRiskCandidateGenerationPolicy.from_dict(data)
