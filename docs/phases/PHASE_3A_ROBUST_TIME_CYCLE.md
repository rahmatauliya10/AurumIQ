# Phase 3A: Robust Time Cycle Features

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, RIGOROUSLY VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟢 **ARCHITECTURE IMPLEMENTED / CALIBRATION PENDING_DATA**

---

## XAUUSD Migration & Calibration Architecture

### 1. Implementation Status
- **XAUUSD Phase 3A Architecture:** ✅ **IMPLEMENTED** (`CalibrationStatus`, `Cycle3AProfile`, pure-Python calibration pipeline in `engine/cycles/calibration.py`, engine profile isolation).
- **XAUUSD Empirical Calibration:** 🟡 **PENDING_DATA** (multi-year historical spot data is not bundled in the repo; zero synthetic or scraped numbers are fabricated).
- **XAUUSD Candidate Artifact:** 🔒 **NOT PRODUCTION FROZEN** (candidate artifacts strictly produce 0.0 production scores).
- **XAUUSD Production Weights:** 🔒 **NOT_FROZEN** (weights remain NOT_FROZEN until Phase 6 walk-forward/backtest/ablation validation).
- **Hidden Legacy Numerical Fallback:** 🚫 **NONE** (zero numerical fallbacks in uncalibrated or candidate XAUUSD execution paths).

### 2. Architectural Components & Isolation
1. **Explicit Calibration Governance (`engine/cycles/profile.py`):**
   - `CalibrationStatus` enum: `LEGACY_REFERENCE`, `PENDING_DATA`, `CANDIDATE_NOT_FROZEN`, `PRODUCTION_FROZEN`.
   - `Cycle3AProfile.legacy_xaut_profile()` preserves frozen historical XAUT reference parameters (15.0 / 20.0 / 5.0 / 5.0 / 2.0 scoring, 30.0 sample threshold, 0.60 stability, 30m pre/post blackout).
   - `Cycle3AProfile.uncalibrated_xauusd_profile()` contains `None` for all empirical scoring constants, guaranteeing zero hidden fallback to legacy numbers.
   - All mapping and sequence containers in profiles and artifacts enforce defensive immutability (`MappingProxyType` / tuples).
2. **Fail-Safe Descriptive Operation:**
   - Uncalibrated XAUUSD computes deterministic facts (DST-aware session labels via `zoneinfo`, progress, local times, causal swing market_age & known_age, calendar DOW/hour/month/month-end, macro feed health, event proximity).
   - Empirical scores are strictly `0.0` with `sample_quality = INSUFFICIENT` and status `PENDING_DATA`.
   - Candidate artifacts (`CANDIDATE_NOT_FROZEN`) expose descriptive candidate statistics for audit while strictly blocking production cycle scores (`0.0`).
3. **Pure-Python Empirical Calibration Pipeline (`engine/cycles/calibration.py`):**
   - Implements `CalibrationProvenance` with strict chronological validation (`data_start <= data_end <= as_of`).
   - Implements `Cycle3ACalibrationArtifact` with defensive immutability.
   - `calibrate_session_expectancy()` eliminates default sample thresholds or t-statistic cuts; requires explicit sample evaluation mappings (Raw N != Effective N).
   - `calibrate_swing_durations()` separates `known_duration` (from `detected_at`) and `market_duration` (from `timestamp`), strictly rejecting unconfirmed or future swings.
   - `build_profile_from_artifact()` generates `CANDIDATE_NOT_FROZEN` profiles with `None` for all production scoring fields.
   - Point-in-time safe, pure Python, zero Django imports, zero network calls.
4. **Closed-Candle & Causality Invariants:**
   - Operational decisions strictly require closed candles (`is_closed=True`); unclosed candles raise `IncompleteCandleError`.
   - Future candle mutations and revisions cannot modify snapshots at timestamp $T$.
   - Swing maturity strictly filters `detected_at <= as_of` prior to selection; future swing confirmations at $T$ cannot alter historical calculations.

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **COMPLETED, RIGOROUSLY VERIFIED & FROZEN**  
> **Primary Goal:** Implement deterministic, point-in-time robust timing features (Session Cycle, Swing Duration, Macro Event Point-in-Time Gate, and Calendar Seasonality) with strict statistical sample guards, statistical significance gates, fail-closed effective-N, and zero future lookahead.

### 1. Subsystem Architecture (`engine/cycles/`)

