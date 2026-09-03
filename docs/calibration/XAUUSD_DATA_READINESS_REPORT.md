# AurumIQ — XAUUSD Data Readiness Audit Report

> **Governance Level:** Evidence-Driven Empirical Calibration Campaign (Post Phase 7 / Pre Phase 8)  
> **Authoritative Baseline SHA:** `57f6de1405d0df8548182a166d245f1a3173363d`<br>
> **Audit Code Revision:** `da09745d2160a065ebde6716c3b0c10052b7756b`<br>
> **Data Acquisition Code Revision:** `UNRESOLVED_PRECOMMIT_WORKTREE`<br>
> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)<br>
> **Authoritative Analytical Market Source:** `PRIMARY_XAUUSD_SPOT`<br>
> **Audit Date:** 2026-09-03<br>
> **Audit Status:** ❌ **CALIBRATION_DATA_NOT_READY**

---

## 1. Executive Summary

In accordance with Sections 0, 4, 5, and 6 of the AurumIQ XAUUSD Empirical Calibration Campaign Protocol, a complete point-in-time audit of all persisted market data, provider listings, and auxiliary evidence was conducted prior to parameter optimization or search.

**Findings:**
1. **Total Persisted Candles:** 170660 across all six required standard analytical timeframes (1m, 5m, 15m, 1h, 4h, 1d).
2. **Historical Window Coverage:** INCOMPLETE (Required: 2020-04-07T00:00:00+00:00 to 2026-09-01T00:00:00+00:00; Observed: 2026-06-01T00:00:00+00:00 to 2026-09-01T00:00:00+00:00).
3. **15m Usable Feature Warm-Up:** 8832 / 20 required bars (PASS).
4. **Volume Evidence Classification:** `UNAVAILABLE`.
5. **Data Contamination:** 0 foreign or cross-asset records detected.
6. **Auxiliary Coverage:** Macro events: 0, Historical Quotes: 0.
7. **Empirical Friction:** `EMPIRICAL_FRICTION_NOT_CONFIGURED`.

**Hard Data-Readiness Gate Result:**
Decision: **`CALIBRATION_DATA_NOT_READY`**

### Decision Rationale & Missing Dependencies:
- HISTORICAL_COVERAGE_INCOMPLETE: Required calibration window [2020-04-07 to 2026-09-01] is incomplete for timeframe(s): 15m, 1h, 4h, 1d, 5m, 1m.

---

## 2. Market Listing & Provider Attribution

| Field | Value | Governance Requirement | Compliance |
| :--- | :--- | :--- | :--- |
| **Instrument** | `XAU/USD` | Spot Gold denominated in USD | ✅ PASS |
| **Listing Role** | `PRIMARY_XAUUSD_SPOT` | Primary analytical source | ✅ PASS |
| **Provider ID** | `twelve_data_xauusd` | Spot provider registry binding | ✅ PASS |
| **Provider Symbol** | `XAU/USD` | Explicit spot symbol | ✅ PASS |
| **Listing Status** | `ACTIVE` | Active provider registry | ✅ PASS |

---

## 3. Historical Coverage & Timeframe Cadence

### A. Window Coverage by Timeframe
| Timeframe | Candle Count | Earliest Timestamp Open | Latest Timestamp Close | Start Satisfied | End Satisfied | Coverage Complete |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 132480 | `2026-06-01T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ❌ FAIL | ✅ PASS | ❌ INCOMPLETE |
| **5m** | 26496 | `2026-06-01T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ❌ FAIL | ✅ PASS | ❌ INCOMPLETE |
| **15m** | 8832 | `2026-06-01T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ❌ FAIL | ✅ PASS | ❌ INCOMPLETE |
| **1h** | 2208 | `2026-06-01T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ❌ FAIL | ✅ PASS | ❌ INCOMPLETE |
| **4h** | 552 | `2026-06-01T01:00:00+00:00` | `2026-09-01T01:00:00+00:00` | ❌ FAIL | ✅ PASS | ❌ INCOMPLETE |
| **1d** | 92 | `2026-06-01T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ❌ FAIL | ✅ PASS | ❌ INCOMPLETE |

### B. Internal Cadence & Gap Analysis (Inside Observed Span)
| Timeframe | Observed Count | Expected Cadence | Internal Gaps | Missing Intervals | Missing % | Largest Gap | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 132480 | 60s | 0 | 0 | 0.00% | 0s | `EVALUATED` |
| **5m** | 26496 | 300s | 0 | 0 | 0.00% | 0s | `EVALUATED` |
| **15m** | 8832 | 900s | 0 | 0 | 0.00% | 0s | `EVALUATED` |
| **1h** | 2208 | 3600s | 0 | 0 | 0.00% | 0s | `EVALUATED` |
| **4h** | 552 | 14400s | 0 | 0 | 0.00% | 0s | `EVALUATED` |
| **1d** | 92 | 86400s | 0 | 0 | 0.00% | 0s | `EVALUATED` |

---

## 4. Volume Evidence Distribution

- `REAL_VOLUME`: 0
- `TICK_VOLUME`: 0
- `PROXY_VOLUME`: 0
- `UNAVAILABLE`: 170660
- **Classification:** `UNAVAILABLE`

---

## 5. Dataset Provenance & Fingerprints

- **Audit Code Revision:** `da09745d2160a065ebde6716c3b0c10052b7756b`
- **Data Acquisition Code Revision:** `UNRESOLVED_PRECOMMIT_WORKTREE`
- **Phase 6 15m Dataset Fingerprint:** `d5a97bfed92a97fe96bcb2ec1b9d02fd20f027ad8d17ed9f5b9c824a525fe749`
- **Readiness Evidence Fingerprint (6-TF):** `0dcacedf50dd818499bd7a23af159b96153da14122ee6e29897cab89d3efed56`
- **Readiness Fingerprint Reproducible:** `PASS`

---

## 6. Governance Determination
- **Production Authority:** `FALSE` (Enforced)
- **Published Decision:** `WAIT` (Enforced)
- **Phase 8 Status:** `HOLD` (Enforced)
- **Calibration Status:** `CALIBRATION_DATA_NOT_READY`
- **Overall Historical Coverage:** `INCOMPLETE`
- **Final Decision:** `CALIBRATION_DATA_NOT_READY`
