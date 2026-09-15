"""
Phase 5 XAUUSD Risk Policy Architecture, Profile & Calibration Governance.

Provides explicit, immutable profile configurations and calibration governance
for XAUUSD side-aware risk intelligence. Ensures strict segregation between
historical frozen XAUT reference numbers and target XAUUSD configuration,
preventing uncalibrated or candidate instruments from silently inheriting legacy numerical fallbacks.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import math
from typing import Optional

from engine.core.types import (
    EntryExecutionPolicy,
    Phase5CalibrationStatus,
)


@dataclass(frozen=True)
class SideRiskPolicy:
    """
    Independent risk policy for one side (LONG or SHORT).
    All empirical numerical fields default to None (NOT_CONFIGURED).
    """
    structure_buffer: Optional[Decimal] = None
    atr_multiplier: Optional[Decimal] = None
    max_stop_distance_atr: Optional[Decimal] = None
    min_rr_tp1: Optional[Decimal] = None
    tp2_atr_multiplier: Optional[Decimal] = None      # OPTIONAL: None does NOT invalidate TP1

    @property
    def is_configured(self) -> bool:
        """Core risk completeness check."""
        core = [self.structure_buffer, self.atr_multiplier,
                self.max_stop_distance_atr, self.min_rr_tp1]
        if any(v is None for v in core):
            return False
        for v in core:
            if not v.is_finite() or v <= Decimal("0"):
                return False
        if self.tp2_atr_multiplier is not None:
            if not self.tp2_atr_multiplier.is_finite() or self.tp2_atr_multiplier <= Decimal("0"):
                return False
        return True


@dataclass(frozen=True)
class XauUsdExecutionPolicy:
    """
    Policy-specific execution parameters.
    Supports both native broker points (Phase 6 empirical evidence)
    and legacy synthetic percentage-based parameters.
    """
    latency_seconds: Optional[float] = None
    synthetic_spread_pct: Optional[Decimal] = None     # Optional percent-based spread
    slippage_pct: Optional[Decimal] = None             # Optional percent-based modeled execution gap
    synthetic_spread_points: Optional[Decimal] = None  # Explicit absolute broker spread in points (e.g. 260.0)
    point_size: Optional[Decimal] = Decimal("0.001")   # Governed contract point size (0.001 for XAUUSD 3-digit)
    modeled_execution_gap_points: Optional[Decimal] = None # Explicit modeled execution gap in points (e.g. 0.0)

    def is_configured_for(self, policy: EntryExecutionPolicy) -> bool:
        """Policy-specific completeness check with strict fail-closed fallthrough."""
        if self.latency_seconds is None or self.latency_seconds < 0.0 or not math.isfinite(self.latency_seconds):
            return False

        has_slip_pct = self.slippage_pct is not None and self.slippage_pct.is_finite() and self.slippage_pct >= Decimal("0")
        has_gap_pts = (
            self.modeled_execution_gap_points is not None
            and self.modeled_execution_gap_points.is_finite()
            and self.modeled_execution_gap_points >= Decimal("0")
        )
        if not (has_slip_pct or has_gap_pts):
            return False

        if policy == EntryExecutionPolicy.MARKET_AFTER_SIGNAL:
            return True
        elif policy == EntryExecutionPolicy.NEXT_BAR_OPEN:
            has_spread_pct = (
                self.synthetic_spread_pct is not None
                and self.synthetic_spread_pct.is_finite()
                and self.synthetic_spread_pct >= Decimal("0")
            )
            has_spread_pts = (
                self.synthetic_spread_points is not None
                and self.synthetic_spread_points.is_finite()
                and self.synthetic_spread_points >= Decimal("0")
            )
            return has_spread_pct or has_spread_pts
        elif policy == EntryExecutionPolicy.LIMIT_ZONE:
            return True
        else:
            return False  # Fail closed for any unknown policy


@dataclass(frozen=True)
class XauUsdRiskProfile:
    """
    Immutable master risk profile for XAUUSD Phase 5.
    Production authority is strictly blocked pending Phase 6.
    """
    name: str = "XAUUSD_UNCALIBRATED"
    target_instrument: str = "XAUUSD"
    calibration_status: Phase5CalibrationStatus = Phase5CalibrationStatus.PENDING_PHASE6
    long_risk_policy: SideRiskPolicy = field(default_factory=SideRiskPolicy)
    short_risk_policy: SideRiskPolicy = field(default_factory=SideRiskPolicy)
    long_execution_policy: XauUsdExecutionPolicy = field(default_factory=XauUsdExecutionPolicy)
    short_execution_policy: XauUsdExecutionPolicy = field(default_factory=XauUsdExecutionPolicy)
    is_production_authorized: bool = False
    risk_version: str = "5.0.0-xauusd-v1"

    def __post_init__(self):
        if self.target_instrument != "XAUUSD":
            raise ValueError(f"XauUsdRiskProfile target must be 'XAUUSD', got '{self.target_instrument}'")
        if self.is_production_authorized:
            raise ValueError("XAUUSD Phase 5 production authority is blocked pending Phase 6.")


def uncalibrated_xauusd_risk_profile() -> XauUsdRiskProfile:
    """Factory returning uncalibrated XAUUSD profile with all None numerics."""
    return XauUsdRiskProfile()
