# Phase 6: Backtesting Lab & Walk-Forward Ablation

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟡 `XAUUSD PIT BACKTEST REQUIRED`  
> **Primary Goal:** Provide point-in-time historical backtesting, walk-forward time-series splitting with label purging and embargo, realistic trade friction simulation, and automated component ablation testing for XAUUSD.

---

## 1. Non-Negotiable Core Principle: One Engine Rule (R2 & A09)

The backtesting engine must NEVER maintain a simplified secondary set of trading rules. Live analysis and backtesting must resolve the **exact same pure-Python `SignalEngine` class, version, configuration, and feature set**.

```text
HISTORICAL STORE (XAUUSD) ──► Build Point-in-Time MarketContext(t) ──► SignalEngine.analyze(context)
                                                                               │
                                                                               ▼
METRICS & ABLATION ◄── Record Trade ◄── Simulate Execution ◄── BUY_WINDOW / SELL_WINDOW
```

---

## 2. Automated Component Ablation (`engine/backtest/ablation.py` & A10)

Quantifies the exact marginal contribution of each engine layer on out-of-sample data without mutating production configuration:
1. **Layer 0 (Baseline):** Core Trend & Structural Direction only.
2. **Layer 1:** + Phase 3A Session Expectancy.
3. **Layer 2:** + Phase 3A Swing Duration Maturity.
4. **Layer 3:** + Phase 3A Macro Event Blackout Gate.
5. **Layer 4 (Candidate):** + Macro Cross-Market Indicators (DXY, Yields, Gold Futures).
6. **Layer 5 (Experimental):** + Phase 3B Spectral Cycles (Evaluated against promotion gate).

---

## 3. Definition of Done Checklist

### Historical Baseline
- [x] Walk-forward split generator with purging and embargo verified.
- [x] Trade simulator with realistic spreads, fees, and slippage implemented.
- [x] Automated component ablation framework verified (`A10`).
- [x] Asynchronous Celery backtest job execution verified (`apps/backtests/`).

### Target XAUUSD Scope
- [ ] Run automated ablation tests across all candidate features on historical XAUUSD data.
- [ ] Finalize production feature set and scoring weights based on ablation evidence.
