# Phase 6: XAUUSD Point-in-Time Backtesting, Walk-Forward Validation & Ablation

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟢 **IMPLEMENTED ON FEATURE BRANCH — PENDING FINAL MERGE VERIFICATION**  
> **Canonical Status:** **SINGLE ACTIVE GOVERNING SPECIFICATION FOR PHASE 6**

---

## 1. Objective & Governance

Phase 6 is the unified empirical governance and validation laboratory for spot XAUUSD. It constructs a deterministic, side-aware point-in-time historical simulation, walk-forward validation, and component ablation engine that directly resolves the production pure-Python calculation engine (`XauUsdSignalEngine`, `XauUsdRiskPlanner`, `SideAwareEntryExecutionModel`, and `SideAwareIntrabarResolver`).

### Key Governance Invariants
1. **Zero Look-Ahead Bias:** Point-in-time historical simulation strictly uses data knowable at evaluation step $T$.
2. **Zero Speculative Sizing:** Position sizing, capital allocation, account balance tracking, margin calculations, and leverage compounding are **STRICTLY OUT OF SCOPE**. Performance is evaluated exclusively in normalized $R$ units.
3. **Zero Order Execution:** The backtest lab contains zero live exchange connectivity, broker execution bindings, or order dispatch endpoints.
4. **Publication Guard Invariant:** Layer B published user decision remains strictly `WAIT` (`is_production_authorized = False`) until Phase 6 empirical validation and calibration criteria are explicitly frozen.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                      PHASE 6 VALIDATION PIPELINE                        │
│                                                                         │
│  CLOSED CANDLE DATA STORE (15m, 1H, 4H, 1D)                             │
│         │                                                               │
│         ▼                                                               │
│  POINT-IN-TIME MARKET CONTEXT (Strictly timestamp <= T)                 │
│         │                                                               │
│         ├──► [REPLAY & WALK-FORWARD VALIDATION]                         │
│         │    - Direct Phase 4 Engine Call (XAU-P4-01..04)               │
│         │    - Direct Phase 5 Risk Planner (XAU-P5-01..03)              │
│         │    - Phase 5 SideAwareEntryExecutionModel                     │
│         │    - Phase 5 SideAwareIntrabarResolver                        │
│         │    - Chronological Folds with Purging & Embargo               │
│         │    - Normalized R Metrics (No Account Sizing / No Compounding)│
│         │    - Reporting: LONG (XAU-P6-01), SHORT (XAU-P6-02),          │
│         │      Combined Side-Aware Parity / Reporting (XAU-P6-03)       │
│         │                                                               │
│         └──► [COMPONENT ABLATION LAB]                                   │
│              - Baseline: Full Sealed Phase 4 + Phase 5 Pipeline         │
│              - Isolated Ablation Variants (Disabling 1 Factor at a time)│
│              - Zero Mutation of Baseline Candidate Results              │
│              - Marginal Expectancy & Stability Evaluation               │
│              - Empirical Evidence Requirement for Production Promotion  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Point-in-Time Data Replay

1. **Closed-Candle Isolation (R3, R4, A31):** For historical evaluation step $T$, market data queries strictly apply `timestamp_close <= T` and `is_closed=True`.
2. **Future Mutation Masking:** Candles with timestamp $> T$ are future and strictly invisible. Mutating post-exit data produces zero difference in historical outputs at $T$.
3. **Safety-Hold Alignment:** An unclosed candle at or before $T$ activates the exact same Phase 4 XAUUSD runtime safety context and hard-gate mechanics (`FORCE_WAIT` $\rightarrow$ `WAIT`). Phase 6 does not invent a divergent exception path.

---

## 3. Phase 4 Signal Engine Parity

1. **One Engine Rule (R2, A09):** The backtesting engine instantiates and evaluates the master `XauUsdSignalEngine` directly. Live analysis, paper observation, and historical backtesting resolve the identical calculation class, version, and feature configuration.
2. **Deterministic Dual-Side Output Replay:** Replays `LongDirectionScore`, `ShortDirectionScore`, `LongTimingScore`, `ShortTimingScore`, `candidate_state`, and `candidate_user_decision` (`BUY_WINDOW / BUY`, `SELL_WINDOW / SELL`, `WAIT`, `CONFLICT`).

