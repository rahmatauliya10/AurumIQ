# Phase 7: Dashboard, LiveMonitor & Informational Alerts

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** ⏸️ `PRODUCT COMPLETION PAUSED`  
> **Primary Goal:** Build a responsive, server-rendered Django dashboard with interactive Plotly visual analytics, a real-time `LiveMonitor` (WebSocket + Redis with TTL and freshness guards), REST API endpoints, user management with immutable audit logs, and informational alert dispatchers.

---

## 1. Dashboard & Visual Presentation (`apps/dashboard/`)

Server-rendered Django templates with dynamic Plotly visualizations and premium dark-tech aesthetics:
- **Overview:** Live price, last analysis timestamp, active state (`BUY / WAIT / SELL`), and risk architecture.
- **Live Analysis:** Multi-timeframe candlestick chart (1D/4H/1H/15m) with confirmed swings, BOS markers, and structure zones.
- **Time Cycle Lab:** CWT scalogram heatmaps, swing duration maturity gauges, and session expectancy matrices.
- **Signals History:** Paginated, filterable table of immutable signal records with canonical SHA-256 fingerprints.
- **Backtest Lab:** Form to launch asynchronous backtest and ablation jobs with real-time polling.
- **User Management & Audit Trail:** RBAC user directory with last-active admin lockout protection and read-only audit logs.

---

## 2. LiveMonitor Service (`apps/live_monitor/` & A28)

Decouples high-frequency tick monitoring from candle-close signal computation.

```text
WebSocket Stream / REST Poller ──► Redis Store (Key: livequote:XAUUSD, TTL: 30s)
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
- If quote age in Redis $> 30$ seconds $\implies$ `LIVE_DATA_STALE`.
- If latest `ProviderHealthSnapshot` is not `HEALTHY` or `DEGRADED` $\implies$ `PROVIDER_UNHEALTHY`.
- In either case, `ENTRY_ZONE_REACHED` alerts are strictly suppressed.

---

## 3. Informational Alerting & TradingView Policy (R1, R18)

- **Informational Alerting (`apps/alerts/`):** Dispatches real-time notifications via Webhooks / Telegram without order execution capability.
- **TradingView Isolation (R18 & A18):** TradingView is permitted only for client-side rendering (Lightweight Charts) or external manual chart links; engine code has zero scraping dependencies.

---

## 4. Definition of Done Checklist

### Historical Baseline
- [x] Django dashboard views and Plotly chart templates implemented.
- [x] `LiveMonitor` service operates against Redis TTL quotes with freshness guards (`A28`).
- [x] Informational alerts trigger without trading execution permissions (`A12`).
- [x] Static AST scan confirms zero trading key / execution methods in codebase (`A12`, `P7-45`).
- [x] RBAC permissions and user management audit trails hardened (`A46`, `A47`, `P7-AUTH-01` to `P7-AUTH-07`).

### Target XAUUSD Scope
- [ ] Connect dashboard and LiveMonitor to direct spot XAUUSD feeds upon completion of Phases 1–6.
