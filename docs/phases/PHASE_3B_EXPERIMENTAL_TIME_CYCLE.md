# Phase 3B: Experimental Spectral Cycle Research & Promotion Gate

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source Baseline:** `main` @ `823d176b140f3823f1e41d1071b4dc98bf558eab`  
> **Current XAUUSD Target Status:** 🧪 **REVALIDATED / IMPLEMENTED (RESEARCH ONLY, PRODUCTION WEIGHT = 0.0)**

---

## Current Phase 3B Governance Declarations

| Metric / Governance Area | Formal Status | Operational Interpretation |
|---|:---:|---|
| **Phase 3B Architecture** | ✅ `REVALIDATED / IMPLEMENTED` | Mathematical & spectral engines (ACF, FFT, Wavelet, Hilbert) fully operational. |
| **Spectral Algorithms** | 🔬 `RESEARCH ONLY` | Used exclusively for descriptive frequency/phase diagnostics, not live scoring. |
| **XAUUSD Empirical Detection Thresholds** | 🟡 `NOT_CONFIGURED / NOT_FROZEN` | No Bartlett bound, FFT power cutoff, Wavelet COI, or Hilbert stability frozen for XAUUSD. |
| **XAUUSD Empirical Reliability Thresholds** | 🟡 `NOT_CONFIGURED / NOT_FROZEN` | Dispersion, agreement, and scoring bands strictly `None`; reliability score locks to `0.0` (`UNRELIABLE`). |
| **XAUUSD Promotion Threshold Policy** | 🟡 `NOT_CONFIGURED / NOT_FROZEN` | No speculative hurdle numbers injected for XAUUSD. |
| **Current XAUUSD Promotion Evaluation** | 🚫 `POLICY_NOT_CONFIGURED` | Uncalibrated profile evaluations deterministically report `POLICY_NOT_CONFIGURED`. |
| **Phase 6 Promotion Dependency** | 🔒 `BLOCKED_BY_PHASE6` | Evaluation requires verified empirical XAUUSD backtest baseline from Phase 6. |
| **Production Weight** | 🔒 `0.0 HARD LOCKED` | Permanently 0.0 across all dataclass, engine, and database check constraints. |
| **Historical XAUT Phase 3B Evidence** | 🧊 `PRESERVED` | Historical XAUT reference numbers isolated under explicit `legacy_xaut_research_profile()`. |
| **Phase 4+ Direction / Timing / Execution** | ⏸️ `NOT STARTED` | Zero BUY/SELL directional score, timing score, or execution logic. |

---

## 1. XAUUSD Research Governance & Architectural Invariants

1. **Hard-Locked Production Weight = 0.0:**  
   `Cycle3BExperimentalSnapshot.production_weight` is hard-locked to `0.0` with `init=False` and a database `CheckConstraint` (`phase3b_production_weight_locked_to_zero`). Even `PROMOTABLE` research evaluations produce `0.0` production weight.
2. **Explicit Profile Segregation (`Cycle3BResearchProfile`):**  
   Target XAUUSD uses `uncalibrated_xauusd_research_profile()`, where all empirical detection, reliability, and promotion fields are strictly `None`. Historical XAUT constants (1.96 Bartlett, 0.15 FFT, 0.40 COI, 0.60 Hilbert, 15/30% dispersion, 60/35/15 bands, 80/50% agreement) are quarantined exclusively within `legacy_xaut_research_profile()`. Incomplete explicit `LEGACY_REFERENCE` profiles are strictly rejected. Configured XAUUSD research profiles require an explicit non-empty `timeframe`.
3. **Causal Descriptive Operation:**  
   Uncalibrated XAUUSD computes pure descriptive mathematical facts (autocorrelation series, dominant period, PSD top frequencies, wavelet scale energy, instantaneous phase/amplitude) while setting `is_significant = False`, `is_cycle_detected = False`, `is_clean_endpoint = False`, `is_endpoint_reliable = False`, `reliability_score = 0.0`, and `status = UNRELIABLE` (`CALIBRATION_REQUIRED`).
4. **Deterministic 4-Stage Promotion Precedence (`evaluate_promotion_eligibility`):**  
   - **Stage A:** Promotion policy not configured $\rightarrow$ `POLICY_NOT_CONFIGURED`.
   - **Stage B:** Policy configured, but baseline is missing, non-empirical, not XAUUSD, not PIT-safe, not Phase 6 validated, or timeframe mismatch $\rightarrow$ `BLOCKED_BY_PHASE6`.
   - **Stage C:** Valid XAUUSD Phase 6 empirical baseline exists, but hurdles fail (including insufficient trade count) $\rightarrow$ `FAILED`.
   - **Stage D:** Valid XAUUSD Phase 6 empirical baseline exists and all hurdles pass $\rightarrow$ `PROMOTABLE` (research eligibility only, `production_weight = 0.0`).
