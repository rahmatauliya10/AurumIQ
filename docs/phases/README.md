# XAUT Signal Intelligence — Phased Execution Master Plan

> **Governing Blueprint:** `XAUT_Signal_Intelligence_Blueprint_Django_Python.md`  
> **Master Implementation Plan:** `v2.1.2 (Frozen Specification)`  
> **System Scope:** Research & Decision-Support System (`BUY / WAIT / AVOID`). **Strictly NO automated order execution.**

---

## Phase Index & Implementation Roadmap

Every phase is documented in its own dedicated, unabridged specification file. An agentic worker or developer must execute **ONE phase at a time**, verify the Definition of Done, ensure all unit/integration tests pass, and obtain human review before moving to the next phase.

| Phase | Specification Document | Focus Area | Acceptance Tests | DoD Status |
|---|---|---|---|---|
| **Phase 0** | [`PHASE_0_FOUNDATION.md`](./PHASE_0_FOUNDATION.md) | Django 5.2 LTS, Celery 5 Queues, Docker Stack, RBAC, Protocol Boundary | Smoke Tests | ✅ **VERIFIED** |
| **Phase 1** | [`PHASE_1_DATA_ENGINE.md`](./PHASE_1_DATA_ENGINE.md) | 3-Tier Domain, Provider Abstraction, Market Integrity, USDT Normalization, 1m/5m Replay Data | A15, A17, A20, A21 | ⏳ Ready to Start |
| **Phase 2** | [`PHASE_2_INDICATORS_REGIME_STRUCTURE.md`](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Python Indicators, Causal Regimes, ZigZag Swings, Sample Guard (Normalized HHI) | A01, A16 | 📋 Planned |
| **Phase 3A** | [`PHASE_3A_ROBUST_TIME_CYCLE.md`](./PHASE_3A_ROBUST_TIME_CYCLE.md) | Session Cycle (DST-aware), Swing Duration, Event PiT Gate (Revision-safe), Calendar Seasonality | A02, A06, A26 | 📋 Planned |
| **Phase 3B** | [`PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md`](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Causal ACF, FFT, Wavelet CWT, Hilbert Phase, Multi-Criteria Promotion Gate | A05, A13, A24 | 📋 Planned |
| **Phase 4** | [`PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md`](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Direction Score, Timing Score, State Machine, Explainer, Idempotent Analysis Tasks | A03, A04, A08, A23 | 📋 Planned |
| **Phase 5** | [`PHASE_5_RISK_ENGINE_EXECUTION.md`](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Structure/ATR Stops, TP1/TP2, Intrabar Resolver (1m/5m), Causal Post-Signal Fill Model | A07, A14, A19, A22, A25, A27 | 📋 Planned |
| **Phase 6** | [`PHASE_6_BACKTESTING_ABLATION.md`](./PHASE_6_BACKTESTING_ABLATION.md) | Point-in-Time Replay, Walk-Forward Splits, Purge/Embargo, Cost Simulator, Automated Ablation | A09, A10 | 📋 Planned |
| **Phase 7** | [`PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md`](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Django Dashboard, Plotly Charts, LiveMonitor (WebSocket/Redis TTL), DRF API, Alerts | A11, A12, A18, A28 | 📋 Planned |
| **Phase 8** | [`PHASE_8_LIVE_PAPER_OBSERVATION.md`](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | Immutable Live Signals, Triple-Barrier Outcome Tracking, Live vs Backtest Parity Audit | Live Parity | 📋 Planned |
| **Phase 9** | [`PHASE_9_ML_META_FILTER.md`](./PHASE_9_ML_META_FILTER.md) | Candidate PiT Dataset, Logistic / XGBoost / LightGBM Meta-Filter, Probability Calibration | ML Out-of-Sample | 📋 Planned |

---

## 20 Master Global Constraints (R1–R20)

1. **R1 — No trading execution:** Zero buy/sell/order/withdraw endpoints in V1-V2.
2. **R2 — One engine:** Live analysis and backtest call the same pure-Python `XautSignalEngine`.
3. **R3 — Point-in-time correctness:** At timestamp $t$, no data after $t$ may be knowable.
4. **R4 — Closed-candle decisions:** Decisions on closed 15m/1H/4H/1D candles. Live quotes are for monitoring only.
5. **R5 — Reproducibility:** Every signal records engine, config, feature version, code revision SHA, and analysis fingerprint.
6. **R6 — Abstention is valid:** System is allowed and expected to return WAIT/AVOID most of the time.
7. **R7 — No 90% promise:** Optimize expectancy, profit factor, drawdown, and robustness.
8. **R8 — Tests before features:** Phase incomplete until automated tests pass.
9. **R9 — Dependency rule:** `engine/*` must not import Django ORM. `CandleRepository` is pure `typing.Protocol`.
10. **R10 — Immutability:** Signal records and components are append-only.
11. **R11 — UTC everywhere:** Market timestamps stored in UTC; session converted via `zoneinfo`.
12. **R12 — Typed interfaces:** Fully typed functions and frozen dataclasses.
13. **R13 — Versioned config:** All parameters live in versioned `EngineConfig`.
14. **R14 — Multi-source abstraction:** Provider adapters with health snapshots and 5-point continuity verification.
15. **R15 — XAUT ≠ Gold:** XAUT is execution instrument; XAU/USD is directional confirmation.
16. **R16 — Statistical sample guard:** Patterns with small samples ($N < 30$) receive 0 weight; effective $N$ with normalized HHI.
17. **R17 — Intrabar ambiguity:** Single-candle TP/SL collisions resolve via 1m/5m replay or conservative `SL_FIRST`.
18. **R18 — TradingView isolation:** Zero scraping or engine dependencies on TradingView.
19. **R19 — Quote normalization:** $XAUT_{USDT}$ normalized by $USDT/USD$ before comparing with $XAU/USD$. Two-way peg monitoring.
20. **R20 — Causal execution:** Backtest entry uses next-bar open or post-signal quote ($t \ge \text{signal\_ts} + \text{latency}$).
