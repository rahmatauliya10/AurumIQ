# Phase 4: Direction Score, Timing Score & State Machine

> **Status:** ✅ **APPROVED**  
> **Primary Goal:** Implement deterministic Direction Score (0–100), Timing Score (0–100), Selective Gate state machine with side-aware `BUY / WAIT / SELL` decision mapping, human-readable explainers, canonical production input fingerprinting, and idempotent persistence.

---

## 1. Two-Score Architecture

A market can have strong directional alignment while the immediate entry timing is terrible (e.g. overextended momentum, active macro blackout). The engine explicitly separates trend conviction from entry timing:

```text
       DIRECTION ENGINE (0–100)            TIMING ENGINE (0–100)
       (Is the trend favorable?)         (Is right now the right entry window?)
                    \                               /
                     \                             /
                      ▼                           ▼
                 SELECTIVE GATE & FINITE STATE MACHINE
                 (NO_TRADE / AVOID / WATCH / READY / BUY_WINDOW / SELL_CANDIDATE)
                                  │
                                  ▼
                        USER DECISION MAPPING
                         (BUY / WAIT / SELL)
```

---

## 2. Direction Score (0–100) (`engine/signals/direction.py`)

Weighted aggregation of multi-timeframe directional evidence for XAUUSD:

> [!NOTE]
> **NOT FROZEN / REVALIDATION REQUIRED**: Preliminary weights shown below serve as reference defaults and require empirical recalibration during XAUUSD historical baseline research.

| Component | Max Weight | Criteria & Calculations | Calibration Status |
|---|---|---|:---:|
| **Market Regime Quality** | 15 | Score from deterministic 1D/4H regime detector (`BULL_TREND` / `BEAR_TREND` alignment). | *Revalidation Required* |
| **1D + 4H Trend Alignment** | 20 | 1D & 4H EMA alignment (20/50/200), positive scale-invariant slope, ADX trend component. | *Revalidation Required* |
| **Confirmed Structure / BOS** | 20 | Confirmed HH/HL (Bullish) or LH/LL (Bearish) sequence, structural BOS confirmation. | *Revalidation Required* |
| **Pullback / Retracement Quality** | 10 | Healthy correction depth (38.2%–61.8% retracement of prior swing impulse). | *Revalidation Required* |
| **Momentum State** | 10 | RSI14 in healthy non-extreme zone, MACD directional histogram expansion. | *Revalidation Required* |
| **Volume Confirmation** | 10 | Volume expansion on impulse advances, contraction on consolidation pullbacks. | *Revalidation Required* |
| **Macro USD Alignment** | 15 | Canonical Gold vs USD index (DXY) / macro sentiment concordance. | *Revalidation Required* |
| **Total** | **100** | | |

*Strict Invariant:* Missing historical data receives 0 points for that component.

---

## 3. Timing Score (0–100) (`engine/signals/timing.py`)

Evaluates the precision of the entry trigger:

> [!NOTE]
> **NOT FROZEN / REVALIDATION REQUIRED**: Preliminary weights shown below serve as reference defaults and require empirical recalibration during XAUUSD historical baseline research.

| Component | Max Weight | Criteria & Calculations | Calibration Status |
|---|---|---|:---:|
| **Entry Zone Proximity / ATR** | 25 | Price within 1.0 ATR of confirmed active support/resistance zone bounding box. | *Revalidation Required* |
| **Closed 15m Reversal Confirmation** | 20 | Closed 15m candle showing rejection (pin bar / engulfing / directional close). | *Revalidation Required* |
| **15m / 1H Momentum Turn** | 15 | Short-term RSI cross or MACD histogram directional momentum tick. | *Revalidation Required* |
| **Phase 3A Robust Timing** | 25 | Composite score from Session statistical expectancy, Swing maturity, and Calendar flows. | *Revalidation Required* |
| **Volume Response** | 10 | Volume spike on reversal off active zone. | *Revalidation Required* |
| **Macro Event Safety** | 5 | Proximity to macro releases outside blackout buffer. | *Revalidation Required* |
| **Total** | **100** | | |

*Phase 3B Invariant:* Experimental spectral features contribute **0.0 points** to production Timing Score.

---

## 4. Selective Gate & Finite State Machine (`engine/signals/gate.py`)

