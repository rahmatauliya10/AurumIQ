# Phase 3A: Robust Time Cycle Features

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Implement deterministic, point-in-time robust timing features (Session Cycle, Swing Duration, Macro Event Point-in-Time Gate, and Calendar Seasonality) and establish the baseline backtest benchmark.

---

## 1. Subsystem Architecture (`engine/cycles/`)

Phase 3A contains the **proven, well-behaved time features**. They must be validated in isolation before any experimental spectral features (Phase 3B) are introduced.

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

### A. Trading Session Cycle (`cycles/session.py`)
- Standardized trading session windows evaluated via Python standard library `zoneinfo`:
  - `ASIA`: Tokyo / Shanghai active session.
  - `LONDON_PREOPEN`: Pre-market European positioning.
  - `LONDON`: Core London morning liquidity.
  - `LONDON_NY_OVERLAP`: Maximum global liquidity window.
  - `NEW_YORK`: Afternoon US session.
  - `US_LATE`: Low-liquidity closing hours.
- **R11 & A02:** Never hard-code UTC offsets. Explicit timezone conversions handle daylight saving transitions without look-ahead error.
- Computes historical expectancy per `(Session, Regime)` bucket, guarded by `EffectiveSampleEstimator`.

### B. Swing Duration Maturity (`cycles/swing_duration.py`)
- Tracks historical duration (bars and hours) of impulse and corrective waves.
- Evaluates current active pullback age against historical distribution:
  - $P10, P25, P50, P75, P90$.
- **Guardrail:** A mature correction (e.g. $P75 - P90$) increases timing readiness **only if** market structure and momentum turn confirm support. Age alone never triggers a buy.

### C. Macro Event Gate — Revision Point-in-Time Safe (`cycles/events.py` & A26)
- Evaluates time to / from high-impact macroeconomic releases (CPI, NFP, FOMC).
- **Point-in-Time Anti-Look-Ahead Rule (A26):**
  - Historical analysis at timestamp $t$ only accesses macroeconomic numbers published at $t_{\text{released}} \le t$.
  - Subsequent data revisions published at $t_{\text{revised}} > t$ are strictly masked.
- **Blackout Policy:** If high-impact event is within configured blackout window (e.g. $\pm 30$ minutes) $\rightarrow$ **BUY_WINDOW prohibited $\rightarrow$ forced WAIT**.

### D. Calendar Seasonality (`cycles/calendar.py`)
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

## 4. Phase 3A Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A02** | DST Session Label Integrity | Session labeling remains accurate across London & NY daylight saving clock shifts. |
| **A06** | Macro Event Blackout Gate | High-impact economic release within blackout window forces signal state to `WAIT`. |
| **A26** | Macro Event Revision PiT Safety | Future revisions to economic figures do not modify historical event features at $t < t_{\text{rev}}$. |

---

## 5. Baseline Backtest Checkpoint

Before proceeding to Phase 3B, execute a baseline backtest combining **Phase 2 (Indicators/Structure) + Phase 3A (Robust Timing)**:
- Record: `Base_Profit_Factor`, `Base_Expectancy_R`, `Base_Max_Drawdown`, `Base_Trade_Count`.
- This benchmark forms the exact baseline hurdle that Phase 3B experimental features must beat.
