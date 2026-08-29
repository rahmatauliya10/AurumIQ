# Phase 5: Risk Engine, Intrabar Resolver & Entry Execution Model

> **Status:** ✅ **COMPLETED & FROZEN**  
> **Primary Goal:** Transform frozen Phase 4 `BUY_WINDOW` signals into causal, point-in-time Risk Plans (support-derived entry zone, structure stops, ATR stop guard, TP1/TP2, RR $\ge 1.80$ gate), an Intrabar Ambiguity Resolver with pre-validated grid replay, and causal fill simulation without placing real exchange orders.

---

## 1. Risk Planning Engine (`engine/risk/planner.py`)

A `BUY_WINDOW` signal from Phase 4 must pass an objective, structure- and ATR-aware risk evaluation before it can be considered executable.

```text
PHASE 4 SIGNAL
(BUY_WINDOW / BUY) ─── Immutable Audit Trail Preserved
       │
       ▼
┌────────────────────────────────────────────────────────┐
│                   PHASE 5 RISK PLAN                    │
├────────────────────────────────────────────────────────┤
│ 1. Entry Zone (P5-32):                                 │
│    entry_min = primary_support.price_low               │
│    entry_max = primary_support.price_high              │
│    entry_mid = (entry_min + entry_max) / 2             │
│    (latest_close is market context only)               │
│ 2. Structure Stop: support_zone_low - structure_buffer │
│ 3. ATR Stop: entry_mid - (atr_mult * ATR14)            │
│ 4. Stop Final: min(stop_structure, stop_atr)           │
│ 5. Stop Distance Guard: (entry_max - SL) / ATR <= max  │
│ 6. TP1: Nearest meaningful confirmed resistance        │
│ 7. RR Gate: (TP1 - entry_max) / (entry_max - SL) >= 1.8│
└──────────────────────────┬─────────────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
      RR >= 1.80                     RR < 1.80
   is_valid_risk_plan = True      is_valid_risk_plan = False
   execution_eligible = True      execution_eligible = False
   effective_action = BUY         effective_action = WAIT
```

### Invariant 1: Source Signal Eligibility Gate (P5-25)
Only `SignalState.BUY_WINDOW` signals with `UserDecision.BUY` are eligible for Risk Planning. All other states (`READY`, `WATCH`, `AVOID`, `FORCE_WAIT`, `NO_TRADE`) immediately return `is_valid_risk_plan = False` and `execution_eligible = False`.

### Invariant 2: Phase 4 Signal Immutability (A07)
If a Phase 4 `BUY_WINDOW` signal fails Phase 5 Risk Planning (e.g. $RR = 1.55 < 1.80$):
* **Phase 4 `SignalRecord` remains `BUY_WINDOW` (`BUY`)** and is never modified or overwritten.
* **Phase 5 `RiskPlanSnapshot` records `is_valid_risk_plan = False`, `execution_eligible = False`, `effective_action = WAIT`**.

### Invariant 3: RiskPlanSnapshot Immutable Provenance Contract (P5-32B)
`RiskPlanSnapshot` explicitly preserves all reproducible point-in-time coordinates and provenance versions:
* `source_signal_fingerprint`, `signal_generated_at`
* `risk_version`, `execution_model_version`, `config_version`, `code_revision`
* `entry_min`, `entry_mid`, `entry_max` (derived strictly from the active support zone)
* `source_zone_id`, `source_zone_timestamp`
* `stop_structure`, `stop_atr`, `stop_final`, `stop_distance_atr`
* `tp1`, `tp2`, `rr_tp1`, `rr_tp2`
* `is_valid_risk_plan`, `execution_eligible`, `effective_action`, `reasons`
* Backward-compatible property aliases: `source_zone`, `source_zone_identity`, `entry_price_ideal`, `entry_limit_max`, `stop_loss_price`, `risk_reward_ratio`.

---

## 2. Stop Loss & Target Engine (`engine/risk/stops.py`, `targets.py`)

