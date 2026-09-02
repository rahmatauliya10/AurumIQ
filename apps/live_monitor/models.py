"""Models for Live Monitor presentation projections and immutable live risk records."""
from decimal import Decimal
from django.db import models


class LiveMonitorState(models.Model):
    """
    Live presentation state projection (mutable cache-layer projection).

    Strict Invariants (P7-C1, P7-C4):
      1. Path A (Quote Path) and Path B (Decision Path) are independent writers with field-level scoping.
      2. Quote path updates quote fields only (bid, ask, spread, age, sequence, entry_zone_status).
      3. Decision path updates decision fields only (scores, signal_state, risk_plan, effective_action).
      4. Preserves Phase 4 signal (signal_state, signal_user_decision) and Phase 5 action (effective_action) separately.

    XAUUSD Dual-Side Addendum:
      5. Candidate layer (Layer A) and published layer (Layer B) stored independently.
      6. Long/Short direction and timing scores stored independently (no single overloaded score).
      7. Historical XAUT single-score fields remain nullable for backward compatibility.
    """
    instrument = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Canonical instrument symbol (e.g. XAUUSD, XAUT/USDT)",
    )

    # Path A: Quote Fields
    current_bid = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    current_ask = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    spread = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    spread_pct = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    quote_source_timestamp = models.DateTimeField(null=True, blank=True, db_index=True)
    quote_received_timestamp = models.DateTimeField(null=True, blank=True)
    quote_age_seconds = models.FloatField(null=True, blank=True)
    is_quote_stale = models.BooleanField(default=False)
    quote_sequence = models.BigIntegerField(null=True, blank=True)
    entry_zone_status = models.CharField(
        max_length=32,
        default="NO_ACTIVE_ZONE",
        db_index=True,
        help_text="INSIDE_ZONE, ABOVE_ZONE, BELOW_ZONE, NO_ACTIVE_ZONE",
    )
    distance_to_entry_zone_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

    # Path B: Decision Fields — Historical XAUT (single-side, nullable for XAUUSD)
    last_closed_candle_ts = models.DateTimeField(null=True, blank=True, db_index=True)
    last_analysis_timestamp = models.DateTimeField(null=True, blank=True)
    signal_fingerprint = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    signal_state = models.CharField(max_length=32, default="NO_TRADE", db_index=True)
    signal_user_decision = models.CharField(max_length=16, default="WAIT", db_index=True)
    direction_score = models.FloatField(default=0.0, help_text="Historical XAUT single direction score, NULL for XAUUSD")
    timing_score = models.FloatField(default=0.0, help_text="Historical XAUT single timing score, NULL for XAUUSD")

    # Path B: Decision Fields — XAUUSD Dual-Layer (Phase 7 additive)
    candidate_state = models.CharField(max_length=32, null=True, blank=True, db_index=True,
                                       help_text="Phase 4 Layer A candidate state (BUY_WINDOW, SELL_WINDOW, WAIT, CONFLICT, NO_TRADE)")
    candidate_user_decision = models.CharField(max_length=16, null=True, blank=True, db_index=True,
                                                help_text="Phase 4 Layer A candidate decision (BUY, SELL, WAIT)")
    published_state = models.CharField(max_length=32, null=True, blank=True, db_index=True,
                                       help_text="Phase 4 Layer B published state (from snapshot.state)")
    published_user_decision = models.CharField(max_length=16, null=True, blank=True, db_index=True,
                                                help_text="Phase 4 Layer B published decision (WAIT while unauthorized)")

    # Path B: XAUUSD Dual-Side Scores (Phase 7 additive)
    long_direction_score = models.FloatField(null=True, blank=True, help_text="XAUUSD Long Direction Score (0-100)")
    short_direction_score = models.FloatField(null=True, blank=True, help_text="XAUUSD Short Direction Score (0-100)")
    long_timing_score = models.FloatField(null=True, blank=True, help_text="XAUUSD Long Timing Score (0-100)")
    short_timing_score = models.FloatField(null=True, blank=True, help_text="XAUUSD Short Timing Score (0-100)")

    # Path B: XAUUSD Provenance & Calibration (Phase 7 additive)
    candidate_resolution_reason = models.CharField(max_length=255, null=True, blank=True)
    publication_reason = models.CharField(max_length=255, null=True, blank=True)
    profile_name = models.CharField(max_length=64, null=True, blank=True)
    calibration_status = models.CharField(max_length=64, null=True, blank=True)
    phase4_policy_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    analysis_fingerprint = models.CharField(max_length=64, null=True, blank=True)

    # Path B: Decision Fields (Phase 5 Risk)
    risk_plan_valid = models.BooleanField(default=False)
    execution_eligible = models.BooleanField(default=False)
    effective_action = models.CharField(
        max_length=16,
        default="WAIT",
        db_index=True,
        help_text="Primary user-facing operational decision: BUY, WAIT, AVOID",
    )

    # Path B: XAUUSD Side-Aware Risk (Phase 7 additive)
    risk_side = models.CharField(max_length=16, null=True, blank=True,
                                 help_text="LONG or SHORT for active risk plan")
    risk_candidate_status = models.CharField(max_length=32, null=True, blank=True,
                                              help_text="e.g. VALID, INVALID_GEOMETRY, NO_CANDIDATE")
    candidate_effective_action = models.CharField(max_length=16, null=True, blank=True,
                                                   help_text="Layer A effective action (BUY, SELL, WAIT)")
    publication_effective_action = models.CharField(max_length=16, null=True, blank=True,
                                                     help_text="Layer B effective action (always WAIT in Phase 7)")

    # Risk geometry
    entry_min = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_mid = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_max = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_structure = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_atr = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_final = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_distance_atr = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    tp1 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tp2 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    rr_tp1 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    rr_tp2 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

    # Risk fingerprints (Phase 7 additive)
    risk_plan_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    source_phase4_fingerprint = models.CharField(max_length=64, null=True, blank=True)

    # Explainability & Provenance
    reasons_positive = models.JSONField(default=list, blank=True)
    reasons_negative = models.JSONField(default=list, blank=True)
    hard_gate_reasons = models.JSONField(default=list, blank=True)
    feed_health_data = models.JSONField(default=dict, blank=True)

    # Provenance Signatures (P7-C6)
    engine_version = models.CharField(max_length=32, default="4.0.0")
    config_version = models.CharField(max_length=32, default="cfg-2026-v1")
    feature_version = models.CharField(max_length=32, default="feat-2026-v1")
    cycle_version = models.CharField(max_length=32, default="3.0.0-3A")
    risk_version = models.CharField(max_length=32, default="5.0.0")
    code_revision = models.CharField(max_length=40, default="15d388d1")
    decision_sequence = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "live_monitor_states"
        verbose_name = "Live Monitor State"
        verbose_name_plural = "Live Monitor States"

    def __str__(self) -> str:
        return f"LiveMonitorState({self.instrument}: {self.signal_state}/{self.signal_user_decision} -> Effective:{self.effective_action} @ ASK {self.current_ask})"


