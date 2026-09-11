"""Phase 8 Live Paper Observation Service.

Coordinates runtime paper execution, immutable PaperObservationRecord persistence,
and 14-day operational continuity tracking.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional, Sequence, Tuple

import structlog
from django.db import transaction

from apps.live_monitor.models import (
    LiveRiskPlanRecord,
    PaperObservationRecord,
    Phase8InterruptionRecord,
    Phase8OperationalState,
)
from apps.market_data.friction.resolution import resolve_friction_model
from apps.market_data.models import MarketCandle
from apps.signals.models import SignalRecord
from engine.core.types import CandleData
from engine.paper.continuity import (
    Phase8ContinuityTracker,
    is_expected_market_closure,
)
from engine.paper.guards import assert_paper_execution_safety
from engine.paper.observer import Phase8PaperObserver
from engine.paper.types import InterruptionCategory, Phase8Status


logger = structlog.get_logger(__name__)


class ObservationResult(tuple):
    """
    Tuple-compatible result of process_production_signal_observation: (record, status).
    Provides backward compatibility for code treating return value as record directly:
      obs, status = process(...)  # Unpackable as 2-tuple
      obs = process(...)          # Usable directly as PaperObservationRecord
      obs.observation_id          # Proxies to record
      obs.status                  # "DUPLICATE_NOOP" or "OBSERVED_BUY_..."
    """
    def __new__(cls, record: PaperObservationRecord, status: str):
        return super().__new__(cls, (record, status))

    @property
    def record(self) -> PaperObservationRecord:
        return self[0]

    @property
    def status(self) -> str:
        return self[1]

    def __getattr__(self, name: str) -> Any:
        return getattr(self[0], name)


def _candle_to_core(c: MarketCandle) -> CandleData:
    return CandleData(
        timestamp_open=c.timestamp_open,
        timestamp_close=c.timestamp_close,
        open=c.open,
        high=c.high,
        low=c.low,
        close=c.close,
        volume=c.volume,
        is_closed=c.is_closed,
        quote_rate=c.quote_rate,
        close_usd=c.close_usd,
        source_id=c.source,
    )


class Phase8PaperService:
    """
    Authoritative service orchestrating Phase 8 Live Paper Observation.

    Strict Invariants:
      1. Zero Real Orders: Structural safety assertion enforced on every execution step.
      2. Authoritative Signal Truth: Consumes existing SignalRecord and LiveRiskPlanRecord.
         Does NOT execute an independent signal generation pipeline.
      3. Point-in-Time Active Friction: Resolves EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1
         via resolve_friction_model.
      4. Zero Lookahead: Only consumes future candles with timestamp_close > decision_timestamp.
      5. Position-Size Guarded PnL: Monetary PnL is null/NOT_EVALUATED unless explicit lots supplied.
      6. Continuity Tracking: 14 calendar days required. EXPECTED_MARKET_CLOSURE never interrupts phase.
      7. Idempotent Observations: created=False is treated as DUPLICATE_NOOP; never increments signals
         evaluated or refreshes evaluation timestamps.
    """

    @classmethod
    def get_or_create_operational_state(cls) -> Phase8OperationalState:
        """Get or initialize the singleton Phase 8 operational state."""
        assert_paper_execution_safety("get_state")
        state, _ = Phase8OperationalState.objects.get_or_create(
            id=1,
            defaults={
                "status": Phase8Status.NOT_STARTED.value,
                "observation_day": 0,
                "completed_calendar_days": 0,
                "paper_only": True,
                "real_order_execution": "disabled",
            },
        )
        return state

    @classmethod
    def get_future_candles(
        cls,
        instrument_id: int,
        timeframe: str,
        after_timestamp: datetime,
        limit: int = 128,
    ) -> List[CandleData]:
        """Fetch strictly future closed candles after decision timestamp T."""
        qs = (
            MarketCandle.objects.filter(
                instrument_id=instrument_id,
                timeframe=timeframe,
                timestamp_close__gt=after_timestamp,
                is_closed=True,
            )
            .order_by("timestamp_close")[:limit]
        )
        return [_candle_to_core(c) for c in qs]

    @classmethod
    def record_interruption(
        cls,
        category: InterruptionCategory,
        description: str = "",
        timestamp: Optional[datetime] = None,
        duration_seconds: Optional[float] = None,
    ) -> Phase8InterruptionRecord:
        """Record an interruption event and update operational state."""
        assert_paper_execution_safety("record_interruption")
        event_ts = timestamp or datetime.now(timezone.utc)
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)

        is_failure = bool(category != InterruptionCategory.EXPECTED_MARKET_CLOSURE and not is_expected_market_closure(event_ts))

        with transaction.atomic():
            rec = Phase8InterruptionRecord.objects.create(
                timestamp=event_ts,
                category=category.value,
                description=description,
                is_operational_failure=is_failure,
                duration_seconds=duration_seconds,
                resolved=True,
            )

            state = cls.get_or_create_operational_state()
            if is_failure:
                state.runtime_errors_count += 1
                state.unresolved_integrity_failures += 1
                state.status = Phase8Status.INTERRUPTED.value
                state.save()

            return rec

    @classmethod
    def process_production_signal_observation(
        cls,
        signal_record: SignalRecord,
        risk_record: Optional[LiveRiskPlanRecord] = None,
        paper_volume_lots: Optional[Decimal] = None,
        pnl_currency: str = "USC",
    ) -> ObservationResult:
        """
        Process a production SignalRecord into an immutable PaperObservationRecord.
        Returns ObservationResult(record, status), where status is DUPLICATE_NOOP if
        already observed, or OBSERVED_{side}_{outcome} if newly recorded.
        """
        assert_paper_execution_safety("process_signal")

        # 1. Resolve active empirical friction model point-in-time
        friction_model = resolve_friction_model(
            as_of=signal_record.timestamp,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            legal_entity_code="EXNESS_SC_LTD",
        )

        # 2. Fetch strictly future closed candles after decision timestamp T
        inst_id = signal_record.instrument_id
        decision_ts = signal_record.timestamp

        future_15m = cls.get_future_candles(inst_id, "15m", decision_ts, limit=64)
        future_5m = cls.get_future_candles(inst_id, "5m", decision_ts, limit=128)
        future_1m = cls.get_future_candles(inst_id, "1m", decision_ts, limit=300)

        # 3. Execute paper observer
        observer = Phase8PaperObserver()
        obs_snap = observer.observe_production_setup(
            signal_record=signal_record,
            risk_record=risk_record,
            future_candles_15m=future_15m,
            future_candles_5m=future_5m,
            future_candles_1m=future_1m,
            friction_model=friction_model,
            paper_volume_lots=paper_volume_lots,
            pnl_currency=pnl_currency,
        )

        # 4. Persist PaperObservationRecord idempotently
        with transaction.atomic():
            rec, created = PaperObservationRecord.objects.get_or_create(
                observation_fingerprint=obs_snap.observation_fingerprint,
                defaults={
                    "observation_id": obs_snap.observation_id,
                    "source_signal_record": signal_record,
                    "source_risk_plan_record": risk_record,
                    "source_signal_fingerprint": obs_snap.source_signal_fingerprint,
                    "source_risk_plan_fingerprint": obs_snap.source_risk_plan_fingerprint,
                    "instrument": obs_snap.instrument,
                    "broker_symbol": obs_snap.broker_symbol,
                    "side": obs_snap.side,
                    "decision_timeframe": obs_snap.decision_timeframe,
                    "decision_timestamp": obs_snap.decision_timestamp,
                    "decision_candle_close": obs_snap.decision_candle_close,
                    "friction_model_version_id": obs_snap.friction_model_version_id,
                    "friction_evidence_fingerprint": obs_snap.friction_evidence_fingerprint,
                    "dataset_readiness_fingerprint": obs_snap.dataset_readiness_fingerprint,
                    "paper_entry_timestamp": obs_snap.paper_entry_timestamp,
                    "paper_entry_price_basis": obs_snap.paper_entry_price_basis,
                    "spread_friction_applied": obs_snap.spread_friction_applied,
                    "commission_friction_applied": obs_snap.commission_friction_applied,
                    "financing_friction_applied": obs_snap.financing_friction_applied,
                    "paper_exit_timestamp": obs_snap.paper_exit_timestamp,
                    "paper_exit_price": obs_snap.paper_exit_price,
                    "exit_reason": obs_snap.exit_reason,
                    "outcome": obs_snap.outcome,
                    "mfe_r": obs_snap.mfe_r,
                    "mae_r": obs_snap.mae_r,
                    "gross_r": obs_snap.gross_r,
                    "net_r": obs_snap.net_r,
                    "holding_duration_seconds": obs_snap.holding_duration_seconds,
                    "paper_volume_lots": obs_snap.paper_volume_lots,
                    "pnl_currency": obs_snap.pnl_currency,
                    "gross_pnl": obs_snap.gross_pnl,
                    "net_pnl": obs_snap.net_pnl,
                    "signal_decision": obs_snap.signal_decision,
                    "signal_score": obs_snap.signal_score,
                    "engine_version": signal_record.engine_version,
                    "config_version": signal_record.config_version,
                    "code_revision": signal_record.code_revision,
                },
            )

            # 5. Update Operational State
            state = cls.get_or_create_operational_state()
            now_utc = datetime.now(timezone.utc)

            if state.observation_window_start is None:
                state.observation_window_start = now_utc
                state.status = Phase8Status.OBSERVING.value
                state.observation_day = 1
                state.completed_calendar_days = 0
                state.unresolved_integrity_failures = 0
                state.runtime_errors_count = 0

            tracker = Phase8ContinuityTracker(
                window_start=state.observation_window_start,
                unresolved_failures=state.unresolved_integrity_failures,
                current_status=Phase8Status(state.status),
                eligible_cycles_expected=state.eligible_cycles_expected,
                eligible_cycles_observed=state.eligible_cycles_observed,
                eligible_cycles_missing=state.eligible_cycles_missing,
                last_successful_evaluation=state.last_successful_evaluation_timestamp,
            )
            day_idx, comp_days, _ = tracker.calculate_day_progress(now_utc)
            state.observation_day = day_idx
            state.completed_calendar_days = comp_days

            if not created:
                # Duplicate observation must remain strictly idempotent:
                # - does NOT increment signals evaluated
                # - does NOT increment eligible cycles observed
                # - does NOT refresh last_successful_evaluation_timestamp
                # - does NOT update last_processed_eligible_close
                state.duplicate_noop_cycles += 1
                state.save()
                return ObservationResult(rec, "DUPLICATE_NOOP")

            # New observation: record progression
            state.total_signals_evaluated += 1
            state.eligible_cycles_observed += 1
            state.eligible_cycles_expected = max(state.eligible_cycles_expected, state.eligible_cycles_observed)
            state.last_successful_evaluation_timestamp = now_utc
            state.last_processed_eligible_close = rec.decision_candle_close
            state.last_expected_eligible_close = rec.decision_candle_close

            if obs_snap.side == "BUY":
                state.buy_count += 1
            elif obs_snap.side == "SELL":
                state.sell_count += 1

            if obs_snap.signal_decision in ("WAIT", "NO_TRADE"):
                state.no_trade_count += 1

            if obs_snap.paper_entry_timestamp is not None:
                state.paper_positions_opened += 1
            if obs_snap.paper_exit_timestamp is not None:
                state.paper_positions_closed += 1
                state.outcomes_resolved += 1
            elif obs_snap.paper_entry_timestamp is not None:
                state.outcomes_pending += 1

            state.data_freshness_status = "HEALTHY"
            state.status = tracker.evaluate_gate_status(
                now_utc,
                eligible_cycles_expected=state.eligible_cycles_expected,
                eligible_cycles_observed=state.eligible_cycles_observed,
                eligible_cycles_missing=state.eligible_cycles_missing,
                last_successful_evaluation=state.last_successful_evaluation_timestamp,
            ).value
            state.save()

            return ObservationResult(rec, f"OBSERVED_{obs_snap.side}_{obs_snap.outcome}")

    @classmethod
    def step_observation_cycle(cls, paper_volume_lots: Optional[Decimal] = None) -> Tuple[int, str]:
        """
        Execute an observation cycle against the latest closed candle and production records.
        Forwards optional paper_volume_lots cleanly through to observation execution.
        """
        assert_paper_execution_safety("step_cycle")
        now_utc = datetime.now(timezone.utc)

        # Check for latest SignalRecord
        latest_sig = SignalRecord.objects.order_by("-timestamp").first()
        if not latest_sig:
            # Check if there are closed 15m candles to evaluate via Phase 7 production pipeline
            latest_candle = (
                MarketCandle.objects.filter(timeframe="15m", is_closed=True)
                .order_by("-timestamp_close")
                .first()
            )
            if latest_candle:
                try:
                    from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
                    from apps.live_monitor.types import CandleClosedEvent

                    event = CandleClosedEvent(
                        event_id=f"closed-15m-{int(latest_candle.timestamp_close.timestamp())}",
                        instrument="XAUUSD",
                        timeframe="15m",
                        timestamp_open=latest_candle.timestamp_open,
                        timestamp_close=latest_candle.timestamp_close,
                        open=latest_candle.open,
                        high=latest_candle.high,
                        low=latest_candle.low,
                        close=latest_candle.close,
                        volume=latest_candle.volume,
                        source="TWELVE_DATA",
                        is_closed=True,
                    )
                    latest_sig, _, _ = XauUsdLiveDecisionPipelineService.process_closed_candle(
                        event=event,
                        code_revision="phase8-live-obs-day1",
                        is_feed_stale=False,
                    )
                except Exception as exc:
                    logger.warning("Auto-evaluating latest candle through Phase 7 failed", exc=str(exc))

        if not latest_sig:
            # Check if market closure
            if is_expected_market_closure(now_utc):
                cls.record_interruption(
                    category=InterruptionCategory.EXPECTED_MARKET_CLOSURE,
                    description="Observation cycle executed during weekend/closed market; no closed candle present.",
                    timestamp=now_utc,
                )
                state = cls.get_or_create_operational_state()
                if state.observation_window_start is None:
                    state.observation_window_start = now_utc
                    state.status = Phase8Status.OBSERVING.value
                    state.observation_day = 1
                    state.save()
                return 0, "EXPECTED_MARKET_CLOSURE"

            # Operational missing signal
            cls.record_interruption(
                category=InterruptionCategory.MISSED_ELIGIBLE_INTERVAL,
                description="Zero SignalRecord found in database during eligible market hours.",
                timestamp=now_utc,
            )
            return 0, "MISSED_ELIGIBLE_INTERVAL"

        # Find matching risk plan
        risk_plan = LiveRiskPlanRecord.objects.filter(
            source_signal_fingerprint=latest_sig.analysis_fingerprint
        ).first()

        res = cls.process_production_signal_observation(
            signal_record=latest_sig,
            risk_record=risk_plan,
            paper_volume_lots=paper_volume_lots,
        )

        status_str = res.status if hasattr(res, "status") else "PROCESSED"
        if status_str == "DUPLICATE_NOOP":
            return 0, "DUPLICATE_NOOP"

        return 1, status_str

    @classmethod
    def audit_operational_continuity(
        cls,
        now_utc: Optional[datetime] = None,
        grace_minutes: int = 30,
    ) -> dict:
        """
        Continuity watchdog audit (Section 5).
        Verifies expected eligible closed 15m decision intervals vs processed observations.
        Exempts configured market closures (Friday 21:00 UTC - Sunday 21:00 UTC).
        Flags market-open eligible intervals past the grace period without observation as MISSED_ELIGIBLE_INTERVAL.
        """
        assert_paper_execution_safety("watchdog_audit")
        eval_time = now_utc or datetime.now(timezone.utc)
        if eval_time.tzinfo is None:
            eval_time = eval_time.replace(tzinfo=timezone.utc)

        state = cls.get_or_create_operational_state()
        if state.status == Phase8Status.NOT_STARTED.value or not state.observation_window_start:
            return {
                "status": state.status,
                "audited": False,
                "reason": "NOT_STARTED",
            }

        window_start = state.observation_window_start

        # Check all closed 15m MarketCandles in the DB since window_start
        closed_candles = list(
            MarketCandle.objects.filter(
                timeframe="15m",
                is_closed=True,
                timestamp_close__gt=window_start,
                timestamp_close__lte=eval_time,
            ).order_by("timestamp_close")
        )

        # Baseline observed count from DB
        actual_obs_count = PaperObservationRecord.objects.count()
        if actual_obs_count > state.eligible_cycles_observed:
            state.eligible_cycles_observed = actual_obs_count

        missing_count = 0
        expected_closure_count = 0

        # Check each closed candle in DB for matching paper observation
        for candle in closed_candles:
            c_close = candle.timestamp_close
            if is_expected_market_closure(c_close):
                expected_closure_count += 1
                continue

            has_obs = PaperObservationRecord.objects.filter(decision_candle_close=c_close).exists()
            if not has_obs:
                grace_limit = c_close + timedelta(minutes=grace_minutes)
                if eval_time >= grace_limit:
                    missing_count += 1

        if expected_closure_count > state.expected_market_closure_cycles:
            state.expected_market_closure_cycles = expected_closure_count

        # Check if any new missing intervals detected
        if missing_count > state.eligible_cycles_missing:
            new_missing = missing_count - state.eligible_cycles_missing
            state.eligible_cycles_missing = missing_count
            cls.record_interruption(
                category=InterruptionCategory.MISSED_ELIGIBLE_INTERVAL,
                description=f"Watchdog detected {new_missing} eligible 15m market-open intervals without paper observation past {grace_minutes}m grace.",
                timestamp=eval_time,
            )
            state.refresh_from_db()
            state.eligible_cycles_missing = missing_count

        tracker = Phase8ContinuityTracker(
            window_start=state.observation_window_start,
            unresolved_failures=state.unresolved_integrity_failures,
            current_status=Phase8Status(state.status),
            eligible_cycles_expected=state.eligible_cycles_expected,
            eligible_cycles_observed=state.eligible_cycles_observed,
            eligible_cycles_missing=state.eligible_cycles_missing,
            last_successful_evaluation=state.last_successful_evaluation_timestamp,
        )
        gate_status = tracker.evaluate_gate_status(eval_time)
        state.status = gate_status.value
        state.save()

        return {
            "status": state.status,
            "audited": True,
            "eligible_cycles_expected": state.eligible_cycles_expected,
            "eligible_cycles_observed": state.eligible_cycles_observed,
            "eligible_cycles_missing": state.eligible_cycles_missing,
            "duplicate_noop_cycles": state.duplicate_noop_cycles,
            "expected_market_closure_cycles": state.expected_market_closure_cycles,
            "unresolved_integrity_failures": state.unresolved_integrity_failures,
        }
