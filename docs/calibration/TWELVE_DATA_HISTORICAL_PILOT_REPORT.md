# AurumIQ — Twelve Data Historical Backfill Pilot Report

> **Governance Standard:** Evidence-Driven Empirical Calibration Campaign (Data Pilot Phase)  
> **Target Instrument:** Canonical Spot Gold (`XAU/USD`), Base `XAU`, Quote `USD`  
> **Authoritative Analytical Provider:** `twelve_data_xauusd` (`XAU/USD`)  
> **Listing Role:** `PRIMARY_XAUUSD_SPOT` (`ACTIVE`, Priority `0`)  
> **Date:** 2026-09-02  
> **Pilot Window:** `2026-06-01T00:00:00Z` to `2026-09-01T00:00:00Z` (92 Calendar Days, Exclusive End)  
> **Status:** ✅ **TWELVE_DATA_XAUUSD_HISTORICAL_PILOT_SEALED**  
> **Calibration Readiness Decision:** ❌ **CALIBRATION_DATA_NOT_READY** (Historical Coverage Incomplete)

---

## 1. Executive Summary

In accordance with the Twelve Data Historical Backfill Pilot protocol, AurumIQ has bound `twelve_data_xauusd` as its authoritative primary analytical candlestick provider for spot gold (`XAU/USD`), implemented safe backward pagination (`outputsize=4900`), and executed a controlled historical backfill pilot covering `2026-06-01T00:00:00Z` → `2026-09-01T00:00:00Z` (92 calendar days) across all six analytical and execution timeframes (`1d, 4h, 1h, 15m, 5m, 1m`).

All 170,660 pilot candles were acquired within the historical request budget, persisted atomically, and verified with zero defects:
- 0 duplicate records
- 0 naive timestamps
- 0 invalid OHLC records
- 0 non-positive prices
- 0 non-closed persisted bars
- 0 cross-source/asset contamination

---

## 2. Authoritative Provider Binding

| Attribute | Configured Value | Verification Status |
| :--- | :--- | :--- |
| **Instrument Symbol** | `XAU/USD` (Canonical Spot Gold) | ✅ Seeded & Verified |
| **Active Primary Provider** | `twelve_data_xauusd` | ✅ `PRIMARY_XAUUSD_SPOT`, Priority 0 |
| **Provider Symbol** | `XAU/USD` | ✅ Exact Spot Gold Match |
| **Listing Status** | `ACTIVE` | ✅ Active in DB (Single Primary Enforced) |
| **Legacy Primary Listing** | `xauusd_primary` | ✅ Demoted to `GENERIC`, `HALTED`, Priority 99 |
| **Secondary Listing** | `xauusd_secondary` | ✅ Preserved as `SECONDARY_XAUUSD_SPOT`, `HALTED` |

---

## 3. Backfill Pilot Invariants & Request Accounting

- **Pilot Calendar Duration:** 92 calendar days (`2026-06-01T00:00:00Z` to `2026-09-01T00:00:00Z` exclusive end).
- **Pagination Scheme:** Monotonic backward pagination anchored at `end_date`, querying up to `outputsize=4900` closed bars per page.
- **Request Pacing:** Strict $\ge 8.0$ seconds between requests.
- **Minimum Unique Data Page Requests:** **39**
  - `1m`: $\lceil 132,480 / 4899 \rceil = 28$ pages
  - `5m`: $\lceil 26,496 / 4899 \rceil = 6$ pages
  - `15m`: $\lceil 8,832 / 4899 \rceil = 2$ pages
  - `1h`: 1 page (2,208 candles)
  - `4h`: 1 page (552 candles)
  - `1d`: 1 page (92 candles)
  - **Total Minimum Unique Historical Data Pages:** 39
- **Actual Cumulative Campaign Requests:** `UNRESOLVED_ACROSS_RESUMED_INVOCATIONS`  
  *(Historical pilot executed across multiple monitored resume phases due to transient upstream network/socket drops before the 45s timeout and auto-retry patch; estimated ~44 pilot queries + 7 idempotency re-run queries).*
- **Daily Credit Guard:** Implemented fail-closed `--daily-credit-ceiling 700` (reserves 100 safe credits from Basic Free 800/day). Queries `/api_usage` at startup to cap `effective_request_budget = min(--max-api-requests, ceiling - daily_usage)`.

---

## 4. Persisted Pilot Dataset Breakdown

| Timeframe | Candle Count | Earliest Timestamp (UTC) | Latest Timestamp (UTC) | Duration Covered | Contamination Count |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | 132,480 | `2026-06-01T00:00:00+00:00` | `2026-08-31T23:59:00+00:00` | 92 Calendar Days | 0 |
| **5m** | 26,496 | `2026-06-01T00:00:00+00:00` | `2026-08-31T23:55:00+00:00` | 92 Calendar Days | 0 |
| **15m** | 8,832 | `2026-06-01T00:00:00+00:00` | `2026-08-31T23:45:00+00:00` | 92 Calendar Days | 0 |
| **1h** | 2,208 | `2026-06-01T00:00:00+00:00` | `2026-08-31T23:00:00+00:00` | 92 Calendar Days | 0 |
| **4h** | 552 | `2026-06-01T01:00:00+00:00` | `2026-08-31T21:00:00+00:00` | 92 Calendar Days | 0 |
| **1d** | 92 | `2026-06-01T00:00:00+00:00` | `2026-08-31T00:00:00+00:00` | 92 Calendar Days | 0 |
| **TOTAL** | **170,660** | `2026-06-01T00:00:00+00:00` | `2026-08-31T23:59:00+00:00` | **92 Calendar Days** | **0** |

