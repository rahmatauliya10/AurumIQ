# Phase 3A: Robust Time-Cycles, Session Statistics & Macro Gate

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟡 `EMPIRICAL REBUILD REQUIRED`  
> **Primary Goal:** Implement deterministic, causally sound time-cycle features: daylight saving time (DST)-aware session expectancies, swing duration maturity distributions, revision-safe macro economic calendar blackout gates, and calendar seasonality.

---

## 1. DST-Aware Trading Session Engine (`engine/cycles/sessions.py`)

Trading sessions in spot gold exhibit pronounced liquidity and volatility clustering governed by UTC offsets that shift during Daylight Saving Time (DST).

### Implementation Standard
- Evaluated strictly using standard library `zoneinfo` (`Europe/London`, `America/New_York`, `Asia/Tokyo`).
- Sessions: London Open, London/NY Overlap, New York Open, Asian Session.
- **Statistical Significance Gate (P3A-14):** Session historical expectancy applies a weight multiplier only when historical sample size $N \ge 30$ and $p\text{-value} \le 0.05$.

> **Rebuild Notice:** Session expectancy matrices require an empirical rebuild using multi-year XAUUSD historical data during Phase 6 backtesting.

---

## 2. Swing Duration Maturity Engine (`engine/cycles/swings.py`)

Quantifies whether an active price swing is temporally young, mature, or exhausted.

### Causality Contract (P3A-07)
Maturity is measured strictly from the swing point's **confirmation candle timestamp** ($i+R$), never the retrospective peak formation time:
$$\text{known\_age}(T) = T - \text{timestamp\_close}(i+R)$$
Percentile distributions ($P10, P50, P90$) are computed over a rolling sample of confirmed historical swings.

---

## 3. Revision-Safe Macro Event Gate (`engine/cycles/macro.py` & A26)

Prevents trading into high-volatility macroeconomic releases (US Non-Farm Payrolls, CPI, FOMC rate decisions).

```text
       t_event - pre_buffer                  t_event + post_buffer
──────────────┬───────────────────▲───────────────────┬──────────────► Time
              │                   │                   │
         [NORMAL TRADING]    BLACKOUT WINDOW     [NORMAL TRADING]
                             State: FORCE_WAIT
```

### Point-in-Time Revision Safety (A26)
- Decisions at $T$ consume only macro calendar figures knowable at $t_{\text{released}} \le T$.
- Revisions published at $t_{\text{revised}} > T$ are strictly masked during historical replay.
- Missing or malformed macro feeds fail closed to `FORCE_WAIT`.

---

## 4. Definition of Done Checklist

### Historical Baseline
- [x] DST-aware session partition engine implemented using `zoneinfo`.
- [x] Causal swing duration maturity estimator implemented.
- [x] Revision-safe macro blackout gate implemented and verified (`A06`, `A26`).
- [x] Targeted tests `P3A-01` to `P3A-18` passing.

### Target XAUUSD Scope
- [ ] Empirically rebuild session expectancy distributions on historical XAUUSD data.
- [ ] Calibrate macro pre/post blackout buffers for spot gold volatility characteristics.
