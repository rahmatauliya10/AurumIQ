"""DRF Serializers for Live Monitor presentation projections and read-only audit records."""
from rest_framework import serializers

from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.signals.models import SignalRecord


class LiveMonitorStateSerializer(serializers.ModelSerializer):
    """Read-only serializer for the live presentation state projection."""

    class Meta:
        model = LiveMonitorState
        fields = [
            "id",
            "instrument",
            "current_bid",
            "current_ask",
            "spread",
            "spread_pct",
            "quote_source_timestamp",
            "quote_received_timestamp",
            "quote_age_seconds",
            "is_quote_stale",
            "quote_sequence",
            "entry_zone_status",
            "distance_to_entry_zone_pct",
            "last_closed_candle_ts",
            "last_analysis_timestamp",
            "signal_fingerprint",
            "signal_state",
            "signal_user_decision",
            "direction_score",
            "timing_score",
            # Phase 7 Dual-Layer & Dual-Side
            "candidate_state",
            "candidate_user_decision",
            "published_state",
            "published_user_decision",
            "long_direction_score",
            "short_direction_score",
            "long_timing_score",
            "short_timing_score",
            "risk_side",
            "risk_candidate_status",
            "candidate_effective_action",
            "publication_effective_action",
            "risk_plan_valid",
            "execution_eligible",
            "effective_action",
            "entry_min",
            "entry_mid",
            "entry_max",
            "stop_structure",
            "stop_atr",
            "stop_final",
            "stop_distance_atr",
            "tp1",
            "tp2",
            "rr_tp1",
            "rr_tp2",
            "calibration_status",
            "profile_name",
            "candidate_resolution_reason",
            "publication_reason",
            "reasons_positive",
            "reasons_negative",
            "hard_gate_reasons",
            "feed_health_data",
            "analysis_fingerprint",
            "phase4_policy_fingerprint",
            "risk_plan_fingerprint",
            "source_phase4_fingerprint",
            "engine_version",
            "config_version",
            "feature_version",
            "cycle_version",
            "risk_version",
            "code_revision",
            "decision_sequence",
            "updated_at",
            "created_at",
        ]
        read_only_fields = fields


class SignalRecordReadOnlySerializer(serializers.ModelSerializer):
    """Read-only serializer for immutable historical SignalRecords."""

    instrument_symbol = serializers.CharField(source="instrument.symbol", read_only=True)

    class Meta:
        model = SignalRecord
        fields = [
            "id",
            "analysis_fingerprint",
            "instrument_symbol",
            "timeframe",
            "timestamp",
            "state",
            "user_decision",
            "direction_score",
            "timing_score",
            "long_direction_score",
            "short_direction_score",
            "long_timing_score",
            "short_timing_score",
            "profile_name",
            "calibration_status",
            "resolution_reason",
            "phase4_policy_fingerprint",
            "reasons_positive",
            "reasons_negative",
            "hard_gate_reasons",
            "components_breakdown",
            "provenance",
            "engine_version",
            "config_version",
            "feature_version",
            "cycle_version",
            "code_revision",
            "created_at",
        ]
        read_only_fields = fields


class LiveRiskPlanRecordReadOnlySerializer(serializers.ModelSerializer):
    """Read-only serializer for immutable historical LiveRiskPlanRecords."""

    class Meta:
        model = LiveRiskPlanRecord
        fields = [
            "id",
            "source_signal_fingerprint",
            "signal_timestamp",
            "instrument",
            "risk_side",
            "risk_candidate_status",
            "risk_candidate_valid",
            "simulation_eligible",
            "candidate_effective_action",
            "publication_effective_action",
            "entry_min",
            "entry_mid",
            "entry_max",
            "stop_structure",
            "stop_atr",
            "stop_final",
            "stop_distance_atr",
            "tp1",
            "tp2",
            "rr_tp1",
            "rr_tp2",
            "is_valid_risk_plan",
            "execution_eligible",
            "effective_action",
            "reasons",
            "entry_zone_fingerprint",
            "tp1_zone_fingerprint",
            "tp2_zone_fingerprint",
            "phase5_policy_fingerprint",
            "risk_plan_fingerprint",
            "source_phase4_fingerprint",
            "source_zone_id",
            "source_zone_timestamp",
            "risk_version",
            "execution_model_version",
            "config_version",
            "code_revision",
            "created_at",
        ]
        read_only_fields = fields
