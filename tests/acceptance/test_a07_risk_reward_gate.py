"""
Acceptance Test A07: Reward-to-Risk (RR) Gate & Phase 4 Immutability Preservation.
Verifies that a setup with RR < 1.80 is rejected by the Risk Engine (effective action WAIT),
while the historical Phase 4 SignalRecord strictly remains BUY_WINDOW (BUY) and is unmutated.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import (
    DirectionScoreResult,
    HardGateEvaluation,
    StructureZone,
    SignalSnapshot,
    SignalState,
    StructureResult,
    StructureType,
    BosType,
    TimingScoreResult,
    UserDecision,
)
from engine.risk.planner import RiskPlanner


@pytest.mark.acceptance
@pytest.mark.django_db
def test_a07_risk_reward_gate():
    xaut = Asset.objects.create(code="XAUT_A07", name="Tether Gold A07")
    usdt = Asset.objects.create(code="USDT_A07", name="Tether USD A07")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    eval_ts = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)

    # 1. Construct and Persist a valid Phase 4 BUY_WINDOW Signal
    phase4_snapshot = SignalSnapshot(
        timestamp=eval_ts,
        instrument=inst.symbol,
        timeframe="15m",
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        direction=DirectionScoreResult(85.0, 100.0, (), True),
        timing=TimingScoreResult(85.0, 100.0, (), True),
        hard_gate=HardGateEvaluation(False, None, ()),
        reasons_positive=("Strong bull trend",),
        reasons_negative=(),
        hard_gate_reasons=(),
        analysis_fingerprint="a07_signal_fingerprint_hash_12345",
        code_revision="eae30005",
    )

    record, created = SignalPersistenceService.save_signal_snapshot(inst, phase4_snapshot)
    assert created is True
    assert record.state == SignalState.BUY_WINDOW
    assert record.user_decision == UserDecision.BUY

    # 2. Case A: Nearest confirmed resistance yields RR = 1.20 < 1.80 minimum threshold
    # Support: 2500 - 2505 (entry_max = 2505, stop_struct = 2499, stop_atr = 2492.50 -> stop_final = 2492.50, risk = 12.50)
    # Resistance at 2520 -> TP1 = 2520 -> Reward = 15 -> RR = 15 / 12.50 = 1.20
    support_zone = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), eval_ts, 3, True)
    nearby_resistance = StructureZone("RESISTANCE", Decimal("2520.00"), Decimal("2525.00"), eval_ts, 2, True)

    struct_15m_tight = StructureResult(
        timestamp=eval_ts,
        structure_type=StructureType.HH,
        bos=BosType.BULLISH,
        last_swing_high=None,
        last_swing_low=None,
        swings=(),
        zones=(support_zone, nearby_resistance),
    )

    planner = RiskPlanner(code_revision="eae30005", min_rr=Decimal("1.80"))
    risk_plan_rejected = planner.plan(
        signal_snapshot=phase4_snapshot,
        structure_15m=struct_15m_tight,
        atr14=5.0,
    )

    # Risk Engine rejects trade setup
    assert risk_plan_rejected.is_valid_risk_plan is False
    assert risk_plan_rejected.execution_eligible is False
    assert risk_plan_rejected.effective_action == UserDecision.WAIT
    assert risk_plan_rejected.rr_tp1 < Decimal("1.80")
    assert any("< 1.80" in r or "minimum required" in r for r in risk_plan_rejected.reasons)

    # CRITICAL INVARIANT: Phase 4 SignalRecord is 100% untouched and remains BUY_WINDOW (BUY)
    record.refresh_from_db()
    assert record.state == SignalState.BUY_WINDOW
    assert record.user_decision == UserDecision.BUY

    # 3. Case B: Ample structural room -> RR = 2.40 >= 1.80
    # Resistance at 2535 -> TP1 = 2535 -> Reward = 30 -> RR = 30 / 12.50 = 2.40
    farther_resistance = StructureZone("RESISTANCE", Decimal("2535.00"), Decimal("2540.00"), eval_ts, 2, True)
    struct_15m_ample = StructureResult(
        timestamp=eval_ts,
        structure_type=StructureType.HH,
        bos=BosType.BULLISH,
        last_swing_high=None,
        last_swing_low=None,
        swings=(),
        zones=(support_zone, farther_resistance),
    )

    risk_plan_valid = planner.plan(
        signal_snapshot=phase4_snapshot,
        structure_15m=struct_15m_ample,
        atr14=5.0,
    )

    assert risk_plan_valid.is_valid_risk_plan is True
    assert risk_plan_valid.execution_eligible is True
    assert risk_plan_valid.effective_action == UserDecision.BUY
    assert risk_plan_valid.rr_tp1 >= Decimal("1.80")
