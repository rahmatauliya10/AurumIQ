"""
Phase 6 / Backtest Lab Governance Guard for Protected Partitions.

Authoritative boundary resolver and hard validator enforcing strict isolation of:
1. Approved Research / In-Sample Partition: [historical_start, earliest_oos_start)
2. Protected Walk-Forward Out-Of-Sample (OOS) Partition: [earliest_oos_start, latest_oos_end)
3. Quarantined Post-Calibration Gap: [latest_oos_end, observation_window_start)
4. Protected Phase 8 / Post-Remediation Observation Partition: [observation_window_start, +inf)

Invariants:
  - Canonical selection policy is the authoritative single source of truth.
  - Zero MarketCandle queries during policy resolution.
  - Any intersection between requested window and protected partitions causes IMMEDIATE rejection.
  - Celery task independently re-validates before any MarketCandle query (defense-in-depth).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence, Tuple


class BacktestGovernanceError(ValueError):
    """Raised when a requested backtest window violates governance boundaries."""
    pass


class NoApprovedResearchWindowError(BacktestGovernanceError):
    """Raised when no approved research window can be resolved from canonical policy."""
    pass


_dynamic_observation_resolver: Optional[Callable[[], Optional[datetime]]] = None


def register_dynamic_observation_resolver(
    resolver: Optional[Callable[[], Optional[datetime]]]
) -> None:
    """Register runtime observation boundary provider without engine importing apps/django."""
    global _dynamic_observation_resolver
    _dynamic_observation_resolver = resolver


@dataclass(frozen=True)
class PartitionWindow:
    """Immutable representation of a half-open [start, end) time partition."""
    name: str
    start: datetime
    end: datetime
    description: str

    def intersects(self, req_start: datetime, req_end: datetime) -> bool:
        """
        Deterministic half-open interval intersection test:
        [req_start, req_end) ∩ [self.start, self.end) != ∅ <=> req_start < self.end and req_end > self.start
        """
        return req_start < self.end and req_end > self.start

    def contains(self, req_start: datetime, req_end: datetime) -> bool:
        """
        Deterministic containment test:
        [req_start, req_end) ⊆ [self.start, self.end) <=> req_start >= self.start and req_end <= self.end
        """
        return req_start >= self.start and req_end <= self.end


@dataclass(frozen=True)
class BacktestGovernanceSpec:
    """Canonical governance specification for Backtest Lab."""
    policy_id: str
    is_approved_configured: bool
    approved_window: Optional[PartitionWindow]
    protected_partitions: Tuple[PartitionWindow, ...]
    error_code: Optional[str] = None


def _to_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"Naive datetime rejected in backtest governance: {dt}")
    return dt.astimezone(timezone.utc)


def resolve_backtest_governance_spec(
    policy=None,
    dynamic_observation_start: Optional[datetime] = None,
) -> BacktestGovernanceSpec:
    """
    Resolve authoritative governance partitions from canonical policy and dynamic observation state.
    Strictly forbids querying MarketCandle.
    """
    if policy is None:
        try:
            from engine.backtest.xauusd_calibration_policy import load_governed_selection_policy
            policy = load_governed_selection_policy()
        except Exception as e:
            return BacktestGovernanceSpec(
                policy_id="UNRESOLVED",
                is_approved_configured=False,
                approved_window=None,
                protected_partitions=(),
                error_code="NO_APPROVED_RESEARCH_WINDOW_CONFIGURED",
            )

    try:
        hist_start = _to_utc(policy.historical_start)
        hist_end_exclusive = _to_utc(policy.historical_end_exclusive)

        # Extract OOS boundaries across all walk-forward folds
        fold_oos_starts = []
        fold_oos_ends = []
        for fold in getattr(policy, "folds", ()):
            if isinstance(fold, dict):
                if "oos_start" in fold:
                    fold_oos_starts.append(_to_utc(datetime.fromisoformat(fold["oos_start"])))
                if "oos_end" in fold:
                    fold_oos_ends.append(_to_utc(datetime.fromisoformat(fold["oos_end"])))

        if not fold_oos_starts or not fold_oos_ends:
            return BacktestGovernanceSpec(
                policy_id=getattr(policy, "policy_id", "UNKNOWN"),
                is_approved_configured=False,
                approved_window=None,
                protected_partitions=(),
                error_code="NO_APPROVED_RESEARCH_WINDOW_CONFIGURED",
            )

        earliest_oos_start = min(fold_oos_starts)
        latest_oos_end = max(fold_oos_ends)

        if hist_start >= earliest_oos_start:
            return BacktestGovernanceSpec(
                policy_id=policy.policy_id,
                is_approved_configured=False,
                approved_window=None,
                protected_partitions=(),
                error_code="NO_APPROVED_RESEARCH_WINDOW_CONFIGURED",
            )

        # 1. Approved Research / In-Sample Partition: [historical_start, earliest_oos_start)
        approved_window = PartitionWindow(
            name="APPROVED_RESEARCH_IN_SAMPLE",
            start=hist_start,
            end=earliest_oos_start,
            description="Frozen in-sample training and validation partition (Amendment 7)",
        )

        # 2. Protected Walk-Forward Out-Of-Sample (OOS) Partition: [earliest_oos_start, latest_oos_end)
        oos_partition = PartitionWindow(
            name="PROTECTED_WALKFORWARD_OOS",
            start=earliest_oos_start,
            end=latest_oos_end,
            description="Protected walk-forward out-of-sample partitions across folds 1-5",
        )

        # 3. Quarantined Calibration Gap Partition: [latest_oos_end, phase8_observation_start)
        p8_policy_start = _to_utc(policy.phase8_observation_window_start)
        quarantine_partition = PartitionWindow(
            name="QUARANTINED_CALIBRATION_GAP",
            start=latest_oos_end,
            end=p8_policy_start,
            description="Quarantined post-calibration gap excluded from research",
        )

        # 4. Protected Phase 8 / Post-Remediation Observation Partition: [obs_start, +inf)
        if dynamic_observation_start is not None:
            effective_obs_start = min(p8_policy_start, _to_utc(dynamic_observation_start))
        elif _dynamic_observation_resolver is not None:
            try:
                dyn = _dynamic_observation_resolver()
                if dyn is not None:
                    effective_obs_start = min(p8_policy_start, _to_utc(dyn))
                else:
                    effective_obs_start = p8_policy_start
            except Exception:
                effective_obs_start = p8_policy_start
        else:
            effective_obs_start = p8_policy_start

        unbounded_future = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        observation_partition = PartitionWindow(
            name="PROTECTED_OBSERVATION_PARTITION",
            start=effective_obs_start,
            end=unbounded_future,
            description="Protected Phase 8 post-remediation live observation partition",
        )

        protected_partitions = (
            oos_partition,
            quarantine_partition,
            observation_partition,
        )

        return BacktestGovernanceSpec(
            policy_id=policy.policy_id,
            is_approved_configured=True,
            approved_window=approved_window,
            protected_partitions=protected_partitions,
            error_code=None,
        )
    except Exception as e:
        return BacktestGovernanceSpec(
            policy_id=getattr(policy, "policy_id", "UNKNOWN"),
            is_approved_configured=False,
            approved_window=None,
            protected_partitions=(),
            error_code="NO_APPROVED_RESEARCH_WINDOW_CONFIGURED",
        )


def validate_backtest_window_governance(
    start_dt: datetime,
    end_dt: datetime,
    spec: Optional[BacktestGovernanceSpec] = None,
) -> None:
    """
    Validate that requested [start_dt, end_dt) window strictly respects governed partitions.

    Invariants:
      1. Approved research window must be configured and resolvable.
      2. Requested window must not intersect ANY protected partition:
         REQUESTED_WINDOW ∩ PROTECTED_WINDOW != ∅ => REJECT BEFORE TASK CREATION => ZERO DATA ACCESS.
      3. Requested window must be fully contained within the approved research partition:
         Only explicitly approved research/in-sample partitions may be used.
    """
    start_utc = _to_utc(start_dt)
    end_utc = _to_utc(end_dt)

    if end_utc <= start_utc:
        raise ValueError("end_date must be strictly greater than start_date.")

    if spec is None:
        spec = resolve_backtest_governance_spec()

    if not spec.is_approved_configured or spec.approved_window is None:
        raise NoApprovedResearchWindowError(
            spec.error_code or "NO_APPROVED_RESEARCH_WINDOW_CONFIGURED"
        )

    # Invariant 2: Reject if window intersects any protected partition
    for part in spec.protected_partitions:
        if part.intersects(start_utc, end_utc):
            raise BacktestGovernanceError(
                "Requested historical window intersects a protected research partition."
            )

    # Invariant 3: Reject if window is not fully contained in approved research partition
    if not spec.approved_window.contains(start_utc, end_utc):
        raise BacktestGovernanceError(
            "Requested historical window is outside the approved research partition."
        )
