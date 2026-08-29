# Phase 5: Risk Engine, Intrabar Resolver & Entry Execution Model

> **Status:** 📋 **PLANNED**  
> **Primary Goal:** Implement structure- and ATR-aware risk planning (entry zones, stops, TP1/TP2, RR gate $\ge 1.8$), a 4-mode Intrabar Ambiguity Resolver (with 1m/5m replay), and a strictly causal Entry Execution Model.

---

## 1. Risk Planning Engine (`engine/risk/planner.py`)

No `BUY_WINDOW` signal is valid without an explicit, objective risk architecture.

### A. Structure-Aware Stop Loss (`risk/stops.py`)
$$\text{Stop}_{\text{structure}} = \text{Support\_Level} - \text{Buffer}$$
$$\text{Stop}_{\text{ATR}} = \text{Entry\_Mid} - (k \times \text{ATR}_{14})$$
$$\text{Stop}_{\text{final}} = \min(\text{Stop}_{\text{structure}}, \text{Stop}_{\text{ATR}})$$
- **Guard:** Ensures the stop sits safely below confirmed structural invalidation while adhering to maximum allowable ATR risk.

### B. Take-Profit Targets (`risk/targets.py`)
- **TP1 (1R Target / Immediate Structure):** First meaningful structural resistance level ($\ge 1.0\text{R}$).
- **TP2 (Expansion Target):** Next major higher-timeframe resistance zone or $2.0 - 3.0\times$ ATR expansion.

### C. Reward-to-Risk (RR) Gate (A07)
$$\text{Reward-to-Risk} = \frac{\text{TP1} - \text{Entry\_Max}}{\text{Entry\_Max} - \text{Stop\_Loss}}$$
- **Hard Gate:** If $\text{RR} < \text{min\_rr}$ (Config default: **1.80**; preferred $\ge 2.0$) $\rightarrow$ **BUY_WINDOW prohibited $\rightarrow$ forced WAIT**.

---

## 2. Intrabar Ambiguity Resolver (`engine/risk/intrabar.py`)

### The Core Problem
When evaluating a historical candle (e.g. 1H or 15m) where:
$$\text{High} \ge \text{TP} \quad \text{AND} \quad \text{Low} \le \text{SL}$$
Standard backtesting engines naively assume TP was hit first, creating **fake profit**.

### 4 Intrabar Policies (R17, A14, A22)
```python
class IntrabarPolicy(Enum):
    LOWER_TIMEFRAME_REPLAY = "lower_timeframe_replay" # 1. Chronological 1m/5m replay
    CONSERVATIVE_SL_FIRST = "conservative_sl_first"   # 2. Assume SL hit first
    WORST_CASE = "worst_case"                         # 3. Assume maximum penalty
    SKIP_AMBIGUOUS = "skip_ambiguous"                 # 4. Exclude ambiguous setup
```

### 1m/5m Resolution Hierarchy
- **For 1H / 4H Candles:** Replay 15m candles first. If 15m is also ambiguous $\rightarrow$ drill down to 1m/5m.
- **For 15m Candles:** Replay 1m/5m resolution data.
- **Fallback:** If lower-timeframe data is unavailable $\rightarrow$ automatically fall back to **`CONSERVATIVE_SL_FIRST`**.

---

## 3. Causal Entry Execution Model (`engine/risk/execution.py`)

### The Look-Ahead Trap
A signal generated at the close of a 10:00–10:15 candle is knowable only **at 10:15:02**. A backtest cannot execute at the 10:15 close price.

### 3 Execution Policies (R20, A19, A25, A27)

```python
class EntryExecutionPolicy(Enum):
    NEXT_BAR_OPEN = "next_bar_open"               # Baseline: Open of first bar >= signal_ts + latency
    LIMIT_ZONE = "limit_zone"                     # Limit order: Fill only if market touches entry zone
    MARKET_AFTER_SIGNAL = "market_after_signal"   # Market order: First quote >= signal_ts + latency
```

### Algorithm & Timestamp Rules

```python
class EntryExecutionModel:
    def compute_fill(self, 
                     signal_generated_at: datetime,
                     repository: 'CandleRepository',
                     timeframe: str,
                     post_signal_quotes: list[QuoteData] | None,
                     policy: EntryExecutionPolicy,
                     config: EngineConfigData) -> FillResult:
        earliest_exec_ts = signal_generated_at + timedelta(seconds=config.entry_latency_seconds)

        match policy:
            case EntryExecutionPolicy.NEXT_BAR_OPEN:
                next_bar = repository.get_first_bar_open_after(earliest_exec_ts, timeframe)
                raw_fill = next_bar.open
                fill_ts = next_bar.timestamp_open # A27: Price and timestamp from the same bar

            case EntryExecutionPolicy.MARKET_AFTER_SIGNAL:
                eligible_quotes = [q for q in post_signal_quotes if q.timestamp >= earliest_exec_ts]
                first_quote = eligible_quotes[0]
                raw_fill = first_quote.ask        # A25: Uses actual ask quote
                fill_ts = first_quote.timestamp

        # Friction modeling
        spread_amount = raw_fill * (Decimal(str(config.entry_spread_pct)) / Decimal('100'))
        slippage_amount = raw_fill * (Decimal(str(config.entry_slippage_pct)) / Decimal('100'))
        final_price = raw_fill + spread_amount + slippage_amount

        return FillResult(final_price, fill_ts, policy, config.entry_latency_seconds, spread_amount, slippage_amount)
```

---

## 4. Phase 5 Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A07** | Risk/Reward Gate | Feasible setup with $RR < 1.8$ cannot emit `BUY_WINDOW` $\rightarrow$ forces `WAIT`. |
| **A14** | Intrabar Ambiguity Policy | Candle touching TP and SL resolves via lower TF replay or conservative `SL_FIRST`. |
| **A19** | No Same-Bar Fill | Backtest fill cannot occur at the close price of the signal-generating candle. |
| **A22** | 15m Ambiguity 1m/5m Replay | Ambiguous 15m bar uses 1m/5m resolution data to determine correct barrier hit. |
| **A25** | Post-Signal Fill Causality | `MARKET_AFTER_SIGNAL` uses first executable quote at $t \ge \text{signal\_ts} + \text{latency}$. |
| **A27** | Next-Bar Timestamp Causality | `NEXT_BAR_OPEN` fill price and fill timestamp strictly belong to the first bar open $\ge \text{earliest\_exec\_ts}$. |

---

## 5. Definition of Done Checklist

- [ ] `RiskEngine.plan()` computes structure/ATR stops, TP1, TP2, and valid entry zones.
- [ ] `IntrabarResolver` accurately replays 1m/5m candles and falls back to `SL_FIRST`.
- [ ] `EntryExecutionModel` implements `NEXT_BAR_OPEN` and `MARKET_AFTER_SIGNAL` causally.
- [ ] Acceptance tests **A07, A14, A19, A22, A25, A27** passing.
