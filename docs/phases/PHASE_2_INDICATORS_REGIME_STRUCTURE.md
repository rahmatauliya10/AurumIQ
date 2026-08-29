# Phase 2: Core Indicators, Regime, Structure & Sample Guard

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Implement framework-independent, pure-Python technical indicators, deterministic market regime classifier, causal ZigZag market structure with Break of Structure (BOS), and a Statistical Sample Guard with normalized HHI.

---

## 1. Pure-Python Indicators (`engine/indicators/`)

All calculations operate strictly on causal numpy arrays or pandas DataFrames ending at timestamp $t$.

### A. Trend Subsystem (`indicators/trend.py`)
- **EMAs:** EMA20, EMA50, EMA200 (calculated via recursive smoothing $\alpha = \frac{2}{N+1}$).
- **Normalized Slopes:** $\text{Slope}_{EMA} = \frac{EMA_t - EMA_{t-k}}{k \times ATR_t}$.
- **Alignment Flags:** Bullish alignment ($\text{Close} > EMA20 > EMA50 > EMA200$).
- **ADX & Directional Movement:** 14-period Wilder smoothed $+DI, -DI, ADX$.

### B. Momentum Subsystem (`indicators/momentum.py`)
- **RSI14:** Wilder smoothed relative strength index.
- **MACD:** Fast EMA12, Slow EMA26, Signal EMA9, Histogram delta.
- **Rate of Change (ROC):** Normalized $k$-bar percentage momentum.

### C. Volatility Subsystem (`indicators/volatility.py`)
- **ATR14:** True Range rolling Wilder average.
- **ATR Percentile:** 100-bar rolling percentile of ATR14.
- **Bollinger Bands:** 20-period SMA $\pm 2\sigma$, Bandwidth percentage.

### D. Volume Subsystem (`indicators/volume.py`)
- **Volume Ratio:** $\frac{\text{Volume}_t}{\text{SMA}_{20}(\text{Volume})}$.
- **Volume Z-Score:** Rolling 50-bar standardized volume.

---

## 2. Deterministic Regime Engine (`engine/regime/`)

Transparent, rule-based classification into 6 explicit states:
$$\text{MarketRegime} \in \{\text{BULL\_TREND}, \text{BEAR\_TREND}, \text{RANGE}, \text{HIGH\_VOLATILITY}, \text{TRANSITION}, \text{UNKNOWN}\}$$

```python
bull_points = 0
if ema50 > ema200: bull_points += 25
if ema50_slope > config.slope_threshold: bull_points += 25
if adx > config.trend_adx_threshold: bull_points += 25
if structure_state == StructurePattern.HH_HL: bull_points += 25

if volatility_percentile > config.high_vol_percentile_cutoff:
    regime = MarketRegime.HIGH_VOLATILITY
elif bull_points >= 75:
    regime = MarketRegime.BULL_TREND
elif bear_points >= 75:
    regime = MarketRegime.BEAR_TREND
elif adx < 20 and volatility_percentile < 40:
    regime = MarketRegime.RANGE
else:
    regime = MarketRegime.TRANSITION
```

---

## 3. Causal Market Structure Engine (`engine/structure/`)

### Anti-Look-Ahead Swings (`structure/swings.py`)
- Centered fractal swings are strictly validated: A swing high at candle $t$ requiring $k$ confirmation bars is knowable **only at timestamp $t+k$**.
- Every swing stores:
  - `source_swing_timestamp`: When the peak/trough actually occurred.
  - `detected_at_timestamp`: When the swing became causally confirmed ($t+k$).

### Patterns & Break of Structure (BOS) (`structure/patterns.py`)
- Higher High (HH), Higher Low (HL), Lower High (LH), Lower Low (LL).
- **Bullish BOS:** Closed candle above the most recent confirmed swing high.
- **Bearish BOS:** Closed candle below the most recent confirmed swing low.

### ATR-Aware Support / Resistance Zones (`structure/zones.py`)
- Support and resistance are constructed as **price zones** ($\text{Level} \pm 0.25 \times ATR$), never arbitrary single-dollar points.

---

## 4. Statistical Sample Guard with Normalized HHI (`engine/core/sample_guard.py`)

No pattern with a small sample size may inject false confidence into the system.

### Normalized Herfindahl-Hirschman Index (HHI)
For $k$ unique observed regimes with share $s_i = \frac{n_i}{N}$:
$$HHI = \sum_{i=1}^k s_i^2, \quad HHI_{\min} = \frac{1}{k}$$
$$HHI_{\text{norm}} = \frac{HHI - HHI_{\min}}{1.0 - HHI_{\min}} \quad (\text{for } k > 1)$$

$$\text{RegimeDiscount} = 1.0 - (HHI_{\text{norm}} \times \text{penalty\_factor})$$
$$\text{EffectiveN} = \text{round}(N_{\text{dedup}} \times \text{RegimeDiscount})$$

### Tiered Confidence Table (R16 & A16)
| Effective $N$ | Confidence Level | Weight Multiplier | Action |
|---|---|---|---|
| $< 30$ | `INSUFFICIENT_DATA` | **0.0** | Positive score contribution completely blocked |
| $30 - 99$ | `LOW` | **0.3** | Heavily discounted |
| $100 - 299$ | `MEDIUM` | **0.7** | Moderate confidence |
| $\ge 300$ | `HIGH` | **1.0** | Full confidence weight allowed |

---

## 5. Phase 2 Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A01** | Anti-Look-Ahead Mutation Test | Modifying historical candles at $t > T$ produces 100% byte-identical analysis result at $T$. |
| **A16** | Minimum Sample Guard Gate | Any setup pattern or cycle bucket with $N < 30$ receives `weight_multiplier = 0.0`. |

---

## 6. Definition of Done Checklist

- [ ] All indicators match manually verified mathematical fixtures.
- [ ] `RegimeEngine` transparently computes regime and confidence.
- [ ] `StructureEngine` constructs causal swings, BOS events, and ATR-normalized zones.
- [ ] `EffectiveSampleEstimator` correctly discounts regime-clustered observations.
- [ ] Acceptance tests **A01 and A16** passing.
