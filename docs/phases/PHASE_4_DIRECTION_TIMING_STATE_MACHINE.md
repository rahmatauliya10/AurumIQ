# Phase 4: Direction Score, Timing Score & State Machine

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Implement independent Direction Score (0–100) and Timing Score (0–100), the 7-state Selective Gate state machine, human-readable explainers, and idempotent Celery analysis tasks with reproducible fingerprints.

---

## 1. Two-Score Architecture

A market can have strong bullish directional alignment while the immediate entry timing is terrible (e.g. overextended momentum, high event risk). The engine must be able to state **BULLISH $\rightarrow$ WAIT**.

```text
       DIRECTION ENGINE (0–100)           TIMING ENGINE (0–100)
       (Is the trend favorable?)         (Is right now the right entry window?)
                    \                               /
                     \                             /
                      ▼                           ▼
                 SELECTIVE GATE & FINITE STATE MACHINE
                      (NO_TRADE / AVOID / WATCH / READY / BUY_WINDOW)
```

---

## 2. Direction Score (0–100) (`engine/signals/direction.py`)

Weighted aggregation of multi-timeframe directional evidence:

| Component | Weight | Criteria & Calculations |
|---|---|---|
| **Market Regime Quality** | 15 | Score from deterministic 1D/4H regime detector. |
| **Daily Trend Strength** | 10 | 1D EMA alignment, slope normalized by ATR. |
| **4H Trend Strength** | 10 | 4H EMA alignment, ADX directional component. |
| **Market Structure** | 20 | Confirmed HH/HL, Bullish BOS presence, structure support. |
| **Pullback Quality** | 10 | Healthy correction depth ($38.2\% - 61.8\%$ of prior impulse). |
| **Momentum State** | 10 | RSI14 not overbought, MACD bullish turn/divergence. |
| **Volume Confirmation** | 5 | Expansion on impulsive advances, contraction on pullbacks. |
| **XAU Gold Confirmation** | 10 | XAU/USD reference trend and structure alignment (R15). |
| **Macro USD Filter** | 5 | DXY trend tailwind / neutral / headwind. |
| **XAUT Basis Quality** | 5 | Normalized basis z-score penalty if XAUT is excessively overvalued (R19). |

---

## 3. Timing Score (0–100) (`engine/signals/timing.py`)

Evaluates the precision of the entry trigger:

| Component | Weight | Criteria & Calculations |
|---|---|---|
| **Entry Zone Proximity** | 25 | Distance to confirmed support zone normalized by ATR. |
| **15m Reversal Confirmation** | 20 | Closed 15m candle showing rejection / bullish reversal candle. |
| **15m / 1H Momentum Turn** | 15 | Short-term RSI cross or MACD histogram tick upward. |
| **Time Cycle Score (3A/3B)** | 25 | Composite score from Session, Swing Age, and promoted cycles. |
| **Volume Reversal Response** | 10 | Volume spike on reversal off support. |
| **Macro Event Safety** | 5 | Absence of upcoming event blackout. |

---

## 4. Live Signal State Machine (`engine/signals/gate.py`)

7 explicit states with formal transition guards:

```text
NO_TRADE ──> AVOID ──> WATCH ──> READY ──> BUY_WINDOW ──> ACTIVE/PAPER ──> COMPLETED
   ▲          │          │         │            │
   │          │          │         │            │
   └──────────┴──────────┴─────────┴─────<──────┴── INVALIDATED
```

### Transition Guard Rules

| From State | To State | Mandatory Guard Conditions |
|---|---|---|
| `NO_TRADE` | `AVOID` | Data quality is healthy; market regime is Bearish or High Volatility. |
| `AVOID` | `WATCH` | Direction Score $\ge 70.0$; Market Structure intact. |
| `WATCH` | `READY` | Direction Score $\ge 75.0$; Timing Score $\ge 70.0$; Price near Entry Zone. |
| `READY` | `BUY_WINDOW` | Direction Score $\ge 80.0$; Timing Score $\ge 80.0$; 15m candle confirmed closed; Risk plan valid ($RR \ge 1.8$); Data quality healthy. |
| Any | `INVALIDATED` | Confirmed close below structure invalidation level / stop loss. |
| Any | `FORCE_WAIT` | Stale market data or Provider in `TRANSITION` state. |

---

## 5. Signal Explainability (`engine/signals/explainer.py`)

Every generated signal produces structured, human-readable components:

```json
{
  "symbol": "XAUTUSDT",
  "state": "READY",
  "direction_score": 88.5,
  "timing_score": 76.0,
  "market_regime": "BULL_TREND",
  "reasons_positive": [
    "+ 1D and 4H directional EMA alignment is bullish (+18.4 pts)",
    "+ 4H higher-low structure confirmed valid (+19.0 pts)",
    "+ XAU/USD gold reference confirms trend (+10.0 pts)",
    "+ London/NY overlap session has positive historical expectancy (+12.5 pts)"
  ],
  "reasons_negative": [
    "- XAUT premium basis z-score is slightly elevated (-3.2 pts)",
    "- Upcoming medium-impact macro event in 45 minutes (-2.0 pts)"
  ],
  "analysis_fingerprint": "a8f62c1...sha256"
}
```

---

## 6. Celery Idempotency & Persistence (`apps/signals/tasks.py`)

- **Task Key:** `analyze_closed_candle(instrument_id, timeframe, candle_ts, engine_version)`.
- **Reproducibility Fingerprint (R5):**
  $$\text{Fingerprint} = \text{SHA256}(\text{instrument} + \text{timeframe} + \text{candle\_ts} + \text{engine\_ver} + \text{config\_ver} + \text{feat\_ver} + \text{git\_sha})$$
- Unique database constraint on `analysis_fingerprint` guarantees **zero duplicate signals** upon task reruns (A03).

---

## 7. Phase 4 Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A03** | Signal Analysis Idempotency | Rerunning analysis on the same closed candle creates exactly one immutable Signal record. |
| **A04** | Stale Data Hard Gate | Market feed delay exceeding threshold blocks `BUY_WINDOW` and forces `WAIT`. |
| **A08** | Immutable Audit Log | Activating a new `EngineConfig` version does not modify or overwrite historical signals. |
| **A23** | Live Quote Score Immutability | Real-time ticker ticks triggering entry alerts cannot alter closed-candle Direction/Timing scores. |

---

## 8. Definition of Done Checklist

- [ ] `DirectionEngine` and `TimingEngine` independently score context.
- [ ] `SelectiveGate` transitions match all state machine guards.
- [ ] `SignalExplainer` formats structured positive and negative contributors.
- [ ] Celery `analyze_closed_candle` task is fully idempotent via `analysis_fingerprint`.
- [ ] Acceptance tests **A03, A04, A08, A23** passing.
