"""Django ORM models for immutable backtest audit records (Phase 6)."""
from decimal import Decimal
from django.db import models


class BacktestRun(models.Model):
    """
    Immutable audit record of a point-in-time or walk-forward backtest run.

    Strict Invariants:
      1. run_fingerprint is UNIQUE: Identical inputs retrieve existing record (idempotency).
      2. Material config/dataset/code changes generate a distinct run.
      3. Completed runs are append-only and never overwritten in place.
    """
    id = models.BigAutoField(primary_key=True)
    run_fingerprint = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Canonical SHA-256 fingerprint of the backtest specification",
    )
    instrument = models.CharField(max_length=30, default="XAUT/USDT", db_index=True)
    dataset_identity = models.CharField(max_length=64, help_text="SHA-256 hash or identifier of historical dataset")
    historical_start = models.DateTimeField(db_index=True)
    historical_end = models.DateTimeField(db_index=True)

    engine_version = models.CharField(max_length=30, default="4.0.0")
    config_version = models.CharField(max_length=30, default="cfg-2026-v1")
    feature_version = models.CharField(max_length=30, default="feat-2026-v1")
    cycle_version = models.CharField(max_length=30, default="3.0.0-3A")
    risk_version = models.CharField(max_length=30, default="5.0.0")
    execution_model_version = models.CharField(max_length=30, default="5.0.0-exec-v1")
    backtest_version = models.CharField(max_length=30, default="6.0.0")
    code_revision = models.CharField(max_length=40, help_text="Git commit SHA or frozen baseline revision")

    cost_config = models.JSONField(default=dict, help_text="Friction parameters (spread, fees, slippage)")
    walkforward_config = models.JSONField(default=dict, help_text="Folds, ratios, purge, and embargo specifications")
    purge_policy = models.CharField(max_length=50, default="EXACT_DEPENDENCY_PURGE")
    embargo_policy = models.CharField(max_length=50, default="POST_BOUNDARY_BUFFER")
    ablation_id = models.CharField(max_length=50, default="BASELINE", db_index=True)

    aggregate_metrics = models.JSONField(default=dict, help_text="Funnel, expectancy, PF, and drawdown metrics")
    temporal_stability = models.JSONField(default=dict, help_text="Cross-fold OOS stability and variance breakdown")

    status = models.CharField(
        max_length=20,
        default="COMPLETED",
        db_index=True,
        choices=[
            ("PENDING", "Pending"),
            ("RUNNING", "Running"),
            ("COMPLETED", "Completed"),
            ("FAILED", "Failed"),
        ],
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "backtest_runs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["instrument", "ablation_id", "created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["code_revision"]),
        ]

    def __str__(self) -> str:
        return f"BacktestRun({self.instrument} [{self.ablation_id}] {self.run_fingerprint[:12]} - {self.status})"


class BacktestTrade(models.Model):
    """
    Immutable trade ledger entry simulated in a point-in-time backtest.
    Supports both historical XAUT records and side-aware XAUUSD records.
    """
    id = models.BigAutoField(primary_key=True)
    backtest_run = models.ForeignKey(
        BacktestRun,
        on_delete=models.CASCADE,
        related_name="trades",
    )
    trade_id = models.CharField(max_length=64, db_index=True)
    side = models.CharField(max_length=10, default="LONG", null=True, blank=True, db_index=True)
    candidate_state = models.CharField(max_length=40, null=True, blank=True)
    candidate_decision = models.CharField(max_length=20, null=True, blank=True)
    source_signal_fingerprint = models.CharField(max_length=64, db_index=True)
    risk_plan_fingerprint = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    execution_evidence_fingerprint = models.CharField(max_length=64, null=True, blank=True)

    signal_timestamp = models.DateTimeField(db_index=True)
    dependency_end_timestamp = models.DateTimeField(db_index=True)
    fill_timestamp = models.DateTimeField(null=True, blank=True)
    fill_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    exit_timestamp = models.DateTimeField(null=True, blank=True)
    exit_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    outcome = models.CharField(max_length=30, db_index=True)
    planned_risk_amount = models.DecimalField(max_digits=12, decimal_places=4)
    gross_r = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    net_r = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    gross_return_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    net_return_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    mfe_r = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    mae_r = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    entry_fee = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0"))
    exit_fee = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0"))
    entry_spread = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0"))
    exit_spread = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0"))
    entry_slippage = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0"))
    exit_slippage = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0"))

    fold_id = models.IntegerField(null=True, blank=True)
    ambiguity_policy = models.CharField(max_length=40, default="LOWER_TIMEFRAME_REPLAY")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "backtest_trades"
        ordering = ["signal_timestamp"]
        unique_together = [("backtest_run", "trade_id")]
        indexes = [
            models.Index(fields=["backtest_run", "outcome"]),
            models.Index(fields=["backtest_run", "side"]),
            models.Index(fields=["signal_timestamp", "dependency_end_timestamp"]),
        ]

    def __str__(self) -> str:
        return f"BacktestTrade({self.trade_id}: [{self.side}] {self.outcome} NetR={self.net_r})"