---

## 4. Phase 5 Risk Engine Parity

1. **Direct Risk Planner Resolution:** Reuses `XauUsdRiskPlanner` directly from Phase 5.
2. **Exact Decimal Arithmetic:** Preserves exact Decimal arithmetic throughout entries, stops, targets, spreads, slippage, and RR calculations without floating-point precision loss.
3. **Unrounded Validation Gates:** Threshold decisions (`stop_distance_atr <= max_stop_distance_atr` and `planned_rr_tp1 >= min_rr_tp1`) compare raw unrounded Decimal values (boundary equality is valid).
4. **Deterministic Structural Risk Geometry:**
   - **LONG:** Derived from 15m Support Zone (highest `price_high` selection; ties broken by `created_at ASC`, `price_low ASC`, `zone_fingerprint ASC`). Conservative planned risk uses `entry_max`. Structural TP1 only (nearest resistance above `entry_max`).
   - **SHORT:** Derived from 15m Resistance Zone (lowest `price_low` selection; ties broken by `created_at ASC`, `price_high DESC`, `zone_fingerprint ASC`). Conservative planned risk uses `entry_min`. Structural TP1 only (nearest support below `entry_min`).
5. **Demotion-Only Invariant:** Phase 5 risk evaluation may demote candidates ($\text{BUY} \to \text{WAIT}$, $\text{SELL} \to \text{WAIT}$), but can never promote $\text{WAIT} \to \text{BUY}$ or $\text{WAIT} \to \text{SELL}$.

---

## 5. Causal Execution Replay

1. **Direct Phase 5 Execution Model Reuse:** Entry simulation reuses `SideAwareEntryExecutionModel` from Phase 5 (`MARKET_AFTER_SIGNAL`, `NEXT_BAR_OPEN`, `LIMIT_ZONE`, LONG ASK, SHORT BID, observed spread counted once, synthetic spread as Phase 5 specifies, adverse slippage, evidence fingerprints, `NO_FILL`). Phase 6 must not independently recompute entry fills.
2. **Causal Execution Timestamp (R13, A19, A27):** Evidence before $\text{earliest\_exec\_ts} = \text{signal\_generated\_at} + \text{latency}$ is strictly forbidden. Evidence exactly at $\text{earliest\_exec\_ts}$ is eligible ($\text{timestamp} \ge \text{earliest\_exec\_ts}$). When latency > 0, signal-time evidence is naturally pre-activation and ineligible.
3. **Phase 6 Friction & Commission Assumptions:** Round-trip commissions, execution fees, and friction models are `NOT_CONFIGURED / EVIDENCE-DRIVEN` until separately specified for XAUUSD. Historical crypto fee assumptions (maker/taker) do not apply to active XAUUSD backtesting.

---

## 6. Intrabar Resolution

1. **Direct Phase 5 Intrabar Resolver Reuse:** Triple-barrier resolution reuses `SideAwareIntrabarResolver` (`TP1_FIRST`, `SL_FIRST`, `TIMEOUT`).
2. **Chronological Lower-Timeframe Sequence:** Ambiguous parent candles resolve via chronological lower-timeframe (1m preferred, 5m fallback) replay.
3. **Conservative Fail-Safe:** If lower-timeframe data is missing or malformed, the resolver falls back to `CONSERVATIVE_SL_FIRST`. If the parent bar itself is malformed or neither barrier is touched within the horizon, it resolves to `UNRESOLVED`.

---

## 7. Walk-Forward Validation

1. **Chronological Splitting (No Random Shuffle):** Multi-year spot XAUUSD data is partitioned into rolling half-open intervals $[ \text{start}, \text{end} )$ for Train, Validation, and Out-of-Sample (OOS) evaluation.
2. **Exact Dependency Purging:** Samples whose outcome dependency interval $[ \text{signal\_ts}, \text{dependency\_end\_ts} ]$ crosses partition boundaries are purged from earlier segments to prevent forward label leakage.
3. **Post-Boundary Embargo:** A protective buffer after each test partition is excluded to prevent serial correlation leakage into subsequent folds.
4. **Strict OOS Isolation:** Candidate selection and parameter evaluation APIs structurally accept only Train and Validation inputs. OOS evaluation is strictly downstream of frozen model selection.

