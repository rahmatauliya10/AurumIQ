# Phased Implementation Documentation

> **Directory:** `docs/phases/`  
> **Total Documents:** 12 Detailed Markdown Plans + Master README + Summary

---

## Deliverables & Execution Progress Summary

| Document | File Path | Focus | Status |
|---|---|---|:---:|
| **Master Index** | [`docs/phases/README.md`](./README.md) | Phase Index, Roadmap & 20 Global Constraints (`R1–R20`) | 🟢 Updated |
| **Phase 0** | [`docs/phases/PHASE_0_FOUNDATION.md`](./PHASE_0_FOUNDATION.md) | Django 5.2 LTS, Celery 5 Queues, Docker Stack, RBAC, Protocol Boundary | ✅ **FROZEN (`f3f8bbb2ab`)** |
| **Phase 1** | [`docs/phases/PHASE_1_DATA_ENGINE.md`](./PHASE_1_DATA_ENGINE.md) | 3-Tier Domain, Provider Abstraction, Market Integrity, USDT Normalization, 1m/5m Replay Data | ✅ **FROZEN (`6bfb233e61`)** |
| **Phase 2** | [`docs/phases/PHASE_2_INDICATORS_REGIME_STRUCTURE.md`](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Indicators, Causal Regimes, ZigZag Swings, Sample Guard (Normalized HHI) | ✅ **VERIFIED (68/68 Tests)** |
| **Phase 3A** | [`docs/phases/PHASE_3A_ROBUST_TIME_CYCLE.md`](./PHASE_3A_ROBUST_TIME_CYCLE.md) | Session Cycle (DST-aware), Swing Duration, Event PiT Gate (Revision-safe), Calendar Seasonality | 📋 Next Scope |
| **Phase 3B** | [`docs/phases/PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md`](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Causal ACF, FFT, Wavelet CWT, Hilbert Phase, Multi-Criteria Promotion Gate | 📋 Planned |
| **Phase 4** | [`docs/phases/PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md`](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Direction Score, Timing Score, State Machine, Explainer, Idempotent Analysis Tasks | 📋 Planned |
| **Phase 5** | [`docs/phases/PHASE_5_RISK_ENGINE_EXECUTION.md`](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Structure/ATR Stops, TP1/TP2, Intrabar Resolver (1m/5m), Causal Post-Signal Fill Model | 📋 Planned |
| **Phase 6** | [`docs/phases/PHASE_6_BACKTESTING_ABLATION.md`](./PHASE_6_BACKTESTING_ABLATION.md) | Point-in-Time Replay, Walk-Forward Splits, Purge/Embargo, Cost Simulator, Automated Ablation | 📋 Planned |
| **Phase 7** | [`docs/phases/PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md`](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Django Dashboard, Plotly Charts, LiveMonitor (WebSocket/Redis TTL), DRF API, Alerts | 📋 Planned |
| **Phase 8** | [`docs/phases/PHASE_8_LIVE_PAPER_OBSERVATION.md`](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | Immutable Live Signals, Triple-Barrier Outcome Tracking, Live vs Backtest Parity Audit | 📋 Planned |
| **Phase 9** | [`docs/phases/PHASE_9_ML_META_FILTER.md`](./PHASE_9_ML_META_FILTER.md) | Candidate PiT Dataset, Logistic / XGBoost / LightGBM Meta-Filter, Probability Calibration | 📋 Planned |
