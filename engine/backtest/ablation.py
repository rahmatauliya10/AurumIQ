"""Pure Python component ablation framework for research-only paired robustness evaluation."""
import hashlib
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.backtest.fingerprint import compute_backtest_fingerprint
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.types import (
    AblationComparison,
    AblationDelta,
    AblationReport,
    AblationSpec,
    AblationType,
    BacktestMetrics,
    BacktestRunSpec,
    WalkForwardConfig,
    WalkForwardResult,
)
from engine.backtest.walkforward import WalkForwardEngine
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    DirectionScoreResult,
    HardGateEvaluation,
    MacroEventContext,
    RegimeResult,
    SignalSnapshot,
    StructureResult,
    TimingScoreResult,
    UserDecision,
)
from engine.signals.direction import calculate_direction_score
from engine.signals.engine import XautSignalEngine
from engine.signals.explainer import explain_signal
from engine.signals.gate import evaluate_hard_gates, evaluate_selective_gate
from engine.signals.timing import calculate_timing_score


class AblatedSignalEngine(XautSignalEngine):
    """
    Research-only signal engine subclass that isolates or removes specific components.

    Strict Invariants (P6-29, P6-30, A37):
      1. Zero mutation of baseline XautSignalEngine production defaults.
      2. Phase 3B production weight remains hard-locked to 0.0 before, during, and after ablation.
      3. Hard gate ablations (e.g. NO_MACRO_SAFETY) are strictly labeled unsafe research.
    """

    def __init__(
        self,
        ablation_type: AblationType,
        code_revision: str,
        engine_version: str = "4.0.0",
        config_version: str = "cfg-2026-v1",
        feature_version: str = "feat-2026-v1",
        cycle_version: str = "3.0.0-3A",
    ):
        super().__init__(
            code_revision=code_revision,
            engine_version=engine_version,
            config_version=config_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
        )
        self.ablation_type = ablation_type

    def analyze(
        self,
        candles_15m: Sequence[CandleData],
        candles_4h: Optional[Sequence[CandleData]] = None,
        candles_1d: Optional[Sequence[CandleData]] = None,
        xau_reference_price: Optional[Decimal] = None,
        xau_reference_is_bullish: Optional[bool] = None,
        usdt_rate: Optional[Decimal] = None,
        cycle_3a: Optional[Cycle3ASnapshot] = None,
        macro_context: Optional[MacroEventContext] = None,
        is_feed_stale: bool = False,
        is_provider_transition: bool = False,
        as_of: Optional[datetime] = None,
    ) -> SignalSnapshot:
        """Analyze market context with specified component explicitly ablated."""
        if self.ablation_type == AblationType.BASELINE:
            return super().analyze(
                candles_15m=candles_15m,
                candles_4h=candles_4h,
                candles_1d=candles_1d,
                xau_reference_price=xau_reference_price,
                xau_reference_is_bullish=xau_reference_is_bullish,
                usdt_rate=usdt_rate,
                cycle_3a=cycle_3a,
                macro_context=macro_context,
                is_feed_stale=is_feed_stale,
                is_provider_transition=is_provider_transition,
                as_of=as_of,
            )

        # 1. Component Ablation Overrides
        effective_cycle_3a = cycle_3a
        if self.ablation_type == AblationType.NO_PHASE3A_TIMING:
            effective_cycle_3a = None

        effective_xau_price = xau_reference_price
        effective_xau_bullish = xau_reference_is_bullish
        if self.ablation_type == AblationType.NO_XAU_BASIS_COMPONENT:
            effective_xau_price = None
            effective_xau_bullish = None

        effective_macro = macro_context
        if self.ablation_type == AblationType.NO_MACRO_SAFETY:
            # Unsafe research: suppress macro blackout hard gate
            effective_macro = None

        # Execute standard feature extraction & structure analysis
        as_of_utc = (
            as_of.astimezone(timezone.utc)
            if as_of and as_of.tzinfo
            else (as_of.replace(tzinfo=timezone.utc) if as_of else None)
        )

        valid_15m = [
            c for c in candles_15m
            if (as_of_utc is None or (c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)) <= as_of_utc)
            and c.is_closed
        ]
        if not valid_15m:
            raise ValueError(f"No valid closed 15m candles found on or before as_of={as_of_utc}.")

        latest_candle = valid_15m[-1]
        analysis_timestamp = latest_candle.timestamp_close
        is_data_sufficient = len(valid_15m) >= 32

        features_15m = self.feature_engine.extract_features(valid_15m) if is_data_sufficient else None
        prev_features_15m = self.feature_engine.extract_features(valid_15m[:-1]) if len(valid_15m) >= 33 else None
        regime_15m = self.regime_engine.classify(features_15m) if features_15m else None
        structure_15m = (
            self.structure_engine.analyze(valid_15m, atr=features_15m.atr14)
            if is_data_sufficient and features_15m and self.ablation_type != AblationType.NO_STRUCTURE_COMPONENT
            else None
        )

        # Multi-timeframe features
        features_4h = None
        if candles_4h:
            v_4h = [c for c in candles_4h if c.is_closed and (as_of_utc is None or c.timestamp_close <= analysis_timestamp)]
            if len(v_4h) >= 32:
                features_4h = self.feature_engine.extract_features(v_4h)

        features_1d = None
        if candles_1d:
            v_1d = [c for c in candles_1d if c.is_closed and (as_of_utc is None or c.timestamp_close <= analysis_timestamp)]
            if len(v_1d) >= 32:
                features_1d = self.feature_engine.extract_features(v_1d)

        # 3. Normalized Basis Evaluation (Statistical Rolling Z-Score)
        xaut_basis_z = None
        if effective_xau_price is not None and usdt_rate is not None and latest_candle and float(effective_xau_price) > 0:
            xaut_usd = float(latest_candle.close) * float(usdt_rate)
            xau_usd = float(effective_xau_price)
            current_basis_pct = (xaut_usd - xau_usd) / xau_usd
            
            basis_pct_series = []
            for c in valid_15m[-32:]:
                c_rate = float(c.quote_rate) if c.quote_rate else float(usdt_rate)
                c_xaut_usd = float(c.close) * c_rate
                basis_pct_series.append((c_xaut_usd - xau_usd) / xau_usd)

            if len(basis_pct_series) >= 8:
                mean_basis = sum(basis_pct_series) / len(basis_pct_series)
                var_basis = sum((b - mean_basis) ** 2 for b in basis_pct_series) / len(basis_pct_series)
                std_basis = math.sqrt(var_basis)
                if std_basis > 1e-6:
                    xaut_basis_z = round((current_basis_pct - mean_basis) / std_basis, 2)
                else:
                    xaut_basis_z = 0.0
            else:
                xaut_basis_z = 0.0

        # Hard Gate
        is_blackout = effective_macro.is_in_blackout if effective_macro else False
        is_missing_xau_gate = effective_xau_price is None or effective_xau_bullish is None
        is_missing_norm_gate = usdt_rate is None

        hard_gate = evaluate_hard_gates(
            is_feed_stale=is_feed_stale,
            is_provider_transition=is_provider_transition,
            is_macro_blackout=is_blackout,
            is_missing_xau=is_missing_xau_gate,
            is_missing_normalization=is_missing_norm_gate,
            is_unclosed_candle=False,
        )

        # Scoring with component ablations
        effective_features_15m = features_15m
        if self.ablation_type == AblationType.NO_MOMENTUM_COMPONENT:
            effective_features_15m = None

        direction = calculate_direction_score(
            regime=regime_15m,
            features_15m=effective_features_15m,
            structure_15m=structure_15m,
            features_4h=features_4h,
            features_1d=features_1d,
            xau_reference_is_bullish=effective_xau_bullish,
            xaut_basis_zscore=xaut_basis_z,
            config_version=self.config_version,
        )

        effective_prev_features = prev_features_15m
        if self.ablation_type == AblationType.NO_VOLUME_COMPONENT:
            effective_prev_features = None

        timing = calculate_timing_score(
            latest_closed_candle=latest_candle,
            features_15m=effective_features_15m,
            structure_15m=structure_15m,
            cycle_3a=effective_cycle_3a,
            macro_context=effective_macro,
            prev_features_15m=effective_prev_features,
            config_version=self.config_version,
        )

        is_reversal = False
        is_near_support = False
        if timing.components:
            for comp in timing.components:
                if comp.name == "15m Reversal Confirmation" and comp.score >= 14.0:
                    is_reversal = True if self.ablation_type != AblationType.NO_REVERSAL_COMPONENT else False
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

        pos_reasons, neg_reasons, gate_reasons = explain_signal(
            direction=direction,
            timing=timing,
            hard_gate=hard_gate,
            state=state,
            user_decision=user_decision,
        )

        # Provenance metadata & fingerprint
        provenance = {
            "engine_version": self.engine_version,
            "config_version": self.config_version,
            "feature_version": self.feature_version,
            "cycle_version": self.cycle_version,
            "code_revision": self.code_revision,
            "ablation_type": self.ablation_type.value,
        }

        h = hashlib.sha256()
        h.update(f"ablation:{self.ablation_type.value}".encode("utf-8"))
        h.update(f"timestamp:{analysis_timestamp.isoformat()}".encode("utf-8"))
        h.update(f"state:{state.value}".encode("utf-8"))
        h.update(f"decision:{user_decision.value}".encode("utf-8"))
        h.update(f"code_revision:{self.code_revision}".encode("utf-8"))
        fp = h.hexdigest()

        return SignalSnapshot(
            timestamp=analysis_timestamp,
            instrument="XAUT/USDT",
            timeframe="15m",
            state=state,
            user_decision=user_decision,
            direction=direction,
            timing=timing,
            hard_gate=hard_gate,
            reasons_positive=tuple(pos_reasons),
            reasons_negative=tuple(neg_reasons),
            hard_gate_reasons=tuple(gate_reasons),
            analysis_fingerprint=fp,
            code_revision=self.code_revision,
        )


