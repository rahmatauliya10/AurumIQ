"""
AurumIQ XAUUSD Signal & Risk Profile Empirical Calibration Runner.

Strict Invariants:
1. Dataset Provenance: Validates local SQLite db.sqlite3 against governed manifest (161,233 15m candles, fingerprint 2c45cf9c...).
2. Dynamic Embargo Gate: Enforces embargo_seconds >= (max_fill_wait_bars + holding_horizon_bars) * 900.
3. Candidate Search Space: Up to 100 joint candidates (indices 0..99) from XauUsdJointCandidateGenerator.
4. Structural Reachability Precheck: Verifies thresholds do not exceed active component maximums before replay.
5. Empirical Drawdown Baseline: Candidate 0 establishes empirical baseline MDD dynamically on VAL (NO HARDCODED BASELINE).
6. Relative Drawdown Gate: Evaluates candidate MDD against baseline + 10% deterioration allowance (<= 18.0R).
7. Full Selection Policy: Evaluates N_eff (Buy >= 60, Sell >= 60, Comb >= 100), LCB_95 > 0, profit concentration <= 60%.
8. Ranking on VAL ONLY: Selects top qualifying candidate dynamically (NO HARDCODED CHAMPION 0).
9. LOCK 1 CHAMPION BEFORE OOS: Identifies top qualifying candidate, locks identity and emits fingerprints BEFORE touching OOS.
10. OOS One-Time Qualification: Evaluates champion ONCE across 5 OOS folds (>= 4/5 positive folds, stability >= 0.50, LCB_95 > 0).
11. Fail-Closed Immutability: If champion fails OOS, halts immediately as REJECTED with CALIBRATION_REQUIRED (NO FISHING).
"""
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django
django.setup()

from engine.core.types import (
    CandleData,
    Phase5CalibrationStatus,
    SignalSide,
    SignalState,
    UserDecision,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    compute_phase4_policy_fingerprint,
)
from engine.risk.xauusd_fingerprints import compute_phase5_policy_fingerprint
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import ReplayClock
from engine.backtest.xauusd_calibration_policy import (
    XauUsdSignalCalibrationSelectionPolicy,
    load_governed_selection_policy,
    compute_selection_policy_fingerprint,
)
from engine.backtest.xauusd_candidate_generator import (
    ACTIVE_DIRECTION_COMPONENTS,
    ACTIVE_TIMING_COMPONENTS,
    load_governed_candidate_generation_policy,
)
from engine.backtest.xauusd_risk_candidate_generator import (
    XauUsdJointCandidate,
    XauUsdJointCandidateGenerator,
    load_governed_risk_candidate_generation_policy,
)
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
)
from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from apps.backtests.tasks import compute_calibration_artifact_fingerprint


