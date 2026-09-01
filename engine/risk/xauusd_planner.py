"""
Master XauUsdRiskPlanner evaluating entry, stops, targets, and RR gates for XAUUSD (Phase 5).
Implements independent LONG and SHORT risk planning strictly decoupled from Phase 4 signals.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence

from engine.core.types import (
    DualSideSignalSnapshot,
    RiskCandidateStatus,
    RiskSide,
    SideRiskPlanSnapshot,
    SignalState,
    StructureResult,
    StructureZone,
    UserDecision,
)
from engine.risk.xauusd_fingerprints import (
    canonical_utc_timestamp,
    compute_phase5_policy_fingerprint,
    compute_risk_plan_fingerprint,
    compute_zone_fingerprint,
)
from engine.risk.xauusd_policy import (
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)
from engine.risk.xauusd_stops import (
    calculate_long_stops,
    calculate_short_stops,
)
from engine.risk.xauusd_targets import (
    calculate_long_targets,
    calculate_short_targets,
)


class XauUsdRiskPlanner:
    """
    Pure Python risk planning engine for XAUUSD strictly decoupled from Phase 4 signals.

    Strict Invariants:
      1. Never modifies Phase 4 DualSideSignalSnapshot (Immutability).
      2. Authoritative evaluation timestamp T is strictly phase4_snapshot.timestamp.
      3. LONG planning requires candidate_state == BUY_WINDOW AND candidate_user_decision == BUY.
      4. SHORT planning requires candidate_state == SELL_WINDOW AND candidate_user_decision == SELL.
      5. Phase 5 may DEMOTE (candidate BUY/SELL -> effective action WAIT on invalid risk)
         but must NEVER PROMOTE (candidate WAIT -> BUY/SELL is forbidden).
      6. Published Layer B action is ALWAYS WAIT pending Phase 6 governance.
      7. Decimal ATR throughout Phase 5 calculations.
      8. Zero Django, ORM, Celery, Redis, network, subprocess, or hidden clock.
    """

    def __init__(
        self,
        code_revision: str,
        risk_profile: Optional[XauUsdRiskProfile] = None,
        risk_version: str = "5.0.0-xauusd-v1",
    ):
        if not code_revision or not isinstance(code_revision, str) or not code_revision.strip():
            raise ValueError("code_revision is required for risk plan provenance.")
        self.code_revision = code_revision.strip()
        self.risk_profile = risk_profile if risk_profile is not None else uncalibrated_xauusd_risk_profile()
        self.risk_version = risk_version
        self.policy_fingerprint = compute_phase5_policy_fingerprint(self.risk_profile)

    def plan_long(
        self,
        phase4_snapshot: DualSideSignalSnapshot,
        structure_15m: Optional[StructureResult],
        atr14: Decimal,
        structure_4h: Optional[StructureResult] = None,
    ) -> SideRiskPlanSnapshot:
        """
        Evaluate and create an immutable SideRiskPlanSnapshot for a LONG setup.
        """
        # 1. Canonical Instrument & Timestamp Validation
        if phase4_snapshot.instrument != "XAUUSD":
            raise ValueError(f"XauUsdRiskPlanner requires XAUUSD snapshot, got '{phase4_snapshot.instrument}'")

        authoritative_t = phase4_snapshot.timestamp
        if authoritative_t.tzinfo is None or authoritative_t.tzinfo.utcoffset(authoritative_t) is None:
            raise ValueError("phase4_snapshot.timestamp must be timezone aware with non-None utcoffset.")

        sig_fp = phase4_snapshot.analysis_fingerprint

        # 2. Source Candidate Eligibility Gate
        if (
            phase4_snapshot.candidate_state != SignalState.BUY_WINDOW
            or phase4_snapshot.candidate_user_decision != UserDecision.BUY
        ):
            return self._build_invalid_snapshot(
                side=RiskSide.LONG,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14 if isinstance(atr14, Decimal) and atr14.is_finite() else Decimal("0"),
                reasons=(
                    f"Source candidate ({phase4_snapshot.candidate_state.value} / "
                    f"{phase4_snapshot.candidate_user_decision.value}) is not eligible for LONG risk planning "
                    "(requires candidate BUY_WINDOW / BUY).",
                ),
            )

        # 3. Policy Completeness Gate
        policy = self.risk_profile.long_risk_policy
        if not policy.is_configured:
            return self._build_invalid_snapshot(
                side=RiskSide.LONG,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14 if isinstance(atr14, Decimal) and atr14.is_finite() else Decimal("0"),
                reasons=("LONG risk policy is not configured (uncalibrated profile).",),
            )

        # 4. Active Support Invalidation Zone Resolution (PIT-validated strictly from StructureResult)
        support_zone: Optional[StructureZone] = None
        if (
            structure_15m is not None
            and structure_15m.timestamp.tzinfo is not None
            and structure_15m.timestamp.tzinfo.utcoffset(structure_15m.timestamp) is not None
            and structure_15m.timestamp <= authoritative_t
        ):
            candidates = [
                z for z in structure_15m.zones
                if (
                    z.zone_type == "SUPPORT"
                    and z.is_active
                    and z.created_at.tzinfo is not None
                    and z.created_at.tzinfo.utcoffset(z.created_at) is not None
                    and z.created_at <= authoritative_t
                )
            ]
            if candidates:
                # Total deterministic sorting: highest price_high, created_at ASC, price_low ASC, zone_fp ASC
                candidates.sort(key=lambda z: (
                    -z.price_high,
                    canonical_utc_timestamp(z.created_at),
                    z.price_low,
                    compute_zone_fingerprint(z),
                ))
                support_zone = candidates[0]

        if support_zone is None:
            return self._build_invalid_snapshot(
                side=RiskSide.LONG,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14,
                reasons=("Missing confirmed active support zone for risk boundary.",),
            )

        entry_zone_fp = compute_zone_fingerprint(support_zone)

        # 5. Entry Zone Planning
        entry_min = support_zone.price_low
        entry_max = support_zone.price_high
        entry_mid = (entry_min + entry_max) / Decimal("2")

        # 6. Stop Loss & ATR Guard Calculation
        stop_struct, stop_atr, stop_final, stop_dist_atr, stops_ok, stops_err = calculate_long_stops(
            support_zone=support_zone,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            atr14=atr14,
            policy=policy,
        )

        if not stops_ok:
            return self._build_invalid_snapshot(
                side=RiskSide.LONG,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14,
                entry_min=entry_min,
                entry_mid=entry_mid,
                entry_max=entry_max,
                stop_structure=stop_struct,
                stop_atr=stop_atr,
                stop_final=stop_final,
                stop_distance_atr=stop_dist_atr,
                entry_zone_fingerprint=entry_zone_fp,
                reasons=(stops_err or "Invalid stop loss architecture.",),
            )

        # 7. Take-Profit Targets & RR Validation
        tp1, tp2, rr_tp1, rr_tp2, tp1_fp, tp2_fp, targets_ok, targets_err = calculate_long_targets(
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_final=stop_final,
            structure_15m=structure_15m,
            atr14=atr14,
            authoritative_t=authoritative_t,
            policy=policy,
            structure_4h=structure_4h,
        )

        if not targets_ok:
            return self._build_invalid_snapshot(
                side=RiskSide.LONG,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14,
                entry_min=entry_min,
                entry_mid=entry_mid,
                entry_max=entry_max,
                stop_structure=stop_struct,
                stop_atr=stop_atr,
                stop_final=stop_final,
                stop_distance_atr=stop_dist_atr,
                tp1=tp1,
                tp2=tp2,
                planned_rr_tp1=rr_tp1,
                planned_rr_tp2=rr_tp2,
                entry_zone_fingerprint=entry_zone_fp,
                tp1_zone_fingerprint=tp1_fp,
                tp2_zone_fingerprint=tp2_fp,
                reasons=(targets_err or "Invalid reward-to-risk architecture.",),
            )

        # 8. Valid LONG Risk Plan Snapshot
        risk_plan_fp = compute_risk_plan_fingerprint(
            source_phase4_fingerprint=sig_fp,
            source_candidate_state=phase4_snapshot.candidate_state,
            source_candidate_decision=phase4_snapshot.candidate_user_decision,
            side=RiskSide.LONG,
            authoritative_timestamp=authoritative_t,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_struct,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_dist_atr,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=rr_tp1,
            planned_rr_tp2=rr_tp2,
            entry_zone_fingerprint=entry_zone_fp,
            tp1_zone_fingerprint=tp1_fp,
            tp2_zone_fingerprint=tp2_fp,
            atr_value=atr14,
            phase5_policy_fingerprint=self.policy_fingerprint,
            risk_version=self.risk_version,
            code_revision=self.code_revision,
        )

        return SideRiskPlanSnapshot(
            side=RiskSide.LONG,
            source_phase4_fingerprint=sig_fp,
            source_candidate_state=phase4_snapshot.candidate_state,
            source_candidate_decision=phase4_snapshot.candidate_user_decision,
            signal_generated_at=authoritative_t,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_struct,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_dist_atr,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=rr_tp1,
            planned_rr_tp2=rr_tp2,
            risk_candidate_valid=True,
            risk_candidate_status=RiskCandidateStatus.VALID_LONG_RISK_CANDIDATE,
            simulation_eligible=True,
            candidate_effective_action=UserDecision.BUY,
            publication_effective_action=UserDecision.WAIT,
            reasons=(
                f"Valid LONG risk plan established (Stop: {stop_final}, TP1: {tp1}, Planned RR: {rr_tp1}).",
            ),
            entry_zone_fingerprint=entry_zone_fp,
            tp1_zone_fingerprint=tp1_fp,
            tp2_zone_fingerprint=tp2_fp,
            phase5_policy_fingerprint=self.policy_fingerprint,
            risk_plan_fingerprint=risk_plan_fp,
            risk_version=self.risk_version,
            code_revision=self.code_revision,
        )

    def plan_short(
        self,
        phase4_snapshot: DualSideSignalSnapshot,
        structure_15m: Optional[StructureResult],
        atr14: Decimal,
        structure_4h: Optional[StructureResult] = None,
    ) -> SideRiskPlanSnapshot:
        """
        Evaluate and create an immutable SideRiskPlanSnapshot for a SHORT setup.
        """
        # 1. Canonical Instrument & Timestamp Validation
        if phase4_snapshot.instrument != "XAUUSD":
            raise ValueError(f"XauUsdRiskPlanner requires XAUUSD snapshot, got '{phase4_snapshot.instrument}'")

        authoritative_t = phase4_snapshot.timestamp
        if authoritative_t.tzinfo is None or authoritative_t.tzinfo.utcoffset(authoritative_t) is None:
            raise ValueError("phase4_snapshot.timestamp must be timezone aware with non-None utcoffset.")

        sig_fp = phase4_snapshot.analysis_fingerprint

        # 2. Source Candidate Eligibility Gate
        if (
            phase4_snapshot.candidate_state != SignalState.SELL_WINDOW
            or phase4_snapshot.candidate_user_decision != UserDecision.SELL
        ):
            return self._build_invalid_snapshot(
                side=RiskSide.SHORT,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14 if isinstance(atr14, Decimal) and atr14.is_finite() else Decimal("0"),
                reasons=(
                    f"Source candidate ({phase4_snapshot.candidate_state.value} / "
                    f"{phase4_snapshot.candidate_user_decision.value}) is not eligible for SHORT risk planning "
                    "(requires candidate SELL_WINDOW / SELL).",
                ),
            )

        # 3. Policy Completeness Gate
        policy = self.risk_profile.short_risk_policy
        if not policy.is_configured:
            return self._build_invalid_snapshot(
                side=RiskSide.SHORT,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14 if isinstance(atr14, Decimal) and atr14.is_finite() else Decimal("0"),
                reasons=("SHORT risk policy is not configured (uncalibrated profile).",),
            )

        # 4. Active Resistance Invalidation Zone Resolution (PIT-validated strictly from StructureResult)
        resistance_zone: Optional[StructureZone] = None
        if (
            structure_15m is not None
            and structure_15m.timestamp.tzinfo is not None
            and structure_15m.timestamp.tzinfo.utcoffset(structure_15m.timestamp) is not None
            and structure_15m.timestamp <= authoritative_t
        ):
            candidates = [
                z for z in structure_15m.zones
                if (
                    z.zone_type == "RESISTANCE"
                    and z.is_active
                    and z.created_at.tzinfo is not None
                    and z.created_at.tzinfo.utcoffset(z.created_at) is not None
                    and z.created_at <= authoritative_t
                )
            ]
            if candidates:
                # Total deterministic sorting: lowest price_low, created_at ASC, price_high DESC, zone_fp ASC
                candidates.sort(key=lambda z: (
                    z.price_low,
                    canonical_utc_timestamp(z.created_at),
                    -z.price_high,
                    compute_zone_fingerprint(z),
                ))
                resistance_zone = candidates[0]

        if resistance_zone is None:
            return self._build_invalid_snapshot(
                side=RiskSide.SHORT,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14,
                reasons=("Missing confirmed active resistance zone for risk boundary.",),
            )

        entry_zone_fp = compute_zone_fingerprint(resistance_zone)

        # 5. Entry Zone Planning
        entry_min = resistance_zone.price_low
        entry_max = resistance_zone.price_high
        entry_mid = (entry_min + entry_max) / Decimal("2")

        # 6. Stop Loss & ATR Guard Calculation
        stop_struct, stop_atr, stop_final, stop_dist_atr, stops_ok, stops_err = calculate_short_stops(
            resistance_zone=resistance_zone,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            atr14=atr14,
            policy=policy,
        )

        if not stops_ok:
            return self._build_invalid_snapshot(
                side=RiskSide.SHORT,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14,
                entry_min=entry_min,
                entry_mid=entry_mid,
                entry_max=entry_max,
                stop_structure=stop_struct,
                stop_atr=stop_atr,
                stop_final=stop_final,
                stop_distance_atr=stop_dist_atr,
                entry_zone_fingerprint=entry_zone_fp,
                reasons=(stops_err or "Invalid stop loss architecture.",),
            )

        # 7. Take-Profit Targets & RR Validation
        tp1, tp2, rr_tp1, rr_tp2, tp1_fp, tp2_fp, targets_ok, targets_err = calculate_short_targets(
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_final=stop_final,
            structure_15m=structure_15m,
            atr14=atr14,
            authoritative_t=authoritative_t,
            policy=policy,
            structure_4h=structure_4h,
        )

        if not targets_ok:
            return self._build_invalid_snapshot(
                side=RiskSide.SHORT,
                source_phase4_fingerprint=sig_fp,
                source_candidate_state=phase4_snapshot.candidate_state,
                source_candidate_decision=phase4_snapshot.candidate_user_decision,
                authoritative_t=authoritative_t,
                atr_value=atr14,
                entry_min=entry_min,
                entry_mid=entry_mid,
                entry_max=entry_max,
                stop_structure=stop_struct,
                stop_atr=stop_atr,
                stop_final=stop_final,
                stop_distance_atr=stop_dist_atr,
                tp1=tp1,
                tp2=tp2,
                planned_rr_tp1=rr_tp1,
                planned_rr_tp2=rr_tp2,
                entry_zone_fingerprint=entry_zone_fp,
                tp1_zone_fingerprint=tp1_fp,
                tp2_zone_fingerprint=tp2_fp,
                reasons=(targets_err or "Invalid reward-to-risk architecture.",),
            )

        # 8. Valid SHORT Risk Plan Snapshot
        risk_plan_fp = compute_risk_plan_fingerprint(
            source_phase4_fingerprint=sig_fp,
            source_candidate_state=phase4_snapshot.candidate_state,
            source_candidate_decision=phase4_snapshot.candidate_user_decision,
            side=RiskSide.SHORT,
            authoritative_timestamp=authoritative_t,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_struct,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_dist_atr,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=rr_tp1,
            planned_rr_tp2=rr_tp2,
            entry_zone_fingerprint=entry_zone_fp,
            tp1_zone_fingerprint=tp1_fp,
            tp2_zone_fingerprint=tp2_fp,
            atr_value=atr14,
            phase5_policy_fingerprint=self.policy_fingerprint,
            risk_version=self.risk_version,
            code_revision=self.code_revision,
        )

        return SideRiskPlanSnapshot(
            side=RiskSide.SHORT,
            source_phase4_fingerprint=sig_fp,
            source_candidate_state=phase4_snapshot.candidate_state,
            source_candidate_decision=phase4_snapshot.candidate_user_decision,
            signal_generated_at=authoritative_t,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_struct,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_dist_atr,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=rr_tp1,
            planned_rr_tp2=rr_tp2,
            risk_candidate_valid=True,
            risk_candidate_status=RiskCandidateStatus.VALID_SHORT_RISK_CANDIDATE,
            simulation_eligible=True,
            candidate_effective_action=UserDecision.SELL,
            publication_effective_action=UserDecision.WAIT,
            reasons=(
                f"Valid SHORT risk plan established (Stop: {stop_final}, TP1: {tp1}, Planned RR: {rr_tp1}).",
            ),
            entry_zone_fingerprint=entry_zone_fp,
            tp1_zone_fingerprint=tp1_fp,
            tp2_zone_fingerprint=tp2_fp,
            phase5_policy_fingerprint=self.policy_fingerprint,
            risk_plan_fingerprint=risk_plan_fp,
            risk_version=self.risk_version,
            code_revision=self.code_revision,
        )

    def _build_invalid_snapshot(
        self,
        side: RiskSide,
        source_phase4_fingerprint: str,
        source_candidate_state: SignalState,
        source_candidate_decision: UserDecision,
        authoritative_t: datetime,
        atr_value: Decimal,
        entry_min: Optional[Decimal] = None,
        entry_mid: Optional[Decimal] = None,
        entry_max: Optional[Decimal] = None,
        stop_structure: Optional[Decimal] = None,
        stop_atr: Optional[Decimal] = None,
        stop_final: Optional[Decimal] = None,
        stop_distance_atr: Optional[Decimal] = None,
        tp1: Optional[Decimal] = None,
        tp2: Optional[Decimal] = None,
        planned_rr_tp1: Optional[Decimal] = None,
        planned_rr_tp2: Optional[Decimal] = None,
        entry_zone_fingerprint: Optional[str] = None,
        tp1_zone_fingerprint: Optional[str] = None,
        tp2_zone_fingerprint: Optional[str] = None,
        reasons: Sequence[str] = (),
    ) -> SideRiskPlanSnapshot:
        """
        Helper constructing a strictly demoted invalid SideRiskPlanSnapshot.
        """
        risk_plan_fp = compute_risk_plan_fingerprint(
            source_phase4_fingerprint=source_phase4_fingerprint,
            source_candidate_state=source_candidate_state,
            source_candidate_decision=source_candidate_decision,
            side=side,
            authoritative_timestamp=authoritative_t,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_structure,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_distance_atr,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=planned_rr_tp1,
            planned_rr_tp2=planned_rr_tp2,
            entry_zone_fingerprint=entry_zone_fingerprint,
            tp1_zone_fingerprint=tp1_zone_fingerprint,
            tp2_zone_fingerprint=tp2_zone_fingerprint,
            atr_value=atr_value,
            phase5_policy_fingerprint=self.policy_fingerprint,
            risk_version=self.risk_version,
            code_revision=self.code_revision,
        )

        return SideRiskPlanSnapshot(
            side=side,
            source_phase4_fingerprint=source_phase4_fingerprint,
            source_candidate_state=source_candidate_state,
            source_candidate_decision=source_candidate_decision,
            signal_generated_at=authoritative_t,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_structure,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_distance_atr,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=planned_rr_tp1,
            planned_rr_tp2=planned_rr_tp2,
            risk_candidate_valid=False,
            risk_candidate_status=RiskCandidateStatus.INVALID_RISK_CANDIDATE,
            simulation_eligible=False,
            candidate_effective_action=UserDecision.WAIT,
            publication_effective_action=UserDecision.WAIT,
            reasons=tuple(reasons) if reasons else ("Invalid risk candidate.",),
            entry_zone_fingerprint=entry_zone_fingerprint,
            tp1_zone_fingerprint=tp1_zone_fingerprint,
            tp2_zone_fingerprint=tp2_zone_fingerprint,
            phase5_policy_fingerprint=self.policy_fingerprint,
            risk_plan_fingerprint=risk_plan_fp,
            risk_version=self.risk_version,
            code_revision=self.code_revision,
        )
