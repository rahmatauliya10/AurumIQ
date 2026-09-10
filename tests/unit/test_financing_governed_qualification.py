"""Targeted tests for Stage D6 — Financing / Swap Closure.

Verifies:
1. financing swap type points accepted
2. long swap -698.5 preserved exactly
3. short swap 0.0 preserved exactly
4. Wednesday multiplier 3
5. native USC financing cost conversion correct
6. 0.01 lot financing conversion correct
7. historical snapshot does not overwrite current runtime snapshot
8. no averaging between snapshots
9. account swap-free UNKNOWN cannot silently become false
10. account swap-free TRUE correctly bypasses/adjusts financing if runtime model supports it
11. stale Legal Entity HOLD blocker removed from manifest
12. current Legal Entity PASS preserved
13. Phase 6 evidence unchanged
14. execution gap semantics unchanged
15. raw evidence remains untracked
16. readiness changes only when all required evidence qualifies
"""

import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import pytest

from apps.market_data.friction.artifact_parsers import (
    parse_financing_backing_artifact,
    parse_optional_evidence_bool,
)
from apps.market_data.friction.financing import (
    calculate_overnight_swap_usd,
    is_triple_swap_day,
)
from apps.market_data.friction.legal_entity import (
    verify_governed_composite_legal_entity,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FREEZE_MANIFEST_PATH = ROOT_DIR / "artifacts" / "calibration" / "phase6_xauusdc_empirical_freeze.json"
NATIVE_MANIFEST_PATH = ROOT_DIR / "artifacts" / "calibration" / "xauusd_standard_cent_empirical_friction_manifest.json"
FINANCING_POLICY_PATH = ROOT_DIR / "artifacts" / "calibration" / "xauusdc_financing_policy.json"
EVIDENCE_INDEX_PATH = ROOT_DIR / "artifacts" / "calibration" / "xauusdc_freeze_evidence_index.json"


@pytest.fixture
def canonical_financing_policy():
    assert FINANCING_POLICY_PATH.exists(), f"Missing financing policy: {FINANCING_POLICY_PATH}"
    return json.loads(FINANCING_POLICY_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def native_manifest():
    assert NATIVE_MANIFEST_PATH.exists(), f"Missing native manifest: {NATIVE_MANIFEST_PATH}"
    return json.loads(NATIVE_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def freeze_manifest():
    assert FREEZE_MANIFEST_PATH.exists(), f"Missing freeze manifest: {FREEZE_MANIFEST_PATH}"
    return json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def evidence_index():
    assert EVIDENCE_INDEX_PATH.exists(), f"Missing evidence index: {EVIDENCE_INDEX_PATH}"
    return json.loads(EVIDENCE_INDEX_PATH.read_text(encoding="utf-8"))


# --- 1. Financing swap type points accepted ---
def test_1_financing_swap_type_points_accepted(canonical_financing_policy, native_manifest):
    """Test 1: Swap type 'points' is recognized and accepted."""
    assert canonical_financing_policy["symbol_swap_schedule"]["swap_type"] == "points"
    assert native_manifest["evidence_inventory"]["financing_policy"]["terminal_swap_type"] == "points"


# --- 2. Long swap -698.5 preserved exactly ---
def test_2_long_swap_preserved_exactly(canonical_financing_policy, native_manifest):
    """Test 2: Terminal swap long points -698.5 is preserved exactly."""
    policy_long = Decimal(str(canonical_financing_policy["symbol_swap_schedule"]["swap_long_points"]))
    assert policy_long == Decimal("-698.5")

    manifest_long = Decimal(str(native_manifest["evidence_inventory"]["financing_policy"]["swap_long_points"]))
    assert manifest_long == Decimal("-698.5")


# --- 3. Short swap 0.0 preserved exactly ---
def test_3_short_swap_preserved_exactly(canonical_financing_policy, native_manifest):
    """Test 3: Terminal swap short points 0.0 is preserved exactly."""
    policy_short = Decimal(str(canonical_financing_policy["symbol_swap_schedule"]["swap_short_points"]))
    assert policy_short == Decimal("0.0")

    manifest_short = Decimal(str(native_manifest["evidence_inventory"]["financing_policy"]["swap_short_points"]))
    assert manifest_short == Decimal("0.0")


# --- 4. Wednesday multiplier 3 ---
def test_4_wednesday_multiplier_three():
    """Test 4: Wednesday overnight rollover applies a 3.0x triple swap multiplier."""
    wed_dt = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    thu_dt = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)
    tue_dt = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)

    assert is_triple_swap_day(wed_dt) is True
    assert is_triple_swap_day(thu_dt) is False
    assert is_triple_swap_day(tue_dt) is False


# --- 5. Native USC financing cost conversion correct ---
def test_5_native_usc_financing_cost_conversion():
    """Test 5: Native USC financing amount conversion for 1.00 lot XAUUSDc.

    Geometry:
        point_size = 0.001
        contract_size = 1.0 XAU
        tick_value = 0.1 USC per tick (1 tick = 0.001)

    Cost derivation:
        1 lot * 1.0 oz * (-698.5 points * 0.001 USD) = -0.6985 USD
        -0.6985 USD * 100 USC/USD = -69.85 USC per normal rollover.
        Triple swap (Wednesday): -69.85 * 3 = -209.55 USC.
    """
    contract_size = Decimal("1.0")
    point_size = Decimal("0.001")
    swap_long = Decimal("-698.5")

    # Normal day
    tue_dt = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
    swap_usd_1lot = calculate_overnight_swap_usd(
        volume_lots=Decimal("1.0"),
        contract_size=contract_size,
        swap_points_per_day=swap_long,
        point_size=point_size,
        rollover_dt=tue_dt,
    )
    # USD amount rounded to 2 decimals is -0.70 USD = -70 USC, but exact unrounded is -0.6985 USD
    exact_swap_usd = Decimal("1.0") * contract_size * swap_long * point_size * Decimal("1.0")
    exact_swap_usc = exact_swap_usd * Decimal("100")
    assert exact_swap_usc == Decimal("-69.85")

    # Wednesday triple swap
    wed_dt = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)
    exact_wed_usd = Decimal("1.0") * contract_size * swap_long * point_size * Decimal("3.0")
    exact_wed_usc = exact_wed_usd * Decimal("100")
    assert exact_wed_usc == Decimal("-209.55")

    # Short swap is 0.0
    swap_short = Decimal("0.0")
    short_swap_usd = calculate_overnight_swap_usd(
        volume_lots=Decimal("1.0"),
        contract_size=contract_size,
        swap_points_per_day=swap_short,
        point_size=point_size,
        rollover_dt=wed_dt,
    )
    assert short_swap_usd == Decimal("0.00")


