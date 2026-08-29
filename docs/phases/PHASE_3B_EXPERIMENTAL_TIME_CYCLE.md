# Phase 3B: Experimental Spectral Cycle Research & Promotion Gate

> **Status:** 🧪 **IMPLEMENTED, VERIFIED & PENDING HUMAN REVIEW**  
> **Primary Goal:** Implement advanced spectral and wavelet time-cycle analysis (Causal ACF, Causal FFT, Continuous Wavelet Transform, Causal Hilbert Phase) with strict point-in-time safety, zero default active weight, and a multi-criteria out-of-sample promotion gate.

---

## 1. Experimental Philosophy & Safety Principles

1. **Hard-Locked Production Weight = 0.0:** No experimental cycle feature influences production scoring until it earns promotion through empirical walk-forward backtesting against an empirical Phase 3A baseline.
2. **Strict Causal Trailing Windows (R3, A05, A13):** Every Fourier, autocorrelation, wavelet, or Hilbert transform operates exclusively on a causal historical rolling window $[t - W, t]$. No forward-padding, future boundary reflection, or non-causal filtering is permitted.
3. **Fail-Closed on Noise & Disagreement:** If dominant periodicity is unstable or spectral methods disagree ($> 30\%$ dispersion), reliability collapses to `0.0` (`UNRELIABLE`).

---

## 2. Experimental Subsystems (`engine/cycles/experimental/`)

### A. Causal Autocorrelation (ACF) (`engine/cycles/experimental/acf.py`)
- Evaluates linear detrended prices over trailing causal lookback $W \ge 32$ bars.
- Computes causal sample autocorrelation:
  $$r_k = \frac{\sum_{t=k+1}^N (x_t - \bar{x})(x_{t-k} - \bar{x})}{\sum_{t=1}^N (x_t - \bar{x})^2}$$
- Tests statistical significance ($95\%$ Bartlett confidence bounds $\pm \frac{1.96}{\sqrt{N}}$).
- Guarded by Effective Sample Estimator ($n_{eff} \ge 30$).

### B. Causal Detrended FFT Spectral Analysis (`engine/cycles/experimental/fft.py`)
- Trailing window $\rightarrow$ linear detrending $\rightarrow$ zero-mean centering $\rightarrow$ Hann window taper normalization $\rightarrow$ Real FFT power spectrum.
- Calculates Power Spectral Density (PSD), dominant frequency $f_{\text{dom}}$, dominant period $P_{\text{dom}} = 1 / f_{\text{dom}}$, spectral power ratio $P(f_{\text{dom}}) / \sum P_k$, and normalized spectral entropy.
- Masked DC component ($f=0$) and search bounded by research-safe limits ($4 \le P \le N/2$).

### C. Continuous Wavelet Transform (CWT) (`engine/cycles/experimental/wavelet.py`)
- Uses analytic Morlet wavelet (`pywt.cwt`) over trailing closed history.
- Measures time-localized multi-scale energy distribution.
- **Cone of Influence (COI) & Edge Handling:** Evaluates endpoint boundary contamination. If dominant cycle scale is heavily edge-contaminated at $T$, `is_clean_endpoint` is marked `False`.

### D. Hilbert Transform Instantaneous Phase (`engine/cycles/experimental/hilbert.py`)
- Computes analytic signal $z(t) = x(t) + i \mathcal{H}[x](t) = A(t) e^{i \theta(t)}$.
- Extracts instantaneous phase $\theta(t) \in [-\pi, \pi]$ and instantaneous amplitude $A(t)$.
- **Endpoint Distortion Guard:** Phase velocity $\frac{d\theta}{dt}$ and unwrapped phase progression over trailing 5 bars evaluate phase stability. If stability $< 0.60$ or $N < 48$, `is_endpoint_reliable` is `False`.

### E. Cycle Reliability Composite (`engine/cycles/experimental/reliability.py`)
- Consolidates ACF, FFT, Wavelet, and Hilbert evidence.
- Calculates cross-method agreement dispersion:
  - Dispersion $\le 15\% \rightarrow 100\%$ agreement.
  - Dispersion $\le 30\% \rightarrow 65\%$ agreement.
  - Dispersion $> 30\% \rightarrow 0\%$ agreement (material spectral disagreement).
- **Strict Zero-Reliability Gates (A13, P3B-10):**
  - If $n_{eff} < 30.0$ or methods diverge $> 30\% \rightarrow$ `reliability_score = 0.0`, `status = UNRELIABLE`.

---

## 3. Multi-Criteria Empirical Promotion Gate (`engine/cycles/experimental/promotion.py` & A24)

An experimental feature is **NOT promoted** simply because in-sample Profit Factor rises slightly.

