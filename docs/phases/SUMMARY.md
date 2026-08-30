# AurumIQ — Summary of Phased Deliverables

> **Scope:** Multi-Timeframe Quantitative Gold Intelligence Platform  
> **Target Instrument:** `XAU/USD` (Canonical: `XAUUSD`)  
> **Historical Baseline:** `XAUT` (Tether Gold) verified and frozen for baseline audit continuity.

---

## 1. Comprehensive Phase Status Index

| Phase Document | Primary Deliverable | Historical XAUT Status | Current XAUUSD Target Status |
|---|---|:---:|:---:|
| [**PHASE 0**](./PHASE_0_FOUNDATION.md) | Foundation, PostgreSQL, Celery, RBAC, Protocols | ✅ `VERIFIED / FROZEN` | 🟢 `REUSABLE` |
| [**PHASE 1**](./PHASE_1_DATA_ENGINE.md) | Ingestion Pipeline, Multi-Provider Normalization | ✅ `VERIFIED / FROZEN` | 🟡 `MIGRATION REQUIRED` |
| [**PHASE 2**](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Indicators, Market Regimes, Causal Swings | ✅ `VERIFIED / FROZEN` | 🟡 `REVALIDATION REQUIRED` |
| [**PHASE 3A**](./PHASE_3A_ROBUST_TIME_CYCLE.md) | DST Sessions, Swing Maturity, Macro Blackout Gate | ✅ `VERIFIED / FROZEN` | 🟡 `EMPIRICAL REBUILD REQUIRED` |
| [**PHASE 3B**](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Spectral Cycles (ACF, FFT, Wavelet, Hilbert) | ✅ `VERIFIED / FROZEN` | 🟡 `REVALIDATION REQUIRED (WEIGHT = 0.0)` |
| [**PHASE 4**](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Direction/Timing Scores, State Machine, Fingerprinting | ✅ `VERIFIED / FROZEN` (Long) | 🔴 `DUAL-SIDE REDESIGN REQUIRED (NOT IMPLEMENTED)` |
| [**PHASE 5**](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Risk Engine, Side-Aware Stops/Targets, Intrabar Replay | ✅ `VERIFIED / FROZEN` (Long) | 🔴 `LONG / SHORT REDESIGN REQUIRED (NOT IMPLEMENTED)` |
| [**PHASE 6**](./PHASE_6_BACKTEST_VALIDATION.md) | PIT Backtesting, Walk-Forward Purge/Embargo | ✅ `VERIFIED / FROZEN` | 🟡 `XAUUSD PIT BACKTEST REQUIRED` |
| [**PHASE 6**](./PHASE_6_BACKTESTING_ABLATION.md) | Automated Component Ablation Lab | ✅ `VERIFIED / FROZEN` | 🟡 `XAUUSD PIT BACKTEST REQUIRED` |
| [**PHASE 7**](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Dashboard UI, LiveMonitor, Informational Alerts | ✅ `VERIFIED / FROZEN` | ⏸️ `PRODUCT COMPLETION PAUSED` |
| [**PHASE 8**](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | Live Paper Observation, 3-Tier Parity Auditing | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |
| [**PHASE 9**](./PHASE_9_ML_META_FILTER.md) | ML Meta-Filter, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |

---

## 2. Key Architectural Invariants

1. **Pure Engine Isolation:** The mathematical calculation core (`engine/`) has zero dependencies on Django ORM, Celery, Redis, or Channels.
2. **Two-Path Invariant:** Live streaming quote presentation (Path A) is strictly decoupled from closed-candle decision scoring and persistence (Path B).
3. **Decoupled Risk Planning:** Phase 4 emits candidate signal states; Phase 5 independently evaluates structural and volatility risk.
4. **One Engine Rule:** Backtesting, paper trading observation, and live monitoring resolve the exact same pure-Python calculation engine.
5. **Zero Real-Order Execution Policy:** The codebase contains zero exchange trading keys, order dispatch endpoints, or testnet trading capabilities.
