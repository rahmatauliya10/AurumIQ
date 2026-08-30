# Phase 4: Direction & Timing Scoring with State Machine Architecture

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN` (Long Direction Baseline)  
> **Current XAUUSD Target Status:** 🔴 `DUAL-SIDE REDESIGN REQUIRED (NOT IMPLEMENTED)`  
> **Primary Goal:** Specify independent Direction and Timing scoring engines, side-aware state machine transitions (`BUY / WAIT / SELL`), and canonical SHA-256 fingerprinting for institutional auditability.

---

## 1. Two-Score Separation Principle

AurumIQ evaluates trend conviction independently from entry trigger timing:
1. **Direction Score (0–100):** Measures higher-timeframe trend alignment, market regime, and structural bias.
2. **Timing Score (0–100):** Measures lower-timeframe pullback completion, zone proximity, and candle reversal triggers.

---

## 2. Scoring Architecture: Historical Baseline vs Target XAUUSD

### A. Legacy XAUT Baseline Scoring (Historical Reference Only)

#### Historical Direction Weights (LEGACY XAUT BASELINE ONLY)
| Component | Points | Description |
|---|:---:|---|
| Market Regime Quality | 15 | Regime alignment and confidence |
| 1D + 4H Trend Alignment | 20 | Multi-timeframe EMA slope alignment |
| Confirmed Structure / BOS | 20 | Break of structure and swing bias |
| Pullback Quality | 10 | Normalized distance to EMA cluster |
| Momentum State | 10 | RSI position and MACD histogram velocity |
| Volume Confirmation | 5 | Volume surge on structural break |
| XAU Alignment & XAUT Basis Quality | 20 | Basis deviation and primary reference alignment |
| **TOTAL** | **100** | |

#### Historical Timing Weights (LEGACY XAUT BASELINE ONLY)
| Component | Points | Description |
|---|:---:|---|
| Entry Zone Proximity / ATR | 25 | Distance to active support zone |
| Closed 15m Reversal | 20 | Reversal candle pattern confirmation |
| 15m / 1H Momentum Turn | 15 | Short-term oscillator hook |
| Phase 3A Robust Timing | 25 | Session expectancy and swing maturity |
| Volume Response | 10 | Volume expansion at trigger |
| Macro Event Safety | 5 | Distance from high-impact macro window |
| **TOTAL** | **100** | |

### B. Target XAUUSD Dual-Direction Specification (Pending Redesign)
For the target XAUUSD instrument, scoring is generalized into side-aware evaluators:
$$\text{LongDirectionScore} = \sum w_i \cdot \text{LongEvidence}_i$$
$$\text{ShortDirectionScore} = \sum w_i \cdot \text{ShortEvidence}_i$$

> [!IMPORTANT]
> **XAUUSD WEIGHTS & THRESHOLDS NOT FROZEN:**  
> All feature weights and score thresholds for XAUUSD are **NOT FROZEN / REVALIDATION REQUIRED**. Exact numerical parameters will be calibrated empirically during Phase 6 walk-forward backtesting. Macro components (DXY, 10Y Yields, Gold Futures) are candidate components only and carry no frozen weights at this stage.

---

## 3. Target State Machine Architecture (Conceptual Specification)

```text
TARGET DUAL-SIDE STATE MACHINE SPECIFICATION (CONCEPTUAL)

                   ┌────────────────────────┐
                   │        NO_TRADE        │
                   └───────────┬────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
       [BULLISH BIAS]                  [BEARISH BIAS]
┌─────────────────────────────┐ ┌─────────────────────────────┐
│         WATCH_LONG          │ │         WATCH_SHORT         │
└──────────────┬──────────────┘ └──────────────┬──────────────┘
               ▼                               ▼
┌─────────────────────────────┐ ┌─────────────────────────────┐
│         READY_LONG          │ │         READY_SHORT         │
└──────────────┬──────────────┘ └──────────────┬──────────────┘
               ▼                               ▼
┌─────────────────────────────┐ ┌─────────────────────────────┐
│         BUY_WINDOW          │ │         SELL_WINDOW         │
└──────────────┬──────────────┘ └──────────────┬──────────────┘
               │                               │
               ▼                               ▼
      User Action: BUY                User Action: SELL

All other states (NO_TRADE, WATCH, READY, FORCE_WAIT) ──► User Action: WAIT
```

### User Decision Mapping Contract
- $\text{BUY\_WINDOW} \implies \mathbf{BUY}$ (Eligible for Long Risk Planning)
- $\text{SELL\_WINDOW} \implies \mathbf{SELL}$ (Eligible for Short Risk Planning)
- $\text{All Other States} \implies \mathbf{WAIT}$ (Action prohibited / suppressed)

---

## 4. Canonical Deterministic Fingerprinting (R5)

Every analysis output must generate a canonical SHA-256 fingerprint from sorted, deterministic JSON inputs:
$$\text{analysis\_fingerprint} = \text{SHA256}(\text{canonical\_json}(\text{feature\_vector}, \text{parameters}, \text{git\_sha}))$$
This ensures 100% bitwise reproducibility between backtesting, forward observation, and live analysis.

---

## 5. Definition of Done Checklist

### Historical Baseline
- [x] Independent Direction and Timing score separation implemented for Long baseline.
- [x] Canonical SHA-256 fingerprinting implemented and verified (`P4-01` through `P4-22`).
- [x] Closed-candle analysis idempotency verified (`A03`).

### Target XAUUSD Scope (Pending Phase 4 Code Implementation)
- [ ] Implement `ShortDirectionScore` and `ShortTimingScore` evaluators (`XAU-P4-01`, `XAU-P4-02`).
- [ ] Implement dual-side state machine with conflict resolution to `WAIT` (`XAU-P4-03`).
- [ ] Verify macro blackout blocks both BUY and SELL states (`XAU-P4-04`).
- [ ] Empirically calibrate and freeze Direction/Timing thresholds via Phase 6 backtesting.
