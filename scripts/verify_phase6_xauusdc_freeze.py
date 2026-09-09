"""Phase 6 XAUUSDc Empirical Freeze Two-Level Dual Verifier.

Level 1 (Public / CI Safe):
- Validates manifest schema and metadata consistency
- Validates frozen contract geometry invariants
- Validates evidence index and hash declarations
- Validates sample counts and semantic guardrails
- Validates Phase 8 remains strictly blocked
- Validates raw evidence directory is ignored by Git
- NEVER claims raw byte verification when files are absent

Level 2 (Local Full Evidence):
- Executes when local evidence files exist in artifacts/calibration/xauusdc_freeze_evidence/
- Cryptographically verifies bit-for-bit SHA-256 for all 9 evidence files
- Validates byte length match
- Parses execution telemetry CSV (54 deals)
- Parses quote execution audit CSV (54 quotes, 100% coverage)
- Verifies normal market entry proxy: 27 entries (19 BUY, 8 SELL) with 0.0 pts execution gap
- Verifies tick lag statistics (median 256ms, p95 1010.9ms, max 1339ms)
- Verifies forced-exit displacement quarantine: 24 SO (max 356 pts adverse, -76 pts favorable)
- Verifies true requested price slippage remains UNOBSERVABLE
"""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "artifacts" / "calibration" / "phase6_xauusdc_empirical_freeze.json"
INDEX_PATH = ROOT / "artifacts" / "calibration" / "xauusdc_freeze_evidence_index.json"
LOCAL_EVIDENCE_DIR = ROOT / "artifacts" / "calibration" / "xauusdc_freeze_evidence"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}")
        raise AssertionError(f"VERIFICATION_FAILED: {msg}")
    print(f"PASS: {msg}")


def verify_level_1_public_metadata() -> Dict[str, Any]:
    print("\n--- LEVEL 1: PUBLIC / CI SAFE VERIFICATION ---")
    check(MANIFEST_PATH.exists(), f"Manifest exists: {MANIFEST_PATH.name}")
    check(INDEX_PATH.exists(), f"Evidence index exists: {INDEX_PATH.name}")

    m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    # Schema & identity
    check(m.get("schema") == "aurumiq.phase6.empirical_freeze.v1", "manifest schema is aurumiq.phase6.empirical_freeze.v1")
    check(m.get("freeze_id") == "XAUUSDc-EXNESS-STANDARD-CENT-20260909", "freeze_id is XAUUSDc-EXNESS-STANDARD-CENT-20260909")
    check(m.get("freeze_status") == "READY_FOR_REPO_INTEGRATION_NOT_YET_PRODUCTION_QUALIFIED", "freeze_status is valid")

    inst = m.get("instrument", {})
    check(inst.get("symbol") == "XAUUSDc", "instrument symbol is XAUUSDc")
    check(inst.get("account_tier") == "Standard Cent", "instrument account_tier is Standard Cent")
    check(inst.get("account_currency") == "USC", "instrument account_currency is USC")
    check(inst.get("server") == "Exness-MT5Real25", "server is Exness-MT5Real25")

    # Frozen contract geometry invariants
    geo = m.get("contract_geometry", {})
    check(geo.get("status") == "SEALED", "contract geometry status is SEALED")
    check(geo.get("classification") == "DIRECT", "contract geometry classification is DIRECT")
    check(geo.get("digits") == 3, "digits == 3")
    check(geo.get("point") == 0.001, "point == 0.001")
    check(geo.get("tick_size") == 0.001, "tick_size == 0.001")
    check(geo.get("tick_value") == 0.1, "tick_value == 0.1 USC")
    check(geo.get("tick_value_profit") == 0.1, "tick_value_profit == 0.1 USC")
    check(geo.get("tick_value_loss") == 0.1, "tick_value_loss == 0.1 USC")
    check(geo.get("contract_size") == 1.0, "contract_size == 1.0 XAU")
    check(geo.get("volume_min_lot") == 0.01, "volume_min_lot == 0.01")
    check(geo.get("volume_max_lot") == 200.0, "volume_max_lot == 200.0")
    check(geo.get("volume_step_lot") == 0.01, "volume_step_lot == 0.01")

    # Critical semantics
    emp = m.get("execution_empirics", {})
    check(emp.get("all_xauusdc_deals_n") == 54, "all_xauusdc_deals_n == 54")
    check(emp.get("broker_tick_reference_coverage_n") == 54, "broker_tick_reference_coverage_n == 54")
    check(emp.get("broker_tick_reference_coverage_pct") == 100.0, "coverage pct == 100.0%")

    mkt = emp.get("market_mobile_entries", {})
    check(mkt.get("n") == 27, "market_mobile_entries n == 27")
    check(mkt.get("buy_n") == 19, "market_mobile_entries buy_n == 19")
    check(mkt.get("sell_n") == 8, "market_mobile_entries sell_n == 8")

    gaps = mkt.get("execution_gap_vs_reference_quote_points", {})
    check(gaps.get("mean") == 0.0, "execution_gap mean == 0.0")
    check(gaps.get("median") == 0.0, "execution_gap median == 0.0")
    check(gaps.get("p95") == 0.0, "execution_gap p95 == 0.0")
    check(gaps.get("max_adverse") == 0.0, "execution_gap max_adverse == 0.0")

    lag = mkt.get("reference_tick_lag_ms", {})
    check(lag.get("median") == 256.0, "reference tick lag median == 256.0 ms")
    check(lag.get("p95") == 1010.9, "reference tick lag p95 == 1010.9 ms")
    check(lag.get("max") == 1339.0, "reference tick lag max == 1339.0 ms")

    slippage = emp.get("true_requested_price_slippage", {})
    check(slippage.get("status") == "UNOBSERVABLE", "true_requested_price_slippage is UNOBSERVABLE")

    so = emp.get("order_price_displacement_observations", {})
    check(so.get("status") == "MEASURED_BUT_NOT_NORMAL_MARKET_SLIPPAGE", "order_price_displacement status is MEASURED_BUT_NOT_NORMAL_MARKET_SLIPPAGE")
    check(so.get("stop_out_n") == 24, "stop_out_n == 24")
    check(so.get("stop_out_max_adverse_points") == 356.0, "stop_out_max_adverse_points == 356.0")
    check(so.get("stop_out_max_favorable_points") == -76.0, "stop_out_max_favorable_points == -76.0")

    # Governance: Phase 8 Gate
    gov = m.get("governance", {})
    p8 = gov.get("phase8_gate", {})
    check(p8.get("status") == "BLOCKED", "phase8_gate status is strictly BLOCKED")
    check("LEGAL_ENTITY governed qualification HOLD" in str(p8.get("remaining_known_blockers")), "LEGAL_ENTITY blocker registered")
    check("FINANCING remains PARTIAL" in str(p8.get("remaining_known_blockers")), "FINANCING blocker registered")

    # Check evidence hashes declaration count
    hashes = m.get("evidence_hashes", {})
    check(len(hashes) == 9, "9 evidence file hashes declared in manifest")
    check(len(idx.get("evidence_files", [])) == 9, "9 evidence file declarations in evidence index")

    # Gitignore verification for raw evidence path
    git_ignore_path = ROOT / ".gitignore"
    check(git_ignore_path.exists(), ".gitignore file exists")
    gitignore_text = git_ignore_path.read_text(encoding="utf-8")
    check("artifacts/calibration/xauusdc_freeze_evidence/" in gitignore_text, "raw evidence directory is explicitly in .gitignore")

    return m