---

## 5. Data Quality, Volume & Idempotency Audits

1. **Duplicate Records:** `0` duplicate composite keys `(instrument, source, timeframe, timestamp_open)`.
2. **Timezone Awareness:** `0` naive timestamps; 100% strict UTC-aware datetimes.
3. **OHLC Geometry:** `0` violations ($\text{Low} \le \text{Open} \le \text{High}$, $\text{Low} \le \text{Close} \le \text{High}$).
4. **Volume Stored Value:** `volume = Decimal('0')` across all 170,660 candles.
5. **Volume Classification:** `volume_evidence = UNAVAILABLE` (100% of dataset). Twelve Data XAU/USD feed does not provide validated tick or real volume; stored strictly as 0 with `UNAVAILABLE` evidence.
6. **Quote Rate & Currency Alignment:** `quote_rate = 1.000000`, `close_usd = close` across all 170,660 records.
7. **Idempotency Re-Run Test:** Re-ran backfill on slice `2026-08-31T00:00:00Z` to `2026-09-01T00:00:00Z` across all 6 timeframes. Produced 0 errors, 0 duplicate keys, and database record counts remained strictly invariant at 170,660.

---

## 6. Weekend & Session Semantics Audit

- **Provider Bar Continuity:** Continuous 1,440 1-minute bars per calendar day ($132,480 / 92 = 1,440$).
- **Weekend Provider Data:** **OBSERVED**.
  - Saturday sample (`2026-06-06`): 1,440 bars, 0 continuity gaps, tight price range (0.27 USD), 0 flat candles.
  - Sunday sample (`2026-06-07`): 1,440 bars, 0 continuity gaps, price range (37.21 USD), 0 flat candles.
  - Weekday sample (`2026-06-10`): 1,440 bars, 0 continuity gaps, active price range (204.48 USD), 0 flat candles.
- **Provider vs Execution Venue Separation:**
  - **Twelve Data:** Authoritative analytical composite / reference source.
  - **Exness:** Manual execution venue operating standard OTC metals session hours (closed weekends).
- **Session Policy:**  
  `EXECUTION_SESSION_POLICY = PENDING_PRE_CALIBRATION_REVIEW`  
  *(Must be explicitly resolved before any production execution simulation or performance claims. Calibration remains fail-closed anyway).*

---

## 7. Dataset Cryptographic Fingerprints & Provenance

- **Phase 6 15m Dataset Fingerprint:**  
  `d5a97bfed92a97fe96bcb2ec1b9d02fd20f027ad8d17ed9f5b9c824a525fe749`
- **Readiness Evidence Fingerprint (6-TF):**  
  `0dcacedf50dd818499bd7a23af159b96153da14122ee6e29897cab89d3efed56`
- **Audit Code Revision:** `da09745d2160a065ebde6716c3b0c10052b7756b`
- **Data Acquisition Code Revision:** `UNRESOLVED_PRECOMMIT_WORKTREE`
- **Fingerprint Reproducibility:** ✅ **PASS** (Re-evaluated on local database, producing the exact identical hash across 170,660 candles).

---

## 8. Calibration Readiness Gate Evaluation

- **Warm-Up Feature Satisfaction:** `8,832 / 20` required 15m bars (PASS).
- **Dataset Purity & Quality:** PASS (0 errors, 0 duplicates, 0 contamination).
- **Historical Full Coverage:** **INCOMPLETE** (`2026-06-01` to `2026-09-01` covers 92 calendar days; required full calibration window is `2020-04-07` to `2026-09-01`).
- **Gate Decision:** **`CALIBRATION_DATA_NOT_READY`** (Fail-Closed).
- **Calibration Engine / Optimizer:** **REMAINS STRICTLY BLOCKED**.

---

## 9. Governance Invariants & System Status

- **Primary Provider:** `twelve_data_xauusd` (`XAU/USD`)
- **Active Primary Count:** `1`
- **Production Authority:** `is_production_authorized = False` (Strictly Enforced).
- **Published Live Monitor Decision:** `WAIT` (Enforced).
- **Automatic Trading:** `ABSENT`.
- **MT5 Bridge:** `PARKED`.
- **Telegram:** `OUT_OF_SCOPE`.
- **Phase 8 (Signal / Trading Authority):** `HOLD`.
- **Cloud Deployment (Neon / Render):** `UNTOUCHED`.
- **Branch:** `research/xauusd-data-readiness` (Unmerged).