### A. Structure Invalidation & ATR Stop Guard (`stops.py` — P5-28, P5-31)
$$\text{Stop}_{\text{structure}} = \text{Support\_Zone\_Low} - \text{Structure\_Buffer}$$
$$\text{Stop}_{\text{ATR}} = \text{Entry\_Mid} - (k \times \text{ATR}_{14})$$
$$\text{Stop}_{\text{final}} = \min(\text{Stop}_{\text{structure}}, \text{Stop}_{\text{ATR}})$$
$$\text{Stop\_Distance}_{\text{ATR}} = \frac{\text{Entry\_Max} - \text{Stop}_{\text{final}}}{\text{ATR}_{14}}$$

**Safety Checks:**
1. `stop_final < entry_min` (Stop must strictly sit below entry zone).
2. `ATR14 > 0` (Non-zero volatility required).
3. `stop_distance_atr <= max_stop_distance_atr` (Default 4.0 ATR; invalid if stop is excessively wide).
4. *Rule:* Never tighten stop above structural invalidation level just to force RR to pass!

### B. Take-Profit Targets & RR Gate (`targets.py` — A07, P5-09)
* $\text{TP1}$: First meaningful confirmed structural resistance level known at signal timestamp.
* $\text{Risk} = \text{Entry\_Max} - \text{Stop}_{\text{final}}$
* $\text{RR}_{\text{TP1}} = \frac{\text{TP1} - \text{Entry\_Max}}{\text{Risk}}$
* **Hard Gate:** If $\text{RR}_{\text{TP1}} < 1.80 \rightarrow$ Risk Plan is **INVALID**.
* *Rule:* Never skip a nearby resistance level to cherry-pick a distant target! $\text{TP2}$ can never rescue an invalid $\text{TP1}$.
* $\text{TP2}$: Next higher-timeframe resistance level or configured ATR expansion ($2.0 - 3.0\times$ ATR).

---

## 3. Causal Entry Execution Model (`engine/risk/execution.py`)

A signal generated at the close of a 10:00–10:15 candle is knowable only at $t \ge \text{signal\_ts} + \text{latency}$.

$$\text{Earliest\_Exec\_TS} = \text{Signal\_Generated\_At} + \text{Latency\_Seconds}$$

### 3 Execution Policies (A19, A25, A27, P5-27)

1. **`NEXT_BAR_OPEN` (A19, A27)**:
   * Fills at the open of the first bar whose $\text{timestamp\_open} \ge \text{Earliest\_Exec\_TS}$.
   * Fill price and fill timestamp strictly come from the same bar.
   * Friction: $\text{Raw\_Fill} + \text{Synthetic\_Spread} + \text{Adverse\_Slippage}$.
2. **`MARKET_AFTER_SIGNAL` (A25)**:
   * Fills at the first executable market quote with $\text{timestamp} \ge \text{Earliest\_Exec\_TS}$.
   * Uses actual **ASK** quote (which already has spread embedded).
   * Friction: $\text{Ask} + \text{Adverse\_Slippage}$ (Zero synthetic spread to prevent double counting).
3. **`LIMIT_ZONE` (P5-19, P5-27)**:
   * Order becomes active only at $\text{Earliest\_Exec\_TS}$. Pre-activation touches are ignored.
   * Fills if post-activation $\text{Ask} \le \text{Limit\_Price}$ at $\min(\text{Limit\_Price}, \text{Actual\_Ask})$.
   * Mid-bar activation on parent OHLC candles without intrabar timestamps cannot infer limit fills (fails closed).

---

## 4. Intrabar Ambiguity Resolver (`engine/risk/intrabar.py` — A14, A22, P5-26)

### The Ambiguity Condition
When within the same candle after fill:
$$\text{High} \ge \text{TP} \quad \text{AND} \quad \text{Low} \le \text{SL}$$

### 4 Intrabar Policies (A14)
1. **`LOWER_TIMEFRAME_REPLAY`**: Replays lower-timeframe candles in strict chronological order with verified grid integrity.
2. **`CONSERVATIVE_SL_FIRST`**: Assumes stop loss was hit first, exiting at `stop_final`.
3. **`WORST_CASE`**: Assumes stop loss hit with adverse slippage / gap penalty.
4. **`SKIP_AMBIGUOUS`**: Marks trade as skipped to exclude from performance samples.

