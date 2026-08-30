# Phase 7: Dashboard, LiveMonitor & Alerts

> **Status:** ✅ **APPROVED**  
> **Primary Goal:** Build a responsive, server-rendered Django dashboard with interactive Plotly visual analytics, a real-time `LiveMonitor` (WebSocket + Redis with TTL and freshness guards), REST API endpoints, user management with immutable audit logs, and informational alert dispatchers.

---

## 1. Dashboard Architecture (`apps/dashboard/`)

Server-rendered Django templates with dynamic Plotly visualizations and premium dark-tech aesthetics:

### Navigation Hierarchy
`OVERVIEW | LIVE ANALYSIS | TIME CYCLE LAB | SIGNALS HISTORY | BACKTEST LAB | DATA INTEGRITY | SYSTEM HEALTH | AUDIT LOG | USER MANAGEMENT`

### Page Specifications
1. **Overview (`overview.html`):**
   - Live XAUUSD price, last analysis timestamp (prominently displayed), Data Quality score.
   - Large Gauge / KPI cards: Direction Score, Timing Score, Market Regime, Active Signal State (`BUY / WAIT / SELL`).
   - Current Risk Architecture: Entry Zone ($[\text{Min}, \text{Max}]$), Invalidation/Stop, TP1, TP2, RR.
   - Primary gold reference status and macro alignment card.
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
7. **User Management & Audit Trail (`user_management.html`, `audit_log.html`):**
   - RBAC directory for user management (Admin only) with last-active admin lockout protection, role assignments (`VIEWER`, `ANALYST`, `ADMIN`), and immutable `UserManagementAuditLog` records.

---

## 2. LiveMonitor Service (`apps/live_monitor/` & A28)

Decouples high-frequency tick monitoring from candle-close signal computation.

```text
WebSocket Stream / REST Poller ──> Redis Store (Key: livequote:XAUUSD, TTL: 30s)
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

### Freshness & Health Guard Rules (A28)
- If quote age in Redis $> 30$ seconds $\rightarrow$ **`LIVE_DATA_STALE`**.
- If latest `ProviderHealthSnapshot` is not `HEALTHY` or `DEGRADED` $\rightarrow$ **`PROVIDER_UNHEALTHY`**.
- In either case, **`ENTRY_ZONE_REACHED` alerts are strictly suppressed**.

---

## 3. Informational Alerting (`apps/alerts/`)

Dispatches real-time notifications via Webhooks / Telegram without order execution capability.

### Alert Payload Schema
```json
{
  "event_type": "ENTRY_ZONE_REACHED",
  "symbol": "XAUUSD",
  "current_price": 2845.50,
  "signal_state": "READY",
  "direction": "BUY",
  "entry_zone": [2840.00, 2848.00],
  "stop_loss": 2818.00,
  "tp1": 2905.00,
  "data_timestamp": "2026-08-30T14:15:00Z",
  "disclaimer": "MANUAL DECISION SUPPORT ONLY — NO AUTO-ORDER EXECUTION."
}
```

---

## 4. TradingView Policy (R18 & A18)

- **Allowed:** TradingView Lightweight Charts library rendering internal data; manual link to external TradingView charts.
- **Prohibited:** Scraping TradingView; fetching data from TradingView into the engine calculation pipeline.

---

## 5. Phase 7 Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A11** | API Freshness Metadata | REST API returns explicit `last_analysis_timestamp` and `data_quality_score`. |
| **A12** | No Execution Code Gate | Static AST scan confirms zero order placement or exchange trading endpoints in codebase. |
| **A18** | TradingView Isolation | Engine code has zero network calls or scraping dependencies to TradingView. |
| **A28** | Live Quote Freshness & Health | Stale, expired, or quarantined Redis live quote emits `LIVE_DATA_STALE` and cannot trigger entry alerts. |

---

## 6. Definition of Done Checklist

- [x] Django dashboard views and Plotly charts fully responsive.
- [x] `LiveMonitor` service operates against Redis TTL quotes with freshness guards.
- [x] Informational alerts trigger without any trading execution permissions.
- [x] AST scan validates zero trading key / execution methods in codebase.
- [x] RBAC permissions and user management audit trails hardened.
- [x] Acceptance tests **A11, A12, A18, A28, A39, A40, A41, A42, A43, A44, A45** passing.
- [x] Targeted tests **P7-01 through P7-27** and **P7-AUTH-01 through P7-AUTH-07** passing.
