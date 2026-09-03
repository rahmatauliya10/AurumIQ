# AurumIQ — Twelve Data XAU/USD Live Provider Qualification Report

> **Document Class:** Provider Qualification & Analytical Feed Audit  
> **Authoritative Target:** `XAUUSD` (Spot Gold denominated in USD)  
> **Provider Identity:** `twelve_data_xauusd`  
> **Provider Symbol:** `XAU/USD`  
> **Baseline Commit:** `6a6bb16de112ba88cd6e0a80e11fe51074e2018d`  
> **Seal Micro-Patch Commit:** `research/xauusd-data-readiness`  
> **Qualification Date:** September 2026  
> **Execution Status:** Complete — Technical Qualification Sealed  
> **Final Qualification Status:** `TWELVE_DATA_XAUUSD_PRIMARY_USABLE`  
> *(Strictly defined as: Primary Analytical Candlestick Feed Usable. Execution quotes and live bid/ask remain NOT_CONFIGURED).*

---

## 1. Executive Summary

In strict accordance with the AurumIQ XAU/USD Data Readiness Protocol, Twelve Data has been empirically qualified via authenticated live HTTPS probes as the primary analytical market data provider for `XAUUSD`.

The provider satisfies all core analytical requirements for automated market monitoring, indicator calculation, and multi-timeframe regime analysis without requiring paid infrastructure.

```
+-----------------------------------------------------------------------------------+
|                           AURUMIQ DATA ARCHITECTURE                               |
|                                                                                   |
|  [ Twelve Data HTTPS API ] ---> Analytical Feed (1m, 5m, 15m, 1h, 4h, 1d)          |
|                                 - Spot Gold (XAU/USD)                             |
|                                 - Direct Decimal(str) Parsing (Float Prohibited)  |
|                                 - UTC-Normalized Datetime (Explicit timezone=UTC) |
|                                 - Closed 15m Signal Filtering                     |
|                                                                                   |
|  [ Exness MT5 ]            ---> Execution Venue (Manual Execution Only)           |
|                                 - Distinct Broker Liquidity & Spreads             |
|                                 - Status: PARKED                                  |
|                                                                                   |
|  [ Governance Gates ]      ---> CALIBRATION_DATA_NOT_READY (Preserved)             |
|                                 - Persisted Historical Candles: 0                 |
|                                 - Production Authority: FALSE                     |
|                                 - Published Decision: WAIT                        |
|                                 - Phase 8: HOLD                                   |
+-----------------------------------------------------------------------------------+
```

---

## 2. Source Semantics & Strict Decoupling

Per Section 10 and 11 of the protocol:
1. **Analytical Data Source:** Twelve Data XAU/USD serves strictly as the analytical and signal intelligence price-action reference.
2. **Execution Venue:** Exness is the target execution venue for manual trader execution.
3. **Prohibition on False Identity:** Twelve Data prices must **never** be represented as:
   - Exness executable bid/ask quotes
   - Exness live spreads
   - Exness slippage evidence
   - Exness execution settlement prices
4. **Execution Evidence Fields:**
   - `BID/ASK`: `NOT_AVAILABLE`
   - `SPREAD_EVIDENCE`: `NOT_CONFIGURED`
   - `ENTRY_SLIPPAGE`: `NOT_CONFIGURED`
   - `EXIT_SLIPPAGE`: `NOT_CONFIGURED`
   - `FEE_EVIDENCE`: `NOT_CONFIGURED`
5. **Live Monitoring Integration Scope:**
   - `ANALYTICAL_CANDLE_SOURCE`: `USABLE`
   - `LIVE_BID_ASK_SOURCE`: `NOT_CONFIGURED`
   - `PHASE7_QUOTE_LIVEMONITOR`: `NOT_YET_BOUND_TO_TWELVE_DATA`

---

## 3. Empirical Live Access & Timeframe Evidence (Explicit UTC Re-Probe)

All probes were executed against the official Twelve Data HTTPS endpoint (`https://api.twelvedata.com`) with explicit parameter `timezone=UTC` and pacing adhering to the Basic Free tier rate limit (8 requests/minute):

| Timeframe | Provider Interval | HTTP Status | Returned Symbol | Returned Interval | Raw Latest Datetime | Normalized UTC Datetime | Future Sanity Check | OHLC Geometry | Volume Presence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`1m`** | `1min` | 200 OK | `XAU/USD` | `1min` | `2026-09-02 15:54:00` | `2026-09-02 15:54:00+00:00` | **PASS** (Not in future) | **PASS** | `ABSENT` |
| **`5m`** | `5min` | 200 OK | `XAU/USD` | `5min` | `2026-09-02 15:50:00` | `2026-09-02 15:50:00+00:00` | **PASS** (Not in future) | **PASS** | `ABSENT` |
| **`15m`** | `15min` | 200 OK | `XAU/USD` | `15min` | `2026-09-02 15:45:00` | `2026-09-02 15:45:00+00:00` | **PASS** (Not in future) | **PASS** | `ABSENT` |
| **`1h`** | `1h` | 200 OK | `XAU/USD` | `1h` | `2026-09-02 15:00:00` | `2026-09-02 15:00:00+00:00` | **PASS** (Not in future) | **PASS** | `ABSENT` |
| **`4h`** | `4h` | 200 OK | `XAU/USD` | `4h` | `2026-09-02 13:00:00` | `2026-09-02 13:00:00+00:00` | **PASS** (Not in future) | **PASS** | `ABSENT` |
| **`1d`** | `1day` | 200 OK | `XAU/USD` | `1day` | `2026-09-02` | `2026-09-02 00:00:00+00:00` | **PASS** (Current date) | **PASS** | `ABSENT` |

