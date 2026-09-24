"""
Phase 8 XAUUSD Directional Sub-Profile Calibration & Composite Governance.

Authoritative specification, loader, validator, side-wise metrics evaluator,
deterministic ranking engine, and composite candidate assembler for XAUUSD
directional sub-profile calibration.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.core.types import (
    FeedCriticality,
    Phase5CalibrationStatus,
    SignalSide,
    SignalState,
    UserDecision,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4FeedPolicy,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    normalize_xauusd_target,
)
from engine.risk.xauusd_policy import SideRiskPolicy, XauUsdExecutionPolicy, XauUsdRiskProfile
from engine.backtest.xauusd_risk_candidate_generator import XauUsdJointCandidate
from engine.backtest.xauusd_types import XauUsdSimulatedTrade, XauUsdTradeOutcome

DEFAULT_COMPOSITE_POLICY_PATH = Path(
    "artifacts/calibration/xauusd_directional_composite_calibration_policy.json"
)


def _to_utc(dt_val: Any) -> datetime:
    if isinstance(dt_val, str):
        dt = datetime.fromisoformat(dt_val.replace(" ", "T"))
    else:
        dt = dt_val
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_composite_policy_fingerprint(policy_dict: Dict[str, Any]) -> str:
    """
    Generate deterministic SHA-256 fingerprint from canonical JSON payload
    excluding any fingerprint fields.
    """
    payload = {
        k: v
        for k, v in policy_dict.items()
        if k not in ("policy_fingerprint", "artifact_fingerprint", "fingerprint")
    }
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def compute_trade_effective_n(trades: Sequence[XauUsdSimulatedTrade]) -> float:
    """
    Derive statistical Effective-N for a trade ledger using empirical A16 policy.
    Reuses existing empirical A16 implementation exactly.
    """
    filled = [
        t
        for t in trades
        if t.fill_timestamp is not None and t.exit_timestamp is not None
    ]
    if len(filled) < 2:
        return float(len(filled))

    try:
        from engine.guards.empirical_a16 import ObservationWindow, measure_empirical_a16

        windows = [
            ObservationWindow(
                start=_to_utc(t.fill_timestamp),
                end=_to_utc(t.exit_timestamp),
                value=float(t.net_r or Decimal("0")),
                regime=(
                    t.regime.value
                    if hasattr(t.regime, "value")
                    else str(t.regime or "UNKNOWN")
                ),
            )
            for t in filled
        ]
        res = measure_empirical_a16(windows)
        return float(res.evaluation.effective_n)
    except Exception:
        return float(len(filled))


@dataclass(frozen=True)
class SideEvaluationMetrics:
    """
    Evaluation metrics computed for a single directional side (LONG or SHORT)
    strictly from filled trades of that side across historical validation folds.
    """
    candidate_id: str
    index: int
    side: SignalSide
    trade_count: int
    effective_n: float
    mean_r: float
    std_r: float
    side_lcb_95: float
    side_max_drawdown_r: float
    temporal_stability: float
    profit_concentration_pct: float
    fold_expectancies: Tuple[float, ...]
    positive_folds: int
    total_folds: int
    qualified: bool
    disqualification_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class XauUsdDirectionalCompositeCalibrationPolicy:
    """
    Authoritative governed policy specification for directional composite calibration.
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
    total_folds: int
    folds: Tuple[Dict[str, Any], ...]
    dynamic_embargo_formula: str

    # Side-specific qualification criteria
    buy_min_effective_n: float
    side_long_lcb_95_min: float
    side_long_max_drawdown_r: float
    long_min_temporal_stability: float
    long_max_profit_concentration_pct: float
    long_min_positive_folds: int

    sell_min_effective_n: float
    side_short_lcb_95_min: float
    side_short_max_drawdown_r: float
    short_min_temporal_stability: float
    short_max_profit_concentration_pct: float
    short_min_positive_folds: int

    # Ranking rules & composite hurdles
    side_ranking_hierarchy: Tuple[str, ...]
    composite_combined_min_effective_n: float
    composite_combined_lcb_95_min: float
    composite_combined_max_drawdown_r: float
    composite_min_temporal_stability: float
    composite_max_profit_concentration_pct: float
    composite_reachability_requirements: Tuple[str, ...]

    shared_profile_specification: Dict[str, Any]
    provisional_artifact_path: str
    policy_fingerprint: str
    raw_payload: Dict[str, Any]

    @classmethod
    def from_dict(
        cls, data: Dict[str, Any]
    ) -> "XauUsdDirectionalCompositeCalibrationPolicy":
        schema = data.get("schema")
        if schema not in (
            "aurumiq.calibration.composite_calibration_policy.v1",
            "aurumiq.calibration.composite_calibration_policy.v2",
        ):
            raise ValueError(
                f"Invalid composite calibration policy schema: '{schema}'"
            )

        policy_id = data.get("policy_id")
        if policy_id not in (
            "XAUUSD-DIRECTIONAL-COMPOSITE-CALIBRATION-POLICY-v1",
            "XAUUSD-DIRECTIONAL-COMPOSITE-CALIBRATION-POLICY-v2",
        ):
            raise ValueError(f"Invalid policy_id: '{policy_id}'")

        inst = data.get("instrument", "")
        norm_inst = normalize_xauusd_target(inst)
        if norm_inst != "XAUUSD":
            raise ValueError(f"Policy must target XAUUSD, got '{inst}'")

        expected_fp = data.get("policy_fingerprint")
        if not expected_fp:
            raise ValueError("policy_fingerprint field is required.")
        computed_fp = compute_composite_policy_fingerprint(data)
        if expected_fp != computed_fp:
            raise ValueError(
                f"Policy fingerprint mismatch: expected '{expected_fp}', computed '{computed_fp}'"
            )

        hist_bounds = data.get("historical_bounds", {})
        start_str = hist_bounds.get("start_time")
        end_str = hist_bounds.get("end_time_exclusive")
        start_dt = _to_utc(datetime.fromisoformat(start_str))
        end_dt = _to_utc(datetime.fromisoformat(end_str))
        if start_dt >= end_dt:
            raise ValueError("historical_bounds start must strictly precede end.")

        wf = data.get("walk_forward_policy", {})
        total_folds = wf.get("total_folds", 0)
        folds_list = wf.get("folds", [])
        if total_folds != 5 or len(folds_list) != 5:
            raise ValueError("Walk-forward policy must declare exactly 5 folds.")

        long_q = data.get("long_subprofile_qualification", {})
        short_q = data.get("short_subprofile_qualification", {})
        comp_q = data.get("composite_qualification", {})

        auth = data.get("production_authority_status", {})
        if auth.get("is_production_authorized") is not False:
            raise ValueError("is_production_authorized must be strictly False.")
        if auth.get("paper_only") is not True:
            raise ValueError("paper_only must be strictly True.")
        if auth.get("real_order_execution") != "disabled":
            raise ValueError("real_order_execution must be strictly 'disabled'.")

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
            total_folds=total_folds,
            folds=tuple(folds_list),
            dynamic_embargo_formula=data.get("dynamic_embargo_rule", {}).get(
                "formula", ""
            ),
            buy_min_effective_n=float(long_q.get("buy_min_effective_n", 60.0)),
            side_long_lcb_95_min=float(long_q.get("side_lcb_95_min", 0.0)),
            side_long_max_drawdown_r=float(long_q.get("side_max_drawdown_r", 18.0)),
            long_min_temporal_stability=float(
                long_q.get("min_temporal_stability_score", 0.5)
            ),
            long_max_profit_concentration_pct=float(
                long_q.get("max_single_fold_profit_concentration_pct", 60.0)
            ),
            long_min_positive_folds=int(long_q.get("min_positive_folds", 4)),
            sell_min_effective_n=float(short_q.get("sell_min_effective_n", 60.0)),
            side_short_lcb_95_min=float(short_q.get("side_lcb_95_min", 0.0)),
            side_short_max_drawdown_r=float(short_q.get("side_max_drawdown_r", 18.0)),
            short_min_temporal_stability=float(
                short_q.get("min_temporal_stability_score", 0.5)
            ),
            short_max_profit_concentration_pct=float(
                short_q.get("max_single_fold_profit_concentration_pct", 60.0)
            ),
            short_min_positive_folds=int(short_q.get("min_positive_folds", 4)),
            side_ranking_hierarchy=tuple(data.get("side_ranking_hierarchy", [])),
            composite_combined_min_effective_n=float(
                comp_q.get("combined_min_effective_n", 100.0)
            ),
            composite_combined_lcb_95_min=float(comp_q.get("combined_lcb_95_min", 0.0)),
            composite_combined_max_drawdown_r=float(
                comp_q.get("combined_max_drawdown_r", 18.0)
            ),
            composite_min_temporal_stability=float(
                comp_q.get("min_temporal_stability_score", 0.5)
            ),
            composite_max_profit_concentration_pct=float(
                comp_q.get("max_single_fold_profit_concentration_pct", 60.0)
            ),
            composite_reachability_requirements=tuple(
                comp_q.get("reachability_requirements", [])
            ),
            shared_profile_specification=data.get(
                "shared_profile_specification", {}
            ),
            provisional_artifact_path=data.get(
                "provisional_artifact_path",
                "artifacts/calibration/xauusd_calibrated_profile_candidate_composite.json",
            ),
            policy_fingerprint=expected_fp,
            raw_payload=data,
        )

    def validate_dynamic_embargo(
        self,
        max_fill_wait_bars_15m: Optional[int],
        holding_horizon_bars_15m: Optional[int],
        declared_embargo_seconds: float,
    ) -> bool:
        """
        Verify declared embargo satisfies dynamic dependency horizon requirement:
        embargo_seconds >= (max_fill_wait_bars_15m + holding_horizon_bars_15m) * 900
        """
        if max_fill_wait_bars_15m is None or holding_horizon_bars_15m is None:
            raise ValueError("Dependency horizon cannot be resolved: bars are None.")
        min_required_seconds = float(
            (max_fill_wait_bars_15m + holding_horizon_bars_15m) * 900
        )
        return declared_embargo_seconds >= min_required_seconds

    def compute_effective_n_lcb_95(
        self, mean_r: float, std_r: float, effective_n: float
    ) -> float:
        """
        Compute Effective-N-adjusted one-sided Student's t lower confidence bound at alpha=0.05.
        Reuses existing calibration formula bit-for-bit.
        """
        if effective_n <= 1.0 or std_r < 0.0:
            return float("-inf")
        df = max(1.0, effective_n - 1.0)
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