class AblationLab:
    """
    Automated component ablation suite providing paired fold-to-fold comparisons.

    Strict Invariants:
      1. Zero Django/ORM dependencies in pure engine.
      2. Compares strictly paired folds on identical time spans, datasets, costs, and code revisions.
      3. Produces descriptive assessment without automated winner promotion.
    """

    DEFAULT_ABLATIONS = (
        AblationType.NO_PHASE3A_TIMING,
        AblationType.NO_XAU_BASIS_COMPONENT,
        AblationType.NO_STRUCTURE_COMPONENT,
        AblationType.NO_MOMENTUM_COMPONENT,
        AblationType.NO_VOLUME_COMPONENT,
        AblationType.NO_REVERSAL_COMPONENT,
    )

    @classmethod
    def run_ablation_experiment(
        cls,
        dataset: PointInTimeDataset,
        base_spec: BacktestRunSpec,
        wf_config: Optional[WalkForwardConfig] = None,
        ablations: Optional[Sequence[AblationType]] = None,
    ) -> AblationReport:
        """
        Execute paired ablation experiment across all folds against frozen baseline.
        """
        config = wf_config or WalkForwardConfig()
        selected_ablations = ablations or cls.DEFAULT_ABLATIONS

        # 1. Run Baseline Walk-Forward
        baseline_wf_engine = WalkForwardEngine()
        baseline_wf_res = baseline_wf_engine.run_walkforward(
            dataset=dataset,
            spec=base_spec,
            wf_config=config,
        )
        baseline_agg = baseline_wf_res.stability_report.aggregate_oos_metrics

        comparisons: List[AblationComparison] = []

        # 2. Run Paired Walk-Forward for each Ablation
        for ab_type in selected_ablations:
            ab_engine = AblatedSignalEngine(
                ablation_type=ab_type,
                code_revision=base_spec.code_revision,
                engine_version=base_spec.engine_version,
                config_version=base_spec.config_version,
                feature_version=base_spec.feature_version,
                cycle_version=base_spec.cycle_version,
            )

            ab_spec = BacktestRunSpec(
                instrument=base_spec.instrument,
                start_time=base_spec.start_time,
                end_time=base_spec.end_time,
                timeframes=base_spec.timeframes,
                cost_config=base_spec.cost_config,
                cost_scenario=base_spec.cost_scenario,
                dataset_hash=base_spec.dataset_hash,
                engine_version=base_spec.engine_version,
                config_version=base_spec.config_version,
                feature_version=base_spec.feature_version,
                cycle_version=base_spec.cycle_version,
                risk_version=base_spec.risk_version,
                execution_model_version=base_spec.execution_model_version,
                backtest_version=base_spec.backtest_version,
                code_revision=base_spec.code_revision,
                ablation_type=ab_type,
            )

            ab_wf_engine = WalkForwardEngine(signal_engine=ab_engine)
            ab_wf_res = ab_wf_engine.run_walkforward(
                dataset=dataset,
                spec=ab_spec,
                wf_config=config,
            )
            ab_agg = ab_wf_res.stability_report.aggregate_oos_metrics

            # Calculate paired deltas (ablation - baseline)
            delta = AblationDelta(
                delta_expectancy_r=round(ab_agg.net_expectancy_r - baseline_agg.net_expectancy_r, 4),
                delta_profit_factor=round(ab_agg.profit_factor - baseline_agg.profit_factor, 4),
                delta_drawdown_r=round(ab_agg.max_trade_sequence_drawdown_r - baseline_agg.max_trade_sequence_drawdown_r, 4),
                delta_trade_count=ab_agg.trade_count - baseline_agg.trade_count,
                delta_fill_rate=round(ab_agg.fill_rate - baseline_agg.fill_rate, 4),
                delta_cost_drag_r=round(ab_agg.cost_drag_r - baseline_agg.cost_drag_r, 4),
            )

            # Descriptive assessment
            assessment = cls._evaluate_assessment(delta, baseline_wf_res, ab_wf_res)

            paired_folds: List[Dict[str, Any]] = []
            for b_fold, a_fold in zip(baseline_wf_res.folds, ab_wf_res.folds):
                paired_folds.append({
                    "fold_id": b_fold.fold_id,
                    "baseline_oos_expectancy_r": b_fold.oos_metrics.net_expectancy_r,
                    "ablation_oos_expectancy_r": a_fold.oos_metrics.net_expectancy_r,
                    "delta_expectancy_r": round(a_fold.oos_metrics.net_expectancy_r - b_fold.oos_metrics.net_expectancy_r, 4),
                    "baseline_oos_trades": b_fold.oos_metrics.trade_count,
                    "ablation_oos_trades": a_fold.oos_metrics.trade_count,
                })

            comparison = AblationComparison(
                ablation_type=ab_type,
                baseline_run_fingerprint=baseline_wf_res.walkforward_fingerprint,
                ablation_run_fingerprint=ab_wf_res.walkforward_fingerprint,
                baseline_metrics=baseline_agg,
                ablation_metrics=ab_agg,
                delta=delta,
                assessment=assessment,
                paired_fold_comparisons=tuple(paired_folds),
            )
            comparisons.append(comparison)

        # 3. Report Fingerprint
        h = hashlib.sha256()
        h.update(f"base:{baseline_wf_res.walkforward_fingerprint}".encode("utf-8"))
        for c in comparisons:
            h.update(f"ablation:{c.ablation_type.value}:{c.delta.delta_expectancy_r}".encode("utf-8"))
        report_fp = h.hexdigest()

        return AblationReport(
            baseline_spec=base_spec,
            baseline_metrics=baseline_agg,
            comparisons=tuple(comparisons),
            report_fingerprint=report_fp,
        )

    @staticmethod
    def _evaluate_assessment(
        delta: AblationDelta,
        base_wf: WalkForwardResult,
        ab_wf: WalkForwardResult,
    ) -> str:
        """Descriptive assessment of component removal impact."""
        # If removing the component significantly reduces expectancy or PF -> Component was contributing (DEGRADES when removed)
        if delta.delta_expectancy_r < -0.05 or delta.delta_profit_factor < -0.10:
            return "DEGRADES"
        # If removing the component increases expectancy or PF -> Component was creating drag (IMPROVES when removed)
        if delta.delta_expectancy_r > 0.05 or delta.delta_profit_factor > 0.10:
            return "IMPROVES"
        # If fold deltas have opposing signs across folds -> UNSTABLE
        if base_wf.folds and ab_wf.folds:
            deltas = [
                a.oos_metrics.net_expectancy_r - b.oos_metrics.net_expectancy_r
                for b, a in zip(base_wf.folds, ab_wf.folds)
            ]
            has_pos = any(d > 0.02 for d in deltas)
            has_neg = any(d < -0.02 for d in deltas)
            if has_pos and has_neg:
                return "UNSTABLE"

        return "NEUTRAL"