---

## 4. History Capability Assessment & Timezone Clarification

The official `/earliest_timestamp` endpoint was re-probed with explicit parameter `timezone=UTC`:

| Timeframe | Raw Provider Timestamp | Unix Epoch Seconds | Normalized UTC Timestamp | Timezone Semantics |
| :--- | :--- | :--- | :--- | :--- |
| **`1m`** | `2020-04-06 06:40:00` | `1586155200` | `2020-04-06 06:40:00+00:00` | **Explicit UTC** (Matches unix epoch exactly) |
| **`5m`** | `2020-03-16 01:10:00` | `1584321000` | `2020-03-16 01:10:00+00:00` | **Explicit UTC** (Matches unix epoch exactly) |
| **`15m`** | `2020-01-24 02:00:00` | `1579831200` | `2020-01-24 02:00:00+00:00` | **Explicit UTC** (Matches unix epoch exactly) |
| **`1h`** | `2020-01-24 02:00:00` | `1579831200` | `2020-01-24 02:00:00+00:00` | **Explicit UTC** (Matches unix epoch exactly) |
| **`4h`** | `2020-01-24 00:00:00` | `1579824000` | `2020-01-24 00:00:00+00:00` | **Explicit UTC** (Matches unix epoch exactly) |
| **`1d`** | `1979-12-26` | `315000000` | `1979-12-26` (Date-Only) | **Date-Only** (No intraday session anchor implied) |

**Note on Daily Bars:** For `1day`, Twelve Data delivers date-only values (`YYYY-MM-DD`). AurumIQ normalizes daily storage to midnight UTC (`00:00:00+00:00`), documented as canonical platform normalization rather than provider intraday session open evidence.

**Prohibition on Bulk Downloading:** No bulk backfill was initiated during qualification; historical records persisted into the database remain exactly `0`.

---

## 5. Two Completed Market Days — 1min Quality Audit

A continuous evaluation was conducted on two full completed market days (Monday 2026-08-31 00:00:00 UTC through Tuesday 2026-09-01 23:59:00 UTC):

- **Candle Count Retrieved:** 2,880 bars ($2 \times 1,440\text{ min}$)
- **First Timestamp:** `2026-08-31 00:00:00 UTC`
- **Last Timestamp:** `2026-09-01 23:59:00 UTC`
- **Duplicates:** `0` (Zero duplicate timestamps)
- **OHLC Geometry Violations:** `0` (All bars strictly satisfy $h \ge \max(o,c,l)$ and $l \le \min(o,c,h)$)
- **Chronological Ordering:** Strict ascending sequence verified
- **Expected Session Gaps:** 0 (Market remained fully continuous during this window)
- **Unexpected Internal Gaps:** `0`
- **Largest Unexpected Gap:** `NONE (0 min)`

---

## 6. Closed 15-Minute Candle Validation & Datetime Input Contracts

- **Provider Timestamp Convention:** Twelve Data returns the bar opening timestamp with `timezone="UTC"` parameter.
- **In-Progress Bar Detection:** When a request is made while a 15m bar is forming, Twelve Data delivers the floating in-progress bar as `values[0]`.
- **Closed Bar Rule:**
  $$\text{timestamp\_close} = \text{timestamp\_open} + \text{interval\_delta}$$
  A bar is classified as closed if and only if:
  $$\text{timestamp\_close} \le T_{\text{now, UTC}}$$
  AurumIQ's `TwelveDataProvider` automatically filters out in-progress bars when `only_closed=True`, ensuring signal computation is conducted exclusively on completed price action.
- **Strict Datetime Input Contract:**
  `_normalize_to_utc_aware(dt, param_name)` enforces:
  - Rejects `None` (`ValueError: MISSING_DATETIME`)
  - Rejects naive datetime (`ValueError: NAIVE_DATETIME_FORBIDDEN`)
  - Rejects ambiguous tzinfo (`dt.utcoffset() is None`)
  - Converts timezone-aware inputs strictly via `dt.astimezone(timezone.utc)`
  - Rejects invalid bounded windows (`start > end`)

---

## 7. Latency Metrics Breakdown