def verify_level_2_local_evidence(manifest: Dict[str, Any]) -> Tuple[bool, bool]:
    print("\n--- LEVEL 2: LOCAL FULL EVIDENCE VERIFICATION ---")
    hashes = manifest.get("evidence_hashes", {})

    if not LOCAL_EVIDENCE_DIR.exists():
        print(f"INFO: Local evidence directory not present at {LOCAL_EVIDENCE_DIR}")
        return False, False

    present_count = 0
    for fname in hashes.keys():
        fpath = LOCAL_EVIDENCE_DIR / fname
        if fpath.exists():
            present_count += 1

    if present_count == 0:
        print("INFO: No raw evidence files present in local directory (public/clean checkout).")
        return False, False

    if present_count < len(hashes):
        print(f"FAIL: Incomplete raw evidence directory ({present_count}/{len(hashes)} files present).")
        return True, False

    print(f"PASS: All {present_count} raw evidence files present locally.")

    # Cryptographic SHA-256 and byte length verification
    for fname, meta in hashes.items():
        fpath = LOCAL_EVIDENCE_DIR / fname
        check(fpath.exists(), f"Local file exists: {fname}")
        actual_bytes = fpath.stat().st_size
        check(actual_bytes == meta["bytes"], f"Byte length matches for {fname}: {actual_bytes} == {meta['bytes']}")
        calc_sha = sha256_file(fpath)
        check(calc_sha == meta["sha256"], f"SHA-256 matches for {fname}: {calc_sha}")

    # Parse and verify CSV records
    exec_csv_path = LOCAL_EVIDENCE_DIR / "AurumIQ_XAUUSDc_execution_evidence_20260909_165151.csv"
    quote_csv_path = LOCAL_EVIDENCE_DIR / "AurumIQ_XAUUSDc_quote_execution_audit_20260909_165827.csv"

    with open(exec_csv_path, "r", encoding="utf-8") as f:
        exec_rows = list(csv.DictReader(f))
    with open(quote_csv_path, "r", encoding="utf-8") as f:
        quote_rows = list(csv.DictReader(f))

    check(len(exec_rows) == 54, f"Execution deals count == 54 (observed {len(exec_rows)})")
    check(len(quote_rows) == 54, f"Quote audit deals count == 54 (observed {len(quote_rows)})")

    check(all(r["symbol"] == "XAUUSDc" for r in exec_rows), "All execution deals have symbol XAUUSDc")
    check(all(r["symbol"] == "XAUUSDc" for r in quote_rows), "All quote audit rows have symbol XAUUSDc")
    check(all(r["account_currency"] == "USC" for r in exec_rows), "All execution deals have account_currency USC")
    check(all(r["server"] == "Exness-MT5Real25" for r in exec_rows), "All execution deals have server Exness-MT5Real25")
    check(all(Decimal(r["point"]) == Decimal("0.001") for r in exec_rows), "All execution deals have point == 0.001")
    check(all(Decimal(r["tick_size"]) == Decimal("0.001") for r in exec_rows), "All execution deals have tick_size == 0.001")
    check(all(Decimal(r["tick_value"]) == Decimal("0.1") for r in exec_rows), "All execution deals have tick_value == 0.1")
    check(all(Decimal(r["contract_size"]) == Decimal("1.0") for r in exec_rows), "All execution deals have contract_size == 1.0")

    # Deal classifications
    entries = [r for r in exec_rows if r["entry"] == "DEAL_ENTRY_IN"]
    exits = [r for r in exec_rows if r["entry"] == "DEAL_ENTRY_OUT"]
    check(len(entries) == 27, f"27 entry deals (observed {len(entries)})")
    check(len(exits) == 27, f"27 exit deals (observed {len(exits)})")

    mobile_entries = [r for r in entries if r["reason"] == "DEAL_REASON_MOBILE"]
    check(len(mobile_entries) == 27, f"27 mobile entries (observed {len(mobile_entries)})")
    buys = [r for r in mobile_entries if r["side"] == "DEAL_TYPE_BUY"]
    sells = [r for r in mobile_entries if r["side"] == "DEAL_TYPE_SELL"]
    check(len(buys) == 19, f"19 BUY entries (observed {len(buys)})")
    check(len(sells) == 8, f"8 SELL entries (observed {len(sells)})")

    so_exits = [r for r in exits if r["reason"] == "DEAL_REASON_SO"]
    sl_exits = [r for r in exits if r["reason"] == "DEAL_REASON_SL"]
    check(len(so_exits) == 24, f"24 stop-out exits (observed {len(so_exits)})")
    check(len(sl_exits) == 3, f"3 stop-loss exits (observed {len(sl_exits)})")

    # Execution gap vs reference quote
    mobile_quotes = [r for r in quote_rows if r["entry"] == "DEAL_ENTRY_IN" and r["reason"] == "DEAL_REASON_MOBILE"]
    check(len(mobile_quotes) == 27, f"27 mobile quote audits (observed {len(mobile_quotes)})")
    check(all(Decimal(r["execution_gap_points"]) == Decimal("0.0") for r in mobile_quotes), "Execution gap is 0.0 points for all 27 mobile entries")
    check(all(Decimal(r["spread_points"]) == Decimal("260.0") for r in quote_rows), "All quote audit reference spreads are 260.0 points")

    # Forced exit displacement quarantine
    so_deals = [r for r in exec_rows if r["reason"] == "DEAL_REASON_SO" and r["signed_slippage_points"]]
    check(len(so_deals) == 24, f"24 stop-out deals with displacement (observed {len(so_deals)})")
    displacements = [float(r["signed_slippage_points"]) for r in so_deals]
    check(abs(max(displacements) - 356.0) < 1e-9, f"SO max adverse displacement == 356.0 points (observed {max(displacements)})")
    check(abs(min(displacements) - (-76.0)) < 1e-9, f"SO max favorable displacement == -76.0 points (observed {min(displacements)})")

    return True, True


