# Phase 6: Backtesting Lab & Walk-Forward Ablation (Historical Reference)

> **Document Status:** 🧊 **SUPERSEDED / HISTORICAL REFERENCE ONLY**  
> **Canonical Governing Specification:** [`PHASE_6_BACKTEST_VALIDATION.md`](./PHASE_6_BACKTEST_VALIDATION.md)  
> **Historical Baseline Commit:** `f22483addd7cc5095c46e4f1c928a8b6651d83eb`  
> **Notice:** This document is retained strictly for historical architecture reference and audit continuity. All active XAUUSD Point-in-Time backtesting, walk-forward validation (Phase 6A), and component ablation (Phase 6B) specifications are consolidated in [`PHASE_6_BACKTEST_VALIDATION.md`](./PHASE_6_BACKTEST_VALIDATION.md).

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **APPROVED (HISTORICAL XAUT REFERENCE)**  
> **Primary Goal:** Implement point-in-time historical backtesting, walk-forward time-series splitting with label purging and embargo, realistic trade friction simulation, and automated component ablation testing.

### 1. Non-Negotiable Core Principle (R2 & A09)

> **ONE ENGINE RULE:** The backtesting engine must NEVER maintain a simplified secondary set of trading rules. Live analysis and backtesting must resolve the **exact same pure-Python `XautSignalEngine` class, version, configuration, and feature set**.

```text
HISTORICAL STORE ──> Build Point-in-Time MarketContext(t) ──> XautSignalEngine.analyze(context)
                                                                       │
                                                                       ▼
METRICS & ABLATION <── Record Trade <── Simulate Execution <── BUY_WINDOW (if emitted)
```

### 2. Walk-Forward Splitting, Purge & Embargo (`engine/backtesting/splits.py`)

#### Time-Series Folds (No Random Shuffle)
```text
Window 1: [ Train A ] -> [ Val B ] -> [ Test C ]
Window 2:             -> [ Train B ] -> [ Val C ] -> [ Test D ]
Window 3:                          -> [ Train C ] -> [ Val D ] -> [ Test E ]
```

#### Purge & Embargo Windows
- **Purging:** If signal outcome labeling horizon is 24 hours (96 bars), remove 96 bars immediately preceding train/validation/test boundaries to eliminate overlapping target leakage.
- **Embargo:** Add a safety buffer after the test set to account for autocorrelation before the next fold starts.

### 3. Trade Simulator & Realistic Friction (`engine/backtesting/simulator.py`)

#### Simulated Trade Lifecycle
1. **Trigger:** `engine.analyze(t)` emits `BUY_WINDOW`.
2. **Fill:** `EntryExecutionModel` calculates fill price on the next bar or post-signal quote.
3. **Frictions Applied:**
   - Bid-Ask Spread: Configured percentage (e.g. $0.05\%$).
   - Exchange Maker/Taker Fees: Configured percentage (e.g. $0.04\%$).
   - Execution Slippage: Configured percentage (e.g. $0.02\%$).
4. **Monitoring:** Triple-barrier evaluation (TP1, TP2, Stop Loss, or Max Holding Time Horizon).
5. **Intrabar Resolution:** Applies `IntrabarResolver` (1m/5m replay or `SL_FIRST`) on ambiguous candles.

### 4. Performance Metrics Suite (`engine/backtesting/metrics.py`)

Every backtest report calculates comprehensive, risk-adjusted statistics:

| Metric Category | Specific Metrics Computed |
|---|---|
| **Sample Size** | Total Setups, Valid Trades ($N$), Trades / Month |
| **Payoff Profile** | Win Rate ($\%$), Avg Win ($R$), Avg Loss ($R$), Payoff Ratio |
| **Expectancy** | Expected $R$ per Trade: $\mathbb{E}[R] = (W\% \times \text{AvgWin}) - (L\% \times \text{AvgLoss})$ |
| **Profitability** | Profit Factor ($\frac{\text{Gross Profit}}{\text{Gross Loss}}$), Net Return ($\%$) |
| **Downside Risk** | Max Drawdown ($\%$), Max Drawdown Duration (days), Consecutive Losses |
| **Risk-Adjusted** | Sharpe Ratio, Sortino Ratio, Calmar Ratio |
| **Execution Quality** | Maximum Favorable Excursion (MFE), Maximum Adverse Excursion (MAE) |
| **Subsystem Breakdown** | Performance partitioned by **Regime**, **Session**, and **Cycle Phase** |

### 5. Automated Component Ablation (`engine/backtesting/ablation.py` & A10)

Quantifies the exact marginal contribution of each engine layer:

```text
BASELINE: Direction & Structure Only                → PF = 1.62, Expectancy = +0.22R
+ Session Expectancy (Phase 3A)                     → PF = 1.74, Expectancy = +0.28R (+27%)
+ Swing Duration Maturity (Phase 3A)                → PF = 1.82, Expectancy = +0.33R (+18%)
+ Macro Event Gate (Phase 3A)                       → PF = 1.88, Expectancy = +0.36R (+9%)
+ Experimental Cycles (Phase 3B — Promoted only)    → Evaluated against Promotion Gate
+ Normalized XAU Confirmation                       → PF = 1.95, Expectancy = +0.41R (+14%)
```

### 6. Django Backtest Job Management (`apps/backtests/`)

- Model: `BacktestRun` (stores parameters, start/end dates, engine/config versions, aggregate results).
- Model: `BacktestTrade` (stores individual simulated trade entries, exits, MFE, MAE, realized $R$).
- Celery Task: `run_backtest` executed on the dedicated `backtest` Celery queue.

### 7. Historical Acceptance Tests

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A09** | One Engine Parity | Live engine and backtester resolve identical `XautSignalEngine` class, version, and config. |
| **A10** | Component Ablation | Automated paired fold ablation evaluates incremental PF and expectancy. |
| **A14** | Intrabar Ambiguity | Resolves ambiguous candles via lower-TF sequence or conservative SL-first. |
| **A19** | Execution Latency | Fill timestamp strictly occurs at $t \ge t_{\text{signal}} + \text{latency}$. |
| **A27** | Conservative Execution | Unfavorable price gaps and adverse slippage applied to execution fills. |
| **A31** | Point-in-Time Isolation | Evaluates historical state at $T$ without future candle leakage. |
| **A32** | Spread Deduplication | Spread accounted for exactly once across actual quotes and synthetic candles. |
| **A34** | Walk-Forward Purging | Purges overlapping triple-barrier outcome horizon at fold boundaries. |
| **A35** | Walk-Forward Embargo | Enforces post-test embargo buffer to prevent serial correlation leakage. |
| **A37** | Ablation Isolation | Ablation runs execute without mutating baseline candidate dataset. |