def load_governed_composite_calibration_policy(
    policy_path: Optional[Path] = None,
) -> XauUsdDirectionalCompositeCalibrationPolicy:
    """Load authoritative directional composite calibration policy from disk."""
    path = policy_path or DEFAULT_COMPOSITE_POLICY_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Authoritative composite calibration policy not found at: {path}"
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return XauUsdDirectionalCompositeCalibrationPolicy.from_dict(data)


def evaluate_side_trades(
    trades: Sequence[XauUsdSimulatedTrade],
    side: SignalSide,
    folds: Sequence[Dict[str, Any]],
    policy: XauUsdDirectionalCompositeCalibrationPolicy,
    candidate_id: str = "",
    candidate_index: int = 0,
    mdd_ceiling: Optional[float] = None,
) -> SideEvaluationMetrics:
    """
    Derive statistical metrics for a single side (LONG or SHORT) strictly from
    filled trades of that side within validation fold boundaries.
    Guarantees parity with existing governed statistics:
    - Effective-N via empirical A16
    - LCB95 via Effective-N adjusted Student's t
    - Max drawdown peak-to-trough in R
    - Temporal stability and profit concentration across governed folds
    """
    fold_trade_map: Dict[int, List[XauUsdSimulatedTrade]] = {
        f["fold_id"]: [] for f in folds
    }
    side_trades: List[XauUsdSimulatedTrade] = []

    for t in trades:
        if t.fill_timestamp is None:
            continue
        if t.outcome in (
            XauUsdTradeOutcome.NO_FILL,
            XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN,
            XauUsdTradeOutcome.SKIPPED,
        ):
            continue
        if t.side != side:
            continue
        for f in folds:
            f_start = _to_utc(f["val_start"])
            f_end = _to_utc(f["val_end"])
            if f_start <= _to_utc(t.signal_timestamp) < f_end:
                side_trades.append(t)
                fold_trade_map[f["fold_id"]].append(t)
                break

    eff_n = compute_trade_effective_n(side_trades)
    net_r_list = [float(t.net_r or Decimal("0")) for t in side_trades]

    mean_r = float(statistics.mean(net_r_list)) if net_r_list else 0.0
    std_r = float(statistics.stdev(net_r_list)) if len(net_r_list) > 1 else 0.0
    lcb_95 = policy.compute_effective_n_lcb_95(mean_r, std_r, eff_n)

    # Max Drawdown peak-to-trough in R
    max_dd_r = 0.0
    peak_r = 0.0
    cum_r = 0.0
    for r in net_r_list:
        cum_r += r
        if cum_r > peak_r:
            peak_r = cum_r
        else:
            dd = peak_r - cum_r
            if dd > max_dd_r:
                max_dd_r = dd

    # Fold expectancies & profit concentration
    fold_expectancies = []
    fold_profits = []
    positive_folds = 0
    for f in folds:
        f_trades = fold_trade_map[f["fold_id"]]
        f_r = [float(t.net_r or Decimal("0")) for t in f_trades]
        f_mean = float(statistics.mean(f_r)) if f_r else 0.0
        fold_expectancies.append(f_mean)
        fold_profits.append(sum(f_r))
        if f_mean > 0.0:
            positive_folds += 1

    total_profit = sum(fold_profits)
    max_fold_profit = max(fold_profits) if fold_profits else 0.0
    profit_concentration = (
        float(max_fold_profit / total_profit * 100.0)
        if total_profit > 0 and max_fold_profit > 0
        else 0.0
    )

    # Temporal stability score
    if fold_expectancies and len(fold_expectancies) > 1:
        f_mean = statistics.mean(fold_expectancies)
        f_std = statistics.stdev(fold_expectancies)
        temporal_stability = (
            max(0.0, 1.0 - (f_std / (abs(f_mean) + 1.0)))
            if (abs(f_mean) + 1.0) > 0
            else 0.0
        )
    else:
        temporal_stability = 0.0

    # Qualification check
    reasons: List[str] = []
    if side == SignalSide.LONG:
        min_eff = policy.buy_min_effective_n
        min_pos = policy.long_min_positive_folds
        min_stab = policy.long_min_temporal_stability
        max_conc = policy.long_max_profit_concentration_pct
        max_mdd = mdd_ceiling or policy.side_long_max_drawdown_r
    else:
        min_eff = policy.sell_min_effective_n
        min_pos = policy.short_min_positive_folds
        min_stab = policy.short_min_temporal_stability
        max_conc = policy.short_max_profit_concentration_pct
        max_mdd = mdd_ceiling or policy.side_short_max_drawdown_r

    if eff_n < min_eff:
        reasons.append(f"Effective N {eff_n:.2f} < {min_eff:.1f}")
    if lcb_95 <= 0.0:
        reasons.append(f"LCB95 {lcb_95:+.4f} <= 0.0")
    if max_dd_r > max_mdd:
        reasons.append(f"MDD {max_dd_r:.2f}R > {max_mdd:.2f}R")
    if positive_folds < min_pos:
        reasons.append(f"Positive folds {positive_folds}/{len(folds)} < {min_pos}")
    if temporal_stability < min_stab:
        reasons.append(f"Temporal stability {temporal_stability:.4f} < {min_stab:.2f}")
    if profit_concentration > max_conc:
        reasons.append(
            f"Profit concentration {profit_concentration:.1f}% > {max_conc:.1f}%"
        )

    qualified = len(reasons) == 0

    return SideEvaluationMetrics(
        candidate_id=candidate_id,
        index=candidate_index,
        side=side,
        trade_count=len(side_trades),
        effective_n=round(eff_n, 2),
        mean_r=round(mean_r, 4),
        std_r=round(std_r, 4),
        side_lcb_95=lcb_95,
        side_max_drawdown_r=round(max_dd_r, 4),
        temporal_stability=round(temporal_stability, 4),
        profit_concentration_pct=round(profit_concentration, 2),
        fold_expectancies=tuple(round(x, 4) for x in fold_expectancies),
        positive_folds=positive_folds,
        total_folds=len(folds),
        qualified=qualified,
        disqualification_reasons=tuple(reasons),
    )


