# Phase 9: Machine Learning Meta-Filter (Optional Research)

> **Status:** 📋 **PLANNED (RESEARCH LAYER)**  
> **Primary Goal:** Implement a point-in-time machine learning meta-label classifier (Logistic Regression $\rightarrow$ XGBoost / LightGBM) that filters already-valid rule-based candidate signals, calibrates probabilities, and manages model lifecycle in a versioned registry.

---

## 1. Meta-Labeling Architecture (`engine/ml/`)

ML **never originates BUY signals**. The deterministic rule engine proposes candidates; the ML meta-model acts purely as a secondary filter:

```text
DETERMINISTIC ENGINE ──> Propose Candidate Setup ──> ML META-FILTER ──> ACCEPT / REJECT ──> RISK GATE ──> BUY_WINDOW
```

### Why Meta-Filtering Beats Direct ML Prediction
1. **Curse of Dimensionality Solved:** ML focuses only on the conditional probability $\mathbb{P}(\text{Win} \mid \text{Rule Candidate})$, rather than searching across all market noise.
2. **Transparent Fallback:** If ML is disabled or retrained, the system falls back safely to the deterministic baseline.

---

## 2. Point-in-Time Dataset Construction (`engine/ml/dataset.py`)

- **Feature Vector $X_t$:** Point-in-time features knowable at candidate signal timestamp $t$:
  - Regime confidence, trend slopes, normalized EMA distances, RSI delta, ATR percentile.
  - Structure state, distance to support zone, normalized XAUT/XAU basis z-score.
  - Session expectancy score, swing duration age percentile, cycle reliability.
- **Binary Label $y_t$ (Meta-Label):**
  $$y_t = \begin{cases} 1 & \text{if candidate reached TP1 before SL (-1R)} \\ 0 & \text{if candidate reached SL first or timed out with negative return} \end{cases}$$

---

## 3. Model Progression & Simplest-Winner Rule

Models are trained in strict order of increasing complexity:

1. **Baseline Model:** Logistic Regression with $L_2$ regularization.
2. **Non-Linear Candidate A:** XGBoost with constrained tree depth ($\le 4$) to prevent overfitting.
3. **Non-Linear Candidate B:** LightGBM with leaf-wise regularization.

> **Principle:** Keep the simplest model that demonstrates statistically significant out-of-sample expectancy improvement after all trading frictions.

---

## 4. Probability Calibration (`engine/ml/calibration.py`)

Raw tree scores are not true probabilities. Probabilities are calibrated using:
- **Platt Scaling (Logistic Sigmoid)** or **Isotonic Regression**.
- Validated via **Brier Score** and **Reliability Calibration Curves**.

---

## 5. Model Version Registry (`apps/machine_learning/models.py`)

```python
class ModelVersion(models.Model):
    name = models.CharField(max_length=64)
    model_type = models.CharField(max_length=32) # LOGISTIC, XGBOOST, LIGHTGBM
    version = models.CharField(max_length=32, unique=True)
    brier_score = models.DecimalField(max_digits=6, decimal_places=4)
    oos_expectancy_gain_r = models.DecimalField(max_digits=6, decimal_places=3)
    is_active = models.BooleanField(default=False)
    artifact_path = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## 6. Definition of Done Checklist

- [ ] Point-in-time dataset builder produces zero look-ahead training sets.
- [ ] Walk-forward cross-validation enforces purging and embargo.
- [ ] Logistic, XGBoost, and LightGBM models evaluated with probability calibration.
- [ ] Model registry supports zero-downtime rollback to rule-only baseline.
