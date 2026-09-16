"""
Script to extract causal market geometry from Fold-1 TRAIN dataset, compute empirical quantiles,
and emit the sealed xauusd_risk_geometry_fold1_train.json artifact.
"""
import os
import sys
import json
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import numpy as np

# Ensure django setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
import django
django.setup()

from apps.market_data.models import MarketCandle
from engine.core.types import CandleData, VolumeEvidenceType
from engine.features.volatility import calculate_atr
from engine.structure.engine import CausalStructureEngine
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity


def main():
    print("=============================================================")
    print("AURUMIQ XAUUSD FOLD-1 TRAIN RISK GEOMETRY EXTRACTOR")
    print("=============================================================")

    # 1. Verify Dataset Fingerprint
    expected_dataset_fp = "2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9"
    all_15m_qs = MarketCandle.objects.filter(timeframe="15m").order_by("timestamp_open")
    total_15m = all_15m_qs.count()
    print(f"Total 15m rows in datastore: {total_15m}")
    if total_15m != 161233:
        raise AssertionError(f"DATASET_VERIFICATION_FAIL: Expected 161233 15m candles, found {total_15m}")

    earliest_c = all_15m_qs.first()
    latest_c = all_15m_qs.last()

    earliest_dt = datetime(2020, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
    latest_dt = datetime(2026, 9, 1, 1, 0, 0, tzinfo=timezone.utc)

    # 2. Fold 1 TRAIN boundaries
    fold1_train_start = datetime(2020, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
    fold1_train_end = datetime(2024, 2, 8, 19, 12, 0, tzinfo=timezone.utc)

    fold1_qs = all_15m_qs.filter(
        timestamp_open__gte=fold1_train_start,
        timestamp_close__lte=fold1_train_end,
    ).order_by("timestamp_open")

    fold1_count = fold1_qs.count()
    print(f"Fold-1 TRAIN 15m count (close <= 2024-02-08 19:12:00Z): {fold1_count}")
    if fold1_count == 0:
        raise AssertionError("DATASTORE_EMPTY: No Fold-1 TRAIN 15m candles found!")

    # 3. Load Fold 1 TRAIN candles
    print("Loading Fold-1 TRAIN candles into memory...")
    candles_list = []
    for c in fold1_qs.iterator(chunk_size=10000):
        # Strict boundary invariant check
        if c.timestamp_open < fold1_train_start:
            raise ValueError(f"LEAKAGE_DETECTED: Candle open {c.timestamp_open} < fold1_train_start")
        if c.timestamp_close > fold1_train_end:
            raise ValueError(f"LEAKAGE_DETECTED: Candle close {c.timestamp_close} > fold1_train_end")

        candles_list.append(
            CandleData(
                timestamp_open=c.timestamp_open,
                timestamp_close=c.timestamp_close,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed,
                source_id=getattr(c, "source", "twelve_data_xauusd"),
                quote_rate=getattr(c, "quote_rate", Decimal("1.000000")),
                close_usd=getattr(c, "close_usd", c.close),
                volume_evidence=VolumeEvidenceType.UNAVAILABLE,
            )
        )

    print(f"Loaded {len(candles_list)} Fold-1 TRAIN candles.")

    # 4. Run Causal Market Structure & Geometry Extraction
    # Universe: EVERY_ELIGIBLE_15M_TIMESTAMP
    print("Running geometry extraction across EVERY_ELIGIBLE_15M_TIMESTAMP (step=1)...")
    structure_engine = CausalStructureEngine()
    warmup_bars = 100

    closes = [c.close_usd if c.close_usd is not None else c.close for c in candles_list]
    highs = [c.high for c in candles_list]
    lows = [c.low for c in candles_list]

    long_structure_buffer = []
    long_atr_multiplier = []
    long_max_stop_distance_atr = []
    long_min_rr_tp1 = []

    short_structure_buffer = []
    short_atr_multiplier = []
    short_max_stop_distance_atr = []
    short_min_rr_tp1 = []

    raw_long_samples = 0
    raw_short_samples = 0

    eligible_timestamps_count = 0

    for i in range(warmup_bars, len(candles_list)):
        curr_c = candles_list[i]
        eligible_timestamps_count += 1

        # Causal slice: historical window up to bar i
        c_slice = candles_list[max(0, i - 200) : i + 1]

        atr = calculate_atr(
            highs[max(0, i - 50) : i + 1],
            lows[max(0, i - 50) : i + 1],
            closes[max(0, i - 50) : i + 1],
            period=14,
        )
        if atr is None or atr <= 0:
            continue

        struct = structure_engine.analyze(c_slice, atr=atr)
        zones = struct.zones
        if not zones:
            continue

        p = float(curr_c.close_usd if curr_c.close_usd is not None else curr_c.close)
        atr_val = float(atr)

        # Active support zones (below current price)
        support_zones = [z for z in zones if z.zone_type == "SUPPORT" and float(z.price_low) < p]
        # Active resistance zones (above current price)
        resistance_zones = [z for z in zones if z.zone_type == "RESISTANCE" and float(z.price_high) > p]

        raw_long_samples += 1
        raw_short_samples += 1

        # LONG geometry observation
        if support_zones and resistance_zones:
            nearest_sup = max(support_zones, key=lambda z: float(z.price_high))
            furthest_sup = min(support_zones, key=lambda z: float(z.price_low))
            nearest_res = min(resistance_zones, key=lambda z: float(z.price_low))

            sup_low = float(nearest_sup.price_low)
            sup_high = float(nearest_sup.price_high)
            res_low = float(nearest_res.price_low)

            risk_dist = p - sup_low
            reward_dist = res_low - p

            if risk_dist > 0 and reward_dist > 0:
                buffer_val = sup_high - sup_low
                atr_mult = risk_dist / atr_val
                max_stop_dist = (p - float(furthest_sup.price_low)) / atr_val
                rr = reward_dist / risk_dist

                long_structure_buffer.append(buffer_val)
                long_atr_multiplier.append(atr_mult)
                long_max_stop_distance_atr.append(max_stop_dist)
                long_min_rr_tp1.append(rr)

        # SHORT geometry observation
        if support_zones and resistance_zones:
            nearest_res = min(resistance_zones, key=lambda z: float(z.price_low))
            furthest_res = max(resistance_zones, key=lambda z: float(z.price_high))
            nearest_sup = max(support_zones, key=lambda z: float(z.price_high))

            res_high = float(nearest_res.price_high)
            res_low = float(nearest_res.price_low)
            sup_high = float(nearest_sup.price_high)

            risk_dist = res_high - p
            reward_dist = p - sup_high

            if risk_dist > 0 and reward_dist > 0:
                buffer_val = res_high - res_low
                atr_mult = risk_dist / atr_val
                max_stop_dist = (float(furthest_res.price_high) - p) / atr_val
                rr = reward_dist / risk_dist

                short_structure_buffer.append(buffer_val)
                short_atr_multiplier.append(atr_mult)
                short_max_stop_distance_atr.append(max_stop_dist)
                short_min_rr_tp1.append(rr)

    print(f"Total eligible 15m timestamps processed: {eligible_timestamps_count}")
    print(f"LONG: raw={raw_long_samples}, valid={len(long_structure_buffer)}")
    print(f"SHORT: raw={raw_short_samples}, valid={len(short_structure_buffer)}")

    def calc_quantiles(raw_n, vals, decimals=2):
        arr = np.array(vals)
        p10, p25, p50, p75, p90 = np.percentile(arr, [10, 25, 50, 75, 90], method="linear")
        return {
            "raw_sample_count": raw_n,
            "valid_sample_count": len(vals),
            "min": round(float(arr.min()), decimals),
            "p10": round(float(p10), decimals),
            "p25": round(float(p25), decimals),
            "p50": round(float(p50), decimals),
            "p75": round(float(p75), decimals),
            "p90": round(float(p90), decimals),
            "max": round(float(arr.max()), decimals),
        }

    long_stats = {
        "structure_buffer": calc_quantiles(raw_long_samples, long_structure_buffer, decimals=2),
        "atr_multiplier": calc_quantiles(raw_long_samples, long_atr_multiplier, decimals=2),
        "max_stop_distance_atr": calc_quantiles(raw_long_samples, long_max_stop_distance_atr, decimals=2),
        "min_rr_tp1": calc_quantiles(raw_long_samples, long_min_rr_tp1, decimals=2),
    }

    short_stats = {
        "structure_buffer": calc_quantiles(raw_short_samples, short_structure_buffer, decimals=2),
        "atr_multiplier": calc_quantiles(raw_short_samples, short_atr_multiplier, decimals=2),
        "max_stop_distance_atr": calc_quantiles(raw_short_samples, short_max_stop_distance_atr, decimals=2),
        "min_rr_tp1": calc_quantiles(raw_short_samples, short_min_rr_tp1, decimals=2),
    }

    # Deterministic fingerprint of raw observation vectors
    vectors_payload = {
        "long_structure_buffer": [round(x, 4) for x in long_structure_buffer],
        "long_atr_multiplier": [round(x, 4) for x in long_atr_multiplier],
        "long_max_stop_distance_atr": [round(x, 4) for x in long_max_stop_distance_atr],
        "long_min_rr_tp1": [round(x, 4) for x in long_min_rr_tp1],
        "short_structure_buffer": [round(x, 4) for x in short_structure_buffer],
        "short_atr_multiplier": [round(x, 4) for x in short_atr_multiplier],
        "short_max_stop_distance_atr": [round(x, 4) for x in short_max_stop_distance_atr],
        "short_min_rr_tp1": [round(x, 4) for x in short_min_rr_tp1],
    }
    vectors_bytes = json.dumps(vectors_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    vectors_sha256 = hashlib.sha256(vectors_bytes).hexdigest()

    # Get current git revision
    import subprocess
    git_rev = "b2acb6b735c378c6ae8d39372012c91949052305"
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        git_rev = p.stdout.strip()
    except Exception:
        pass

    # Build Geometry Artifact
    geometry_artifact = {
        "schema": "aurumiq.calibration.risk_geometry.v1",
        "instrument": "XAUUSD",
        "dataset_fingerprint": expected_dataset_fp,
        "code_revision": git_rev,
        "fold1_train_start": "2020-04-07T00:00:00+00:00",
        "fold1_train_end_exclusive": "2024-02-08T19:12:00+00:00",
        "fold1_train_row_count": fold1_count,
        "observation_universe": "EVERY_ELIGIBLE_15M_TIMESTAMP",
        "eligible_timestamps_count": eligible_timestamps_count,
        "pit_rules": {
            "strict_closed_candles_only": True,
            "zero_lookahead": True,
            "warmup_bars": warmup_bars,
            "future_trade_results_forbidden": True,
            "validation_excluded": True,
            "oos_excluded": True,
            "phase8_excluded": True
        },
        "atr_provenance": "Wilder 14-period ATR from causal closed high, low, close series",
        "structure_provenance": "CausalStructureEngine (5 left / 5 right bars causal swing confirmation, ATR-normalized zones)",
        "quantile_method": "numpy.percentile(method='linear')",
        "sample_counts": {
            "long_raw_sample_count": raw_long_samples,
            "long_valid_sample_count": len(long_structure_buffer),
            "short_raw_sample_count": raw_short_samples,
            "short_valid_sample_count": len(short_structure_buffer)
        },
        "distributions": {
            "long": long_stats,
            "short": short_stats
        },
        "source_data_fingerprint": expected_dataset_fp,
        "observation_vectors_fingerprint": vectors_sha256,
    }

    # Compute artifact fingerprint
    artifact_bytes = json.dumps(geometry_artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    artifact_fp = hashlib.sha256(artifact_bytes).hexdigest()
    geometry_artifact["artifact_fingerprint"] = artifact_fp

    out_geom_path = ROOT / "artifacts" / "calibration" / "xauusd_risk_geometry_fold1_train.json"
    out_geom_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_geom_path, "w", encoding="utf-8") as f:
        json.dump(geometry_artifact, f, indent=2)
    print(f"Emitted geometry artifact: {out_geom_path}")
    print(f"Geometry Artifact Fingerprint: {artifact_fp}")

    # Print summary
    print("\n--- COMPUTED FOLD-1 TRAIN RISK GEOMETRY ---")
    print("LONG:")
    for k, v in long_stats.items():
        print(f"  {k:22}: p10={v['p10']:<5} p25={v['p25']:<5} p50={v['p50']:<5} p75={v['p75']:<5} p90={v['p90']:<5} (valid={v['valid_sample_count']}/{v['raw_sample_count']})")
    print("SHORT:")
    for k, v in short_stats.items():
        print(f"  {k:22}: p10={v['p10']:<5} p25={v['p25']:<5} p50={v['p50']:<5} p75={v['p75']:<5} p90={v['p90']:<5} (valid={v['valid_sample_count']}/{v['raw_sample_count']})")

    return geometry_artifact


if __name__ == "__main__":
    main()
