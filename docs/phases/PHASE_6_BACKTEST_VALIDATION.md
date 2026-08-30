# Phase 6: Point-in-Time Backtesting & Robustness Validation

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟡 `XAUUSD PIT BACKTEST REQUIRED`  
> **Primary Objective:** Empirically evaluate whether the AurumIQ engine exhibits persistent, out-of-sample edge on XAUUSD after accounting for realistic trading friction, spread deduplication, adverse slippage, explicit fees, intrabar ambiguity, and temporal regime variation.

---

## 1. Non-Negotiable Core Principle: One Engine Rule (R2 & A09)

The backtest lab NEVER duplicates trading or risk rules. Backtesting and live monitoring resolve the **exact same pure-Python `SignalEngine` class, version, configuration, and feature set**.

```text
Historical Point-in-Time Dataset (XAUUSD)
            │
            ▼
       Replay Clock T
            │
            ├── candles known @ T (15m, 1h, 4h, 1d)
            ├── macro state known @ T
            └── historical baseline reference @ T
            │
            ▼
       Phase 0–4 Engine (SignalEngine)
            │
            ▼
       SignalSnapshot (BUY / WAIT / SELL)
            │
            ├── WAIT (audited in ledgers)
            └── BUY_WINDOW / SELL_WINDOW
                     │
                     ▼
             Phase 5 RiskPlanner (Long / Short)
                     │
             valid + eligible?
                │          │
               NO         YES
                │          │
              skip         ▼
                    OutcomeEngine (Causal Fill)
                           │
                           ▼
                    IntrabarResolver (TP / SL / CONSERVATIVE)
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
| **P6-C1: Decision Time vs Outcome Time** | Signal evaluation at $T$ strictly accesses closed data $\le T$. Outcome simulation consumes post-$T$ data chronologically. |
| **P6-C2: Baseline Terminal Outcomes** | Terminal outcomes are strictly: `TP1_FIRST`, `SL_FIRST`, `NO_FILL`, `SKIPPED`, `CONSERVATIVE_SL_FIRST`, `UNRESOLVED`. No speculative trade management additions. |
| **P6-C3: Frozen R Denominator** | Denominator $R$ is strictly $\text{planned\_risk\_amount} = |\text{entry\_boundary} - \text{stop\_final}| > 0$. Realized $R = \frac{\text{pnl}}{\text{planned\_risk\_amount}}$. |
| **P6-C4: Normalized Drawdown Only** | Phase 6 contains no account sizing or balance compounding. Drawdown is measured strictly in cumulative normalized $R$. |
| **P6-C5: Post-Fill MFE / MAE Causality** | Only candles starting at or after `fill_timestamp` and ending on/before `exit_timestamp` are evaluated for MFE/MAE. |

---

## 3. Walk-Forward Architecture (`engine/backtest/`)

- **Chronological Fold Generator (`folds.py`):** Slices dataset chronologically into Train, Validation, and OOS half-open intervals `[start, end)`.
- **Exact Dependency Purging (`purge.py`):** Samples whose label outcome dependency interval crosses partition boundaries are purged from earlier segments to prevent forward label leakage.
- **Configurable Embargo (`purge.py`):** Post-boundary exclusion buffer prevents serial correlation leakage.
- **Strict OOS Isolation (`walkforward.py`):** Candidate selection API structurally accepts only Train and Validation inputs; OOS evaluation is strictly downstream.

---

## 4. Definition of Done Checklist

### Historical Baseline
- [x] Pure backtest execution engine implemented and verified against One Engine Rule (`A09`).
- [x] Walk-forward split generator with purging and embargo verified (`A34`, `A35`).
- [x] Deduplicated cost model and adverse slippage verified (`A32`).
- [x] Targeted tests `P6-01` to `P6-35` passing.

### Target XAUUSD Scope
- [ ] Ingest multi-year historical spot XAUUSD dataset (15m, 1H, 4H, 1D and 1m/5m intrabar).
- [ ] Run full walk-forward validation on dual-direction XAUUSD engine.
- [ ] Calibrate Direction/Timing scoring weights and minimum RR boundary.
