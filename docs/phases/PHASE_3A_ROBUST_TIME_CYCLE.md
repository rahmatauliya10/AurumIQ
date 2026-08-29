# Phase 3A: Robust Time Cycle Features

> **Status:** ✅ **COMPLETED, RIGOROUSLY VERIFIED & HARDENED**  
> **Primary Goal:** Implement deterministic, point-in-time robust timing features (Session Cycle, Swing Duration, Macro Event Point-in-Time Gate, and Calendar Seasonality) with strict statistical sample guards and zero future lookahead.

---

## 1. Subsystem Architecture (`engine/cycles/`)

Phase 3A contains the **proven, well-behaved time features**. Validated in isolation before any experimental spectral features (Phase 3B) are introduced.

```text
PHASE 3A ROBUST TIMING
├── Session Cycle (DST-aware via zoneinfo + Empirical Expectancy Guard)
├── Swing Duration Maturity (Knowable Age + Timeframe-Safe + Sample Guard)
├── Macro Event Gate (Revision-Safe + No Leakage + Missing-Feed Fail-Safe)
└── Calendar Seasonality (No-Evidence Gate + Exact Month-End Math)
            ↓
BASELINE BACKTEST HURDLE (Empirical Benchmark Recorder)
```

---

## 2. Subsystem Details & Statistical Invariants

### A. Trading Session Cycle (`engine/cycles/session.py`)
- Standardized trading session windows evaluated via Python standard library `zoneinfo`:
  - `ASIA`: Tokyo / Shanghai active session.
  - `LONDON_PREOPEN`: Pre-market European positioning.
  - `LONDON`: Core London morning liquidity.
  - `LONDON_NY_OVERLAP`: Maximum global liquidity window.
  - `NEW_YORK`: Afternoon US session.
  - `US_LATE`: Low-liquidity closing hours.
- **R11 & A02:** Never hard-code UTC offsets. Explicit timezone conversions handle daylight saving transitions without look-ahead error.
- **P3A-06 Empirical Expectancy Guard:** Zero hardcoding of expectancy scores. If no empirical historical table or bucket $n_{eff} < 30$, `expectancy_score = 0.0` (`INSUFFICIENT_DATA`). Positive scores require $n_{eff} \ge 30$.

### B. Swing Duration Maturity (`engine/cycles/swing_duration.py`)
- **P3A-07 Knowable Age Causality:** Distinguishes `market_age` (from formation `timestamp`) and `known_age` (from `detected_at` confirmation). Scoring and maturity readiness strictly evaluate `known_age`.
- **P3A-08 Timeframe Safety:** Bar calculation adapts dynamically to timeframe string (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) without static second approximations.
- **P3A-09 Historical Sample Guard:** Zero hardcoded fallback arrays. If historical sample $N < 30$, `pullback_age_percentile = None`, `is_mature = False`, and `maturity_score = 0.0`.

### C. Macro Event Gate — Revision Point-in-Time Safe (`engine/cycles/events.py` & A06, A26)
- **A06 Blackout Policy:** Scheduled high-impact event within configured blackout window (e.g. $\pm 30$ minutes) $\rightarrow$ `is_in_blackout = True` $\rightarrow$ `is_blocked_by_event = True` $\rightarrow$ forces `WAIT`.
- **A26 Revision PiT Rule:** Releases at $t_{\text{released}} \le T$ return `initial_value`. Revisions at $t_{\text{revised}} > T$ are strictly masked.
- **P3A-11 Revision Timestamp Leakage Prevention:** Future unscheduled revisions at $t_{\text{revised}} > T$ cannot trigger a pre-revision blackout before $t_{\text{revised}}$ occurs.
- **P3A-12 Missing Feed Fail-Safe:** Missing or unverified macro calendar feed yields zero clear-market bonus (`macro_clear_bonus = 0.0`).

### D. Calendar Seasonality (`engine/cycles/calendar.py`)
- Analyzes DOW, Hour UTC, Month, and exact Month-End flows (calculated via `calendar.monthrange`).
- **P3A-10 No-Evidence Gate:** If historical fold stabilities are missing or empty, `stability_score = 0.0` and `seasonality_score = 0.0`. If stability $< 0.60$, score strictly defaults to `0.0`.

---

## 3. Initial 3A Scoring Weights (Sample-Guarded)

| Component | Max Weight | Guardrail |
|---|---|---|
| **Session Expectancy** | 15.0 | Requires $n_{eff} \ge 30$ via Tiered Sample Guard |
| **Swing Duration Maturity** | 20.0 | Requires $N \ge 30$ historical sample and known age $P75-P90$ |
| **Calendar Seasonality** | 5.0 | Requires stability $\ge 0.60$ across rolling folds |
| **Macro Event Timing** | 5.0 | Requires verified healthy feed and $> 120$m clear window |

---

## 4. Phase 3A Acceptance & Targeted Test Suite

| Test ID | Test Name | Assertion Criteria | Status |
|---|---|---|:---:|
| **A02** | DST Session Label Integrity | Session labeling remains accurate across London & NY daylight saving clock shifts. | ✅ PASS |
| **A06** | Macro Event Blackout Gate | High-impact economic release within blackout window forces signal state to `WAIT`. | ✅ PASS |
| **A26** | Macro Event Revision PiT Safety | Future revisions to economic figures do not modify historical event features at $t < t_{\text{rev}}$. | ✅ PASS |
| **P3A-01** | Session Progress & Liquidity | Intra-session progress [0..100%] and high-liquidity flags correctly computed. | ✅ PASS |
| **P3A-06** | Session Sample Guard | No historical session statistics $\to$ `expectancy_score = 0.0`, `INSUFFICIENT_DATA`. | ✅ PASS |
| **P3A-07** | Swing Knowable Age | Scoring age starts strictly from `detected_at` confirmation, not hidden formation time. | ✅ PASS |
| **P3A-08** | Timeframe-Safe Swing Duration | Dynamic timeframe parsing; 1H candle $\ne$ 4 bars of 15m. | ✅ PASS |
| **P3A-09** | Swing Historical Sample Guard | No historical duration sample $\to$ `maturity_score = 0.0`, `percentile = None`. | ✅ PASS |
| **P3A-10** | Calendar No-Evidence Gate | No historical folds $\to$ `stability = 0.0`, `score = 0.0`, accurate month length. | ✅ PASS |
| **P3A-11** | Revision Timestamp Safety | Future unknown revision cannot create historical blackout before revision is known. | ✅ PASS |
| **P3A-12** | Missing Macro Feed Safety | No macro calendar data $\to$ zero clear-market bonus. | ✅ PASS |
| **P3A-13** | Versioned Cycle Snapshot | New `cycle_version` does not overwrite existing historical snapshots. | ✅ PASS |

---

## 5. Definition of Done Checklist

- [x] `SessionCycleEngine` strictly statistical: zero hardcoded expectancy without empirical sample (`P3A-06`).
- [x] `SwingDurationEngine` computes knowable age from `detected_at` (`P3A-07`) and timeframe-safe bar duration (`P3A-08`).
- [x] Zero-evidence gates enforced for swing durations (`P3A-09`) and calendar seasonality (`P3A-10`).
- [x] Macro event revision timestamp leakage eliminated (`P3A-11`) and missing feed fail-safe enforced (`P3A-12`).
- [x] Snapshot version immutability (`unique_together` with `cycle_version`) verified (`P3A-13`).
- [x] Engine AST purity verified (zero Django imports in `engine/cycles/`).
- [x] Full regression suite passing **88/88 tests** in Docker.
