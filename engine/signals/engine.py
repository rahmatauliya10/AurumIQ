"""Master XautSignalEngine combining Phase 1-4 intelligence deterministically (Phase 4)."""
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    Cycle3BExperimentalSnapshot,
    DirectionScoreResult,
    DualSideDirectionResult,
    DualSideSignalSnapshot,
    DualSideTimingResult,
    FeatureSnapshot,
    FeedHealthStatus,
    HardGateEvaluation,
    MacroEventContext,
    RegimeResult,
    RegimeType,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSnapshot,
    SignalState,
    StructureResult,
    TimingScoreResult,
    UserDecision,
    XauUsdHardGateEvaluation,
)
from engine.cycles.profile import Cycle3AProfile
from engine.features.engine import FeatureEngine
from engine.regime.engine import RegimeEngine
from engine.structure.engine import CausalStructureEngine
from engine.signals.direction import calculate_direction_score, calculate_xauusd_dual_direction
from engine.signals.explainer import (
    compute_canonical_fingerprint,
    compute_research_fingerprint,
    compute_xauusd_fingerprint,
    explain_dual_side_signal,
    explain_signal,
)
from engine.signals.gate import (
    evaluate_hard_gates,
    evaluate_selective_gate,
    evaluate_xauusd_candidate_gate,
    evaluate_xauusd_hard_gates,
)
from engine.signals.profile import (
    Phase4SignalProfile,
    compute_phase4_policy_fingerprint,
    normalize_xauusd_target,
    uncalibrated_xauusd_signal_profile,
)
from engine.signals.timing import calculate_timing_score, calculate_xauusd_dual_timing


