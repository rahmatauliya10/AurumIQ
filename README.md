# AurumIQ — Multi-Timeframe Quantitative Gold Intelligence Platform

> **Target Instrument Scope:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD)  
> **Historical Baseline:** `XAUT` (Tether Gold) historical baseline verified, frozen, and permanently retained for audit integrity.  
> **User Decision Scope:** `BUY / WAIT / SELL` (Human decision support only — zero automated order execution).

---

## 1. System Overview & Dual-Scope Architecture

AurumIQ is an institutional-grade, point-in-time multi-timeframe quantitative market intelligence and signal analysis platform for gold trading.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                       AURUMIQ SIGNAL PIPELINE                           │
│                                                                         │
│  CLOSED CANDLE ENGINE (15m, 1H, 4H, 1D) ──► STATE MACHINE               │
│  - Multi-Timeframe Indicators              - Long Direction & Timing    │
│  - Causal Swing & Structure Detection      - Short Direction & Timing   │
│  - Statistical Session Cycles (DST-aware)  - Decision: BUY / WAIT / SELL│
│  - Macro Blackout Gate (Revision-Safe)     - Canonical SHA-256 Provenance│
│                                                          │              │
│                                                          ▼              │
│  LIVE MONITOR (WebSocket / Redis TTL) ◄───────── RISK PLANNING GATE     │
│  - Real-Time Presentation Only                  - Side-Aware Long/Short │
│  - Stale Feed Guard (<30s)                      - Structural + ATR Stop │
│  - Zone Proximity Alerts                        - RR Evaluation Gate    │
│  - Zero Execution Code                          - Intrabar 1m/5m Replay │
└─────────────────────────────────────────────────────────────────────────┘
```

### Core Operating Invariants
1. **Zero Real-Order Execution Policy (R1):** The platform contains zero exchange trading keys, broker execution bindings, order dispatch endpoints, or testnet trading capabilities. All outputs are strictly decision support.
2. **Decoupled Two-Path Architecture:**
   - **Path A (Live Streaming Quotes):** Low-latency WebSockets and Redis TTL ($30\text{s}$) updating presentation metrics and live zone proximity monitoring.
   - **Path B (Closed Candles Decision Pipeline):** Strict closed-bar signal evaluation, immutable fingerprint generation, and PostgreSQL persistence on completed 15m, 1H, 4H, and 1D candles.
3. **Intrabar Replay Segregation:** 1m and 5m streams are strictly isolated for causal fill simulation, execution latency, and intrabar barrier collision resolution during backtesting and forward paper observation.
4. **TradingView Policy (R18 & A18):** TradingView is permitted exclusively for external visual reference or rendering via Lightweight Charts. The calculation engine contains zero scraping dependencies or network calls to TradingView.

---

## 2. Dual Status: Historical Baseline vs Current XAUUSD Target

To maintain complete audit integrity, AurumIQ maintains a clear separation between the **Historical XAUT Baseline** (which verified core algorithmic infrastructure) and the **Current XAUUSD Target** (which governs active platform scope).

| Phase | Specification Focus | Historical XAUT Status | Current XAUUSD Target Status |
|---|---|:---:|:---:|
| **Phase 0** | Foundation Architecture, PostgreSQL, Celery Queues, RBAC | ✅ `VERIFIED / FROZEN` | 🟢 `REUSABLE` |
| **Phase 1** | Ingestion Engine, Multi-Provider Data, Health Lifecycle | ✅ `VERIFIED / FROZEN` | 🟡 `MIGRATION REQUIRED` |
| **Phase 2** | Pure Indicators, Market Regimes, Causal Swings, Sample Guard | ✅ `VERIFIED / FROZEN` | 🟡 `REVALIDATION REQUIRED` |
| **Phase 3A** | DST Session Cycles, Swing Maturity, Macro Blackout Gate | ✅ `VERIFIED / FROZEN` | 🟡 `EMPIRICAL REBUILD REQUIRED` |
| **Phase 3B** | Experimental Spectral Cycles (ACF, FFT, Wavelet, Hilbert) | ✅ `VERIFIED / FROZEN` | 🟡 `REVALIDATION REQUIRED (WEIGHT = 0.0)` |
| **Phase 4** | Direction & Timing Scores, State Machine, Fingerprinting | ✅ `VERIFIED / FROZEN` (Long) | 🔴 `DUAL-SIDE REDESIGN REQUIRED (NOT IMPLEMENTED)` |
| **Phase 5** | Risk Planning Engine, Side-Aware Stops/Targets, Intrabar Replay | ✅ `VERIFIED / FROZEN` (Long) | 🔴 `LONG / SHORT REDESIGN REQUIRED (NOT IMPLEMENTED)` |
| **Phase 6** | Point-in-Time Backtesting, Walk-Forward Purge/Embargo, Ablation | ✅ `VERIFIED / FROZEN` | 🟡 `XAUUSD PIT BACKTEST REQUIRED` |
| **Phase 7** | Dashboard UI, Plotly Charts, LiveMonitor, Informational Alerts | ✅ `VERIFIED / FROZEN` | ⏸️ `PRODUCT COMPLETION PAUSED` |
| **Phase 8** | Live Paper Observation, 3-Tier Parity Auditing (BUY/SELL/Combined) | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |
| **Phase 9** | ML Meta-Filter, Side-Aware Labels, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |

---

## 3. Two Separate Taxonomies

### A. Repository Terminology Audit (Taxonomy A)
- `LEGACY`: Historical XAUTUSDT candle stores, USDT/USD rate providers, baseline basis calculation tables.
- `KEEP_GENERIC`: `CandleRepository`, `MarketDataProvider`, `Timeframe`, math statistical libraries, pure engine protocols.
- `MIGRATE`: Primary operational specifications in `./docs/phases/` and root `README.md`.
- `REMOVE`: Deprecated on-chain Ethereum redemption assertions from active operational specs.

### B. Acceptance-Test Migration Matrix (Taxonomy B)
- `LEGACY_XAUT`: Tests validating historical USDT/USD normalization and crypto basis integrity (`A17`, `A21`).
- `KEEP_GENERIC`: Multi-timeframe repository ordering, swing detection causality, mathematical indicator parity (`A01`, `A02`, `A03`, `A05`, `A08`, `A11`, `A12`, `A13`, `A16`, `A18`, `A19`, `A20`, `A24`, `A25`, `A26`, `A27`, `A29`, `A30`, `A31`, `A33`, `A35`, `A36`, `A37`, `A38`, `A40`, `A41`, `A42`, `A43`, `A44`, `A45`, `A46`, `A47`).
- `MODIFY_FOR_XAUUSD`: Live monitor quotes, provider health thresholds, DXY macro correlation feeds, backtest engine parity (`A04`, `A06`, `A07`, `A09`, `A10`, `A14`, `A15`, `A22`, `A23`, `A28`, `A32`, `A34`, `A39`, `A39X`).
- `REPLACE_FOR_XAUUSD`: Single-instrument spot gold ingestion and session cycle evaluation.
- `NEW_XAUUSD`: Planned future test contracts for side-aware Short risk evaluation, dual-direction triple-barrier outcome tracking, and 3-tier parity audits (`XAU-P1-01` through `XAU-P9-01`).

---

## 4. Technical Stack & Governance

- **Backend Framework:** Django 5.2 LTS (Python 3.13)
- **Task Queue & Cache:** Celery 5.x + Redis (5 dedicated priority queues)
- **Database:** PostgreSQL 16 (JSONB, append-only immutable audit logs)
- **Mathematical Engine:** Pure Python (`numpy`, `scipy`, `pandas` — zero Django/ORM dependencies in `engine/`)
- **Documentation & Roadmap Index:** [`docs/phases/README.md`](./docs/phases/README.md)
- **Phase Deliverables Summary:** [`docs/phases/SUMMARY.md`](./docs/phases/SUMMARY.md)

---

> [!IMPORTANT]
> **GOVERNANCE & CODE FREEZE NOTICE:**  
> All scoring weights and decision thresholds for XAUUSD remain **NOT FROZEN / REVALIDATION REQUIRED**. No automated order placement, `SELL_WINDOW` implementation, or scoring weight mutations are active in production code during documentation migration.
