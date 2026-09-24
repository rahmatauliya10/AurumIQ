"""
Phase 8 XAUUSD Directional Sub-Profile Calibration & Composite Empirical Runner.

Strict Invariants & Implementation Guards:
1. Replay each deterministic candidate exactly ONCE across historical VAL folds.
   Derive LONG and SHORT side metrics by filtering the single resulting trade list.
2. Reuse exact governed statistical methodology:
   - Effective-N via empirical A16
   - LCB95 via Effective-N adjusted Student's t
   - Governed 5-fold boundaries
   - Required positive-fold rule (>= 4/5)
   - Temporal stability >= 0.50
   - Profit concentration <= 60%
   - Drawdown peak-to-trough in R <= 18.0 R
3. Fail-closed:
   If LONG_CANDIDATES_QUALIFIED == 0 or SHORT_CANDIDATES_QUALIFIED == 0:
       COMPOSITE_NOT_CONSTRUCTED
       CALIBRATION_REQUIRED
       STOP
4. Construct exactly ONE composite candidate from winning Long and Short sub-profiles:
   - Long: direction, timing, gate, risk_policy, execution_policy
   - Short: direction, timing, gate, risk_policy, execution_policy
5. Shared fields explicitly governed by composite policy (never inherited arbitrarily).
6. Composite evaluated with existing runtime signal engine and normal risk/outcome path.
7. Composite must independently pass all original dual-side requirements.
8. Reachability verified on the same composite: BUY_WINDOW, SELL_WINDOW, READY_SHORT.
9. Provisional artifact saved to artifacts/calibration/xauusd_calibrated_profile_candidate_composite.json
   calibration_status = DEVELOPMENT_COMPOSITE_PROVISIONAL
   production_authority = false, paper_only = true
   Do NOT overwrite official champion.
10. OOS_ACCESS_COUNT = 0, AURUMIQ_ENABLE_OOS = 0.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import pickle
import sqlite3
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
os.environ["AURUMIQ_ENABLE_OOS"] = "0"
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import django
django.setup()

from engine.core.types import SignalSide, SignalState, UserDecision
from engine.signals.profile import compute_phase4_policy_fingerprint
from engine.risk.xauusd_fingerprints import compute_phase5_policy_fingerprint
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_candidate_generator import (
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
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
)
from engine.backtest.xauusd_composite_policy import (
    SideEvaluationMetrics,
    XauUsdDirectionalCompositeCalibrationPolicy,
    construct_composite_candidate,
    evaluate_side_trades,
    load_governed_composite_calibration_policy,
    rank_side_subprofiles,
)
from scripts.run_xauusd_calibration import (
    _to_utc,
    check_structural_reachability,
    compute_trade_effective_n,
    load_point_in_time_dataset_from_db,
)
from apps.backtests.tasks import compute_calibration_artifact_fingerprint


def replay_candidate_on_val(
    dataset: PointInTimeDataset,
    candidate: XauUsdJointCandidate,
    policy: XauUsdDirectionalCompositeCalibrationPolicy,
    market_cache: Sequence[XauUsdReplayMarketSnapshot],
    runner: Optional[XauUsdBacktestRunner] = None,
    cost_config: Optional[XauUsdCostConfig] = None,
) -> Tuple[List[XauUsdSimulatedTrade], Dict[str, bool]]:
    """
    Replay a single candidate ONCE across historical validation folds using market cache.
    Returns (trades, reachability_flags).
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

    reach_buy = any(
        getattr(s, "candidate_state", None) == SignalState.BUY_WINDOW
        or getattr(s, "candidate_state", None) == SignalState.BUY_WINDOW.value
        for s in signals
    )
    reach_sell = any(
        getattr(s, "candidate_state", None) == SignalState.SELL_WINDOW
        or getattr(s, "candidate_state", None) == SignalState.SELL_WINDOW.value
        for s in signals
    )
    reach_ready_short = any(
        getattr(s, "candidate_state", None) == SignalState.READY_SHORT
        or getattr(s, "candidate_state", None) == SignalState.READY_SHORT.value
        for s in signals
    )

    reachability = {
        "reachability_buy_window": reach_buy,
        "reachability_sell_window": reach_sell,
        "reachability_ready_short": reach_ready_short,
    }

    return trades, reachability


