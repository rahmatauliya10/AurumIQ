# AurumIQ — Master Phased Implementation Roadmap

> **Target Instrument Scope:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD)  
> **Historical Baseline:** `XAUT` (Tether Gold) historical baseline verified, frozen, and permanently retained for audit integrity.  
> **User Decision Scope:** `BUY / WAIT / SELL` (Human decision support only — zero automated order execution).

---

## 1. Dual Status Master Index

To preserve audit integrity, this index records both the **Historical XAUT Baseline Status** (which verified core algorithmic infrastructure) and the **Current XAUUSD Target Status** (which governs active platform scope).

| Phase Document | Focus Area | Historical XAUT Status | Current XAUUSD Target Status |
|---|---|:---:|:---:|
| [**PHASE 0: Foundation**](./PHASE_0_FOUNDATION.md) | Django 5.2, PostgreSQL, Celery, RBAC, Protocols | ✅ `VERIFIED / FROZEN` | 🟢 `REUSABLE` |
| [**PHASE 1: Ingestion Engine**](./PHASE_1_DATA_ENGINE.md) | Ingestion, Multi-Provider Data, Health Lifecycle | ✅ `VERIFIED / FROZEN` | 🟡 `MIGRATION REQUIRED` |
| [**PHASE 2: Indicators & Regimes**](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Indicators, Market Regimes, Causal Swings | ✅ `VERIFIED / FROZEN` | 🟡 `REVALIDATION REQUIRED` |
| [**PHASE 3A: Robust Cycles**](./PHASE_3A_ROBUST_TIME_CYCLE.md) | DST Sessions, Swing Maturity, Macro Blackout Gate | ✅ `VERIFIED / FROZEN` | 🟡 `EMPIRICAL REBUILD REQUIRED` |
| [**PHASE 3B: Experimental Cycles**](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Spectral Cycles (ACF, FFT, Wavelet, Hilbert) | ✅ `VERIFIED / FROZEN` | 🟡 `REVALIDATION REQUIRED (WEIGHT = 0.0)` |
| [**PHASE 4: State Machine**](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Direction/Timing Scores, State Machine, Fingerprint | ✅ `VERIFIED / FROZEN` (Long) | 🔴 `DUAL-SIDE REDESIGN REQUIRED (NOT IMPLEMENTED)` |
| [**PHASE 5: Risk Engine**](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Risk Planning, Side-Aware Stops/Targets, Intrabar Replay | ✅ `VERIFIED / FROZEN` (Long) | 🔴 `LONG / SHORT REDESIGN REQUIRED (NOT IMPLEMENTED)` |
| [**PHASE 6: Backtest Lab**](./PHASE_6_BACKTEST_VALIDATION.md) | PIT Backtesting, Walk-Forward Purge/Embargo | ✅ `VERIFIED / FROZEN` | 🟡 `XAUUSD PIT BACKTEST REQUIRED` |
| [**PHASE 6: Backtest Ablation**](./PHASE_6_BACKTESTING_ABLATION.md) | Automated Component Ablation Lab | ✅ `VERIFIED / FROZEN` | 🟡 `XAUUSD PIT BACKTEST REQUIRED` |
| [**PHASE 7: LiveMonitor & Alerts**](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Dashboard UI, LiveMonitor, Informational Alerts | ✅ `VERIFIED / FROZEN` | ⏸️ `PRODUCT COMPLETION PAUSED` |
| [**PHASE 8: Live Paper Observation**](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | Live Paper Observation, 3-Tier Parity Auditing | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |
| [**PHASE 9: ML Meta-Filter**](./PHASE_9_ML_META_FILTER.md) | ML Meta-Filter, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION` |

---

## 2. Global Constraints (R1 – R20)

| Rule | Category | Constraint Description |
|---|---|---|
| **R1** | **Safety** | **Zero Exchange Trading Access:** No exchange private keys, order endpoints, or testnet trading allowed in codebase. |
| **R2** | **Parity** | **One Engine Rule:** Backtest, paper observation, and live monitors resolve the exact same pure-Python calculation engine. |
| **R3** | **Causality** | **Closed Candle Decisions:** Operational decisions occur strictly on closed candles (15m, 1H, 4H, 1D). 1m/5m data is isolated for simulation/replay. |
| **R4** | **Data Integrity** | **Fail-Closed Macro Blackout:** When high-impact macro news is active or feeds are missing, system transitions to `FORCE_WAIT`. |
| **R5** | **Auditability** | **Canonical SHA-256 Fingerprinting:** Every signal state must generate an immutable hash of its input features and parameters. |
| **R6** | **Security** | **Effective Admin Invariant:** At least one active administrator (`is_active=True` and (`is_superuser=True` or `role=ADMIN`)) must always exist. |
| **R7** | **Durability** | **Protected Audit Trails:** Audit log foreign keys use `on_delete=models.PROTECT`. Hard deletion is strictly forbidden. |
| **R8** | **Causality** | **Look-Ahead Prevention:** Historical backtest replay at $T$ strictly masks all market data with $t > T$. |
| **R9** | **Architecture** | **Engine Purity:** The `engine/` package has zero dependencies on Django ORM, Celery, Redis, or Channels. |
| **R10** | **Governance** | **Immutable Snapshots:** Signals and risk plans are persisted append-only in PostgreSQL; historical records are never updated. |
| **R11** | **Validation** | **Walk-Forward Splitting:** Backtest evaluation utilizes chronological folds with dependency purging and post-boundary embargo. |
| **R12** | **Risk** | **Side-Aware Risk Planning:** Long and Short setups enforce structural invalidation stops, ATR guards, and minimum RR boundaries. |
| **R13** | **Execution** | **Causal Execution Timestamp:** Simulated order fills occur at $t \ge t_{\text{signal}} + \text{latency}$; fill on signal close is impossible. |
| **R14** | **Intrabar** | **Conservative Ambiguity Resolution:** Ambiguous intrabar candles resolve via chronological lower-TF replay or fall back to SL-first. |
| **R15** | **Costs** | **Deduplicated Friction Accounting:** Bid-ask spreads are never double-counted when actual executable quotes are available. |
| **R16** | **Statistics** | **Minimum Sample Guard:** Features or patterns with effective sample size $n_{eff} < 30$ receive zero active weight. |
| **R17** | **Cycles** | **Phase 3B Production Lock:** Experimental spectral features have an active production scoring weight locked to `0.0`. |
| **R18** | **Policy** | **TradingView Scraping Ban:** Scraping TradingView or using it as a calculation engine data source is strictly forbidden. |
| **R19** | **ML Protocol** | **Secondary Meta-Filtering Only:** ML models act purely as secondary probability filters on deterministic candidate setups. |
| **R20** | **Rollback** | **Deterministic Rollback:** ML models support instant zero-downtime rollback to rule-only baseline without server restart. |

---

## 3. Two Separate Taxonomies

### A. Repository Terminology Audit (Taxonomy A)
- `LEGACY`: Historical XAUTUSDT candle stores, USDT/USD rate providers, baseline basis calculation tables.
- `KEEP_GENERIC`: `CandleRepository`, `MarketDataProvider`, `Timeframe`, math statistical libraries, pure engine protocols.
- `MIGRATE`: Primary operational specifications in `./docs/phases/` and root `README.md`.
- `REMOVE`: Deprecated on-chain Ethereum redemption assertions from active operational specs.

### B. Acceptance-Test Migration Matrix (Taxonomy B)
- `LEGACY_XAUT`: Tests validating historical USDT/USD normalization formula (`A21`).
- `KEEP_GENERIC`: Multi-timeframe repository ordering, swing detection causality, mathematical indicator parity (`A01`, `A02`, `A03`, `A05`, `A08`, `A11`, `A12`, `A13`, `A16`, `A18`, `A19`, `A20`, `A24`, `A25`, `A26`, `A27`, `A29`, `A30`, `A31`, `A33`, `A35`, `A36`, `A37`, `A38`, `A40`, `A41`, `A42`, `A43`, `A44`, `A45`, `A46`, `A47`).
- `MODIFY_FOR_XAUUSD`: Live monitor quotes, provider health thresholds, DXY macro correlation feeds, backtest engine parity (`A04`, `A06`, `A07`, `A09`, `A10`, `A14`, `A15`, `A22`, `A23`, `A28`, `A32`, `A34`, `A39`, `A39X`).
- `REPLACE_FOR_XAUUSD`: `A17` (Historical XAUT/XAU basis integrity active contract replaced by XAUUSD provider-integrity contract).
- `NEW_XAUUSD` (Planned Future Contracts): Approved contracts for XAUUSD scope (`XAU-P1-01`, `XAU-P1-02`, `XAU-P2-01`, `XAU-P4-01`, `XAU-P4-02`, `XAU-P4-03`, `XAU-P4-04`, `XAU-P5-01`, `XAU-P5-02`, `XAU-P5-03`, `XAU-P6-01`, `XAU-P6-02`, `XAU-P6-03`, `XAU-P7-01`, `XAU-P8-01`, `XAU-P9-01`).
