# Outcome Accounting Remediation Implementation Plan

> **Goal:** Fix the post-fill risk plan barrier validation bug in XAUUSD backtest outcome engine where NEXT_BAR_OPEN entries that blow past TP1 or SL are retroactively tagged as TP1_FIRST / SL_FIRST with manufactured negative R.
> **Architecture:** Fail-closed post-fill geometric validation in `XauUsdOutcomeEngine.resolve_trade()` returning `ENTRY_INVALIDATED_STALE_RISK_PLAN`, plus defense-in-depth validation in `SideAwareIntrabarResolver`.
> **Tech Stack:** Python 3.12, Django, pytest, Decimal math.

## Global Constraints
- DO NOT modify signal weights, gate thresholds, risk candidate parameters, TP/SL domains, cooldowns, qualification hurdles, or candidate generation policies.
- DO NOT access historical OOS (`AURUMIQ_ENABLE_OOS=0`, `OOS_ACCESS_COUNT=0`).
- PRODUCTION_AUTHORITY = OFF, PAPER_ONLY = TRUE, REAL_ORDER_EXECUTION = OFF.
- STOP after targeted validation on the 5 representative candidates (017, 089, 012, 049, 084).

---

### Task 1: Extend Outcome Enums with `ENTRY_INVALIDATED_STALE_RISK_PLAN`
**Files:**
- Modify: `engine/backtest/xauusd_types.py`
- Modify: `engine/backtest/types.py`

**Interfaces:**
- Produces: `XauUsdTradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN = "ENTRY_INVALIDATED_STALE_RISK_PLAN"`
- Produces: `TradeOutcome.ENTRY_INVALIDATED_STALE_RISK_PLAN = "ENTRY_INVALIDATED_STALE_RISK_PLAN"`

---

### Task 2: Implement Post-Fill Risk Plan Validation in `XauUsdOutcomeEngine`
**Files:**
- Modify: `engine/backtest/xauusd_outcomes.py`
- Modify: `engine/risk/xauusd_intrabar.py`

**Validation Logic:**
- LONG requires: `stop_final < fill_price < tp1`
  - If `fill_price >= tp1` OR `fill_price <= stop_final`: return `ENTRY_INVALIDATED_STALE_RISK_PLAN`
- SHORT requires: `tp1 < fill_price < stop_final`
  - If `fill_price <= tp1` OR `fill_price >= stop_final`: return `ENTRY_INVALIDATED_STALE_RISK_PLAN`
- Defense-in-depth in `SideAwareIntrabarResolver.resolve()`:
  - Accept `fill_price: Optional[Decimal] = None`.
  - Validate geometric barrier ordering relative to fill_price before checking candle touches.

---

### Task 3: Update Metrics and Composite Trade Filtering
**Files:**
- Modify: `engine/backtest/xauusd_metrics.py`
- Modify: `engine/backtest/xauusd_composite_policy.py`

**Logic:**
- `filled_trades` strictly excludes `NO_FILL`, `ENTRY_INVALIDATED_STALE_RISK_PLAN`, and `SKIPPED`.
- `invalidated_entry_count` tracked in metrics.
- `evaluate_side_trades` strictly filters out non-filled / invalidated trades.

---

### Task 4: Comprehensive TDD Unit & Regression Test Suite
**Files:**
- Create: `tests/unit/test_xauusd_outcome_semantics_remediation.py`

**Test Cases:**
- Case A (LONG valid fill < TP1 and > SL, then TP touched -> TP1_FIRST, gross_r >= 0)
- Case B (LONG fill >= TP1 -> ENTRY_INVALIDATED_STALE_RISK_PLAN, never TP1_FIRST)
- Case C (LONG fill <= SL -> ENTRY_INVALIDATED_STALE_RISK_PLAN, never SL_FIRST)
- Case D (SHORT valid fill > TP1 and < SL, then TP touched -> TP1_FIRST, gross_r >= 0)
- Case E (SHORT fill <= TP1 -> ENTRY_INVALIDATED_STALE_RISK_PLAN, never TP1_FIRST)
- Case F (SHORT fill >= SL -> ENTRY_INVALIDATED_STALE_RISK_PLAN, never SL_FIRST)
- Invariant: Every resolved TP1_FIRST satisfies gross_r >= 0 and favorable exit price.
- Invariant: Every resolved SL_FIRST satisfies gross_r <= 0 and adverse exit price.

---

### Task 5: Targeted Validation across 5 Representative Candidates
**Script:**
- Create: `scratch/verify_outcome_remediation.py`
- Rerun 017 LONG, 089 LONG, 012 LONG, 049 SHORT, 084 SHORT using cached market snapshots.
- Verify required acceptance:
  - `STALE_TP_NEGATIVE_GROSS_COUNT = 0`
  - `STALE_SL_POSITIVE_GROSS_COUNT = 0`
  - `OUTCOME_ACCOUNTING = PASS`
  - `REALIZED_R_PARITY = PASS`
  - `OOS_ACCESS_COUNT = 0`
- Produce before/after comparative table.
- STOP.
