# Phase 5: Risk Engine, Intrabar Resolver & Entry Execution Model

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN` (Long Risk Architecture)  
> **Current XAUUSD Target Status:** 🔴 `LONG / SHORT RISK REDESIGN REQUIRED (NOT IMPLEMENTED)`  
> **Primary Goal:** Transform candidate signals into causal, point-in-time Risk Plans (side-aware entry zones, structure stops, ATR stop guard, targets, RR gate), an Intrabar Ambiguity Resolver with pre-validated grid replay, and causal fill simulation without placing real orders.

---

## 1. Risk Planning Engine (`engine/risk/planner.py`)

A candidate signal from Phase 4 must pass an objective, structure- and ATR-aware risk evaluation before it can be considered execution-eligible.

```text
PHASE 4 SIGNAL
(BUY_WINDOW / SELL_WINDOW) ─── Immutable Audit Trail Preserved
       │
       ▼
┌────────────────────────────────────────────────────────┐
│                   PHASE 5 RISK PLAN                    │
├────────────────────────────────────────────────────────┤
│ LONG SETUPS (BUY):                                     │
│ 1. Entry Zone: Derived from Support Zone               │
│ 2. Structure Stop: Below Support - Buffer              │
│ 3. ATR Stop: Entry - (k * ATR14)                       │
│ 4. Stop Final: min(Stop_Structure, Stop_ATR)           │
│ 5. Target: Nearest confirmed structural resistance     │
│ 6. RR Gate: Planned Reward / Planned Risk >= Min_RR    │
│                                                        │
│ SHORT SETUPS (SELL - Conceptual Target Spec):          │
│ 1. Entry Zone: Derived from Resistance Zone            │
│ 2. Structure Stop: Above Resistance + Buffer           │
│ 3. ATR Stop: Entry + (k * ATR14)                       │
│ 4. Stop Final: max(Stop_Structure, Stop_ATR)           │
│ 5. Target: Nearest confirmed structural support        │
│ 6. RR Gate: Planned Reward / Planned Risk >= Min_RR    │
└──────────────────────────┬─────────────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
      RR >= Min_RR                RR < Min_RR
   is_valid_risk_plan = True   is_valid_risk_plan = False
   execution_eligible = True   execution_eligible = False
   effective_action = BUY/SELL effective_action = WAIT
```

> **Threshold Notice:** The legacy minimum risk-to-reward ratio ($RR \ge 1.80$) is a **LEGACY BASELINE ONLY / REVALIDATION REQUIRED** parameter. Target XAUUSD thresholds will be validated during Phase 6 backtesting.

---

## 2. Invariant Contracts

### Invariant 1: Source Signal Eligibility Gate (P5-25)
Only valid candidate signals (`BUY_WINDOW` / `SELL_WINDOW`) are eligible for Risk Planning. All non-window states immediately return `is_valid_risk_plan = False` and `execution_eligible = False`.

### Invariant 2: Phase 4 Signal Immutability (A07)
If a Phase 4 candidate signal fails Phase 5 Risk Planning (e.g. insufficient $RR$):
* **Phase 4 `SignalRecord` remains unchanged** in its candidate state.
* **Phase 5 `RiskPlanSnapshot` records `is_valid_risk_plan = False`, `execution_eligible = False`, `effective_action = WAIT`**.

### Invariant 3: Causal Execution Timestamps (A19, A27)
Simulated order fills occur strictly at $t \ge t_{\text{signal}} + \text{latency}$. Execution on the close of the signal-generating candle is structurally forbidden.

---

## 3. Intrabar Ambiguity Resolver (`engine/risk/intrabar.py` — A14, A22, P5-26)

When within the same candle after fill:
$$\text{High} \ge \text{TP} \quad \text{AND} \quad \text{Low} \le \text{SL}$$

### Grid Pre-Validation Hierarchy
1. Pre-validate lower-timeframe candle sequence (15m $\rightarrow$ 5m $\rightarrow$ 1m) for duration, chronological order, containment, and grid continuity.
2. If valid: replay lower-timeframe bars in strict chronological sequence.
3. If lower-timeframe data is missing or malformed: fail safe to `CONSERVATIVE_SL_FIRST`.

---

## 4. Definition of Done Checklist

### Historical Baseline
- [x] Long risk planner implemented with structural and ATR stops (`A07`).
- [x] Immutable `RiskPlanSnapshot` provenance contract implemented (`P5-32B`).
- [x] Intrabar ambiguity resolver with grid pre-validation implemented (`A14`, `A22`, `P5-26`).
- [x] Causal execution models (`NEXT_BAR_OPEN`, `MARKET_AFTER_SIGNAL`, `LIMIT_ZONE`) verified.

### Target XAUUSD Scope (Pending Phase 5 Code Implementation)
- [ ] Implement side-aware Short risk planner with resistance-derived entry and stop geometry.
- [ ] Revalidate ATR multipliers and minimum RR threshold for XAUUSD spot dynamics.
- [ ] Verify 1m/5m intrabar replay accuracy against high-volatility spot gold spikes.
