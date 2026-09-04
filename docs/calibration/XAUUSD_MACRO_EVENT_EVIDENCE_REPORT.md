# XAUUSD Macroeconomic Event Evidence Audit Report (Checkpoint B)

**Generated At:** 2026-09-04T11:07:41.377493+00:00
**Calibration Window:** 2020-04-07T00:00:00Z to 2026-09-01T00:00:00Z
**Governance State:** FAIL-CLOSED

---

## 1. Executive Summary & Gate Decision
* **Decision:** `CANDLES_READY_MACRO_MISSING`
* **Passed:** `False`
* **Production Authority:** `is_production_authorized = False`
* **Phase 3B Production Weight:** `0.0`
* **Fingerprint Invariance:**
  * Total Candles: `3,096,312`
  * Phase-6 15m Fingerprint: `2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9`
  * Readiness 6-TF Fingerprint: `d5d8f7a20cf820f177ccafb99d60d09cf503e5a80eee95a89bc7cf02334764b9`
  * Macro Evidence Fingerprint: `88dd8ecb30d4dc3bcbb2a8018072c7c172b55248d814ae656027515db0bf0725`

---

## 2. Canonical Coverage Reconciliation
| Macro Family | Expected | Observed | Matched | Missing | Duplicates | Invalid | Coverage % | Complete |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **FOMC_RATE** | 51 | 51 | 51 | 0 | 0 | 0 | 100.00% | True |
| **US_CPI** | 77 | 76 | 76 | 1 | 0 | 0 | 98.70% | False |
| **US_NFP** | 76 | 75 | 75 | 1 | 0 | 0 | 98.68% | False |
| **TOTAL** | **204** | **202** | **202** | **2** | **0** | **0** | **99.02%** | **False** |

---

## 3. Ingestion Execution Statistics
```json
{
  "source_snapshots_inserted": 0,
  "identities_inserted": 0,
  "schedule_vintages_inserted": 0,
  "observations_inserted": 0,
  "revisions_inserted": 0,
  "idempotent_skips": 631,
  "duplicates": 0,
  "conflicts": 0,
  "quarantined": 0,
  "rejected": 0,
  "invalid_timestamp_records": 0,
  "missing_provenance_records": 2
}
```

---

## 4. Provenance & Fail-Closed Justification
* **FOMC_RATE**: 51/51 decisions matched against official Federal Reserve Board annual calendar announcements (`monetary20190517a.htm` - `monetary20240809a.htm`) and policy statements.
* **US_CPI**: 76/77 observations matched from ALFRED point-in-time vintages. Reference period `2025-10` is missing from the BLS schedule.
* **US_NFP**: 75/76 observations matched from ALFRED point-in-time vintages. Reference period `2025-10` is missing from the BLS schedule.
* **Governance Assessment**: Under non-negotiable governance, missing canonical keys cannot be synthetically manufactured. The gate remains locked on `CANDLES_READY_MACRO_MISSING`.
