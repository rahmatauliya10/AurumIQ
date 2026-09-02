# AurumIQ — Twelve Data XAU/USD Live Provider Qualification Report

> **Document Class:** Provider Qualification & Analytical Feed Audit  
> **Authoritative Target:** `XAUUSD` (Spot Gold denominated in USD)  
> **Provider Identity:** `twelve_data_xauusd`  
> **Provider Symbol:** `XAU/USD`  
> **Baseline Commit:** `6a6bb16de112ba88cd6e0a80e11fe51074e2018d`  
> **Qualification Date:** September 2026  
> **Execution Status:** Complete — Technical Qualification Successful  
> **Final Qualification Status:** `TWELVE_DATA_XAUUSD_PRIMARY_USABLE`

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
|                                 - Direct Decimal(str) Parsing                     |
|                                 - UTC-Normalized Datetime                         |
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

---

## 3. Empirical Live Access & Timeframe Evidence

All probes were executed against the official Twelve Data HTTPS endpoint (`https://api.twelvedata.com`) with pacing adhering to the Basic Free tier rate limit (8 requests/minute):

| Timeframe | Provider Interval | HTTP Status | Returned Symbol | Returned Interval | Sample Count | Earliest Accessible | Latest Observed Timestamp | OHLC Geometry | Volume Presence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`1m`** | `1min` | 200 OK | `XAU/USD` | `1min` | 5 | `2020-04-06 16:40:00` | `2026-09-03 01:34:00` | **PASS** | `ABSENT` |
| **`5m`** | `5min` | 200 OK | `XAU/USD` | `5min` | 5 | `2020-03-16 12:10:00` | `2026-09-03 01:30:00` | **PASS** | `ABSENT` |
| **`15m`** | `15min` | 200 OK | `XAU/USD` | `15min` | 5 | `2020-01-24 13:00:00` | `2026-09-03 01:30:00` | **PASS** | `ABSENT` |
| **`1h`** | `1h` | 200 OK | `XAU/USD` | `1h` | 5 | `2020-01-24 13:00:00` | `2026-09-03 01:00:00` | **PASS** | `ABSENT` |
| **`4h`** | `4h` | 200 OK | `XAU/USD` | `4h` | 5 | `2020-01-24 11:00:00` | `2026-09-02 23:00:00` | **PASS** | `ABSENT` |
| **`1d`** | `1day` | 200 OK | `XAU/USD` | `1day` | 5 | `1979-12-26` | `2026-09-02` | **PASS** | `ABSENT` |

---

## 4. History Capability Assessment

- **Earliest Timestamp Endpoint:** Twelve Data official `/earliest_timestamp` endpoint is fully accessible on the user's Basic tier.
- **Intraday History Depth:**
  - 1m history extends back to April 2020 (~6.4 years of 1-minute data).
  - 15m and 1h history extends back to January 2020 (~6.6 years of data).
  - 1d history extends back to December 1979 (>46 years of daily data).
- **Prohibition on Bulk Downloading:** No bulk backfill was initiated during qualification; historical records persisted into database remain exactly 0.

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

## 6. Closed 15-Minute Candle Validation

- **Provider Timestamp Convention:** Twelve Data returns the bar opening timestamp with `timezone="UTC"` parameter.
- **In-Progress Bar Detection:** When a request is made while a 15m bar is forming (e.g., at 15:37 UTC for the 15:30 bar), Twelve Data delivers the floating in-progress bar as `values[0]`.
- **Closed Bar Rule:**
  $$\text{timestamp\_close} = \text{timestamp\_open} + \text{interval\_delta}$$
  A bar is classified as closed if and only if:
  $$\text{timestamp\_close} \le T_{\text{now, UTC}}$$
  AurumIQ's `TwelveDataProvider` automatically filters out in-progress bars when `only_closed=True`, ensuring signal computation is conducted exclusively on completed price action.

---

## 7. Decimal Integrity & Precision

- **No Float Contamination:** Price attributes (`open`, `high`, `low`, `close`) are parsed directly via `Decimal(str(val))` without intermediate IEEE-754 float representation.
- **Verification:** Precision test confirmed exact 5-decimal retention (`4370.40058`).

---

## 8. Volume Semantics Classification

- **Observed Payload:** Twelve Data spot `XAU/USD` returns no volume field in standard REST payload.
- **Classification:** Categorized strictly as `UNAVAILABLE` with `volume = Decimal("0")`.
- **Prohibition on Synthetic Volume:** Tick volume or proxy crypto-volume is never promoted to `REAL_VOLUME`.

---

## 9. Quota & Free Plan Suitability

- **API Usage Probe:**
  - Plan Category: `basic` (Free tier)
  - Rate Limit: 8 requests / minute
  - Daily Budget: 800 requests / day
- **Qualification Consumption:**
  - Requests used during live audit: 17 calls
  - 429 Rate Limit Encountered: **NO**
- **AurumIQ Live Cadence Requirement:**
  - One 15-minute evaluation cycle every 15 minutes: $4 \times 24 = 96\text{ requests/day}$.
  - Daily Budget Utilization: $96 / 800 = 12.0\%$.
  - Free Tier Headroom: **88.0% safety margin**.
- **Assessment:** Fully suitable for operational live monitoring without incurring infrastructure costs.

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

## 11. Conclusion & Provider Identity

The Twelve Data provider adapter is integrated as:
- **Module:** `apps/market_data/providers/twelve_data.py`
- **Registry ID:** `twelve_data_xauusd`
- **Management Command:** `python manage.py qualify_twelve_data_xauusd`
- **Unit Test Suite:** `tests/unit/test_twelve_data_provider.py` (34 tests passing)

**Status:** `TWELVE_DATA_XAUUSD_PRIMARY_USABLE`