class XautSignalEngine:
    """
    Deterministic Master Signal Engine for XAUT/USDT.

    Strict Invariants:
      1. Pure Python: Zero Django, ORM, Celery, or Redis imports in engine package.
      2. Point-in-Time Safe: Evaluates closed candles on or before `as_of`.
      3. Hard Gates Precedence: Stale data, transition, macro blackout force WAIT regardless of score.
      4. Phase 3B Isolation: Experimental spectral cycles have 0.0 production weight.
      5. Canonical Fingerprint: SHA-256 derived deterministically from production inputs.
    """

    def __init__(
        self,
        code_revision: str,
        engine_version: str = "4.0.0",
        config_version: str = "cfg-2026-v1",
        feature_version: str = "feat-2026-v1",
        cycle_version: str = "3.0.0-3A",
    ):
        if not code_revision:
            raise ValueError("code_revision is required for canonical signal provenance.")
        self.code_revision = code_revision
        self.engine_version = engine_version
        self.config_version = config_version
        self.feature_version = feature_version
        self.cycle_version = cycle_version

        self.feature_engine = FeatureEngine()
        self.regime_engine = RegimeEngine()
        self.structure_engine = CausalStructureEngine()

    def analyze(
        self,
        candles_15m: Sequence[CandleData],
        as_of: Optional[datetime] = None,
        instrument: str = "XAUT/USDT",
        timeframe: str = "15m",
        candles_4h: Optional[Sequence[CandleData]] = None,
        candles_1d: Optional[Sequence[CandleData]] = None,
        candles_xau: Optional[Sequence[CandleData]] = None,
        xau_reference_price: Optional[Decimal] = None,
        xau_reference_is_bullish: Optional[bool] = None,
        xau_reference_ts: Optional[datetime] = None,
        usdt_rate: Optional[Decimal] = None,
        usdt_rate_ts: Optional[datetime] = None,
        provider_status: str = "HEALTHY",
        is_provider_transition: bool = False,
        is_feed_stale: bool = False,
        macro_context: Optional[MacroEventContext] = None,
        cycle_3a: Optional[Cycle3ASnapshot] = None,
        cycle_3b: Optional[Cycle3BExperimentalSnapshot] = None,
    ) -> SignalSnapshot:
        """
        Execute deterministic closed-candle signal evaluation.
        """
        if not candles_15m:
            raise ValueError("Signal analysis requires at least one 15m candle.")

        # 1. Point-in-Time Filtering
        as_of_utc = (
            as_of.astimezone(timezone.utc)
            if as_of and as_of.tzinfo
            else (as_of.replace(tzinfo=timezone.utc) if as_of else None)
        )

        valid_15m: List[CandleData] = []
        is_unclosed = False
        for c in candles_15m:
            c_close = (
                c.timestamp_close.astimezone(timezone.utc)
                if c.timestamp_close.tzinfo
                else c.timestamp_close.replace(tzinfo=timezone.utc)
            )
            if as_of_utc is not None and c_close > as_of_utc:
                continue
            if not c.is_closed:
                is_unclosed = True
                continue
            valid_15m.append(c)

        if not valid_15m:
            raise ValueError(f"No valid closed 15m candles found on or before as_of={as_of_utc}.")

        latest_candle = valid_15m[-1]
        analysis_timestamp = latest_candle.timestamp_close

        # Check sufficiency of critical data
        is_data_sufficient = len(valid_15m) >= 32

        # 2. Phase 2 Features, Regime & Structure on 15m
        features_15m: Optional[FeatureSnapshot] = None
        prev_features_15m: Optional[FeatureSnapshot] = None
        regime_15m: Optional[RegimeResult] = None
        structure_15m: Optional[StructureResult] = None

        if is_data_sufficient:
            features_15m = self.feature_engine.extract_features(valid_15m)
            if len(valid_15m) >= 33:
                prev_features_15m = self.feature_engine.extract_features(valid_15m[:-1])
            regime_15m = self.regime_engine.classify(features_15m)
            structure_15m = self.structure_engine.analyze(valid_15m, atr=features_15m.atr14)

        # Multi-timeframe auxiliary features (4H, 1D)
        features_4h: Optional[FeatureSnapshot] = None
        if candles_4h:
            valid_4h = [
                c for c in candles_4h
                if c.is_closed and (as_of_utc is None or c.timestamp_close <= analysis_timestamp)
            ]
            if len(valid_4h) >= 32:
                features_4h = self.feature_engine.extract_features(valid_4h)

        features_1d: Optional[FeatureSnapshot] = None
        if candles_1d:
            valid_1d = [
                c for c in candles_1d
                if c.is_closed and (as_of_utc is None or c.timestamp_close <= analysis_timestamp)
            ]
            if len(valid_1d) >= 32:
                features_1d = self.feature_engine.extract_features(valid_1d)

        # 3. Normalized Basis Evaluation (Statistical Rolling Z-Score with True Historical Pairing)
        xaut_basis_z: Optional[float] = None
        if xau_reference_price is not None and usdt_rate is not None and latest_candle and float(xau_reference_price) > 0:
            xaut_usd_current = float(latest_candle.close) * float(usdt_rate)
            xau_usd_current = float(xau_reference_price)
            current_basis_pct = (xaut_usd_current - xau_usd_current) / xau_usd_current
            
            basis_pct_series = []
            if candles_xau:
                xau_by_time = {
                    (c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)): float(c.close)
                    for c in candles_xau if c.is_closed
                }
                for c in valid_15m[-32:]:
                    c_ts = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)
                    if c_ts in xau_by_time and xau_by_time[c_ts] > 0:
                        if c.quote_rate is None:
                            continue  # Strictly skip historical candle with missing PIT rate
                        c_rate = float(c.quote_rate)
                        c_xaut_usd = float(c.close) * c_rate
                        c_xau_usd = xau_by_time[c_ts]
                        basis_pct_series.append((c_xaut_usd - c_xau_usd) / c_xau_usd)

            if len(basis_pct_series) >= 8:
                mean_basis = sum(basis_pct_series) / len(basis_pct_series)
                var_basis = sum((b - mean_basis) ** 2 for b in basis_pct_series) / len(basis_pct_series)
                std_basis = math.sqrt(var_basis)
                if std_basis > 1e-6:
                    xaut_basis_z = round((current_basis_pct - mean_basis) / std_basis, 2)
                else:
                    xaut_basis_z = None
            else:
                xaut_basis_z = None

        # 4. Hard Gate Evaluation & Macro Context Resolution
        if macro_context is None and cycle_3a is not None and cycle_3a.macro_event is not None:
            macro_context = cycle_3a.macro_event

        is_blackout = (
            macro_context.is_in_blackout
            if macro_context
            else (cycle_3a.is_blocked_by_event if cycle_3a else False)
        )
        is_missing_xau_gate = xau_reference_price is None or xau_reference_is_bullish is None
        is_missing_norm_gate = usdt_rate is None

        hard_gate = evaluate_hard_gates(
            is_feed_stale=is_feed_stale,
            is_provider_transition=is_provider_transition,
            is_macro_blackout=is_blackout,
            is_missing_xau=is_missing_xau_gate,
            is_missing_normalization=is_missing_norm_gate,
            is_unclosed_candle=is_unclosed,
        )

        # 5. Direction & Timing Scoring
        direction = calculate_direction_score(
            regime=regime_15m,
            features_15m=features_15m,
            structure_15m=structure_15m,
            features_4h=features_4h,
            features_1d=features_1d,
            xau_reference_is_bullish=xau_reference_is_bullish,
            xaut_basis_zscore=xaut_basis_z,
            config_version=self.config_version,
        )

        timing = calculate_timing_score(
            latest_closed_candle=latest_candle,
            features_15m=features_15m,
            structure_15m=structure_15m,
            cycle_3a=cycle_3a,
            macro_context=macro_context,
            prev_features_15m=prev_features_15m,
            config_version=self.config_version,
        )

        # 6. Selective Gate State Machine
        is_reversal = False
        is_near_support = False
        if timing.components:
            for comp in timing.components:
                if comp.name == "15m Reversal Confirmation" and comp.score >= 14.0:
                    is_reversal = True
                if comp.name == "Entry Zone Proximity" and comp.score >= 15.0:
                    is_near_support = True

        state, user_decision = evaluate_selective_gate(
            direction=direction,
            timing=timing,
            regime=regime_15m,
            structure=structure_15m,
            hard_gate=hard_gate,
            is_reversal_confirmed=is_reversal,
            is_near_support=is_near_support,
            is_data_sufficient=is_data_sufficient,
        )

        # 7. Explainability & Fingerprinting
        pos_reasons, neg_reasons, gate_reasons = explain_signal(
            direction=direction,
            timing=timing,
            hard_gate=hard_gate,
            state=state,
            user_decision=user_decision,
        )

        if macro_context is None:
            macro_state = "MISSING"
        elif not macro_context.is_feed_healthy:
            macro_state = "UNHEALTHY"
        elif macro_context.is_in_blackout:
            macro_state = "BLACKOUT"
        else:
            macro_state = "NORMAL"

        fingerprint = compute_canonical_fingerprint(
            instrument=instrument,
            timeframe=timeframe,
            as_of=analysis_timestamp,
            closed_candles=valid_15m,
            direction=direction,
            timing=timing,
            state=state,
            user_decision=user_decision,
            code_revision=self.code_revision,
            closed_candles_4h=valid_4h if candles_4h else None,
            closed_candles_1d=valid_1d if candles_1d else None,
            closed_candles_xau=candles_xau if candles_xau else None,
            cycle_3a=cycle_3a,
            xau_reference_val=str(xau_reference_price) if xau_reference_price else None,
            xau_reference_ts=xau_reference_ts.isoformat() if xau_reference_ts else None,
            usdt_rate_val=str(usdt_rate) if usdt_rate else None,
            usdt_rate_ts=usdt_rate_ts.isoformat() if usdt_rate_ts else None,
            macro_state=macro_state,
            is_feed_stale=is_feed_stale,
            is_provider_transition=is_provider_transition,
            provider_status=provider_status,
            feature_version=self.feature_version,
            cycle_version=self.cycle_version,
            engine_version=self.engine_version,
            config_version=self.config_version,
        )

        research_fp = compute_research_fingerprint(
            production_fingerprint=fingerprint,
            cycle_3b=cycle_3b,
        )

        return SignalSnapshot(
            timestamp=analysis_timestamp,
            instrument=instrument,
            timeframe=timeframe,
            state=state,
            user_decision=user_decision,
            direction=direction,
            timing=timing,
            hard_gate=hard_gate,
            reasons_positive=pos_reasons,
            reasons_negative=neg_reasons,
            hard_gate_reasons=gate_reasons,
            analysis_fingerprint=fingerprint,
            research_fingerprint=research_fp,
            engine_version=self.engine_version,
            config_version=self.config_version,
            feature_version=self.feature_version,
            cycle_version=self.cycle_version,
            code_revision=self.code_revision,
            cycle_3b_informational=cycle_3b,
        )