### Grid Integrity Pre-Validation & Resolution Hierarchy (P5-26)
* **Parent 4H / 1H Candle:**
  1. Pre-validate 15m sequence for: original chronological order, containment inside parent interval, exact 900s duration, duplicate/overlap rejection, initial coverage at fill timestamp, and grid continuity.
  2. If the 15m sequence is malformed, it **must never select an ambiguous child by list position** and must fail safe to `CONSERVATIVE_SL_FIRST`.
  3. If valid, chronologically inspect 15m bars. If an individual 15m child is ambiguous, slice its own 1m/5m bars and drill down.
* **Parent 15m Candle (or drilled 15m child):**
  1. Pre-validate 1m grid (60s) for continuity and initial coverage.
  2. If 1m valid: replay 1m chronologically.
  3. If 1m incomplete / missing: fallback to 5m grid (300s).
  4. If lower-TF unavailable / malformed: fallback to `CONSERVATIVE_SL_FIRST`.

---

## 5. Phase 5 Acceptance & Targeted Test Matrix

| Test ID | Test Category | Assertion Criteria | Status |
|---|---|---|:---:|
| **A07** | Acceptance | Signal with $RR < 1.80$ is rejected by RiskPlan while historical Phase 4 SignalRecord remains unchanged. | ✅ PASS |
| **A14** | Acceptance | Ambiguous bar resolves via chronological lower-TF replay or falls back to `CONSERVATIVE_SL_FIRST`. | ✅ PASS |
| **A19** | Acceptance | Fill cannot occur at the close price of the signal-generating candle. | ✅ PASS |
| **A22** | Acceptance | Ambiguous 15m candle uses 1m/5m resolution data to determine correct barrier hit. | ✅ PASS |
| **A25** | Acceptance | `MARKET_AFTER_SIGNAL` uses first quote $\ge \text{signal\_ts} + \text{latency}$ without double-counting spread. | ✅ PASS |
| **A27** | Acceptance | `NEXT_BAR_OPEN` price and timestamp come from the first bar open $\ge \text{earliest\_exec\_ts}$. | ✅ PASS |
| **P5-01..P5-24** | Targeted | Stop architecture, ATR guards, RR boundaries, decimal determinism, AST import isolation, and zero exchange APIs. | ✅ PASS |
| **P5-25** | Targeted | Source signal eligibility gate (`BUY_WINDOW` + `BUY` required; others rejected). | ✅ PASS |
| **P5-26** | Targeted | Lower-TF coverage integrity gate & 15m parent grid pre-validation (rejects unordered sequences). | ✅ PASS |
| **P5-27** | Targeted | Causal LIMIT execution contract & mid-bar activation guard. | ✅ PASS |
| **P5-28** | Targeted | Structural stop placement formula and composite stop resolution. | ✅ PASS |
| **P5-29** | Targeted | Direction isolation and provenance tracking. | ✅ PASS |
| **P5-30** | Targeted | Missing confirmed active support zone fails closed. | ✅ PASS |
| **P5-31** | Targeted | Stop coordinates verified and ATR guard strictly enforced. | ✅ PASS |
| **P5-32A** | Targeted | Entry zone strictly derived from support zone (`min`, `mid`, `max` invariant to `latest_close`). | ✅ PASS |
| **P5-32B** | Targeted | `RiskPlanSnapshot` immutable contract explicit fields & backward-compatible aliases. | ✅ PASS |

---

## 6. Definition of Done Checklist

- [x] `RiskPlanner` derives entry coordinates strictly from point-in-time support zones and computes stops, targets, and $RR \ge 1.80$.
- [x] Phase 4 `SignalRecord` remains immutable upon Phase 5 rejection.
- [x] `RiskPlanSnapshot` persists complete provenance (`source_zone_id`, `source_zone_timestamp`, `execution_model_version`, versions).
- [x] `IntrabarResolver` pre-validates 15m/5m/1m grid integrity before replay and fails safe to `CONSERVATIVE_SL_FIRST` on malformed grids.
- [x] `EntryExecutionModel` implements causal next-bar, market quote, and limit simulation without look-ahead bias.
- [x] All 6 Phase 5 Acceptance tests (**A07, A14, A19, A22, A25, A27**) passing.
- [x] All 34 Phase 5 Targeted tests (**P5-01 through P5-32B**) passing.
- [x] Full test suite (192 tests) passing 100% with zero Django issues.

