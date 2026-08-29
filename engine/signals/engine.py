"""Master XautSignalEngine combining Phase 1-4 intelligence deterministically (Phase 4)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Sequence

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    Cycle3BExperimentalSnapshot,
    DirectionScoreResult,
    FeatureSnapshot,
    HardGateEvaluation,
    MacroEventContext,
    RegimeResult,
    RegimeType,
    SignalSnapshot,
    SignalState,
    StructureResult,
    TimingScoreResult,
    UserDecision,
)
from engine.features.engine import FeatureEngine
from engine.regime.engine import RegimeEngine
from engine.structure.engine import CausalStructureEngine
from engine.signals.direction import calculate_direction_score
from engine.signals.explainer import (
    compute_canonical_fingerprint,
    compute_research_fingerprint,
    explain_signal,
)
from engine.signals.gate import evaluate_hard_gates, evaluate_selective_gate
from engine.signals.timing import calculate_timing_score


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

        # 3. Normalized Basis Evaluation
        xaut_basis_z: Optional[float] = None
        if xau_reference_price is not None and usdt_rate is not None and latest_candle:
            xaut_usd = float(latest_candle.close) * float(usdt_rate)
            basis_usd = xaut_usd - float(xau_reference_price)
            xaut_basis_z = round(basis_usd / 2.0, 2)

        # 4. Hard Gate Evaluation
        is_blackout = macro_context.is_in_blackout if macro_context else False
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

        fingerprint = compute_canonical_fingerprint(
            instrument=instrument,
            timeframe=timeframe,
            as_of=analysis_timestamp,
            closed_candles=valid_15m,
            direction=direction,
            timing=timing,
            state=state,
            user_decision=user_decision,
            xau_reference_val=str(xau_reference_price) if xau_reference_price else None,
            xau_reference_ts=xau_reference_ts.isoformat() if xau_reference_ts else None,
            usdt_rate_val=str(usdt_rate) if usdt_rate else None,
            usdt_rate_ts=usdt_rate_ts.isoformat() if usdt_rate_ts else None,
            provider_status=provider_status,
            feature_version=self.feature_version,
            cycle_version=self.cycle_version,
            engine_version=self.engine_version,
            config_version=self.config_version,
            code_revision=self.code_revision,
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