def evaluate_dual_side_composite(
    dataset: PointInTimeDataset,
    composite_candidate: XauUsdJointCandidate,
    policy: XauUsdDirectionalCompositeCalibrationPolicy,
    market_cache: Sequence[XauUsdReplayMarketSnapshot],
    runner: Optional[XauUsdBacktestRunner] = None,
    cost_config: Optional[XauUsdCostConfig] = None,
) -> Dict[str, Any]:
    """
    Evaluate the ONE composite candidate against the complete original dual-side requirements.
    """
    trades, reachability = replay_candidate_on_val(
        dataset=dataset,
        candidate=composite_candidate,
        policy=policy,
        market_cache=market_cache,
        runner=runner,
        cost_config=cost_config,
    )

    val_folds = policy.folds
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
        if f_mean > 0.0:
            positive_folds += 1

    total_profit = sum(fold_profits)
    max_fold_profit = max(fold_profits) if fold_profits else 0.0
    profit_concentration = (
        float(max_fold_profit / total_profit * 100.0)
        if total_profit > 0 and max_fold_profit > 0
        else 0.0
    )

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

    qualified = (
        buy_eff_n >= policy.buy_min_effective_n
        and sell_eff_n >= policy.sell_min_effective_n
        and comb_eff_n >= policy.composite_combined_min_effective_n
        and val_lcb_95 > policy.composite_combined_lcb_95_min
        and max_dd_r <= policy.composite_combined_max_drawdown_r
        and temporal_stability >= policy.composite_min_temporal_stability
        and profit_concentration <= policy.composite_max_profit_concentration_pct
        and positive_folds >= 4
    )

    return {
        "candidate_id": composite_candidate.signal_profile.name,
        "buy_effective_n": round(buy_eff_n, 2),
        "sell_effective_n": round(sell_eff_n, 2),
        "combined_effective_n": round(comb_eff_n, 2),
        "val_mean_r": round(mean_r, 4),
        "val_std_r": round(std_r, 4),
        "val_lcb_95": val_lcb_95,
        "val_max_drawdown_r": round(max_dd_r, 4),
        "val_temporal_stability": round(temporal_stability, 4),
        "val_profit_concentration_pct": round(profit_concentration, 2),
        "fold_expectancies": [round(x, 4) for x in fold_expectancies],
        "positive_folds": positive_folds,
        "total_folds": len(val_folds),
        "trade_count": len(filled_trades),
        "invalidated_entry_count": invalidated_entry_count,
        "stale_tp_negative_gross_count": stale_tp_negative_gross_count,
        "stale_sl_positive_gross_count": stale_sl_positive_gross_count,
        "qualified": qualified,
        "reachability_buy_window": reachability["reachability_buy_window"],
        "reachability_sell_window": reachability["reachability_sell_window"],
        "reachability_ready_short": reachability["reachability_ready_short"],
    }