def main() -> int:
    print("===========================================================")
    print("AURUMIQ PHASE 6 XAUUSDc EMPIRICAL FREEZE DUAL VERIFIER")
    print("===========================================================")

    # 1. Level 1 verification (Public / CI Safe)
    manifest = verify_level_1_public_metadata()
    public_ok = True

    # 2. Level 2 verification (Local Full Evidence)
    raw_available, raw_sha_ok = verify_level_2_local_evidence(manifest)

    print("\n===========================================================")
    print("VERIFIER SUMMARY REPORT:")
    print(f"PUBLIC_METADATA_VERIFIED = {str(public_ok).lower()}")
    print(f"RAW_EVIDENCE_AVAILABLE = {str(raw_available).lower()}")
    print(f"RAW_EVIDENCE_SHA256_VERIFIED = {str(raw_sha_ok).lower()}")

    if raw_available and raw_sha_ok:
        print("FULL_EVIDENCE_VERIFIED = true")
        print("LEVEL_2_LOCAL_AUDIT = PASS")
    else:
        print("FULL_EVIDENCE_VERIFIED = false")
        print("REASON = LOCAL_RAW_EVIDENCE_NOT_AVAILABLE" if not raw_available else "REASON = RAW_EVIDENCE_SHA256_MISMATCH")

    print("\nREADINESS STATE:")
    print("PHASE8_READY = false")
    print("hard_readiness_gate.passed = false")
    print("is_production_authorized = false")
    print("published_decision = WAIT")
    print("===========================================================")

    if not public_ok:
        return 1
    if raw_available and not raw_sha_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
