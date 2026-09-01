# AURUMIQ — XAUUSD SIGNAL INTELLIGENCE
## Full-Python Django Engineering Blueprint — XAUUSD Canonical Edition

**Document Status:** Implementation Blueprint v2.0  
**Active Target Instrument:** `XAU/USD` (Canonical Internal Identifier: `XAUUSD`)  
**Historical Baseline:** `XAUT` / Tether Gold (Retained strictly as frozen audit and regression evidence)  
**Decision Scope:** `BUY / WAIT / SELL` candidate intelligence with human decision support only  
**Order Execution Policy:** **FORBIDDEN** — Zero live or testnet order placement  
**Authoritative Phase 5 Merge SHA:** `9011764958d31c5e96860488da7c54568def1352` (Merged via PR #12)

---

# 0. Document Authority, Precedence & Governance

### 0.1 Purpose
This document is the single active governing engineering specification for AurumIQ. It defines the point-in-time multi-timeframe quantitative market intelligence, side-aware signal scoring, risk planning, backtesting, and ML meta-filtering architecture for Spot Gold (`XAUUSD`).

### 0.2 Precedence Hierarchy
1. Non-negotiable operating invariants (R1–R20).
2. Pure mathematical engine boundary definitions (`engine/`).
3. Point-in-time causality, closed-candle semantics, and data isolation.
4. Django models, Celery workflows, and database schema (`apps/`).
5. Presentation and informational alerting (`dashboard/`, `live_monitor/`, `alerts/`).

### 0.3 Historical Baseline Audit Invariant
Historical XAUT artifacts, acceptance tests (`A01`–`A47`), and modules remain strictly frozen for baseline regression continuity. No active XAUUSD feature may introduce USDT conversion dependencies, XAUT basis math, or exchange fee examples.

### 0.4 Documentation Governance Rule
Whenever phase implementation status changes:
1. Update individual phase specification in `docs/phases/`.
2. Update `README.md` master index and contract matrices.
3. Update `docs/phases/SUMMARY.md` deliverable summary.
4. Update this master blueprint (`XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md`).
5. Update downstream dependency statements and pre-conditions.
6. Record authoritative commit SHAs, PR numbers, and automated test suite evidence.
7. Preserve historical frozen specifications verbatim under explicit historical headings.
8. Never mark a phase complete based on a plan or candidate branch alone.

---

# 1. Non-Negotiable Core Principles (R1–R20)

| Rule | Category | Mandate |
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
| **R13** | **Execution** | **Causal Execution Timestamp:** Simulated order fills occur at $t \ge t_{\text{signal}} + \text{latency}$; fill on signal close is impossible. |
| **R14** | **Intrabar** | **Conservative Ambiguity Resolution:** Ambiguous intrabar candles resolve via chronological lower-TF replay or fall back to SL-first. |
| **R15** | **Costs** | **Deduplicated Friction Accounting:** Bid-ask spreads are never double-counted when actual executable quotes are available. |
| **R16** | **Statistics** | **Minimum Sample Guard:** Features or patterns with effective sample size $n_{eff} < 30$ receive zero active weight. |
| **R17** | **Cycles** | **Phase 3B Production Lock:** Experimental spectral features have an active production scoring weight locked to `0.0`. |
| **R18** | **Policy** | **TradingView Scraping Ban:** Scraping TradingView or using it as a calculation engine data source is strictly forbidden. |
| **R19** | **ML Protocol** | **Secondary Meta-Filtering Only:** ML models act purely as secondary probability filters on deterministic candidate setups. |
| **R20** | **Rollback** | **Deterministic Rollback:** ML models support instant zero-downtime rollback to rule-only baseline without server restart. |

---

# 2. Master Phased Architecture & Current Status

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                       AURUMIQ SIGNAL PIPELINE                           │
│                                                                         │
│  CLOSED CANDLE INGESTION (15m, 1H, 4H, 1D) ──► STATE MACHINE            │
│  - Primary & Secondary Spot Providers          - Long Direction & Timing│
│  - Multi-Timeframe Causal Indicators           - Short Direction & Timing│
│  - Causal Swing Structure (BOS, Zones)         - Candidate: BUY/WAIT/SELL│
│  - DST-Aware Robust Sessions (Phase 3A)        - Publication: WAIT (L-B)│
│  - Spectral Diagnostics (Weight = 0.0)         - Lossless SHA-256 Provenance│
│                                                          │              │
│                                                          ▼              │
│  LIVE MONITOR (Redis TTL 30s) ◄──────────────── RISK PLANNING GATE     │
│  - Real-Time Presentation Only                  - Side-Aware Long/Short │
│  - Proximity & Stale Feed Alerts                - Structural + ATR Stop │
│  - Zero Execution Code                          - Unrounded RR Gate     │
│                                                 - Intrabar 1m/5m Replay │
└─────────────────────────────────────────────────────────────────────────┘
```

### Authoritative Phase Status Master Table

| Phase | Title & Scope | Current XAUUSD Status | Authoritative Baseline / Provenance |
|---|---|:---:|---|
| **Phase 0** | Foundation Architecture, PostgreSQL, Celery, RBAC | 🟢 `REUSABLE` | Baseline: `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a` |
| **Phase 1** | Ingestion Engine, Multi-Provider Data, Health | 🟢 `CORE MIGRATION IMPLEMENTED` | Provider binding & empirical integrity thresholds pending |
| **Phase 2** | Pure Indicators, Market Regimes, Causal Swings | 🟡 `CORE ARCHITECTURE IMPLEMENTED` | Spot volume semantics implemented; empirical thresholds not frozen |
| **Phase 3A** | Robust Time Cycles, DST Sessions, Macro Gate | 🟡 `ARCHITECTURE IMPLEMENTED` | Empirical calibration `PENDING_DATA` |
| **Phase 3B** | Experimental Spectral Cycles (ACF, FFT, Wavelet) | 🧪 `IMPLEMENTED / RESEARCH ONLY` | Production weight hard-locked to `0.0` |
| **Phase 4** | Dual-Side Direction/Timing, State Machine | ✅ `COMPLETED & VERIFIED` | Sealed Baseline: `b619a140391e5e308241246e105b9767a1b0716d` |
| **Phase 5** | Side-Aware Risk Engine, Causal Execution, Intrabar | ✅ `COMPLETED & VERIFIED` | Merged via PR #12 | Main SHA: `9011764958d31c5e96860488da7c54568def1352` |
| **Phase 6** | PIT Backtesting, Walk-Forward Validation & Ablation | 🟡 `NOT STARTED` | Single canonical phase combining Phase 6A & Phase 6B |
| **Phase 7** | Dashboard UI, LiveMonitor, Multi-Timeframe Charts | ⏸️ `PAUSED / ADAPTATION PENDING` | Target presentation: XAUUSD, dual-side scores/states |
| **Phase 8** | Live Paper Observation, 3-Tier Parity Auditing | 📋 `HOLD — TARGET SPECIFICATION` | Requires Phase 6 backtest & Phase 7 live monitor |
| **Phase 9** | ML Meta-Filter, Probability Calibration | 📋 `HOLD — TARGET SPECIFICATION` | Secondary meta-filter only; cannot promote `WAIT` |

---

# 3. Layer-by-Layer Subsystem Specifications

## 3.1 Phase 1: Ingestion Engine & Spot Market Data
- **Canonical Target:** `Instrument.get_canonical_xauusd()` resolving Spot Gold denominated directly in USD (`quote_asset="USD"`).
- **Multi-Provider Topology:**
  - `PrimaryXauUsdSpotProvider` (`provider_id="xauusd_primary"`).
  - `SecondaryXauUsdSpotProvider` (`provider_id="xauusd_secondary"`).
- **Integrated Ingestion Integrity (`XAU-P1-02`):** Aligns closed candles by timestamp across providers, evaluates spread and price delta, and flags data quality without price averaging.
- **Fail-Closed Default:** Unconfigured credentials or missing feeds set `ProviderHealthStatus.NOT_CONFIGURED` and generate `DataQualitySnapshot(hard_fail=True)`.
- **Volume Evidence Semantics (`XAU-P2-01`):** `REAL_VOLUME`, `TICK_VOLUME`, `PROXY_VOLUME`, or `UNAVAILABLE`. Missing volume is never fabricated.

## 3.2 Phase 2: Pure Indicators, Market Regimes & Structural Analysis
- **Engine Purity:** Pure Python calculations in `engine/features/` with zero Django ORM dependencies.
- **Causal Swings (`engine/structure/causal_swings.py`):** Swing high/low at candle $i$ with $L=3, R=3$ is knowable strictly at candle $i+R$ close. Contains `timestamp` (peak/trough time) and `detected_at` (confirmation time).
- **Break of Structure (BOS):** Confirmed candle close breaking prior confirmed swing.
- **Support & Resistance Price Zones:** Bounded price intervals $[\text{price\_low}, \text{price\_high}]$ with point-in-time timestamp and touch tracking.
- **Regime Classification:** Transparent 6-state detector (`BULL_TREND`, `BEAR_TREND`, `RANGE`, `HIGH_VOLATILITY`, `TRANSITION`, `UNKNOWN`). Uncalibrated XAUUSD defaults to `UNKNOWN` (`CALIBRATION_REQUIRED`).

## 3.3 Phase 3A: Robust Time Cycle Engine
- **DST-Aware Sessions (`engine/cycles/session.py`):** Evaluates `ASIA`, `LONDON_PREOPEN`, `LONDON`, `LONDON_NY_OVERLAP`, `NEW_YORK`, `US_LATE` using Python standard library `zoneinfo`.
- **Knowable Swing Maturity (`engine/cycles/swing_duration.py`):** Evaluates swing maturity strictly based on `known_age` ($\text{as\_of} - \text{detected\_at}$). Unknown statistical distributions fail closed with `effective_n = 0.0` and `maturity_score = 0.0`.
- **Macro Event Blackout Gate (`engine/cycles/events.py`):** Scheduled high-impact events within configured blackout windows force `is_blocked_by_event = True` $\rightarrow$ `FORCE_WAIT`. Point-in-time revision safe (masks future revisions).

## 3.4 Phase 3B: Experimental Spectral Cycles (Research Only)
- **Spectral Subsystems:** Causal Autocorrelation (ACF), Detrended FFT, Continuous Wavelet Transform (CWT Morlet), Hilbert Transform Instantaneous Phase.
- **Hard-Locked Production Weight = 0.0:** `production_weight` is hard-locked to `0.0` across all dataclasses, engines, and database check constraints.
- **Empirical Promotion Gate:** Requires multi-year out-of-sample Phase 6 validation and statistical significance before any production weighting can be considered.

## 3.5 Phase 4: Dual-Side State Machine & Candidate Engine
- **Independent Dual-Side Scoring:**
  $$\text{LongDirectionScore}, \text{ShortDirectionScore}, \text{LongTimingScore}, \text{ShortTimingScore} \in [0, 100]$$
- **Two-Layer State Machine:**
  - **Layer A (Candidate Mechanics):** Evaluates `NO_TRADE`, `WATCH_LONG`, `READY_LONG`, `BUY_WINDOW`, `WATCH_SHORT`, `READY_SHORT`, `SELL_WINDOW`, `CONFLICT`, and `FORCE_WAIT`.
  - **Layer B (Publication Authority Guard):** `profile.is_production_authorized == False` holds published `state` at `NO_TRADE` and published `user_decision` at `WAIT`.
- **Verified Contracts:** `XAU-P4-01` (`BUY`), `XAU-P4-02` (`SELL`), `XAU-P4-03` (`CONFLICT`), `XAU-P4-04` (`FORCE_WAIT`).

## 3.6 Phase 5: Side-Aware Risk Planning, Execution & Intrabar Resolver
- **Candidate Gate:** LONG requires `BUY_WINDOW` + `BUY`; SHORT requires `SELL_WINDOW` + `SELL`. Demotes candidates to `WAIT`, never promotes `WAIT` to `BUY`/`SELL`.
- **Authoritative Timestamp $T$:** $T = \text{phase4\_snapshot.timestamp}$ (explicitly timezone-aware).
- **Dual PIT Gating:** Requires $\text{structure\_result.timestamp} \le T$, $\text{zone.created\_at} \le T$, and $\text{zone.is\_active} == \text{True}$.
- **Deterministic Entry Selection:**
  - LONG: Active Support from PIT-valid 15m structure; highest `price_high`, ties broken by `created_at ASC`, `price_low ASC`, `zone_fingerprint ASC`.
  - SHORT: Active Resistance from PIT-valid 15m structure; lowest `price_low`, ties broken by `created_at ASC`, `price_high DESC`, `zone_fingerprint ASC`.
- **Stop Loss Geometry:**
  - LONG: $\text{Stop}_{\text{structure}} = \text{Support\_Low} - \text{Buffer}$; $\text{Stop}_{\text{ATR}} = \text{Entry\_Mid} - (k \times \text{ATR}_{14})$; $\text{Stop}_{\text{Final}} = \min(\text{Stop}_{\text{structure}}, \text{Stop}_{\text{ATR}})$.
  - SHORT: $\text{Stop}_{\text{structure}} = \text{Resistance\_High} + \text{Buffer}$; $\text{Stop}_{\text{ATR}} = \text{Entry\_Mid} + (k \times \text{ATR}_{14})$; $\text{Stop}_{\text{Final}} = \max(\text{Stop}_{\text{structure}}, \text{Stop}_{\text{ATR}})$.
- **Conservative Planned Risk:**
  - LONG: $\text{Planned\_Risk} = \text{Entry\_Max} - \text{Stop}_{\text{Final}}$.
  - SHORT: $\text{Planned\_Risk} = \text{Stop}_{\text{Final}} - \text{Entry\_Min}$.
- **Unrounded Raw Gates:** Stop distance ATR $\le \text{max\_stop\_distance\_atr}$ and $\text{Planned\_RR} \ge \text{min\_rr\_tp1}$ evaluated on exact raw `Decimal` values (boundary equality is valid).
- **Structural Targets Only:**
  - LONG: $\text{TP1} =$ nearest confirmed structural resistance strictly above $\text{Entry\_Max}$.
  - SHORT: $\text{TP1} =$ nearest confirmed structural support strictly below $\text{Entry\_Min}$.
  - $\text{TP2}$ must sit strictly beyond $\text{TP1}$ ($\text{TP2} > \text{TP1}$ for LONG, $\text{TP2} < \text{TP1}$ for SHORT); inferior or equal candidates skipped (`tp2=None`).
- **Target Total Ordering:**
  - LONG: `price_low ASC`, `created_at ASC`, `price_high ASC`, `zone_fingerprint ASC`.
  - SHORT: `price_high DESC`, `created_at ASC`, `price_low DESC`, `zone_fingerprint ASC`.
- **Lossless SHA-256 Fingerprints:**
  - `StructureZone`: Binds `zone_type`, `price_low`, `price_high`, `created_at`, `touches`, `is_active`.
  - `QuoteEvidence`: Binds `evidence_type=QUOTE`, `timestamp`, `bid`, `ask`, `source`.
  - `CandleEvidence`: Binds full canonical `CandleData` fields.
  - `RiskPlan`: Binds Phase 4 fingerprint, state, decision, side, authoritative $T$, Decimal ATR, entries, stops, targets, RR, zone fingerprints, policy fingerprint, risk version, and caller-injected `code_revision`.
- **Side-Aware Execution Model:**
  - Earliest execution: $t \ge t_{\text{signal}} + \text{latency}$.
  - LONG Market: fills at $\text{ASK} + \text{adverse\_slippage}$; spread counted once.
  - SHORT Market: fills at $\text{BID} - \text{adverse\_slippage}$; spread counted once.
  - Next Bar Open: $\text{bar.open} \pm \text{synthetic\_spread} \pm \text{adverse\_slippage}$.
  - Limit Zone: LONG requires $\text{ASK} \le \text{limit}$; SHORT requires $\text{BID} \ge \text{limit}$.
  - `NO_FILL`: returns `raw_executable_price=None`, `fill_price=None`, `source_evidence_fingerprint=None`.
- **Side-Aware Intrabar Resolver:**
  - LONG: $\text{TP} \iff \text{high} \ge \text{TP}$; $\text{SL} \iff \text{low} \le \text{SL}$.
  - SHORT: $\text{TP} \iff \text{low} \le \text{TP}$; $\text{SL} \iff \text{high} \ge \text{SL}$.
  - Neither touched $\rightarrow$ `UNRESOLVED` (`exit_price=None`, `exit_timestamp=None`).
  - Ambiguous parent resolves via chronological 1m/5m lower-TF sequence or `CONSERVATIVE_SL_FIRST`.
  - Worst-case gap requires explicit non-negative Decimal `worst_case_adverse_gap`.
- **Verified Contracts:** `XAU-P5-01` (LONG), `XAU-P5-02` (SHORT), `XAU-P5-03` (Execution), Hostile Matrix `H1`–`H74`.
- **Position Sizing Boundary:** Strictly out of scope for Phase 5 and Phase 6.

## 3.7 Phase 6: Canonical Validation & Ablation Laboratory
- **Single Canonical Specification:** [`PHASE_6_BACKTEST_VALIDATION.md`](./docs/phases/PHASE_6_BACKTEST_VALIDATION.md) combining Phase 6A (PIT Replay & Walk-Forward) and Phase 6B (Component Ablation).
- **One Engine Parity:** Backtest lab instantiates and evaluates master `XauUsdSignalEngine` and `XauUsdRiskPlanner` directly.
- **Walk-Forward Splitting:** Chronological folds with label purging and embargo safety buffers.
- **Normalized Metrics:** Measures payoff, expectancy $\mathbb{E}[R]$, profit factor, and max drawdown in $R$ units with zero account sizing.
- **Ablation Lab:** Evaluates marginal contributions without mutating baseline candidate signals.
- **Planned Contracts:** `XAU-P6-01` (LONG PIT replay), `XAU-P6-02` (SHORT PIT replay), `XAU-P6-03` (combined portfolio parity & ablation).

## 3.8 Phase 7: Dashboard UI, LiveMonitor & Alerts
- **Presentation Focus:** Real-time presentation of live XAUUSD spot quotes, multi-timeframe candle charts, side-aware direction/timing gauges, candidate vs published states, and risk geometry.
- **LiveMonitor Service:** Redis store (`livequote:XAUUSD`, TTL 30s) decoupling tick streams from closed-candle decision engines. Emits `LIVE_DATA_STALE` or `PROVIDER_UNHEALTHY` if feeds lapse.
- **Informational Alerting:** Multi-channel webhooks/notifications for candidate setups, entry zone touches, invalidations, and macro blackouts. Zero automated order execution.

## 3.9 Phase 8: Live Paper Observation
- **Operational Stability Audit:** 14-day continuous forward paper observation without placing real orders.
- **Tri-Level Parity Auditing:** Measures $\Delta_{\text{Fill}}$, $\Delta_{\text{Expectancy}}$, $\Delta_{\text{WinRate}}$, and $\Delta_{\text{Slippage}}$ across BUY, SELL, and Combined dimensions against Phase 6 backtest expectations.

## 3.10 Phase 9: Machine Learning Meta-Filter
- **Secondary Filtering Only:** Predicts conditional probability $\mathbb{P}(\text{Win} \mid \text{Rule Candidate})$.
- **Allowed Actions:** $\text{BUY} \rightarrow \text{ACCEPT/REJECT}$, $\text{SELL} \rightarrow \text{ACCEPT/REJECT}$.
- **Forbidden Actions:** $\text{WAIT} \rightarrow \text{BUY/SELL}$. ML can never invent signals from `WAIT`.
- **Model Lifecycle:** Logistic Regression baseline $\rightarrow$ XGBoost $\rightarrow$ LightGBM. Calibrated via Platt Scaling or Isotonic Regression. Instant zero-downtime rollback to rule-only engine.

---

# 4. Celery Queue Topology & Infrastructure

The application orchestrates asynchronous workloads across 5 dedicated Celery queues:

```text
┌──────────────────┬────────────────────────────────────────────────────────┐
│ Queue Name       │ Purpose & Workload Scope                               │
├──────────────────┼────────────────────────────────────────────────────────┤
│ market_data      │ Market data ingestion, provider health checks, candles │
│ analysis         │ Closed-candle signal generation, state machine, risk   │
│ backtest         │ Point-in-time historical simulation & walk-forward lab │
│ machine_learning │ Meta-filter dataset extraction & model training        │
│ maintenance      │ System heartbeats, session cleanup, log maintenance    │
└──────────────────┴────────────────────────────────────────────────────────┘
```

---

# 5. Security, Auditability & Database Architecture

1. **Effective Active Admin Invariant (R6):**  
   $$\text{Effective Active Admin} \iff \text{is\_active} = \text{True} \land (\text{is\_superuser} = \text{True} \lor \text{profile.role} = \text{ADMIN})$$
2. **Audit Trail Durability (R7):** `UserManagementAuditLog.target_user` enforces `on_delete=models.PROTECT`. Hard deletion is strictly prohibited in Django Admin and ORM.
3. **Immutable Snapshots (R10):** All `SignalRecord`, `RiskPlanSnapshot`, and `DataQualitySnapshot` tables are append-only. Updates and deletions are prevented at the model and database check levels.
4. **Pure Engine Isolation (R9):** The `engine/` package contains zero imports of `django`, `celery`, `redis`, or `channels`.

---

# 6. Conclusion & Governance Notice

AurumIQ is fully synchronized at the **Phase 5 Completed & Verified** baseline (`9011764958d31c5e96860488da7c54568def1352`).

Production trading authority remains **NOT AUTHORIZED** (`is_production_authorized = False`, publication user decision held at `WAIT`). Automated order execution is **FORBIDDEN**. Phase 6 PIT Backtesting, Walk-Forward Validation, and Component Ablation must be completed and empirically verified before live paper observation (Phase 8) or live production publication can be considered.
