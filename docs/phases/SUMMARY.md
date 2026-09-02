# AurumIQ — Summary of Phased Deliverables

> **Scope:** Multi-Timeframe Quantitative Gold Intelligence Platform  
> **Target Instrument:** `XAU/USD` (Canonical: `XAUUSD`)  
> **Historical Baseline:** `XAUT` (Tether Gold) verified and frozen for baseline audit continuity.  
> **Authoritative Main Baseline:** `9011764958d31c5e96860488da7c54568def1352` (Phase 5 Merged via PR #12)

---

## 1. Comprehensive Phase Status Index

| Phase Document | Primary Deliverable | Historical XAUT Status | Current XAUUSD Target Status |
|---|---|:---:|:---:|
| [**PHASE 0**](./PHASE_0_FOUNDATION.md) | Foundation Architecture, PostgreSQL, Celery, RBAC, Protocols | ✅ `VERIFIED / FROZEN` | 🟢 `REUSABLE` |
| [**PHASE 1**](./PHASE_1_DATA_ENGINE.md) | Ingestion Pipeline, Multi-Provider Normalization, Health Lifecycle | ✅ `VERIFIED / FROZEN` | 🟢 `CORE MIGRATION IMPLEMENTED (BINDING + EMPIRICAL THRESHOLDS PENDING)` |
| [**PHASE 2**](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Indicators, Market Regimes, Causal Swings, Volume Semantics | ✅ `VERIFIED / FROZEN` | 🟡 `CORE ARCHITECTURE IMPLEMENTED (EMPIRICAL THRESHOLDS NOT FROZEN)` |
| [**PHASE 3A**](./PHASE_3A_ROBUST_TIME_CYCLE.md) | DST Sessions, Swing Maturity, Macro Blackout Gate | ✅ `VERIFIED / FROZEN` | 🟡 `ARCHITECTURE IMPLEMENTED (CALIBRATION PENDING_DATA)` |
| [**PHASE 3B**](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Spectral Cycles (ACF, FFT, Wavelet, Hilbert) | ✅ `VERIFIED / FROZEN` | 🧪 `IMPLEMENTED / RESEARCH ONLY (PRODUCTION WEIGHT = 0.0)` |
| [**PHASE 4**](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Dual-Side Direction/Timing Scores, State Machine, Fingerprints | ✅ `VERIFIED / FROZEN` (Long) | ✅ `COMPLETED & VERIFIED (SEALED PHASE 4 BASELINE)` |
| [**PHASE 5**](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Side-Aware Risk Planning, Causal Execution, Intrabar Resolver | ✅ `VERIFIED / FROZEN` (Long) | ✅ `COMPLETED & VERIFIED (MERGED PR #12 @ 9011764)` |
| [**PHASE 6**](./PHASE_6_BACKTEST_VALIDATION.md) | PIT Backtesting, Walk-Forward Validation & Ablation | ✅ `VERIFIED / FROZEN` | ✅ `COMPLETED & VERIFIED (MERGED PR #14 @ dab3b6f)` |
| [**PHASE 7**](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Dashboard UI, LiveMonitor, Multi-Timeframe Charts, Alerts | ✅ `VERIFIED / FROZEN` | ✅ `COMPLETED & VERIFIED (MERGED PR #15 @ 57f6de1)` |
| [**PHASE 8**](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | Live Paper Observation, 3-Tier Parity Auditing (BUY/SELL/Combined) | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |
| [**PHASE 9**](./PHASE_9_ML_META_FILTER.md) | ML Meta-Filter, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |

---

## 2. Key Architectural Invariants

1. **Pure Engine Isolation:** The mathematical calculation core (`engine/`) has zero dependencies on Django ORM, Celery, Redis, or Channels.
2. **Two-Path Invariant:** Live streaming quote presentation (Path A) is strictly decoupled from closed-candle decision scoring and persistence (Path B).
3. **Decoupled Risk Planning:** Phase 4 emits candidate signal states; Phase 5 independently evaluates structural and volatility risk and may demote candidates to `WAIT`, but never promote `WAIT` to `BUY`/`SELL`.
4. **Publication Authority Guard:** Even after Phase 5 verification, Layer B publication user decision remains strictly `WAIT` (`is_production_authorized = False`).
5. **One Engine Rule:** Backtesting, paper trading observation, and live monitoring resolve the exact same pure-Python calculation engine.
6. **Zero Real-Order Execution Policy:** The codebase contains zero exchange trading keys, order dispatch endpoints, or testnet trading capabilities.
7. **Position Sizing Boundary:** Position sizing is strictly out of scope for Phase 5 and Phase 6.
