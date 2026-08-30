"""Signal models and immutable audit persistence (Phase 4)."""
from django.db import models


class SignalRecord(models.Model):
    """
    Immutable historical audit record of generated trading signals.

    Invariant A03 & A08:
      - Unique constraint on analysis_fingerprint guarantees zero duplicate records.
      - Never overwritten; historical signals are append-only.
    """
    id = models.BigAutoField(primary_key=True)
    analysis_fingerprint = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Deterministic SHA-256 hash of canonical production inputs",
    )
    instrument = models.ForeignKey(
        "instruments.Instrument",
        on_delete=models.CASCADE,
        related_name="signal_records",
    )
    timeframe = models.CharField(max_length=10, db_index=True)
    timestamp = models.DateTimeField(db_index=True, help_text="Timestamp of closed candle analyzed")
    state = models.CharField(max_length=20, db_index=True)
    user_decision = models.CharField(max_length=10, db_index=True)
    direction_score = models.FloatField()
    timing_score = models.FloatField()

    reasons_positive = models.JSONField(default=list)
    reasons_negative = models.JSONField(default=list)
    hard_gate_reasons = models.JSONField(default=list)
    components_breakdown = models.JSONField(default=dict)
    provenance = models.JSONField(default=dict)

    research_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    engine_version = models.CharField(max_length=30, default="4.0.0")
    config_version = models.CharField(max_length=30, default="cfg-2026-v1")
    feature_version = models.CharField(max_length=30, default="feat-2026-v1")
    cycle_version = models.CharField(max_length=30, default="3.0.0-3A")
    code_revision = models.CharField(max_length=40)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "signal_records"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "timestamp"]),
            models.Index(fields=["user_decision", "state"]),
            models.Index(fields=["config_version"]),
        ]

    def __str__(self) -> str:
        return f"SignalRecord({self.instrument.symbol} {self.timeframe} {self.timestamp.isoformat()}: {self.state} -> {self.user_decision})"