Per Section 7 of the protocol:
- **`HTTP_ROUNDTRIP_LATENCY_MS`:** `506ms` *(observed range: 506ms – 816ms)*. This represents network roundtrip transit time for HTTPS REST request/response.
- **`CANDLE_PUBLICATION_LATENCY`:** `NOT_EMPIRICALLY_MEASURED` *(requires long-term tick timestamp differential logging; not fabricated)*.

---

## 8. Quota Model & Multi-Scenario Accounting

Twelve Data Basic Free Tier quotas:
- **Rate Limit:** 8 requests / minute
- **Daily Budget:** 800 requests (credits) / day
- **Credit Weight:** 1 credit per request (equal for `/time_series`, `/earliest_timestamp`, `/api_usage`).

### Operational Scenarios:

| Scenario | Description | Query Cadence | Daily Requests / Credits | Free Plan Budget Utilization | Free Tier Suitability |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario A** | **15m Trigger-Only** | 1 request every 15 minutes at bar close | $4 \times 24 = 96$ / day | **12.0%** (88% headroom) | **PASS** |
| **Scenario B** *(Recommended)* | **Timeframe-Aligned Refresh** | 15m: every 15m (96/d)<br>1h: every 1h (24/d)<br>4h: every 4h (6/d)<br>1d: once daily (1/d)<br>Health probes: 24/d<br>Retries buffer: ~10/d | $96 + 24 + 6 + 1 + 24 + 10 = \mathbf{161}$ / day | **20.1%** (79.9% headroom) | **PASS** |
| **Scenario C** | **Conservative Upper Bound** | All 6 timeframes fetched on every 15m cycle | $6 \times 4 \times 24 = 576$ / day<br>+ 24 health checks = $\mathbf{600}$ / day | **75.0%** (25.0% headroom) | **PASS** |

**Conclusion:** Twelve Data Free Basic plan is fully suitable for operational live monitoring without consuming excessive quota or requiring paid plans.

---

## 9. Qualification Command Reproducibility & Mode Semantics

The qualification command `apps/market_data/management/commands/qualify_twelve_data_xauusd.py` enforces three distinct execution modes with non-overlapping status semantics:

1. **Full Bounded Qualification (Authoritative Gate):**
   ```powershell
   uv run python manage.py qualify_twelve_data_xauusd --full
   ```
   - **Scope:** Authoritative qualification verifying XAU/USD identity, live access across all 6 timeframes (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) with explicit `timezone=UTC`, future timestamp sanity checks, OHLC geometry, and live closed 15m candle boundary handling (`only_closed=True`, `is_closed=True`, `timestamp_close <= now`).
   - **Emits:** `FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_USABLE` (or `LIMITED`, `UNUSABLE`, `TIMESTAMP_SEMANTICS_UNRESOLVED`).
   - **Pre-requisite:** ONLY this mode qualifies the analytical candle feed as usable.

2. **Fast Diagnostic Probe (Diagnostic Only):**
   ```powershell
   uv run python manage.py qualify_twelve_data_xauusd
   ```
   - **Scope:** Quick connectivity check verifying API key presence, endpoint health, and a single 15m closed candle request.
   - **Emits:** `FINAL STATUS: TWELVE_DATA_FAST_PROBE_PASS`
   - **Invariant:** Strictly diagnostic; explicitly warns that it does NOT constitute comprehensive provider qualification and never emits `PRIMARY_USABLE`.

3. **Offline Contract Check (CI/CD Verification):**
   ```powershell
   uv run python manage.py qualify_twelve_data_xauusd --offline
   ```
   - **Scope:** Validates timezone contracts and prohibited proxy filters locally without network calls.
   - **Emits:** `STATUS: OFFLINE_CONTRACT_CHECK_ONLY`

---

## 10. Safety Governance Status

| Gate / Invariant | Status | Enforcement |
| :--- | :--- | :--- |
| **Production Authority** | `FALSE` | Explicitly locked |
| **Published Decision** | `WAIT` | Active invariant |
| **Automatic Trading** | `ABSENT` | No broker trade execution endpoints exist |
| **MT5 Remote Bridge** | `PARKED` | Kept dormant; future optional cross-validator |
| **Telegram Alerts** | `OUT_OF_SCOPE` | Mock/disabled |
| **Web Dashboard** | `PRIMARY USER INTERFACE` | Active local UI |
| **Phase 8 (Live Automation)**| `HOLD` | Strict hold pending empirical calibration |
| **Calibration Readiness Gate**| `CALIBRATION_DATA_NOT_READY`| Preserved; 0 persisted candles |

---

## 11. Final Status Declaration

The Twelve Data provider adapter is sealed as:
- **Module:** `apps/market_data/providers/twelve_data.py`
- **Registry ID:** `twelve_data_xauusd`
- **Management Command:** `python manage.py qualify_twelve_data_xauusd`
- **Unit Test Suite:** `tests/unit/test_twelve_data_provider.py` (41 tests passing)

**Status:** `TWELVE_DATA_XAUUSD_PRIMARY_USABLE`  
*(Primary Analytical Candlestick Feed Usable)*
