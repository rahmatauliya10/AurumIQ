# AurumIQ — XAUUSD Data Readiness Audit Report

> **Governance Level:** Evidence-Driven Empirical Calibration Campaign (Post Phase 7 / Pre Phase 8)  
> **Authoritative Baseline SHA:** `a9922dfbe3df001e2403b334b0b2dad3957590f5`<br>
> **Audit Code Revision:** `a9922dfbe3df001e2403b334b0b2dad3957590f5`<br>
> **Data Acquisition Code Revision:** `c5c521c03c1f4e18da8f2dd7039af998d599d78c`<br>
> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)<br>
> **Authoritative Analytical Market Source:** `PRIMARY_XAUUSD_SPOT`<br>
> **Audit Date:** 2026-09-04<br>
> **Audit Status:** ❌ **CANDLES_READY_MACRO_MISSING**

---

## 1. Executive Summary

In accordance with Sections 0, 4, 5, and 6 of the AurumIQ XAUUSD Empirical Calibration Campaign Protocol, a complete point-in-time audit of all persisted market data, provider listings, and auxiliary evidence was conducted prior to parameter optimization or search.

**Findings:**
1. **Total Persisted Candles:** 3096312 across all six required standard analytical timeframes (1m, 5m, 15m, 1h, 4h, 1d).
2. **Historical Window Coverage:** COMPLETE (Required: 2020-04-07T00:00:00+00:00 to 2026-09-01T00:00:00+00:00; Observed: 2020-04-07T00:00:00+00:00 to 2026-09-01T01:00:00+00:00).
3. **15m Usable Feature Warm-Up:** 161233 / 20 required bars (PASS).
4. **Volume Evidence Classification:** `UNAVAILABLE`.
5. **Data Contamination:** 0 foreign or cross-asset records detected.
6. **Auxiliary Coverage:** Macro events: 0, Historical Quotes: 0.
7. **Empirical Friction:** `EMPIRICAL_FRICTION_NOT_CONFIGURED`.

**Hard Data-Readiness Gate Result:**
Decision: **`CANDLES_READY_MACRO_MISSING`**

### Decision Rationale & Missing Dependencies:
- Auxiliary evidence incomplete: Point-in-time macro event coverage is 0. Macro feed remains MISSING.

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
| Timeframe | Candle Count | Native Required Start | Earliest Timestamp Open | Latest Timestamp Close | Start Satisfied | End Satisfied | Coverage Complete |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 2399751 | `2020-04-07T00:00:00+00:00` | `2020-04-07T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ✅ PASS | ✅ PASS | ✅ COMPLETE |
| **5m** | 482458 | `2020-04-07T00:00:00+00:00` | `2020-04-07T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ✅ PASS | ✅ PASS | ✅ COMPLETE |
| **15m** | 161233 | `2020-04-07T00:00:00+00:00` | `2020-04-07T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ✅ PASS | ✅ PASS | ✅ COMPLETE |
| **1h** | 40407 | `2020-04-07T00:00:00+00:00` | `2020-04-07T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ✅ PASS | ✅ PASS | ✅ COMPLETE |
| **4h** | 10647 | `2020-04-07T01:00:00+00:00` | `2020-04-07T01:00:00+00:00` | `2026-09-01T01:00:00+00:00` | ✅ PASS | ✅ PASS | ✅ COMPLETE |
| **1d** | 1816 | `2020-04-07T00:00:00+00:00` | `2020-04-07T00:00:00+00:00` | `2026-09-01T00:00:00+00:00` | ✅ PASS | ✅ PASS | ✅ COMPLETE |

### B. Internal Cadence & Gap Analysis (Inside Observed Span)
| Timeframe | Observed Count | Expected Cadence | Internal Gaps | Missing Intervals | Missing % | Largest Gap | Semantics | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 2399751 | 60s | 8645 | 966969 | 28.72% | 275340s | `OBSERVATIONAL_PROVIDER_CONTINUITY` | `EVALUATED` |
| **5m** | 482458 | 300s | 2098 | 190886 | 28.35% | 275100s | `OBSERVATIONAL_PROVIDER_CONTINUITY` | `EVALUATED` |
| **15m** | 161233 | 900s | 1495 | 63215 | 28.16% | 274500s | `OBSERVATIONAL_PROVIDER_CONTINUITY` | `EVALUATED` |
| **1h** | 40407 | 3600s | 1360 | 15705 | 27.99% | 273600s | `OBSERVATIONAL_PROVIDER_CONTINUITY` | `EVALUATED` |
| **4h** | 10647 | 14400s | 314 | 3415 | 24.34% | 259200s | `OBSERVATIONAL_PROVIDER_CONTINUITY` | `EVALUATED` |
| **1d** | 1816 | 86400s | 282 | 522 | 22.33% | 259200s | `OBSERVATIONAL_PROVIDER_CONTINUITY` | `EVALUATED` |

---

## 4. Volume Evidence Distribution

- `REAL_VOLUME`: 0
- `TICK_VOLUME`: 0
- `PROXY_VOLUME`: 0
- `UNAVAILABLE`: 3096312
- **Classification:** `UNAVAILABLE`

---

## 5. Dataset Provenance & Fingerprints

- **Audit Code Revision:** `a9922dfbe3df001e2403b334b0b2dad3957590f5`
- **Data Acquisition Code Revision:** `c5c521c03c1f4e18da8f2dd7039af998d599d78c`
- **Phase 6 15m Dataset Fingerprint:** `2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9`
- **Readiness Evidence Fingerprint (6-TF):** `d5d8f7a20cf820f177ccafb99d60d09cf503e5a80eee95a89bc7cf02334764b9`
- **Readiness Fingerprint Reproducible:** `PASS`

---

## 6. Governance Determination
- **Production Authority:** `FALSE` (Enforced)
- **Published Decision:** `WAIT` (Enforced)
- **Phase 8 Status:** `HOLD` (Enforced)
- **Calibration Status:** `CALIBRATION_DATA_NOT_READY`
- **Overall Historical Coverage:** `COMPLETE`
- **Final Decision:** `CANDLES_READY_MACRO_MISSING`