### Production Internal States & User Decision Mapping

```text
INTERNAL STATE          USER DECISION
──────────────          ─────────────
NO_TRADE          →     WAIT
AVOID             →     WAIT / AVOID
WATCH             →     WAIT
READY             →     WAIT
BUY_WINDOW        →     BUY
SELL_CANDIDATE    →     SELL

FORCE_WAIT        →     WAIT  (Hard Override)
```

### Precedence Gate Rules

1. **Uninitialized / Insufficient Critical Data** $\rightarrow$ `NO_TRADE` (`WAIT`).
2. **Structurally Hostile Condition / Choppy Noise** $\rightarrow$ `AVOID` (`WAIT`).
3. **Hard Gate Blockers** (Stale market data, provider in `TRANSITION`, active macro blackout, unclosed decision candle, missing gold reference) $\rightarrow$ `FORCE_WAIT` (`WAIT`).
4. **Direction Score $< 70.0$** $\rightarrow$ `NO_TRADE` or `WATCH` (`WAIT`).
5. **Direction Score $\ge 70.0$ & Structure Intact** $\rightarrow$ `WATCH` (`WAIT`).
6. **Direction Score $\ge 75.0$, Timing Score $\ge 70.0$, Near Active Zone** $\rightarrow$ `READY` (`WAIT`).
7. **Bullish Direction $\ge 80.0$, Timing $\ge 80.0$, Closed 15m Bullish Reversal Confirmed** $\rightarrow$ `BUY_WINDOW` (`BUY`).
8. **Bearish Direction $\ge 80.0$, Timing $\ge 80.0$, Closed 15m Bearish Reversal Confirmed** $\rightarrow$ `SELL_CANDIDATE` (`SELL`).

*Important:* No SL, TP, Risk/Reward (RR), or position sizing is evaluated in Phase 4.

---

## 5. Canonical Analysis Fingerprint & Signal Explainer (`engine/signals/explainer.py`)

### Canonical Deterministic Fingerprint
$$\text{analysis\_fingerprint} = \text{SHA256}(\text{canonical\_json}(\text{production\_inputs}))$$

Payload includes:
* `instrument`, `timeframe`, `as_of` (UTC ISO-8601)
* Closed candle hashes / features (15m, 4h, 1d)
* Individual Direction & Timing component breakdowns
* Primary gold reference value & timestamp
* Provider health / data quality state
* Phase 2 feature/regime/structure versions
* Phase 3A cycle snapshot & version
* `engine_version`, `config_version`, `feature_version`, `code_revision`

*Strict Rules:*
* Live quote ticks are EXCLUDED (preserves closed-candle score immutability).
* Phase 3B experimental output is EXCLUDED from production fingerprint.

---

## 6. Immutable Signal Persistence (`apps/signals/`)

- Model: `SignalRecord` (app `apps/signals/`).
- Idempotency: `get_or_create(analysis_fingerprint=...)` ensures duplicate task runs return the existing record without modifying history.
- Historical Immutability: New configuration version or corrected data produces a new distinct fingerprint and record, never overwriting historical signals.

---

## 7. Acceptance Test Suite Phase 4

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A03** | Signal Analysis Idempotency | Rerunning analysis on the same closed candle creates exactly one immutable `SignalRecord`. Corrected data creates a distinct second record. |
| **A04** | Stale Data Hard Gate | Point-in-time stale feed forces `FORCE_WAIT` and blocks signals regardless of scores. |
| **A08** | Immutable Audit Log | Activating `ConfigVersion B` creates a new signal while preserving `Signal A` unchanged. |
| **A23** | Live Quote Score Immutability | Real-time price fluctuations do not alter closed-candle Direction/Timing scores or fingerprint. |

---

## 8. Definition of Done Checklist

- [x] `DirectionEngine` and `TimingEngine` evaluate pure closed-candle evidence.
- [x] Selective Gate implements strict state precedence and side-aware decision mapping.
- [x] Canonical deterministic SHA-256 analysis fingerprint operational.
- [x] Phase 3B contribution strictly locked to 0.0.
- [x] Celery idempotent task and `SignalRecord` persistence bridge in place.
- [x] Acceptance tests **A03, A04, A08, A23** passing.
- [x] Targeted tests **P4-01 through P4-22** passing.
