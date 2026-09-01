# Phase 7: Dashboard, LiveMonitor & Alerts

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** ⏸️ **PRODUCT COMPLETION PAUSED / ADAPTATION PENDING**

---

## XAUUSD Migration Addendum

### 1. Target Scope & Presentation Adaptation
- **Target Instrument:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD).
- **User Decision Display:** `BUY / WAIT / SELL` across 15m, 1H, 4H, and 1D closed intervals.
- **Dual-Side Metric Dashboard:**
  - **Live Feed & Health:** Live XAUUSD price, feed freshness (configuration-driven), provider health status (`HEALTHY`, `DEGRADED`, `UNHEALTHY`, `NOT_CONFIGURED`).
  - **Direction & Timing Scores:** Long Direction Score, Short Direction Score, Long Timing Score, Short Timing Score.
  - **Dual-Layer State Presentation:** Candidate State (`candidate_state`), Candidate Decision (`candidate_user_decision`), Published State (`state`), Published Decision (`user_decision` — held at `WAIT` pending Phase 6 validation).
  - **Risk Planning Geometry:** Selected Entry Zone ($[\text{Min}, \text{Max}]$), Invalidation Stop (`Stop_Final`), Structural $\text{TP1}$, $\text{TP2}$, and unrounded Reward-to-Risk ratio.
  - **Diagnostics & Governance:** Risk candidate status, calibration profile status (`PENDING_DATA` / `CALIBRATION_REQUIRED`), hard-gate reasons, and Phase 3B research status (`production_weight = 0.0`).
- **Live Cache Architecture:** Active quote streaming and monitoring utilize Redis cache key `livequote:XAUUSD` with configuration-driven Redis TTL. (Historical `livequote:XAUTUSDT` is superseded).
- **Elimination of Deprecated Active Dependencies:** Removed active dependencies on XAUT/USDT basis z-score, USDT/USD peg deviation monitor, and "XAU confirms XAUT" cross-asset validation.

### 2. Dual-Side Informational Alerting Matrix
Alerts provide pure real-time notification support for human traders. **Zero alerts contain order execution instructions.**
- **Long Setup Alerts:** `WATCH_LONG_CREATED`, `READY_LONG`, `BUY_WINDOW_CANDIDATE`.
- **Short Setup Alerts:** `WATCH_SHORT_CREATED`, `READY_SHORT`, `SELL_WINDOW_CANDIDATE`.
- **State & Safety Alerts:** `CONFLICT`, `MACRO_BLACKOUT_ACTIVE`, `SYSTEM_SAFETY_HOLD`.
- **Proximity & Invalidation Alerts:** `ENTRY_ZONE_REACHED`, `INVALIDATION_TOUCHED`.
- **Infrastructure Alerts:** `LIVE_DATA_STALE`, `PROVIDER_UNHEALTHY`.

### 3. Approved Planned Test Contracts
- **`XAU-P7-01`**: Dual-side BUY / WAIT / SELL presentation and informational alerting contract (`PLANNED / FUTURE CONTRACT`).

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **APPROVED (HISTORICAL XAUT REFERENCE)**  
> **Primary Goal:** Build a responsive, server-rendered Django dashboard with interactive Plotly visual analytics, a real-time `LiveMonitor` (WebSocket + Redis with TTL and freshness guards), REST API endpoints, and informational alert dispatchers.

### 1. Dashboard Architecture (`apps/dashboard/`)

Server-rendered Django templates with dynamic Plotly visualizations and premium CSS styling:

#### Navigation Hierarchy
`OVERVIEW | LIVE ANALYSIS | TIME CYCLE LAB | SIGNALS HISTORY | BACKTEST LAB | DATA INTEGRITY | SYSTEM HEALTH | AUDIT LOG`

#### Page Specifications
1. **Overview (`overview.html`):**
   - Live XAUT price, last analysis timestamp (prominently displayed), Data Quality score.
   - Large Gauge / KPI cards: Direction Score, Timing Score, Market Regime, Active Signal State.
   - Current Risk Architecture: Entry Zone ($[\text{Min}, \text{Max}]$), Invalidation/Stop, TP1, TP2, RR.
   - USDT/USD peg deviation monitor and XAU gold reference confirmation card (Historical).
   - Top positive and negative reasons list.
2. **Live Analysis (`live_analysis.html`):**
   - Interactive Plotly multi-timeframe candlestick chart (1D/4H/1H/15m) with overlay of confirmed swings, BOS markers, and Support/Resistance zones.
3. **Time Cycle Research (`time_cycle.html`):**
   - Continuous Wavelet Transform (CWT) scalogram power spectrum heatmap.
   - Active swing duration percentile gauge ($P10 - P90$).
   - Session expectancy matrix table by regime and hour.
4. **Signals History (`signals.html`):**
   - Paginated, filterable table of immutable signals and full component breakdown.
5. **Backtest Lab (`backtest_lab.html`):**
   - Interactive form to launch backtest / ablation jobs asynchronously; progress bar via polling.
6. **Data Integrity & System Health (`data_quality.html`, `system_health.html`):**
   - Provider health snapshot table, quarantine log, Celery queue heartbeats.

### 2. LiveMonitor Service (`apps/live_monitor/` & A28)

Decouples high-frequency tick monitoring from candle-close signal computation.

```text
WebSocket Stream / REST Poller ──> Redis Store (Key: livequote:XAUTUSDT, TTL: 30s)
                                              │
                                              ▼
                                      LIVEMONITOR SERVICE
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                 Quote Fresh & Provider Healthy?         Quote Stale / Expired?
                              │                               │
                              ▼                               ▼
               Evaluate Entry / Invalidation Zones       Emit LIVE_DATA_STALE
                              │                         (Suppress all zone alerts)
                              ▼
                ENTRY_ZONE_REACHED / INVALIDATION_TOUCHED
```

#### Freshness & Health Guard Rules (A28)
- If quote age in Redis $> 30$ seconds $\rightarrow$ **`LIVE_DATA_STALE`**.
- If latest `ProviderHealthSnapshot` is not `HEALTHY` or `DEGRADED` $\rightarrow$ **`PROVIDER_UNHEALTHY`**.
- In either case, **`ENTRY_ZONE_REACHED` alerts are strictly suppressed**.

### 3. Informational Alerting (`apps/alerts/`)

Dispatches real-time notifications via Webhooks / Telegram without order execution capability.

#### Alert Payload Schema
```json
{
  "event_type": "ENTRY_ZONE_REACHED",
  "symbol": "XAUTUSDT",
  "current_price": 4588.50,
  "signal_state": "READY",
  "entry_zone": [4580.00, 4590.00],
  "stop_loss": 4530.00,
  "tp1": 4690.00,
  "data_timestamp": "2026-08-29T14:15:00Z",
  "disclaimer": "MANUAL DECISION SUPPORT ONLY — NO AUTO-ORDER EXECUTION."
}
```

### 4. TradingView Policy (R18 & A18)

- **Allowed:** TradingView Lightweight Charts library rendering internal data; manual link to external TradingView charts.
- **Strictly Forbidden:** Using TradingView as a source for indicator math or scraping calculation data.
