# Phase 3A: Robust Time Cycle Features

> **Status:** ✅ **COMPLETED & VERIFIED**  
> **Primary Goal:** Implement deterministic, point-in-time robust timing features (Session Cycle, Swing Duration, Macro Event Point-in-Time Gate, and Calendar Seasonality) and establish the baseline backtest benchmark.

---

## 1. Subsystem Architecture (`engine/cycles/`)

Phase 3A contains the **proven, well-behaved time features**. Validated in isolation before any experimental spectral features (Phase 3B) are introduced.

```text
PHASE 3A ROBUST TIMING
├── Session Cycle (DST-aware via zoneinfo)
├── Swing Duration Maturity (Historical Percentiles)
├── Macro Event Gate (Point-in-Time Revision-Safe)
└── Calendar Seasonality (Rolling Stability Filter)
            ↓
BASELINE BACKTEST RECORDED (Benchmark PF / Expectancy)
```

---

## 2. Subsystem Details

### A. Trading Session Cycle (`engine/cycles/session.py`)
- Standardized trading session windows evaluated via Python standard library `zoneinfo`:
  - `ASIA`: Tokyo / Shanghai active session.
  - `LONDON_PREOPEN`: Pre-market European positioning.
  - `LONDON`: Core London morning liquidity.
  - `LONDON_NY_OVERLAP`: Maximum global liquidity window.
  - `NEW_YORK`: Afternoon US session.
  - `US_LATE`: Low-liquidity closing hours.
- **R11 & A02:** Never hard-code UTC offsets. Explicit timezone conversions handle daylight saving transitions without look-ahead error.
- Computes historical expectancy per `(Session, Regime)` bucket, guarded by `EffectiveSampleEstimator`.

### B. Swing Duration Maturity (`engine/cycles/swing_duration.py`)
- Tracks historical duration (bars and hours) of impulse and corrective waves.
- Evaluates current active pullback age against historical distribution:
  - $P10, P25, P50, P75, P90$.
- **Guardrail:** A mature correction (e.g. $P75 - P90$) increases timing readiness **only if** market structure and momentum turn confirm support. Age alone never triggers a buy.

### C. Macro Event Gate — Revision Point-in-Time Safe (`engine/cycles/events.py` & A26)
- Evaluates time to / from high-impact macroeconomic releases (CPI, NFP, FOMC).
- **Point-in-Time Anti-Look-Ahead Rule (A26):**
  - Historical analysis at timestamp $t$ only accesses macroeconomic numbers published at $t_{\text{released}} \le t$.
  - Subsequent data revisions published at $t_{\text{revised}} > t$ are strictly masked.
- **Blackout Policy:** If high-impact event is within configured blackout window (e.g. $\pm 30$ minutes) $\rightarrow$ **BUY_WINDOW prohibited $\rightarrow$ forced WAIT**.

### D. Calendar Seasonality (`engine/cycles/calendar.py`)
- Analyzes hour-of-day, day-of-week, and end-of-month historical flows.
- Integrates a rolling stability coefficient: If seasonal bias is unstable across rolling 3-month folds, its score contribution defaults to **0.0**.

---

## 3. Initial 3A Scoring Weights

| Component | Initial Weight | Guardrail |
|---|---|---|
| **Session Expectancy** | 15 | Requires $N \ge 30$ via Sample Guard |
| **Swing Duration Maturity** | 20 | Must align with structure support |
| **Calendar Seasonality** | 5 | Rolling stability check required |
| **Macro Event Timing** | 5 | Acts as hard risk gate on blackout |

---

## 4. Phase 3A Acceptance & Unit Test Suite

| Test ID | Test Name | Assertion Criteria | Status |
|---|---|---|:---:|
| **A02** | DST Session Label Integrity | Session labeling remains accurate across London & NY daylight saving clock shifts. | ✅ PASS |
| **A06** | Macro Event Blackout Gate | High-impact economic release within blackout window forces signal state to `WAIT`. | ✅ PASS |
| **A26** | Macro Event Revision PiT Safety | Future revisions to economic figures do not modify historical event features at $t < t_{\text{rev}}$. | ✅ PASS |
| **P3A-01** | Session Progress & Liquidity | Intra-session progress [0..100%] and high-liquidity flags correctly computed. | ✅ PASS |
| **P3A-02** | Swing Duration Maturity | Causal pullback age percentiles (P10..P90) and `is_mature` flag verified. | ✅ PASS |
| **P3A-03** | Calendar Stability Filter | Unstable historical folds (< 0.60) strictly collapse seasonality score to 0.0. | ✅ PASS |
| **P3A-04** | Robust Cycle Engine End-to-End | Consolidated `RobustTimeCycleEngine` produces immutable `Cycle3ASnapshot`. | ✅ PASS |
| **P3A-05** | Baseline Benchmark Recorder | Baseline metrics hurdle (`PF`, `Expectancy_R`, `MaxDD`, `Trades`) recorded. | ✅ PASS |

---

## 5. Definition of Done Checklist

- [x] `SessionCycleEngine` correctly classifies trading sessions across London & NY DST transitions (`A02`).
- [x] `SwingDurationEngine` computes causal pullback duration percentiles from confirmed swing timestamps.
- [x] `MacroEventGate` enforces $\pm 30$m blackout (`A06`) and point-in-time revision masking (`A26`).
- [x] `CalendarSeasonalityEngine` implements rolling stability filter (unstable folds drop score to 0.0).
- [x] `RobustTimeCycleEngine` consolidates snapshot and enforces event blackout blocking.
- [x] Engine AST purity verified (zero Django imports in `engine/cycles/`).
- [x] Django ORM persistence bridge (`CycleSnapshotRecord`) created and verified.
- [x] All **84/84 tests passing** in Docker test suite.