5. **Closed-Candle & Time-Grid Isolation (PIT-Safe):**  
   - Unclosed candles at or before $T \rightarrow$ `IncompleteCandleError`.
   - Unclosed candles strictly after $T \rightarrow$ completely ignored; snapshot at $T$ identical to baseline.
   - Future closed candles after $T \rightarrow$ completely ignored; snapshot at $T$ identical to baseline.
   - Broken time-grid or irregular spacing $\rightarrow$ fails closed with `0.0` reliability.

---

## 2. Experimental Subsystems (`engine/cycles/experimental/`)

### A. Causal Autocorrelation (ACF) (`acf.py`)
- Evaluates linear detrended prices over trailing causal lookback $W \ge 32$ bars.
- Computes causal sample autocorrelation:
  $$r_k = \frac{\sum_{t=k+1}^N (x_t - \bar{x})(x_{t-k} - \bar{x})}{\sum_{t=1}^N (x_t - \bar{x})^2}$$
- In uncalibrated mode: returns descriptive ACF series and candidate dominant lag, but `is_significant = False` and `confidence_bound = 0.0`. Uses `acf_bartlett_z_multiplier` when configured without hardcoded 30/60/100 sample-tier leakage.

### B. Causal Detrended FFT Spectral Analysis (`fft.py`)
- Trailing window $\rightarrow$ linear detrending $\rightarrow$ zero-mean centering $\rightarrow$ Hann window taper normalization $\rightarrow$ Real FFT power spectrum.
- Calculates Power Spectral Density (PSD), dominant frequency, dominant period, power ratio, and normalized spectral entropy.
- In uncalibrated mode: returns descriptive spectrum and top frequencies, but `is_cycle_detected = False`. Zero fallback to legacy constants on explicit profiles.

### C. Continuous Wavelet Transform (CWT) (`wavelet.py`)
- Uses analytic Morlet wavelet (`pywt.cwt`) over trailing closed history.
- Measures time-localized multi-scale energy distribution.
- In uncalibrated mode: returns descriptive scales and energy ratio, but `is_clean_endpoint = False`. Zero fallback to legacy constants on explicit profiles.

### D. Hilbert Transform Instantaneous Phase (`hilbert.py`)
- Computes analytic signal $z(t) = x(t) + i \mathcal{H}[x](t) = A(t) e^{i \theta(t)}$.
- Extracts instantaneous phase $\theta(t) \in [-\pi, \pi]$ and instantaneous amplitude $A(t)$.
- Evaluates phase velocity and stability over trailing 5 bars.
- In uncalibrated mode: returns descriptive phase/amplitude, but `is_endpoint_reliable = False`. Zero fallback to legacy constants on explicit profiles.

### E. Cycle Reliability Composite (`reliability.py`)
- Consolidates ACF, FFT, Wavelet, and Hilbert spectral evidence.
- In uncalibrated mode: computes descriptive consensus period, but locks `reliability_score = 0.0` and `reliability_status = UNRELIABLE` (`CALIBRATION_REQUIRED`).
- Classifies reliability using configurable `reliability_high_min_agreement_pct` and `reliability_moderate_min_agreement_pct` without hardcoded 80/50 or duplicate wavelet 0.40 leakage.

---

## 3. Phase 3B Acceptance & Targeted Test Suite

| Test Suite | File | Tests | Assertion Criteria | Status |
|---|---|:---:|---|:---:|
| **XAUUSD Acceptance** | `tests/acceptance/test_xauusd_phase3b.py` | 31 | Profile governance, zero defaults, symmetric mismatch rejection, deterministic promotion precedence, closed-candle split, hostile lock, naive dt, runtime TF | ✅ PASS |
| **Historical Targeted** | `tests/unit/test_phase3b_targeted.py` | 27 | P3B-01 through P3B-27 historical invariant preservation | ✅ PASS |
| **A05 Acceptance** | `tests/acceptance/test_a05_causal_spectral_isolation.py` | 1 | Future candle perturbation produces identical spectral outputs at $T$ | ✅ PASS |
| **A13 Acceptance** | `tests/acceptance/test_a13_cycle_reliability_stability.py` | 3 | High consensus, subthreshold sample zeroing, material disagreement zeroing | ✅ PASS |
| **A24 Acceptance** | `tests/acceptance/test_a24_experimental_promotion_gate.py` | 3 | Rejection on non-empirical baseline, verified empirical promotion, fold concentration guard | ✅ PASS |

**Total Targeted Tests:** 65/65 passing (0 failures, 0 warnings).  
**Full Regression Suite:** 452/452 passing across whole repository.
