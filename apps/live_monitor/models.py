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
    """
    instrument = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Canonical instrument symbol (e.g. XAUT/USDT)",
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

    # Path B: Decision Fields (Phase 4 Signal)
    last_closed_candle_ts = models.DateTimeField(null=True, blank=True, db_index=True)
    last_analysis_timestamp = models.DateTimeField(null=True, blank=True)
    signal_fingerprint = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    signal_state = models.CharField(max_length=32, default="NO_TRADE", db_index=True)
    signal_user_decision = models.CharField(max_length=16, default="WAIT", db_index=True)
    direction_score = models.FloatField(default=0.0)
    timing_score = models.FloatField(default=0.0)

    # Path B: Decision Fields (Phase 5 Risk)
    risk_plan_valid = models.BooleanField(default=False)
    execution_eligible = models.BooleanField(default=False)
    effective_action = models.CharField(
        max_length=16,
        default="WAIT",
        db_index=True,
        help_text="Primary user-facing operational decision: BUY, WAIT, AVOID",
    )
    entry_min = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_mid = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_max = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_final = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tp1 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tp2 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    rr_tp1 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    rr_tp2 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

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
    """
    id = models.BigAutoField(primary_key=True)
    source_signal_fingerprint = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 fingerprint of the triggering Phase 4 SignalRecord",
    )
    signal_timestamp = models.DateTimeField(db_index=True)
    instrument = models.CharField(max_length=32, db_index=True)

    entry_min = models.DecimalField(max_digits=18, decimal_places=4)
    entry_mid = models.DecimalField(max_digits=18, decimal_places=4)
    entry_max = models.DecimalField(max_digits=18, decimal_places=4)

    stop_structure = models.DecimalField(max_digits=18, decimal_places=4)
    stop_atr = models.DecimalField(max_digits=18, decimal_places=4)
    stop_final = models.DecimalField(max_digits=18, decimal_places=4)
    stop_distance_atr = models.DecimalField(max_digits=8, decimal_places=4)

    tp1 = models.DecimalField(max_digits=18, decimal_places=4)
    tp2 = models.DecimalField(max_digits=18, decimal_places=4)
    rr_tp1 = models.DecimalField(max_digits=8, decimal_places=4)
    rr_tp2 = models.DecimalField(max_digits=8, decimal_places=4)

    is_valid_risk_plan = models.BooleanField(db_index=True)
    execution_eligible = models.BooleanField(db_index=True)
    effective_action = models.CharField(max_length=16, db_index=True)
    reasons = models.JSONField(default=list, blank=True)

    source_zone_id = models.CharField(max_length=128, null=True, blank=True)
    source_zone_timestamp = models.DateTimeField(null=True, blank=True)

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
        ]

    def __str__(self) -> str:
        status = "VALID" if self.is_valid_risk_plan else "INVALID"
        return f"LiveRiskPlanRecord({self.instrument} @ {self.signal_timestamp.isoformat()}: {status} -> {self.effective_action})"
