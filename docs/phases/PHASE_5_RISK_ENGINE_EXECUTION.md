# Phase 5: Risk Engine, Intrabar Resolver & Entry Execution Model

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN** (Long Risk Architecture)  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** ✅ **IMPLEMENTED & VERIFIED** (Side-Aware Risk Planning, Causal Execution & Intrabar Resolver)

---

## XAUUSD Migration Addendum

### 1. Dual-Direction Risk Planning Specification
For the target XAUUSD instrument, the risk engine evaluates both Long and Short candidate signals via `XauUsdRiskPlanner`:

```text
LONG SETUPS (BUY):
1. Candidate Source Gate: Requires candidate_state == BUY_WINDOW and candidate_user_decision == BUY (no WAIT promotion: WAIT -> WAIT)
2. Entry Zone: Derived from Support Zone [Support_Low, Support_High] (highest price_high deterministic selection; ties broken by created_at ASC, price_low ASC, zone_fp ASC)
3. Structure Stop: Support_Low - Structure_Buffer
4. ATR Stop Reference: Entry_Mid - (k * ATR14) [where Entry_Mid = (Entry_Min + Entry_Max) / 2]
5. Stop Final: min(Stop_Structure, Stop_ATR) (invariant: Stop_Final < Entry_Min, planned_risk > 0, stop_distance_atr <= max_stop_distance_atr)
6. Target 1: Nearest confirmed structural resistance strictly above Entry_Max (deduplicated by zone fingerprint; no RR fabrication)
7. Target 2: Next structural resistance (strictly beyond TP1) or optional synthetic ATR expansion (strictly beyond TP1; omitted if <= TP1)
8. Conservative RR Gate: Planned Reward / Planned Risk >= Min_RR (conservative worst-entry: Entry_Max)
   Planned Risk = Entry_Max - Stop_Final
   Planned RR TP1 = (TP1 - Entry_Max) / (Entry_Max - Stop_Final)
9. Production Authority: Layer B published user decision remains strictly WAIT (is_production_authorized = False pending Phase 6)

SHORT SETUPS (SELL):
1. Candidate Source Gate: Requires candidate_state == SELL_WINDOW and candidate_user_decision == SELL (no WAIT promotion: WAIT -> WAIT)
2. Entry Zone: Derived from Resistance Zone [Resistance_Low, Resistance_High] (lowest price_low deterministic selection; ties broken by created_at ASC, price_high DESC, zone_fp ASC)
3. Structure Stop: Resistance_High + Structure_Buffer
4. ATR Stop Reference: Entry_Mid + (k * ATR14) [where Entry_Mid = (Entry_Min + Entry_Max) / 2]
5. Stop Final: max(Stop_Structure, Stop_ATR) (invariant: Stop_Final > Entry_Max, planned_risk > 0, stop_distance_atr <= max_stop_distance_atr)
6. Target 1: Nearest confirmed structural support strictly below Entry_Min (deduplicated by zone fingerprint; no RR fabrication)
7. Target 2: Next structural support (strictly beyond TP1) or optional synthetic ATR contraction (strictly beyond TP1; omitted if <= TP1)
8. Conservative RR Gate: Planned Reward / Planned Risk >= Min_RR (conservative worst-entry: Entry_Min)
   Planned Risk = Stop_Final - Entry_Min
   Planned RR TP1 = (Entry_Min - TP1) / (Stop_Final - Entry_Min)
9. Production Authority: Layer B published user decision remains strictly WAIT (is_production_authorized = False pending Phase 6)
```

