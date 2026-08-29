"""Master RiskPlanner evaluating entry, stops, targets, and RR gates (Phase 5)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from engine.core.types import (
    StructureZone,
    RiskPlanSnapshot,
    SignalSnapshot,
    SignalState,
    StructureResult,
    UserDecision,
)
from engine.risk.stops import calculate_stops
from engine.risk.targets import calculate_targets


class RiskPlanner:
    """
    Pure Python risk planning engine strictly decoupled from Phase 4 signals.

    Strict Invariants:
      1. Never modifies Phase 4 SignalSnapshot or SignalRecord (Immutability).
      2. P5-25: Source Signal Eligibility Gate - requires signal_snapshot.state == BUY_WINDOW
         AND signal_snapshot.user_decision == BUY. All other states (READY, WATCH, AVOID, NO_TRADE, WAIT)
         are strictly rejected (is_valid_risk_plan = False, execution_eligible = False).
      3. If RR < 1.80 -> returns is_valid_risk_plan = False, execution_eligible = False, effective_action = WAIT.
      4. Zero Django, ORM, Celery, or Redis imports (pure domain logic).
    """

    def __init__(
        self,
        code_revision: str,
        risk_version: str = "5.0.0",
        execution_model_version: str = "5.0.0-exec-v1",
        config_version: str = "cfg-2026-v1",
        min_rr: Decimal = Decimal("1.80"),
        structure_buffer: Decimal = Decimal("1.0"),
        atr_multiplier: Decimal = Decimal("2.0"),
        max_stop_distance_atr: Decimal = Decimal("4.0"),
    ):
        if not code_revision:
            raise ValueError("code_revision is required for risk plan provenance.")
        self.code_revision = code_revision
        self.risk_version = risk_version
        self.execution_model_version = execution_model_version
        self.config_version = config_version
        self.min_rr = min_rr
        self.structure_buffer = structure_buffer
        self.atr_multiplier = atr_multiplier
        self.max_stop_distance_atr = max_stop_distance_atr

    def plan(
        self,
        signal_snapshot: SignalSnapshot,
        structure_15m: Optional[StructureResult],
        atr14: float,
        structure_4h: Optional[StructureResult] = None,
        custom_support_zone: Optional[StructureZone] = None,
        latest_close: Optional[Decimal] = None,
    ) -> RiskPlanSnapshot:
        """
        Evaluate and create an immutable RiskPlanSnapshot for a BUY_WINDOW signal.
        """
        signal_fp = signal_snapshot.analysis_fingerprint
        signal_ts = signal_snapshot.timestamp

        # 1. P5-25: Source Signal Eligibility Gate
        # Only BUY_WINDOW state with UserDecision.BUY is eligible for Risk Planning.
        if signal_snapshot.state != SignalState.BUY_WINDOW or signal_snapshot.user_decision != UserDecision.BUY:
            eff_action = UserDecision.AVOID if signal_snapshot.user_decision == UserDecision.AVOID else UserDecision.WAIT
            return RiskPlanSnapshot(
                source_signal_fingerprint=signal_fp,
                signal_generated_at=signal_ts,
                entry_min=Decimal("0"),
                entry_mid=Decimal("0"),
                entry_max=Decimal("0"),
                stop_structure=Decimal("0"),
                stop_atr=Decimal("0"),
                stop_final=Decimal("0"),
                stop_distance_atr=Decimal("0"),
                tp1=Decimal("0"),
                tp2=Decimal("0"),
                rr_tp1=Decimal("0"),
                rr_tp2=Decimal("0"),
                is_valid_risk_plan=False,
                execution_eligible=False,
                effective_action=eff_action,
                reasons=(
                    f"Source signal ({signal_snapshot.state.value} / {signal_snapshot.user_decision.value}) "
                    "is not eligible for risk planning (requires BUY_WINDOW / BUY).",
                ),
                source_zone_id=None,
                source_zone_timestamp=None,
                risk_version=self.risk_version,
                execution_model_version=self.execution_model_version,
                config_version=self.config_version,
                code_revision=self.code_revision,
            )

        # 2. Active Support Invalidation Zone Resolution
        support_zone = custom_support_zone
        if support_zone is None and structure_15m is not None:
            support_candidates = [
                z for z in structure_15m.zones
                if z.zone_type == "SUPPORT" and z.is_active
            ]
            if support_candidates:
                # Sort by price_high descending to get nearest support below/at price
                support_candidates.sort(key=lambda z: z.price_high, reverse=True)
                support_zone = support_candidates[0]

        if support_zone is None:
            return RiskPlanSnapshot(
                source_signal_fingerprint=signal_fp,
                signal_generated_at=signal_ts,
                entry_min=Decimal("0"),
                entry_mid=Decimal("0"),
                entry_max=Decimal("0"),
                stop_structure=Decimal("0"),
                stop_atr=Decimal("0"),
                stop_final=Decimal("0"),
                stop_distance_atr=Decimal("0"),
                tp1=Decimal("0"),
                tp2=Decimal("0"),
                rr_tp1=Decimal("0"),
                rr_tp2=Decimal("0"),
                is_valid_risk_plan=False,
                execution_eligible=False,
                effective_action=UserDecision.WAIT,
                reasons=("Missing confirmed active support zone for risk boundary.",),
                source_zone_id=None,
                source_zone_timestamp=None,
                risk_version=self.risk_version,
                execution_model_version=self.execution_model_version,
                config_version=self.config_version,
                code_revision=self.code_revision,
            )

        # Source zone provenance identity and timestamp
        source_zone_id = f"SZ_{support_zone.zone_type}_{support_zone.price_low}_{support_zone.price_high}_{int(support_zone.created_at.timestamp())}"
        source_zone_timestamp = support_zone.created_at

        # 3. Entry Zone Planning (P5-32)
        # Entry coordinates are derived strictly from the point-in-time support zone.
        # latest_close is market context only and does not alter the entry boundaries.
        entry_min = support_zone.price_low.quantize(Decimal("0.01"))
        entry_max = support_zone.price_high.quantize(Decimal("0.01"))
        entry_mid = ((entry_min + entry_max) / Decimal("2")).quantize(Decimal("0.01"))

        # 4. Stop Loss & ATR Guard Calculation
        stop_struct, stop_atr, stop_final, stop_dist_atr, stops_ok, stops_err = calculate_stops(
            support_zone=support_zone,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            atr14=atr14,
            structure_buffer=self.structure_buffer,
            atr_multiplier=self.atr_multiplier,
            max_stop_distance_atr=self.max_stop_distance_atr,
        )

        if not stops_ok:
            return RiskPlanSnapshot(
                source_signal_fingerprint=signal_fp,
                signal_generated_at=signal_ts,
                entry_min=entry_min,
                entry_mid=entry_mid,
                entry_max=entry_max,
                stop_structure=stop_struct,
                stop_atr=stop_atr,
                stop_final=stop_final,
                stop_distance_atr=stop_dist_atr,
                tp1=Decimal("0"),
                tp2=Decimal("0"),
                rr_tp1=Decimal("0"),
                rr_tp2=Decimal("0"),
                is_valid_risk_plan=False,
                execution_eligible=False,
                effective_action=UserDecision.WAIT,
                reasons=(stops_err or "Invalid stop loss architecture.",),
                source_zone_id=source_zone_id,
                source_zone_timestamp=source_zone_timestamp,
                risk_version=self.risk_version,
                execution_model_version=self.execution_model_version,
                config_version=self.config_version,
                code_revision=self.code_revision,
            )

        # 5. Take-Profit Targets & RR Validation (A07)
        tp1, tp2, rr_tp1, rr_tp2, targets_ok, targets_err = calculate_targets(
            entry_max=entry_max,
            entry_mid=entry_mid,
            stop_final=stop_final,
            structure_15m=structure_15m,
            atr14=atr14,
            structure_4h=structure_4h,
            min_rr_tp1=self.min_rr,
        )

        if not targets_ok:
            return RiskPlanSnapshot(
                source_signal_fingerprint=signal_fp,
                signal_generated_at=signal_ts,
                entry_min=entry_min,
                entry_mid=entry_mid,
                entry_max=entry_max,
                stop_structure=stop_struct,
                stop_atr=stop_atr,
                stop_final=stop_final,
                stop_distance_atr=stop_dist_atr,
                tp1=tp1,
                tp2=tp2,
                rr_tp1=rr_tp1,
                rr_tp2=rr_tp2,
                is_valid_risk_plan=False,
                execution_eligible=False,
                effective_action=UserDecision.WAIT,
                reasons=(targets_err or f"Reward-to-Risk RR {rr_tp1:.2f} < {self.min_rr:.2f} threshold.",),
                source_zone_id=source_zone_id,
                source_zone_timestamp=source_zone_timestamp,
                risk_version=self.risk_version,
                execution_model_version=self.execution_model_version,
                config_version=self.config_version,
                code_revision=self.code_revision,
            )

        # 6. Valid Risk Architecture Output
        return RiskPlanSnapshot(
            source_signal_fingerprint=signal_fp,
            signal_generated_at=signal_ts,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=stop_struct,
            stop_atr=stop_atr,
            stop_final=stop_final,
            stop_distance_atr=stop_dist_atr,
            tp1=tp1,
            tp2=tp2,
            rr_tp1=rr_tp1,
            rr_tp2=rr_tp2,
            is_valid_risk_plan=True,
            execution_eligible=True,
            effective_action=UserDecision.BUY,
            reasons=(f"Valid Risk Plan confirmed. RR {rr_tp1:.2f} >= {self.min_rr:.2f}.",),
            source_zone_id=source_zone_id,
            source_zone_timestamp=source_zone_timestamp,
            risk_version=self.risk_version,
            execution_model_version=self.execution_model_version,
            config_version=self.config_version,
            code_revision=self.code_revision,
        )