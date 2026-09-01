# Phase 6: XAUUSD Point-in-Time Backtesting, Walk-Forward Validation & Ablation

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟡 **NOT STARTED (PIT BACKTEST + WALK-FORWARD + ABLATION REQUIRED)**  
> **Canonical Status:** **SINGLE ACTIVE GOVERNING SPECIFICATION FOR PHASE 6 (COMBINING 6A & 6B)**

---

## XAUUSD Canonical Validation Architecture (Phase 6A & Phase 6B)

Phase 6 is the empirical governance and validation laboratory for XAUUSD. It directly resolves the production pure-Python calculation engine (`XauUsdSignalEngine`, `XauUsdRiskPlanner`, `SideAwareEntryExecutionModel`, and `SideAwareIntrabarResolver`) with zero look-ahead bias, zero double-counted costs, zero speculative account sizing, and strict out-of-sample isolation.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                      PHASE 6 VALIDATION PIPELINE                        │
│                                                                         │
│  CLOSED CANDLE DATA STORE (15m, 1H, 4H, 1D)                             │
│         │                                                               │
│         ▼                                                               │
│  POINT-IN-TIME MARKET CONTEXT (Strictly timestamp <= T)                 │
│         │                                                               │
│         ├──► [PHASE 6A] REPLAY & WALK-FORWARD VALIDATION                │
│         │    - Direct Phase 4 Engine Call (XAU-P4-01..04)               │
│         │    - Direct Phase 5 Risk Planner (XAU-P5-01..03)              │
│         │    - Causal Fills (t >= signal_ts + latency)                  │
│         │    - 1m/5m Intrabar Replay / Conservative SL-First            │
│         │    - Chronological Folds with Purging & Embargo               │
│         │    - Normalized R Metrics (No Account Sizing / No Compounding)│
│         │    - Reporting: LONG (XAU-P6-01), SHORT (XAU-P6-02),          │
│         │      Combined Parity (XAU-P6-03)                              │
│         │                                                               │
│         └──► [PHASE 6B] COMPONENT ABLATION LAB                          │
│              - Isolated Paired Folds (Regime, Structure, Trend,         │
│                Session, Swing Maturity, Macro Blackout, Phase 3B)       │
│              - Zero Mutation of Baseline Candidate Results              │
│              - Marginal Expectancy & Sharpe Contribution                │
│              - Empirical Evidence Requirement for Production Promotion  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Phase 6A: Point-in-Time Replay & Walk-Forward Validation

### A. Non-Negotiable Core Principle: The One Engine Rule (R2 & A09)
The backtesting engine must NEVER maintain a simplified secondary set of trading rules. Live analysis, paper observation, and historical backtesting must resolve the **exact same pure-Python `XauUsdSignalEngine` and `XauUsdRiskPlanner` classes, configurations, and feature sets**.

### B. Point-in-Time Causality & Data Isolation (R3, R8, A31)
1. **Closed-Candle Isolation:** For historical evaluation step $T$, market data queries strictly apply `timestamp_close <= T` and `is_closed=True`. Unclosed candles at or before $T$ raise `IncompleteCandleError`.
2. **Future Mutation Masking:** Perturbing or altering market data with timestamp $> T$ or after a trade's exit timestamp must produce zero difference in historical outputs at $T$.
3. **Causal Execution Timestamp (R13, A19, A27):** Fills occur strictly at $t \ge t_{\text{signal}} + \text{latency}$. Immediate execution at signal timestamp $t_{\text{signal}}$ is impossible.

### C. Frictions & Spread Deduplication Model (`engine/backtest/costs.py`, A32)
1. **Actual Quotes:** When executable Bid/Ask quotes exist, spread is embedded in the quote ($\text{Spread}_{\text{synthetic}} = 0$). Double-counting spread is prohibited.
2. **Mid / OHLC Candles:** Synthetic spread is applied exactly once (half-spread on entry, half-spread on exit).
3. **Adverse Slippage:** Slippage is strictly adverse (adds to LONG entry price and SHORT exit price; subtracts from SHORT entry and LONG exit proceeds).
4. **Fees:** Explicit maker/taker percentage applied deterministically per leg.