### 2. Mathematics, Provenance & Evidence Governance
1. **Decimal Precision:** All prices, ATR14 values, stops, targets, spreads, slippages, and RR metrics strictly use `Decimal` with deterministic quantizing (`0.01`).
2. **Canonical UTC Serialization:** Timestamps require timezone awareness and serialize as canonical ISO-8601 with trailing `Z` (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).
3. **Lossless Fingerprint Architecture:**
   - **`StructureZone` Fingerprint:** Binds `zone_type`, `price_low`, `price_high`, `created_at`, `touches`, and `is_active` (SHA-256).
   - **`QuoteEvidence` Fingerprint:** Binds `evidence_type=QUOTE`, `timestamp`, `bid`, `ask`, and `source` (SHA-256).
   - **`CandleEvidence` Fingerprint:** Binds complete `CandleData` fields including open, high, low, close, volume, and volume evidence type (SHA-256).
   - **`RiskPlan` Fingerprint:** Binds Phase 4 fingerprint, state/decision, side, authoritative $T$, Decimal ATR, entries, stops, targets, RR, zone fingerprints, policy fingerprint, risk version, and caller-injected `code_revision`.
   - **Caller-Injected Code Revision:** The Phase 4 baseline SHA is never hardcoded; producing `code_revision` must be caller-injected.
4. **Target Deduplication & Strict Ordering:**
   - Multi-timeframe target evidence across 15m and 4H is deduplicated by canonical `zone_fingerprint` prior to sorting.
   - Equal-price target zones sort deterministically by `created_at ASC`, `price_high DESC` (for LONG) / `price_low ASC` (for SHORT), and `zone_fingerprint ASC`.
   - Structural or ATR $\text{TP2}$ must sit strictly beyond $\text{TP1}$ ($\text{TP2} > \text{TP1}$ for LONG, $\text{TP2} < \text{TP1}$ for SHORT); equal or inferior TP2 candidates are skipped (`tp2=None`).
5. **No Fake Evidence & NO_FILL Semantics:**
   - Plans failing entry selection or stop validation return `None` for missing coordinates rather than fabricated zero-valued evidence.
   - `NO_FILL` execution returns `is_filled=False`, `raw_executable_price=None`, `fill_price=None`, and `source_evidence_fingerprint=None`.
6. **Execution Simulation & Timestamp Boundary:**
   - Earliest execution timestamp: $\text{earliest\_exec\_ts} = \text{signal\_generated\_at} + \text{latency}$.
   - Eligible execution evidence: $\text{timestamp} \ge \text{earliest\_exec\_ts}$ (exact equality boundary is valid; pre-activation touches strictly ignored).
   - Market execution applies adverse slippage to `ASK` for LONG, `BID` for SHORT; observed spread is not added again synthetically.
   - Candle open execution applies synthetic spread and adverse slippage exactly once to `bar.open`.
   - Limit execution is bounded by limit price. Mid-bar activations without lower-TF evidence fail closed (`is_filled=False`).
7. **Strict Validation & Intrabar Replay:**
   - Strict candle geometric and finite checks (`low <= high`, `open` and `close` bounded, positive finite values).
   - Intrabar parent candle validation fails closed (`UNRESOLVED`) on malformed parents.
   - Malformed lower-TF sequences fall back safely to `CONSERVATIVE_SL_FIRST`.
   - Intrabar `WORST_CASE` resolution: LONG uses $\text{stop} - \text{gap}$, SHORT uses $\text{stop} + \text{gap}$.
8. **Position Sizing Governance:**
   - **POSITION SIZING IS OUT OF SCOPE FOR PHASE 5 AND PHASE 6.** Not authorized without a separate future specification and human approval.

### 3. Threshold & Calibration Status
All numerical risk parameters for XAUUSD (including minimum RR, ATR multiplier $k$, maximum stop distance ATR, and buffer sizes) are managed via `XauUsdRiskProfile`.
- `uncalibrated_xauusd_risk_profile()` defaults all empirical numerics to `None` (zero leaked constants, zero 1.80 fallback in production profile).
- Test fixtures use explicit, clearly marked `TEST_ONLY` configurations.