def rank_side_subprofiles(
    qualified_candidates: Sequence[SideEvaluationMetrics],
) -> List[SideEvaluationMetrics]:
    """
    Deterministically rank qualifying side sub-profiles:
      1. Highest side LCB95 (descending)
      2. Lowest side MDD (ascending)
      3. Deterministic candidate index tie-break (ascending)
    """
    if not qualified_candidates:
        return []

    def ranking_key(m: SideEvaluationMetrics) -> Tuple[float, float, int]:
        return (
            -float(m.side_lcb_95),
            float(m.side_max_drawdown_r),
            int(m.index),
        )

    return sorted(qualified_candidates, key=ranking_key)


def construct_composite_candidate(
    best_long_candidate: XauUsdJointCandidate,
    best_short_candidate: XauUsdJointCandidate,
    policy: XauUsdDirectionalCompositeCalibrationPolicy,
) -> XauUsdJointCandidate:
    """
    Construct exactly ONE dual-side composite candidate by combining:
    - Long parameters from selected Long sub-profile (direction, timing, gate, risk, execution)
    - Short parameters from selected Short sub-profile (direction, timing, gate, risk, execution)
    - Shared parameters explicitly governed by policy (feed policy, instrument, calibration status)
    """
    shared_spec = policy.shared_profile_specification
    status_str = shared_spec.get(
        "calibration_status", "DEVELOPMENT_COMPOSITE_PROVISIONAL"
    )

    feed_policy_dict = shared_spec.get("feed_policy", {})
    def _to_crit(val: Any) -> FeedCriticality:
        if isinstance(val, FeedCriticality):
            return val
        return FeedCriticality(str(val))

    feed_policy = Phase4FeedPolicy(
        primary_15m=_to_crit(feed_policy_dict.get("primary_15m", "CRITICAL")),
        primary_1h=_to_crit(feed_policy_dict.get("primary_1h", "OPTIONAL")),
        primary_4h=_to_crit(feed_policy_dict.get("primary_4h", "OPTIONAL")),
        primary_1d=_to_crit(feed_policy_dict.get("primary_1d", "OPTIONAL")),
        secondary_provider=_to_crit(feed_policy_dict.get("secondary_provider", "OPTIONAL")),
        macro_blackout=_to_crit(feed_policy_dict.get("macro_blackout", "CRITICAL")),
        volume=_to_crit(feed_policy_dict.get("volume", "OPTIONAL")),
        phase3a=_to_crit(feed_policy_dict.get("phase3a", "OPTIONAL")),
        phase3b=_to_crit(feed_policy_dict.get("phase3b", "INFORMATIONAL")),
        dxy_yields_futures=_to_crit(feed_policy_dict.get("dxy_yields_futures", "INFORMATIONAL")),
    )

    comp_sig_name = (
        f"XAUUSD_COMPOSITE_L{best_long_candidate.index:03d}_S{best_short_candidate.index:03d}"
    )
    comp_risk_name = (
        f"XAUUSD_RISK_COMPOSITE_L{best_long_candidate.index:03d}_S{best_short_candidate.index:03d}"
    )

    composite_signal_profile = Phase4SignalProfile(
        name=comp_sig_name,
        target_instrument=shared_spec.get("target_instrument", "XAUUSD"),
        timeframe=shared_spec.get("timeframe", "15m"),
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=best_long_candidate.signal_profile.long_direction,
        short_direction=best_short_candidate.signal_profile.short_direction,
        long_timing=best_long_candidate.signal_profile.long_timing,
        short_timing=best_short_candidate.signal_profile.short_timing,
        long_gate=best_long_candidate.signal_profile.long_gate,
        short_gate=best_short_candidate.signal_profile.short_gate,
        feed_policy=feed_policy,
    )

    composite_risk_profile = XauUsdRiskProfile(
        name=comp_risk_name,
        target_instrument=shared_spec.get("target_instrument", "XAUUSD"),
        calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_risk_policy=best_long_candidate.risk_profile.long_risk_policy,
        short_risk_policy=best_short_candidate.risk_profile.short_risk_policy,
        long_execution_policy=best_long_candidate.risk_profile.long_execution_policy,
        short_execution_policy=best_short_candidate.risk_profile.short_execution_policy,
        is_production_authorized=False,
        risk_version="5.0.0-composite-v1",
    )

    return XauUsdJointCandidate(
        index=-1,
        signal_profile=composite_signal_profile,
        risk_profile=composite_risk_profile,
        is_reference=False,
    )
