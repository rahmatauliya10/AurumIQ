# Phase 8: Live Paper Observation Mode

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Execute continuous, live paper decision-support observation in a production-like environment without placing orders, logging all decisions immutably, and tracking triple-barrier outcomes to audit parity against historical backtesting.

---

## 1. Operating Protocol

1. **Zero Exchange Trading Access (R1):** The live paper runner operates exclusively on public/read-only market feeds.
2. **Immutable Append-Only Logging (R5, R10):** Every live signal emitted upon a candle close is stored permanently with its full feature vector, version metadata, and reason tree.
3. **Automated Outcome Auditing:** As subsequent candles unfold, a Celery tracking task updates `SignalOutcome` records and evaluates whether live behavior matches historical simulation expectations.

---

## 2. Triple-Barrier Live Outcome Tracking (`apps/signals/outcome_tasks.py`)

For every confirmed `BUY_WINDOW` signal at $t_0$, the tracking engine monitors subsequent market prices:

```text
At t0:
  Upper Barrier = TP1 / TP2
  Lower Barrier = Stop Loss (-1.0R)
  Time Barrier  = Max Holding Horizon (e.g. 24 Hours / 96 bars on 15m)

Live Resolution:
  1. Did price touch TP1 first?  -> TP1_HIT (Record timestamp, realized R, MFE/MAE)
  2. Did price touch SL first?   -> STOP_HIT (Record timestamp, realized -1.0R)
  3. Did time barrier expire?    -> TIMEOUT (Record exit price, mark outcome)
```

### Model Schema (`apps/signals/models.py: SignalOutcome`)
```python
class SignalOutcome(models.Model):
    signal = models.OneToOneField(Signal, on_delete=models.CASCADE, related_name="outcome")
    first_barrier_hit = models.CharField(max_length=16) # TP1, TP2, STOP, TIMEOUT, ACTIVE
    realized_r = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    mfe_price = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    mae_price = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    tp1_hit_at = models.DateTimeField(null=True, blank=True)
    stop_hit_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
```

---

## 3. Live vs Replay Discrepancy Audit

The system calculates discrepancy metrics comparing live paper results with point-in-time backtest replay over the identical date window:

$$\Delta_{\text{Fill}} = |\text{Live\_Simulated\_Fill} - \text{Backtest\_Replay\_Fill}|$$
$$\Delta_{\text{Expectancy}} = |\text{Live\_Realized\_Expectancy} - \text{Backtest\_Expected\_R}|$$

If $\Delta_{\text{Expectancy}}$ exceeds reasonable friction tolerance ($> 0.15\text{R}$), an automated disparity investigation event is logged to `AuditEvent`.

---

## 4. Definition of Done Checklist

- [ ] Live analysis task runs autonomously on Celery scheduler for closed candles.
- [ ] `SignalOutcome` tracking resolves barriers using 1m/5m resolution data.
- [ ] Parity comparison dashboard compares live paper performance against backtest replay.
- [ ] Zero execution failures or missed candle intervals during a 14-day sample observation window.
