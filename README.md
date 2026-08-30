# AurumIQ — XAUUSD Algorithmic Signal Intelligence & Execution Readiness Platform

AurumIQ is a research-grade, production-hardened algorithmic intelligence and decision-support platform for **XAUUSD** (Spot Gold / US Dollar) with preserved historical **XAUT** audit baseline continuity. It provides point-in-time causal signal analysis, multi-timeframe regime classification (15m, 1H, 4H, 1D), side-aware directional risk architecture (`BUY / WAIT / SELL`), and real-time execution readiness monitoring.

---

## 🛡️ Core Operating Philosophy & Safety Boundaries

1. **Zero Real-Order Placement**: AurumIQ is strictly an intelligence and decision-support system. It possesses zero exchange order placement APIs, private trading keys, broker execution integrations, or fund withdrawal capabilities.
2. **One Engine Parity**: The identical frozen pure Python calculation engines ([engine/](file:///d:/Data%20Kacong/Antigravity%20Project/AurumIQ/engine/)) are used across historical backtesting, walk-forward validation, and live real-time decision pipelines.
3. **Point-in-Time Causality & Immutability**: All market signals, risk boundaries, and audit records are strictly closed-candle ($T$) evaluated. Streaming live ticker quotes update only presentation quotes; signal scores, states, and fingerprints are 100% immutable.
4. **Authoritative Fail-Closed**: In the event of stale data feeds, provider transitions, macro event blackouts, or unclosed decision candles, the system unconditionally fails closed to `WAIT` (`FORCE_WAIT`).
5. **Side-Aware Dual Direction Support**: Full support for both `BUY` and `SELL` setups with independent, side-aware structural stops, ATR buffers, and take-profit targets.
6. **Historical Audit Preservation**: Historical XAUT records, schemas, and test baselines remain preserved for audit reproducibility and are never overwritten.

---

## 🏛️ System Architecture

```text
                                  ┌──────────────────────────┐
                                  │   Public Market Feeds    │
                                  │ (XAUUSD, Macro, Baseline)│
                                  └────────────┬─────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       ▼                                               ▼
              [ Path A: Live Quotes ]                       [ Path B: Closed Candles ]
                       │                                               │
             LiveQuoteService (Redis TTL)                  LiveDecisionPipelineService
                       │                                               │
                       │                                      SignalEngine (Phase 4: BUY/WAIT/SELL)
                       │                                      RiskPlanner (Phase 5: Long/Short)
                       │                                               │
                       │                                       Durable Persistence
                       │                                    (SignalRecord, LiveRiskPlan)
                       │                                               │
                       └───────────────────────┬───────────────────────┘
                                               ▼
                                 ┌───────────────────────────┐
                                 │   Live Monitor WebSocket  │
                                 │  Typed Incremental Events │
                                 └─────────────┬─────────────┘
                                               ▼
                                 ┌───────────────────────────┐
                                 │   AurumIQ Live Dashboard  │
                                 │   (Django 5.2 + Plotly)   │
                                 └───────────────────────────┘
```

---

## 📂 Repository Layout

- **`engine/`**: Pure, framework-agnostic mathematical engines (zero Django/Celery/Redis imports).
  - `engine/core/`: Interfaces, data types, and protocol definitions.
  - `engine/features/`: Technical indicators (EMA, MACD, RSI, ADX, ATR, Realized Vol).
  - `engine/regime/`: Causal regime classification and sample guard.
  - `engine/structure/`: Swing high/low, BOS/CHoCH, and active supply/demand zones.
  - `engine/cycles/`: DST-aware session cycles (Phase 3A) & research spectral methods (Phase 3B).
  - `engine/signals/`: Direction & Timing scoring, state machine (`BUY/WAIT/SELL`), and canonical fingerprinting.
  - `engine/risk/`: Structure/ATR stops, dynamic TP1/TP2 targets, and side-aware risk planning.
  - `engine/backtest/`: Walk-forward validator, cost simulation, and component ablation lab.
- **`apps/`**: Django application layer and adapters.
  - `apps/market_data/`: Ingestion pipelines, provider adapters, and quote normalization.
  - `apps/analysis/`: Feature, regime, structure, and cycle snapshot persistence.
  - `apps/signals/`: Idempotent signal persistence (`SignalRecord`).
  - `apps/backtests/`: Asynchronous backtesting tasks and results storage.
  - `apps/live_monitor/`: Two-path live streaming services, WebSockets, and health probes.
  - `apps/accounts/`: Hardened RBAC, profile management, and immutable audit logs.
- **`tests/`**: Comprehensive multi-phase test suite (Unit, Integration, Concurrency, and Acceptance gates).

---

## 🚀 Deployment & Operation

### Development Mode (Local Docker Compose)
```bash
# Start PostgreSQL, Redis, Django Web, and Celery Worker (Development)
docker compose -f docker/docker-compose.yml up -d

# Run Django system check
docker compose -f docker/docker-compose.yml exec web python manage.py check

# Run full multi-phase regression suite (Phase 0 - 7 + Account Hardening)
docker compose -f docker/docker-compose.yml exec web pytest

# Create Local Operator User
docker compose -f docker/docker-compose.yml exec web python manage.py createsuperuser
```

### Production Mode (Hardened ASGI Deployment)
```bash
# Build and run production-grade ASGI container stack with isolated networking
docker compose -f docker/docker-compose.prod.yml up -d --build
```
Navigate to `http://localhost:8000/accounts/login/` and sign in with provisioned credentials.

---

## 📋 Phase Roadmap & Implementation Status

| Phase | Description | Focus Instrument / Action | Status |
|---|---|---|:---:|
| **Phase 0** | Foundation, Docker, Django 5.2 LTS, 5 Named Celery Queues | Generic / Multi-Asset Framework | ✅ **APPROVED** |
| **Phase 1** | Market Data Ingestion, Integrity Engine, Normalization | Multi-Provider Ingestion | ✅ **APPROVED** |
| **Phase 2** | Pure Feature Engine, Regime Classification & Causal Swings | Technical Features & Swings | ✅ **APPROVED** |
| **Phase 3A** | Robust Time Cycles (DST Session, Swing Duration, Calendar) | Multi-Timeframe Timing | ✅ **APPROVED** |
| **Phase 3B** | Experimental Spectral Cycles (ACF, FFT, Wavelets, Hilbert) | Locked 0.0 Production Weight | ✅ **APPROVED** |
| **Phase 4** | Direction & Timing Scoring, Hard Gates & Fingerprints | `BUY / WAIT / SELL` Mapping | ✅ **APPROVED** |
| **Phase 5** | Risk Architecture, Dual-Layer Decisions, Execution Models | Side-Aware Long & Short Planning | ✅ **APPROVED** |
| **Phase 6** | Point-in-Time Walk-Forward Backtesting & Component Ablation | Historical Robustness Lab | ✅ **APPROVED** |
| **Phase 7** | Live Signal Intelligence, Real-Time Dashboard & Alerts | Operational Monitoring Dashboard | ✅ **APPROVED** |
| **Phase 8** | Live Paper Observation & Forward Execution Audit | XAUUSD BUY+SELL Paper Observation | 📋 **PLANNED** |
| **Phase 9** | ML Meta-Filter & Out-of-Sample Probability Calibration | XAUUSD BUY+SELL Candidate Filter | 📋 **PLANNED** |

---

## 🔍 Repository Terminology Audit (Taxonomy A)

| Term / Artifact Category | Classification | Context / Migration Policy |
|---|---|---|
| Historical XAUT Data / Initial Migrations | `LEGACY` | Preserved as baseline audit reference; never purged or rewritten. |
| Engine Core Interfaces / Protocols | `KEEP_GENERIC` | Pure typed interfaces (`CandleRepository`, `MarketDataProvider`) remain instrument-agnostic. |
| Active Specifications & Signal Documentation | `MIGRATE` | Updated to establish `XAUUSD` as primary instrument with `BUY/WAIT/SELL` scope. |
| Speculative Tether Gold On-Chain Assumptions | `REMOVE` | Deprecated and purged from active production specifications. |