To be promoted to active scoring weight, a feature must satisfy **ALL 7 rigorous empirical criteria**:

1. `BaselineBenchmark.is_empirical` MUST be `True` (P3B-11).
2. Experimental backtest must have $\ge 100$ valid trades (P3B-12).
3. Profit Factor improvement $\ge +5.0\%$ vs baseline (P3B-13).
4. Expectancy in R must increase ($\text{Exp}_R > \text{Base\_Exp}_R$).
5. Max Drawdown deterioration must be $\le 10.0\%$ worse than baseline (P3B-14).
6. At least 4 of 6 walk-forward OOS folds must beat baseline (P3B-15).
7. Effective sample size $n_{eff} \ge 30.0$.

---

## 4. Phase 3B Acceptance & Targeted Test Suite

| Test ID | Test Name | Assertion Criteria | Status |
|---|---|---|:---:|
| **A05** | Causal Spectral Future Invariance | Mutating price candles at $t > T$ produces 100% identical ACF, FFT, Wavelet, and Hilbert outputs at $T$. | ✅ PASS |
| **A13** | Cycle Reliability Consensus Gate | High consensus yields high score; material disagreement or $n_{eff} < 30$ strictly zeroes reliability. | ✅ PASS |
| **A24** | Multi-Criteria Promotion Gate | Rejects promotion on non-empirical baseline, trade count $< 100$, PF diff $< 5\%$, DD worse $> 10\%$, or folds $< 4/6$. | ✅ PASS |
| **P3B-01** | ACF Future Mutation Invariance | ACF outputs identical under future perturbation. | ✅ PASS |
| **P3B-02** | FFT Future Mutation Invariance | FFT outputs identical under future perturbation. | ✅ PASS |
| **P3B-03** | Wavelet Future Mutation Invariance | Wavelet outputs identical under future perturbation. | ✅ PASS |
| **P3B-04** | Hilbert Future Mutation Invariance | Hilbert outputs identical under future perturbation. | ✅ PASS |
| **P3B-05** | Synthetic Sine Period Recovery | 16-bar and 32-bar pure sinusoids detected accurately by FFT and ACF. | ✅ PASS |
| **P3B-06** | Constant / Flat Series Handling | Flat series handled cleanly with zero division protection. | ✅ PASS |
| **P3B-07** | Insufficient Lookback Handling | Series $< 32$ bars returns safe zero-result dataclasses. | ✅ PASS |
| **P3B-08** | NaN & Malformed Input Handling | Sanitizes dirty input without unhandled exceptions. | ✅ PASS |
| **P3B-09** | Spectral Disagreement Gate | Divergent methods ($> 30\%$) collapse reliability score to 0.0. | ✅ PASS |
| **P3B-10** | Effective N Guard | $n_{eff} < 30$ strictly forces reliability score to 0.0. | ✅ PASS |
| **P3B-11** | Non-Empirical Baseline Gate | Non-empirical baseline sets `status = BASELINE_NOT_EMPIRICAL`, `production_weight = 0.0`. | ✅ PASS |
| **P3B-12** | Trades Count Guard | $< 100$ trades cannot promote. | ✅ PASS |
| **P3B-13** | PF Improvement Hurdle | $< +5.0\%$ PF improvement cannot promote. | ✅ PASS |
| **P3B-14** | Drawdown Deterioration Hurdle | $> +10.0\%$ drawdown deterioration cannot promote. | ✅ PASS |
| **P3B-15** | Walk-Forward Consistency | $< 4/6$ folds cannot promote. | ✅ PASS |
| **P3B-16** | Zero Production Weight Contract | `Cycle3BExperimentalSnapshot.production_weight` is permanently 0.0. | ✅ PASS |
| **P3B-17** | Engine AST Purity | `engine/cycles/experimental/` contains zero Django imports. | ✅ PASS |
| **P3B-18** | Experimental Snapshot Persistence | Persists `ExperimentalCycleSnapshotRecord` with `experimental_version` unique composite key. | ✅ PASS |

---

## 5. Definition of Done Checklist

- [x] Causal ACF, FFT, Wavelet CWT, and Hilbert Phase implemented with zero look-ahead.
- [x] `CycleReliability` composite metric operational with cross-method agreement.
- [x] `PromotionGate` module enforces all 7 statistical and empirical criteria.
- [x] `production_weight` hard-locked to 0.0 while baseline is non-empirical.
- [x] Django ORM model `ExperimentalCycleSnapshotRecord` created and migrated.
- [x] Acceptance tests **A05, A13, A24** passing.
- [x] Full regression suite passing **117/117 tests** in Docker.
