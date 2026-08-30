# Phase 3B: Experimental Time-Cycles & Spectral Signal Lab

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟡 `REVALIDATION REQUIRED (PRODUCTION WEIGHT = 0.0)`  
> **Primary Goal:** Provide an isolated research lab for spectral signal processing (Causal ACF, FFT, Wavelet CWT, Hilbert Phase) while strictly locking active production scoring weight to `0.0`.

---

## 1. Experimental Methods Overview

Advanced mathematical cycle analysis tools operating under strict causal expanding/rolling windows:
1. **Causal Autocorrelation Function (ACF):** Estimates dominant recurring cycle lags without forward data leakage.
2. **Fast Fourier Transform (FFT):** Spectral peak detection on detrended price series.
3. **Continuous Wavelet Transform (CWT):** Morlet wavelet power spectrum and scalograms for time-localized frequency analysis.
4. **Hilbert-Huang Transform:** Analytic signal representation and instantaneous phase unwrapping.

---

## 2. Strict Production Lock & Promotion Gate (R17)

```text
EXPERIMENTAL SPECTRAL MODULES (CWT / FFT / Hilbert)
                       │
                       ▼
          Scoring Weight Locked to 0.0
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
Does NOT meet 7 criteria     Meets ALL 7 criteria
      (STAYS 0.0)             (Promotion Gate Review)
```

### 7 Mandatory Promotion Criteria
1. **Out-of-Sample Expectancy Gain:** Demonstrates statistically significant expectancy improvement ($\ge +0.05R$) across all walk-forward folds.
2. **Non-Redundancy:** Correlation with Phase 3A session/swing features is $r < 0.30$.
3. **Stationarity Proof:** Dominant cycle frequencies show persistent out-of-sample stability.
4. **Friction Resilience:** Retains positive expectancy after deducting spreads, fees, and adverse slippage.
5. **Execution Latency Safety:** Computation latency is $< 50\text{ms}$ per 15m candle.
6. **Regime Consistency:** Delivers positive information coefficient across Bull, Bear, and Range regimes.
7. **AST Import Isolation:** Maintains zero Django or external service dependencies in calculation code.

---

## 3. Definition of Done Checklist

### Historical Baseline
- [x] Causal ACF, FFT, Morlet CWT, and Hilbert Phase calculation modules implemented.
- [x] Production weight locked to `0.0` in configuration and verified (`P3B-24`).
- [x] Plotly CWT scalogram visualization integrated into Time Cycle Lab.

### Target XAUUSD Scope
- [ ] Evaluate spectral feature stability against multi-year XAUUSD datasets in Phase 6 ablation testing.