class LiveRiskPlanRecord(models.Model):
    """
    Immutable historical audit record of Phase 5 Risk Plans generated in live pipeline (P7-C3).
    Linked 1-to-1 with Phase 4 SignalRecord via source_signal_fingerprint.

    XAUUSD Phase 7 Addendum:
      Preserves full SideRiskPlanSnapshot semantics including side, candidate/publication
      effective actions, zone provenance fingerprints, and policy fingerprint.
      Evidence-dependent geometry fields are nullable to represent invalid plans without fake prices.
      Historical XAUT records remain readable (new fields are nullable).
    """
    id = models.BigAutoField(primary_key=True)
    source_signal_fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 fingerprint of the triggering Phase 4 SignalRecord",
    )
    signal_timestamp = models.DateTimeField(db_index=True)
    instrument = models.CharField(max_length=32, db_index=True)

    # Side-aware risk (Phase 7 additive — nullable for historical XAUT rows)
    risk_side = models.CharField(max_length=16, null=True, blank=True,
                                 help_text="LONG or SHORT")
    risk_candidate_status = models.CharField(max_length=32, null=True, blank=True,
                                              help_text="Risk candidate evaluation status")
    risk_candidate_valid = models.BooleanField(null=True, blank=True,
                                                help_text="True if risk plan geometry is valid")
    simulation_eligible = models.BooleanField(null=True, blank=True,
                                               help_text="True if simulation/execution eligible")

    # Dual-layer effective actions (Phase 7 additive)
    candidate_effective_action = models.CharField(max_length=16, null=True, blank=True,
                                                   help_text="Layer A: BUY, SELL, or WAIT")
    publication_effective_action = models.CharField(max_length=16, null=True, blank=True,
                                                     help_text="Layer B: always WAIT in Phase 7")

    # Entry Zone (nullable for invalid plans)
    entry_min = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_mid = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_max = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    # Stop Loss (nullable for invalid plans)
    stop_structure = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_atr = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_final = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_distance_atr = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

    # Targets (nullable for invalid plans)
    tp1 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tp2 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    rr_tp1 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                 help_text="Alias for planned_rr_tp1")
    rr_tp2 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                 help_text="Alias for planned_rr_tp2")

    # Status & authority (historical fields preserved)
    is_valid_risk_plan = models.BooleanField(db_index=True)
    execution_eligible = models.BooleanField(db_index=True)
    effective_action = models.CharField(max_length=16, db_index=True)
    reasons = models.JSONField(default=list, blank=True)

    # Zone provenance fingerprints (Phase 7 additive)
    entry_zone_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    tp1_zone_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    tp2_zone_fingerprint = models.CharField(max_length=64, null=True, blank=True)

    # Deterministic fingerprints
    source_zone_id = models.CharField(max_length=128, null=True, blank=True)
    source_zone_timestamp = models.DateTimeField(null=True, blank=True)
    phase5_policy_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    risk_plan_fingerprint = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Immutable unique identity of the Phase 5 Risk Plan",
    )
    source_phase4_fingerprint = models.CharField(max_length=64, null=True, blank=True,
                                                  help_text="Alias for source_signal_fingerprint for side-aware records")

    risk_version = models.CharField(max_length=32, default="5.0.0")
    execution_model_version = models.CharField(max_length=32, default="5.0.0-exec-v1")
    config_version = models.CharField(max_length=32, default="cfg-2026-v1")
    code_revision = models.CharField(max_length=40)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "live_risk_plan_records"
        ordering = ["-signal_timestamp"]
        indexes = [
            models.Index(fields=["instrument", "-signal_timestamp"]),
            models.Index(fields=["effective_action"]),
            models.Index(fields=["is_valid_risk_plan", "execution_eligible"]),
            models.Index(fields=["risk_side"]),
        ]

    def __str__(self) -> str:
        status = "VALID" if self.is_valid_risk_plan else "INVALID"
        side = f" {self.risk_side}" if self.risk_side else ""
        return f"LiveRiskPlanRecord({self.instrument}{side} @ {self.signal_timestamp.isoformat()}: {status} -> {self.effective_action})"
