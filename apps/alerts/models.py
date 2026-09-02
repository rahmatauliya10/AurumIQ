"""Models for AlertEvent persistence, outbox dispatch, and deduplication (Phase 7)."""
from django.db import models


class AlertStatus(models.TextChoices):
    PENDING = "PENDING", "Pending Dispatch"
    DISPATCHED = "DISPATCHED", "Dispatched"
    SUPPRESSED = "SUPPRESSED", "Suppressed by Safety Policy"
    FAILED = "FAILED", "Dispatch Failed"
    DISABLED = "DISABLED", "Transport Disabled / Not Configured"


class AlertEvent(models.Model):
    """
    Immutable audit record of generated informational alerts.
    
    Strict Invariants:
      1. Unique event_id guarantees zero duplicate emissions (idempotency).
      2. Separates alert generation from transport dispatch (outbox pattern).
      3. Never contains order execution parameters.
    """
    id = models.BigAutoField(primary_key=True)
    event_id = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Deterministic SHA-256 or structured idempotency key",
    )
    event_type = models.CharField(max_length=64, db_index=True)
    instrument = models.CharField(max_length=32, default="XAUUSD", db_index=True)
    display_symbol = models.CharField(max_length=32, default="XAU/USD")

    # State Context
    candidate_state = models.CharField(max_length=32, default="NO_TRADE")
    candidate_user_decision = models.CharField(max_length=16, default="WAIT")
    published_state = models.CharField(max_length=32, default="NO_TRADE")
    published_user_decision = models.CharField(max_length=16, default="WAIT")
    side = models.CharField(max_length=16, null=True, blank=True)

    # Prices & Geometry
    bid = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    ask = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_min = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    entry_max = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    stop_final = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tp1 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tp2 = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    planned_rr_tp1 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

    # Timestamps
    analysis_timestamp = models.DateTimeField(null=True, blank=True, db_index=True)
    quote_timestamp = models.DateTimeField(null=True, blank=True)

    # Fingerprints
    analysis_fingerprint = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    risk_plan_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    calibration_status = models.CharField(max_length=64, default="CALIBRATION_REQUIRED")

    # Diagnostics & Payload
    hard_gate_reasons = models.JSONField(default=list, blank=True)
    reasons = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, help_text="Full canonical JSON payload")
    disclaimer = models.CharField(
        max_length=255,
        default="MANUAL DECISION SUPPORT ONLY — NO AUTO-ORDER EXECUTION.",
    )

    # Outbox & Dispatch Tracking
    status = models.CharField(
        max_length=32,
        choices=AlertStatus.choices,
        default=AlertStatus.PENDING,
        db_index=True,
    )
    dispatch_attempts = models.IntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    dispatch_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "alert_events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["instrument", "-created_at"]),
            models.Index(fields=["event_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"AlertEvent({self.event_id}: {self.event_type} for {self.instrument} [{self.status}])"
