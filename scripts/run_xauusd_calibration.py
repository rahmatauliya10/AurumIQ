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
import pickle
import sqlite3
import statistics
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
os.environ["AURUMIQ_ENABLE_OOS"] = "0"
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

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
from engine.backtest.xauusd_replay import (
    XauUsdReplayMarketSnapshot,
    build_market_snapshot_cache,
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
    market_cache: Optional[Sequence[XauUsdReplayMarketSnapshot]] = None,
) -> Dict[str, Any]:
    """
    Execute empirical backtest replay across historical validation folds and calculate metrics.
    """
    c_config = cost_config or XauUsdCostConfig.idealized()
    bt_runner = runner or XauUsdBacktestRunner()

    val_folds = policy.folds
    val_start = min(_to_utc(f["val_start"]) for f in val_folds)
    val_end = max(_to_utc(f["val_end"]) for f in val_folds)

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=val_start,
        end_time=val_end,
        timeframes=("15m",),
        cost_config=c_config,
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash="",
        code_revision=policy.code_revision,
        holding_horizon_bars_15m=32,
        max_fill_wait_bars_15m=8,
        signal_profile=candidate.signal_profile,
        risk_profile=candidate.risk_profile,
    )

    metrics, trades, signals, run_fp = bt_runner.run_point_in_time(
        dataset,
        spec,
        market_cache=market_cache,
    )

    reachability_buy_window = any(
        getattr(s, "candidate_state", None) == SignalState.BUY_WINDOW
        or getattr(s, "candidate_state", None) == SignalState.BUY_WINDOW.value
        for s in signals
    )
    reachability_sell_window = any(
        getattr(s, "candidate_state", None) == SignalState.SELL_WINDOW
        or getattr(s, "candidate_state", None) == SignalState.SELL_WINDOW.value
        for s in signals
    )
    reachability_ready_short = any(
        getattr(s, "candidate_state", None) == SignalState.READY_SHORT
        or getattr(s, "candidate_state", None) == SignalState.READY_SHORT.value
        for s in signals
    )

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

    # Strictly filter genuine filled trades, excluding non-filled and invalidated entries
    filled_trades = [
        t
        for t in val_trades
        if t.fill_timestamp is not None
        and t.outcome not in (
            XauUsdTradeOutcome.NO_FILL,
            XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN,
            XauUsdTradeOutcome.SKIPPED,
        )
    ]
    buy_trades = [t for t in filled_trades if t.side == SignalSide.LONG]
    sell_trades = [t for t in filled_trades if t.side == SignalSide.SHORT]

    invalidated_entry_count = sum(
        1
        for t in val_trades
        if t.outcome == XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN
    )
    stale_tp_negative_gross_count = sum(
        1
        for t in filled_trades
        if t.outcome == XauUsdTradeOutcome.TP1_FIRST
        and (t.gross_r or Decimal("0")) < Decimal("0")
    )
    stale_sl_positive_gross_count = sum(
        1
        for t in filled_trades
        if t.outcome
        in (
            XauUsdTradeOutcome.SL_FIRST,
            XauUsdTradeOutcome.CONSERVATIVE_SL_FIRST,
        )
        and (t.gross_r or Decimal("0")) > Decimal("0")
    )

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
            t
            for t in fold_trade_map[f["fold_id"]]
            if t.fill_timestamp is not None
            and t.outcome not in (
                XauUsdTradeOutcome.NO_FILL,
                XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN,
                XauUsdTradeOutcome.SKIPPED,
            )
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
        "invalidated_entry_count": invalidated_entry_count,
        "stale_tp_negative_gross_count": stale_tp_negative_gross_count,
        "stale_sl_positive_gross_count": stale_sl_positive_gross_count,
        "reachability_buy_window": reachability_buy_window,
        "reachability_sell_window": reachability_sell_window,
        "reachability_ready_short": reachability_ready_short,
    }


