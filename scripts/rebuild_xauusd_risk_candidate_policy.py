"""
Script to rebuild artifacts/calibration/xauusd_risk_candidate_generation_policy.json
by strictly consuming artifacts/calibration/xauusd_risk_geometry_fold1_train.json
and the frozen selection policy.
"""
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GEOM_PATH = ROOT / "artifacts" / "calibration" / "xauusd_risk_geometry_fold1_train.json"
POLICY_PATH = ROOT / "artifacts" / "calibration" / "xauusd_risk_candidate_generation_policy.json"
SEL_POLICY_PATH = ROOT / "artifacts" / "calibration" / "xauusd_signal_calibration_selection_policy.json"


def compute_policy_fingerprint(data: dict) -> str:
    clean = {k: v for k, v in data.items() if k != "policy_fingerprint"}
    b = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def main():
    print("Rebuilding risk candidate generation policy from computed geometry artifact...")
    with open(GEOM_PATH, "r", encoding="utf-8") as f:
        geom = json.load(f)

    with open(POLICY_PATH, "r", encoding="utf-8") as f:
        policy = json.load(f)

    with open(SEL_POLICY_PATH, "r", encoding="utf-8") as f:
        sel_policy = json.load(f)

    fold1 = sel_policy["walk_forward_policy"]["folds"][0]
    assert fold1["fold_id"] == 1

    # 1. Correct Fold Metadata strictly matching selection policy
    policy["selection_policy_fingerprint_reference"] = "28da9f1b84b8aaddff44a535c38a6e99aeaa8b019a5687205d3605b5d80e8ccc"
    policy["geometry_artifact_fingerprint_reference"] = geom["artifact_fingerprint"]

    policy["historical_bounds"] = {
        "calibration_window_start": "2020-04-07T00:00:00+00:00",
        "calibration_window_end_exclusive": "2026-09-01T00:00:00+00:00",
        "common_risk_domain_source": "FOLD_1_TRAIN_ONLY",
        "train_start": fold1["train_start"],
        "train_end": fold1["train_end"],
        "val_start": fold1["val_start"],
        "val_end": fold1["val_end"],
        "oos_start": fold1["oos_start"],
        "oos_end": fold1["oos_end"],
        "selection_policy_fingerprint": "28da9f1b84b8aaddff44a535c38a6e99aeaa8b019a5687205d3605b5d80e8ccc",
        "oos_exclusion_status": "STRICT_EXCLUSION_ACTIVE",
    }

    # 2. Update train_only_causal_geometry from geom artifact
    l_dist = geom["distributions"]["long"]
    s_dist = geom["distributions"]["short"]

    def extract_quantiles(dist_item):
        return {
            "p10": dist_item["p10"],
            "p25": dist_item["p25"],
            "p50": dist_item["p50"],
            "p75": dist_item["p75"],
            "p90": dist_item["p90"],
        }

    policy["train_only_causal_geometry"] = {
        "source_window": "FOLD_1_TRAIN_ONLY (2020-04-07T00:00:00+00:00 <= t < 2024-02-08T19:12:00+00:00)",
        "isolation_rule": "Strictly Fold 1 TRAIN-only PIT geometry. Excludes Fold 1 validation (>= 2024-02-08T19:12:00Z), all OOS periods (>= 2025-05-21T09:36:00Z), Phase 8 data, and future outcomes (MAE, MFE, PnL, winner/loser labels).",
        "geometry_artifact_path": "artifacts/calibration/xauusd_risk_geometry_fold1_train.json",
        "geometry_artifact_fingerprint": geom["artifact_fingerprint"],
        "observation_vectors_fingerprint": geom["observation_vectors_fingerprint"],
        "observation_universe": geom["observation_universe"],
        "sample_counts": geom["sample_counts"],
        "geometric_observations": {
            "structure_buffer": {
                "causal_basis": "TRAIN_PIT_CONFIRMED_ZONE_WIDTH",
                "unit": "NATIVE_PRICE_UNITS_USD",
                "description": "Zone width buffer derived from Fold 1 TRAIN-only PIT structure zones",
                "long_support": extract_quantiles(l_dist["structure_buffer"]),
                "short_support": extract_quantiles(s_dist["structure_buffer"]),
            },
            "atr_multiplier": {
                "causal_basis": "TRAIN_PIT_STRUCTURE_DISTANCE_NORMALIZED_ATR14",
                "unit": "DIMENSIONLESS_ATR14",
                "description": "Structure-to-invalidation distance normalized by ATR14 in Fold 1 TRAIN-only PIT data",
                "long_support": extract_quantiles(l_dist["atr_multiplier"]),
                "short_support": extract_quantiles(s_dist["atr_multiplier"]),
            },
            "max_stop_distance_atr": {
                "causal_basis": "TRAIN_PIT_DEEP_STRUCTURAL_DISTANCE_NORMALIZED_ATR14",
                "unit": "DIMENSIONLESS_ATR14",
                "description": "Deep protective structural boundary distance normalized by ATR14 in Fold 1 TRAIN-only PIT data",
                "long_support": extract_quantiles(l_dist["max_stop_distance_atr"]),
                "short_support": extract_quantiles(s_dist["max_stop_distance_atr"]),
            },
            "min_rr_tp1": {
                "causal_basis": "TRAIN_PIT_RAW_STRUCTURAL_REWARD_RISK_RATIO",
                "unit": "DIMENSIONLESS_REWARD_RISK_RATIO",
                "description": "Raw structural risk-reward implied by Fold 1 TRAIN-only PIT structure and nearest opposing target",
                "long_support": extract_quantiles(l_dist["min_rr_tp1"]),
                "short_support": extract_quantiles(s_dist["min_rr_tp1"]),
            },
        },
    }

    # 3. Update REFERENCE_CANDIDATE_0 strictly from computed p50 values
    long_p50 = {
        "structure_buffer": l_dist["structure_buffer"]["p50"],
        "atr_multiplier": l_dist["atr_multiplier"]["p50"],
        "max_stop_distance_atr": l_dist["max_stop_distance_atr"]["p50"],
        "min_rr_tp1": l_dist["min_rr_tp1"]["p50"],
        "tp2_atr_multiplier": None,
    }

    short_p50 = {
        "structure_buffer": s_dist["structure_buffer"]["p50"],
        "atr_multiplier": s_dist["atr_multiplier"]["p50"],
        "max_stop_distance_atr": s_dist["max_stop_distance_atr"]["p50"],
        "min_rr_tp1": s_dist["min_rr_tp1"]["p50"],
        "tp2_atr_multiplier": None,
    }

    policy["reference_candidate_0"]["risk_parameters"] = {
        "long": long_p50,
        "short": short_p50,
    }

    # 4. Recompute Policy Fingerprint
    new_fp = compute_policy_fingerprint(policy)
    policy["policy_fingerprint"] = new_fp

    with open(POLICY_PATH, "w", encoding="utf-8") as f:
        json.dump(policy, f, indent=2)

    print(f"Updated policy saved to: {POLICY_PATH}")
    print(f"New Policy Fingerprint: {new_fp}")
    print(f"Reference Candidate 0 LONG: {long_p50}")
    print(f"Reference Candidate 0 SHORT: {short_p50}")


if __name__ == "__main__":
    main()
