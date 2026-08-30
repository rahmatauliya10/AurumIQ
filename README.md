# AurumIQ — Live XAUT/USDT Signal Intelligence & Execution Readiness Platform

AurumIQ is a research-grade, production-hardened algorithmic intelligence and decision-support system for **XAUT/USDT** (Tether Gold). It provides point-in-time causal signal analysis, multi-timeframe regime classification, strict mathematical risk management, and real-time execution readiness monitoring.

---

## 🛡️ Core Operating Philosophy & Safety Boundaries

1. **Zero Real-Order Placement**: AurumIQ is strictly an intelligence and decision-support dashboard. It possesses zero exchange order placement APIs, private trading keys, testnet keys, or balance withdrawal capabilities.
2. **One Engine Parity**: The identical frozen pure Python calculation engines ([engine/](file:///d:/Data%20Kacong/Antigravity%20Project/AurumIQ/engine/)) are used across historical backtesting, walk-forward validation, and live real-time decision pipelines.
3. **Point-in-Time Causality & Immutability**: All market signals, risk boundaries, and audit records are strictly closed-bar ($T$) evaluated. Streaming live ticker quotes update only presentation quotes; signal scores, states, and fingerprints are 100% immutable.
4. **Authoritative Fail-Closed**: In the event of stale data feeds, provider transitions, macro event blackouts, or missing canonical XAU benchmarks, the system unconditionally fails closed to `WAIT` (`FORCE_WAIT`).

---

## 🏛️ System Architecture

```text
                                  ┌──────────────────────────┐
                                  │   Public Market Feeds    │
                                  │ (Binance, OKX, XAU, USDT)│
                                  └────────────┬─────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       ▼                                               ▼
              [ Path A: Live Quotes ]                       [ Path B: Closed Candles ]
                       │                                               │
             LiveQuoteService (Redis TTL)                  LiveDecisionPipelineService
                       │                                               │
                       │                                      XautSignalEngine (Phase 4)
                       │                                      RiskPlanner (Phase 5)
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
                                 │  AurumIQ Live Dashboard   │
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
  - `engine/signals/`: Direction & Timing scoring, state machine, and canonical fingerprinting.
  - `engine/risk/`: Structure/ATR stops, dynamic TP1/TP2 targets, and position sizing.
  - `engine/backtest/`: Walk-forward validator, cost simulation, and component ablation lab.
- **`apps/`**: Django application layer and adapters.
  - `apps/market_data/`: Ingestion pipelines, provider adapters, and quote normalization.
  - `apps/analysis/`: Feature, regime, structure, and cycle snapshot persistence.
  - `apps/signals/`: Idempotent signal persistence (`SignalRecord`).
  - `apps/backtests/`: Asynchronous backtesting tasks and results storage.
  - `apps/live_monitor/`: Two-path live streaming services, WebSockets, and health probes.
- **`tests/`**: Comprehensive multi-phase test suite (Unit, Integration, and Master Acceptance gates).

---

## 🚀 Quick Start (Development & Testing)

### Running with Docker Compose
```bash
# Start PostgreSQL, Redis, Django Web, and Celery Worker
docker compose -f docker/docker-compose.yml up -d

# Run Django system check
docker compose -f docker/docker-compose.yml exec web python manage.py check

# Run full multi-phase regression suite (Phase 0 - 7)
docker compose -f docker/docker-compose.yml exec web pytest
```

### Operator Sign-In
Navigate to `http://localhost:8000/`. Default operator credentials:
- **Username**: `operator`
- **Password**: `aurumiq123`

---

## 📋 Implementation Status

| Phase | Description | Status |
|---|---|:---:|
| **Phase 0** | Foundation, Docker, Django 5.2 LTS, 5 Named Celery Queues | ✅ **APPROVED** |
| **Phase 1** | Market Data Ingestion, Integrity Engine, Point-in-Time Normalization | ✅ **APPROVED** |
| **Phase 2** | Pure Feature Engine, Regime Classification & Causal Swings | ✅ **APPROVED** |
| **Phase 3A** | Robust Time Cycles (DST Session, Swing Duration, Calendar Seasonality) | ✅ **APPROVED** |
| **Phase 3B** | Experimental Spectral Cycles (ACF, FFT, Wavelets, Hilbert) | ✅ **APPROVED** |
| **Phase 4** | Direction & Timing Scoring, Hard Gates & Canonical Fingerprints | ✅ **APPROVED** |
| **Phase 5** | Risk Architecture, Dual-Layer Decisions, Execution Models | ✅ **APPROVED** |
| **Phase 6** | Point-in-Time Walk-Forward Backtesting & Component Ablation | ✅ **APPROVED** |
| **Phase 7** | Live Signal Intelligence, Real-Time Dashboard & Operational Resilience | ✅ **APPROVED** |
| **Phase 8** | Live Paper Observation & Forward Execution Audit | 📋 **PLANNED** |