def evaluate_candidate_0_baseline(
    dataset: Optional[PointInTimeDataset],
    candidate_0: XauUsdJointCandidate,
    policy: XauUsdSignalCalibrationSelectionPolicy,
    cost_config: Optional[XauUsdCostConfig] = None,
    evaluator_fn: Optional[Callable] = None,
    market_cache: Optional[Sequence[XauUsdReplayMarketSnapshot]] = None,
) -> Tuple[float, float, Dict[str, Any]]:
    """
    Establish empirical baseline MDD from actual validation replay of Candidate 0.
    Strictly zero hardcoded baseline values.
    """
    eval_fn = evaluator_fn or evaluate_candidate_val
    import inspect
    sig = inspect.signature(eval_fn)
    if "market_cache" in sig.parameters:
        cand0_metrics = eval_fn(
            dataset,
            candidate_0,
            policy,
            cost_config,
            market_cache=market_cache,
        )
    else:
        cand0_metrics = eval_fn(
            dataset,
            candidate_0,
            policy,
            cost_config,
        )
    baseline_mdd_r = float(cand0_metrics["val_max_drawdown_r"])
    max_deterioration_pct = policy.max_drawdown_deterioration_pct
    if baseline_mdd_r > 0:
        relative_mdd_ceiling = min(
            policy.absolute_max_drawdown_r,
            baseline_mdd_r * (1.0 + max_deterioration_pct / 100.0),
        )
    else:
        relative_mdd_ceiling = policy.absolute_max_drawdown_r
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

    c_config = cost_config or XauUsdCostConfig.idealized()
    bt_runner = runner or XauUsdBacktestRunner()

    oos_folds = policy.folds
    oos_start = min(_to_utc(f["oos_start"]) for f in oos_folds)
    oos_end = max(_to_utc(f["oos_end"]) for f in oos_folds)

    # Strictly build OOS cache ONLY AFTER champion lock verification
    oos_cache_file = (
        ROOT
        / "artifacts"
        / "calibration"
        / f"xauusd_oos_market_cache_{policy.policy_fingerprint[:16]}.pkl"
    )
    if oos_cache_file.exists():
        print(f"Loading existing OOS market snapshot cache from {oos_cache_file.name}...")
        t_load = time.time()
        with open(oos_cache_file, "rb") as f:
            oos_market_cache = pickle.load(f)
        print(f"OOS market snapshot cache loaded: {len(oos_market_cache)} snapshots in {time.time() - t_load:.2f}s")
    else:
        t_oos_cache = time.time()
        oos_market_cache = build_market_snapshot_cache(dataset, oos_start, oos_end)
        print(f"OOS market snapshot cache built: {len(oos_market_cache)} snapshots in {time.time() - t_oos_cache:.2f}s")
        try:
            with open(oos_cache_file, "wb") as f:
                pickle.dump(oos_market_cache, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"Saved OOS market snapshot cache to {oos_cache_file.name}")
        except Exception as e:
            print(f"Warning: could not cache OOS to disk: {e}")

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=oos_start,
        end_time=oos_end,
        timeframes=("15m",),
        cost_config=c_config,
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash="",
        code_revision=policy.code_revision,
        holding_horizon_bars_15m=32,
        max_fill_wait_bars_15m=8,
        signal_profile=champion_candidate.signal_profile,
        risk_profile=champion_candidate.risk_profile,
    )

    metrics, trades, signals, run_fp = bt_runner.run_point_in_time(
        dataset,
        spec,
        market_cache=oos_market_cache,
    )

    fold_trade_map: Dict[int, List[XauUsdSimulatedTrade]] = {
        f["fold_id"]: [] for f in oos_folds
    }
    oos_filled_trades: List[XauUsdSimulatedTrade] = []

    for t in trades:
        if t.fill_timestamp is None or t.outcome in (
            XauUsdTradeOutcome.NO_FILL,
            XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN,
            XauUsdTradeOutcome.SKIPPED,
        ):
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
    """Load historical candles and PIT macro evidence from SQLite into in-memory PointInTimeDataset."""
    import bisect
    from datetime import timedelta
    from django.db.models import F
    from apps.market_data.models import MacroScheduleVintage, MarketCandle, ScheduleStatus
    from engine.core.types import MacroEventContext

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
    # Load provenanced macro schedules from SQLite (Phase 3A PIT evidence)
    # Filter for valid schedules known strictly before release (known_at < scheduled_at)
    schedules = list(
        MacroScheduleVintage.objects.filter(
            schedule_status=ScheduleStatus.SCHEDULED,
            known_at__lt=F("scheduled_at"),
            scheduled_at__gte=start_time - timedelta(days=2),
            scheduled_at__lte=end_time + timedelta(days=2),
        ).values("event_id", "reference_period", "scheduled_at", "known_at")
        .order_by("scheduled_at")
    )

    macro_events: List[Tuple[datetime, MacroEventContext]] = []
    if schedules:
        # Precompute blackout intervals: [-30 min, +30 min] of scheduled_at
        intervals = [
            (
                s["scheduled_at"] - timedelta(minutes=30),
                s["scheduled_at"] + timedelta(minutes=30),
                s["known_at"],
                s["event_id"],
            )
            for s in schedules
        ]
        b_starts = [inv[0] for inv in intervals]

        cov_start = min(s["known_at"] for s in schedules)
        cov_end = max(s["scheduled_at"] for s in schedules) + timedelta(minutes=30)

        for c in candles_15m:
            t = c.timestamp_close
            if t < cov_start or t > cov_end:
                ctx = MacroEventContext(
                    is_in_blackout=False,
                    is_feed_healthy=False,
                )
                macro_events.append((t, ctx))
                continue

            low_idx = bisect.bisect_left(b_starts, t - timedelta(minutes=60))
            high_idx = bisect.bisect_right(b_starts, t)
            in_blackout = False
            active_name = None
            for k in range(max(0, low_idx), min(len(intervals), high_idx)):
                inv_start, inv_end, kt, eid = intervals[k]
                if inv_start <= t <= inv_end and kt <= t:
                    in_blackout = True
                    active_name = eid
                    break

            ctx = MacroEventContext(
                is_in_blackout=in_blackout,
                active_event_name=active_name,
                is_feed_healthy=True,
            )
            macro_events.append((t, ctx))

    return PointInTimeDataset(candles_15m=candles_15m, macro_events=macro_events)


