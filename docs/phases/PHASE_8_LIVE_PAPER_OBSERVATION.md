# Phase 8: Live Paper Observation & Forward Execution Audit (XAUUSD BUY + SELL)

> **Historical XAUT Baseline Status:** ⚪ `N/A`  
> **Current XAUUSD Target Status:** 📋 `HOLD — TARGET SPECIFICATION (NOT YET IMPLEMENTED)`  
> **Primary Goal:** Specify continuous, live paper decision-support observation for **XAUUSD (both BUY and SELL setups)** in a production-like environment without placing real orders, logging all decisions immutably, and tracking side-aware triple-barrier outcomes to audit parity against historical backtesting.

---

## 1. Operating Protocol & Safety Boundaries

1. **Zero Exchange Trading Access (R1):** The live paper runner operates exclusively on public/read-only market feeds. The codebase contains zero exchange trading keys, broker execution integrations, or order placement capabilities.
2. **Immutable Append-Only Logging (R5, R10):** Every live signal emitted upon candle close is stored permanently in `SignalRecord` with its complete feature vector, version metadata, and reason tree.
3. **Side-Aware Dual Direction Support:** Full monitoring and outcome resolution for both `BUY` (Long) and `SELL` (Short) candidate setups.
4. **14-Day Continuity-Only Gate:** The 14-day observation period functions strictly as an **operational continuity and infrastructure stability gate** (zero pipeline crashes, zero missed candle intervals, zero unhandled exceptions), **NOT a statistical significance gate**. Asymptotic statistical significance is established across multi-year walk-forward backtesting.

---

## 2. Side-Aware Triple-Barrier Outcome Tracking (Conceptual Target)

For every confirmed candidate signal emitted at $t_0$, the tracking engine monitors subsequent closed market price action:

```text
LONG SETUP (BUY):
  Upper Barrier = Target TP (In Profit)
  Lower Barrier = Stop Loss (-1.0R, below Support)
  Time Barrier  = Max Holding Horizon (TBD based on Phase 6 validation)

  Resolution:
  1. Price touches TP before SL -> TP_HIT (Record timestamp, realized R, MFE/MAE)
  2. Price touches SL before TP -> STOP_HIT (Record timestamp, realized -1.0R)
  3. Time barrier expires       -> TIMEOUT (Record exit price, mark realized R)

SHORT SETUP (SELL):
  Lower Barrier = Target TP (In Profit)
  Upper Barrier = Stop Loss (-1.0R, above Resistance)
  Time Barrier  = Max Holding Horizon (TBD based on Phase 6 validation)

  Resolution:
  1. Price touches TP before SL -> TP_HIT (Record timestamp, realized R, MFE/MAE)
  2. Price touches SL before TP -> STOP_HIT (Record timestamp, realized -1.0R)
  3. Time barrier expires       -> TIMEOUT (Record exit price, mark realized R)
```

---

## 3. Live vs Backtest Parity Auditing

The system will calculate discrepancy metrics comparing live paper observations against point-in-time backtest replay over the identical date window across **three dedicated reporting dimensions**:
1. **BUY Parity Report:** Compares live long setups against backtest long replay.
2. **SELL Parity Report:** Compares live short setups against backtest short replay.
3. **Combined Parity Report:** Consolidated portfolio-level execution and expectancy comparison.

### Key Parity Dimensions
- $\Delta_{\text{Fill}} = |\text{Live\_Simulated\_Fill} - \text{Backtest\_Replay\_Fill}|$
- $\Delta_{\text{Expectancy}} = |\text{Live\_Realized\_Expectancy\_R} - \text{Backtest\_Expected\_R}|$
- $\Delta_{\text{WinRate}} = |\text{Live\_Win\_Rate} - \text{Backtest\_Win\_Rate}|$
- $\Delta_{\text{Slippage}} = |\text{Live\_Observed\_Slippage} - \text{Backtest\_Assumed\_Slippage}|$

> **Calibration Note:** Parity alerting thresholds and maximum holding time horizons are **NOT FROZEN** and will be calibrated following Phase 6 empirical backtesting.

---

## 4. Definition of Done Checklist (Pending Phase 8 Implementation)

- [ ] Live analysis Celery beat task runs autonomously for all closed candle intervals.
- [ ] Side-aware `SignalOutcome` tracker monitors and resolves BUY and SELL barriers.
- [ ] Automated parity reporting engine generates BUY, SELL, and Combined parity audits.
- [ ] Intrabar ambiguity resolver verified on live 1m/5m quote feeds.
- [ ] 14-day operational continuity gate successfully completed with zero pipeline failures.
- [ ] Static AST analysis confirms zero private exchange keys or order execution code.
