# AurumIQ Signal Intelligence — Phased Execution Master Plan

> **Governing Blueprint:** `AurumIQ_Signal_Intelligence_Blueprint_Django_Python.md`  
> **Master Implementation Plan:** `v3.0.0 (XAUUSD Scope Migration Specification)`  
> **Primary Signal Target:** `XAUUSD` (Spot Gold / US Dollar)  
> **Historical Baseline:** `XAUT` (Tether Gold historical baseline preserved for audit continuity)  
> **System Scope:** Research & Decision-Support System (`BUY / WAIT / SELL`). **Strictly NO automated order execution.**

---

## Phase Index & Implementation Roadmap

Every phase is documented in its own dedicated, unabridged specification file. All development follows strict phased isolation: **ONE phase at a time**, verified against the Definition of Done, with zero lookahead bias and 100% test passing.

| Phase | Specification Document | Focus Area | Acceptance Tests | DoD Status |
|---|---|---|---|:---:|
| **Phase 0** | [`PHASE_0_FOUNDATION.md`](./PHASE_0_FOUNDATION.md) | Django 5.2 LTS, Celery 5 Queues, Docker Stack, Hardened RBAC, Protocol Boundary | Smoke Tests | ✅ **VERIFIED & FROZEN** |
| **Phase 1** | [`PHASE_1_DATA_ENGINE.md`](./PHASE_1_DATA_ENGINE.md) | 3-Tier Domain, Multi-Provider Ingestion, Market Integrity, Point-in-Time Normalization, 1m/5m Replay Data | A15, A17, A20, A21, P1-01..P1-09 | ✅ **VERIFIED & FROZEN** |
| **Phase 2** | [`PHASE_2_INDICATORS_REGIME_STRUCTURE.md`](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Python Indicators, Causal Regimes, ZigZag Swings, Sample Guard (Normalized HHI) | A01, A16, P2-01..P2-08 | ✅ **VERIFIED & FROZEN** |
| **Phase 3A** | [`PHASE_3A_ROBUST_TIME_CYCLE.md`](./PHASE_3A_ROBUST_TIME_CYCLE.md) | Session Cycle (DST-aware), Swing Duration, Event PiT Gate (Revision-safe), Calendar Seasonality | A02, A06, A26, P3A-01..P3A-14 | ✅ **VERIFIED & FROZEN** |
| **Phase 3B** | [`PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md`](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Causal ACF, FFT, Wavelet CWT, Hilbert Phase, Multi-Criteria Promotion Gate (Locked 0.0 Weight) | A05, A13, A24, P3B-01..P3B-16 | ✅ **VERIFIED & FROZEN** |
| **Phase 4** | [`PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md`](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Direction Score, Timing Score, State Machine (`BUY / WAIT / SELL`), Canonical Fingerprinting | A03, A04, A08, A23, P4-01..P4-22 | ✅ **VERIFIED & FROZEN** |
| **Phase 5** | [`PHASE_5_RISK_ENGINE_EXECUTION.md`](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Side-Aware Stops (Long/Short), TP1/TP2, Intrabar Resolver (1m/5m), Causal Post-Signal Fill Model | A07, A14, A19, A22, A25, A27, P5-01..P5-32B | ✅ **VERIFIED & FROZEN** |
| **Phase 6** | [`PHASE_6_BACKTEST_VALIDATION.md`](./PHASE_6_BACKTEST_VALIDATION.md) | Point-in-Time Replay, Walk-Forward Splits, Purge/Embargo, Cost Simulator, Automated Ablation | A09, A10, A31..A38, P6-01..P6-35 | ✅ **VERIFIED & FROZEN** |
| **Phase 7** | [`PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md`](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Django Dashboard, Plotly Charts, Live Two-Path Pipeline, WebSockets, Fail-Safe Recovery | A11, A12, A18, A28, A39..A45, P7-01..P7-27 | ✅ **VERIFIED & FROZEN** |
| **Phase 8** | [`PHASE_8_LIVE_PAPER_OBSERVATION.md`](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | XAUUSD BUY+SELL Live Paper Observation, Side-Aware Triple-Barrier Outcomes, Parity Auditing | Live Parity (BUY/SELL/Comb) | 📋 Planned |
| **Phase 9** | [`PHASE_9_ML_META_FILTER.md`](./PHASE_9_ML_META_FILTER.md) | XAUUSD Candidate PiT Dataset, Logistic / XGBoost / LightGBM Meta-Filter, Probability Calibration | ML Out-of-Sample | 📋 Planned |

---

## 20 Master Global Constraints (R1–R20)

1. **R1 — No trading execution:** Zero buy/sell/order/withdraw endpoints in any codebase component. Strictly decision support.
2. **R2 — One engine:** Live analysis and backtest call the identical pure-Python `SignalEngine`.
3. **R3 — Point-in-time correctness:** At timestamp $t$, no data after $t$ may be knowable or accessible.
4. **R4 — Closed-candle decisions:** Decisions evaluate closed 15m/1H/4H/1D candles. 1m/5m candles are strictly for intrabar replay/fill resolution. Live quotes are for visual monitoring only.
5. **R5 — Reproducibility:** Every signal records engine version, config version, feature version, code revision SHA, and canonical analysis fingerprint.
6. **R6 — Abstention is valid:** System is expected to return `WAIT` most of the time when market conditions are ambiguous.
7. **R7 — No 90% promise:** System optimizes positive expectancy, profit factor, controlled drawdown, and statistical robustness.
8. **R8 — Tests before completion:** No phase or task is complete until all automated unit and integration tests pass.
9. **R9 — Pure engine boundary:** `engine/*` must not import Django ORM, Celery, or Redis. `CandleRepository` is a pure `typing.Protocol`.
10. **R10 — Immutability:** Signal records, risk plans, and component snapshots are strictly append-only.
11. **R11 — UTC everywhere:** All timestamps are stored and manipulated in UTC; session conversions use standard library `zoneinfo`.
12. **R12 — Typed interfaces:** Fully typed functions and frozen dataclasses throughout the calculation engine.
13. **R13 — Versioned config:** All parameters live in versioned `EngineConfig` dataclasses.
14. **R14 — Multi-source abstraction:** Provider adapters with health snapshots and 5-point continuity verification.
15. **R15 — XAUUSD Primary Scope:** XAUUSD is the primary signal instrument; historical XAUT baseline is preserved for audit continuity.
16. **R16 — Statistical sample guard:** Setups with small samples ($n < 30$ or $n_{eff} < 30$) receive 0 weight; effective $N$ accounts for overlap and normalized HHI.
17. **R17 — Intrabar ambiguity:** Single-candle TP/SL collisions resolve via 1m/5m chronological replay or conservative `SL_FIRST`.
18. **R18 — TradingView isolation:** Zero scraping or engine calculation dependencies on TradingView.
19. **R19 — Side-Aware Dual Direction:** Direction engine and risk engine evaluate both `BUY` and `SELL` setups with independent structural stops and targets.
20. **R20 — Causal execution:** Backtest entry uses next-bar open or post-signal quote ($t \ge \text{signal\_ts} + \text{latency}$).

---

## 🏛️ Classification Taxonomies

### Taxonomy A: Repository Terminology Audit
- **`LEGACY`**: Historical XAUT data, initial schema migrations, baseline audit ledgers.
- **`KEEP_GENERIC`**: Instrument-agnostic protocol boundaries, base provider adapters, core mathematical utilities.
- **`MIGRATE`**: Active signal definitions, risk planning specifications, multi-timeframe regime analysis.
- **`REMOVE`**: Deprecated crypto token assumptions and unsupported on-chain dependencies in active specs.

### Taxonomy B: Acceptance Test Migration Matrix
- **`LEGACY_XAUT`**: Preserved test contracts verifying historical XAUT baseline integrity.
- **`KEEP_GENERIC`**: Core engine purity, timezone handling, sample guard, and math tests.
- **`MODIFY_FOR_XAUUSD`**: Target instrument updates in multi-provider ingestion and live monitoring tests.
- **`REPLACE_FOR_XAUUSD`**: Updated configuration and calibration fixtures tailored for XAUUSD spot volatility.
- **`NEW_XAUUSD`**: Dedicated test contracts for side-aware `SELL` setups, parity audits, and dual-direction meta-filtering.
