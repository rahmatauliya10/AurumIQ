"""Phase 8 Live Paper Observer consuming production SignalRecord and LiveRiskPlanRecord.

Strict Invariants:
  1. Zero Duplicate Signal Engine: Consumes existing production SignalRecord and
     LiveRiskPlanRecord emitted by Phase 7 XauUsdLiveDecisionPipelineService.
  2. Authoritative Signal Record: Does NOT act as a competing signal authority.
  3. Closed-Candle Decisions: Decision timeframes limited to 15m, 1h, 4h, 1d.
     1m and 5m are used exclusively for intrabar barrier collision resolution.
  4. Dual-Side Direction Awareness: BUY and SELL remain completely distinct.
  5. Empirical Friction Point-in-Time: Incorporates active empirical friction parameters
     from EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1.
  6. True Requested-Price Slippage remains strictly UNOBSERVABLE.
  7. Zero Speculative Monetary PnL: Unless explicit paper lot volume is specified,
     monetary PnL remains null/NOT_EVALUATED; gross_r, net_r, MFE, MAE are authoritative.
  8. Paper-Only: Zero real broker order capability.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional, Sequence
import uuid

from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_types import XauUsdCostConfig, XauUsdSimulatedTrade, XauUsdTradeOutcome
from engine.core.types import (
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
    RiskCandidateStatus,
    RiskSide,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideRiskPlanSnapshot,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
)
from engine.paper.fingerprint import compute_observation_fingerprint
from engine.paper.guards import assert_paper_execution_safety
from engine.paper.types import PaperObservationSnapshot, PaperOutcomeType
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_policy import XauUsdExecutionPolicy


STRATEGIC_TIMEFRAMES = {"15m", "1h", "4h", "1d"}
INTRABAR_ONLY_TIMEFRAMES = {"1m", "5m"}


def _require_utc(dt: datetime, name: str = "timestamp") -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware UTC, got naive: {dt}")
    return dt.astimezone(timezone.utc)


class Phase8PaperObserver:
    """
    Evaluates paper execution and forward triple-barrier outcomes from production records.
    """

    def __init__(
        self,
        holding_horizon_bars_15m: int = 32,
        max_fill_wait_bars_15m: int = 4,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    ):
        assert_paper_execution_safety("init")
        self.holding_horizon_bars_15m = holding_horizon_bars_15m
        self.max_fill_wait_bars_15m = max_fill_wait_bars_15m
        self.intrabar_policy = intrabar_policy

    @classmethod
    def validate_decision_timeframe(cls, timeframe: str) -> None:
        """Enforce that strategic decisions occur strictly on eligible closed timeframes."""
        tf = str(timeframe).strip().lower()
        if tf in INTRABAR_ONLY_TIMEFRAMES:
            raise ValueError(
                f"INTRABAR_RESOLUTION_ONLY: Timeframe '{timeframe}' cannot independently create a strategic signal."
            )
        if tf not in STRATEGIC_TIMEFRAMES:
            raise ValueError(
                f"INVALID_TIMEFRAME: Timeframe '{timeframe}' is not an eligible Phase 8 strategic decision timeframe."
            )

    def observe_production_setup(
        self,
        signal_record: Any,
        risk_record: Optional[Any],
        future_candles_15m: Sequence[CandleData],
        future_candles_5m: Optional[Sequence[CandleData]] = None,
        future_candles_1m: Optional[Sequence[CandleData]] = None,
        friction_model: Optional[Any] = None,
        paper_volume_lots: Optional[Decimal] = None,
        pnl_currency: str = "USC",
    ) -> PaperObservationSnapshot:
        """
        Create a deterministic PaperObservationSnapshot from authoritative production records.
        """
        assert_paper_execution_safety("observe")

        timeframe = str(signal_record.timeframe).strip().lower()
        self.validate_decision_timeframe(timeframe)

        decision_ts = _require_utc(signal_record.timestamp, "signal_record.timestamp")

        # Determine side (default to LONG / BUY if side is not specified)
        raw_side = getattr(risk_record, "risk_side", None) if risk_record else None
        if not raw_side:
            # Check candidate_user_decision or user_decision on signal_record
            dec = str(getattr(signal_record, "user_decision", "WAIT")).upper()
            if dec == "SELL":
                raw_side = "SHORT"
            else:
                raw_side = "LONG"

        obs_side = "BUY" if str(raw_side).upper() in ("LONG", "BUY") else "SELL"
        risk_side_enum = RiskSide.LONG if obs_side == "BUY" else RiskSide.SHORT
        signal_side_enum = SignalSide.LONG if obs_side == "BUY" else SignalSide.SHORT

        # Extract friction parameters
        friction_model_id = (
            getattr(friction_model, "model_version_id", None)
            or "EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1"
        )
        friction_evidence_fp = getattr(
            friction_model, "empirical_friction_evidence_fingerprint", None
        )

        base_spread_bps = (
            Decimal(str(getattr(friction_model, "base_spread_bps", "0.8338")))
            if friction_model
            else Decimal("0.8338")
        )
        base_slippage_bps = (
            Decimal(str(getattr(friction_model, "base_slippage_bps", "0.0000")))
            if friction_model
            else Decimal("0.0000")
        )

        cost_config = XauUsdCostConfig(
            synthetic_spread_bps=base_spread_bps,
            exit_slippage_bps=base_slippage_bps,
            entry_fee_bps=Decimal("0.0"),
            exit_fee_bps=Decimal("0.0"),
            entry_slippage_bps=Decimal("0.0"),
        )


        # Build execution / outcome engine with explicit side-aware entry model
        policy_fp = (
            getattr(risk_record, "phase5_policy_fingerprint", None)
            or getattr(risk_record, "risk_plan_fingerprint", None)
            or "phase8_paper_policy_v1"
        )
        code_rev = getattr(signal_record, "code_revision", "phase8-live-obs") or "phase8-live-obs"
        exec_policy = XauUsdExecutionPolicy(
            latency_seconds=0.0,
            synthetic_spread_pct=Decimal("0.0001"),
            slippage_pct=Decimal("0.0000"),
        )
        entry_model = SideAwareEntryExecutionModel(
            code_revision=code_rev,
            execution_policy=exec_policy,
            phase5_policy_fingerprint=policy_fp,
        )

        outcome_engine = XauUsdOutcomeEngine(
            cost_config=cost_config,
            holding_horizon_bars_15m=self.holding_horizon_bars_15m,
            max_fill_wait_bars_15m=self.max_fill_wait_bars_15m,
            entry_execution_model=entry_model,
            code_revision=code_rev,
        )

        # Build in-memory SignalSnapshot from authoritative SignalRecord
        cand_dec_str = getattr(signal_record, "user_decision", "WAIT")
        cand_state_str = getattr(signal_record, "state", "NO_TRADE")

        cand_dec = UserDecision.BUY if cand_dec_str == "BUY" else (UserDecision.SELL if cand_dec_str == "SELL" else UserDecision.WAIT)
        cand_state = SignalState.BUY_WINDOW if cand_state_str == "BUY_WINDOW" else (SignalState.SELL_WINDOW if cand_state_str == "SELL_WINDOW" else SignalState.NO_TRADE)

        dir_res = SideDirectionScoreResult(
            side=signal_side_enum,
            total_score=getattr(signal_record, "long_direction_score", None) if obs_side == "BUY" else getattr(signal_record, "short_direction_score", None),
            max_score=100.0,
            components=(),
            is_valid=True,
            is_direction_ready=True,
        )
        tim_res = SideTimingScoreResult(
            side=signal_side_enum,
            total_score=getattr(signal_record, "long_timing_score", None) if obs_side == "BUY" else getattr(signal_record, "short_timing_score", None),
            max_score=100.0,
            components=(),
            is_valid=True,
            is_timing_ready=True,
        )
        hg = XauUsdHardGateEvaluation(
            is_blocked=False,
            override_state=None,
            block_reasons=(),
            runtime_health=RuntimeFeedHealth(),
        )

        signal_snapshot = DualSideSignalSnapshot(
            timestamp=decision_ts,
            instrument="XAUUSD",
            timeframe=timeframe,
            state=cand_state,
            user_decision=cand_dec,
            candidate_state=cand_state,
            candidate_user_decision=cand_dec,
            long_direction=dir_res,
            short_direction=dir_res,
            long_timing=tim_res,
            short_timing=tim_res,
            hard_gate=hg,
            reasons_long_positive=(),
            reasons_long_negative=(),
            reasons_short_positive=(),
            reasons_short_negative=(),
            hard_gate_reasons=(),
            resolution_reason=getattr(signal_record, "calibration_status", "PHASE8_PAPER_OBSERVATION") or "PHASE8_PAPER_OBSERVATION",
            candidate_resolution_reason="Phase 8 paper observation",
            publication_reason="Paper observation",
            analysis_fingerprint=signal_record.analysis_fingerprint,
            phase4_policy_fingerprint=getattr(signal_record, "phase4_policy_fingerprint", "") or "phase8-p4-policy",
            code_revision=code_rev,
            profile_name=getattr(signal_record, "profile_name", "XAUUSD_PAPER") or "XAUUSD_PAPER",
            calibration_status=getattr(signal_record, "calibration_status", "CALIBRATED") or "CALIBRATED",
        )

        # Build in-memory SideRiskPlanSnapshot from authoritative LiveRiskPlanRecord
        is_eligible = bool(
            risk_record and getattr(risk_record, "execution_eligible", False) and getattr(risk_record, "is_valid_risk_plan", False)
        )

        entry_min = getattr(risk_record, "entry_min", None) if risk_record else None
        entry_mid = getattr(risk_record, "entry_mid", None) if risk_record else None
        entry_max = getattr(risk_record, "entry_max", None) if risk_record else None
        stop_final = getattr(risk_record, "stop_final", None) if risk_record else None
        tp1 = getattr(risk_record, "tp1", None) if risk_record else None
        tp2 = getattr(risk_record, "tp2", None) if risk_record else None

        planned_risk = None
        if is_eligible and stop_final is not None:
            if risk_side_enum == RiskSide.LONG and entry_max is not None:
                planned_risk = entry_max - stop_final
            elif risk_side_enum == RiskSide.SHORT and entry_min is not None:
                planned_risk = stop_final - entry_min

        cand_status = (
            RiskCandidateStatus.VALID_LONG_RISK_CANDIDATE
            if (is_eligible and risk_side_enum == RiskSide.LONG)
            else (
                RiskCandidateStatus.VALID_SHORT_RISK_CANDIDATE
                if (is_eligible and risk_side_enum == RiskSide.SHORT)
                else RiskCandidateStatus.INVALID_RISK_CANDIDATE
            )
        )

        risk_plan_snapshot = SideRiskPlanSnapshot(
            side=risk_side_enum,
            source_phase4_fingerprint=signal_record.analysis_fingerprint,
            source_candidate_state=cand_state,
            source_candidate_decision=cand_dec,
            signal_generated_at=decision_ts,
            entry_min=entry_min,
            entry_mid=entry_mid,
            entry_max=entry_max,
            stop_structure=getattr(risk_record, "stop_structure", None) if risk_record else None,
            stop_atr=getattr(risk_record, "stop_atr", None) if risk_record else None,
            stop_final=stop_final,
            stop_distance_atr=getattr(risk_record, "stop_distance_atr", None) if risk_record else None,
            tp1=tp1,
            tp2=tp2,
            planned_rr_tp1=getattr(risk_record, "rr_tp1", None) if risk_record else None,
            planned_rr_tp2=getattr(risk_record, "rr_tp2", None) if risk_record else None,
            risk_candidate_valid=is_eligible,
            risk_candidate_status=cand_status,
            simulation_eligible=is_eligible,
            candidate_effective_action=cand_dec,
            publication_effective_action=UserDecision.WAIT,
            reasons=tuple(getattr(risk_record, "reasons", ())) if risk_record else (),
            entry_zone_fingerprint=getattr(risk_record, "entry_zone_fingerprint", None) if risk_record else None,
            tp1_zone_fingerprint=getattr(risk_record, "tp1_zone_fingerprint", None) if risk_record else None,
            tp2_zone_fingerprint=getattr(risk_record, "tp2_zone_fingerprint", None) if risk_record else None,
            phase5_policy_fingerprint=policy_fp,
            risk_plan_fingerprint=getattr(risk_record, "risk_plan_fingerprint", None) or f"paper-plan-fp-{signal_record.analysis_fingerprint[:12]}",
            risk_version=getattr(risk_record, "risk_version", "5.0.0") or "5.0.0",
            code_revision=code_rev,
        )

        # Execute simulated outcome
        trade_sim: Optional[XauUsdSimulatedTrade] = None
        if is_eligible and planned_risk is not None and planned_risk > Decimal("0"):
            trade_sim = outcome_engine.resolve_trade(
                signal=signal_snapshot,
                risk_plan=risk_plan_snapshot,
                future_candles_15m=future_candles_15m,
                future_candles_5m=future_candles_5m,
                future_candles_1m=future_candles_1m,
                execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
                intrabar_policy=self.intrabar_policy,
                trade_id=f"paper-{signal_record.analysis_fingerprint[:12]}-{obs_side}",
            )

        # Calculate outcomes
        if trade_sim is not None:
            raw_outcome = trade_sim.outcome.value if hasattr(trade_sim.outcome, "value") else str(trade_sim.outcome)
            paper_entry_ts = trade_sim.fill_timestamp
            paper_entry_price = trade_sim.fill_price
            paper_exit_ts = trade_sim.exit_timestamp
            paper_exit_price = trade_sim.exit_price
            gross_r = trade_sim.gross_r
            net_r = trade_sim.net_r
            mfe_r = trade_sim.mfe_r
            mae_r = trade_sim.mae_r
            duration = trade_sim.holding_duration_seconds
            exit_reason = raw_outcome
        else:
            raw_outcome = PaperOutcomeType.SKIPPED.value if not is_eligible else PaperOutcomeType.UNRESOLVED.value
            paper_entry_ts = None
            paper_entry_price = None
            paper_exit_ts = None
            paper_exit_price = None
            gross_r = None
            net_r = None
            mfe_r = None
            mae_r = None
            duration = None
            exit_reason = "NOT_EXECUTION_ELIGIBLE" if not is_eligible else "AWAITING_FUTURE_EVIDENCE"

        # Monetary PnL calculation strictly guarded by position size
        gross_pnl: Optional[Decimal] = None
        net_pnl: Optional[Decimal] = None
        effective_currency: Optional[str] = None

        if paper_volume_lots is not None and paper_volume_lots > Decimal("0"):
            effective_currency = pnl_currency
            if trade_sim is not None and trade_sim.gross_pnl_per_unit is not None:
                # Direct contract geometry: contract_size = 1.0 XAU
                units = paper_volume_lots * Decimal("1.0")
                gross_pnl = (trade_sim.gross_pnl_per_unit * units).quantize(Decimal("0.0001"))
                net_pnl = (trade_sim.net_pnl_per_unit * units).quantize(Decimal("0.0001"))

        # Deterministic Observation Fingerprint
        obs_id = f"p8_obs_{uuid.uuid4().hex[:16]}"
        market_hash = getattr(signal_record, "analysis_fingerprint", "EMPTY")[:32]

        obs_fingerprint = compute_observation_fingerprint(
            code_revision=signal_record.code_revision,
            engine_version=signal_record.engine_version,
            config_version=signal_record.config_version,
            market_data_hash=market_hash,
            friction_model_version_id=friction_model_id,
            decision_timestamp=decision_ts,
            decision_timeframe=timeframe,
            side=obs_side,
            source_signal_fingerprint=signal_record.analysis_fingerprint,
        )

        score = getattr(signal_record, "long_direction_score", None) if obs_side == "BUY" else getattr(signal_record, "short_direction_score", None)

        return PaperObservationSnapshot(
            observation_id=obs_id,
            observation_fingerprint=obs_fingerprint,
            source_signal_fingerprint=signal_record.analysis_fingerprint,
            source_risk_plan_fingerprint=getattr(risk_record, "risk_plan_fingerprint", None) if risk_record else None,
            side=obs_side,
            decision_timeframe=timeframe,
            decision_timestamp=decision_ts,
            decision_candle_close=decision_ts,
            instrument="XAUUSD",
            broker_symbol="XAUUSDc",
            dataset_readiness_fingerprint="",
            friction_model_version_id=friction_model_id,
            friction_evidence_fingerprint=friction_evidence_fp,
            signal_score=score,
            signal_decision=cand_dec_str,
            paper_entry_timestamp=paper_entry_ts,
            paper_entry_price_basis=paper_entry_price,
            spread_friction_applied=base_spread_bps,
            commission_friction_applied=Decimal("0"),
            financing_friction_applied=Decimal("0"),
            paper_exit_timestamp=paper_exit_ts,
            paper_exit_price=paper_exit_price,
            exit_reason=exit_reason,
            outcome=raw_outcome,
            mfe_r=mfe_r,
            mae_r=mae_r,
            gross_r=gross_r,
            net_r=net_r,
            holding_duration_seconds=duration,
            paper_volume_lots=paper_volume_lots,
            pnl_currency=effective_currency,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            created_at=datetime.now(timezone.utc),
        )
