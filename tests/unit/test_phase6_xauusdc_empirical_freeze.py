"""Unit tests for Phase 6 XAUUSDc Empirical Freeze Integration.

Adheres strictly to Stage D4 Governance & Public Repository Privacy Policy:
1. Frozen contract geometry invariants
2. Strict semantic separation:
   - TRUE_REQUESTED_PRICE_SLIPPAGE = UNOBSERVABLE
   - EXECUTION_GAP_VS_REFERENCE_QUOTE = EMPIRICAL / MEASURED
   - STOP_OUT_DISPLACEMENT = EMPIRICAL / FORCED_EXIT_ONLY
3. UNOBSERVABLE cannot default to zero or numeric float
4. Stop Out forced exits cannot enter normal entry slippage calibration
5. Expected aggregate sample counts and quote coverage invariants (54 deals, 54 quotes)
6. Evidence SHA-256 declarations exist and match between manifest and evidence index
7. Raw evidence directory is strictly gitignored (privacy protection)
8. Phase 8 remains strictly blocked (hard_readiness_gate.passed = False, WAIT)
9. Dual verifier Level 1 (public/CI safe) and Level 2 (local full evidence when present)
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FREEZE_MANIFEST_PATH = ROOT / "artifacts" / "calibration" / "phase6_xauusdc_empirical_freeze.json"
EVIDENCE_INDEX_PATH = ROOT / "artifacts" / "calibration" / "xauusdc_freeze_evidence_index.json"
NATIVE_MANIFEST_PATH = ROOT / "artifacts" / "calibration" / "xauusd_standard_cent_empirical_friction_manifest.json"
LOCAL_EVIDENCE_DIR = ROOT / "artifacts" / "calibration" / "xauusdc_freeze_evidence"


@pytest.fixture
def freeze_manifest():
    assert FREEZE_MANIFEST_PATH.exists(), f"Missing freeze manifest: {FREEZE_MANIFEST_PATH}"
    return json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def evidence_index():
    assert EVIDENCE_INDEX_PATH.exists(), f"Missing evidence index: {EVIDENCE_INDEX_PATH}"
    return json.loads(EVIDENCE_INDEX_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def native_manifest():
    assert NATIVE_MANIFEST_PATH.exists(), f"Missing native manifest: {NATIVE_MANIFEST_PATH}"
    return json.loads(NATIVE_MANIFEST_PATH.read_text(encoding="utf-8"))


# --- 1. Frozen Contract Geometry Invariants ---
def test_frozen_contract_geometry_invariants(freeze_manifest, native_manifest):
    """Verify XAUUSDc contract geometry is sealed and matches exact broker specifications."""
    geo = freeze_manifest["contract_geometry"]
    assert geo["status"] == "SEALED"
    assert geo["classification"] == "DIRECT"
    assert geo["digits"] == 3
    assert Decimal(str(geo["point"])) == Decimal("0.001")
    assert Decimal(str(geo["tick_size"])) == Decimal("0.001")
    assert Decimal(str(geo["tick_value"])) == Decimal("0.1")
    assert geo["tick_value_currency"] == "USC"
    assert Decimal(str(geo["tick_value_profit"])) == Decimal("0.1")
    assert Decimal(str(geo["tick_value_loss"])) == Decimal("0.1")
    assert Decimal(str(geo["contract_size"])) == Decimal("1.0")
    assert Decimal(str(geo["volume_min_lot"])) == Decimal("0.01")
    assert Decimal(str(geo["volume_max_lot"])) == Decimal("200.0")
    assert Decimal(str(geo["volume_step_lot"])) == Decimal("0.01")

    # Native manifest alignment
    native_geo = native_manifest["evidence_inventory"]["contract_geometry"]
    assert native_geo["status"] == "SEALED"
    assert native_geo["broker_symbol"] == "XAUUSDc"
    assert native_geo["digits"] == 3
    assert Decimal(str(native_geo["point_size"])) == Decimal("0.001")
    assert Decimal(str(native_geo["trade_tick_size"])) == Decimal("0.001")
    assert Decimal(str(native_geo["trade_tick_value"])) == Decimal("0.1")
    assert Decimal(str(native_geo["contract_size"])) == Decimal("1.0")
    assert Decimal(str(native_geo["volume_min"])) == Decimal("0.01")
    assert Decimal(str(native_geo["volume_max"])) == Decimal("200.0")
    assert Decimal(str(native_geo["volume_step"])) == Decimal("0.01")


# --- 2. Semantic Separation Invariant ---
def test_semantic_separation_of_empirical_telemetry(freeze_manifest, native_manifest):
    """
    Critical semantics check:
    TRUE_REQUESTED_PRICE_SLIPPAGE = UNOBSERVABLE
    EXECUTION_GAP_VS_REFERENCE_QUOTE = EMPIRICAL / MEASURED
    STOP_OUT_DISPLACEMENT = EMPIRICAL / FORCED_EXIT_ONLY
    These must NEVER be collapsed into a single generic slippage field.
    """
    emp = freeze_manifest["execution_empirics"]
    assert "true_requested_price_slippage" in emp
    assert "market_mobile_entries" in emp
    assert "execution_gap_vs_reference_quote_points" in emp["market_mobile_entries"]
    assert "order_price_displacement_observations" in emp

    # Native manifest check
    native_telemetry = native_manifest["evidence_inventory"]["execution_slippage_telemetry"]
    assert native_telemetry["true_requested_price_slippage"] == "UNOBSERVABLE"
    assert "execution_gap_vs_reference_quote_mean_points" in native_telemetry
    assert "forced_exit_displacement_observations" in native_telemetry
    assert native_telemetry["forced_exit_displacement_observations"]["status"] == "MEASURED_BUT_NOT_NORMAL_MARKET_SLIPPAGE"


# --- 3. UNOBSERVABLE Cannot Default to Zero ---
def test_unobservable_cannot_default_to_zero_or_float(freeze_manifest, native_manifest):
    """Fail closed if true requested price slippage is coerced to 0 or treated as zero cost."""
    true_slippage_status = freeze_manifest["execution_empirics"]["true_requested_price_slippage"]["status"]
    assert true_slippage_status == "UNOBSERVABLE"
    assert true_slippage_status != 0
    assert true_slippage_status != 0.0
    assert not isinstance(true_slippage_status, (int, float))

    native_status = native_manifest["evidence_inventory"]["execution_slippage_telemetry"]["true_requested_price_slippage"]
    assert native_status == "UNOBSERVABLE"
    assert native_status != 0
    assert native_status != 0.0

    # Test calibration consumer guard: function expecting numeric slippage must reject UNOBSERVABLE
    def calculate_calibrated_entry_slippage(val):
        if val == "UNOBSERVABLE":
            raise ValueError("SLIPPAGE_UNOBSERVABLE: True requested price slippage cannot be coerced to numeric cost.")
        return Decimal(str(val))

    with pytest.raises(ValueError, match="SLIPPAGE_UNOBSERVABLE"):
        calculate_calibrated_entry_slippage(true_slippage_status)


# --- 4. Stop Out Cannot Enter Normal Calibration ---
def test_stop_out_cannot_enter_normal_slippage_calibration(freeze_manifest, native_manifest):
    """Ensure Stop Out liquidation samples (displacement up to 356 pts) are strictly quarantined."""
    obs = freeze_manifest["execution_empirics"]["order_price_displacement_observations"]
    assert obs["status"] == "MEASURED_BUT_NOT_NORMAL_MARKET_SLIPPAGE"
    assert obs["stop_out_n"] == 24
    assert Decimal(str(obs["stop_out_max_adverse_points"])) == Decimal("356.0")
    assert Decimal(str(obs["stop_out_max_favorable_points"])) == Decimal("-76.0")

    # Normal market entry stats must have 0 execution gap proxy, not 356 points!
    mkt = freeze_manifest["execution_empirics"]["market_mobile_entries"]
    assert Decimal(str(mkt["execution_gap_vs_reference_quote_points"]["max_adverse"])) == Decimal("0.0")
    assert Decimal(str(mkt["execution_gap_vs_reference_quote_points"]["mean"])) == Decimal("0.0")

    # Native manifest check
    native_so = native_manifest["evidence_inventory"]["execution_slippage_telemetry"]["forced_exit_displacement_observations"]
    assert native_so["stop_out_count"] == 24
    assert Decimal(str(native_so["stop_out_max_adverse_points"])) == Decimal("356.0")
    assert "quarantine_rule" in native_so


# --- 5. Expected Aggregate Sample Counts & Quote Coverage ---
def test_expected_aggregate_sample_counts_and_quote_coverage(freeze_manifest, native_manifest):
    """Verify history N=54, quote coverage 54/54, 27 entries (19 buy, 8 sell), 24 SO, 3 SL."""
    emp = freeze_manifest["execution_empirics"]
    assert emp["all_xauusdc_deals_n"] == 54
    assert emp["broker_tick_reference_coverage_n"] == 54
    assert Decimal(str(emp["broker_tick_reference_coverage_pct"])) == Decimal("100.0")

    mkt = emp["market_mobile_entries"]
    assert mkt["n"] == 27
    assert mkt["buy_n"] == 19
    assert mkt["sell_n"] == 8

    # Lag statistics
    lag = mkt["reference_tick_lag_ms"]
    assert Decimal(str(lag["median"])) == Decimal("256.0")
    assert abs(float(lag["p95"]) - 1010.9) < 1e-5
    assert Decimal(str(lag["max"])) == Decimal("1339.0")


# --- 6. Evidence SHA-256 Declarations Invariance ---
def test_evidence_sha256_declarations_exist_and_consistent(freeze_manifest, evidence_index):
    """Verify 9 evidence files have valid 64-hex SHA-256 hashes declared consistently."""
    manifest_hashes = freeze_manifest["evidence_hashes"]
    assert len(manifest_hashes) == 9

    index_files = {item["filename"]: item for item in evidence_index["evidence_files"]}
    assert len(index_files) == 9

    for fname, meta in manifest_hashes.items():
        assert fname in index_files, f"File {fname} declared in manifest but missing in evidence index"
        assert len(meta["sha256"]) == 64, f"Invalid SHA-256 length for {fname}"
        assert meta["sha256"] == index_files[fname]["sha256"], f"Hash mismatch between manifest and index for {fname}"
        assert meta["bytes"] == index_files[fname]["bytes"], f"Byte mismatch between manifest and index for {fname}"


# --- 7. Raw Evidence Destination is Gitignored ---
def test_raw_evidence_destination_is_gitignored():
    """Verify raw evidence directory is ignored by Git to protect private trading records."""
    gitignore_path = ROOT / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert "artifacts/calibration/xauusdc_freeze_evidence/" in content

    # Test git check-ignore if git is available
    git_candidates = [
        Path(r"C:\Users\PLANT03\AppData\Local\Programs\Git\cmd\git.exe"),
        Path(r"C:\Program Files\Git\cmd\git.exe"),
    ]
    git_bin = next((p for p in git_candidates if p.exists()), None)
    if git_bin:
        res = subprocess.run(
            [str(git_bin), "check-ignore", "-v", "artifacts/calibration/xauusdc_freeze_evidence/sample.csv"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "artifacts/calibration/xauusdc_freeze_evidence/" in res.stdout


# --- 8. Phase 8 Readiness Gate Remains Strictly Blocked ---
def test_phase8_remains_strictly_blocked(freeze_manifest, native_manifest):
    """Verify Phase 8 remains strictly BLOCKED, weight=0.0, passed=False, WAIT."""
    p8 = freeze_manifest["governance"]["phase8_gate"]
    assert p8["status"] == "BLOCKED"
    blockers = p8["remaining_known_blockers"]
    assert any("LEGAL_ENTITY" in b for b in blockers)
    assert any("FINANCING" in b for b in blockers)

    native_gate = native_manifest["hard_readiness_gate"]
    assert native_gate["passed"] is False
    assert native_gate["is_production_authorized"] is False
    assert Decimal(str(native_gate["phase3b_production_weight"])) == Decimal("0.0")
    assert native_gate["published_decision"] == "WAIT"
    assert native_gate["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


# --- 9. Local Evidence Verification (When Files Present) ---
def test_local_evidence_verification_when_present(freeze_manifest):
    """
    If raw evidence files are present locally (Option B local storage):
    Cryptographically verifies SHA-256, byte lengths, and CSV invariants.
    If files are absent (clean CI runner):
    Skips gracefully without failing CI and without claiming full verification.
    """
    if not LOCAL_EVIDENCE_DIR.exists():
        pytest.skip("Local raw evidence directory not present (public CI checkout).")

    manifest_hashes = freeze_manifest["evidence_hashes"]
    present = [fname for fname in manifest_hashes if (LOCAL_EVIDENCE_DIR / fname).exists()]
    if not present:
        pytest.skip("Local raw evidence files absent (public CI checkout).")

    assert len(present) == len(manifest_hashes), f"Incomplete local evidence: {len(present)}/{len(manifest_hashes)}"

    for fname, meta in manifest_hashes.items():
        fpath = LOCAL_EVIDENCE_DIR / fname
        assert fpath.stat().st_size == meta["bytes"]
        h = hashlib.sha256(fpath.read_bytes()).hexdigest()
        assert h == meta["sha256"], f"SHA-256 mismatch for {fname}"
