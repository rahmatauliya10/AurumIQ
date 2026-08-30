# Phase 8: Live Paper Observation & Forward Execution Audit (XAUUSD BUY + SELL)

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Execute continuous, live paper decision-support observation for **XAUUSD (both BUY and SELL setups)** in a production-like environment without placing orders, logging all decisions immutably, and tracking side-aware triple-barrier outcomes to audit parity against historical walk-forward backtesting.

---

## 1. Operating Protocol & Non-Negotiable Safety Boundaries

1. **Zero Exchange Trading Access (R1):** The live paper runner operates exclusively on public/read-only market feeds. The codebase contains zero exchange trading keys, broker execution integrations, or balance withdrawal capabilities.
2. **Immutable Append-Only Logging (R5, R10):** Every live signal emitted upon candle close is stored permanently in `SignalRecord` with its complete feature vector, version metadata, and reason tree.
3. **Side-Aware Dual Direction Support:** Full monitoring and outcome resolution for both `BUY` (Long) and `SELL` (Short) setups.
4. **Automated Outcome Auditing:** As subsequent closed candles unfold, a Celery tracking task updates `SignalOutcome` records and evaluates whether live market behavior matches historical simulation expectations.
5. **14-Day Continuity-Only Gate:** The 14-day observation period functions strictly as an **operational continuity and infrastructure stability gate** (zero pipeline crashes, zero missed candle intervals, zero unhandled exceptions), **NOT a statistical significance gate**. Asymptotic statistical significance is established across multi-year walk-forward backtesting.

---

## 2. Side-Aware Triple-Barrier Live Outcome Tracking (`apps/signals/outcome_tasks.py`)

For every confirmed candidate signal emitted at $t_0$, the tracking engine monitors subsequent market price action:

```text
LONG SETUP (BUY):
  Upper Barrier = TP1 / TP2 (In Profit)
  Lower Barrier = Stop Loss (-1.0R, below Support)
  Time Barrier  = Max Holding Horizon (e.g. 24 Hours / 96 bars on 15m)

  Resolution:
  1. Price touches TP1 before SL -> TP1_HIT (Record timestamp, realized R >= +1.80R, MFE/MAE)
  2. Price touches SL before TP1 -> STOP_HIT (Record timestamp, realized -1.0R)
  3. Time barrier expires        -> TIMEOUT (Record exit price, mark realized R)

SHORT SETUP (SELL):
  Lower Barrier = TP1 / TP2 (In Profit)
  Upper Barrier = Stop Loss (-1.0R, above Resistance)
  Time Barrier  = Max Holding Horizon (e.g. 24 Hours / 96 bars on 15m)

  Resolution:
  1. Price touches TP1 before SL -> TP1_HIT (Record timestamp, realized R >= +1.80R, MFE/MAE)
  2. Price touches SL before TP1 -> STOP_HIT (Record timestamp, realized -1.0R)
  3. Time barrier expires        -> TIMEOUT (Record exit price, mark realized R)
```

### Model Schema (`apps/signals/models.py: SignalOutcome`)
```python
class BarrierHitType(models.TextChoices):
    TP1 = "TP1", "Take Profit 1 Hit"
    TP2 = "TP2", "Take Profit 2 Hit"
    STOP = "STOP", "Stop Loss Hit"
    TIMEOUT = "TIMEOUT", "Time Horizon Expired"
    ACTIVE = "ACTIVE", "Active Tracking in Progress"

class SignalOutcome(models.Model):
    signal = models.OneToOneField("SignalRecord", on_delete=models.CASCADE, related_name="outcome")
    direction = models.CharField(max_length=8) # BUY, SELL
    first_barrier_hit = models.CharField(max_length=16, choices=BarrierHitType.choices, default=BarrierHitType.ACTIVE)
    planned_entry_price = models.DecimalField(max_digits=12, decimal_places=4)
    simulated_fill_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    stop_loss_price = models.DecimalField(max_digits=12, decimal_places=4)
    take_profit_price = models.DecimalField(max_digits=12, decimal_places=4)
    exit_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    realized_r = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    mfe_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    mae_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    tp1_hit_at = models.DateTimeField(null=True, blank=True)
    stop_hit_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    tracking_resolution = models.CharField(max_length=8, default="15m")
```

---

## 3. Live vs Backtest Parity Auditing & Discrepancy Matrix

The system continuously calculates discrepancy metrics comparing live paper observations against point-in-time backtest replay over the identical date window across **three dedicated reporting dimensions**:

1. **BUY Parity Report:** Compares live long setups against backtest long replay.
2. **SELL Parity Report:** Compares live short setups against backtest short replay.
3. **Combined Parity Report:** Consolidated portfolio-level execution and expectancy comparison.

### Key Parity Metrics
$$\Delta_{\text{Fill}} = |\text{Live\_Simulated\_Fill} - \text{Backtest\_Replay\_Fill}|$$
$$\Delta_{\text{Expectancy}} = |\text{Live\_Realized\_Expectancy\_R} - \text{Backtest\_Expected\_R}|$$
$$\Delta_{\text{WinRate}} = |\text{Live\_Win\_Rate} - \text{Backtest\_Win\_Rate}|$$
$$\Delta_{\text{Slippage}} = |\text{Live\_Observed\_Slippage} - \text{Backtest\_Assumed\_Slippage}|$$

### Disparity Alerting Thresholds
- If $\Delta_{\text{Expectancy}} > 0.15\text{R}$, an automated disparity investigation event is logged to `AuditEvent`.
- If $\Delta_{\text{Fill}} > 0.50 \times \text{ATR}_{14}$, entry execution model assumptions are flagged for review.
- If live spread exceeds historical backtest friction envelope by $> 50\%$, a cost-model recalibration warning is triggered.

---

## 4. Operational Readiness & 14-Day Continuity Verification

Before declaring Phase 8 observation complete, the platform must satisfy the following operational criteria:

1. **Zero Pipeline Outages:** 14 consecutive calendar days of automated Celery decision cycles without missed closed-candle triggers (15m, 1H, 4H, 1D).
2. **Intrabar Resolution Parity:** 100% of ambiguous candles successfully resolved via 1m/5m data or conservative fallback without unhandled exceptions.
3. **Zero Look-Ahead Proof:** Hash verification confirming all live decisions utilized strictly closed-bar data at generation time.
4. **Zero Live Order API Gate:** Static AST security scan verifying zero order-placement APIs exist in the codebase.

---

## 5. Definition of Done Checklist

- [ ] Live analysis Celery beat task runs autonomously for all closed candle intervals.
- [ ] Side-aware `SignalOutcome` tracker monitors and resolves BUY and SELL barriers.
- [ ] Automated parity reporting engine generates BUY, SELL, and Combined parity audits.
- [ ] Intrabar ambiguity resolver verified on live 1m/5m quote feeds.
- [ ] 14-day operational continuity gate successfully completed with zero pipeline failures.
- [ ] Static AST analysis confirms zero private exchange keys or order execution code.
