# Phase 9: Machine Learning Meta-Filter & Probability Calibration (XAUUSD BUY + SELL)

> **Historical XAUT Baseline Status:** ⚪ `N/A`  
> **Current XAUUSD Target Status:** 📋 `HOLD — TARGET SPECIFICATION (NOT YET IMPLEMENTED)`  
> **Primary Goal:** Specify a point-in-time machine learning meta-label classifier (Logistic Regression $\rightarrow$ XGBoost $\rightarrow$ LightGBM) that filters already-valid rule-based candidate signals (**both BUY and SELL setups**), calibrates outcome probabilities, and manages model lifecycles in a versioned registry with instant rollback.

---

## 1. Meta-Labeling Architecture (`engine/ml/`)

Machine learning **never originates signals from raw market noise**. The deterministic rule engine proposes candidate setups; the ML meta-model acts purely as a secondary filter:

```text
DETERMINISTIC RULE ENGINE ──► Propose Candidate Setup (BUY / SELL)
                                              │
                                              ▼
                                      ML META-FILTER
                                 (Predict P(Win | Candidate))
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                     P(Win) >= Threshold             P(Win) < Threshold
                         (ACCEPT)                         (REJECT)
                              │                               │
                              ▼                               ▼
                          RISK GATE                      SUPPRESS / WAIT
                              │
                              ▼
                      BUY_WINDOW / SELL_WINDOW
```

### Core Architecture Invariants
1. **Solves the Signal-to-Noise Problem:** ML focuses only on the conditional probability $\mathbb{P}(\text{Win} \mid \text{Rule Candidate})$, rather than searching across unstructured price noise.
2. **Transparent Deterministic Fallback:** If ML is disabled, degraded, or in maintenance, the system immediately and deterministically falls back to the rule-only baseline without server restart.
3. **Zero Execution Code (R1):** Machine learning modules contain zero order routing or execution capabilities.

---

## 2. Point-in-Time Dataset & Feature Matrix

### A. Side-Aware Feature Vector $X_t$
Point-in-time features strictly knowable at candidate signal timestamp $t$:
- **Macro & Regime Features:** Market regime classification, regime confidence, DXY correlation, macro calendar buffer.
- **Trend & Momentum Features:** Multi-timeframe EMA slopes (15m, 1H, 4H, 1D), EMA alignment score, RSI delta, MACD histogram velocity, normalized ADX.
- **Structure & Volatility Features:** Distance to active support/resistance zone, zone touch count, ATR percentile, normalized realized volatility.
- **Time-Cycle Features:** Phase 3A session expectancy, swing duration maturity percentile, calendar seasonality stability.
- **Setup Context:** Direction indicator (`+1` for Long, `-1` for Short), planned risk-to-reward ratio.
*(Note: Mandatory USDT normalization and crypto basis dependencies are removed from the active XAUUSD feature matrix).*

### B. Binary Meta-Label $y_t$ (Side-Aware Triple-Barrier Outcome)
$$y_t = \begin{cases} 1 & \text{if candidate reached TP before SL (-1.0R)} \\ 0 & \text{if candidate reached SL first or timed out with negative return} \end{cases}$$

---

## 3. Directional Architecture & Model Progression

### A. Directional Modeling Paths (To Be Researched in Phase 9)
1. **Shared Unified Model:** A single meta-classifier trained on all candidates with `direction` as a feature.
2. **Dedicated Directional Models:** Two specialized models trained independently (`XauLongMetaFilter` and `XauShortMetaFilter`).

### B. Model Sequence & Simplest-Winner Contract
1. **Baseline:** Logistic Regression with $L_2$ regularization (interpretable feature coefficients).
2. **Non-Linear Model A:** XGBoost (hyperparameters tuned via walk-forward cross-validation).
3. **Non-Linear Model B:** LightGBM (leaf-wise regularized gradient boosting).

> **Simplest-Winner Rule:** A more complex model is selected only if it demonstrates a statistically significant out-of-sample expectancy improvement over Logistic Regression across all walk-forward folds without overfitting. Exact promotion thresholds and tree hyperparameters will be determined from empirical OOS evidence during Phase 9 research.

---

## 4. Probability Calibration & Anti-Leakage Validation

- **Purging & Embargo:** Training samples whose triple-barrier outcome window crosses into test/validation folds are purged. Embargo buffers are derived directly from the label horizon to prevent serial correlation leakage.
- **Probability Calibration:** Raw model outputs are calibrated using **Platt Scaling (Logistic Sigmoid)** or **Isotonic Regression**, validated via **Brier Score**, **Reliability Curves**, and **Expected Calibration Error (ECE)**.

---

## 5. Definition of Done Checklist (Pending Phase 9 Implementation)

- [ ] Point-in-time dataset builder produces zero look-ahead training sets for XAUUSD BUY and SELL setups.
- [ ] Purging and embargo walk-forward cross-validation engine operational.
- [ ] Logistic Regression, XGBoost, and LightGBM models trained and evaluated.
- [ ] Probability calibration verified via Brier Score and reliability diagrams.
- [ ] Model registry supports instant zero-downtime rollback to rule-only baseline.
- [ ] Static AST scan confirms zero trading execution APIs in machine learning modules.