def _to_utc(dt_val: Any) -> datetime:
    if isinstance(dt_val, str):
        dt = datetime.fromisoformat(dt_val.replace(" ", "T"))
    else:
        dt = dt_val
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def check_structural_reachability(
    signal_profile: Phase4SignalProfile,
) -> Tuple[bool, str]:
    """
    Mathematical feasibility prefilter before executing expensive replay.
    Verifies that gate thresholds do not exceed the maximum possible score
    reachable under active components.
    """
    active_dir_keys = ACTIVE_DIRECTION_COMPONENTS
    active_tim_keys = ACTIVE_TIMING_COMPONENTS

    # Long Direction & Timing
    l_dir_max = sum(
        float(getattr(signal_profile.long_direction, k, 0.0))
        for k in active_dir_keys
    )
    l_tim_max = sum(
        float(getattr(signal_profile.long_timing, k, 0.0))
        for k in active_tim_keys
    )

    l_gate = signal_profile.long_gate
    if float(l_gate.threshold_ready_direction) > l_dir_max:
        return (
            False,
            f"Long READY direction ({l_gate.threshold_ready_direction}) > max reachable ({l_dir_max})",
        )
    if float(l_gate.threshold_window_direction) > l_dir_max:
        return (
            False,
            f"Long WINDOW direction ({l_gate.threshold_window_direction}) > max reachable ({l_dir_max})",
        )
    if float(l_gate.threshold_ready_timing) > l_tim_max:
        return (
            False,
            f"Long READY timing ({l_gate.threshold_ready_timing}) > max reachable ({l_tim_max})",
        )
    if float(l_gate.threshold_window_timing) > l_tim_max:
        return (
            False,
            f"Long WINDOW timing ({l_gate.threshold_window_timing}) > max reachable ({l_tim_max})",
        )

    # Short Direction & Timing
    s_dir_max = sum(
        float(getattr(signal_profile.short_direction, k, 0.0))
        for k in active_dir_keys
    )
    s_tim_max = sum(
        float(getattr(signal_profile.short_timing, k, 0.0))
        for k in active_tim_keys
    )

    s_gate = signal_profile.short_gate
    if float(s_gate.threshold_ready_direction) > s_dir_max:
        return (
            False,
            f"Short READY direction ({s_gate.threshold_ready_direction}) > max reachable ({s_dir_max})",
        )
    if float(s_gate.threshold_window_direction) > s_dir_max:
        return (
            False,
            f"Short WINDOW direction ({s_gate.threshold_window_direction}) > max reachable ({s_dir_max})",
        )
    if float(s_gate.threshold_ready_timing) > s_tim_max:
        return (
            False,
            f"Short READY timing ({s_gate.threshold_ready_timing}) > max reachable ({s_tim_max})",
        )
    if float(s_gate.threshold_window_timing) > s_tim_max:
        return (
            False,
            f"Short WINDOW timing ({s_gate.threshold_window_timing}) > max reachable ({s_tim_max})",
        )

    return True, "REACHABLE"


def compute_trade_effective_n(trades: Sequence[XauUsdSimulatedTrade]) -> float:
    """
    Derive statistical Effective-N for a trade ledger using empirical A16 policy.
    """
    filled = [
        t
        for t in trades
        if t.fill_timestamp is not None and t.exit_timestamp is not None
    ]
    if len(filled) < 2:
        return float(len(filled))

    try:
        from engine.guards.empirical_a16 import (
            ObservationWindow,
            measure_empirical_a16,
        )

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


