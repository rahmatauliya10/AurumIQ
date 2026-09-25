"""
Phase 6 / Phase 8 XAUUSD Signal Calibration Selection Policy Governance.

Authoritative loader, validator, and contract evaluator for the frozen
XAUUSD signal calibration selection policy artifact.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


DEFAULT_POLICY_PATH = Path("artifacts/calibration/xauusd_signal_calibration_selection_policy.json")
DEFAULT_POLICY_PATH_V2 = Path("artifacts/calibration/xauusd_signal_calibration_selection_policy_v2.json")


def _to_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"Naive datetime rejected: {dt}")
    return dt.astimezone(timezone.utc)


def compute_selection_policy_fingerprint(policy_dict: Dict[str, Any]) -> str:
    """
    Generate deterministic SHA-256 fingerprint from canonical JSON payload
    excluding any fingerprint fields.
    """
    payload = {
        k: v for k, v in policy_dict.items()
        if k not in ("policy_fingerprint", "artifact_fingerprint", "fingerprint")
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


@dataclass(frozen=True)
class XauUsdSignalCalibrationSelectionPolicy:
    """
    Frozen governed selection policy defining empirical qualification criteria
    for XAUUSD signal and risk profile calibration.
    """
    schema: str
    policy_id: str
    instrument: str
    target_instrument_normalized: str
    timeframe: str
    code_revision: str
    created_at: str
    historical_start: datetime
    historical_end_exclusive: datetime
    total_duration_days: float
    phase8_observation_window_start: datetime
    total_folds: int
    rolling_window: bool
    train_ratio: float
    val_ratio: float
    oos_ratio: float
    oos_duration_per_fold_days: float
    val_duration_per_fold_days: float
    folds: Tuple[Dict[str, Any], ...]
    dynamic_embargo_formula: str
    buy_min_effective_n: float
    sell_min_effective_n: float
    combined_min_effective_n: float
    single_side_promotion_authorized: bool
    confidence_level: float
    alpha: float
    absolute_max_drawdown_r: float
    max_drawdown_deterioration_pct: float
    min_positive_folds: int
    min_positive_folds_total: int
    min_temporal_stability_score: float
    max_single_fold_profit_concentration_pct: float
    max_candidate_evaluations: int
    policy_fingerprint: str
    raw_payload: Dict[str, Any]
    base_code_revision: str = ""
    decision_at: str = ""
    artifact_created_at: str = ""
    dataset_fingerprint: str = ""
    required_timeframes: Tuple[str, ...] = ()
    cache_schema: str = ""
    cache_semantics: str = ""
    fold_assignment: str = ""
    overall_trade_semantics: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "XauUsdSignalCalibrationSelectionPolicy":
        """Construct and strictly validate policy from raw dictionary."""
        # 1. Schema & Identity
        schema = data.get("schema")
        if schema not in (
            "aurumiq.calibration.selection_policy.v1",
            "aurumiq.calibration.selection_policy.v2",
        ):
            raise ValueError(f"Unknown or invalid policy schema: '{schema}'")

        policy_id = data.get("policy_id")
        if not policy_id or not isinstance(policy_id, str):
            raise ValueError(f"Invalid policy_id: '{policy_id}'")

        inst = data.get("instrument")
        norm_inst = data.get("target_instrument_normalized")
        if inst != "XAUUSD" or norm_inst != "XAUUSD":
            raise ValueError(f"Policy must target 'XAUUSD', got instrument='{inst}', normalized='{norm_inst}'")

        # 2. Fingerprint Integrity
        expected_fp = data.get("policy_fingerprint")
        if not expected_fp:
            raise ValueError("policy_fingerprint field is missing.")
        computed_fp = compute_selection_policy_fingerprint(data)
        if expected_fp != computed_fp:
            raise ValueError(
                f"Policy fingerprint mismatch: expected '{expected_fp}', computed '{computed_fp}'"
            )

        # 3. Dates & Boundaries
        hist_bounds = data.get("historical_bounds", {})
        start_str = hist_bounds.get("start_time")
        end_str = hist_bounds.get("end_time_exclusive")
        if not start_str or not end_str:
            raise ValueError("historical_bounds start_time and end_time_exclusive are required.")

        start_dt = _to_utc(datetime.fromisoformat(start_str))
        end_dt = _to_utc(datetime.fromisoformat(end_str))
        if start_dt >= end_dt:
            raise ValueError(f"historical_bounds start ({start_dt}) must strictly precede end ({end_dt})")

        p8_ref = data.get("actual_phase8_boundary_reference", {})
        p8_start_str = p8_ref.get("observation_window_start")
        if not p8_start_str:
            raise ValueError("actual_phase8_boundary_reference observation_window_start is required.")
        p8_start_dt = _to_utc(datetime.fromisoformat(p8_start_str))

        if p8_start_dt < end_dt:
            raise ValueError(
                f"Phase 8 observation start ({p8_start_dt}) cannot precede historical calibration cutoff ({end_dt})."
            )

        # 4. Walk-Forward Rules
        wf = data.get("walk_forward_policy", {})
        total_folds = wf.get("total_folds", 0)
        if total_folds != 5:
            raise ValueError(f"Walk-forward policy must freeze total_folds=5, got {total_folds}")
        if wf.get("rolling_window") is not False:
            raise ValueError("Walk-forward policy requires rolling_window=False (expanding window).")

        folds_list = wf.get("folds", [])
        if len(folds_list) != 5:
            raise ValueError(f"Walk-forward policy must declare exactly 5 fold specs, got {len(folds_list)}")

        # 5. Sample Quality Rules
        sq = data.get("sample_quality_requirements", {})
        buy_eff = float(sq.get("buy_min_effective_n", 0))
        sell_eff = float(sq.get("sell_min_effective_n", 0))
        comb_eff = float(sq.get("combined_min_effective_n", 0))
        if buy_eff < 60.0 or sell_eff < 60.0 or comb_eff < 100.0:
            raise ValueError(
                f"Sample quality requirements invalid: buy_eff={buy_eff} (>=60), sell_eff={sell_eff} (>=60), comb_eff={comb_eff} (>=100)"
            )
        if sq.get("single_side_promotion_authorized") is not False:
            raise ValueError("single_side_promotion_authorized must be strictly false.")

        # 6. Expectancy Rules
        exp_rule = data.get("expectancy_confidence_rule", {})
        conf_lvl = float(exp_rule.get("confidence_level", 0))
        if conf_lvl != 0.95:
            raise ValueError(f"confidence_level must be exactly 0.95 (95% one-sided), got {conf_lvl}")
        if exp_rule.get("additional_arbitrary_hurdle") is not None:
            raise ValueError("additional_arbitrary_hurdle must be null (strictly LCB > 0).")

        # 7. Drawdown Rules
        dd = data.get("drawdown_governance", {})
        abs_dd = float(dd.get("absolute_max_drawdown_r", 0))
        rel_dd = float(dd.get("max_drawdown_deterioration_pct_vs_baseline", 0))
        if abs_dd != 18.0 or rel_dd != 10.0:
            raise ValueError(f"Drawdown thresholds invalid: absolute={abs_dd} (18.0), deterioration={rel_dd} (10.0)")

        # 8. Stability & Concentration
        stab = data.get("temporal_stability_policy", {})
        min_pos = int(stab.get("min_positive_folds", 0))
        tot_pos = int(stab.get("min_positive_folds_total", 0))
        min_stab = float(stab.get("min_temporal_stability_score", 0))
        if min_pos != 4 or tot_pos != 5 or min_stab != 0.50:
            raise ValueError(f"Temporal stability rules invalid: min_pos={min_pos}/5, min_stab={min_stab} (0.50)")

        conc = data.get("single_period_dependency_policy", {})
        max_conc = float(conc.get("max_single_fold_profit_concentration_pct", 0))
        if max_conc != 60.0:
            raise ValueError(f"max_single_fold_profit_concentration_pct must be exactly 60.0, got {max_conc}")

        # 9. Search Budget
        search_budget = data.get("candidate_search_budget", {})
        max_cands = int(search_budget.get("max_candidate_evaluations", 0))
        if max_cands != 100:
            raise ValueError(f"max_candidate_evaluations must be exactly 100, got {max_cands}")
        if search_budget.get("auto_increase_permitted") is not False:
            raise ValueError("auto_increase_permitted must be strictly false.")

        # 10. Train/Val/OOS Isolation
        tvo = data.get("train_val_oos_isolation_policy", {})
        if (
            tvo.get("same_oos_retuning_permitted") is not False
            or tvo.get("second_best_selection_after_oos_permitted") is not False
            or tvo.get("threshold_mutation_after_oos_permitted") is not False
        ):
            raise ValueError("Train/Val/OOS isolation invariants violated in policy payload.")

        # 11. Production Authority
        auth = data.get("production_authority_status", {})
        if auth.get("is_production_authorized") is not False:
            raise ValueError("is_production_authorized must be strictly false in selection policy artifact.")

        return cls(
            schema=schema,
            policy_id=policy_id,
            instrument=inst,
            target_instrument_normalized=norm_inst,
            timeframe=data.get("timeframe", "15m"),
            code_revision=data.get("code_revision", ""),
            created_at=data.get("created_at", ""),
            historical_start=start_dt,
            historical_end_exclusive=end_dt,
            total_duration_days=float(hist_bounds.get("total_duration_days", 2338.0)),
            phase8_observation_window_start=p8_start_dt,
            total_folds=total_folds,
            rolling_window=False,
            train_ratio=float(wf.get("train_ratio", 0.60)),
            val_ratio=float(wf.get("val_ratio", 0.20)),
            oos_ratio=float(wf.get("oos_ratio", 0.20)),
            oos_duration_per_fold_days=float(wf.get("oos_duration_per_fold_days", 93.52)),
            val_duration_per_fold_days=float(wf.get("val_duration_per_fold_days", 467.60)),
            folds=tuple(folds_list),
            dynamic_embargo_formula=data.get("dynamic_embargo_rule", {}).get("formula", ""),
            buy_min_effective_n=buy_eff,
            sell_min_effective_n=sell_eff,
            combined_min_effective_n=comb_eff,
            single_side_promotion_authorized=False,
            confidence_level=conf_lvl,
            alpha=float(exp_rule.get("alpha", 0.05)),
            absolute_max_drawdown_r=abs_dd,
            max_drawdown_deterioration_pct=rel_dd,
            min_positive_folds=min_pos,
            min_positive_folds_total=tot_pos,
            min_temporal_stability_score=min_stab,
            max_single_fold_profit_concentration_pct=max_conc,
            max_candidate_evaluations=max_cands,
            policy_fingerprint=expected_fp,
            raw_payload=data,
            base_code_revision=data.get("base_code_revision", data.get("code_revision", "")),
            decision_at=data.get("decision_at", ""),
            artifact_created_at=data.get("artifact_created_at", data.get("created_at", "")),
            dataset_fingerprint=data.get("dataset_fingerprint", ""),
            required_timeframes=tuple(data.get("required_timeframes", ())),
            cache_schema=data.get("cache_schema", ""),
            cache_semantics=data.get("cache_semantics", ""),
            fold_assignment=data.get("fold_assignment", ""),
            overall_trade_semantics=data.get("overall_trade_semantics", ""),
        )

    def validate_dynamic_embargo(
        self,
        max_fill_wait_bars_15m: Optional[int],
        holding_horizon_bars_15m: Optional[int],
        declared_embargo_seconds: float,
    ) -> bool:
        """
        Verify that declared embargo satisfies dynamic dependency horizon requirement:
        embargo_seconds >= (max_fill_wait_bars_15m + holding_horizon_bars_15m) * 900
        """
        if max_fill_wait_bars_15m is None or holding_horizon_bars_15m is None:
            raise ValueError("Dependency horizon cannot be resolved: bars are None.")
        min_required_seconds = float((max_fill_wait_bars_15m + holding_horizon_bars_15m) * 900)
        return declared_embargo_seconds >= min_required_seconds

    def compute_effective_n_lcb_95(self, mean_r: float, std_r: float, effective_n: float) -> float:
        """
        Compute Effective-N-adjusted one-sided Student's t lower confidence bound at alpha=0.05:
        LCB_95(E[R]) = mean_R - t_{N_eff - 1, 0.95} * (std_R / sqrt(N_eff))
        """
        if effective_n <= 1.0 or std_r < 0.0:
            return float("-inf")
        # Critical value t for one-sided 95% (approximation / standard table)
        df = max(1.0, effective_n - 1.0)
        # For df >= 30, t_0.95 ranges from 1.697 (df=30) to 1.645 (df=inf)
        if df >= 100.0:
            t_crit = 1.645 + (1.645 / (4.0 * df))
        elif df >= 60.0:
            t_crit = 1.671
        elif df >= 30.0:
            t_crit = 1.697
        else:
            t_crit = 1.70 + (30.0 - df) * 0.02
        se = std_r / math.sqrt(effective_n)
        return float(round(mean_r - t_crit * se, 4))


def load_governed_selection_policy(
    policy_path: Optional[Path] = None,
) -> XauUsdSignalCalibrationSelectionPolicy:
    """Load and validate authoritative XAUUSD calibration selection policy from disk."""
    if policy_path is not None:
        path = policy_path
    elif DEFAULT_POLICY_PATH_V2.exists():
        path = DEFAULT_POLICY_PATH_V2
    else:
        path = DEFAULT_POLICY_PATH

    if not path.exists():
        raise FileNotFoundError(f"Authoritative selection policy not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return XauUsdSignalCalibrationSelectionPolicy.from_dict(data)
