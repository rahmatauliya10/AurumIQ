"""
AurumIQ XAUUSD Phase 3A empirical evidence readiness audit.

READ-ONLY:
- Does not mutate database.
- Does not create calibration authority.
- Does not freeze any profile.
- Does not alter signal thresholds.

Purpose:
Determine whether local evidence is sufficient to begin governed
XAUUSD Phase 3A empirical calibration.
"""

from dataclasses import fields
import inspect
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings.development",
)

import django
django.setup()


from django.db.models import Count, F, Max, Min

from apps.analysis.models import (
    CycleSnapshotRecord,
    FeatureSnapshotRecord,
    RegimeSnapshotRecord,
    StructureSnapshotRecord,
)
from apps.instruments.models import (
    ListingRole,
    ListingStatus,
    MarketListing,
)
from apps.market_data.models import (
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    MarketCandle,
)
from engine.cycles import calibration as cycle_calibration
from engine.cycles.engine import RobustTimeCycleEngine
from engine.cycles.profile import Cycle3AProfile


def _json_safe(value):
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return value


def _serialize_aggregate(data):
    return {
        key: _json_safe(value)
        for key, value in data.items()
    }


def _pct(num, den):
    if not den:
        return 0.0

    return round((float(num) / float(den)) * 100.0, 2)


