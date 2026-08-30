# Phase 4: Direction Score, Timing Score & State Machine

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN** (Long Direction Baseline)  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🔴 **DUAL-SIDE REDESIGN REQUIRED (NOT IMPLEMENTED)**

---

## XAUUSD Migration Addendum

### 1. Dual-Side Scoring Architecture (Conceptual Target)
For the target XAUUSD instrument, scoring is generalized into side-aware evaluators:
$$\text{LongDirectionScore} = \sum w_i \cdot \text{LongEvidence}_i$$
$$\text{ShortDirectionScore} = \sum w_i \cdot \text{ShortEvidence}_i$$

### 2. Target State Machine & User Decision Mapping
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

### 3. XAUUSD Scoring Weights & Thresholds Status
All feature weights, scoring component distributions, and selective gate thresholds (such as 70, 75, 80) for XAUUSD are **NOT FROZEN / REVALIDATION REQUIRED**. Exact numerical parameters will be calibrated empirically during Phase 6 walk-forward backtesting. Macro components (DXY, 10Y Yields, Gold Futures) are candidate components only and carry no frozen weights.

### 4. Approved Future Test Contracts (Planned)
- **`XAU-P4-01`**: BUY candidate contract (`PLANNED / FUTURE CONTRACT`)
- **`XAU-P4-02`**: SELL candidate contract (`PLANNED / FUTURE CONTRACT`)
- **`XAU-P4-03`**: Long/Short conflict resolves to `WAIT` (`PLANNED / FUTURE CONTRACT`)
- **`XAU-P4-04`**: Macro blackout blocks both BUY and SELL (`PLANNED / FUTURE CONTRACT`)

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **APPROVED**  
> **Primary Goal:** Implement deterministic Direction Score (0–100), Timing Score (0–100), Selective Gate state machine with BUY / WAIT / AVOID decision mapping, human-readable explainers, canonical production input fingerprinting, and idempotent persistence.

### 1. Two-Score Architecture

A market can have strong bullish directional alignment while the immediate entry timing is terrible (e.g. overextended momentum, active macro blackout). The engine explicitly separates trend conviction from entry timing:

```text
       DIRECTION ENGINE (0–100)            TIMING ENGINE (0–100)
       (Is the trend favorable?)         (Is right now the right entry window?)
                    \                               /
                     \                             /
                      ▼                           ▼
                 SELECTIVE GATE & FINITE STATE MACHINE
                 (NO_TRADE / AVOID / WATCH / READY / BUY_WINDOW)
                                  │
                                  ▼
                        USER DECISION MAPPING
                         (BUY / WAIT / AVOID)
```

### 2. Direction Score (0–100) (`engine/signals/direction.py`)

Weighted aggregation of multi-timeframe directional evidence (Config Version 1.0):

| Component | Max Weight | Criteria & Calculations |
|---|---|---|
| **Market Regime Quality** | 15 | Score from deterministic 1D/4H regime detector (`BULL_TREND` = 15, `RANGE` = 5, `BEAR_TREND`/`HIGH_VOLATILITY` = 0). |
| **1D + 4H Trend Alignment** | 20 | 1D & 4H EMA alignment (20/50/200), positive scale-invariant slope, ADX trend component. |
| **Confirmed Structure / BOS** | 20 | Confirmed HH/HL sequence, bullish BOS confirmation, distance above active support. |
| **Pullback Quality** | 10 | Healthy correction depth (38.2%–61.8% retracement of prior swing impulse). |
| **Momentum State** | 10 | RSI14 in healthy non-overbought zone (45–65), MACD bullish histogram expansion. |
| **Volume Confirmation** | 5 | Volume expansion on impulse advances, contraction on consolidation pullbacks. |
| **XAU Alignment & XAUT Basis Quality** | 20 | Canonical XAU/USD trend concordance (10 pts) + USDT/USD normalized XAUT basis z-score stability (10 pts). |
| **Total** | **100** | |

*Strict Invariant:* No evidence or missing historical data receives 0 points for that component.

### 3. Timing Score (0–100) (`engine/signals/timing.py`)

Evaluates the precision of the entry trigger (Config Version 1.0):

| Component | Max Weight | Criteria & Calculations |
|---|---|---|
| **Entry Zone Proximity / ATR** | 25 | Price within 1.0 ATR of confirmed active support zone bounding box. |
| **Closed 15m Reversal Confirmation** | 20 | Closed 15m candle showing bullish rejection (pin bar / engulfing / bullish close). |
| **15m / 1H Momentum Turn** | 15 | Short-term RSI upward cross or MACD histogram positive momentum tick. |
| **Phase 3A Robust Timing** | 25 | Composite score from Session statistical expectancy, Swing maturity, and Calendar flows. |
| **Volume Response** | 10 | Volume spike on reversal off support zone. |
| **Macro Event Safety** | 5 | Proximity to macro releases outside blackout buffer. |
| **Total** | **100** | |