---

## 8. Performance Metrics

Every backtest report calculates comprehensive, normalized risk-adjusted statistics:

1. **Exact Expectancy Formula:**
   $$\mathbb{E}[R] = (\text{Win\_Rate} \times \bar{R}_{\text{win}}) - ((1 - \text{Win\_Rate}) \times \bar{R}_{\text{loss}})$$
2. **Profit Factor:** $\frac{\text{Gross Profit } R}{\text{Gross Loss } R}$ with division-by-zero safeguard.
3. **Normalized Drawdown:** Peak-to-trough cumulative $R$ drawdown (`max_drawdown_r`, `drawdown_duration_trades`) without speculative account sizing or compounding.
4. **Execution Quality & Funnel:** Maximum Favorable Excursion (MFE), Maximum Adverse Excursion (MAE), signal-to-fill conversion funnel, and cost drag ($\text{gross\_expectancy\_r} - \text{net\_expectancy\_r}$).

---

## 9. Component Ablation

1. **Isolated Paired Fold Analysis:** Quantifies the exact marginal out-of-sample contribution of each individual engine subsystem by comparing the full baseline model against ablated variants:
   - **BASELINE:** Full deterministic XAUUSD Phase 4 + Phase 5 pipeline (immutable).
   - **ABLATION VARIANTS:** Disable/remove one approved component at a time in isolated research runs:
     - Without Market Regime Filter
     - Without Phase 3A Session Expectancy
     - Without Phase 3A Swing Duration Maturity
     - Without Phase 3A Macro Blackout Gate
     - Without Multi-Timeframe Trend Confirmation
     - With Phase 3B Experimental Spectral Factors (Evaluated against promotion gate)
2. **Ablation Invariants:**
   - Running an ablation trial must NEVER alter or mutate the primary candidate signal dataset.
   - Component ablation is executed via pure research workflows without auto-promoting parameters.

---

## 10. Calibration Evidence

1. **Evidence-Driven Governance:** Empirical promotion criteria, sample thresholds, maturity bands, significance methods, and stability requirements must be explicitly estimated, documented, and frozen from approved XAUUSD walk-forward evidence.
2. **Uncalibrated Status:** Until empirical validation is complete, all active calibration parameters remain `NOT_CONFIGURED / NOT_FROZEN`.
3. **No In-Sample Optimization:** No feature or threshold is approved for production promotion based solely on in-sample win rate.

---

## 11. Acceptance Contracts

| Contract ID | Name | Focus | Scope | Status |
|---|---|---|---|:---:|
| **`XAU-P6-01`** | LONG Point-in-Time Replay | Verifies PIT replay, causal execution, intrabar collision resolution, and normalized $R$ metrics for BUY candidates | Replay Engine | 🟡 `PLANNED / FUTURE CONTRACT` |
| **`XAU-P6-02`** | SHORT Point-in-Time Replay | Verifies PIT replay, causal execution, intrabar collision resolution, and normalized $R$ metrics for SELL candidates | Replay Engine | 🟡 `PLANNED / FUTURE CONTRACT` |
| **`XAU-P6-03`** | Combined Side-Aware Parity / Reporting & Ablation | Verifies combined Long/Short parity reporting, walk-forward purging/embargo, and isolated component ablation | Validation & Ablation | 🟡 `PLANNED / FUTURE CONTRACT` |

---

## 12. Definition of Done (Pending Phase 6 Implementation)

