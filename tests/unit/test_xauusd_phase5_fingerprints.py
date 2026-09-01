"""
Unit tests for XAUUSD Phase 5 lossless cryptographic fingerprints.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    QuoteData,
    RiskSide,
    SignalState,
    StructureZone,
    UserDecision,
    VolumeEvidenceType,
)
from engine.risk.xauusd_fingerprints import (
    canonical_utc_timestamp,
    compute_candle_evidence_fingerprint,
    compute_execution_fingerprint,
    compute_phase5_policy_fingerprint,
    compute_quote_evidence_fingerprint,
    compute_risk_plan_fingerprint,
    compute_zone_fingerprint,
)
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)


@pytest.mark.unit
def test_canonical_utc_timestamp():
    """Converts timezone-aware datetime to UTC ISO string with microseconds and Z."""
    dt1 = datetime(2026, 9, 1, 10, 30, 0, 123456, tzinfo=timezone.utc)
    assert canonical_utc_timestamp(dt1) == "2026-09-01T10:30:00.123456Z"

    # Equivalent offset time produces identical string
    tz_plus_2 = timezone(timedelta(hours=2))
    dt2 = datetime(2026, 9, 1, 12, 30, 0, 123456, tzinfo=tz_plus_2)
    assert canonical_utc_timestamp(dt2) == "2026-09-01T10:30:00.123456Z"

    # Naive datetime raises ValueError
    with pytest.raises(ValueError, match="Timezone-aware"):
        canonical_utc_timestamp(datetime(2026, 9, 1, 10, 30, 0))


@pytest.mark.unit
def test_zone_fingerprint_lossless():
    """Zone fingerprint binds all 6 fields including touches."""
    ts = datetime(2026, 9, 1, 8, 0, 0, 500000, tzinfo=timezone.utc)
    z1 = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), ts, 3, True)
    z2 = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), ts, 4, True)

    fp1 = compute_zone_fingerprint(z1)
    fp2 = compute_zone_fingerprint(z2)

    assert isinstance(fp1, str) and len(fp1) == 64
    assert fp1 != fp2  # touches difference changes fingerprint


@pytest.mark.unit
def test_quote_evidence_fingerprint():
    """Quote fingerprint is deterministic and binds all quote attributes."""
    ts = datetime(2026, 9, 1, 8, 15, 0, 0, tzinfo=timezone.utc)
    q1 = QuoteData(ts, Decimal("2500.10"), Decimal("2500.30"), "feed1")
    q2 = QuoteData(ts, Decimal("2500.10"), Decimal("2500.35"), "feed1")

    fp1 = compute_quote_evidence_fingerprint(q1)
    fp2 = compute_quote_evidence_fingerprint(q2)

    assert fp1 == compute_quote_evidence_fingerprint(q1)
    assert fp1 != fp2


@pytest.mark.unit
def test_candle_evidence_fingerprint():
    """Candle evidence fingerprint binds all 13 canonical CandleData attributes."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)
    c1 = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2505.00"), Decimal("2498.00"), Decimal("2502.00"),
        Decimal("100.0"), True, "test_feed", Decimal("1.0"), Decimal("2502.00"), VolumeEvidenceType.REAL_VOLUME
    )
    c2 = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2505.00"), Decimal("2498.00"), Decimal("2502.00"),
        Decimal("105.0"), True, "test_feed", Decimal("1.0"), Decimal("2502.00"), VolumeEvidenceType.REAL_VOLUME
    )

    fp1 = compute_candle_evidence_fingerprint(c1)
    fp2 = compute_candle_evidence_fingerprint(c2)

    assert fp1 == compute_candle_evidence_fingerprint(c1)
    assert fp1 != fp2  # volume difference changes fingerprint


@pytest.mark.unit
def test_policy_fingerprint_deterministic():
    """Policy fingerprint derives deterministically from profile configuration."""
    profile1 = XauUsdRiskProfile(
        long_risk_policy=SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.8")),
        short_risk_policy=SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.8")),
    )
    profile2 = XauUsdRiskProfile(
        long_risk_policy=SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.8")),
        short_risk_policy=SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.8")),
    )
    profile3 = XauUsdRiskProfile(
        long_risk_policy=SideRiskPolicy(Decimal("2.0"), Decimal("2.0"), Decimal("4.0"), Decimal("1.8")),
        short_risk_policy=SideRiskPolicy(Decimal("1.5"), Decimal("2.0"), Decimal("4.0"), Decimal("1.8")),
    )

    fp1 = compute_phase5_policy_fingerprint(profile1)
    fp2 = compute_phase5_policy_fingerprint(profile2)
    fp3 = compute_phase5_policy_fingerprint(profile3)

    assert fp1 == fp2
    assert fp1 != fp3
