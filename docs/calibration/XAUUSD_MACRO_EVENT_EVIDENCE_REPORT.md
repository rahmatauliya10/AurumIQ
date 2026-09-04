# XAUUSD Macroeconomic Event Evidence Audit Report (Checkpoint B Remediation)

**Generated At:** 2026-09-04T11:41:55.283120+00:00
**Calibration Window:** 2020-04-07T00:00:00Z to 2026-09-01T00:00:00Z
**Governance State:** FAIL-CLOSED

---

## 1. Executive Summary & Gate Decision
* **Decision:** `CANDLES_READY_EMPIRICAL_FRICTION_MISSING`
* **Passed:** `False`
* **Production Authority:** `is_production_authorized = False`
* **Phase 3B Production Weight:** `0.0`
* **Published Decision:** `WAIT`
* **Fingerprint Invariance:**
  * Total Candles: `3,096,312`
  * Phase-6 15m Fingerprint: `2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9`
  * Readiness 6-TF Fingerprint: `d5d8f7a20cf820f177ccafb99d60d09cf503e5a80eee95a89bc7cf02334764b9`
  * Macro Evidence Fingerprint: `08581dfc56575ec98d48d9d005218657204a5fe7d4c4cb9453598e40d7d63f3a`

---

## 2. Separate Coverage Dimensions

### A. Event Lifecycle Coverage
| Family | Expected Lifecycles | Complete | Incomplete | Status |
| :--- | :---: | :---: | :---: | :---: |
| **FOMC_RATE** | 51 | 51 | 0 | COMPLETE |
| **US_NFP** | 76 | 76 | 0 | COMPLETE |
| **US_CPI** | 77 | 77 | 0 | COMPLETE |
| **TOTAL** | **204** | **204** | **0** | **COMPLETE** |

### B. Schedule Coverage
| Family | Scheduled | Rescheduled | Cancelled | Unknown known_at | Complete |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **FOMC_RATE** | 51 | 0 | 0 | 0 | True |
| **US_NFP** | 76 | 0 | 1 | 0 | True |
| **US_CPI** | 77 | 0 | 1 | 0 | True |

### C. Observation Coverage
| Family | Published | Late/Bundled | Officially Not Published | Missing Unexplained | Invalid | Complete |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **FOMC_RATE** | 51 | 0 | 0 | 0 | 0 | True |
| **US_NFP** | 75 | 1 | 0 | 0 | 0 | True |
| **US_CPI** | 76 | 0 | 1 | 0 | 0 | True |

### D. Provenance Coverage
* **Unknown known_at count:** `0`
* **Provenance incomplete count:** `0`
* **Duplicates:** `0`
* **Unexpected extras:** `0`

---

## 3. 2025 Shutdown Lifecycle Chronology

### US_CPI_2025_10
* **Original Schedule:** 2025-11-13T13:30:00+00:00 (known at: 2024-12-01T00:00:00+00:00)
* **Cancellation:** CANCELLED known at 2025-11-20T13:30:00+00:00
* **Observation Status:** `OFFICIALLY_NOT_PUBLISHED` (reason: `2025_LAPSE_IN_APPROPRIATIONS`)
* **Numeric Observation:** `None` (strictly None; no synthetic data)
* **Authoritative Source:** `https://www.bls.gov/news.release/archives/cpi_12182025.htm`
* **Source SHA-256:** `85627e2f11db4d58fbdebc5877b27e71022c0ef17db77ddfb04497c4e33105b5`

### US_NFP_2025_10
* **Original Schedule:** 2025-11-07T13:30:00+00:00 (known at: 2024-12-01T00:00:00+00:00)
* **Cancellation:** CANCELLED known at 2025-11-20T13:30:00+00:00
* **Observation Status:** `PUBLISHED_LATE_OR_BUNDLED`
* **Publication Timestamp:** `2025-12-16T13:30:00+00:00`
* **Observed Value:** `-105K` (Level: `159488.0000K`, Change: `-105.0000K`)
* **Authoritative Source:** `https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS&vintage_date=2025-12-16`
* **Source SHA-256:** `c449c146f4e381de6199f27ccd9a742a4a3210e924f936333f74af68885b1281`

---

## 4. Ingestion Execution Statistics
```json
{
  "source_snapshots_inserted": 0,
  "identities_inserted": 0,
  "schedule_vintages_inserted": 0,
  "observations_inserted": 0,
  "revisions_inserted": 0,
  "idempotent_skips": 642,
  "duplicates": 0,
  "conflicts": 0,
  "quarantined": 0,
  "rejected": 0,
  "invalid_timestamp_records": 0,
  "missing_provenance_records": 0
}
```
