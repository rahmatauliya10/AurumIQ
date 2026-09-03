"""
Hostile unit tests for XAUUSD Data Readiness Semantics (Scenarios A through R).

Verifies strict fail-closed generic backfill guard, per-timeframe independent coverage,
real gap statistics, dual fingerprint separation (Phase 6 15m vs Readiness 6-TF),
immutable provenance, and calibration boundary invariants.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from unittest.mock import patch, MagicMock
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.instruments.models import (
    Instrument,
    MarketListing,
    ListingRole,
    ListingStatus,
)
from apps.market_data.models import MarketCandle, VolumeEvidenceType
from apps.market_data.readiness import (
    XauUsdDataReadinessEvaluator,
    XauUsdDataReadinessReport,
    compute_xauusd_readiness_fingerprint,
    evaluate_timeframe_coverage_and_gaps,
    EMPTY_DATASET_HASH,
)


class MockCandle:
    """Lightweight in-memory candle object adhering to MarketCandle contract."""
    def __init__(
        self,
        timeframe: str,
        timestamp_open: datetime,
        timestamp_close: datetime,
        open: Decimal = Decimal("2000.00"),
        high: Decimal = Decimal("2010.00"),
        low: Decimal = Decimal("1995.00"),
        close: Decimal = Decimal("2005.00"),
        volume: Decimal = Decimal("100"),
        source: str = "twelve_data_xauusd",
        is_closed: bool = True,
        volume_evidence: VolumeEvidenceType = VolumeEvidenceType.REAL_VOLUME,
    ):
        self.timeframe = timeframe
        self.timestamp_open = timestamp_open
        self.timestamp_close = timestamp_close
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.source = source
        self.source_id = source
        self.is_closed = is_closed
        self.volume_evidence = volume_evidence


@pytest.fixture
def xauusd_test_env(db):
    """Seed instruments and return canonical spot XAUUSD."""
    call_command("seed_instruments")
    instrument = Instrument.get_canonical_xauusd()
    primary_listing = MarketListing.objects.filter(
        instrument=instrument,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    ).first()
    return instrument, primary_listing


# =====================================================================
# Scenario A: Generic backfill_candles with XAU/USD + twelve_data_xauusd fails
# =====================================================================
@pytest.mark.django_db
def test_scenario_a_generic_backfill_fails_before_provider_fetch(xauusd_test_env):
    """Generic backfill_candles must fail closed before making any network call for XAU/USD Twelve Data."""
    with patch("apps.market_data.providers.registry.registry.get") as mock_get:
        with pytest.raises(CommandError) as exc:
            call_command(
                "backfill_candles",
                symbol="XAU/USD",
                timeframes="15m",
                days=1,
                provider="twelve_data_xauusd",
            )
        assert "CANONICAL_XAUUSD_TWELVE_DATA_REQUIRES_SPECIALIZED_BACKFILL" in str(exc.value)
        mock_get.assert_not_called()


# =====================================================================
# Scenario B: Specialized backfill remains allowed
# =====================================================================
@pytest.mark.django_db
def test_scenario_b_specialized_backfill_remains_allowed():
    """Verify specialized backfill_xauusd_twelve_data exists and generic non-canonical pairs do not trigger guard."""
    from apps.market_data.management.commands.backfill_xauusd_twelve_data import Command as SpecializedCommand
    assert hasattr(SpecializedCommand, "handle")

    # Generic backfill for a non-XAU/USD symbol should NOT fail with the specialized guard
    with patch("apps.market_data.providers.registry.registry.get") as mock_get:
        mock_provider = MagicMock()
        mock_provider.fetch_candles.return_value = []
        mock_get.return_value = mock_provider
        try:
            call_command(
                "backfill_candles",
                symbol="EUR/USD",
                timeframes="15m",
                days=1,
                provider="mock_forex",
            )
        except CommandError as ce:
            assert "CANONICAL_XAUUSD_TWELVE_DATA_REQUIRES_SPECIALIZED_BACKFILL" not in str(ce)


# =====================================================================
# Scenario C: Global earliest 2020 from 1d but 1m starts 2025 -> coverage FALSE
# =====================================================================
def test_scenario_c_global_1d_early_but_1m_late_fails_coverage(xauusd_test_env):
    """If 1d starts in 2020 but 1m starts in 2025, overall coverage must be FALSE."""
    instrument, listing = xauusd_test_env
    candles = []

    # 1d candle covering 2020-04-07
    c_1d = MockCandle("1d", datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc), datetime(2020, 4, 8, 0, 0, tzinfo=timezone.utc))
    candles.append(c_1d)

    # 1m candle starting late in 2025-01-01
    c_1m = MockCandle("1m", datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc), datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc))
    candles.append(c_1m)

    # Other TFs
    for tf in ("5m", "15m", "1h", "4h"):
        candles.append(MockCandle(tf, datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc), datetime(2020, 4, 7, 1, 0, tzinfo=timezone.utc)))

    # End candle for 2026-09-01
    candles.append(MockCandle("1d", datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc), datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)))
    candles.append(MockCandle("1m", datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc), datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)))

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles,
        expected_coverage_start=datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc),
        expected_coverage_end=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
    )

    assert report.coverage_complete is False
    assert report.coverage_by_timeframe["1m"]["coverage_start_satisfied"] is False
    assert report.coverage_by_timeframe["1d"]["coverage_start_satisfied"] is True


# =====================================================================
# Scenario D: All TFs except 5m complete -> coverage FALSE
# =====================================================================
def test_scenario_d_all_tfs_except_5m_complete_fails_coverage(xauusd_test_env):
    """If 5m fails to cover the requested window, overall coverage must be FALSE."""
    instrument, listing = xauusd_test_env
    t_start = datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    candles = []

    for tf in ("1m", "15m", "1h", "4h", "1d"):
        candles.append(MockCandle(tf, t_start, t_start + timedelta(minutes=15)))
        candles.append(MockCandle(tf, t_end - timedelta(minutes=15), t_end))

    # 5m candle starts late
    candles.append(MockCandle("5m", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc), t_end))

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles,
        expected_coverage_start=t_start,
        expected_coverage_end=t_end,
    )

    assert report.coverage_complete is False
    assert report.coverage_by_timeframe["5m"]["coverage_complete"] is False
    assert report.coverage_by_timeframe["15m"]["coverage_complete"] is True


# =====================================================================
# Scenario E: Missing required timeframe -> coverage FALSE
# =====================================================================
def test_scenario_e_missing_required_timeframe_fails_coverage(xauusd_test_env):
    """If 4h has 0 candles, coverage must be FALSE."""
    instrument, listing = xauusd_test_env
    t_start = datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    candles = []

    for tf in ("1m", "5m", "15m", "1h", "1d"):
        candles.append(MockCandle(tf, t_start, t_start + timedelta(minutes=15)))
        candles.append(MockCandle(tf, t_end - timedelta(minutes=15), t_end))

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles,
        expected_coverage_start=t_start,
        expected_coverage_end=t_end,
    )

    assert report.coverage_complete is False
    assert report.coverage_by_timeframe["4h"]["count"] == 0
    assert report.coverage_by_timeframe["4h"]["coverage_complete"] is False


# =====================================================================
# Scenario F: Every required TF covers requested window -> coverage TRUE
# =====================================================================
def test_scenario_f_every_required_tf_covers_window_succeeds(xauusd_test_env):
    """When all 6 required timeframes cover start and end boundaries, coverage is TRUE."""
    instrument, listing = xauusd_test_env
    t_start = datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    candles = []

    # Provide 20 bars of 15m to satisfy warm-up
    for i in range(25):
        t_o = t_start + i * timedelta(minutes=15)
        candles.append(MockCandle("15m", t_o, t_o + timedelta(minutes=15)))
    candles.append(MockCandle("15m", t_end - timedelta(minutes=15), t_end))

    for tf in ("1m", "5m", "1h", "4h", "1d"):
        candles.append(MockCandle(tf, t_start, t_start + timedelta(minutes=15)))
        candles.append(MockCandle(tf, t_end - timedelta(minutes=15), t_end))

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles,
        expected_coverage_start=t_start,
        expected_coverage_end=t_end,
    )

    assert report.coverage_complete is True
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert report.coverage_by_timeframe[tf]["coverage_complete"] is True


# =====================================================================
# Scenario G: An internal single missing 1m interval is counted as a gap, not coverage loss
# =====================================================================
def test_scenario_g_internal_missing_interval_counted_as_gap():
    """An internal missing 1m bar between 00:00-00:01 and 00:02-00:03 is an internal gap."""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t0 + timedelta(minutes=2)
    t3 = t0 + timedelta(minutes=3)

    c1 = MockCandle("1m", t0, t1)
    c2 = MockCandle("1m", t2, t3)  # Missing 1 minute: 00:01 to 00:02

    cov, gaps = evaluate_timeframe_coverage_and_gaps("1m", [c1, c2], None, None)

    assert gaps["internal_gap_count"] == 1
    assert gaps["missing_interval_count"] == 1
    assert gaps["largest_gap_seconds"] == 60


# =====================================================================
# Scenario H: Target history before observed earliest is coverage incomplete, not internal gap
# =====================================================================
def test_scenario_h_pre_pilot_missing_is_coverage_incomplete_not_internal_gap(xauusd_test_env):
    """History prior to 2026-06-01 is classified as coverage incomplete, not internal data gaps."""
    instrument, listing = xauusd_test_env
    t_pilot_start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    candles = []

    # Create 25 contiguous 15m candles inside the pilot
    for i in range(25):
        t_o = t_pilot_start + i * timedelta(minutes=15)
        candles.append(MockCandle("15m", t_o, t_o + timedelta(minutes=15)))

    for tf in ("1m", "5m", "1h", "4h", "1d"):
        candles.append(MockCandle(tf, t_pilot_start, t_pilot_start + timedelta(hours=1)))

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles,
        expected_coverage_start=datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc),
        expected_coverage_end=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
    )

    # Coverage is incomplete because start is not met
    assert report.coverage_complete is False
    assert report.coverage_by_timeframe["15m"]["coverage_start_satisfied"] is False

    # But within observed span, internal gaps are 0
    assert report.gap_statistics_by_timeframe["15m"]["internal_gap_count"] == 0
    assert report.gap_statistics_by_timeframe["15m"]["missing_interval_count"] == 0


# =====================================================================
# Scenario I: Gap statistics no longer default falsely to 100.0%
# =====================================================================
def test_scenario_i_gap_statistics_no_longer_default_to_100_percent():
    """Contiguous candles within span report 0.00%, while empty returns NOT_EVALUATED."""
    # Empty set
    cov_empty, gap_empty = evaluate_timeframe_coverage_and_gaps("15m", [], None, None)
    assert gap_empty["missing_intervals_pct"] == "NOT_EVALUATED"

    # Contiguous 2 candles
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    c1 = MockCandle("15m", t0, t0 + timedelta(minutes=15))
    c2 = MockCandle("15m", t0 + timedelta(minutes=15), t0 + timedelta(minutes=30))
    cov_contig, gap_contig = evaluate_timeframe_coverage_and_gaps("15m", [c1, c2], None, None)
    assert gap_contig["missing_intervals_pct"] == "0.00%"


# =====================================================================
# Scenario J: Largest gap is deterministically computed
# =====================================================================
def test_scenario_j_largest_gap_deterministically_computed():
    """Largest internal gap calculation is strictly deterministic."""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    c1 = MockCandle("1m", t0, t0 + timedelta(minutes=1))
    c2 = MockCandle("1m", t0 + timedelta(minutes=3), t0 + timedelta(minutes=4))  # gap = 120s
    c3 = MockCandle("1m", t0 + timedelta(minutes=9), t0 + timedelta(minutes=10)) # gap = 300s

    cov, gaps = evaluate_timeframe_coverage_and_gaps("1m", [c1, c2, c3], None, None)
    assert gaps["largest_gap_seconds"] == 300
    assert gaps["internal_gap_count"] == 2
    assert gaps["missing_interval_count"] == 7  # 2 + 5


# =====================================================================
# Scenario K: Manifest required duration differs from 92-day actual pilot duration
# =====================================================================
def test_scenario_k_manifest_required_duration_differs_from_actual_span(xauusd_test_env):
    """Manifest must distinguish 2338 required days from 92 observed pilot days."""
    instrument, listing = xauusd_test_env
    t_start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    candles = [
        MockCandle("15m", t_start, t_start + timedelta(minutes=15)),
        MockCandle("15m", t_end - timedelta(minutes=15), t_end),
    ]

    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles,
        expected_coverage_start=datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc),
        expected_coverage_end=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
    )

    manifest = report.to_manifest_dict(code_revision="4773f44a8baab65908f3a5ec7a7464e6dd974ac2")
    assert manifest["required_duration_days"] == 2338.0
    assert manifest["actual_observed_span_days"] == 92.0
    assert manifest["required_duration_days"] != manifest["actual_observed_span_days"]


# =====================================================================
# Scenario L: Sealed manifest rejects literal 'HEAD'
# =====================================================================
def test_scenario_l_sealed_manifest_rejects_literal_head(xauusd_test_env):
    """Sealed manifest generation must reject 'HEAD', 'main', or empty revisions unless allow_mutable_revision is True."""
    instrument, listing = xauusd_test_env
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_candles=[])

    for invalid_rev in ("HEAD", "head", "main", "MASTER", "", "   ", "abc"):
        with pytest.raises(ValueError) as exc:
            report.to_manifest_dict(code_revision=invalid_rev)
        assert "IMMUTABLE_CODE_REVISION_REQUIRED" in str(exc.value)


# =====================================================================
# Scenario M: Explicit immutable SHA accepted
# =====================================================================
def test_scenario_m_explicit_immutable_sha_accepted(xauusd_test_env):
    """Valid Git SHA-1 or SHA-256 strings must be accepted."""
    instrument, listing = xauusd_test_env
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_candles=[])

    sha = "4773f44a8baab65908f3a5ec7a7464e6dd974ac2"
    manifest = report.to_manifest_dict(code_revision=sha)
    assert manifest["audit_code_revision"] == sha
    assert manifest["code_revision"] == sha


# =====================================================================
# Scenario N: Readiness fingerprint stable on identical dataset
# =====================================================================
def test_scenario_n_readiness_fingerprint_stable_on_identical_dataset():
    """Fingerprint function returns exact same SHA-256 given identical candles."""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    candles = [
        MockCandle("1m", t0, t0 + timedelta(minutes=1)),
        MockCandle("5m", t0, t0 + timedelta(minutes=5)),
        MockCandle("15m", t0, t0 + timedelta(minutes=15)),
        MockCandle("1h", t0, t0 + timedelta(hours=1)),
        MockCandle("4h", t0, t0 + timedelta(hours=4)),
        MockCandle("1d", t0, t0 + timedelta(days=1)),
    ]

    hash1 = compute_xauusd_readiness_fingerprint(candles)
    hash2 = compute_xauusd_readiness_fingerprint(candles)
    assert hash1 == hash2
    assert len(hash1) == 64


# =====================================================================
# Scenario O: Altering one 1m candle changes readiness fingerprint
# =====================================================================
def test_scenario_o_altering_one_1m_candle_changes_readiness_fingerprint():
    """Mutating any field of a 1m candle produces a different readiness fingerprint."""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    c1 = MockCandle("1m", t0, t0 + timedelta(minutes=1), close=Decimal("2000.00"))
    c2 = MockCandle("5m", t0, t0 + timedelta(minutes=5))

    hash_orig = compute_xauusd_readiness_fingerprint([c1, c2])

    c1_mutated = MockCandle("1m", t0, t0 + timedelta(minutes=1), close=Decimal("2000.01"))
    hash_mutated = compute_xauusd_readiness_fingerprint([c1_mutated, c2])

    assert hash_orig != hash_mutated


# =====================================================================
# Scenario P: Altering one 5m candle changes readiness fingerprint
# =====================================================================
def test_scenario_p_altering_one_5m_candle_changes_readiness_fingerprint():
    """Mutating any field of a 5m candle produces a different readiness fingerprint."""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    c1 = MockCandle("1m", t0, t0 + timedelta(minutes=1))
    c2 = MockCandle("5m", t0, t0 + timedelta(minutes=5), open=Decimal("1999.50"))

    hash_orig = compute_xauusd_readiness_fingerprint([c1, c2])

    c2_mutated = MockCandle("5m", t0, t0 + timedelta(minutes=5), open=Decimal("1999.60"))
    hash_mutated = compute_xauusd_readiness_fingerprint([c1, c2_mutated])

    assert hash_orig != hash_mutated


# =====================================================================
# Scenario Q: Phase 6 15m fingerprint unchanged when non-15m candle altered
# =====================================================================
def test_scenario_q_phase6_15m_fingerprint_unchanged_when_non_15m_altered(xauusd_test_env):
    """Phase 6 15m dataset identity depends ONLY on 15m candles and remains isolated from 1m/5m changes."""
    instrument, listing = xauusd_test_env
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    candles_15m = [MockCandle("15m", t0 + i * timedelta(minutes=15), t0 + (i + 1) * timedelta(minutes=15)) for i in range(25)]

    c_1m_v1 = MockCandle("1m", t0, t0 + timedelta(minutes=1), close=Decimal("2000.00"))
    c_1m_v2 = MockCandle("1m", t0, t0 + timedelta(minutes=1), close=Decimal("2005.00"))

    report_v1 = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles_15m + [c_1m_v1],
    )
    report_v2 = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_candles=candles_15m + [c_1m_v2],
    )

    # 15m identity is identical
    assert report_v1.phase6_15m_dataset_fingerprint == report_v2.phase6_15m_dataset_fingerprint

    # But 6-TF readiness fingerprint differs
    assert report_v1.readiness_evidence_fingerprint != report_v2.readiness_evidence_fingerprint


# =====================================================================
# Scenario R: Existing pilot remains CALIBRATION_DATA_NOT_READY
# =====================================================================
@pytest.mark.django_db
def test_scenario_r_existing_pilot_remains_calibration_data_not_ready(xauusd_test_env):
    """Live database evaluation with full expected window must remain CALIBRATION_DATA_NOT_READY."""
    instrument, listing = xauusd_test_env
    report = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        expected_coverage_start=datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc),
        expected_coverage_end=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
    )

    assert report.decision == "CALIBRATION_DATA_NOT_READY"
    assert report.passed is False
    assert report.coverage_complete is False
    assert any("HISTORICAL_COVERAGE_INCOMPLETE" in r for r in report.reasons)
