# Phase 8: Live Paper Observation & Forward Execution Audit (XAUUSD BUY + SELL)

> **Historical XAUT Baseline Status:** ⚪ `N/A`
> **Current XAUUSD Target Status:** 🚀 `IN PROGRESS / OBSERVING (14-DAY CONTINUITY GATE)`
> **Execution Mode:** `PAPER_ONLY = True`, `REAL_ORDER_EXECUTION = "disabled"` (Structurally Isolated)
> **Primary Goal:** Continuous live paper decision-support observation for **XAUUSD (both BUY and SELL setups)** in a production environment without placing real orders, consuming authoritative `SignalRecord` and `LiveRiskPlanRecord` from Phase 7 `XauUsdLiveDecisionPipelineService`, and tracking side-aware triple-barrier outcomes to audit parity against historical backtesting.
>
> *Important Governance Notice:* **Phase 8 completion is an operational continuity milestone, not proof of profitability and not authorization for automated live-money order execution.**

---

## 1. Operating Protocol & Governance Boundaries

1. **Zero Exchange Trading Access & Structural Isolation (R1):** The live paper runner operates exclusively on public/read-only market feeds. The Phase 8 execution namespace contains zero order dispatch capability, zero broker placement methods (`order_send`, `OrderSend`), and is verified via structural AST testing.
2. **Production Pipeline Hook (No Duplicate Pipeline):** `Phase8PaperObserver` hooks directly into the production Phase 7 decision path (`XauUsdLiveDecisionPipelineService`). Authoritative signals originate strictly from `SignalRecord` and risk plans from `LiveRiskPlanRecord`. Phase 8 does not create competing live signal engines.
3. **Point-in-Time Empirical Friction Resolution:** Frictions are resolved point-in-time from the qualified active model `EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1`. True requested-price slippage remains strictly `UNOBSERVABLE`; execution gap vs reference quote proxy may numerically equal 0.0000 bps without claiming true zero slippage.
4. **Closed-Candle Invariant:** Strategic decisions are strictly limited to closed candles on 15m, 1h, 4h, and 1d. Intrabar 1m/5m data are restricted to resolving barrier collision chronology and never independently generate strategic signals.
5. **Side-Aware Dual Direction Support:** Full monitoring and outcome resolution for both `BUY` (Long) and `SELL` (Short) candidate setups, evaluated and reported separately.
6. **Explicit Position Sizing Boundary:** Monetary paper PnL (`gross_pnl`, `net_pnl` in `USC`) is only computed when an explicit simulated position size (`paper_volume_lots`) is provided. Without explicit volume, monetary PnL remains null / `NOT_EVALUATED`, and normalized R metrics (`gross_r`, `net_r`, MFE, MAE) serve as authoritative performance measures.
7. **14-Day Continuity Semantics:** The 14-day observation period functions strictly as an **operational continuity and infrastructure stability gate** (zero pipeline crashes, zero unhandled exceptions, zero missed eligible observation cycles during open market hours). Expected market closures (weekends, market holidays) are recorded as normal non-trading intervals and do NOT reset the window or increment failure counters. Calendar time alone is insufficient authorization.


---

## 2. Side-Aware Triple-Barrier Outcome Tracking (Conceptual Target)

For every confirmed candidate signal emitted at $t_0$, the tracking engine monitors subsequent closed market price action:

```text
LONG SETUP (BUY):
  Upper Barrier = Target TP (In Profit)
  Lower Barrier = Stop Loss (-1.0R, below Support)
  Time Barrier  = Max Holding Horizon (Calibrated via Phase 6)

  Resolution:
  1. Price touches TP before SL -> TP_HIT (Record timestamp, realized R, MFE/MAE)
  2. Price touches SL before TP -> STOP_HIT (Record timestamp, realized -1.0R)
  3. Time barrier expires       -> TIMEOUT (Record exit price, mark realized R)

SHORT SETUP (SELL):
  Lower Barrier = Target TP (In Profit)
  Upper Barrier = Stop Loss (-1.0R, above Resistance)
  Time Barrier  = Max Holding Horizon (Calibrated via Phase 6)

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
3. **Combined Parity Report:** Consolidated side-aware execution and expectancy comparison (`XAU-P6-03`).

### Key Parity Dimensions
- $\Delta_{\text{Fill}} = |\text{Live\_Simulated\_Fill} - \text{Backtest\_Replay\_Fill}|$
- $\Delta_{\text{Expectancy}} = |\text{Live\_Realized\_Expectancy\_R} - \text{Backtest\_Expected\_R}|$
- $\Delta_{\text{WinRate}} = |\text{Live\_Win\_Rate} - \text{Backtest\_Win\_Rate}|$
- $\Delta_{\text{Slippage}} = |\text{Live\_Observed\_Slippage} - \text{Backtest\_Assumed\_Slippage}|$

---

## 4. Definition of Done Checklist (Pending Phase 8 Implementation)

- [ ] Prior empirical friction calibration gate fully resolved from `CANDLES_READY_EMPIRICAL_FRICTION_MISSING` to qualified status across all 6 evidence categories.
- [ ] Live analysis Celery beat task runs autonomously for all closed candle intervals.
- [ ] Side-aware `SignalOutcome` tracker monitors and resolves BUY and SELL barriers.
- [ ] Verify SELL live-paper barrier chronology (`XAU-P8-01`).
- [ ] Automated parity reporting engine generates BUY, SELL, and Combined parity audits.
- [ ] Intrabar ambiguity resolver verified on live 1m/5m quote feeds.
- [ ] 14-day operational continuity gate successfully completed with zero pipeline failures.
- [ ] Static AST analysis confirms zero private exchange keys or order execution code.