*Phase 3B Invariant:* Experimental spectral features contribute **0.0 points** to production Timing Score.

### 4. Selective Gate & Finite State Machine (`engine/signals/gate.py`)

#### Production Internal States & User Decision Mapping

```text
INTERNAL STATE        USER DECISION
──────────────        ─────────────
NO_TRADE        →     WAIT
AVOID           →     AVOID
WATCH           →     WAIT
READY           →     WAIT
BUY_WINDOW      →     BUY

FORCE_WAIT      →     WAIT  (Hard Override)
```

#### Precedence Gate Rules

1. **Uninitialized / Insufficient Critical Data** $\rightarrow$ `NO_TRADE` (`WAIT`).
2. **Bear Regime / Structurally Hostile Condition** $\rightarrow$ `AVOID` (`AVOID`).
3. **Hard Gate Blockers** (Stale market data, provider in `TRANSITION`, active macro blackout, unclosed decision candle, missing canonical XAU reference) $\rightarrow$ `FORCE_WAIT` (`WAIT`).
4. **Direction Score $< 70.0$** $\rightarrow$ `NO_TRADE` or `WATCH` (`WAIT`).
5. **Direction Score $\ge 70.0$ & Structure Intact** $\rightarrow$ `WATCH` (`WAIT`).
6. **Direction Score $\ge 75.0$, Timing Score $\ge 70.0$, Near Support Zone** $\rightarrow$ `READY` (`WAIT`).
7. **Direction Score $\ge 80.0$, Timing Score $\ge 80.0$, Closed 15m Reversal Confirmed, All Feeds Healthy** $\rightarrow$ `BUY_WINDOW` (`BUY`).

*Important:* No SL, TP, Risk/Reward (RR), or position sizing is evaluated in Phase 4.

### 5. Canonical Analysis Fingerprint & Signal Explainer (`engine/signals/explainer.py`)

#### Canonical Deterministic Fingerprint
$$\text{analysis\_fingerprint} = \text{SHA256}(\text{canonical\_json}(\text{production\_inputs}))$$

Payload includes:
* `instrument`, `timeframe`, `as_of` (UTC ISO-8601)
* Closed candle hashes / features (15m, 4h, 1d)
* Individual Direction & Timing component breakdowns
* Canonical XAU reference value & timestamp
* USDT/USD normalization rate & timestamp
* Provider health / data quality state
* Phase 2 feature/regime/structure versions
* Phase 3A cycle snapshot & version
* `engine_version`, `config_version`, `feature_version`, `code_revision`

*Strict Rules:*
* Live quote ticks are EXCLUDED (preserves closed-candle score immutability).
* Phase 3B experimental output is EXCLUDED from production fingerprint.

### 6. Immutable Signal Persistence (`apps/signals/`)

- Model: `SignalRecord` (app `apps/signals/`).
- Idempotency: `get_or_create(analysis_fingerprint=...)` ensures duplicate task runs return the existing record without modifying history.
- Historical Immutability: New configuration version or corrected data produces a new distinct fingerprint and record, never overwriting historical signals.

### 7. Acceptance Test Suite Phase 4

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A03** | Signal Analysis Idempotency | Rerunning analysis on the same closed candle creates exactly one immutable `SignalRecord`. Corrected data creates a distinct second record. |
| **A04** | Stale Data Hard Gate | Point-in-time stale feed forces `FORCE_WAIT` and blocks `BUY_WINDOW` regardless of scores. |
| **A08** | Immutable Audit Log | Activating `ConfigVersion B` creates a new signal while preserving `Signal A` unchanged. |
| **A23** | Live Quote Score Immutability | Real-time price fluctuations do not alter closed-candle Direction/Timing scores or fingerprint. |

### 8. Definition of Done Checklist

- [x] `DirectionEngine` and `TimingEngine` evaluate pure closed-candle evidence.
- [x] Selective Gate implements strict state precedence and `BUY/WAIT/AVOID` mapping.
- [x] Canonical deterministic SHA-256 analysis fingerprint operational.
- [x] Phase 3B contribution strictly locked to 0.0.
- [x] Celery idempotent task and `SignalRecord` persistence bridge in place.
- [x] Acceptance tests **A03, A04, A08, A23** passing.
- [x] Targeted tests **P4-01 through P4-22** passing.
