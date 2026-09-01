# AurumIQ — Multi-Timeframe Quantitative Gold Intelligence Platform

> **Target Instrument Scope:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD)  
> **Historical Baseline:** `XAUT` (Tether Gold) historical baseline verified, frozen, and permanently retained for audit integrity.  
> **User Decision Scope:** `BUY / WAIT / SELL` (Human decision support only — zero automated order execution).  
> **Authoritative Main Baseline:** `9011764958d31c5e96860488da7c54568def1352` (Phase 5 Merged via PR #12)

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
│  - Statistical Session Cycles (DST-aware)  - Candidate: BUY / WAIT / SELL│
│  - Macro Blackout Gate (Revision-Safe)     - Layer B Publication: WAIT  │
│  - Canonical SHA-256 Provenance            - Zero Execution Code        │
│                                                          │              │
│                                                          ▼              │
│  LIVE MONITOR (WebSocket / Redis TTL) ◄───────── RISK PLANNING GATE     │
│  - Real-Time Presentation Only                  - Side-Aware Long/Short │
│  - Stale Feed Guard (<30s)                      - Structural + ATR Stop │
│  - Zone Proximity Alerts                        - Unrounded RR Gate     │
│  - Zero Execution Code                          - Intrabar 1m/5m Replay │
└─────────────────────────────────────────────────────────────────────────┘
```

### Core Operating Invariants
1. **Zero Real-Order Execution Policy (R1):** The platform contains zero exchange trading keys, broker execution bindings, order dispatch endpoints, or testnet trading capabilities. All outputs are strictly decision support.
2. **Decoupled Two-Path Architecture:**
   - **Path A (Live Streaming Quotes):** Low-latency WebSockets and Redis TTL ($30\text{s}$) updating presentation metrics and live zone proximity monitoring (`livequote:XAUUSD`).
   - **Path B (Closed Candles Decision Pipeline):** Strict closed-bar signal evaluation, immutable fingerprint generation, and PostgreSQL persistence on completed 15m, 1H, 4H, and 1D candles.
3. **Intrabar Replay Segregation:** 1m and 5m streams are strictly isolated for causal fill simulation, execution latency, and intrabar barrier collision resolution during backtesting and forward paper observation.
4. **TradingView Policy (R18 & A18):** TradingView is permitted exclusively for external visual reference or rendering via Lightweight Charts. The calculation engine contains zero scraping dependencies or network calls to TradingView.
5. **Production Publication Authority Invariant:** Even with Phase 4 and Phase 5 verified, Layer B publication user decision remains strictly `WAIT` (`is_production_authorized = False`). Live execution authority requires empirical validation in Phase 6.
6. **Position Sizing Boundary:** Position sizing is strictly out of scope for Phase 5 and Phase 6.

---

## 2. Master Phase Status Index

To maintain complete audit integrity, AurumIQ maintains a clear separation between the **Historical XAUT Baseline** (which verified core algorithmic infrastructure) and the **Current XAUUSD Target** (which governs active platform scope).

| Phase | Specification Focus | Historical XAUT Status | Current XAUUSD Target Status |
|---|---|:---:|:---:|
| **Phase 0** | Foundation Architecture, PostgreSQL, Celery Queues, RBAC, Protocols | ✅ `VERIFIED / FROZEN` | 🟢 `REUSABLE` |
| **Phase 1** | Ingestion Engine, Multi-Provider Normalization, Health Lifecycle | ✅ `VERIFIED / FROZEN` | 🟢 `CORE MIGRATION IMPLEMENTED (BINDING + EMPIRICAL THRESHOLDS PENDING)` |
| **Phase 2** | Pure Indicators, Market Regimes, Causal Swings, Volume Semantics | ✅ `VERIFIED / FROZEN` | 🟡 `CORE ARCHITECTURE IMPLEMENTED (EMPIRICAL THRESHOLDS NOT FROZEN)` |
| **Phase 3A** | DST Session Cycles, Swing Maturity, Macro Blackout Gate | ✅ `VERIFIED / FROZEN` | 🟡 `ARCHITECTURE IMPLEMENTED (CALIBRATION PENDING_DATA)` |
| **Phase 3B** | Experimental Spectral Cycles (ACF, FFT, Wavelet, Hilbert) | ✅ `VERIFIED / FROZEN` | 🧪 `IMPLEMENTED / RESEARCH ONLY (PRODUCTION WEIGHT = 0.0)` |
| **Phase 4** | Dual-Side Direction/Timing Scores, State Machine, Fingerprinting | ✅ `VERIFIED / FROZEN` (Long) | ✅ `COMPLETED & VERIFIED (SEALED PHASE 4 BASELINE)` |
| **Phase 5** | Side-Aware Risk Planning, Execution Model, Intrabar Resolver | ✅ `VERIFIED / FROZEN` (Long) | ✅ `COMPLETED & VERIFIED (MERGED PR #12 @ 9011764)` |
| **Phase 6** | PIT Backtesting, Walk-Forward Validation & Component Ablation | ✅ `VERIFIED / FROZEN` | 🟡 `NOT STARTED (PIT BACKTEST + WALK-FORWARD + ABLATION REQUIRED)` |
| **Phase 7** | Dashboard UI, LiveMonitor, Multi-Timeframe Charts, Alerts | ✅ `VERIFIED / FROZEN` | ⏸️ `PRODUCT COMPLETION PAUSED / ADAPTATION PENDING` |
| **Phase 8** | Live Paper Observation, 3-Tier Parity Auditing (BUY/SELL/Combined) | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |
| **Phase 9** | ML Meta-Filter, Side-Aware Labels, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |

---

## 3. Contract Taxonomy & Verification Matrix

### A. Verified XAUUSD Operational Contracts
- **Phase 1 Contracts:** `XAU-P1-01` (canonical primary XAUUSD target), `XAU-P1-02` (integrated multi-source ingestion integrity).
- **Phase 2 Contracts:** `XAU-P2-01` (explicit volume evidence semantics: `REAL_VOLUME`, `TICK_VOLUME`, `PROXY_VOLUME`, `UNAVAILABLE`).
- **Phase 3A Contracts:** DST-aware session windows, knowable swing age maturity, revision-safe macro blackout.
- **Phase 3B Contracts:** Experimental spectral cycle engine (ACF, FFT, Wavelet, Hilbert) with hard-locked `production_weight = 0.0`.
- **Phase 4 Contracts:** `XAU-P4-01` (`BUY_WINDOW` $\rightarrow$ `BUY`), `XAU-P4-02` (`SELL_WINDOW` $\rightarrow$ `SELL`), `XAU-P4-03` (`CONFLICT` $\rightarrow$ `WAIT`), `XAU-P4-04` (`SYSTEM_SAFETY_HOLD` $\rightarrow$ `WAIT`). Baseline SHA: `b619a140391e5e308241246e105b9767a1b0716d`.
- **Phase 5 Contracts:** `XAU-P5-01` (LONG side-aware risk planning), `XAU-P5-02` (SHORT side-aware risk planning), `XAU-P5-03` (side-aware market bid/ask causal execution). Hostile matrix `H1`–`H74` fully verified. Merge SHA: `9011764958d31c5e96860488da7c54568def1352`.

### B. Planned Future XAUUSD Contracts
- **Phase 6 Planned Contracts:** `XAU-P6-01` (LONG point-in-time backtest replay), `XAU-P6-02` (SHORT point-in-time backtest replay), `XAU-P6-03` (combined dual-side portfolio parity & ablation).
- **Phase 7 Planned Contracts:** `XAU-P7-01` (BUY / WAIT / SELL presentation and dual-side alerting).
- **Phase 8 Planned Contracts:** `XAU-P8-01` (forward paper execution tracking and 14-day operational stability audit).
- **Phase 9 Planned Contracts:** `XAU-P9-01` (point-in-time machine learning meta-labeling filter).

---

## 4. Documentation Governance & Provenance Rules

Whenever phase implementation status changes:
1. Update individual phase specification in `docs/phases/`.
2. Update `README.md` master index and contract matrices.
3. Update `docs/phases/SUMMARY.md` deliverable summary.
4. Update master blueprint `XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md`.
5. Update downstream dependency statements and pre-conditions.
6. Record authoritative commit SHAs, PR numbers, and automated test suite evidence.
7. Preserve historical frozen specifications verbatim under explicit historical headings.
8. Never mark a phase complete based on a plan or candidate branch alone.

---

## 5. Technical Stack & Governance

- **Backend Framework:** Django 5.2 LTS (Python 3.13)
- **Task Queue & Cache:** Celery 5.x + Redis (5 dedicated priority queues)
- **Database:** PostgreSQL 16 (JSONB, append-only immutable audit logs)
- **Mathematical Engine:** Pure Python (`numpy`, `scipy`, `pandas` — zero Django/ORM dependencies in `engine/`)
- **Documentation Roadmap:** [`docs/phases/README.md`](./docs/phases/README.md)
- **Deliverables Summary:** [`docs/phases/SUMMARY.md`](./docs/phases/SUMMARY.md)
- **Active Master Blueprint:** [`XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md`](./XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md)
- **Historical XAUT Blueprint:** [`XAUT_Signal_Intelligence_Blueprint_Django_Python.md`](./XAUT_Signal_Intelligence_Blueprint_Django_Python.md) (Frozen audit baseline)

---

> [!IMPORTANT]
> **GOVERNANCE & PRODUCTION AUTHORITY NOTICE:**  
> Phase 5 is completed and merged into `main`. However, live production BUY/SELL authority remains **NOT AUTHORIZED** (`is_production_authorized = False`; published user decision held at `WAIT`). Automated order execution is forbidden. Phase 6 empirical backtesting and walk-forward validation are mandatory prerequisites prior to live paper observation (Phase 8).
