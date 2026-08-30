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
In the historical XAUT single-direction implementation, the Long Direction score used the following fixed weights:
- *Regime Alignment:* 20 points
- *Trend Slope:* 20 points
- *Structure BOS:* 20 points
- *Pullback Quality:* 15 points
- *Momentum Velocity:* 15 points
- *XAUT Basis / XAU Alignment:* 10 points (Deprecated for future XAUUSD production)

### B. Target XAUUSD Dual-Direction Specification (Pending Redesign)
For the target XAUUSD instrument, scoring is generalized into side-aware evaluators:
$$\text{LongDirectionScore} = \sum w_i \cdot \text{LongEvidence}_i$$
$$\text{ShortDirectionScore} = \sum w_i \cdot \text{ShortEvidence}_i$$

> [!IMPORTANT]
> **WEIGHTS & THRESHOLDS NOT FROZEN:**  
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
- [x] Canonical SHA-256 fingerprinting implemented and verified (`P4-01` to `P4-24`).
- [x] Closed-candle analysis idempotency verified (`A03`).

### Target XAUUSD Scope (Pending Phase 4 Code Implementation)
- [ ] Implement `ShortDirectionScore` and `ShortTimingScore` evaluators.
- [ ] Implement dual-side state machine with `BUY_WINDOW` and `SELL_WINDOW`.
- [ ] Empirically calibrate and freeze Direction/Timing thresholds via Phase 6 backtesting.
