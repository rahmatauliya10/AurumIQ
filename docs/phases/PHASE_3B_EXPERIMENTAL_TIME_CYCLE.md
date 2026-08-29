# Phase 3B: Experimental Time Cycle & Promotion Gate

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Implement advanced spectral and wavelet time-cycle analysis (ACF, FFT, Continuous Wavelet Transform, Hilbert Phase) with strict point-in-time safety, zero default active weight, and a multi-criteria out-of-sample promotion gate.

---

## 1. Experimental Philosophy & Safety Principles

1. **Start at Weight 0:** No experimental cycle feature is allowed to influence production decisions until it earns its promotion through empirical walk-forward backtesting.
2. **Strict Causal Windows (R3 & A13):** Every Fourier, autocorrelation, or wavelet transform operates exclusively on a causal historical rolling window $[t - W, t]$. No forward-padding, future boundary reflection, or non-causal filtering is permitted.
3. **Abstention on Noise:** If dominant periodicity is unstable or signal-to-noise ratio is low, the engine must return `UNKNOWN` phase rather than fabricate precision.

---

## 2. Experimental Subsystems (`engine/cycles/experimental/`)

### A. Causal Autocorrelation (ACF) (`experimental/acf.py`)
- Evaluates log returns: $r_t = \ln(P_t / P_{t-1})$ over rolling causal window $W \in \{256, 384, 512\}$ bars.
- Computes sample autocorrelation lags $\rho_k$.
- Tests statistical significance ($95\%$ Bartlett confidence bounds). Weak or noisy peaks are rejected.

### B. Causal Detrended FFT Spectral Analysis (`experimental/fft.py`)
- Causal rolling window $\rightarrow$ linear/polynomial detrending $\rightarrow$ Hanning window normalization $\rightarrow$ FFT power spectrum.
- Outputs: `dominant_period_bars`, `spectral_power`, `snr` (signal-to-noise ratio).
- Evaluates multi-window stability: Compares dominant peaks across window lengths $W_1, W_2, W_3$.

### C. Continuous Wavelet Transform (CWT) (`experimental/wavelet.py`)
- Uses analytic Morlet wavelet to measure time-localized spectral energy.
- Answers the critical question: *"Is the candidate 30–50 hour cycle active right now at timestamp $t$?"*
- Computes wavelet power strictly ending at the current closed bar $t$.

### D. Hilbert Transform Phase Estimation (`experimental/phase.py`)
- Evaluates cycle phase $\theta_t \in [0^\circ, 360^\circ]$ (Trough: $\approx 0^\circ/360^\circ$, Peak: $\approx 180^\circ$).
- **Safety Gate (A05):** Phase is calculated **only if** `CycleReliability` $\ge \text{threshold}$. If cycle is noisy or unstable, phase status emits `UNKNOWN` and receives **zero positive score bonus**.

### E. Cycle Reliability Composite (`cycles/reliability.py`)
$$\text{CycleReliability} = f(\text{spectral\_snr}, \text{wavelet\_energy}, \text{period\_stability}, \text{cycles\_observed}, \text{regime\_fit})$$

---

## 3. Multi-Criteria Promotion Gate (`experimental/promotion.py` & A24)

An experimental feature is **NOT promoted** simply because in-sample Profit Factor rises slightly.

To be promoted to active scoring weight, a feature must satisfy **ALL 6 rigorous criteria**:

```python
@dataclass(frozen=True)
class PromotionCriteria:
    pf_improvement_min: float = 0.05        # 1. PF must improve by >= 5% relative to Phase 3A baseline
    expectancy_must_improve: bool = True     # 2. Expectancy in R must increase
    max_drawdown_increase_pct: float = 10.0  # 3. Max Drawdown cannot worsen by > 10% relative
    min_trade_count: int = 100               # 4. Must generate at least 100 valid trades
    min_positive_folds: int = 4              # 5. Out of 6 walk-forward OOS folds, at least 4 must beat baseline
    no_single_period_dominant: bool = True   # 6. Gains must not be concentrated in a single lucky quarter
```

### Ablation Walk-Forward Matrix
```text
Phase 3A Baseline             → PF = 1.82, DD = 8.1%,  N = 382
+ ACF                         → Evaluated against criteria
+ FFT                         → Evaluated against criteria
+ Wavelet                     → Evaluated against criteria
+ Hilbert Phase               → Evaluated against criteria
```

---

## 4. Phase 3B Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A05** | Cycle Unreliability Gate | Low SNR / unstable period emits `UNKNOWN` phase; zero positive timing bonus. |
| **A13** | Cycle Future Mutation Test | Mutating price candles at $t > T$ produces identical ACF, FFT, Wavelet, and Phase values at $T$. |
| **A24** | Multi-Criteria Promotion Gate | Reject feature if PF increases but Drawdown or OOS fold consistency fails threshold. |

---

## 5. Definition of Done Checklist

- [ ] Causal ACF, FFT, Wavelet CWT, and Hilbert Phase implemented with zero look-ahead.
- [ ] `CycleReliability` composite metric operational.
- [ ] `PromotionGate` module enforces all 6 statistical criteria.
- [ ] Automated ablation report compares Phase 3A baseline vs 3B components.
- [ ] Acceptance tests **A05, A13, A24** passing.
