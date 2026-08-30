# Phase 9: Machine Learning Meta-Filter & Probability Calibration (XAUUSD BUY + SELL)

> **Status:** 📋 **PLANNED (RESEARCH LAYER)**  
> **Primary Goal:** Implement a point-in-time machine learning meta-label classifier (Logistic Regression $\rightarrow$ XGBoost / LightGBM) that filters already-valid rule-based candidate signals (**both BUY and SELL setups**), calibrates outcome probabilities, and manages model lifecycles in a versioned registry with instant fallback.

---

## 1. Meta-Labeling Architecture (`engine/ml/`)

Machine learning **never originates signals from raw market noise**. The deterministic rule engine proposes candidate setups; the ML meta-model acts purely as a secondary filter:

```text
DETERMINISTIC RULE ENGINE ──> Propose Candidate Setup (BUY / SELL)
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

### Why Meta-Filtering Beats Direct End-to-End ML
1. **Solves the Signal-to-Noise Problem:** ML focuses only on the conditional probability $\mathbb{P}(\text{Win} \mid \text{Rule Candidate})$, rather than searching across unstructured price noise.
2. **Transparent Deterministic Fallback:** If ML is disabled, degraded, or in maintenance, the system immediately and deterministically falls back to the rule-only baseline.

---

## 2. Point-in-Time Dataset Construction (`engine/ml/dataset.py`)

### A. Side-Aware Feature Vector $X_t$
Point-in-time features strictly knowable at candidate signal timestamp $t$:
- **Macro & Regime Features:** Market regime classification, regime confidence, DXY correlation, macro calendar buffer.
- **Trend & Momentum Features:** Multi-timeframe EMA slopes (15m, 1H, 4H, 1D), EMA alignment score, RSI delta, MACD histogram velocity, normalized ADX.
- **Structure & Volatility Features:** Distance to active support/resistance zone, zone touch count, ATR percentile, normalized realized volatility.
- **Time-Cycle Features:** Phase 3A session expectancy, swing duration maturity percentile, calendar seasonality stability.
- **Setup Context:** Direction indicator (`+1` for Long, `-1` for Short), planned risk-to-reward ratio.

*(Note: Mandatory XAUT crypto basis features are removed from the active XAUUSD feature matrix; historical baseline features remain available for legacy audit benchmarking).*

### B. Binary Meta-Label $y_t$ (Side-Aware Triple-Barrier Outcome)
$$y_t = \begin{cases} 1 & \text{if candidate reached TP1 before SL (-1.0R)} \\ 0 & \text{if candidate reached SL first or timed out with negative return} \end{cases}$$

---

## 3. Directional Architecture: Shared vs Dedicated Models

During Phase 9 research, two architectural paradigms will be evaluated under walk-forward validation:

1. **Shared Unified Model:** A single meta-classifier trained on all candidates with `direction` as a feature.
2. **Dedicated Directional Models:** Two specialized models trained independently:
   - `XauLongMetaFilter`: Evaluates only `BUY` candidate setups against upward momentum and support dynamics.
   - `XauShortMetaFilter`: Evaluates only `SELL` candidate setups against downward momentum and resistance dynamics.

> **Selection Principle:** The system will adopt the architecture that delivers higher out-of-sample Expectancy Gain ($\Delta \mathbb{E}[R]$) and lower Brier Score after all simulated frictions.

---

## 4. Model Progression & Simplest-Winner Rule

Models are evaluated in strict order of increasing complexity:

1. **Baseline Model:** Logistic Regression with $L_2$ regularization (interpretable feature coefficients).
2. **Non-Linear Model A:** XGBoost with constrained tree depth ($\le 4$) and column subsampling to prevent overfitting.
3. **Non-Linear Model B:** LightGBM with leaf-wise regularization.

> **Simplest-Winner Contract:** A more complex model is selected only if it demonstrates a statistically significant out-of-sample expectancy improvement ($\ge +0.05\text{R}$) over Logistic Regression across all walk-forward folds.

---

## 5. Point-in-Time Anti-Leakage & Walk-Forward Validation

- **Purging:** Training samples whose triple-barrier outcome window crosses into test/validation folds are strictly purged.
- **Embargo:** A 24-hour post-boundary embargo buffer is applied to eliminate serial correlation leakage.
- **OOS Isolation:** Candidate selection and probability calibration parameters are fitted strictly on Training + Validation folds; OOS folds are evaluated downstream without parameter tuning.

---

## 6. Probability Calibration (`engine/ml/calibration.py`)

Raw tree scores are not true calibrated probabilities. Model outputs are calibrated using:
- **Platt Scaling (Logistic Sigmoid)** or **Isotonic Regression**.
- Validation via **Brier Score**, **Reliability Curves**, and **Expected Calibration Error (ECE)**.

---

## 7. Model Version Registry & Instant Rollback (`apps/machine_learning/models.py`)

```python
class ModelVersion(models.Model):
    name = models.CharField(max_length=64)
    direction_scope = models.CharField(max_length=16, default="UNIFIED") # UNIFIED, LONG_ONLY, SHORT_ONLY
    model_type = models.CharField(max_length=32) # LOGISTIC, XGBOOST, LIGHTGBM
    version = models.CharField(max_length=32, unique=True)
    brier_score = models.DecimalField(max_digits=6, decimal_places=4)
    expected_calibration_error = models.DecimalField(max_digits=6, decimal_places=4)
    oos_expectancy_gain_r = models.DecimalField(max_digits=6, decimal_places=3)
    is_active = models.BooleanField(default=False)
    artifact_path = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
```

### Deterministic Rollback Guarantee
If an active model's live performance degrades or if `is_active` is toggled to `False`, the signal pipeline immediately and seamlessly transitions to the pure rule-based engine with zero downtime or service restart.

---

## 8. Definition of Done Checklist

- [ ] Point-in-time dataset builder produces zero look-ahead training sets for XAUUSD BUY and SELL setups.
- [ ] Purging and embargo walk-forward cross-validation engine operational.
- [ ] Logistic Regression, XGBoost, and LightGBM models trained and evaluated.
- [ ] Probability calibration verified via Brier Score and reliability diagrams.
- [ ] Model registry supports instant zero-downtime rollback to rule-only baseline.
- [ ] Static AST scan confirms zero trading execution APIs in machine learning modules.