# --- Phase 4 XAUUSD Master Dual-Side Signal Engine ---

class XauUsdSignalEngine:
    """
    Deterministic Master Signal Engine for canonical XAUUSD spot.

    Strict Invariants:
      1. Pure Python: Zero Django, ORM, Celery, or Redis imports in engine package.
      2. Closed-Candle PIT: Evaluates closed candles across 15m, 1H, 4H, 1D strictly on or before decision timestamp T.
      3. Dual-Side Scoring: Evaluates Long and Short directions & timings independently.
      4. Two-Layer State Machine:
         - Layer A: Pure candidate mechanics (evaluate_xauusd_candidate_gate).
         - Layer B: Production publication authority guard (blocks BUY/SELL until Phase 6 empirical calibration).
      5. Zero Test Bypass Flags: No bypass arguments in production methods.
    """

    def __init__(
        self,
        code_revision: str,
        engine_version: str = "4.0.0",
        feature_version: str = "feat-2026-v1",
        cycle_version: str = "3.0.0-3A",
    ):
        if not code_revision or not code_revision.strip():
            raise ValueError("code_revision must be a non-empty string.")
        self.code_revision = code_revision.strip()
        self.engine_version = engine_version
        self.feature_version = feature_version
        self.cycle_version = cycle_version

    @staticmethod
    def filter_pit_candles(
        candles: Optional[Sequence[CandleData]],
        as_of: datetime,
    ) -> Tuple[List[CandleData], bool]:
        """
        Pure PIT filter for a candle sequence against decision timestamp as_of (T).

        Rules:
          1. Normalize naive datetimes as UTC consistently.
          2. timestamp_close > T -> future candle, ignore before any closure validation.
          3. timestamp_close <= T and is_closed == True -> eligible.
          4. timestamp_close <= T and is_closed == False -> mark decision context unclosed (FORCE_WAIT).
          5. Future unclosed candle > T -> ignored, must NOT trigger FORCE_WAIT.
          6. Do NOT mutate input. Do NOT interpolate or synthesize missing bars.

        Returns:
          (eligible_closed_candles, has_unclosed_candle_le_T)
        """
        if not candles:
            return [], False

        as_of_utc = as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        eligible: List[CandleData] = []
        has_unclosed_le_t = False

        for c in candles:
            c_close = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)
            if c_close > as_of_utc:
                # Future candle > T: ignored before any closure validation
                continue
            if not c.is_closed:
                # Unclosed candle <= T: triggers unclosed context
                has_unclosed_le_t = True
                continue
            eligible.append(c)

        return eligible, has_unclosed_le_t

    @staticmethod
    def _hash_candles(candles: Optional[Sequence[CandleData]]) -> str:
        """Hash the full PIT-filtered authoritative candle sequence with canonical normalized decimal precision."""
        if not candles:
            return "EMPTY_FEED"
        import hashlib
        import json
        payload = [
            {
                "ts": (c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)).isoformat(),
                "o": f"{Decimal(str(c.open)):.8f}",
                "h": f"{Decimal(str(c.high)):.8f}",
                "l": f"{Decimal(str(c.low)):.8f}",
                "c": f"{Decimal(str(c.close)):.8f}",
                "v": f"{Decimal(str(c.volume)):.8f}",
                "closed": bool(c.is_closed),
            }
            for c in candles
        ]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def analyze(
        self,
        closed_candles_15m: Sequence[CandleData],
        closed_candles_1h: Optional[Sequence[CandleData]] = None,
        closed_candles_4h: Optional[Sequence[CandleData]] = None,
        closed_candles_1d: Optional[Sequence[CandleData]] = None,
        regime_15m: Optional[RegimeResult] = None,
        features_15m: Optional[FeatureSnapshot] = None,
        features_1h: Optional[FeatureSnapshot] = None,
        features_4h: Optional[FeatureSnapshot] = None,
        features_1d: Optional[FeatureSnapshot] = None,
        structure_15m: Optional[StructureResult] = None,
        cycle_3a: Optional[Cycle3ASnapshot] = None,
        cycle_3a_profile: Optional[Cycle3AProfile] = None,
        cycle_3b_informational: Optional[Cycle3BExperimentalSnapshot] = None,
        runtime_health: Optional[RuntimeFeedHealth] = None,
        profile: Optional[Phase4SignalProfile] = None,
        instrument: str = "XAUUSD",
        timeframe: str = "15m",
        as_of: Optional[datetime] = None,
    ) -> DualSideSignalSnapshot:
        """
        Execute deterministic closed-candle dual-side signal analysis for XAUUSD.
        """
        norm_instrument = normalize_xauusd_target(instrument)
        active_profile = profile if profile is not None else uncalibrated_xauusd_signal_profile()
        policy_fp = compute_phase4_policy_fingerprint(active_profile)

        # 1. Determine decision timestamp T (no datetime.now fallback)
        if as_of is not None:
            decision_ts = as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        else:
            if not closed_candles_15m:
                raise ValueError("Signal analysis requires at least one eligible closed 15m candle.")
            closed_15m_candidates = [c for c in closed_candles_15m if c.is_closed]
            if not closed_15m_candidates:
                latest_c = closed_candles_15m[-1]
                decision_ts = latest_c.timestamp_close.astimezone(timezone.utc) if latest_c.timestamp_close.tzinfo else latest_c.timestamp_close.replace(tzinfo=timezone.utc)
            else:
                decision_ts = closed_15m_candidates[-1].timestamp_close.astimezone(timezone.utc) if closed_15m_candidates[-1].timestamp_close.tzinfo else closed_15m_candidates[-1].timestamp_close.replace(tzinfo=timezone.utc)

        # 2. Pure PIT Filtering across all timeframes at decision timestamp T
        pit_15m, unclosed_15m = self.filter_pit_candles(closed_candles_15m, decision_ts)
        pit_1h, unclosed_1h = self.filter_pit_candles(closed_candles_1h, decision_ts)
        pit_4h, unclosed_4h = self.filter_pit_candles(closed_candles_4h, decision_ts)
        pit_1d, unclosed_1d = self.filter_pit_candles(closed_candles_1d, decision_ts)

        has_unclosed_le_t = unclosed_15m or unclosed_1h or unclosed_4h or unclosed_1d
        if not pit_15m and not has_unclosed_le_t:
            if runtime_health and runtime_health.primary_15m != FeedHealthStatus.HEALTHY:
                # Primary feed is missing/unhealthy/stale: fail closed through hard safety gate
                pass
            else:
                raise ValueError(f"No eligible closed 15m candles found on or before as_of={decision_ts.isoformat()}.")

        latest_candle_15m = pit_15m[-1] if pit_15m else None
        analysis_timestamp = decision_ts

        rfh = runtime_health if runtime_health is not None else RuntimeFeedHealth()
        if has_unclosed_le_t or (latest_candle_15m is not None and not latest_candle_15m.is_closed):
            from dataclasses import replace
            rfh = replace(rfh, is_unclosed_candle=True)

        # 3. Dual-Side Direction Evaluation
        dual_dir = calculate_xauusd_dual_direction(
            regime=regime_15m,
            features_15m=features_15m,
            structure_15m=structure_15m,
            features_1h=features_1h,
            features_4h=features_4h,
            features_1d=features_1d,
            profile=active_profile,
        )

        # 4. Dual-Side Timing Evaluation
        dual_tim = calculate_xauusd_dual_timing(
            candle_15m=latest_candle_15m,
            features_15m=features_15m,
            structure_15m=structure_15m,
            features_1h=features_1h,
            cycle_3a=cycle_3a,
            cycle_3a_profile=cycle_3a_profile,
            profile=active_profile,
        )

        # 5. Hard Safety Gate Evaluation
        hard_gate = evaluate_xauusd_hard_gates(
            runtime_health=rfh,
            profile=active_profile,
        )

        # 6. Layer A Candidate State Machine Evaluation
        cand_result = evaluate_xauusd_candidate_gate(
            long_direction=dual_dir.long_direction,
            short_direction=dual_dir.short_direction,
            long_timing=dual_tim.long_timing,
            short_timing=dual_tim.short_timing,
            hard_gate=hard_gate,
            profile=active_profile,
        )

        # 7. Layer B Production Publication Authority Guard
        if active_profile.is_production_authorized:
            published_state = cand_result.candidate_state
            published_user_decision = cand_result.candidate_user_decision
        else:
            if hard_gate.is_blocked:
                published_state = SignalState.FORCE_WAIT
                published_user_decision = UserDecision.WAIT
            else:
                published_state = SignalState.NO_TRADE
                published_user_decision = UserDecision.WAIT

        # 8. Dual-Side Explanation & Reason Segregation
        l_pos, l_neg, s_pos, s_neg, hg_reasons, cand_res_reason, pub_reason = explain_dual_side_signal(
            long_direction=dual_dir.long_direction,
            short_direction=dual_dir.short_direction,
            long_timing=dual_tim.long_timing,
            short_timing=dual_tim.short_timing,
            hard_gate=hard_gate,
            candidate_result=cand_result,
            is_production_authorized=active_profile.is_production_authorized,
        )

        # 9. Deterministic Multi-Timeframe Full PIT Candle Hashes & Analysis Fingerprint
        c15_hash = self._hash_candles(pit_15m)
        c1h_hash = self._hash_candles(pit_1h)
        c4h_hash = self._hash_candles(pit_4h)
        c1d_hash = self._hash_candles(pit_1d)

        cycle_3a_id = f"{cycle_3a.profile_name}:{cycle_3a.calibration_status}" if cycle_3a else None

        analysis_fp = compute_xauusd_fingerprint(
            timestamp=analysis_timestamp,
            instrument=norm_instrument,
            timeframe=timeframe,
            phase4_policy_fingerprint=policy_fp,
            closed_candle_15m_hash=c15_hash,
            closed_candle_1h_hash=c1h_hash,
            closed_candle_4h_hash=c4h_hash,
            closed_candle_1d_hash=c1d_hash,
            long_direction=dual_dir.long_direction,
            short_direction=dual_dir.short_direction,
            long_timing=dual_tim.long_timing,
            short_timing=dual_tim.short_timing,
            runtime_health=rfh,
            published_state=published_state,
            published_user_decision=published_user_decision,
            candidate_state=cand_result.candidate_state,
            candidate_user_decision=cand_result.candidate_user_decision,
            candidate_resolution_reason=cand_res_reason,
            publication_reason=pub_reason,
            code_revision=self.code_revision,
            cycle_3a_identity=cycle_3a_id,
        )

        return DualSideSignalSnapshot(
            timestamp=analysis_timestamp,
            instrument=norm_instrument,
            timeframe=timeframe,
            state=published_state,
            user_decision=published_user_decision,
            candidate_state=cand_result.candidate_state,
            candidate_user_decision=cand_result.candidate_user_decision,
            long_direction=dual_dir.long_direction,
            short_direction=dual_dir.short_direction,
            long_timing=dual_tim.long_timing,
            short_timing=dual_tim.short_timing,
            hard_gate=hard_gate,
            reasons_long_positive=l_pos,
            reasons_long_negative=l_neg,
            reasons_short_positive=s_pos,
            reasons_short_negative=s_neg,
            hard_gate_reasons=hg_reasons,
            resolution_reason=pub_reason,
            candidate_resolution_reason=cand_res_reason,
            publication_reason=pub_reason,
            analysis_fingerprint=analysis_fp,
            phase4_policy_fingerprint=policy_fp,
            code_revision=self.code_revision,
            profile_name=active_profile.name,
            calibration_status=active_profile.calibration_status.value,
            engine_version=self.engine_version,
            config_version=active_profile.name,
            feature_version=self.feature_version,
            cycle_version=self.cycle_version,
            cycle_3b_informational=cycle_3b_informational,
        )