- [ ] Backtest engine imports pure `engine/` modules with zero Django ORM, Celery, Redis, or Channels dependencies (AST verified).
- [ ] Point-in-time candle isolation verified (candles $> T$ invisible; unclosed candles $\le T$ trigger safety hold).
- [ ] Phase 4 `XauUsdSignalEngine` and Phase 5 `XauUsdRiskPlanner` resolved directly without rule divergence.
- [ ] Causal execution simulation strictly enforces $t_{\text{fill}} \ge t_{\text{signal}} + \text{latency}$.
- [ ] Intrabar resolver enforces 1m/5m chronological replay with conservative fallback on ambiguity.
- [ ] Walk-forward fold generator applies chronological slicing, dependency purging, and post-boundary embargo.
- [ ] Component ablation harness runs isolated paired trials without mutating baseline candidate signals.
- [ ] Normalized $R$ metrics suite computed without position sizing, account balances, or leverage compounding.
- [ ] Canonical SHA-256 fingerprinting generated for all backtest configurations and run artifacts.
- [ ] Acceptance contracts `XAU-P6-01`, `XAU-P6-02`, and `XAU-P6-03` passing all gates.

---

## 13. Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** 🟢 **COMPLETED, RIGOROUSLY VERIFIED & FROZEN**  
> **Baseline Commit SHA:** `f22483addd7cc5095c46e4f1c928a8b6651d83eb`  
> **Primary Goal:** Construct a point-in-time historical simulation, walk-forward validation, and component ablation engine that directly resolves the production `SignalEngine` and `RiskPlanner` without look-ahead bias, double-counted costs, or speculative account sizing.

### 1. Core Operating Principles

```text
HISTORICAL POINT-IN-TIME REPLAY PIPELINE
  1. Data Filtering: Strictly closed candles with timestamp_close <= T (A31)
  2. Signal Evaluation: Directly invoke production SignalEngine (A09)
  3. Risk Planning: Directly invoke production RiskPlanner (A07)
  4. Causal Execution: Fill at t >= signal_ts + latency (A19, A27)
  5. Friction Accounting: Synthetic spread only on mid-candles; adverse slippage (A32)
  6. Barrier Outcome: Terminal resolution (TP1_FIRST, SL_FIRST, TIMEOUT) with intrabar replay (A14)
  7. Performance Metrics: Normalized Expectancy R, Profit Factor, Max Drawdown R (No sizing)
  8. Walk-Forward: Chronological folds with dependency purging and embargo (A34, A35)
  9. Component Ablation: Isolated paired fold analysis without mutating baseline (A37)
```

### 2. Key Mathematical Contracts

- **P6-C1: Direct Engine Resolution (A09):** The backtest engine instantiates and evaluates the master `SignalEngine` directly.
- **P6-C2: Point-in-Time Replay Causality (A31):** For historical evaluation step $T$, market data queries strictly apply `timestamp_close <= T` and `is_closed=True`.
- **P6-C3: Exact Expectancy Formula:**
  $$\mathbb{E}[R] = (\text{Win\_Rate} \times \bar{R}_{\text{win}}) - ((1 - \text{Win\_Rate}) \times \bar{R}_{\text{loss}})$$
- **P6-C4: Normalized Drawdown Only:** Drawdown is measured strictly in $R$ units (`max_trade_sequence_drawdown_r`, `drawdown_duration_trades`). No speculative account sizing or compounding is evaluated.
- **P6-C5: Post-Fill MFE / MAE Causality:** A candle partially elapsed at `fill_timestamp` is excluded from candle-only MFE/MAE. Only candles starting at or after `fill_timestamp` and ending on/before `exit_timestamp` are evaluated.

### 3. Cost & Friction Model (`engine/backtest/costs.py`)

- **Actual ASK Entry:** Spread is embedded in the quote $\rightarrow$ synthetic spread is zero (no double counting).
- **Actual BID Long Exit:** Spread is embedded in the quote $\rightarrow$ synthetic spread is zero (no double counting).
- **Mid / OHLC Candle:** Synthetic spread is applied exactly once (half-spread on entry, half-spread on exit).
- **Slippage:** Strictly adverse (adds to entry price, subtracts from exit proceeds).
- **Fees:** Explicit maker/taker percentage.

### 4. Phase 6 Acceptance Test Matrix