Phase 3A contains the **proven, well-behaved time features**. Validated in isolation before any experimental spectral features (Phase 3B) are introduced.

```text
PHASE 3A ROBUST TIMING
├── Session Cycle (DST-aware via zoneinfo + Empirical Expectancy + Significance Gate)
├── Swing Duration Maturity (Knowable Age + Timeframe Whitelist + Fail-Closed Effective N)
├── Macro Event Gate (Revision-Safe + No Leakage + Missing-Feed Fail-Safe)
└── Calendar Seasonality (Empirical Effect Gate + Exact Month-End Math)
            ↓
BASELINE BACKTEST HURDLE (Empirical Benchmark Recorder)
```

### 2. Subsystem Details & Statistical Invariants

#### A. Trading Session Cycle (`engine/cycles/session.py`)
- Standardized trading session windows evaluated via Python standard library `zoneinfo`:
  - `ASIA`: Tokyo / Shanghai active session.
  - `LONDON_PREOPEN`: Pre-market European positioning.
  - `LONDON`: Core London morning liquidity.
  - `LONDON_NY_OVERLAP`: Maximum global liquidity window.
  - `NEW_YORK`: Afternoon US session.
  - `US_LATE`: Low-liquidity closing hours.
- **R11 & A02:** Never hard-code UTC offsets. Explicit timezone conversions handle daylight saving transitions without look-ahead error.
- **P3A-06 & P3A-14 Empirical Expectancy & Significance Gate:** Zero hardcoding of expectancy scores. If no empirical historical table, or bucket $n_{eff} < 30$, or `is_statistically_significant == False`, `expectancy_score = 0.0` (`INSUFFICIENT_DATA`). Positive scores strictly require $n_{eff} \ge 30$ AND verified statistical significance.

