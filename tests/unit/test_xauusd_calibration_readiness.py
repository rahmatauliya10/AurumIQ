"""Unit tests for XAUUSD calibration campaign governance, data manifest, and safety rules."""
import json
import os
from decimal import Decimal
import pytest
from engine.signals.profile import uncalibrated_xauusd_signal_profile, Phase4SignalProfile
from engine.cycles.profile import Cycle3AProfile


def test_production_authority_remains_strictly_false():
    """Governance safety rule: is_production_authorized must remain strictly False."""
    prof = uncalibrated_xauusd_signal_profile()
    assert prof.is_production_authorized is False
    assert isinstance(prof, Phase4SignalProfile)

    # Even with fully configured sub-policies, is_production_authorized must be False
    assert getattr(prof, "is_production_authorized") is False


def test_phase3b_production_weight_strictly_zero():
    """Governance safety rule: Phase 3B experimental production weight locked to 0.0."""
    from engine.core.types import Cycle3BExperimentalSnapshot
    from engine.cycles.experimental.profile import Cycle3BResearchProfile, ResearchCalibrationStatus

    prof = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()
    assert prof.status == ResearchCalibrationStatus.PENDING_DATA

    # Snapshot production_weight field default is strictly 0.0
    from dataclasses import fields
    p_weight_field = next(f for f in fields(Cycle3BExperimentalSnapshot) if f.name == "production_weight")
    assert p_weight_field.default == 0.0


def test_data_readiness_manifest_integrity():
    """Verify machine-readable manifest exists, is valid JSON, and records CALIBRATION_DATA_NOT_READY."""
    manifest_path = os.path.join("artifacts", "calibration", "xauusd_data_manifest.json")
    assert os.path.exists(manifest_path), f"Manifest missing at {manifest_path}"

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["instrument"] == "XAUUSD"
    assert manifest["primary_provider"] == "xauusd_primary"
    assert manifest["listing_role"] == "PRIMARY_XAUUSD_SPOT"
    assert manifest["hard_data_readiness_gate"]["passed"] is False
    assert manifest["hard_data_readiness_gate"]["decision"] == "CALIBRATION_DATA_NOT_READY"
    assert manifest["timeframe_counts"]["15m"] == 0
    assert manifest["empirical_friction_evidence"]["status"] == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
    assert manifest["dataset_fingerprint"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_data_readiness_report_exists():
    """Verify human-readable audit report exists in docs/calibration."""
    report_path = os.path.join("docs", "calibration", "XAUUSD_DATA_READINESS_REPORT.md")
    assert os.path.exists(report_path), f"Report missing at {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "CALIBRATION_DATA_NOT_READY" in content
    assert "PRIMARY_XAUUSD_SPOT" in content
    assert "EMPIRICAL_FRICTION_NOT_CONFIGURED" in content
    assert "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" in content


@pytest.mark.django_db
def test_primary_listing_active_fail_closed():
    """Verify primary listing exists in database and reports fail-closed when data is absent."""
    from django.core.management import call_command
    from apps.instruments.models import MarketListing, ListingRole, ListingStatus
    from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
    from datetime import datetime, timezone

    call_command("seed_instruments")

    primary = MarketListing.objects.filter(
        instrument__base_asset__code="XAU",
        instrument__quote_asset__code="USD",
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    ).first()

    assert primary is not None
    assert primary.provider == "xauusd_primary"

    # Candle query for primary source returns empty list (fail-closed)
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    candles = XauUsdLiveDecisionPipelineService.get_engine_candles(primary.instrument, "15m", now)
    assert len(candles) == 0