| Test ID | Test Name | Gate Criteria | Status |
|---|---|---|:---:|
| **XAU-P6-01** | Historical LONG PIT Replay, Risk Parity, Execution & Normalized R | Single Phase 4 & 5 LONG execution parity, deterministic planned risk $R$, intrabar resolution, and published decision `WAIT` | 🟢 IMPLEMENTED — PASS / PENDING FINAL MERGE VERIFICATION |
| **XAU-P6-02** | Historical SHORT PIT Replay, Risk Parity, Execution & Normalized R | Single Phase 4 & 5 SHORT execution parity, deterministic planned risk $R$, intrabar resolution, and published decision `WAIT` | 🟢 IMPLEMENTED — PASS / PENDING FINAL MERGE VERIFICATION |
| **XAU-P6-03** | Combined Side-Aware Parity, Walk-Forward Purge/Embargo & Ablation | Side-aware metric parity, chronological fold purging, post-boundary embargo, and component ablation immutability | 🟢 IMPLEMENTED — PASS / PENDING FINAL MERGE VERIFICATION |
| **P6-01** | PIT Candle Filtering | `timestamp_close <= as_of` & `is_closed == True` | ✅ PASS |
| **P6-02** | Future Mutation Safety | Mutating $> T$ / $> \text{exit}$ preserves historical outputs | ✅ PASS |
| **P6-03** | Closed Candle Only | Unclosed candle at $T$ rejected from decision set | ✅ PASS |
| **P6-04** | Engine Reuse (A09) | Master `XautSignalEngine` directly resolved | ✅ PASS |
| **P6-05** | Planner Reuse | Master `RiskPlanner` directly resolved | ✅ PASS |
| **P6-06** | No Same-Bar Execution | $t_{\text{fill}} \ge t_{\text{signal}} + \text{latency}$ strictly enforced | ✅ PASS |
| **P6-07** | ASK Spread Integrity | Spread not double counted on actual quote entry | ✅ PASS |
| **P6-08** | BID Spread Integrity | Spread not double counted on actual quote exit | ✅ PASS |
| **P6-09** | Synthetic Spread Once | Mid candles receive half-spread per leg | ✅ PASS |
| **P6-10** | Explicit Fee Accounting | Separate entry and exit fees applied | ✅ PASS |
| **P6-11** | Adverse Slippage | Slippage always penalizes trader | ✅ PASS |
| **P6-12** | Gross vs Net Determinism | Deterministic PnL, return %, and $R$ accounting | ✅ PASS |
| **P6-13** | TP1 Terminal Resolution | Barrier hit on TP1 resolves to `TP1_FIRST` | ✅ PASS |
| **P6-14** | SL Terminal Resolution | Barrier hit on SL resolves to `SL_FIRST` | ✅ PASS |
| **P6-15** | No-Fill Outcome | Valid outcome when fill conditions not met | ✅ PASS |
| **P6-16** | Conservative Intrabar | Fallback to `CONSERVATIVE_SL_FIRST` on ambiguity | ✅ PASS |
| **P6-17** | MFE / MAE Causality | Excludes mid-bar candle active during fill | ✅ PASS |
| **P6-18** | Observational TP2 | Records TP2 extension without altering realized PnL | ✅ PASS |
| **P6-19** | Chronological Folds | Folds strictly preserve temporal arrow `[start, end)` | ✅ PASS |
| **P6-20** | Dependency Purge | Purges crossing dependency windows across boundaries | ✅ PASS |
| **P6-20A** | NO_FILL Timeout Purge | NO_FILL dependency uses timeout timestamp | ✅ PASS |
| **P6-20B** | UNRESOLVED Horizon Purge | UNRESOLVED dependency uses causal evaluation horizon | ✅ PASS |
| **P6-20C** | Trade Exit Purge | Completed trade dependency uses exit timestamp | ✅ PASS |
| **P6-21** | Embargo Exclusion | Post-boundary embargo buffer strictly excluded | ✅ PASS |
| **P6-21A** | Embargo Boundary Semantics | Exact half-open boundary filtering | ✅ PASS |
| **P6-22** | OOS Isolation API | Candidate selection structurally forbids OOS inputs | ✅ PASS |
| **P6-23** | Fold Reproducibility | Deterministic fold generation across runs | ✅ PASS |
| **P6-23A** | Exact Spec Replay | Same config produces identical fold assignment | ✅ PASS |
| **P6-23B** | Config Fingerprinting | Material config change alters walk-forward hash | ✅ PASS |
| **P6-24** | Expectancy R Contract | Mathematically exact $\mathbb{E}[R] = (w \times \bar{R}_w) - (l \times \bar{R}_l)$ | ✅ PASS |
| **P6-25** | Profit Factor Contract | Gross profit / gross loss with division-by-zero safeguard | ✅ PASS |
| **P6-26** | Normalized Drawdown Contract | Peak-to-trough in cumulative $R$ without account sizing | ✅ PASS |
| **P6-27** | Cost Drag Accounting | $\text{gross\_expectancy\_r} - \text{net\_expectancy\_r}$ | ✅ PASS |
| **P6-28** | Signal-to-Fill Funnel | Tracks signal $\rightarrow$ eligible $\rightarrow$ fill $\rightarrow$ resolved counts | ✅ PASS |
| **P6-29** | Ablation Isolation | Ablation engine execution leaves baseline unmodified | ✅ PASS |
| **P6-30** | Phase 3B Zero Weight Lock | Experimental cycle production weight strictly locked $0.0$ | ✅ PASS |
| **P6-31** | Run Fingerprint Determinism | Canonical SHA-256 fingerprint generated from inputs | ✅ PASS |
| **P6-32** | Config Mutation Fingerprint | Friction/fold parameter modification alters hash | ✅ PASS |
| **P6-33** | Code Revision Fingerprint | Git SHA or code revision changes alter hash | ✅ PASS |
| **P6-34** | Pure Backtest Engine Imports | AST verification of zero django/apps/celery/redis in engine | ✅ PASS |
| **P6-35** | Zero Live Order APIs | Backtest engine contains zero live exchange or execution code | ✅ PASS |
| **A09** | One Engine Parity Gate | Backtest engine resolves identical live engine | ✅ PASS |
| **A31** | No Lookahead Gate | Closed candle isolation strictly proven | ✅ PASS |
| **A32** | Cost Integrity Gate | Deduplicated spread and adverse slippage verified | ✅ PASS |
| **A33** | Outcome Isolation Gate | Post-exit mutations cannot alter completed trade | ✅ PASS |
| **A34** | Walk-Forward Purge Gate | Outcome dependencies cannot leak across fold boundaries | ✅ PASS |
| **A35** | OOS Isolation Gate | OOS strictly inaccessible to candidate selection logic | ✅ PASS |
| **A36** | Deterministic Reproducibility Gate | Identical backtest produces identical results & deltas | ✅ PASS |
| **A37** | Production / Research Isolation Gate | BASELINE $\rightarrow$ ABLATION $\rightarrow$ BASELINE produces identical baseline | ✅ PASS |
| **A38** | Future Mutation Gate | Replay output invariant under future data mutation | ✅ PASS |

### 5. Walk-Forward & Ablation Architecture (`engine/backtest/`)

- **Chronological Fold Generator (`folds.py`):** Slices dataset chronologically into Train, Validation, and OOS half-open intervals `[start, end)`.
- **Exact Dependency Purging (`purge.py`):** Samples whose label outcome dependency interval `[signal_ts, dependency_end_ts]` crosses partition boundaries are purged from earlier segments to prevent forward label leakage.
- **Configurable Embargo (`purge.py`):** Configurable post-boundary exclusion buffer prevents serial correlation leakage.
- **Strict OOS Isolation (`walkforward.py`):** Candidate selection API structurally accepts only Train and Validation inputs. OOS evaluation is strictly downstream of frozen candidate selection.
- **Component Ablation Framework (`ablation.py`):** Pure research-only framework enabling paired fold comparison without mutating production engine or auto-promoting parameters.
- **Immutable Django Persistence (`apps/backtests/`):** Append-only audit records (`BacktestRun`, `BacktestTrade`) with canonical SHA-256 fingerprinting and idempotent task execution.