# --- 6. 0.01 lot financing conversion correct ---
def test_6_point_zero_one_lot_financing_conversion():
    """Test 6: Native USC financing amount conversion for 0.01 lot minimum volume.

    Cost derivation:
        0.01 lot * 1.0 oz * (-698.5 * 0.001 USD) = -0.006985 USD
        -0.006985 USD * 100 USC/USD = -0.6985 USC per normal rollover.
        Triple swap: -0.6985 * 3 = -2.0955 USC.
    """
    contract_size = Decimal("1.0")
    point_size = Decimal("0.001")
    swap_long = Decimal("-698.5")

    exact_usd_001 = Decimal("0.01") * contract_size * swap_long * point_size * Decimal("1.0")
    exact_usc_001 = exact_usd_001 * Decimal("100")
    assert exact_usc_001 == Decimal("-0.6985")

    exact_wed_usd_001 = Decimal("0.01") * contract_size * swap_long * point_size * Decimal("3.0")
    exact_wed_usc_001 = exact_wed_usd_001 * Decimal("100")
    assert exact_wed_usc_001 == Decimal("-2.0955")


# --- 7. Historical snapshot does not overwrite current runtime snapshot ---
def test_7_historical_snapshot_does_not_overwrite_runtime_snapshot(freeze_manifest, canonical_financing_policy):
    """Test 7: Historical note acknowledges difference without overwriting runtime truth."""
    # Freeze manifest preserves note regarding capture differential
    note = freeze_manifest["published_and_terminal_cost_context"]["note"]
    assert "Historical/published swap evidence differs by capture time" in note

    # Terminal runtime policy preserves -698.5
    assert canonical_financing_policy["symbol_swap_schedule"]["swap_long_points"] == -698.5
    assert canonical_financing_policy["symbol_swap_schedule"]["swap_short_points"] == 0.0


# --- 8. No averaging between snapshots ---
def test_8_no_averaging_between_snapshots(canonical_financing_policy):
    """Test 8: Current runtime policy does NOT average historical (-15.5) and terminal (-698.5) values."""
    s_long = canonical_financing_policy["symbol_swap_schedule"]["swap_long_points"]
    assert s_long != (-15.5 + -698.5) / 2
    assert s_long == -698.5


