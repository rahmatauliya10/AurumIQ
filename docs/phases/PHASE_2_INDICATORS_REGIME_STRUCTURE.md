# Phase 2: Indicators, Market Regimes & Structural Analysis

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟡 `REVALIDATION REQUIRED`  
> **Primary Goal:** Compute mathematically pure technical indicators, classify point-in-time market regimes, identify causal swing points with structural Break of Structure (BOS), and enforce minimum effective sample size guards ($n_{eff} \ge 30$).

---

## 1. Mathematical Indicator Engine (`engine/indicators/`)

Pure Python calculations with explicit point-in-time causality ($T$):
- **Trend Slopes:** Multi-timeframe normalized EMA slopes on 15m, 1H, 4H, and 1D bars.
- **Oscillators:** Wilder's 14-period RSI, MACD ($12, 26, 9$) histogram velocity.
- **Volatility:** 14-period Average True Range (ATR), Realized Volatility using population standard deviation ($ddof=0$).
- **Trend Strength:** Normalized ADX with directional movement indices ($+DI, -DI$).

---

## 2. Market Regime Classification Engine (`engine/regime/`)

Classifies market state into 6 mutually exclusive regimes:

```text
┌────────────────────────────────────────────────────────┐
│                   MARKET REGIME MATRIX                 │
├───────────────────┬────────────────────────────────────┤
│ BULL_TREND        │ ADX >= 25, +DI > -DI, Price > EMAs │
│ BEAR_TREND        │ ADX >= 25, -DI > +DI, Price < EMAs │
│ RANGE             │ ADX < 20, Choppy Price Action      │
│ HIGH_VOLATILITY   │ ATR > 90th Percentile              │
│ COMPRESSION       │ ATR < 10th Percentile              │
│ UNKNOWN           │ Insufficient Bars / Data Gaps      │
└───────────────────┴────────────────────────────────────┘
```

> **Calibration Note:** Percentile boundaries and ADX thresholds are **NOT FROZEN** for XAUUSD and require empirical validation against historical spot gold data in Phase 6.

---

## 3. Structural Swing & BOS Engine (`engine/structure/`)

### Causal Swing Confirmation Rule
A swing point at candle $i$ is confirmed only after $R$ subsequent closed candles have elapsed without exceeding the swing extreme:
$$\text{Swing High at } i \iff \text{High}_i = \max(\text{High}_{i-L}, \dots, \text{High}_{i+R})$$
$$\text{Confirmation Timestamp} = \text{timestamp\_close of candle } i + R$$

### Break of Structure (BOS) & Change of Character (CHoCH)
- **Bullish BOS:** Closed candle breaches confirmed swing high.
- **Bearish BOS:** Closed candle breaches confirmed swing low.
- Look-ahead bias is strictly prevented: breaches are detectable only at the close of the breaking candle.

---

## 4. Minimum Effective Sample Guard (R16 & A16)

Patterns and regime-specific conditions enforce a minimum effective sample guard:
$$n_{eff} = n \times (1 - \text{HHI\_discount}) \times (1 - \text{clustering\_discount})$$
If $n_{eff} < 30$, the statistical feature weight is forced to `0.0` and flagged as `is_blocked = True`.

---

## 5. Definition of Done Checklist

### Historical Baseline
- [x] Pure indicator calculations verified against standard reference datasets.
- [x] 6-regime classifier implemented with point-in-time causality.
- [x] Causal swing identification and BOS detection verified without look-ahead bias (`P2-01` to `P2-11`).
- [x] Sample guard with normalized HHI discount verified.

### Target XAUUSD Scope
- [ ] Revalidate regime classification boundaries and ATR percentiles against XAUUSD multi-year history.
- [ ] Verify structural support/resistance clustering on XAUUSD spot data.
