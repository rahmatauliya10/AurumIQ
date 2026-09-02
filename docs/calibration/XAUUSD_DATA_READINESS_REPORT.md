# AurumIQ — XAUUSD Data Readiness Audit Report

> **Governance Level:** Evidence-Driven Empirical Calibration Campaign (Post Phase 7 / Pre Phase 8)  
> **Authoritative Baseline SHA:** `57f6de1405d0df8548182a166d245f1a3173363d`<br>
> **Code Revision:** `HEAD`<br>
> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)<br>
> **Authoritative Analytical Market Source:** `PRIMARY_XAUUSD_SPOT`<br>
> **Audit Date:** 2026-09-02<br>
> **Audit Status:** ❌ **CALIBRATION_DATA_NOT_READY**

---

## 1. Executive Summary

In accordance with Sections 0, 4, 5, and 6 of the AurumIQ XAUUSD Empirical Calibration Campaign Protocol, a complete point-in-time audit of all persisted market data, provider listings, and auxiliary evidence was conducted prior to parameter optimization or search.

**Findings:**
1. **Total Persisted Candles:** 170660 across all standard analytical and intrabar timeframes.
2. **15m Usable Feature Warm-Up:** 8832 / 20 required bars (PASS).
3. **Volume Evidence Classification:** `UNAVAILABLE`.
4. **Data Contamination:** 0 foreign or cross-asset records detected.
5. **Auxiliary Coverage:** Macro events: 0, Historical Quotes: 0.
6. **Empirical Friction:** `EMPIRICAL_FRICTION_NOT_CONFIGURED`.

**Hard Data-Readiness Gate Result:**
Decision: **`CALIBRATION_DATA_NOT_READY`**

### Decision Rationale & Missing Dependencies:
- HISTORICAL_COVERAGE_INCOMPLETE: Persisted dataset coverage [2026-06-01 to 2026-09-01] does not cover required full calibration window [2020-04-07 to 2026-09-01].

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

## 3. Timeframe Breakdown

| Timeframe | Candle Count | Missing Intervals | Naive Timestamps | Invalid OHLC | Contamination |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 132480 | N/A | 0 | 0 | 0 |
| **5m** | 26496 | N/A | 0 | 0 | 0 |
| **15m** | 8832 | N/A | 0 | 0 | 0 |
| **1h** | 2208 | N/A | 0 | 0 | 0 |
| **4h** | 552 | N/A | 0 | 0 | 0 |
| **1d** | 92 | N/A | 0 | 0 | 0 |

---

## 4. Volume Evidence Distribution

- `REAL_VOLUME`: 0
- `TICK_VOLUME`: 0
- `PROXY_VOLUME`: 0
- `UNAVAILABLE`: 170660
- **Classification:** `UNAVAILABLE`

---

## 5. Dataset Fingerprint
- **Canonical Dataset SHA-256:** `d5a97bfed92a97fe96bcb2ec1b9d02fd20f027ad8d17ed9f5b9c824a525fe749`

---

## 6. Governance Determination
- **Production Authority:** `FALSE` (Enforced)
- **Published Decision:** `WAIT` (Enforced)
- **Phase 8 Status:** `HOLD` (Enforced)
- **Final Decision:** `CALIBRATION_DATA_NOT_READY`