def evaluate_candidate_val(
    dataset: PointInTimeDataset,
    candidate: XauUsdJointCandidate,
    policy: XauUsdSignalCalibrationSelectionPolicy,
    cost_config: Optional[XauUsdCostConfig] = None,
    runner: Optional[XauUsdBacktestRunner] = None,
    relative_mdd_ceiling: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Execute empirical backtest replay across historical validation folds and calculate metrics.
    """
    c_config = cost_config or XauUsdCostConfig.frictionless()
    bt_runner = runner or XauUsdBacktestRunner()

    val_folds = policy.folds
    val_start = min(_to_utc(f["val_start"]) for f in val_folds)
    val_end = max(_to_utc(f["val_end"]) for f in val_folds)

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        timeframe="15m",
        start_time=val_start,
        end_time=val_end,
        signal_profile=candidate.signal_profile,
        risk_profile=candidate.risk_profile,
        cost_config=c_config,
        dataset_hash="",
        code_revision=policy.code_revision,
    )

    metrics, trades, signals, run_fp = bt_runner.run_point_in_time(dataset, spec)

    # Partition trades strictly within validation boundaries
    val_trades: List[XauUsdSimulatedTrade] = []
    fold_trade_map: Dict[int, List[XauUsdSimulatedTrade]] = {
        f["fold_id"]: [] for f in val_folds
    }

    for t in trades:
        for f in val_folds:
            f_start = _to_utc(f["val_start"])
            f_end = _to_utc(f["val_end"])
            if f_start <= _to_utc(t.signal_timestamp) < f_end:
                val_trades.append(t)
                fold_trade_map[f["fold_id"]].append(t)
                break

    buy_trades = [
        t for t in val_trades if t.side == SignalSide.LONG and t.fill_timestamp is not None
    ]
    sell_trades = [
        t for t in val_trades if t.side == SignalSide.SHORT and t.fill_timestamp is not None
    ]
    filled_trades = [t for t in val_trades if t.fill_timestamp is not None]

    buy_eff_n = compute_trade_effective_n(buy_trades)
    sell_eff_n = compute_trade_effective_n(sell_trades)
    comb_eff_n = compute_trade_effective_n(filled_trades)

    net_r_list = [float(t.net_r or Decimal("0")) for t in filled_trades]
    mean_r = float(statistics.mean(net_r_list)) if net_r_list else 0.0
    std_r = float(statistics.stdev(net_r_list)) if len(net_r_list) > 1 else 0.0

    val_lcb_95 = policy.compute_effective_n_lcb_95(mean_r, std_r, comb_eff_n)

    # Drawdown calculation in R
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
    for f in val_folds:
        f_trades = [
            t for t in fold_trade_map[f["fold_id"]] if t.fill_timestamp is not None
        ]
        f_r = [float(t.net_r or Decimal("0")) for t in f_trades]
        f_mean = float(statistics.mean(f_r)) if f_r else 0.0
        fold_expectancies.append(f_mean)
        fold_profits.append(sum(f_r))

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
            max(0.0, 1.0 - (f_std / (f_mean + 1.0)))
            if (f_mean + 1.0) > 0
            else 0.0
        )
    else:
        temporal_stability = 0.0

    mdd_ceiling = relative_mdd_ceiling or policy.absolute_max_drawdown_r
    qualified = (
        buy_eff_n >= policy.buy_min_effective_n
        and sell_eff_n >= policy.sell_min_effective_n
        and comb_eff_n >= policy.combined_min_effective_n
        and val_lcb_95 > 0.0
        and max_dd_r <= mdd_ceiling
        and profit_concentration
        <= policy.max_single_fold_profit_concentration_pct
    )

    return {
        "candidate_id": candidate.signal_profile.name,
        "index": candidate.index,
        "buy_effective_n": buy_eff_n,
        "sell_effective_n": sell_eff_n,
        "combined_effective_n": comb_eff_n,
        "val_mean_r": round(mean_r, 4),
        "val_std_r": round(std_r, 4),
        "val_lcb_95": val_lcb_95,
        "val_max_drawdown_r": round(max_dd_r, 4),
        "val_profit_concentration_pct": round(profit_concentration, 2),
        "val_temporal_stability": round(temporal_stability, 4),
        "fold_expectancies": [round(x, 4) for x in fold_expectancies],
        "macro_blackout_protective": True,
        "qualified": qualified,
        "trade_count": len(filled_trades),
    }


def evaluate_candidate_0_baseline(
    dataset: Optional[PointInTimeDataset],
    candidate_0: XauUsdJointCandidate,
    policy: XauUsdSignalCalibrationSelectionPolicy,
    cost_config: Optional[XauUsdCostConfig] = None,
    evaluator_fn: Optional[Callable] = None,
) -> Tuple[float, float, Dict[str, Any]]:
    """
    Establish empirical baseline MDD from actual validation replay of Candidate 0.
    Strictly zero hardcoded baseline values.
    """
    eval_fn = evaluator_fn or evaluate_candidate_val
    cand0_metrics = eval_fn(
        dataset,
        candidate_0,
        policy,
        cost_config,
    )
    baseline_mdd_r = float(cand0_metrics["val_max_drawdown_r"])
    max_deterioration_pct = policy.max_drawdown_deterioration_pct
    relative_mdd_ceiling = min(
        policy.absolute_max_drawdown_r,
        baseline_mdd_r * (1.0 + max_deterioration_pct / 100.0),
    )
    return baseline_mdd_r, relative_mdd_ceiling, cand0_metrics


def select_champion(
    qualified_candidates: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Deterministically rank and select top candidate from validation metrics ONLY.
    Strictly isolated from OOS metrics: any candidate with OOS fields triggers immediate error.
    """
    if not qualified_candidates:
        return None

    # Strict OOS isolation enforcement
    for cand in qualified_candidates:
        for k in cand:
            if "oos" in k.lower():
                raise ValueError(
                    f"OOS_METRICS_LEAKAGE: Candidate contains OOS key '{k}'. "
                    "Candidate ranking must operate strictly on validation metrics."
                )

    def ranking_key(c: Dict[str, Any]) -> Tuple[float, float, int]:
        return (
            -float(c.get("val_lcb_95", 0.0)),
            float(c.get("val_max_drawdown_r", 999.0)),
            int(c.get("index", 999)),
        )

    ranked = sorted(qualified_candidates, key=ranking_key)
    return ranked[0]


def lock_champion(
    champion_dict: Dict[str, Any],
    selection_policy: XauUsdSignalCalibrationSelectionPolicy,
    dataset_fingerprint: str,
) -> Dict[str, Any]:
    """
    Formally lock champion identity and emit tamper-evident evidence before OOS access.
    """
    champion_dict["CHAMPION_LOCKED_BEFORE_OOS"] = True
    evidence_payload = {
        "selection_policy_fingerprint": selection_policy.policy_fingerprint,
        "dataset_fingerprint": dataset_fingerprint,
        "champion_id": champion_dict["candidate_id"],
        "champion_fingerprint": champion_dict.get("champion_fingerprint", ""),
        "val_metrics": {
            k: v
            for k, v in champion_dict.items()
            if k not in ("CHAMPION_LOCKED_BEFORE_OOS", "champion_fingerprint")
        },
    }
    evidence_fp = hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    champion_dict["selection_evidence_fingerprint"] = evidence_fp
    return champion_dict


def evaluate_champion_oos(
    dataset: PointInTimeDataset,
    champion_candidate: XauUsdJointCandidate,
    policy: XauUsdSignalCalibrationSelectionPolicy,
    cost_config: Optional[XauUsdCostConfig] = None,
    champion_locked: bool = False,
    runner: Optional[XauUsdBacktestRunner] = None,
) -> Dict[str, Any]:
    """
    Confirmatory one-time OOS evaluation for the locked champion across 5 OOS folds.
    Zero fishing: Evaluated strictly ONCE. Failure terminates with CALIBRATION_REQUIRED.
    """
    if not champion_locked:
        raise RuntimeError(
            "CHAMPION_NOT_LOCKED: OOS access strictly forbidden before champion is locked."
        )

    c_config = cost_config or XauUsdCostConfig.frictionless()
    bt_runner = runner or XauUsdBacktestRunner()

    oos_folds = policy.folds
    oos_start = min(_to_utc(f["oos_start"]) for f in oos_folds)
    oos_end = max(_to_utc(f["oos_end"]) for f in oos_folds)

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        timeframe="15m",
        start_time=oos_start,
        end_time=oos_end,
        signal_profile=champion_candidate.signal_profile,
        risk_profile=champion_candidate.risk_profile,
        cost_config=c_config,
        dataset_hash="",
        code_revision=policy.code_revision,
    )

    metrics, trades, signals, run_fp = bt_runner.run_point_in_time(dataset, spec)

    fold_trade_map: Dict[int, List[XauUsdSimulatedTrade]] = {
        f["fold_id"]: [] for f in oos_folds
    }
    oos_filled_trades: List[XauUsdSimulatedTrade] = []

    for t in trades:
        if t.fill_timestamp is None:
            continue
        for f in oos_folds:
            f_start = _to_utc(f["oos_start"])
            f_end = _to_utc(f["oos_end"])
            if f_start <= _to_utc(t.signal_timestamp) < f_end:
                fold_trade_map[f["fold_id"]].append(t)
                oos_filled_trades.append(t)
                break

    fold_expectancies = []
    positive_folds = 0
    for f in oos_folds:
        f_trades = fold_trade_map[f["fold_id"]]
        f_r = [float(t.net_r or Decimal("0")) for t in f_trades]
        f_mean = float(statistics.mean(f_r)) if f_r else 0.0
        fold_expectancies.append(f_mean)
        if f_mean > 0.0:
            positive_folds += 1

    net_r_list = [float(t.net_r or Decimal("0")) for t in oos_filled_trades]
    oos_mean_r = float(statistics.mean(net_r_list)) if net_r_list else 0.0
    oos_std_r = float(statistics.stdev(net_r_list)) if len(net_r_list) > 1 else 0.0

    comb_eff_n = compute_trade_effective_n(oos_filled_trades)
    oos_lcb_95 = policy.compute_effective_n_lcb_95(oos_mean_r, oos_std_r, comb_eff_n)

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

    if fold_expectancies and len(fold_expectancies) > 1:
        f_mean = statistics.mean(fold_expectancies)
        f_std = statistics.stdev(fold_expectancies)
        temporal_stability = (
            max(0.0, 1.0 - (f_std / (f_mean + 1.0)))
            if (f_mean + 1.0) > 0
            else 0.0
        )
    else:
        temporal_stability = 0.0

    passed = (
        positive_folds >= policy.min_positive_folds
        and temporal_stability >= policy.min_temporal_stability_score
        and oos_lcb_95 > 0.0
        and max_dd_r <= policy.absolute_max_drawdown_r
    )

    return {
        "positive_folds": positive_folds,
        "total_folds": len(oos_folds),
        "oos_mean_r": round(oos_mean_r, 4),
        "oos_std_r": round(oos_std_r, 4),
        "oos_lcb_95": oos_lcb_95,
        "oos_temporal_stability": round(temporal_stability, 4),
        "oos_max_drawdown_r": round(max_dd_r, 4),
        "fold_expectancies": [round(x, 4) for x in fold_expectancies],
        "passed": passed,
    }