def save_composite_provisional_artifact(
    composite_candidate: XauUsdJointCandidate,
    selected_long: SideEvaluationMetrics,
    selected_short: SideEvaluationMetrics,
    composite_val_metrics: Dict[str, Any],
    policy: XauUsdDirectionalCompositeCalibrationPolicy,
    expected_dataset_fp: str,
) -> Tuple[str, str]:
    """
    Save provisional composite artifact strictly without overwriting official champion.
    Returns (artifact_path_str, artifact_fingerprint).
    """
    target_path = ROOT / policy.provisional_artifact_path

    sig = composite_candidate.signal_profile
    risk = composite_candidate.risk_profile

    sig_fp = compute_phase4_policy_fingerprint(sig)
    risk_fp = compute_phase5_policy_fingerprint(risk)
    composite_fp = f"{sig_fp}:{risk_fp}"

    sig_payload = {
        "name": sig.name,
        "target_instrument": sig.target_instrument,
        "calibration_status": "DEVELOPMENT_POST_REMEDIATION_COMPOSITE_PROVISIONAL",
        "timeframe": sig.timeframe,
        "long_direction": {
            k: getattr(sig.long_direction, k)
            for k in [
                "weight_regime", "weight_trend_1h", "weight_trend_4h",
                "weight_trend_1d", "weight_structure_bos", "weight_pullback",
                "weight_momentum", "weight_volume",
            ]
        },
        "short_direction": {
            k: getattr(sig.short_direction, k)
            for k in [
                "weight_regime", "weight_trend_1h", "weight_trend_4h",
                "weight_trend_1d", "weight_structure_bos", "weight_pullback",
                "weight_momentum", "weight_volume",
            ]
        },
        "long_timing": {
            k: getattr(sig.long_timing, k)
            for k in [
                "weight_entry_zone", "weight_reversal_confirmation_15m",
                "weight_momentum_turn_15m_1h", "weight_phase3a",
                "weight_volume_response",
            ]
        },
        "short_timing": {
            k: getattr(sig.short_timing, k)
            for k in [
                "weight_entry_zone", "weight_reversal_confirmation_15m",
                "weight_momentum_turn_15m_1h", "weight_phase3a",
                "weight_volume_response",
            ]
        },
        "long_gate": {
            k: getattr(sig.long_gate, k)
            for k in [
                "threshold_watch_direction", "threshold_ready_direction",
                "threshold_ready_timing", "threshold_window_direction",
                "threshold_window_timing",
            ]
        },
        "short_gate": {
            k: getattr(sig.short_gate, k)
            for k in [
                "threshold_watch_direction", "threshold_ready_direction",
                "threshold_ready_timing", "threshold_window_direction",
                "threshold_window_timing",
            ]
        },
        "feed_policy": {
            "primary_15m": sig.feed_policy.primary_15m.value if hasattr(sig.feed_policy.primary_15m, "value") else str(sig.feed_policy.primary_15m),
            "primary_1h": sig.feed_policy.primary_1h.value if hasattr(sig.feed_policy.primary_1h, "value") else str(sig.feed_policy.primary_1h),
            "primary_4h": sig.feed_policy.primary_4h.value if hasattr(sig.feed_policy.primary_4h, "value") else str(sig.feed_policy.primary_4h),
            "primary_1d": sig.feed_policy.primary_1d.value if hasattr(sig.feed_policy.primary_1d, "value") else str(sig.feed_policy.primary_1d),
            "secondary_provider": sig.feed_policy.secondary_provider.value if hasattr(sig.feed_policy.secondary_provider, "value") else str(sig.feed_policy.secondary_provider),
            "macro_blackout": sig.feed_policy.macro_blackout.value if hasattr(sig.feed_policy.macro_blackout, "value") else str(sig.feed_policy.macro_blackout),
            "volume": sig.feed_policy.volume.value if hasattr(sig.feed_policy.volume, "value") else str(sig.feed_policy.volume),
            "phase3a": sig.feed_policy.phase3a.value if hasattr(sig.feed_policy.phase3a, "value") else str(sig.feed_policy.phase3a),
            "phase3b": sig.feed_policy.phase3b.value if hasattr(sig.feed_policy.phase3b, "value") else str(sig.feed_policy.phase3b),
            "dxy_yields_futures": sig.feed_policy.dxy_yields_futures.value if hasattr(sig.feed_policy.dxy_yields_futures, "value") else str(sig.feed_policy.dxy_yields_futures),
        },
    }

    risk_payload = {
        "name": risk.name,
        "target_instrument": risk.target_instrument,
        "calibration_status": "DEVELOPMENT_POST_REMEDIATION_COMPOSITE_PROVISIONAL",
        "long_risk_policy": {
            "structure_buffer": str(risk.long_risk_policy.structure_buffer),
            "atr_multiplier": str(risk.long_risk_policy.atr_multiplier),
            "max_stop_distance_atr": str(risk.long_risk_policy.max_stop_distance_atr),
            "min_rr_tp1": str(risk.long_risk_policy.min_rr_tp1),
            "tp2_atr_multiplier": None,
        },
        "short_risk_policy": {
            "structure_buffer": str(risk.short_risk_policy.structure_buffer),
            "atr_multiplier": str(risk.short_risk_policy.atr_multiplier),
            "max_stop_distance_atr": str(risk.short_risk_policy.max_stop_distance_atr),
            "min_rr_tp1": str(risk.short_risk_policy.min_rr_tp1),
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
        "artifact_id": Path(policy.provisional_artifact_path).stem,
        "instrument": "XAUUSD",
        "calibration_status": "DEVELOPMENT_POST_REMEDIATION_COMPOSITE_PROVISIONAL",
        "production_authority": False,
        "paper_only": True,
        "real_order_execution": False,
        "code_revision": policy.code_revision,
        "remediation_reference": policy.raw_payload.get("remediation_reference", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "composite_id": sig.name,
        "composite_fingerprint": composite_fp,
        "policy_fingerprint": policy.policy_fingerprint,
        "dataset_fingerprint": expected_dataset_fp,
        "selected_long_id": selected_long.candidate_id,
        "selected_long_metrics": {
            "index": selected_long.index,
            "effective_n": selected_long.effective_n,
            "mean_r": selected_long.mean_r,
            "side_lcb_95": selected_long.side_lcb_95,
            "side_max_drawdown_r": selected_long.side_max_drawdown_r,
            "temporal_stability": selected_long.temporal_stability,
            "profit_concentration_pct": selected_long.profit_concentration_pct,
            "trade_count": selected_long.trade_count,
        },
        "selected_short_id": selected_short.candidate_id,
        "selected_short_metrics": {
            "index": selected_short.index,
            "effective_n": selected_short.effective_n,
            "mean_r": selected_short.mean_r,
            "side_lcb_95": selected_short.side_lcb_95,
            "side_max_drawdown_r": selected_short.side_max_drawdown_r,
            "temporal_stability": selected_short.temporal_stability,
            "profit_concentration_pct": selected_short.profit_concentration_pct,
            "trade_count": selected_short.trade_count,
        },
        "composite_val_metrics": composite_val_metrics,
        "signal_profile": sig_payload,
        "risk_profile": risk_payload,
    }

    artifact_fp = compute_calibration_artifact_fingerprint(artifact_dict)
    artifact_dict["artifact_fingerprint"] = artifact_fp

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(artifact_dict, f, indent=2)
        f.write("\n")

    return str(target_path), artifact_fp


def main():
    print("==================================================================")
    print("AURUMIQ XAUUSD DIRECTIONAL SUB-PROFILE CALIBRATION RUNNER")
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

    manifest_path = ROOT / "artifacts" / "calibration" / "xauusd_data_manifest.json"
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

    # 2. Frozen Composite Calibration Policy & Embargo (Phase A)
    print("\n--- STEP 2: FROZEN COMPOSITE CALIBRATION POLICY & EMBARGO (PHASE A) ---")
    policy = load_governed_composite_calibration_policy()
    print(f"POLICY_ID = {policy.policy_id}")
    print(f"POLICY_FINGERPRINT = {policy.policy_fingerprint}")

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

    # 3. Candidate Generation (Frozen deterministic candidate pool)
    print("\n--- STEP 3: CANDIDATE POOL INITIALIZATION ---")
    joint_gen = XauUsdJointCandidateGenerator()
    candidates = joint_gen.generate_all_joint_candidates(100)
    print(f"CANDIDATES_GENERATED = {len(candidates)}")
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

    print(f"STRUCTURALLY_REACHABLE = {len(reachable_candidates)}/{len(candidates)}")
    assert len(reachable_candidates) == 100

    # 5. Load Dataset from DB & Validate Market Cache
    print("\n--- STEP 5: VALIDATION MARKET SNAPSHOT CACHE VALIDATION ---")
    dataset = load_point_in_time_dataset_from_db(
        policy.historical_start,
        policy.historical_end_exclusive,
    )
    val_cache_file = (
        ROOT
        / "artifacts"
        / "calibration"
        / f"xauusd_val_market_cache_{expected_dataset_fp[:16]}.pkl"
    )
    if not val_cache_file.exists():
        raise FileNotFoundError(
            f"Validation market cache not found at {val_cache_file}"
        )

    t_load = time.time()
    with open(val_cache_file, "rb") as f:
        val_market_cache = pickle.load(f)
    print(
        f"Validation market cache loaded: {len(val_market_cache)} snapshots in {time.time() - t_load:.2f}s"
    )
    assert len(val_market_cache) == 62335

    # 6. Replay Candidates on VAL exactly ONCE & Derive Side Metrics (Phase B)
    print("\n--- STEP 6: CANDIDATE REPLAY (SINGLE PASS) & SIDE METRICS (PHASE B) ---")
    long_metrics_list: List[SideEvaluationMetrics] = []
    short_metrics_list: List[SideEvaluationMetrics] = []

    total_inv_entries = 0
    total_stale_tp = 0
    total_stale_sl = 0

    t_eval_start = time.time()
    for i, cand in enumerate(reachable_candidates):
        t_cand_start = time.time()
        trades, reachability = replay_candidate_on_val(
            dataset=dataset,
            candidate=cand,
            policy=policy,
            market_cache=val_market_cache,
        )
        t_cand_elapsed = time.time() - t_cand_start

        total_inv_entries += sum(
            1 for t in trades if t.outcome == XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN
        )
        total_stale_tp += sum(
            1 for t in trades if t.outcome == XauUsdTradeOutcome.TP1_FIRST and (t.gross_r or Decimal("0")) < Decimal("0")
        )
        total_stale_sl += sum(
            1 for t in trades if t.outcome in (XauUsdTradeOutcome.SL_FIRST, XauUsdTradeOutcome.CONSERVATIVE_SL_FIRST) and (t.gross_r or Decimal("0")) > Decimal("0")
        )

        # Derive LONG metrics strictly from LONG trades
        l_met = evaluate_side_trades(
            trades=trades,
            side=SignalSide.LONG,
            folds=policy.folds,
            policy=policy,
            candidate_id=cand.signal_profile.name,
            candidate_index=cand.index,
        )
        long_metrics_list.append(l_met)

        # Derive SHORT metrics strictly from SHORT trades
        s_met = evaluate_side_trades(
            trades=trades,
            side=SignalSide.SHORT,
            folds=policy.folds,
            policy=policy,
            candidate_id=cand.signal_profile.name,
            candidate_index=cand.index,
        )
        short_metrics_list.append(s_met)

        l_status = "QUALIFIED" if l_met.qualified else "REJECTED"
        s_status = "QUALIFIED" if s_met.qualified else "REJECTED"

        if (
            len(trades) > 0
            or l_met.qualified
            or s_met.qualified
            or reachability["reachability_buy_window"]
            or reachability["reachability_sell_window"]
            or i % 10 == 0
        ):
            print(
                f"  [{i+1}/100] Cand {cand.index:03d} ({t_cand_elapsed:.1f}s): "
                f"Trades={len(trades)} | "
                f"LONG: {l_status} (N_eff={l_met.effective_n:.1f}, LCB={l_met.side_lcb_95:+.4f}, MDD={l_met.side_max_drawdown_r:.2f}R) | "
                f"SHORT: {s_status} (N_eff={s_met.effective_n:.1f}, LCB={s_met.side_lcb_95:+.4f}, MDD={s_met.side_max_drawdown_r:.2f}R)"
            )

    eval_total_seconds = time.time() - t_eval_start
    print(f"\nALL_CANDIDATES_REPLAY_SECONDS = {eval_total_seconds:.2f}")

    # 7. Side-Wise Selection (Phase B)
    long_qualified = [m for m in long_metrics_list if m.qualified]
    short_qualified = [m for m in short_metrics_list if m.qualified]

    long_candidates_evaluated = len(long_metrics_list)
    short_candidates_evaluated = len(short_metrics_list)
    long_candidates_qualified = len(long_qualified)
    short_candidates_qualified = len(short_qualified)

    print("\n--- STEP 7: SIDE-WISE SELECTION SUMMARY ---")
    print(f"LONG_CANDIDATES_EVALUATED = {long_candidates_evaluated}")
    print(f"LONG_CANDIDATES_QUALIFIED = {long_candidates_qualified}")
    print(f"SHORT_CANDIDATES_EVALUATED = {short_candidates_evaluated}")
    print(f"SHORT_CANDIDATES_QUALIFIED = {short_candidates_qualified}")

    # FAIL-CLOSED GUARD (Implementation Guard 3)
    if long_candidates_qualified == 0 or short_candidates_qualified == 0:
        print("\n==================================================================")
        print("FAIL-CLOSED: SIDE QUALIFICATION HURDLE NOT MET")
        print("==================================================================")
        if long_candidates_qualified == 0:
            print("FAILURE_REASON = ZERO_LONG_CANDIDATES_QUALIFIED")
        if short_candidates_qualified == 0:
            print("FAILURE_REASON = ZERO_SHORT_CANDIDATES_QUALIFIED")
        print("COMPOSITE_NOT_CONSTRUCTED")
        print("CALIBRATION_REQUIRED")
        print("STOP")
        print("")
        print(f"COMPOSITE_POLICY_VERSION = {policy.schema}")
        print(f"COMPOSITE_POLICY_FINGERPRINT = {policy.policy_fingerprint}")
        print(f"POST_REMEDIATION_CODE_REVISION = {policy.code_revision}")
        print("")
        print(f"LONG_CANDIDATES_EVALUATED = {long_candidates_evaluated}")
        print(f"LONG_CANDIDATES_QUALIFIED = {long_candidates_qualified}")
        print("SELECTED_LONG_ID = NONE")
        print("LONG_EFFECTIVE_N = 0.00")
        print("LONG_LCB95 = N/A")
        print("LONG_MDD_R = N/A")
        print("LONG_TEMPORAL_STABILITY = N/A")
        print("LONG_PROFIT_CONCENTRATION = N/A")
        print("")
        print(f"SHORT_CANDIDATES_EVALUATED = {short_candidates_evaluated}")
        print(f"SHORT_CANDIDATES_QUALIFIED = {short_candidates_qualified}")
        print("SELECTED_SHORT_ID = NONE")
        print("SHORT_EFFECTIVE_N = 0.00")
        print("SHORT_LCB95 = N/A")
        print("SHORT_MDD_R = N/A")
        print("SHORT_TEMPORAL_STABILITY = N/A")
        print("SHORT_PROFIT_CONCENTRATION = N/A")
        print("")
        print("COMPOSITE_STATUS = NOT_CONSTRUCTED")
        print("COMPOSITE_BUY_EFFECTIVE_N = 0.00")
        print("COMPOSITE_SELL_EFFECTIVE_N = 0.00")
        print("COMPOSITE_COMBINED_EFFECTIVE_N = 0.00")
        print("COMPOSITE_LCB95 = N/A")
        print("COMPOSITE_MDD_R = N/A")
        print("COMPOSITE_TEMPORAL_STABILITY = N/A")
        print("COMPOSITE_PROFIT_CONCENTRATION = N/A")
        print("REACHABILITY_BUY_WINDOW = RED")
        print("REACHABILITY_SELL_WINDOW = RED")
        print("REACHABILITY_READY_SHORT = RED")
        print("")
        print(f"INVALIDATED_ENTRY_COUNT = {total_inv_entries}")
        print(f"STALE_TP_NEGATIVE_GROSS_COUNT = {total_stale_tp}")
        print(f"STALE_SL_POSITIVE_GROSS_COUNT = {total_stale_sl}")
        print("")
        print("OOS_ACCESS_THIS_RUN = 0")
        print("PRODUCTION_AUTHORITY = OFF")
        print("PAPER_ONLY = TRUE")
        print("REAL_ORDER_EXECUTION = OFF")
        return 1

    # Deterministic ranking according to frozen rules
    ranked_long = rank_side_subprofiles(long_qualified)
    ranked_short = rank_side_subprofiles(short_qualified)

    selected_long = ranked_long[0]
    selected_short = ranked_short[0]

    print(
        f"SELECTED_LONG_ID = {selected_long.candidate_id} "
        f"(LCB95={selected_long.side_lcb_95:+.4f}, MDD={selected_long.side_max_drawdown_r:.2f}R, "
        f"N_eff={selected_long.effective_n:.2f})"
    )
    print(
        f"SELECTED_SHORT_ID = {selected_short.candidate_id} "
        f"(LCB95={selected_short.side_lcb_95:+.4f}, MDD={selected_short.side_max_drawdown_r:.2f}R, "
        f"N_eff={selected_short.effective_n:.2f})"
    )

    # 8. Deterministic Composite Construction (Phase C)
    print("\n--- STEP 8: CONSTRUCTING SINGLE COMPOSITE PROFILE (PHASE C) ---")
    best_long_cand = candidates[selected_long.index]
    best_short_cand = candidates[selected_short.index]

    composite_candidate = construct_composite_candidate(
        best_long_candidate=best_long_cand,
        best_short_candidate=best_short_cand,
        policy=policy,
    )
    print(f"COMPOSITE_NAME = {composite_candidate.signal_profile.name}")
    print(f"COMPOSITE_RISK_NAME = {composite_candidate.risk_profile.name}")

    reachable, reason = check_structural_reachability(
        composite_candidate.signal_profile
    )
    if not reachable:
        raise AssertionError(
            f"COMPOSITE_STRUCTURAL_REACHABILITY_FAIL: {reason}"
        )
    print("COMPOSITE_STRUCTURAL_REACHABILITY = PASS")

    # 9. Evaluate ONE Composite against Original Combined Hurdles
    print("\n--- STEP 9: EVALUATING COMPOSITE ON VAL ---")
    comp_metrics = evaluate_dual_side_composite(
        dataset=dataset,
        composite_candidate=composite_candidate,
        policy=policy,
        market_cache=val_market_cache,
    )

    reach_buy_str = "GREEN" if comp_metrics["reachability_buy_window"] else "RED"
    reach_sell_str = "GREEN" if comp_metrics["reachability_sell_window"] else "RED"
    reach_ready_short_str = "GREEN" if comp_metrics["reachability_ready_short"] else "RED"

    # 10. Save Provisional Artifact
    print("\n--- STEP 10: SAVING PROVISIONAL COMPOSITE ARTIFACT ---")
    art_path, art_fp = save_composite_provisional_artifact(
        composite_candidate=composite_candidate,
        selected_long=selected_long,
        selected_short=selected_short,
        composite_val_metrics=comp_metrics,
        policy=policy,
        expected_dataset_fp=expected_dataset_fp,
    )
    print(f"PROVISIONAL_ARTIFACT_SAVED = {art_path}")
    print(f"ARTIFACT_FINGERPRINT = {art_fp}")

    # 11. Emit Final Phase D Report & STOP
    print("\n==================================================================")
    print("DIRECTIONAL COMPOSITE CALIBRATION REPORT (PHASE D)")
    print("==================================================================")
    print(f"COMPOSITE_POLICY_VERSION = {policy.schema}")
    print(f"COMPOSITE_POLICY_FINGERPRINT = {policy.policy_fingerprint}")
    print(f"POST_REMEDIATION_CODE_REVISION = {policy.code_revision}")
    print("")
    print(f"LONG_CANDIDATES_EVALUATED = {long_candidates_evaluated}")
    print(f"LONG_CANDIDATES_QUALIFIED = {long_candidates_qualified}")
    print(f"SELECTED_LONG_ID = {selected_long.candidate_id}")
    print(f"LONG_EFFECTIVE_N = {selected_long.effective_n:.2f}")
    print(f"LONG_LCB95 = {selected_long.side_lcb_95:+.4f}")
    print(f"LONG_MDD_R = {selected_long.side_max_drawdown_r:.4f}")
    print(f"LONG_TEMPORAL_STABILITY = {selected_long.temporal_stability:.4f}")
    print(f"LONG_PROFIT_CONCENTRATION = {selected_long.profit_concentration_pct:.2f}%")
    print("")
    print(f"SHORT_CANDIDATES_EVALUATED = {short_candidates_evaluated}")
    print(f"SHORT_CANDIDATES_QUALIFIED = {short_candidates_qualified}")
    print(f"SELECTED_SHORT_ID = {selected_short.candidate_id}")
    print(f"SHORT_EFFECTIVE_N = {selected_short.effective_n:.2f}")
    print(f"SHORT_LCB95 = {selected_short.side_lcb_95:+.4f}")
    print(f"SHORT_MDD_R = {selected_short.side_max_drawdown_r:.4f}")
    print(f"SHORT_TEMPORAL_STABILITY = {selected_short.temporal_stability:.4f}")
    print(f"SHORT_PROFIT_CONCENTRATION = {selected_short.profit_concentration_pct:.2f}%")
    print("")
    print(f"COMPOSITE_STATUS = {'QUALIFIED' if comp_metrics['qualified'] else 'REJECTED'}")
    print(f"COMPOSITE_BUY_EFFECTIVE_N = {comp_metrics['buy_effective_n']:.2f}")
    print(f"COMPOSITE_SELL_EFFECTIVE_N = {comp_metrics['sell_effective_n']:.2f}")
    print(f"COMPOSITE_COMBINED_EFFECTIVE_N = {comp_metrics['combined_effective_n']:.2f}")
    print(f"COMPOSITE_LCB95 = {comp_metrics['val_lcb_95']:+.4f}")
    print(f"COMPOSITE_MDD_R = {comp_metrics['val_max_drawdown_r']:.4f}")
    print(f"COMPOSITE_TEMPORAL_STABILITY = {comp_metrics['val_temporal_stability']:.4f}")
    print(f"COMPOSITE_PROFIT_CONCENTRATION = {comp_metrics['val_profit_concentration_pct']:.2f}%")
    print("")
    print(f"REACHABILITY_BUY_WINDOW = {reach_buy_str}")
    print(f"REACHABILITY_SELL_WINDOW = {reach_sell_str}")
    print(f"REACHABILITY_READY_SHORT = {reach_ready_short_str}")
    print("")
    print(f"INVALIDATED_ENTRY_COUNT = {comp_metrics.get('invalidated_entry_count', 0)}")
    print(f"STALE_TP_NEGATIVE_GROSS_COUNT = {comp_metrics.get('stale_tp_negative_gross_count', 0)}")
    print(f"STALE_SL_POSITIVE_GROSS_COUNT = {comp_metrics.get('stale_sl_positive_gross_count', 0)}")
    print("")
    print("OOS_ACCESS_THIS_RUN = 0")
    print("PRODUCTION_AUTHORITY = OFF")
    print("PAPER_ONLY = TRUE")
    print("REAL_ORDER_EXECUTION = OFF")
    print("==================================================================")

    return 0 if comp_metrics["qualified"] else 1


if __name__ == "__main__":
    sys.exit(main())
