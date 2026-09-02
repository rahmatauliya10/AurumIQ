# AurumIQ — XAUUSD Data Readiness Audit Report

> **Governance Level:** Evidence-Driven Empirical Calibration Campaign (Post Phase 7 / Pre Phase 8)  
> **Authoritative Baseline SHA:** `57f6de1405d0df8548182a166d245f1a3173363d`  
> **Code Revision:** `HEAD`  
> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)  
> **Authoritative Analytical Market Source:** `ListingRole.PRIMARY_XAUUSD_SPOT`  
> **Audit Date:** 2026-09-02  
> **Audit Status:** ❌ **CALIBRATION_DATA_NOT_READY**

---

## 1. Executive Summary

In accordance with Sections 0, 4, 5, and 6 of the AurumIQ XAUUSD Empirical Calibration Campaign Protocol, a complete point-in-time audit of all persisted market data, provider listings, and auxiliary evidence was conducted prior to parameter optimization or search.

**Findings:**
1. **Total Persisted Candles:** 0 across all standard analytical and intrabar timeframes.
2. **15m Usable Feature Warm-Up:** 0 / 20 required bars (FAIL).
3. **Volume Evidence Classification:** `UNAVAILABLE`.
4. **Data Contamination:** 0 foreign or cross-asset records detected.
5. **Auxiliary Coverage:** Macro events: 0, Historical Quotes: 0.
6. **Empirical Friction:** `EMPIRICAL_FRICTION_NOT_CONFIGURED`.

**Hard Data-Readiness Gate Result:**
Decision: **`CALIBRATION_DATA_NOT_READY`**

### Decision Rationale & Missing Dependencies:
- Zero historical spot XAUUSD candles persisted in the authoritative primary dataset.
- Insufficient 15m feature warm-up bars (0/20 bars required).

---

## 2. Market Listing & Provider Attribution

| Field | Value | Governance Requirement | Compliance |
| :--- | :--- | :--- | :--- |
| **Instrument** | `XAU/USD` | Spot Gold denominated in USD | ✅ PASS |
| **Listing Role** | `ListingRole.PRIMARY_XAUUSD_SPOT` | Primary analytical source | ✅ PASS |
| **Provider ID** | `xauusd_primary` | Spot provider registry binding | ✅ PASS |
| **Provider Symbol** | `XAUUSD` | Explicit spot symbol | ✅ PASS |
| **Listing Status** | `ListingStatus.ACTIVE` | Active provider registry | ✅ PASS |

---

## 3. Timeframe Breakdown

| Timeframe | Candle Count | Missing Intervals | Naive Timestamps | Invalid OHLC | Contamination |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 0 | N/A | 0 | 0 | 0 |
| **5m** | 0 | N/A | 0 | 0 | 0 |
| **15m** | 0 | N/A | 0 | 0 | 0 |
| **1h** | 0 | N/A | 0 | 0 | 0 |
| **4h** | 0 | N/A | 0 | 0 | 0 |
| **1d** | 0 | N/A | 0 | 0 | 0 |

---

## 4. Volume Evidence Distribution

- `REAL_VOLUME`: 0
- `TICK_VOLUME`: 0
- `PROXY_VOLUME`: 0
- `UNAVAILABLE`: 0
- **Classification:** `UNAVAILABLE`

---

## 5. Dataset Fingerprint
- **Canonical Dataset SHA-256:** `15537f598a70da5968b53a3d52c3a4aa090f9aba11e190c43ea06509882bdba0`

---

## 6. Governance Determination
- **Production Authority:** `FALSE` (Enforced)
- **Published Decision:** `WAIT` (Enforced)
- **Phase 8 Status:** `HOLD` (Enforced)
- **Final Decision:** `CALIBRATION_DATA_NOT_READY`