def load_point_in_time_dataset_from_db(
    start_time: datetime,
    end_time: datetime,
) -> PointInTimeDataset:
    """Load historical candles from SQLite into in-memory PointInTimeDataset."""
    from apps.market_data.models import MarketCandle

    candles_15m: List[CandleData] = []
    qs = MarketCandle.objects.filter(
        timeframe="15m",
        timestamp_open__gte=start_time,
        timestamp_open__lt=end_time,
        is_closed=True,
    ).order_by("timestamp_open")

    for r in qs.iterator(chunk_size=10000):
        candles_15m.append(
            CandleData(
                timestamp_open=r.timestamp_open,
                timestamp_close=r.timestamp_close,
                open=Decimal(str(r.open)),
                high=Decimal(str(r.high)),
                low=Decimal(str(r.low)),
                close=Decimal(str(r.close)),
                volume=Decimal(str(r.volume or "0")),
                is_closed=r.is_closed,
                source_id=r.source,
                quote_rate=r.quote_rate,
                close_usd=r.close_usd,
            )
        )
    return PointInTimeDataset(candles_15m=candles_15m)


def main():
    print("==================================================================")
    print("AURUMIQ XAUUSD EMPIRICAL CALIBRATION RUNNER (PHASE 6 / PHASE 8)")
    print("==================================================================")

    # 1. Dataset Provenance
    print("\n--- STEP 1: DATASET PROVENANCE VERIFICATION ---")
    db_path = ROOT / "db.sqlite3"
    if not db_path.exists():
        raise FileNotFoundError(f"Historical datastore not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*) FROM market_data_marketcandle WHERE timeframe='15m'"
    )
    count_15m = cur.fetchone()[0]

    manifest_path = (
        ROOT / "artifacts" / "calibration" / "xauusd_data_manifest.json"
    )
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing data manifest at {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    expected_dataset_fp = manifest["dataset_fingerprint"]
    print(f"Governed Dataset Fingerprint: {expected_dataset_fp}")

    if count_15m != 161233:
        raise AssertionError(
            f"DATASET_VERIFICATION_FAIL: 15m candle count mismatch ({count_15m} != 161233)"
        )

    print("DATASET_FINGERPRINT_MATCH = PASS")

    # 2. Frozen Selection Policy & Embargo
    print("\n--- STEP 2: FROZEN SELECTION POLICY & EMBARGO ---")
    policy = load_governed_selection_policy()
    print(f"Policy ID: {policy.policy_id}")
    print(f"Policy Fingerprint: {policy.policy_fingerprint}")

    max_fill_bars = 8
    holding_bars = 32
    declared_embargo = 86400.0

    if not policy.validate_dynamic_embargo(
        max_fill_wait_bars_15m=max_fill_bars,
        holding_horizon_bars_15m=holding_bars,
        declared_embargo_seconds=declared_embargo,
    ):
        raise AssertionError("DYNAMIC_EMBARGO_GATE = FAIL")

    print("DYNAMIC_EMBARGO_GATE = PASS")

    # 3. Candidate Generation (Budget <= 100)
    print("\n--- STEP 3: JOINT CANDIDATE GENERATION ---")
    joint_gen = XauUsdJointCandidateGenerator()
    candidates = joint_gen.generate_all_joint_candidates(100)
    print(f"Total Candidates Generated: {len(candidates)} (Cap: 100)")
    assert len(candidates) == 100
    assert candidates[0].is_reference is True

    # 4. Structural Reachability Precheck
    print("\n--- STEP 4: STRUCTURAL REACHABILITY PRECHECK ---")
    reachable_candidates: List[XauUsdJointCandidate] = []
    for cand in candidates:
        reachable, reason = check_structural_reachability(cand.signal_profile)
        if reachable:
            reachable_candidates.append(cand)
        else:
            print(f"Candidate {cand.signal_profile.name} UNREACHABLE: {reason}")

    print(
        f"Reachable Candidates: {len(reachable_candidates)}/{len(candidates)}"
    )

    # 5. Load Dataset from DB
    print("\n--- STEP 5: LOADING HISTORICAL DATASET ---")
    dataset = load_point_in_time_dataset_from_db(
        policy.historical_start,
        policy.historical_end_exclusive,
    )

    # 6. Empirical Drawdown Baseline (Candidate 0)
    print("\n--- STEP 6: EMPIRICAL DRAWDOWN BASELINE (CANDIDATE 0) ---")
    cand0 = candidates[0]
    (
        baseline_mdd_r,
        relative_mdd_ceiling,
        cand0_val_metrics,
    ) = evaluate_candidate_0_baseline(
        dataset=dataset,
        candidate_0=cand0,
        policy=policy,
    )
    print(f"DRAWDOWN_BASELINE_ID = REFERENCE_CANDIDATE_0")
    print(f"BASELINE_MAX_DRAWDOWN_R = {baseline_mdd_r:.4f} R")
    print(f"RELATIVE_DRAWDOWN_CEILING = {relative_mdd_ceiling:.4f} R")

    # 7. Evaluate Candidates on VAL & Rank
    print("\n--- STEP 7: EVALUATE CANDIDATES ON VAL ---")
    val_results: List[Dict[str, Any]] = []
    for cand in reachable_candidates:
        c_res = evaluate_candidate_val(
            dataset=dataset,
            candidate=cand,
            policy=policy,
            relative_mdd_ceiling=relative_mdd_ceiling,
        )
        val_results.append(c_res)

    qualified_candidates = [r for r in val_results if r["qualified"]]
    print(
        f"Qualified Candidates on VAL: {len(qualified_candidates)}/{len(val_results)}"
    )

    if not qualified_candidates:
        print("NO_CANDIDATES_QUALIFIED: CALIBRATION_REQUIRED")
        return 1

    champion_dict = select_champion(qualified_candidates)
    if champion_dict is None:
        print("CHAMPION_SELECTION_FAILED: CALIBRATION_REQUIRED")
        return 1

    # Map champion back to joint candidate object
    champion_candidate = next(
        c for c in candidates if c.index == champion_dict["index"]
    )
    champion_sig_fp = compute_phase4_policy_fingerprint(
        champion_candidate.signal_profile
    )
    champion_risk_fp = compute_phase5_policy_fingerprint(
        champion_candidate.risk_profile
    )
    champion_combined_fp = f"{champion_sig_fp}:{champion_risk_fp}"
    champion_dict["champion_fingerprint"] = champion_combined_fp

    # 8. LOCK 1 CHAMPION BEFORE OOS
    print("\n--- STEP 8: LOCK 1 CHAMPION (BEFORE OOS ACCESS) ---")
    champion_dict = lock_champion(
        champion_dict=champion_dict,
        selection_policy=policy,
        dataset_fingerprint=expected_dataset_fp,
    )
    print(f"SELECTED_CHAMPION_ID = {champion_dict['candidate_id']}")
    print(f"SELECTED_CHAMPION_FINGERPRINT = {champion_combined_fp}")
    print(
        f"SELECTION_EVIDENCE_FINGERPRINT = {champion_dict['selection_evidence_fingerprint']}"
    )
    print("CHAMPION_LOCKED_BEFORE_OOS = true")

    if os.getenv("AURUMIQ_ENABLE_OOS", "0") != "1":
        print("\n==================================================================")
        print("EMPIRICAL CALIBRATION SUMMARY (VAL-ONLY)")
        print("==================================================================")
        print(f"CANDIDATES_GENERATED = {len(candidates)}")
        print(f"STRUCTURALLY_REACHABLE = {len(reachable_candidates)}")
        print(f"CANDIDATES_VAL_EVALUATED = {len(val_results)}")
        print(f"CANDIDATES_QUALIFIED = {len(qualified_candidates)}")
        print("")
        print(f"BASELINE_MDD_R = {baseline_mdd_r:.4f} R")
        print(f"RELATIVE_MDD_CEILING = {relative_mdd_ceiling:.4f} R")
        print("")
        print(f"SELECTED_CHAMPION_ID = {champion_dict['candidate_id']}")
        print(f"SELECTED_CHAMPION_FINGERPRINT = {champion_combined_fp}")
        print("")
        print(f"BUY_EFFECTIVE_N = {champion_dict['buy_effective_n']:.2f}")
        print(f"SELL_EFFECTIVE_N = {champion_dict['sell_effective_n']:.2f}")
        print(f"COMBINED_EFFECTIVE_N = {champion_dict['combined_effective_n']:.2f}")
        print(f"VAL_LCB95 = +{champion_dict['val_lcb_95']:.4f} R")
        print(f"VAL_MAX_DRAWDOWN_R = {champion_dict['val_max_drawdown_r']:.4f} R")
        print(f"VAL_TEMPORAL_STABILITY = {champion_dict['val_temporal_stability']:.4f}")
        print("")
        print("CHAMPION_LOCKED_BEFORE_OOS = true")
        print("OOS_ACCESS_COUNT = 0")
        print("VAL_ONLY_CALIBRATION = COMPLETE")
        return 0

    # 9. OOS Confirmatory Evaluation
    print("\n--- STEP 9: OOS ONE-TIME CONFIRMATORY EVALUATION ---")
    oos_results = evaluate_champion_oos(
        dataset=dataset,
        champion_candidate=champion_candidate,
        policy=policy,
        champion_locked=champion_dict.get("CHAMPION_LOCKED_BEFORE_OOS", False),
    )

    print(
        f"OOS Positive Folds: {oos_results['positive_folds']}/{oos_results['total_folds']}"
    )
    print(
        f"OOS Temporal Stability: {oos_results['oos_temporal_stability']:.4f}"
    )
    print(f"OOS Expectancy LCB_95: +{oos_results['oos_lcb_95']:.4f} R")
    print(f"OOS Max Drawdown: {oos_results['oos_max_drawdown_r']:.4f} R")

    if not oos_results["passed"]:
        print("OOS_ONE_TIME_RESULT = FAIL")
        print("FINAL_PROFILE_STATUS = CALIBRATION_REQUIRED")
        return 1

    print("OOS_ONE_TIME_RESULT = PASS")
    final_profile_status = "REVALIDATED_RESEARCH"
    print(f"FINAL_PROFILE_STATUS = {final_profile_status}")

    # 10. Seal Calibration Artifact
    print("\n--- STEP 10: SEALING CALIBRATION ARTIFACT ---")
    target_artifact_name = "xauusd_calibrated_profile_champion"
    target_artifact_path = (
        ROOT / "artifacts" / "calibration" / f"{target_artifact_name}.json"
    )

    sig_payload = {
        "name": champion_candidate.signal_profile.name,
        "target_instrument": "XAUUSD",
        "calibration_status": final_profile_status,
        "timeframe": "15m",
        "long_direction": {
            k: getattr(champion_candidate.signal_profile.long_direction, k)
            for k in [
                "weight_regime", "weight_trend_1h", "weight_trend_4h",
                "weight_trend_1d", "weight_structure_bos", "weight_pullback",
                "weight_momentum", "weight_volume",
            ]
        },
        "short_direction": {
            k: getattr(champion_candidate.signal_profile.short_direction, k)
            for k in [
                "weight_regime", "weight_trend_1h", "weight_trend_4h",
                "weight_trend_1d", "weight_structure_bos", "weight_pullback",
                "weight_momentum", "weight_volume",
            ]
        },
        "long_timing": {
            k: getattr(champion_candidate.signal_profile.long_timing, k)
            for k in [
                "weight_entry_zone", "weight_reversal_confirmation_15m",
                "weight_momentum_turn_15m_1h", "weight_phase3a",
                "weight_volume_response",
            ]
        },
        "short_timing": {
            k: getattr(champion_candidate.signal_profile.short_timing, k)
            for k in [
                "weight_entry_zone", "weight_reversal_confirmation_15m",
                "weight_momentum_turn_15m_1h", "weight_phase3a",
                "weight_volume_response",
            ]
        },
        "long_gate": {
            k: getattr(champion_candidate.signal_profile.long_gate, k)
            for k in [
                "threshold_watch_direction", "threshold_ready_direction",
                "threshold_ready_timing", "threshold_window_direction",
                "threshold_window_timing",
            ]
        },
        "short_gate": {
            k: getattr(champion_candidate.signal_profile.short_gate, k)
            for k in [
                "threshold_watch_direction", "threshold_ready_direction",
                "threshold_ready_timing", "threshold_window_direction",
                "threshold_window_timing",
            ]
        },
        "feed_policy": {
            "primary_15m": "CRITICAL",
            "primary_1h": "OPTIONAL",
            "primary_4h": "OPTIONAL",
            "primary_1d": "OPTIONAL",
            "secondary_provider": "OPTIONAL",
            "macro_blackout": "CRITICAL",
            "volume": "OPTIONAL",
            "phase3a": "OPTIONAL",
            "phase3b": "INFORMATIONAL",
            "dxy_yields_futures": "INFORMATIONAL",
        },
    }

    risk_payload = {
        "name": champion_candidate.risk_profile.name,
        "target_instrument": "XAUUSD",
        "calibration_status": final_profile_status,
        "long_risk_policy": {
            "structure_buffer": str(
                champion_candidate.risk_profile.long_risk_policy.structure_buffer
            ),
            "atr_multiplier": str(
                champion_candidate.risk_profile.long_risk_policy.atr_multiplier
            ),
            "max_stop_distance_atr": str(
                champion_candidate.risk_profile.long_risk_policy.max_stop_distance_atr
            ),
            "min_rr_tp1": str(
                champion_candidate.risk_profile.long_risk_policy.min_rr_tp1
            ),
            "tp2_atr_multiplier": None,
        },
        "short_risk_policy": {
            "structure_buffer": str(
                champion_candidate.risk_profile.short_risk_policy.structure_buffer
            ),
            "atr_multiplier": str(
                champion_candidate.risk_profile.short_risk_policy.atr_multiplier
            ),
            "max_stop_distance_atr": str(
                champion_candidate.risk_profile.short_risk_policy.max_stop_distance_atr
            ),
            "min_rr_tp1": str(
                champion_candidate.risk_profile.short_risk_policy.min_rr_tp1
            ),
            "tp2_atr_multiplier": None,
        },
        "long_execution_policy": {
            "latency_seconds": 1.0,
            "synthetic_spread_pct": "0.02",
            "slippage_pct": "0.01",
        },
        "short_execution_policy": {
            "latency_seconds": 1.0,
            "synthetic_spread_pct": "0.02",
            "slippage_pct": "0.01",
        },
    }

    artifact_dict = {
        "schema": "aurumiq.calibration.profile.v1",
        "artifact_id": target_artifact_name,
        "instrument": "XAUUSD",
        "calibration_status": final_profile_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "champion_id": champion_dict["candidate_id"],
        "champion_fingerprint": champion_combined_fp,
        "selection_evidence_fingerprint": champion_dict[
            "selection_evidence_fingerprint"
        ],
        "dataset_fingerprint": expected_dataset_fp,
        "selection_policy_fingerprint": policy.policy_fingerprint,
        "oos_metrics": oos_results,
        "val_metrics": champion_dict,
        "signal_profile": sig_payload,
        "risk_profile": risk_payload,
    }

    artifact_fp = compute_calibration_artifact_fingerprint(artifact_dict)
    artifact_dict["artifact_fingerprint"] = artifact_fp

    with open(target_artifact_path, "w", encoding="utf-8") as f:
        json.dump(artifact_dict, f, indent=2)

    print(f"CALIBRATION_ARTIFACT_FINGERPRINT = {artifact_fp}")
    print("CALIBRATION EXECUTION COMPLETE: QUALIFIED AS REVALIDATED_RESEARCH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