def main():
    primary = (
        MarketListing.objects
        .filter(
            instrument__base_asset__code="XAU",
            instrument__quote_asset__code="USD",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        )
        .select_related(
            "instrument",
            "instrument__base_asset",
            "instrument__quote_asset",
        )
        .first()
    )

    if primary is None:
        report = {
            "status": "FAIL_CLOSED",
            "reason": "PRIMARY_XAUUSD_SPOT listing not configured",
        }

        print(json.dumps(report, indent=2))
        return 1

    instrument = primary.instrument
    source = primary.provider

    # ---------------------------------------------------------
    # 1. MARKET DATA
    # ---------------------------------------------------------

    candle_report = {}

    for timeframe in (
        "15m",
        "1h",
        "4h",
        "1d",
        "5m",
        "1m",
    ):
        qs = MarketCandle.objects.filter(
            instrument=instrument,
            timeframe=timeframe,
            is_closed=True,
            source=source,
        )

        stats = qs.aggregate(
            count=Count("id"),
            earliest_open=Min("timestamp_open"),
            latest_close=Max("timestamp_close"),
        )

        candle_report[timeframe] = (
            _serialize_aggregate(stats)
        )

    volume_distribution = list(
        MarketCandle.objects
        .filter(
            instrument=instrument,
            source=source,
        )
        .values("volume_evidence")
        .annotate(count=Count("id"))
        .order_by("volume_evidence")
    )

    # ---------------------------------------------------------
    # 2. MACRO PIT EVIDENCE
    # ---------------------------------------------------------

    macro_identity_count = (
        MacroEventIdentity.objects.count()
    )

    schedule_stats = (
        MacroScheduleVintage.objects.aggregate(
            count=Count("vintage_id"),
            earliest_scheduled=Min("scheduled_at"),
            latest_scheduled=Max("scheduled_at"),
            earliest_known=Min("known_at"),
            latest_known=Max("known_at"),
        )
    )

    schedule_count = int(
        schedule_stats["count"] or 0
    )

    schedule_known_before_release = (
        MacroScheduleVintage.objects.filter(
            known_at__lt=F("scheduled_at"),
        ).count()
    )

    schedule_with_source = (
        MacroScheduleVintage.objects
        .filter(source_snapshot__isnull=False)
        .count()
    )

    observation_stats = (
        MacroObservationVintage.objects.aggregate(
            count=Count("vintage_id"),
            earliest_known=Min("known_at"),
            latest_known=Max("known_at"),
            earliest_release=Min("source_published_at"),
            latest_release=Max("source_published_at"),
        )
    )

    observation_with_source = (
        MacroObservationVintage.objects
        .filter(source_snapshot__isnull=False)
        .count()
    )

    # ---------------------------------------------------------
    # 3. EXISTING ANALYTICAL SNAPSHOTS
    # ---------------------------------------------------------

    def snapshot_stats(model):
        return _serialize_aggregate(
            model.objects
            .filter(
                instrument=instrument,
                timeframe="15m",
            )
            .aggregate(
                count=Count("id"),
                earliest=Min("timestamp"),
                latest=Max("timestamp"),
            )
        )

    feature_stats = snapshot_stats(
        FeatureSnapshotRecord
    )

    regime_stats = snapshot_stats(
        RegimeSnapshotRecord
    )

    structure_stats = snapshot_stats(
        StructureSnapshotRecord
    )

    cycle_stats = snapshot_stats(
        CycleSnapshotRecord
    )

    cycle_nonzero_score_count = (
        CycleSnapshotRecord.objects
        .filter(
            instrument=instrument,
            timeframe="15m",
            cycle_score_3a__gt=0.0,
        )
        .count()
    )

    # ---------------------------------------------------------
    # 4. PHASE 3A PERSISTENCE PARITY
    # ---------------------------------------------------------

    cycle_model_fields = {
        field.name
        for field
        in CycleSnapshotRecord._meta.fields
    }

    required_cycle_provenance_fields = {
        "profile_name",
        "calibration_status",
        "calibration_artifact_version",
    }

    missing_cycle_provenance_fields = sorted(
        required_cycle_provenance_fields
        - cycle_model_fields
    )

    # ---------------------------------------------------------
    # 5. CALIBRATION IMPLEMENTATION CAPABILITIES
    # ---------------------------------------------------------

    cycle_profile_field_names = {
        f.name
        for f in fields(Cycle3AProfile)
    }

    cycle_engine_source = inspect.getsource(
        RobustTimeCycleEngine.analyze
    )

    swing_runtime_contract_ready = (
        "swing_sample_evaluation"
        in cycle_profile_field_names
        and "eff_profile.swing_sample_evaluation"
        in cycle_engine_source
    )

    try:
        from engine.cycles.serialization import (
            deserialize_cycle3a_profile,
            serialize_cycle3a_profile,
        )

        governed_serialization_ready = (
            callable(
                serialize_cycle3a_profile
            )
            and callable(
                deserialize_cycle3a_profile
            )
        )
    except ImportError:
        governed_serialization_ready = False

    calibration_capabilities = {
        "session_calibration": hasattr(
            cycle_calibration,
            "calibrate_session_expectancy",
        ),
        "swing_duration_calibration": hasattr(
            cycle_calibration,
            "calibrate_swing_durations",
        ),
        "swing_runtime_sample_contract": (
            swing_runtime_contract_ready
        ),
        "calendar_calibration": any(
            name.startswith("calibrate_calendar")
            for name in dir(cycle_calibration)
        ),
        "profile_builder": hasattr(
            cycle_calibration,
            "build_profile_from_artifact",
        ),
        "governed_serialization": (
            governed_serialization_ready
        ),
    }

    # ---------------------------------------------------------
    # 6. CURRENT DEFAULT XAUUSD PROFILE
    # ---------------------------------------------------------

    current_profile = (
        Cycle3AProfile
        .uncalibrated_xauusd_profile(
            timeframe="15m",
        )
    )

    # ---------------------------------------------------------
    # 7. STATIC BLOCKER CLASSIFICATION
    # ---------------------------------------------------------

    blockers = []

    if not calibration_capabilities[
        "calendar_calibration"
    ]:
        blockers.append(
            "CALENDAR_CALIBRATION_ROUTINE_MISSING"
        )

    if missing_cycle_provenance_fields:
        blockers.append(
            "CYCLE_SNAPSHOT_PROFILE_PROVENANCE_NOT_PERSISTED"
        )

    if schedule_count == 0:
        blockers.append(
            "MACRO_SCHEDULE_EVIDENCE_MISSING"
        )

    if int(observation_stats["count"] or 0) == 0:
        blockers.append(
            "MACRO_OBSERVATION_EVIDENCE_MISSING"
        )

    if not swing_runtime_contract_ready:
        blockers.append(
            "SWING_EFFECTIVE_N_RUNTIME_CONTRACT_NOT_YET_WIRED"
        )

    if not governed_serialization_ready:
        blockers.append(
            "CYCLE3A_GOVERNED_SERIALIZATION_NOT_YET_IMPLEMENTED"
        )

    report = {
        "audit_schema": (
            "aurumiq.phase3a.evidence.audit.v1"
        ),
        "instrument": "XAUUSD",
        "timeframe": "15m",
        "primary_provider": source,

        "market_data": {
            "candles": candle_report,
            "volume_evidence_distribution": (
                volume_distribution
            ),
        },

        "macro_evidence": {
            "identity_count": (
                macro_identity_count
            ),
            "schedule": {
                **_serialize_aggregate(
                    schedule_stats
                ),
                "known_before_release_count": (
                    schedule_known_before_release
                ),
                "known_before_release_pct": _pct(
                    schedule_known_before_release,
                    schedule_count,
                ),
                "with_source_snapshot_count": (
                    schedule_with_source
                ),
                "with_source_snapshot_pct": _pct(
                    schedule_with_source,
                    schedule_count,
                ),
            },
            "observation": {
                **_serialize_aggregate(
                    observation_stats
                ),
                "with_source_snapshot_count": (
                    observation_with_source
                ),
                "with_source_snapshot_pct": _pct(
                    observation_with_source,
                    int(
                        observation_stats[
                            "count"
                        ] or 0
                    ),
                ),
            },
        },

        "persisted_analysis": {
            "feature_15m": feature_stats,
            "regime_15m": regime_stats,
            "structure_15m": structure_stats,
            "cycle_3a_15m": cycle_stats,
            "cycle_nonzero_score_count": (
                cycle_nonzero_score_count
            ),
        },

        "persistence_parity": {
            "cycle_snapshot_fields": sorted(
                cycle_model_fields
            ),
            "required_profile_provenance": sorted(
                required_cycle_provenance_fields
            ),
            "missing_profile_provenance": (
                missing_cycle_provenance_fields
            ),
            "profile_provenance_complete": (
                not missing_cycle_provenance_fields
            ),
        },

        "calibration_capabilities": (
            calibration_capabilities
        ),

        "current_cycle3a_profile": {
            "name": current_profile.name,
            "status": (
                current_profile
                .calibration_status
                .value
            ),
            "production_scoring_enabled": (
                current_profile
                .is_production_scoring_enabled
            ),
        },

        "blockers": blockers,

        "audit_decision": (
            "REMEDIATION_REQUIRED"
            if blockers
            else "READY_FOR_CALIBRATION"
        ),
    }

    print(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