### D. Outcome Resolution & Intrabar Replay (R14, A14)
1. **Deterministic Barriers:** Resolved against Take Profit 1 (`TP1_FIRST`), Stop Loss (`SL_FIRST`), or Horizon Timeout (`TIMEOUT`).
2. **Chronological Intrabar Replay:** Ambiguous parent candles (where both TP and SL are within the candle's high-low range) resolve via chronological lower-timeframe (1m preferred, 5m fallback) sequence.
3. **Conservative Fail-Safe:** If lower-timeframe data is missing or malformed, the resolver falls back to `CONSERVATIVE_SL_FIRST`.

### E. Walk-Forward Cross-Validation with Purging & Embargo (R11, A34, A35)
1. **Chronological Splitting:** Multi-year spot XAUUSD data is partitioned into rolling out-of-sample folds without random shuffling.
2. **Label Purging:** Removes historical samples immediately preceding fold boundaries whose triple-barrier evaluation horizon overlaps the boundary.
3. **Post-Boundary Embargo:** Enforces a protective buffer after each test set to prevent serial correlation leakage into subsequent folds.

### F. Normalized R Metrics (Strictly No Account Sizing or Compounding)
1. **Expectancy per Trade:**
   $$\mathbb{E}[R] = (\text{Win\_Rate} \times \bar{R}_{\text{win}}) - ((1 - \text{Win\_Rate}) \times \bar{R}_{\text{loss}})$$
2. **Normalized Drawdown:** Measured strictly in $R$ units (`max_drawdown_r`, `drawdown_duration_trades`).
3. **Account Sizing Policy:** Position sizing, account balance tracking, margin calculations, and leverage compounding are **STRICTLY OUT OF SCOPE** for Phase 6.

---

## 2. Phase 6B: Component Ablation & Calibration Evidence

### A. Isolated Paired Fold Analysis (A10, A37)
Quantifies the exact marginal out-of-sample contribution of each individual engine subsystem by comparing the full baseline model against ablated variants:
- **Baseline:** Multi-Timeframe Trend & Causal Structure Only.
- **+ Market Regime Filter:** Evaluates volatility regime gating.
- **+ Phase 3A Session Expectancy:** Evaluates DST-aware statistical session edge.
- **+ Phase 3A Swing Duration Maturity:** Evaluates knowable swing age filtering ($P75-P90$).
- **+ Phase 3A Macro Blackout Gate:** Evaluates capital protection during scheduled high-impact events.
- **+ Phase 3B Experimental Spectral Factors:** Evaluates whether promoted spectral features add statistically significant out-of-sample information without overfitting.

### B. Ablation Invariants
1. **No Baseline Mutation:** Running an ablation trial must NEVER alter or mutate the primary candidate signal dataset.
2. **No In-Sample Optimization:** No feature or threshold is approved for production promotion based solely on in-sample win rate. Promotion requires verified positive out-of-sample $R$ expectancy, stability across folds ($\ge 0.60$), and statistical significance ($p < 0.05$).

---

## 3. Official Planned Test Contracts

| Contract ID | Name | Focus | Scope | Status |
|---|---|---|---|:---:|
| **`XAU-P6-01`** | LONG Point-in-Time Replay | Verifies PIT replay, causal execution, intrabar collision resolution, and normalized $R$ metrics for BUY candidates | Phase 6A | 🟡 `PLANNED / FUTURE CONTRACT` |
| **`XAU-P6-02`** | SHORT Point-in-Time Replay | Verifies PIT replay, causal execution, intrabar collision resolution, and normalized $R$ metrics for SELL candidates | Phase 6A | 🟡 `PLANNED / FUTURE CONTRACT` |
| **`XAU-P6-03`** | Dual-Side Parity & Ablation | Verifies combined portfolio parity, walk-forward purging/embargo, and isolated component ablation | Phase 6A & 6B | 🟡 `PLANNED / FUTURE CONTRACT` |

---

## 4. Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** 🟢 **COMPLETED, RIGOROUSLY VERIFIED & FROZEN**  
> **Baseline Commit SHA:** `f22483addd7cc5095c46e4f1c928a8b6651d83eb`  
> **Primary Goal:** Construct point-in-time historical simulation, walk-forward validation, and component ablation engine that directly resolves the production `SignalEngine` and `RiskPlanner`.

### Historical Acceptance Test Matrix (XAUT Reference)

| Test ID | Test Name | Gate Criteria | Status |
|---|---|---|:---:|
| **P6-01** | PIT Candle Filtering | `timestamp_close <= as_of` & `is_closed == True` | ✅ PASS |
| **P6-02** | Future Mutation Safety | Mutating $> T$ / $> \text{exit}$ preserves historical outputs | ✅ PASS |
| **P6-03** | Closed Candle Only | Unclosed candle at $T$ rejected from decision set | ✅ PASS |
| **P6-04** | Engine Reuse (A09) | Master `XautSignalEngine` directly resolved | ✅ PASS |
| **P6-05** | Planner Reuse | Master `RiskPlanner` directly resolved | ✅ PASS |
| **P6-06** | No Same-Bar Execution | $t_{\text{fill}} \ge t_{\text{signal}} + \text{latency}$ strictly enforced | ✅ PASS |
| **P6-07** | ASK Spread Integrity | Spread not double counted on actual quote entry | ✅ PASS |
| **P6-08** | BID Spread Integrity | Spread not double counted on actual quote exit | ✅ PASS |
| **P6-09** | Synthetic Spread Once | Mid candles receive half-spread per leg | ✅ PASS |
| **P6-10** | Explicit Fee Accounting | Separate entry and exit fees applied | ✅ PASS |
| **P6-11** | Adverse Slippage | Slippage always penalizes trader | ✅ PASS |
| **P6-12** | Gross vs Net Determinism | Deterministic PnL, return %, and $R$ accounting | ✅ PASS |
| **P6-13** | TP1 Terminal Resolution | Barrier hit on TP1 resolves to `TP1_FIRST` | ✅ PASS |
| **P6-14** | SL Terminal Resolution | Barrier hit on SL resolves to `SL_FIRST` | ✅ PASS |
| **P6-15** | No-Fill Outcome | Valid outcome when fill conditions not met | ✅ PASS |
| **P6-16** | Conservative Intrabar | Fallback to `CONSERVATIVE_SL_FIRST` on ambiguity | ✅ PASS |
| **P6-17** | MFE / MAE Causality | Excludes mid-bar candle active during fill | ✅ PASS |
| **P6-18** | Trade Persistence | Point-in-time trade persistence with SHA-256 fingerprint | ✅ PASS |
