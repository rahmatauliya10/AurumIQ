# AurumIQ — Multi-Timeframe Quantitative Gold Intelligence Platform

> **Target Instrument Scope:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD)
> **Historical Baseline:** `XAUT` (Tether Gold) historical baseline verified, frozen, and permanently retained for audit integrity.
> **User Decision Scope:** `BUY / WAIT / SELL` (Human decision support only — zero automated order execution).
> **Current Authoritative Main SHA:** `fcbe1a934d9ac125426ec6c64c77f078e0bb7df5` (PR #21 Standard Cent Scope Merged; Post-Merge CI Green)
> **Calibration Status:** `READINESS_GATE = CANDLES_READY_EMPIRICAL_FRICTION_MISSING` (`passed = False`, `is_production_authorized = False`, `production_weight = 0.0`, `decision = WAIT`)

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
│  - Stale Feed Guard (Configurable)              - Structural + ATR Stop │
│  - Zone Proximity Alerts                        - Unrounded RR Gate     │
│  - Zero Execution Code                          - Intrabar 1m/5m Replay │
└─────────────────────────────────────────────────────────────────────────┘
```

### Core Operating Invariants
1. **Zero Real-Order Execution Policy (R1):** The platform contains zero exchange trading keys, broker execution bindings, order dispatch endpoints, or testnet trading capabilities. All outputs are strictly decision support.
2. **Decoupled Two-Path Architecture:**
   - **Path A (Live Streaming Quotes):** Low-latency WebSockets and Redis TTL updating presentation metrics and live zone proximity monitoring (`livequote:XAUUSD`).
   - **Path B (Closed Candles Decision Pipeline):** Strict closed-bar signal evaluation, immutable fingerprint generation, and PostgreSQL persistence on completed 15m, 1H, 4H, and 1D candles.
3. **Analytical vs Execution Boundary:**
   - **Analytical Market Data:** Derived exclusively from the primary market-data feed (Twelve Data) for canonical `XAUUSD`.
   - **Execution & Friction Evidence:** Broker-specific evidence (Exness) is strictly partitioned into execution profiles and never contaminates analytical candles.
4. **Independent Execution Profile Scopes & Artifact Isolation:**
   - Supported account tiers: `STANDARD`, `STANDARD_CENT`, `RAW_SPREAD`.
   - Strict decoupling: `account_tier != broker_symbol != account_currency`.
   - Broker execution symbols are explicit scope (e.g. `STANDARD + expected_broker_symbol=XAUUSDm`, `STANDARD_CENT + expected_broker_symbol=XAUUSDc`). No hidden inference defaults exist.
   - Account currency is explicit declared scope (`USD`, `USC`), carries zero evidence qualification authority, and introduces no conversion engine.
   - Canonical manifest `artifacts/calibration/xauusd_empirical_friction_manifest.json` represents the `STANDARD` account tier evidence scope. `STANDARD_CENT` artifacts are strictly isolated under separate filenames and cannot cross-contaminate.
   - Slippage telemetry for calibration resolves strictly from authentic broker execution fill telemetry, eliminating circular dependency on Phase 8 paper observation.
5. **Intrabar Replay Segregation:** 1m and 5m streams are strictly isolated for causal fill simulation, execution latency, and intrabar barrier collision resolution during backtesting and forward paper observation.
6. **TradingView Policy (R18 & A18):** TradingView is permitted exclusively for external visual reference or rendering via Lightweight Charts. The calculation engine contains zero scraping dependencies or network calls to TradingView.
7. **Production Publication Authority Invariant:** Even with Phase 4, Phase 5, Phase 6, Phase 7, and calibration architecture (PR #20, PR #21) merged, Layer B published user decision remains strictly `WAIT` (`is_production_authorized = False`). Live execution authority is blocked by hard readiness gate `CANDLES_READY_EMPIRICAL_FRICTION_MISSING`.
8. **Position Sizing Boundary:** Position sizing is strictly out of scope for all completed phases.

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
| **Phase 6** | PIT Backtesting, Walk-Forward Validation & Component Ablation | ✅ `VERIFIED / FROZEN` | ✅ `COMPLETED & VERIFIED (MERGED PR #14 @ dab3b6f)` |
| **Phase 7** | Dashboard UI, LiveMonitor, Multi-Timeframe Charts, Alerts | ✅ `VERIFIED / FROZEN` | ✅ `COMPLETED & VERIFIED (MERGED PR #15 @ 57f6de1)` |
| **Calibration** | Empirical Friction & Readiness Governance | ⚪ `N/A` | 🟡 `ARCHITECTURE SEALED (PR #20 @ 92b0bd6, PR #21 @ fcbe1a9; GATE: CANDLES_READY_EMPIRICAL_FRICTION_MISSING)` |
| **Phase 8** | Live Paper Observation, 3-Tier Parity Auditing (BUY/SELL/Combined) | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION (BLOCKED BY CALIBRATION GATE)` |
| **Phase 9** | ML Meta-Filter, Side-Aware Labels, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION (DEPENDS ON PHASE 8 STABILITY)` |

---

## 3. Contract Taxonomy & Verification Matrix

### A. Verified XAUUSD Operational Contracts
- **Phase 1 Contracts:** `XAU-P1-01` (canonical primary XAUUSD target), `XAU-P1-02` (integrated multi-source ingestion integrity).
- **Phase 2 Contracts:** `XAU-P2-01` (explicit volume evidence semantics: `REAL_VOLUME`, `TICK_VOLUME`, `PROXY_VOLUME`, `UNAVAILABLE`).
- **Phase 3A Contracts:** DST-aware session windows, knowable swing age maturity, revision-safe macro blackout.
- **Phase 3B Contracts:** Experimental spectral cycle engine (ACF, FFT, Wavelet, Hilbert) with hard-locked `production_weight = 0.0`.
- **Phase 4 Contracts:** `XAU-P4-01` (`BUY_WINDOW` $\rightarrow$ `BUY`), `XAU-P4-02` (`SELL_WINDOW` $\rightarrow$ `SELL`), `XAU-P4-03` (`CONFLICT` $\rightarrow$ `WAIT`), `XAU-P4-04` (`SYSTEM_SAFETY_HOLD` $\rightarrow$ `WAIT`). Baseline SHA: `b619a140391e5e308241246e105b9767a1b0716d`.
- **Phase 5 Contracts:** `XAU-P5-01` (LONG side-aware risk planning), `XAU-P5-02` (SHORT side-aware risk planning), `XAU-P5-03` (side-aware market bid/ask causal execution). Hostile matrix `H1`–`H74` fully verified. Merge SHA: `9011764958d31c5e96860488da7c54568def1352`.
- **Phase 6 Contracts:** `XAU-P6-01` (LONG point-in-time backtest replay), `XAU-P6-02` (SHORT point-in-time backtest replay), `XAU-P6-03` (combined side-aware parity / reporting & ablation). Merge SHA: `dab3b6f8999bcef537bf4d8450f774ce36eb8e0f` (PR #14).
- **Phase 7 Contracts:** `XAU-P7-01` (BUY / WAIT / SELL presentation and dual-side alerting). Merge SHA: `57f6de1405d0df8548182a166d245f1a3173363d` (PR #15).
- **Calibration Governance & Scope Contracts:**
  - `XAU-CAL-01`: Macro blackout event evidence qualification and revision-safe ingestion (PR #19 @ `06425ba`).
  - `XAU-CAL-02`: Six-category empirical friction qualification architecture without silent defaults (PR #20 @ `92b0bd6`).
  - `XAU-CAL-03`: Isolated Exness Standard Cent execution profile scope with fail-closed symbol binding and zero cross-profile contamination (PR #21 @ `fcbe1a9`).

### B. Planned Future XAUUSD Contracts
- **Phase 8 Planned Contracts:** `XAU-P8-01` (forward paper execution tracking and 14-day operational stability audit — blocked until empirical friction evidence is qualified).
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
9. Clearly distinguish historical phase merge SHAs from current authoritative `main` SHA.

---

## 5. Technical Stack & Governance

- **Backend Framework:** Django 5.2 LTS
- **Python:**
  - `>= 3.12` supported by `pyproject.toml`
  - `3.12` local/developer pin via `.python-version`
  - `3.13` validated by GitHub CI
- **Task Queue & Cache:** Celery 5.x + Redis
- **Database:** PostgreSQL 16 (JSONB, append-only immutable audit logs)
- **Mathematical Engine:** Pure Python (`numpy`, `scipy`, `pandas` — zero Django/ORM dependencies in `engine/`)
- **Documentation Roadmap:** [`docs/phases/README.md`](./docs/phases/README.md)
- **Deliverables Summary:** [`docs/phases/SUMMARY.md`](./docs/phases/SUMMARY.md)
- **Active Master Blueprint:** [`XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md`](./XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md)

---

> [!IMPORTANT]
> **GOVERNANCE & PRODUCTION AUTHORITY NOTICE:**
> Phases 5, 6, and 7, as well as the empirical friction calibration architecture (PR #20) and isolated execution profiles (PR #21), are completed and merged into `main`. However, live production BUY/SELL authority remains **NOT AUTHORIZED** (`is_production_authorized = False`; published user decision held at `WAIT`). Automated order execution is strictly forbidden.
>
> Current blocking readiness gate:
> ```text
> READINESS_GATE = CANDLES_READY_EMPIRICAL_FRICTION_MISSING
> passed = False
> is_production_authorized = False
> production_weight = 0.0
> decision = WAIT
> ```
> Genuine empirical friction evidence (Legal Entity, Contract Specification, Fee Schedule, Financing Swap, Bid/Ask Tick Distribution, Execution Slippage Telemetry) must be ingested and qualified before Phase 8 Live Paper Observation may be authorized.