### 4. Acceptance & Hostile Test Matrix
- **`XAU-P5-01`**: LONG side-aware risk planning contract (Support entry, stop below, nearest resistance TP1, conservative RR using `entry_max`, publication `WAIT`)
- **`XAU-P5-02`**: SHORT side-aware risk planning contract (Resistance entry, stop above, nearest support TP1, conservative RR using `entry_min`, publication `WAIT`)
- **`XAU-P5-03`**: Side-aware market bid/ask execution contract (LONG uses `ASK`, SHORT uses `BID`, adverse slippage, spread counted once, strict quote integrity)
- **Hostile Matrix `H1`–`H74`**: Complete 74-case hostile matrix fully covered across unit test suites (side segregation, tie resolution, decimal math, boundary equality, fingerprints, immutable provenance, strict validation, fail-closed guards).

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **APPROVED**  
> **Primary Goal:** Implement deterministic, point-in-time Risk Planning (Entry Zone, Structure Stop, ATR Stop, Resistance Targets, RR Gate), an Intrabar Ambiguity Resolver with pre-validated grid replay, and a Causal Entry Execution Model without placing real orders.

### 1. Core Operating Invariants

```text
PHASE 4 SIGNAL (BUY_WINDOW) ─── Immutable Audit Trail Preserved
       │
       ▼
┌────────────────────────────────────────────────────────┐
│                   PHASE 5 RISK PLAN                    │
├────────────────────────────────────────────────────────┤
│ 1. Entry Zone: Derived from Active Support Zone        │
│ 2. Structure Stop: Below Support Zone - Buffer         │
│ 3. ATR Stop: Entry_Mid - (k * ATR14)                   │
│ 4. Stop Final: min(Stop_Structure, Stop_ATR)           │
│ 5. Target 1: Nearest Confirmed Resistance Zone         │
│ 6. RR Gate: (TP1 - Entry_Max) / (Entry_Max - Stop)     │
└──────────────────────────┬─────────────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
       RR >= 1.80                  RR < 1.80
   is_valid_risk_plan = True   is_valid_risk_plan = False
   execution_eligible = True   execution_eligible = False
   effective_action = BUY      effective_action = WAIT
```

### Invariant 1: Source Signal Eligibility Gate (P5-25)
Only candidate signals in internal state `BUY_WINDOW` with user decision `BUY` are eligible for active Risk Planning. All other signals (`NO_TRADE`, `AVOID`, `WATCH`, `READY`, `FORCE_WAIT`) result in `is_valid_risk_plan = False`, `execution_eligible = False`, and `effective_action = WAIT`.

### Invariant 2: Phase 4 Signal Immutability (A07)
If a Phase 4 `BUY_WINDOW` signal fails Phase 5 Risk Planning (e.g. $RR < 1.80$ or excessively wide stop):
* **Phase 4 `SignalRecord` remains completely unchanged** (`state = BUY_WINDOW`, `decision = BUY`).
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

### 2. Stop Loss & Target Engine (`engine/risk/stops.py`, `targets.py`)

#### A. Structure Invalidation & ATR Stop Guard (`stops.py` — P5-28, P5-31)
$$\text{Stop}_{\text{structure}} = \text{Support\_Zone\_Low} - \text{Structure\_Buffer}$$
$$\text{Stop}_{\text{ATR}} = \text{Entry\_Mid} - (k \times \text{ATR}_{14})$$
$$\text{Stop}_{\text{final}} = \min(\text{Stop}_{\text{structure}}, \text{Stop}_{\text{ATR}})$$
$$\text{Stop\_Distance}_{\text{ATR}} = \frac{\text{Entry\_Max} - \text{Stop}_{\text{final}}}{\text{ATR}_{14}}$$

**Safety Checks:**
1. `stop_final < entry_min` (Stop must strictly sit below entry zone).
2. `ATR14 > 0` (Non-zero volatility required).
3. `stop_distance_atr <= max_stop_distance_atr` (Default 4.0 ATR; invalid if stop is excessively wide).
4. *Rule:* Never tighten stop above structural invalidation level just to force RR to pass!