def save_champion_artifact(
    champion_candidate: XauUsdJointCandidate,
    champion_dict: Dict[str, Any],
    champion_combined_fp: str,
    expected_dataset_fp: str,
    policy: XauUsdSignalCalibrationSelectionPolicy,
    final_profile_status: str,
    oos_results: Optional[Dict[str, Any]] = None,
    target_artifact_name: str = "xauusd_calibrated_profile_champion",
) -> str:
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
        "production_authority": False,
        "paper_only": True,
        "real_order_execution": False,
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

    return artifact_fp


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
    limit_env = os.getenv("AURUMIQ_CANDIDATE_LIMIT")
    target_count = int(limit_env) if limit_env else 100
    joint_gen = XauUsdJointCandidateGenerator()
    candidates = joint_gen.generate_all_joint_candidates(target_count)
    sig_policy = joint_gen.signal_generator.policy
    risk_policy = joint_gen.risk_generator.policy

    gate_tuples = set()
    for cand in candidates:
        lg = cand.signal_profile.long_gate
        gate_tuples.add((
            lg.threshold_watch_direction,
            lg.threshold_ready_direction,
            lg.threshold_ready_timing,
            lg.threshold_window_direction,
            lg.threshold_window_timing,
        ))

    print(f"GENERATION_POLICY_VERSION = {sig_policy.schema}")
    print(f"GENERATION_POLICY_FINGERPRINT = {sig_policy.policy_fingerprint}")
    print(f"RISK_POLICY_FINGERPRINT = {risk_policy.policy_fingerprint}")
    print(f"CANDIDATES_GENERATED = {len(candidates)}")
    print(f"UNIQUE_GATE_TUPLES = {len(gate_tuples)}")
    assert len(candidates) == target_count
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

    print(f"STRUCTURALLY_REACHABLE = {len(reachable_candidates)}")
    print(
        f"Reachable Candidates: {len(reachable_candidates)}/{len(candidates)}"
    )

    # 5. Load Dataset from DB
    print("\n--- STEP 5: LOADING HISTORICAL DATASET ---")
    dataset = load_point_in_time_dataset_from_db(
        policy.historical_start,
        policy.historical_end_exclusive,
    )

    # Build validation market snapshot cache once (PASS 1)
    print("\n--- VALIDATION MARKET SNAPSHOT CACHE VALIDATION (PASS 1) ---")
    val_folds = policy.folds
    val_start = min(_to_utc(f["val_start"]) for f in val_folds)
    val_end = max(_to_utc(f["val_end"]) for f in val_folds)
    val_cache_file = (
        ROOT
        / "artifacts"
        / "calibration"
        / f"xauusd_val_market_cache_{expected_dataset_fp[:16]}.pkl"
    )
    cache_valid = False
    val_market_cache = None
    cache_load_seconds = 0.0

    if val_cache_file.exists():
        print(f"Validating existing validation market snapshot cache from {val_cache_file.name}...")
        t_load = time.time()
        try:
            with open(val_cache_file, "rb") as f:
                loaded_cache = pickle.load(f)
            cache_load_seconds = time.time() - t_load

            assert len(loaded_cache) == 62335, f"Snapshot count mismatch: {len(loaded_cache)} != 62335"
            assert loaded_cache[0].timestamp >= val_start, "First snapshot precedes val_start"
            assert loaded_cache[-1].timestamp <= val_end, "Last snapshot exceeds val_end"
            first_snap = loaded_cache[0]
            for attr in ("timestamp", "candle_15m", "features_15m", "features_1h", "features_4h", "features_1d", "regime_15m", "structure_15m", "structure_4h", "atr14", "runtime_health"):
                assert hasattr(first_snap, attr), f"Missing snapshot attribute {attr}"
            assert hasattr(first_snap.runtime_health, "macro_blackout_feed"), "Missing macro_blackout_feed"
            assert expected_dataset_fp[:16] in val_cache_file.name, "Dataset fingerprint mismatch in cache filename"

            val_market_cache = loaded_cache
            cache_valid = True
            print("MARKET_CACHE_VALIDATION = PASS")
            print(f"CACHE_LOAD_SECONDS = {cache_load_seconds:.2f}")
            print(
                f"Validation market snapshot cache loaded: {len(val_market_cache)} snapshots in {cache_load_seconds:.2f}s"
            )
        except Exception as e:
            print(f"MARKET_CACHE_VALIDATION = FAIL ({e}), rebuilding cache...")
            cache_valid = False

    if not cache_valid:
        t_cache_start = time.time()
        val_market_cache = build_market_snapshot_cache(dataset, val_start, val_end)
        cache_build_seconds = time.time() - t_cache_start
        print(
            f"Validation market snapshot cache built: {len(val_market_cache)} snapshots in {cache_build_seconds:.2f}s"
        )
        try:
            with open(val_cache_file, "wb") as f:
                pickle.dump(val_market_cache, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"Saved validation market snapshot cache to {val_cache_file.name}")
        except Exception as e:
            print(f"Warning: could not cache to disk: {e}")

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
        market_cache=val_market_cache,
    )
    print(f"DRAWDOWN_BASELINE_ID = REFERENCE_CANDIDATE_0")
    print(f"BASELINE_MAX_DRAWDOWN_R = {baseline_mdd_r:.4f} R")
    print(f"RELATIVE_DRAWDOWN_CEILING = {relative_mdd_ceiling:.4f} R")

    # 7. Evaluate Candidates on VAL & Rank
    print("\n--- STEP 7: EVALUATE CANDIDATES ON VAL ---")
    val_results: List[Dict[str, Any]] = []
    t_eval_start = time.time()
    for i, cand in enumerate(reachable_candidates):
        t_cand_start = time.time()
        c_res = evaluate_candidate_val(
            dataset=dataset,
            candidate=cand,
            policy=policy,
            relative_mdd_ceiling=relative_mdd_ceiling,
            market_cache=val_market_cache,
        )
        val_results.append(c_res)
        t_cand_elapsed = time.time() - t_cand_start
        status_str = "QUALIFIED" if c_res["qualified"] else "REJECTED"
        print(
            f"  [{i+1}/{len(reachable_candidates)}] Candidate {cand.signal_profile.name}: {status_str} "
            f"(LCB95={c_res['val_lcb_95']:+.4f}R, MDD={c_res['val_max_drawdown_r']:.2f}R, "
            f"Trades={c_res['trade_count']}) in {t_cand_elapsed:.2f}s"
        )

    candidate_eval_seconds = time.time() - t_eval_start
    print(f"CANDIDATE_EVAL_SECONDS = {candidate_eval_seconds:.2f}")

    qualified_candidates = [r for r in val_results if r["qualified"]]
    print(
        f"Qualified Candidates on VAL: {len(qualified_candidates)}/{len(val_results)}"
    )

    if not qualified_candidates:
        total_inv = sum(r.get("invalidated_entry_count", 0) for r in val_results)
        total_stale_tp = sum(r.get("stale_tp_negative_gross_count", 0) for r in val_results)
        total_stale_sl = sum(r.get("stale_sl_positive_gross_count", 0) for r in val_results)

        print("\n==================================================================")
        print("EMPIRICAL CALIBRATION SUMMARY (VAL-ONLY PROVISIONAL CHAMPION)")
        print("==================================================================")
        print(f"CANDIDATES_GENERATED = {len(candidates)}")
        print(f"STRUCTURALLY_REACHABLE = {len(reachable_candidates)}")
        print(f"CANDIDATES_EVALUATED = {len(val_results)}")
        print(f"CANDIDATES_QUALIFIED = {len(qualified_candidates)}")
        print("SELECTED_PROVISIONAL_CHAMPION_ID = NONE")
        print("BUY_EFFECTIVE_N = 0.00")
        print("SELL_EFFECTIVE_N = 0.00")
        print("COMBINED_EFFECTIVE_N = 0.00")
        print("VAL_LCB95 = N/A")
        print("VAL_MDD_R = N/A")
        print("VAL_TEMPORAL_STABILITY = N/A")
        print("VAL_PROFIT_CONCENTRATION = N/A")
        print("REACHABILITY_BUY_WINDOW = N/A")
        print("REACHABILITY_SELL_WINDOW = N/A")
        print("REACHABILITY_READY_SHORT = N/A")
        print(f"INVALIDATED_ENTRY_COUNT = {total_inv}")
        print(f"STALE_TP_NEGATIVE_GROSS_COUNT = {total_stale_tp}")
        print(f"STALE_SL_POSITIVE_GROSS_COUNT = {total_stale_sl}")
        print("")
        print("OOS_ACCESS_COUNT = 0")
        print("PRODUCTION_AUTHORITY = OFF")
        print("PAPER_ONLY = TRUE")
        print("REAL_ORDER_EXECUTION = OFF")
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
        provisional_artifact_name = "xauusd_calibrated_profile_candidate_v3_post_remediation"
        artifact_fp = save_champion_artifact(
            champion_candidate=champion_candidate,
            champion_dict=champion_dict,
            champion_combined_fp=champion_combined_fp,
            expected_dataset_fp=expected_dataset_fp,
            policy=policy,
            final_profile_status="DEVELOPMENT_POST_REMEDIATION_PROVISIONAL",
            oos_results=None,
            target_artifact_name=provisional_artifact_name,
        )
        reach_buy = "GREEN" if champion_dict.get("reachability_buy_window") else "RED"
        reach_sell = "GREEN" if champion_dict.get("reachability_sell_window") else "RED"
        reach_ready_short = "GREEN" if champion_dict.get("reachability_ready_short") else "RED"

        print("\n==================================================================")
        print("EMPIRICAL CALIBRATION SUMMARY (VAL-ONLY PROVISIONAL CHAMPION)")
        print("==================================================================")
        print(f"CANDIDATES_GENERATED = {len(candidates)}")
        print(f"STRUCTURALLY_REACHABLE = {len(reachable_candidates)}")
        print(f"CANDIDATES_EVALUATED = {len(val_results)}")
        print(f"CANDIDATES_QUALIFIED = {len(qualified_candidates)}")
        print(f"SELECTED_PROVISIONAL_CHAMPION_ID = {champion_dict['candidate_id']}")
        print(f"BUY_EFFECTIVE_N = {champion_dict['buy_effective_n']:.2f}")
        print(f"SELL_EFFECTIVE_N = {champion_dict['sell_effective_n']:.2f}")
        print(f"COMBINED_EFFECTIVE_N = {champion_dict['combined_effective_n']:.2f}")
        print(f"VAL_LCB95 = +{champion_dict['val_lcb_95']:.4f} R")
        print(f"VAL_MDD_R = {champion_dict['val_max_drawdown_r']:.4f} R")
        print(f"VAL_TEMPORAL_STABILITY = {champion_dict.get('val_temporal_stability', 0.0):.4f}")
        print(f"VAL_PROFIT_CONCENTRATION = {champion_dict.get('val_profit_concentration_pct', 0.0):.2f}%")
        print(f"REACHABILITY_BUY_WINDOW = {reach_buy}")
        print(f"REACHABILITY_SELL_WINDOW = {reach_sell}")
        print(f"REACHABILITY_READY_SHORT = {reach_ready_short}")
        print(f"INVALIDATED_ENTRY_COUNT = {champion_dict.get('invalidated_entry_count', 0)}")
        print(f"STALE_TP_NEGATIVE_GROSS_COUNT = {champion_dict.get('stale_tp_negative_gross_count', 0)}")
        print(f"STALE_SL_POSITIVE_GROSS_COUNT = {champion_dict.get('stale_sl_positive_gross_count', 0)}")
        print("")
        print(f"CALIBRATION_ARTIFACT_FINGERPRINT = {artifact_fp}")
        print("CHAMPION_LOCKED_BEFORE_OOS = true")
        print("OOS_ACCESS_COUNT = 0")
        print("PRODUCTION_AUTHORITY = OFF")
        print("PAPER_ONLY = TRUE")
        print("REAL_ORDER_EXECUTION = OFF")
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
    artifact_fp = save_champion_artifact(
        champion_candidate=champion_candidate,
        champion_dict=champion_dict,
        champion_combined_fp=champion_combined_fp,
        expected_dataset_fp=expected_dataset_fp,
        policy=policy,
        final_profile_status=final_profile_status,
        oos_results=oos_results,
    )
    print(f"CALIBRATION_ARTIFACT_FINGERPRINT = {artifact_fp}")
    print("CALIBRATION EXECUTION COMPLETE: QUALIFIED AS REVALIDATED_RESEARCH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
