# Phase 6: Point-in-Time Backtesting, Walk-Forward Validation & Ablation Engine

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟡 **XAUUSD PIT BACKTEST REQUIRED**

---

## XAUUSD Migration Addendum

### 1. Target Scope & Dual-Side Backtest Architecture
For the target XAUUSD instrument, the backtest lab will evaluate historical multi-year spot XAUUSD datasets across three reporting dimensions:
1. **BUY Replay & Validation:** Evaluates long-side candidate setups (`XAU-P6-01`).
2. **SELL Replay & Validation:** Evaluates short-side candidate setups (`XAU-P6-02`).
3. **Combined Portfolio Parity:** Consolidated dual-side metrics, expectancy, and cost drag (`XAU-P6-03`).

### 2. Methodological Continuity
All core backtest methodologies established during the historical baseline (Point-in-Time candle filtering at $T$, strict post-signal execution latency, deduplicated spread and adverse slippage accounting, chronological fold slicing, dependency purging, post-boundary embargo, and normalized $R$ metrics) are 100% retained. Historical XAUT results serve strictly as baseline algorithmic validation, not statistical proof for XAUUSD.

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

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