#### B. Swing Duration Maturity (`engine/cycles/swing_duration.py`)
- **P3A-07 Knowable Age Causality:** Distinguishes `market_age` (from formation `timestamp`) and `known_age` (from `detected_at` confirmation). Scoring and maturity readiness strictly evaluate `known_age`.
- **P3A-08 & Timeframe Whitelist:** Strict whitelist validation (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`). All other inputs raise `ValueError`.
- **P3A-09, P3A-15, P3A-18 Fail-Closed Effective Sample Guard:** Zero hardcoded fallback arrays. Unknown statistical independence (`effective_n=None` and `sample_eval=None`) strictly defaults to `effective_n = 0.0`, `sample_is_blocked = True`, and `maturity_score = 0.0` (INSUFFICIENT). Raw N is NEVER assumed equal to effective N.

#### C. Macro Event Gate — Revision Point-in-Time Safe (`engine/cycles/events.py` & A06, A26)
- **A06 Blackout Policy:** Scheduled high-impact event within configured blackout window (e.g. $\pm 30$ minutes) $\rightarrow$ `is_in_blackout = True` $\rightarrow$ `is_blocked_by_event = True` $\rightarrow$ forces `WAIT`.
- **A26 Revision PiT Rule:** Releases at $t_{\text{released}} \le T$ return `initial_value`. Revisions at $t_{\text{revised}} > T$ are strictly masked.
- **P3A-11 Revision Timestamp Leakage Prevention:** Future unscheduled revisions at $t_{\text{revised}} > T$ cannot trigger a pre-revision blackout before $t_{\text{revised}}$ occurs.
- **P3A-12 Missing Feed Fail-Safe:** Missing or unverified macro calendar feed yields zero clear-market bonus (`macro_clear_bonus = 0.0`).

#### D. Calendar Seasonality (`engine/cycles/calendar.py`)
- Analyzes DOW, Hour UTC, Month, and exact Month-End flows (calculated via `calendar.monthrange`).
- **P3A-10 & P3A-16 Empirical Effect Gate:** Stable folds alone without empirical directional effect/expectancy yield `seasonality_score = 0.0`. Positive scores require $n_{eff} \ge 30$, `is_statistically_significant == True`, and `stability >= 0.60`.

#### E. Closed Candle Analysis Gate (`engine/cycles/engine.py`)
- **P3A-17 Gate:** `RobustTimeCycleEngine.analyze()` requires a completed candle (`is_closed=True`). An unclosed candle immediately raises `IncompleteCandleError`.

### 3. Initial 3A Scoring Weights (Sample-Guarded)

| Component | Max Weight | Guardrail |
|---|---|---|
| **Session Expectancy** | 15.0 | Requires $n_{eff} \ge 30$ AND `is_statistically_significant == True` |
| **Swing Duration Maturity** | 20.0 | Requires certified $n_{eff} \ge 30$ effective sample and known age $P75-P90$ |
| **Calendar Seasonality** | 5.0 | Requires $n_{eff} \ge 30$, statistical significance, and stability $\ge 0.60$ |
| **Macro Event Timing** | 5.0 | Requires verified healthy feed and $> 120$m clear window |

### 4. Phase 3A Acceptance & Targeted Test Suite

| Test ID | Test Name | Assertion Criteria | Status |
|---|---|---|:---:|
| **A02** | DST Session Label Integrity | Session labeling remains accurate across London & NY daylight saving clock shifts. | ✅ PASS |
| **A06** | Macro Event Blackout Gate | High-impact economic release within blackout window forces signal state to `WAIT`. | ✅ PASS |
| **A26** | Macro Event Revision PiT Safety | Future revisions to economic figures do not modify historical event features at $t < t_{\text{rev}}$. | ✅ PASS |
| **P3A-01** | Session Progress & Liquidity | Intra-session progress [0..100%] and high-liquidity flags correctly computed. | ✅ PASS |
| **P3A-06** | Session Sample Guard | No historical session statistics $\to$ `expectancy_score = 0.0`, `INSUFFICIENT_DATA`. | ✅ PASS |
| **P3A-07** | Swing Knowable Age | Scoring age starts strictly from `detected_at` confirmation, not hidden formation time. | ✅ PASS |
| **P3A-08** | Timeframe-Safe Swing Duration | Whitelist validation (`1m`..`1w`). Invalid timeframe raises `ValueError`. | ✅ PASS |
| **P3A-09** | Swing Historical Sample Guard | No historical duration sample $\to$ `maturity_score = 0.0`, `percentile = None`. | ✅ PASS |
| **P3A-10** | Calendar No-Evidence Gate | No historical folds $\to$ `stability = 0.0`, `score = 0.0`, accurate month length. | ✅ PASS |
| **P3A-11** | Revision Timestamp Safety | Future unknown revision cannot create historical blackout before revision is known. | ✅ PASS |
| **P3A-12** | Missing Macro Feed Safety | No macro calendar data $\to$ zero clear-market bonus. | ✅ PASS |
| **P3A-13** | Versioned Cycle Snapshot | New `cycle_version` does not overwrite existing historical snapshots. | ✅ PASS |
| **P3A-14** | Session Significance Gate | High $n_{eff}$ + positive expectancy but statistically insignificant $\to$ `expectancy_score = 0.0`. | ✅ PASS |
| **P3A-15** | Swing Effective N Guard | Raw $N=100$, but $n_{eff}=18$ discounted $\to$ `maturity_score = 0.0`, `INSUFFICIENT`. | ✅ PASS |
| **P3A-16** | Calendar Empirical Effect Gate | Stable folds alone without directional expectancy table $\to$ `seasonality_score = 0.0`. | ✅ PASS |
| **P3A-17** | Closed Candle Engine Gate | Unclosed candle (`is_closed=False`) rejected with `IncompleteCandleError`. | ✅ PASS |
| **P3A-18** | Fail-Closed Effective-N | Unknown statistical independence (`effective_n=None`) forces `maturity_score = 0.0`. | ✅ PASS |

### 5. Definition of Done Checklist

- [x] `SessionCycleEngine` strictly statistical & significance-gated (`P3A-06`, `P3A-14`).
- [x] `SwingDurationEngine` computes knowable age from `detected_at` (`P3A-07`), timeframe whitelist (`P3A-08`), and fail-closed effective-N (`P3A-18`).
- [x] Calendar seasonality requires empirical directional effect and stability (`P3A-10`, `P3A-16`).
- [x] Engine strictly rejects unclosed candles (`P3A-17`).
- [x] Macro event revision timestamp leakage eliminated (`P3A-11`) and missing feed fail-safe enforced (`P3A-12`).
- [x] Snapshot version immutability (`unique_together` with `cycle_version`) verified (`P3A-13`).
- [x] Engine AST purity verified (zero Django imports in `engine/cycles/`).
- [x] Full regression suite passing **92/92 tests** in Docker.
