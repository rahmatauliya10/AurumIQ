"""
Unit tests for SignalRecord persistence and dual-side snapshot saving.
Covers Task 8 contracts.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from django.utils import timezone as dj_timezone

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import (
    CandidateGateResult,
    ComponentScore,
    DualSideSignalSnapshot,
    FeedHealthStatus,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalSnapshot,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
)


@pytest.fixture
def test_instrument(db):
    base, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": AssetType.COMMODITY})
    quote, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=base,
        quote_asset=quote,
        instrument_type=InstrumentType.SPOT,
        defaults={"role": InstrumentRole.EXECUTION},
    )
    return inst


@pytest.fixture
def test_xaut_instrument(db):
    base, _ = Asset.objects.get_or_create(code="XAUT", defaults={"name": "Tether Gold", "asset_type": AssetType.CRYPTO_TOKEN})
    quote, _ = Asset.objects.get_or_create(code="USDT", defaults={"name": "Tether USD", "asset_type": AssetType.CRYPTO_TOKEN})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=base,
        quote_asset=quote,
        instrument_type=InstrumentType.SPOT,
        defaults={"role": InstrumentRole.EXECUTION},
    )
    return inst


@pytest.mark.django_db
def test_save_dual_side_snapshot_null_legacy_scores(test_instrument):
    """Verify XAUUSD persistence stores direction_score=None and timing_score=None while populating dual-side fields."""
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("Regime", 15.0, 15.0, "Bull"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 20.0, 100.0, (), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 80.0, 100.0, (ComponentScore("Zone", 25.0, 25.0, "Near EMA"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False)
    rfh = RuntimeFeedHealth(primary_15m=FeedHealthStatus.HEALTHY, macro_blackout_feed=FeedHealthStatus.HEALTHY)
    hard_gate = XauUsdHardGateEvaluation(False, None, (), rfh)

    snapshot = DualSideSignalSnapshot(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        reasons_long_positive=("Strong bull regime",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=("No short trigger",),
        hard_gate_reasons=(),
        resolution_reason="UNAUTHORIZED_PRODUCTION_PROFILE",
        analysis_fingerprint="test_xauusd_fp_123456",
        phase4_policy_fingerprint="policy_fp_789",
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
        profile_name="XAUUSD_CANDIDATE_v1",
        calibration_status="CANDIDATE_NOT_FROZEN",
    )

    record, created = SignalPersistenceService.save_dual_side_snapshot(test_instrument, snapshot)
    assert created is True
    assert record.direction_score is None  # Strictly NULL
    assert record.timing_score is None     # Strictly NULL
    assert record.long_direction_score == 85.0
    assert record.short_direction_score == 20.0
    assert record.long_timing_score == 80.0
    assert record.short_timing_score == 10.0
    assert record.profile_name == "XAUUSD_CANDIDATE_v1"
    assert record.calibration_status == "CANDIDATE_NOT_FROZEN"
    assert record.phase4_policy_fingerprint == "policy_fp_789"
    assert record.state == "NO_TRADE"
    assert record.user_decision == "WAIT"

    # Test Idempotency (A03)
    record2, created2 = SignalPersistenceService.save_dual_side_snapshot(test_instrument, snapshot)
    assert created2 is False
    assert record2.id == record.id


@pytest.mark.django_db
def test_candidate_resolution_reason_persistence_roundtrip(test_instrument):
    """Verify candidate_resolution_reason survives publication override and is persisted in DB."""
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 20.0, 100.0, (), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 80.0, 100.0, (), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False)
    rfh = RuntimeFeedHealth(primary_15m=FeedHealthStatus.HEALTHY, macro_blackout_feed=FeedHealthStatus.HEALTHY)
    hard_gate = XauUsdHardGateEvaluation(False, None, (), rfh)

    snapshot = DualSideSignalSnapshot(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        reasons_long_positive=("Bullish momentum",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="BLOCKED_PENDING_PHASE6_CALIBRATION (Candidate: BUY_WINDOW / BUY)",
        candidate_resolution_reason="LONG_QUALIFIED",
        publication_reason="BLOCKED_PENDING_PHASE6_CALIBRATION (Candidate: BUY_WINDOW / BUY)",
        analysis_fingerprint="test_xauusd_roundtrip_cand_fp_999",
        phase4_policy_fingerprint="policy_fp_999",
        code_revision="test-rev-p4",
        profile_name="XAUUSD_UNCALIBRATED",
        calibration_status="PENDING_PHASE6",
    )

    record, created = SignalPersistenceService.save_dual_side_snapshot(test_instrument, snapshot)
    assert created is True
    assert record.resolution_reason == "BLOCKED_PENDING_PHASE6_CALIBRATION (Candidate: BUY_WINDOW / BUY)"
    assert record.provenance["candidate_resolution_reason"] == "LONG_QUALIFIED"
    assert record.components_breakdown["candidate_resolution_reason"] == "LONG_QUALIFIED"