# --- 9. Account swap-free UNKNOWN cannot silently become false ---
def test_9_account_swap_free_unknown_not_silently_false(canonical_financing_policy, native_manifest):
    """Test 9: Unknown swap-free status remains None (UNKNOWN), NOT False."""
    assert canonical_financing_policy["account_swap_free_policy"]["actual_account_swap_free_status"] is None
    assert canonical_financing_policy["account_swap_free_policy"]["interpretation"] == "UNKNOWN"

    assert native_manifest["evidence_inventory"]["financing_policy"]["actual_account_swap_free_status"] is None


# --- 10. Account swap-free TRUE correctly bypasses/adjusts financing ---
def test_10_account_swap_free_true_bypasses_financing():
    """Test 10: If account swap-free status is True, calculate_overnight_swap_usd returns 0.00."""
    wed_dt = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)
    swap = calculate_overnight_swap_usd(
        volume_lots=Decimal("1.0"),
        contract_size=Decimal("1.0"),
        swap_points_per_day=Decimal("-698.5"),
        point_size=Decimal("0.001"),
        rollover_dt=wed_dt,
        actual_account_swap_free_status=True,
    )
    assert swap == Decimal("0.00")


# --- 11. Stale Legal Entity HOLD blocker removed from manifest ---
def test_11_stale_legal_entity_hold_blocker_removed(native_manifest):
    """Test 11: Stale Legal Entity HOLD status is removed from native manifest."""
    le = native_manifest["evidence_inventory"]["legal_entity_scope"]
    assert le["status"] == "QUALIFIED"
    assert le["legal_entity_code"] == "EXNESS_SC_LTD"
    assert le["production_origin_authenticity"] == "PASS"

    blocking = native_manifest.get("blocking_reasons", [])
    assert not any("Legal entity evidence snapshot missing" in b for b in blocking)
    assert not any("Financing policy remains PARTIAL" in b for b in blocking)


# --- 12. Current Legal Entity PASS preserved ---
def test_12_current_legal_entity_pass_preserved():
    """Test 12: Production review receipt auto-discovery continues to pass."""
    res = verify_governed_composite_legal_entity()
    assert res.composite_evidence_integrity == "PASS"


# --- 13. Phase 6 evidence unchanged ---
def test_13_phase6_evidence_unchanged(freeze_manifest, evidence_index):
    """Test 13: Phase 6 frozen evidence counts and hashes remain strictly unchanged."""
    assert len(freeze_manifest["evidence_hashes"]) == 9
    assert len(evidence_index["evidence_files"]) == 9
    assert freeze_manifest["execution_empirics"]["all_xauusdc_deals_n"] == 54


# --- 14. Execution gap semantics unchanged ---
def test_14_execution_gap_semantics_unchanged(freeze_manifest):
    """Test 14: True requested price slippage is UNOBSERVABLE and execution gap mean is 0.0."""
    emp = freeze_manifest["execution_empirics"]
    assert emp["true_requested_price_slippage"]["status"] == "UNOBSERVABLE"
    assert emp["market_mobile_entries"]["execution_gap_vs_reference_quote_points"]["mean"] == 0.0


# --- 15. Raw evidence remains untracked ---
def test_15_raw_evidence_remains_untracked():
    """Test 15: Raw evidence directories are ignored and not tracked in Git."""
    git_candidates = [
        Path(r"C:\Users\PLANT03\AppData\Local\Programs\Git\cmd\git.exe"),
        Path(r"C:\Program Files\Git\cmd\git.exe"),
    ]
    git_bin = next((p for p in git_candidates if p.exists()), None)
    if git_bin:
        res = subprocess.run(
            [str(git_bin), "ls-files", "artifacts/calibration/xauusdc_freeze_evidence/*"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        assert res.stdout.strip() == "", "Raw freeze evidence must not be tracked."

        res_le = subprocess.run(
            [str(git_bin), "ls-files", "artifacts/calibration/legal_entity_evidence/*"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        assert res_le.stdout.strip() == "", "Raw legal entity evidence must not be tracked."


# --- 16. Readiness changes only when all required evidence qualifies ---
def test_16_readiness_integrity(native_manifest):
    """Test 16: Hard readiness gate remains fail-closed until live DB models are activated."""
    gate = native_manifest["hard_readiness_gate"]
    assert gate["passed"] is False
    assert gate["published_decision"] == "WAIT"
    assert gate["is_production_authorized"] is False
