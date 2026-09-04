# XAUUSD Macroeconomic Event Evidence Audit Report (Checkpoint B Remediation)

**Generated At:** 2026-09-04T15:32:38.410607+00:00
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
  * Macro Evidence Fingerprint: `41a9416023fbbfb35288d485d5f351155e979efb0f27c3751f51d09f5edca680`

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
| **US_NFP** | 152 | 0 | 1 | 0 | True |
| **US_CPI** | 154 | 0 | 2 | 0 | True |

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

## 3. BLS Schedule Provenance Reconciliation (Rebuilt from Database)
| Provenance Type | Count | Proportion | Status |
| :--- | :---: | :---: | :---: |
| **BLS_PREVIOUS_RELEASE_ANNOUNCEMENT** | 153 | 49.5% | VALIDATED |
| **OMB_PFEI_SCHEDULE** | 0 | 0.0% | VALIDATED |
| **OTHER_FIRST_PARTY** | 2 | 0.6% | VALIDATED |
| **UNKNOWN** | 154 | 49.8% | DEFECT |
| **TOTAL BLS SCHEDULES** | **309** | **100.0%** | **EXACT RECONCILIATION** |

### Sample Provenance Records
* **CPI Previous Release Announcement Sample:**
```json
[
  {
    "reference_period": "2026-07",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/cpi_07142026.htm",
    "known_at": "2026-07-14 12:30:00+00:00"
  },
  {
    "reference_period": "2026-06",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/cpi_06102026.htm",
    "known_at": "2026-06-10 12:30:00+00:00"
  },
  {
    "reference_period": "2026-05",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/cpi_05122026.htm",
    "known_at": "2026-05-12 12:30:00+00:00"
  }
]
```
* **NFP Previous Release Announcement Sample:**
```json
[
  {
    "reference_period": "2026-07",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/empsit_07022026.htm",
    "known_at": "2026-07-02 12:30:00+00:00"
  },
  {
    "reference_period": "2026-06",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/empsit_06052026.htm",
    "known_at": "2026-06-05 12:30:00+00:00"
  },
  {
    "reference_period": "2026-05",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/empsit_05082026.htm",
    "known_at": "2026-05-08 12:30:00+00:00"
  }
]
```
* **Other First Party Sample (2025 Shutdown):**
```json
[
  {
    "event_id": "US_CPI",
    "reference_period": "2025-10",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/cpi_12182025.htm",
    "known_at": "2025-12-18 13:30:00+00:00"
  },
  {
    "event_id": "US_NFP",
    "reference_period": "2025-10",
    "announcing_release_url": "https://www.bls.gov/news.release/archives/empsit_11202025.htm",
    "known_at": "2025-11-20 13:30:00+00:00"
  }
]
```
* **Unknown Records:**
```json
[
  [
    "US_NFP",
    "2026-07"
  ],
  [
    "US_NFP",
    "2026-06"
  ],
  [
    "US_NFP",
    "2026-05"
  ],
  [
    "US_NFP",
    "2026-04"
  ],
  [
    "US_NFP",
    "2026-03"
  ],
  [
    "US_NFP",
    "2026-02"
  ],
  [
    "US_NFP",
    "2026-01"
  ],
  [
    "US_NFP",
    "2025-12"
  ],
  [
    "US_CPI",
    "2026-07"
  ],
  [
    "US_CPI",
    "2026-06"
  ],
  [
    "US_CPI",
    "2026-05"
  ],
  [
    "US_CPI",
    "2026-04"
  ],
  [
    "US_CPI",
    "2026-03"
  ],
  [
    "US_CPI",
    "2026-02"
  ],
  [
    "US_CPI",
    "2026-01"
  ],
  [
    "US_CPI",
    "2025-12"
  ],
  [
    "US_CPI",
    "2025-10"
  ],
  [
    "US_NFP",
    "2025-11"
  ],
  [
    "US_NFP",
    "2025-10"
  ],
  [
    "US_NFP",
    "2025-09"
  ],
  [
    "US_NFP",
    "2025-08"
  ],
  [
    "US_NFP",
    "2025-07"
  ],
  [
    "US_NFP",
    "2025-06"
  ],
  [
    "US_NFP",
    "2025-05"
  ],
  [
    "US_NFP",
    "2025-04"
  ],
  [
    "US_NFP",
    "2025-03"
  ],
  [
    "US_NFP",
    "2025-02"
  ],
  [
    "US_NFP",
    "2025-01"
  ],
  [
    "US_NFP",
    "2024-12"
  ],
  [
    "US_CPI",
    "2025-11"
  ],
  [
    "US_CPI",
    "2025-10"
  ],
  [
    "US_CPI",
    "2025-09"
  ],
  [
    "US_CPI",
    "2025-08"
  ],
  [
    "US_CPI",
    "2025-07"
  ],
  [
    "US_CPI",
    "2025-06"
  ],
  [
    "US_CPI",
    "2025-05"
  ],
  [
    "US_CPI",
    "2025-04"
  ],
  [
    "US_CPI",
    "2025-03"
  ],
  [
    "US_CPI",
    "2025-02"
  ],
  [
    "US_CPI",
    "2025-01"
  ],
  [
    "US_CPI",
    "2024-12"
  ],
  [
    "US_NFP",
    "2024-11"
  ],
  [
    "US_NFP",
    "2024-10"
  ],
  [
    "US_NFP",
    "2024-09"
  ],
  [
    "US_NFP",
    "2024-08"
  ],
  [
    "US_NFP",
    "2024-07"
  ],
  [
    "US_NFP",
    "2024-06"
  ],
  [
    "US_NFP",
    "2024-05"
  ],
  [
    "US_NFP",
    "2024-04"
  ],
  [
    "US_NFP",
    "2024-03"
  ],
  [
    "US_NFP",
    "2024-02"
  ],
  [
    "US_NFP",
    "2024-01"
  ],
  [
    "US_NFP",
    "2023-12"
  ],
  [
    "US_CPI",
    "2024-11"
  ],
  [
    "US_CPI",
    "2024-10"
  ],
  [
    "US_CPI",
    "2024-09"
  ],
  [
    "US_CPI",
    "2024-08"
  ],
  [
    "US_CPI",
    "2024-07"
  ],
  [
    "US_CPI",
    "2024-06"
  ],
  [
    "US_CPI",
    "2024-05"
  ],
  [
    "US_CPI",
    "2024-04"
  ],
  [
    "US_CPI",
    "2024-03"
  ],
  [
    "US_CPI",
    "2024-02"
  ],
  [
    "US_CPI",
    "2024-01"
  ],
  [
    "US_CPI",
    "2023-12"
  ],
  [
    "US_NFP",
    "2023-11"
  ],
  [
    "US_NFP",
    "2023-10"
  ],
  [
    "US_NFP",
    "2023-09"
  ],
  [
    "US_NFP",
    "2023-08"
  ],
  [
    "US_NFP",
    "2023-07"
  ],
  [
    "US_NFP",
    "2023-06"
  ],
  [
    "US_NFP",
    "2023-05"
  ],
  [
    "US_NFP",
    "2023-04"
  ],
  [
    "US_NFP",
    "2023-03"
  ],
  [
    "US_NFP",
    "2023-02"
  ],
  [
    "US_NFP",
    "2023-01"
  ],
  [
    "US_NFP",
    "2022-12"
  ],
  [
    "US_CPI",
    "2023-11"
  ],
  [
    "US_CPI",
    "2023-10"
  ],
  [
    "US_CPI",
    "2023-09"
  ],
  [
    "US_CPI",
    "2023-08"
  ],
  [
    "US_CPI",
    "2023-07"
  ],
  [
    "US_CPI",
    "2023-06"
  ],
  [
    "US_CPI",
    "2023-05"
  ],
  [
    "US_CPI",
    "2023-04"
  ],
  [
    "US_CPI",
    "2023-03"
  ],
  [
    "US_CPI",
    "2023-02"
  ],
  [
    "US_CPI",
    "2023-01"
  ],
  [
    "US_CPI",
    "2022-12"
  ],
  [
    "US_NFP",
    "2022-11"
  ],
  [
    "US_NFP",
    "2022-10"
  ],
  [
    "US_NFP",
    "2022-09"
  ],
  [
    "US_NFP",
    "2022-08"
  ],
  [
    "US_NFP",
    "2022-07"
  ],
  [
    "US_NFP",
    "2022-06"
  ],
  [
    "US_NFP",
    "2022-05"
  ],
  [
    "US_NFP",
    "2022-04"
  ],
  [
    "US_NFP",
    "2022-03"
  ],
  [
    "US_NFP",
    "2022-02"
  ],
  [
    "US_NFP",
    "2022-01"
  ],
  [
    "US_NFP",
    "2021-12"
  ],
  [
    "US_CPI",
    "2022-11"
  ],
  [
    "US_CPI",
    "2022-10"
  ],
  [
    "US_CPI",
    "2022-09"
  ],
  [
    "US_CPI",
    "2022-08"
  ],
  [
    "US_CPI",
    "2022-07"
  ],
  [
    "US_CPI",
    "2022-06"
  ],
  [
    "US_CPI",
    "2022-05"
  ],
  [
    "US_CPI",
    "2022-04"
  ],
  [
    "US_CPI",
    "2022-03"
  ],
  [
    "US_CPI",
    "2022-02"
  ],
  [
    "US_CPI",
    "2022-01"
  ],
  [
    "US_CPI",
    "2021-12"
  ],
  [
    "US_NFP",
    "2021-11"
  ],
  [
    "US_NFP",
    "2021-10"
  ],
  [
    "US_NFP",
    "2021-09"
  ],
  [
    "US_NFP",
    "2021-08"
  ],
  [
    "US_NFP",
    "2021-07"
  ],
  [
    "US_NFP",
    "2021-06"
  ],
  [
    "US_NFP",
    "2021-05"
  ],
  [
    "US_NFP",
    "2021-04"
  ],
  [
    "US_NFP",
    "2021-03"
  ],
  [
    "US_NFP",
    "2021-02"
  ],
  [
    "US_NFP",
    "2021-01"
  ],
  [
    "US_NFP",
    "2020-12"
  ],
  [
    "US_CPI",
    "2021-11"
  ],
  [
    "US_CPI",
    "2021-10"
  ],
  [
    "US_CPI",
    "2021-09"
  ],
  [
    "US_CPI",
    "2021-08"
  ],
  [
    "US_CPI",
    "2021-07"
  ],
  [
    "US_CPI",
    "2021-06"
  ],
  [
    "US_CPI",
    "2021-05"
  ],
  [
    "US_CPI",
    "2021-04"
  ],
  [
    "US_CPI",
    "2021-03"
  ],
  [
    "US_CPI",
    "2021-02"
  ],
  [
    "US_CPI",
    "2021-01"
  ],
  [
    "US_CPI",
    "2020-12"
  ],
  [
    "US_NFP",
    "2020-11"
  ],
  [
    "US_NFP",
    "2020-10"
  ],
  [
    "US_NFP",
    "2020-09"
  ],
  [
    "US_NFP",
    "2020-08"
  ],
  [
    "US_NFP",
    "2020-07"
  ],
  [
    "US_NFP",
    "2020-06"
  ],
  [
    "US_NFP",
    "2020-05"
  ],
  [
    "US_NFP",
    "2020-04"
  ],
  [
    "US_CPI",
    "2020-11"
  ],
  [
    "US_CPI",
    "2020-10"
  ],
  [
    "US_CPI",
    "2020-09"
  ],
  [
    "US_CPI",
    "2020-08"
  ],
  [
    "US_CPI",
    "2020-07"
  ],
  [
    "US_CPI",
    "2020-06"
  ],
  [
    "US_CPI",
    "2020-05"
  ],
  [
    "US_CPI",
    "2020-04"
  ],
  [
    "US_CPI",
    "2020-03"
  ]
]
```

---

## 4. 2025 Shutdown Lifecycle Chronology

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

## 5. Ingestion Execution Statistics
```json
{
  "source_snapshots_inserted": 0,
  "identities_inserted": 0,
  "schedule_vintages_inserted": 0,
  "observations_inserted": 0,
  "revisions_inserted": 0,
  "idempotent_skips": 795,
  "duplicates": 0,
  "conflicts": 0,
  "quarantined": 0,
  "rejected": 0,
  "invalid_timestamp_records": 0,
  "missing_provenance_records": 0
}
```