#### B. Take-Profit Targets & RR Gate (`targets.py` — A07, P5-09)
* $\text{TP1}$: First meaningful confirmed structural resistance level known at signal timestamp.
* $\text{Risk} = \text{Entry\_Max} - \text{Stop}_{\text{final}}$
* $\text{RR}_{\text{TP1}} = \frac{\text{TP1} - \text{Entry\_Max}}{\text{Risk}}$
* **Hard Gate:** If $\text{RR}_{\text{TP1}} < 1.80 \rightarrow$ Risk Plan is **INVALID**.
* *Rule:* Never skip a nearby resistance level to cherry-pick a distant target! $\text{TP2}$ can never rescue an invalid $\text{TP1}$.
* $\text{TP2}$: Next higher-timeframe resistance level or configured ATR expansion ($2.0 - 3.0\times$ ATR).

### 3. Causal Entry Execution Model (`engine/risk/execution.py`)

A signal generated at the close of a 10:00–10:15 candle is knowable only at $t \ge \text{signal\_ts} + \text{latency}$.

$$\text{Earliest\_Exec\_TS} = \text{Signal\_Generated\_At} + \text{Latency\_Seconds}$$

#### 3 Execution Policies (A19, A25, A27, P5-27)

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

### 4. Intrabar Ambiguity Resolver (`engine/risk/intrabar.py` — A14, A22, P5-26)

#### The Ambiguity Condition
When within the same candle after fill:
$$\text{High} \ge \text{TP} \quad \text{AND} \quad \text{Low} \le \text{SL}$$

#### 4 Intrabar Policies (A14)
1. **`LOWER_TIMEFRAME_REPLAY`**: Replays lower-timeframe candles in strict chronological order with verified grid integrity.
2. **`CONSERVATIVE_SL_FIRST`**: Assumes stop loss was hit first, exiting at `stop_final`.
3. **`WORST_CASE`**: Assumes stop loss hit with adverse slippage / gap penalty.
4. **`SKIP_AMBIGUOUS`**: Marks trade as skipped to exclude from performance samples.

#### Grid Integrity Pre-Validation & Resolution Hierarchy (P5-26)
* **Parent 4H / 1H Candle:**
  1. Pre-validate 15m sequence for: original chronological order, containment inside parent interval, exact 900s duration, duplicate/overlap rejection, initial coverage at fill timestamp, and grid continuity.
  2. If the 15m sequence is malformed, it **must never select an ambiguous child by list position** and must fail safe to `CONSERVATIVE_SL_FIRST`.
  3. If valid, chronologically inspect 15m bars. If an individual 15m child is ambiguous, slice its own 1m/5m bars and drill down.
* **Parent 15m Candle (or drilled 15m child):**
  1. Pre-validate 1m grid (60s) for continuity and initial coverage.
  2. If 1m valid: replay 1m chronologically.
  3. If 1m incomplete / missing: fallback to 5m grid (300s).
  4. If lower-TF unavailable / malformed: fallback to `CONSERVATIVE_SL_FIRST`.

### 5. Phase 5 Acceptance & Targeted Test Matrix

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

### 6. Definition of Done Checklist

- [x] `RiskPlanner` derives entry coordinates strictly from point-in-time support zones and computes stops, targets, and $RR \ge 1.80$.
- [x] Phase 4 `SignalRecord` remains immutable upon Phase 5 rejection.
- [x] `RiskPlanSnapshot` persists complete provenance (`source_zone_id`, `source_zone_timestamp`, `execution_model_version`, versions).
- [x] `IntrabarResolver` pre-validates 15m/5m/1m grid integrity before replay and fails safe to `CONSERVATIVE_SL_FIRST` on malformed grids.
- [x] `EntryExecutionModel` implements causal next-bar, market quote, and limit simulation without look-ahead bias.
- [x] All 6 Phase 5 Acceptance tests (**A07, A14, A19, A22, A25, A27**) passing.
- [x] All 34 Phase 5 Targeted tests (**P5-01 through P5-32B**) passing.
- [x] Full test suite (192 tests) passing 100% with zero Django issues.
