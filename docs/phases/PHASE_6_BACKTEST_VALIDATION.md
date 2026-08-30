# Phase 6: Point-in-Time Backtesting & Robustness Validation

> **Status:** 🟢 **COMPLETED & FROZEN**  
> **Phase Baseline:** `phase5-approved` (`6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee`) — **FROZEN**  
> **Primary Objective:** Empirically evaluate whether the frozen AurumIQ Phase 0–5 engine exhibits persistent, out-of-sample edge after accounting for realistic trading friction, spread deduplication, adverse slippage, explicit fees, intrabar ambiguity, and temporal regime variation.

---

## 1. Core Operating Principles

### A. One Engine Rule (R2 & A09)
The backtest lab NEVER duplicates trading or risk rules. Backtesting and live monitoring resolve the **exact same pure-Python `XautSignalEngine` class, version, configuration, and feature set**.

```text
Historical Point-in-Time Dataset
            │
            ▼
       Replay Clock T
            │
            ├── candles known @ T
            ├── XAU reference known @ T
            ├── USDT normalization rate @ T
            └── macro state known @ T
            │
            ▼
      Phase 0–4 Engine (XautSignalEngine)
            │
            ▼
       SignalSnapshot
            │
            ├── WAIT / AVOID (audited in ledgers)
            └── BUY_WINDOW
                     │
                     ▼
             Phase 5 RiskPlanner
                     │
             valid + eligible?
                │          │
               NO         YES
                │          │
              skip         ▼
                    OutcomeEngine (Causal Fill)
                           │
                           ▼
                    IntrabarResolver (TP1 / SL / CONSERVATIVE)
                           │
                           ▼
                    CostModel (Spread, Adverse Slippage, Fees)
                           │
                           ▼
                    Normalized Outcome (1R Denominator Frozen)
                           │
                           ▼
                Point-in-Time Trade Ledger & Metrics
```

---

## 2. Frozen Pre-Implementation Contracts (P6-C1 to P6-C5)

| Contract | Rule & Specification |
|---|---|
| **P6-C1: Decision Time vs Outcome Time** | Signal evaluation at $T$ strictly accesses closed data $\le T$. Outcome simulation consumes post-$T$ data chronologically. Mutating data $> T$ never alters Signal/Risk at $T$; mutating data $> \text{exit}$ never alters completed trades; mutating data within $[T, \text{exit}]$ legitimately affects outcome. |
| **P6-C2: Baseline Terminal Outcomes** | Terminal outcomes are strictly: `TP1_FIRST`, `SL_FIRST`, `NO_FILL`, `SKIPPED`, `CONSERVATIVE_SL_FIRST`, `UNRESOLVED`. No invented trade management (no partial closes, breakeven stops, trailing stops, or arbitrary max-holding liquidations). `TP2` is recorded strictly as observational analytics (`tp2_reached_after_tp1`, `max_favorable_extension`). |
| **P6-C3: Frozen R Denominator** | Denominator $R$ is strictly $\text{planned\_risk\_amount} = \text{entry\_max} - \text{stop\_final} > 0$. Realized $R = \frac{\text{pnl}}{\text{planned\_risk\_amount}}$. Denominator is never redefined from actual fill price, ensuring execution quality and slippage remain transparent in realized $R$. |
| **P6-C4: Normalized Drawdown Only** | Phase 6 contains no account balance, sizing, or portfolio allocation. Drawdown is strictly `max_trade_sequence_drawdown_r`, `drawdown_duration_trades`, and `maximum_consecutive_losses`. Sharpe/Sortino are computed strictly on normalized daily return series. |
| **P6-C5: Post-Fill MFE / MAE Causality** | A candle partially elapsed at `fill_timestamp` is excluded from candle-only MFE/MAE. Only candles starting at or after `fill_timestamp` and ending on/before `exit_timestamp` are evaluated. |

---

## 3. Cost & Friction Model (`engine/backtest/costs.py`)

Configurable via `BacktestCostConfig`:
- **Actual ASK Entry:** Spread is embedded in the quote $\rightarrow$ synthetic spread is zero (no double counting).
- **Actual BID Long Exit:** Spread is embedded in the quote $\rightarrow$ synthetic spread is zero (no double counting).
- **Mid / OHLC Candle:** Synthetic spread is applied exactly once (half-spread on entry, half-spread on exit).
- **Slippage:** Strictly adverse (adds to entry price, subtracts from exit proceeds).
- **Fees:** Explicit maker/taker percentage.

---

## 4. Phase 6 Acceptance Test Matrix

| Test ID | Test Name | Gate Criteria | Status |
|---|---|---|---|
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

---

## 5. Walk-Forward & Ablation Architecture (`engine/backtest/`)

- **Chronological Fold Generator (`folds.py`):** Slices dataset chronologically into Train, Validation, and OOS half-open intervals `[start, end)`.
- **Exact Dependency Purging (`purge.py`):** Samples whose label outcome dependency interval `[signal_ts, dependency_end_ts]` crosses partition boundaries are purged from earlier segments to prevent forward label leakage.
- **Configurable Embargo (`purge.py`):** Configurable post-boundary exclusion buffer prevents serial correlation leakage.
- **Strict OOS Isolation (`walkforward.py`):** Candidate selection API structurally accepts only Train and Validation inputs. OOS evaluation is strictly downstream of frozen candidate selection.
- **Component Ablation Framework (`ablation.py`):** Pure research-only framework enabling paired fold comparison without mutating production engine or auto-promoting parameters.
- **Immutable Django Persistence (`apps/backtests/`):** Append-only audit records (`BacktestRun`, `BacktestTrade`) with canonical SHA-256 fingerprinting and idempotent task execution.

---

## 6. Staged Phase Status

```text
PHASE 5                         ✅ FROZEN (SHA: 6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee)
PHASE 6A (Replay, Costs, Outcomes, Metrics) 🟢 COMPLETED & VERIFIED (23/23 tests pass)
PHASE 6B (Walk-Forward, Purge, Embargo)     🟢 COMPLETED & VERIFIED (228/228 tests pass)
PHASE 6C (Ablation Lab, Django Persistence)  🟢 COMPLETED & VERIFIED (244/244 tests pass)
PHASE 7 (Dashboard & Live Monitoring)       ⛔ HOLD
```
