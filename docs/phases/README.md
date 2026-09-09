# AurumIQ — Master Phased Implementation Roadmap

> **Target Instrument Scope:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD)
> **Historical Baseline:** `XAUT` (Tether Gold) historical baseline verified, frozen, and permanently retained for audit integrity.
> **User Decision Scope:** `BUY / WAIT / SELL` (Human decision support only — zero automated order execution).
> **Last Verified Baseline SHA:** `2ee19143aa98ccf15f25b1dec0ec4c2f2fc0bfc0` (PR #22 Documentation Truth Sealed & Merged; Post-Merge CI Green)
> **Calibration Status:** `READINESS_GATE = CANDLES_READY_EMPIRICAL_FRICTION_MISSING` (`passed = False`, `is_production_authorized = False`, `production_weight = 0.0`, `decision = WAIT`)

---

## 1. Dual Status Master Index

To preserve audit integrity, this index records both the **Historical XAUT Baseline Status** (which verified core algorithmic infrastructure) and the **Current XAUUSD Target Status** (which governs active platform scope).

| Phase Document | Focus Area | Historical XAUT Status | Current XAUUSD Target Status |
|---|---|:---:|:---:|
| [**PHASE 0: Foundation**](./PHASE_0_FOUNDATION.md) | Django 5.2, PostgreSQL, Celery, RBAC, Protocols | ✅ `VERIFIED / FROZEN` | 🟢 `REUSABLE` |
| [**PHASE 1: Ingestion Engine**](./PHASE_1_DATA_ENGINE.md) | Ingestion, Multi-Provider Data, Health Lifecycle | ✅ `VERIFIED / FROZEN` | 🟢 `CORE MIGRATION IMPLEMENTED (BINDING + EMPIRICAL THRESHOLDS PENDING)` |
| [**PHASE 2: Indicators & Regimes**](./PHASE_2_INDICATORS_REGIME_STRUCTURE.md) | Pure Indicators, Market Regimes, Causal Swings | ✅ `VERIFIED / FROZEN` | 🟡 `CORE ARCHITECTURE IMPLEMENTED (EMPIRICAL THRESHOLDS NOT FROZEN)` |
| [**PHASE 3A: Robust Cycles**](./PHASE_3A_ROBUST_TIME_CYCLE.md) | DST Sessions, Swing Maturity, Macro Blackout Gate | ✅ `VERIFIED / FROZEN` | 🟡 `ARCHITECTURE IMPLEMENTED (CALIBRATION PENDING_DATA)` |
| [**PHASE 3B: Experimental Cycles**](./PHASE_3B_EXPERIMENTAL_TIME_CYCLE.md) | Spectral Cycles (ACF, FFT, Wavelet, Hilbert) | ✅ `VERIFIED / FROZEN` | 🧪 `IMPLEMENTED / RESEARCH ONLY (PRODUCTION WEIGHT = 0.0)` |
| [**PHASE 4: State Machine**](./PHASE_4_DIRECTION_TIMING_STATE_MACHINE.md) | Direction/Timing Scores, State Machine, Fingerprint | ✅ `VERIFIED / FROZEN` (Long) | ✅ `COMPLETED & VERIFIED (SEALED PHASE 4 BASELINE)` |
| [**PHASE 5: Risk Engine**](./PHASE_5_RISK_ENGINE_EXECUTION.md) | Risk Planning, Side-Aware Stops/Targets, Intrabar Replay | ✅ `VERIFIED / FROZEN` (Long) | ✅ `COMPLETED & VERIFIED (MERGED PR #12 @ 9011764)` |
| [**PHASE 6: Backtest Validation & Ablation**](./PHASE_6_BACKTEST_VALIDATION.md) | PIT Backtesting, Walk-Forward Validation & Ablation | ✅ `VERIFIED / FROZEN` | ✅ `COMPLETE & STRUCTURALLY SEALED (PR #14 @ dab3b6f; PR #29 @ 74985e2; FREEZE: HOLD)` |
| [**PHASE 7: LiveMonitor & Alerts**](./PHASE_7_DASHBOARD_LIVEMONITOR_ALERTS.md) | Dashboard UI, LiveMonitor, Informational Alerts | ✅ `VERIFIED / FROZEN` | ✅ `COMPLETED & VERIFIED (MERGED PR #15 @ 57f6de1)` |
| [**Calibration Architecture**](../calibration/XAUUSD_EMPIRICAL_FRICTION_EVIDENCE_REPORT.md) | Empirical Friction Calibration & Execution Scopes | ⚪ `N/A` | 🟡 `SEALED D3 GOVERNANCE (PR #20, PR #21, PR #28; STANDARD_CENT SPREAD QUALIFIED; GATE: CANDLES_READY_EMPIRICAL_FRICTION_MISSING)` |
| [**PHASE 8: Live Paper Observation**](./PHASE_8_LIVE_PAPER_OBSERVATION.md) | Live Paper Observation, 3-Tier Parity Auditing | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION (BLOCKED BY CALIBRATION GATE)` |
| [**PHASE 9: ML Meta-Filter**](./PHASE_9_ML_META_FILTER.md) | ML Meta-Filter, Probability Calibration | ⚪ `N/A` | 📋 `HOLD — TARGET SPECIFICATION (DEPENDS ON PHASE 8 STABILITY)` |

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
| **R12** | **Risk** | **Side-Aware Risk Planning:** Long and Short setups enforce structural invalidation stops, ATR guards, and minimum unrounded RR boundaries. |
| **R13** | **Execution** | **Causal Execution Timestamp:** Simulated order fills occur at $t \ge t_{\text{signal}} + \text{latency}$; evidence before earliest execution timestamp is strictly forbidden. |
| **R14** | **Intrabar** | **Conservative Ambiguity Resolution:** Ambiguous intrabar candles resolve via chronological lower-TF replay or fall back to SL-first. |
| **R15** | **Costs** | **Deduplicated Friction Accounting:** Bid-ask spreads are never double-counted when actual executable quotes are available. |
| **R16** | **Statistics** | **Minimum Sample Guard:** Features or patterns with insufficient effective sample size receive zero active weight (configuration-driven sample threshold). |
| **R17** | **Cycles** | **Phase 3B Production Lock:** Experimental spectral features have an active production scoring weight locked to `0.0`. |
| **R18** | **Policy** | **TradingView Scraping Ban:** Scraping TradingView or using it as a calculation engine data source is strictly forbidden. |
| **R19** | **ML Protocol** | **Secondary Meta-Filtering Only:** ML models act purely as secondary probability filters on deterministic candidate setups. |
| **R20** | **Rollback** | **Deterministic Rollback:** ML models support instant zero-downtime rollback to rule-only baseline without server restart. |

---

## 3. Operational Contract Taxonomy

### A. Verified XAUUSD Operational Contracts
- **Phase 1 Contracts:** `XAU-P1-01` (canonical primary XAUUSD target), `XAU-P1-02` (integrated multi-source ingestion integrity).
- **Phase 2 Contracts:** `XAU-P2-01` (explicit volume evidence semantics: `REAL_VOLUME`, `TICK_VOLUME`, `PROXY_VOLUME`, `UNAVAILABLE`).
- **Phase 3A Contracts:** DST-aware session windows, knowable swing age maturity, revision-safe macro blackout.
- **Phase 3B Contracts:** Experimental spectral cycle engine (ACF, FFT, Wavelet, Hilbert) with hard-locked `production_weight = 0.0`.
- **Phase 4 Contracts:** `XAU-P4-01` (`BUY_WINDOW` $\rightarrow$ `BUY`), `XAU-P4-02` (`SELL_WINDOW` $\rightarrow$ `SELL`), `XAU-P4-03` (`CONFLICT` $\rightarrow$ `WAIT`), `XAU-P4-04` (`SYSTEM_SAFETY_HOLD` $\rightarrow$ `WAIT`). Baseline SHA: `b619a140391e5e308241246e105b9767a1b0716d`.
- **Phase 5 Contracts:** `XAU-P5-01` (LONG side-aware risk planning), `XAU-P5-02` (SHORT side-aware risk planning), `XAU-P5-03` (side-aware market bid/ask causal execution). Hostile matrix `H1`–`H74` fully verified. Merge SHA: `9011764958d31c5e96860488da7c54568def1352`.
- **Phase 6 Contracts:** `XAU-P6-01` (LONG point-in-time backtest replay), `XAU-P6-02` (SHORT point-in-time backtest replay), `XAU-P6-03` (combined side-aware parity / reporting & ablation). Historical Implementation Merge SHA: `dab3b6f8999bcef537bf4d8450f774ce36eb8e0f` (PR #14); Current Structural Seal Merge SHA: `74985e2982d3e488ba655262a03533263ed1cea2` (PR #29; `PHASE6_ENGINE_IMPLEMENTATION = COMPLETE`, `PHASE6_STRUCTURAL_READINESS = SEALED`, walk-forward fixed-spec evaluation, ablation implemented variants only, empirical calibration `NOT_FROZEN`, `PHASE6_FREEZE = HOLD`).
- **Phase 7 Contracts:** `XAU-P7-01` (BUY / WAIT / SELL presentation and dual-side alerting). Merge SHA: `57f6de1405d0df8548182a166d245f1a3173363d`.
- **Calibration Governance & Scope Contracts:**
  - `XAU-CAL-01`: Macro blackout event evidence qualification and revision-safe ingestion (PR #19 @ `06425ba`).
  - `XAU-CAL-02`: Six-category empirical friction qualification architecture without silent defaults (PR #20 @ `92b0bd6`).
  - `XAU-CAL-03`: Isolated Exness Standard Cent execution profile scope with fail-closed symbol binding and zero cross-profile contamination (PR #21 @ `fcbe1a9`).
  - `XAU-CAL-04`: Exness Standard Cent Stage D3 empirical friction governance and evidence capture (PR #28; Spread QUALIFIED $N = 6493208$ / 6,493,208, SHA `b2dbfaf9297075944c1163c3c1ff53db3abfa5f78d5edcf9c6d1b47b1784c749`; Legal Entity, Contract Geometry, Commission, Financing, and Slippage [real execution fill telemetry] remain MISSING).

### B. Planned Future XAUUSD Contracts
- **Phase 8 Planned Contracts:** `XAU-P8-01` (forward paper execution tracking and 14-day operational stability audit — blocked until empirical friction evidence is qualified).
- **Phase 9 Planned Contracts:** `XAU-P9-01` (point-in-time machine learning meta-labeling filter).

---

> [!IMPORTANT]
> **GOVERNANCE & PRODUCTION AUTHORITY NOTICE:**
> Implementation of Phases 5, 6 (structurally sealed in PR #29 @ `74985e2982d3e488ba655262a03533263ed1cea2`), and 7, as well as the calibration qualification architecture and Stage D3 governance (PR #20, PR #21, PR #28), does NOT grant live production authority. Published decision remains strictly `WAIT` (`is_production_authorized = False`, `production_weight = 0.0`), with gate `CANDLES_READY_EMPIRICAL_FRICTION_MISSING`.
>
> Current Standard Cent calibration evidence state (`artifacts/calibration/xauusd_standard_cent_empirical_friction_manifest.json`):
> - Venue: `EXNESS`, Account Tier: `STANDARD_CENT`, Symbol: `XAUUSDc`, Currency: `UNKNOWN / null`
> - `SPREAD`: `QUALIFIED / SEALED` ($N = 6493208$, SHA: `b2dbfaf9297075944c1163c3c1ff53db3abfa5f78d5edcf9c6d1b47b1784c749`)
> - `LEGAL_ENTITY`: `MISSING`
> - `CONTRACT_GEOMETRY`: `MISSING`
> - `COMMISSION`: `MISSING`
> - `FINANCING`: `MISSING`
> - `SLIPPAGE`: `MISSING` (real execution fill telemetry missing; tick history is not slippage)
>
> Phase 8 forward observation requires authentic empirical friction calibration across all categories as a mandatory precondition. Phase 8 and Phase 9 remain on HOLD.
