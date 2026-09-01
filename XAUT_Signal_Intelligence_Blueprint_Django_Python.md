# HISTORICAL XAUT ARCHITECTURE
# SUPERSEDED AS ACTIVE SPECIFICATION

> **Notice:** This document contains the historical XAUT Tether Gold architecture specification. It is **SUPERSEDED** as the active engineering authority by [`XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md`](./XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md). It is permanently retained for historical audit, migration provenance, and baseline regression integrity.

---

# XAUT SIGNAL INTELLIGENCE (HISTORICAL BASELINE)

## Full-Python Django Engineering Blueprint (Legacy v1.0)

Live Analysis, Time Cycle, Signal Scoring, Risk Planning, Backtesting and ML Meta-Filtering

| **PRIMARY PURPOSE** A build specification for an AI coding agent to implement a research-grade XAUT decision-support web application. V1 produces live BUY / WAIT / AVOID guidance and entry planning. It does not place orders. |
|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

Target stack: Python + Django + PostgreSQL + Redis + Celery + Plotly

Baseline framework: Django 5.2 LTS

Document status: Implementation Blueprint v1.0 \| 29 August 2026

Markdown edition: AI coding-agent handoff

| **SAFETY / SCOPE** This system is research and decision-support software. A signal is not a guarantee of profit. The project must be validated with point-in-time backtests, walk-forward testing and live paper observation before any real-money reliance. |
|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

# 0. How the AI Coding Agent Must Use This Document

The agent must treat this document as the governing implementation specification. Do not jump directly to dashboard polish, machine learning, or exchange order execution. Build the system in dependency order and prove each layer with tests before proceeding.

| **Rule**                       | **Mandatory behavior**                                                                             |
|--------------------------------|----------------------------------------------------------------------------------------------------|
| R1 - No trading execution      | No buy/sell/order/withdraw endpoint or exchange permission is allowed in V1-V2.                    |
| R2 - One engine                | Live analysis and backtesting must call the same pure-Python analysis engine.                      |
| R3 - Point-in-time correctness | At timestamp t, no feature, cycle estimate, swing, label or score may use information after t.     |
| R4 - Closed-candle decisions   | Primary decisions are produced on closed 15m/1H/4H/1D candles. Tick/live price is monitoring only. |
| R5 - Reproducibility           | Every saved signal stores engine version, config version, feature version and data timestamp.      |
| R6 - Abstention is valid       | The engine is allowed to return WAIT or AVOID most of the time.                                    |
| R7 - No 90% promise            | Optimize expectancy, profit factor, stability and drawdown - not cosmetic accuracy.                |
| R8 - Tests before features     | A phase is incomplete until its Definition of Done and automated tests pass.                       |

## Required implementation workflow

| SPEC -\> MODELS -\> PURE ENGINE -\> UNIT TESTS -\> INTEGRATION TESTS -\> BACKTEST -\> DASHBOARD -\> LIVE PAPER OBSERVATION -\> ML META FILTER |
|-----------------------------------------------------------------------------------------------------------------------------------------------|

The AI agent should make small, reviewable commits. Each commit should contain one coherent change, tests, and migration notes when applicable.

# 1. Product Definition

## 1.1 User question the application must answer

| **CORE QUESTION** For XAUT right now: is the market direction favorable, is the timing favorable, where is a reasonable entry zone, what invalidates the setup, and what is the expected risk/reward? |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## 1.2 Primary live outputs

| **Output**            | **Meaning**                                                            |
|-----------------------|------------------------------------------------------------------------|
| Direction Score 0-100 | Strength/quality of bullish directional evidence.                      |
| Timing Score 0-100    | Quality of the current entry window, including time-cycle alignment.   |
| Market Regime         | BULL_TREND, BEAR_TREND, RANGE, HIGH_VOLATILITY, TRANSITION, UNKNOWN.   |
| Signal State          | NO_TRADE, AVOID, WATCH, READY, BUY_WINDOW, INVALIDATED, COMPLETED.     |
| Entry Zone            | A price range, never a fake single perfect entry.                      |
| Invalidation / Stop   | Structure- and ATR-aware level that invalidates the setup.             |
| Targets               | TP1 / TP2 based on structure, ATR and minimum reward/risk.             |
| Reasons               | Explainable list of positive and negative contributors.                |
| Data Quality          | Whether source data is complete and fresh enough to permit a decision. |

## 1.3 Explicit non-goals for V1-V2

- Auto-buy or auto-sell

- Leverage or futures execution

- Withdrawal API

- LLM deciding the trade direction

- High-frequency or 1-minute scalping

- Prediction that promises a fixed win rate

- Kubernetes or unnecessary microservices

# 2. System Architecture

## 2.1 Logical architecture

```text
PUBLIC / READ-ONLY MARKET SOURCES
|
v
DATA INGESTION -> VALIDATION -> POSTGRESQL
| |
v v
FEATURE ENGINE HISTORICAL STORE
| |
v v
REGIME -> STRUCTURE -> TIME CYCLE -> BACKTEST
|
v
DIRECTION + TIMING -> SELECTIVE GATE -> RISK PLAN
|
v
SIGNAL SNAPSHOT -> DJANGO API / DASHBOARD / ALERTS
```

## 2.2 Deployment architecture

```text
Client Browser
|
Nginx
|
Gunicorn
|
Django ---------------- PostgreSQL
| |
Redis <---------------------+
|
Celery Workers
|-- market_data
|-- analysis
|-- backtest
|-- machine_learning
`-- maintenance
|
Celery Beat
```

## 2.3 Core design decision: Django is not the math engine

Trading mathematics must live in a framework-independent Python package named engine/. Django apps provide persistence, orchestration, authentication, API and presentation. The same engine package is imported by Celery live tasks and the historical backtester.

# 3. Recommended Technology Stack

| **Layer**          | **Selection**                               | **Reason**                                              |
|--------------------|---------------------------------------------|---------------------------------------------------------|
| Language           | Python 3.13                                 | Strong numerical, data and ML ecosystem.                |
| Web                | Django 5.2 LTS                              | Stable long-term Django baseline.                       |
| API                | Django REST Framework                       | Versioned JSON API and serialization.                   |
| Database           | PostgreSQL                                  | Reliable relational store for market/signal/audit data. |
| Cache / Broker     | Redis                                       | Fast cache and Celery broker for this scale.            |
| Async              | Celery + Celery Beat                        | Separated analysis/backtest queues and schedules.       |
| Analytics          | Pandas, NumPy, SciPy, Statsmodels           | Time-series and numerical processing.                   |
| Technical analysis | Custom indicators first; pandas-ta optional | Critical calculations remain testable and transparent.  |
| Time cycle         | SciPy, PyWavelets, custom causal transforms | ACF, FFT, wavelet and phase research.                   |
| ML                 | scikit-learn, XGBoost, LightGBM             | Meta-label filtering after rule engine validation.      |
| Optimization       | Optuna                                      | Walk-forward parameter search only on train/validation. |
| Charts             | Plotly Python                               | Interactive charts without a separate JS SPA.           |
| Testing            | pytest, pytest-django                       | Fast unit and integration test workflow.                |
| Packaging          | pyproject.toml                              | Pinned dependency groups and reproducibility.           |
| Container          | Docker / Compose                            | Development and single-server production deployment.    |

# 4. Repository and Module Layout

```text
xaut-intelligence/
manage.py
pyproject.toml
.env.example
config/
settings/{base,development,production,testing}.py
urls.py celery.py asgi.py wsgi.py
apps/
accounts/ instruments/ market_data/ indicators/
regimes/ market_structure/ cycles/ macro/ derivatives/
signals/ risk/ backtests/ machine_learning/ alerts/
dashboard/ audit/ system_health/
engine/
core/ indicators/ regime/ structure/ cycles/
signals/ risk/ ml/ backtesting/
tests/
unit/ integration/ backtest/ regression/
scripts/
templates/ static/ docker/
```

## 4.1 Dependency rule

```text
engine/* MUST NOT import Django models.
apps/* MAY import engine/*.
engine/* receives typed Python objects / DataFrames and returns typed result objects.
Persistence is performed by repositories/services in apps/*.
```

# 5. Domain Model and Database Blueprint

## 5.1 Core entities

| **Entity**          | **Purpose**                                  | **Key uniqueness / audit requirement**               |
|---------------------|----------------------------------------------|------------------------------------------------------|
| Instrument          | XAUT, XAU, DXY, yields, futures references   | symbol + venue + instrument_type                     |
| MarketSource        | Source metadata and status                   | source code unique                                   |
| MarketCandle        | OHLCV time-series                            | instrument + source + timeframe + timestamp_open     |
| DataQualitySnapshot | Quality score / anomalies                    | instrument + timeframe + timestamp                   |
| FeatureSnapshot     | Point-in-time engineered features            | instrument + timeframe + timestamp + feature_version |
| RegimeSnapshot      | Regime and confidence                        | timestamp + engine_version                           |
| StructureSnapshot   | Swings, BOS, zones                           | timestamp + engine_version                           |
| CycleSnapshot       | Session, swing age, dominant cycle and phase | timestamp + cycle_version                            |
| AnalysisSnapshot    | Combined direction/timing analysis           | timestamp + engine/config version                    |
| Signal              | Immutable signal decision                    | instrument + timeframe + candle + engine_version     |
| SignalComponent     | Explainable score contributor                | signal + component name                              |
| SignalOutcome       | Observed result after signal                 | one-to-one signal                                    |
| EngineConfig        | Versioned thresholds/weights                 | immutable semantic version                           |
| BacktestRun         | Inputs and aggregate result                  | run UUID                                             |
| BacktestTrade       | Individual simulated setup                   | run + signal time                                    |
| ModelVersion        | ML model registry                            | name + version                                       |
| AuditEvent          | Config/admin/system actions                  | append-only                                          |

## 5.2 MarketCandle fields

```text
id, instrument_id, source_id, timeframe
timestamp_open UTC, timestamp_close UTC
open, high, low, close, volume
is_closed, ingestion_time, source_sequence
data_quality_flag, created_at
```

Store market timestamps in UTC. Session calculations convert to Europe/London, America/New_York, Asia/Tokyo/Shanghai and Asia/Jakarta via zoneinfo. Never hard-code DST offsets.

## 5.3 Signal fields

```text
instrument_id, timeframe, candle_timestamp
state, direction_score, timing_score, rule_quality_score
market_regime, regime_confidence
entry_min, entry_max, stop_loss, tp1, tp2, risk_reward
ml_probability NULLABLE, data_quality_score, event_risk
engine_version, config_version, feature_version, model_version NULLABLE
created_at, invalidated_at, completed_at
```

## 5.4 Immutability

A generated Signal and its score components are append-only. If the engine or configuration changes, generate a new engine/config version. Never overwrite old signals to make historical performance look better.

# 6. Market Data Ingestion

## 6.1 Minimum V1 instruments

| **Instrument**                       | **Role**                                          |
|--------------------------------------|---------------------------------------------------|
| XAUT/USDT or XAUT/USD                | Execution/decision instrument.                    |
| XAU/USD or a reliable gold reference | Primary gold confirmation / fair-value reference. |
| Optional DXY                         | Macro USD filter.                                 |
| Optional US yield / real-yield proxy | Macro opportunity-cost filter.                    |

## 6.2 Timeframes

| **Timeframe** | **Purpose**                  |
|---------------|------------------------------|
| 1D            | Primary directional regime.  |
| 4H            | Trend and market structure.  |
| 1H            | Setup formation.             |
| 15m           | Entry timing / confirmation. |

## 6.3 Ingestion workflow

| fetch -\> normalize schema -\> UTC normalize -\> deduplicate -\> validate OHLC -\> mark closed -\> upsert candle -\> schedule downstream analysis |
|---------------------------------------------------------------------------------------------------------------------------------------------------|

## 6.4 Data quality gates

- Missing expected candle interval

- Duplicate timestamps

- OHLC logical violations (low \> high, open/close outside range)

- Zero/negative price

- Stale market source

- Large unexplained gap

- Unsupported partial candle accidentally marked closed

- Source disagreement beyond configured tolerance

| **HARD GATE** If data quality is below the configured minimum, the system must not emit BUY_WINDOW. It may retain the last analysis for display but must mark it stale. |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

# 7. Feature Engineering

## 7.1 Trend features

```text
EMA20, EMA50, EMA200
EMA slopes and normalized slopes
close_to_ema20_atr, close_to_ema50_atr, close_to_ema200_atr
EMA alignment flags and cross-state age
ADX and directional movement components
```

## 7.2 Momentum features

```text
RSI14, RSI delta 1/3/5 bars, RSI cross state
MACD line, signal, histogram, histogram delta
ROC and short-term normalized return
```

## 7.3 Volatility features

```text
ATR14, ATR percentage, ATR rolling percentile
realized volatility
Bollinger bandwidth
range expansion / contraction state
```

## 7.4 Volume features

```text
volume ratio vs rolling mean
volume z-score
volume trend
breakout-volume confirmation
```

## 7.5 Feature principle

Prefer state and change features over hundreds of correlated indicators. The target is approximately 20-50 meaningful features, with an explicit reason for each. Features used by ML must be generated by the same point-in-time pipeline used in live analysis.

# 8. Market Regime Engine

## 8.1 Output enum

| BULL_TREND \| BEAR_TREND \| RANGE \| HIGH_VOLATILITY \| TRANSITION \| UNKNOWN |
|-------------------------------------------------------------------------------|

## 8.2 V1 deterministic detector

Start with transparent rules using EMA alignment/slope, ADX, ATR percentile and structure. Each component returns a normalized score and an explanation. The regime confidence is not a probability; it is a quality score.

## 8.3 Example rule logic

```text
bull_points = 0
if ema50 > ema200: bull_points += ...
if ema50_slope > threshold: bull_points += ...
if ADX > trend_threshold: bull_points += ...
if structure in {HH_HL, BULL_BOS}: bull_points += ...

if volatility_percentile > high_vol_threshold:
regime = HIGH_VOLATILITY
elif bull_points >= bull_cutoff:
regime = BULL_TREND
...
```

## 8.4 V2 HMM research

Hidden Markov Model is an optional secondary regime classifier. It must never silently replace the deterministic engine. Compare rule-regime and HMM-regime performance through ablation tests and keep the more robust out-of-sample result.

# 9. Market Structure Engine

## 9.1 Required concepts

- Confirmed swing high / swing low

- Higher High (HH), Higher Low (HL), Lower High (LH), Lower Low (LL)

- Break of Structure (BOS)

- Structure invalidation

- Support/resistance zones

- Distance to zone normalized by ATR

## 9.2 Anti-look-ahead requirement

| **CRITICAL** A centered fractal that needs future candles may only become valid after those future candles have closed. The backtest must timestamp the confirmation at the time it actually became knowable, not at the historical swing candle. |
|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## 9.3 Preferred V1 approach

Implement causal confirmed swings or ATR-based ZigZag. Support/resistance must be zones rather than a fake exact price. Every structure event stores detected_at and source_swing timestamps.

# 10. Time Cycle Engine - Core Differentiator

Time Cycle is a timing and filter layer. It must never be treated as a deterministic prophecy that a reversal will happen at a particular clock time.

## 10.1 Subsystems

| **Subsystem**         | **Purpose**                                                       | **V1 priority**       |
|-----------------------|-------------------------------------------------------------------|-----------------------|
| Session Cycle         | Measure expectancy by trading session and regime                  | Highest               |
| Swing Duration        | Compare current correction/impulse age to historical distribution | Highest               |
| Autocorrelation (ACF) | Find recurring return lags                                        | Medium                |
| FFT / Spectrum        | Candidate dominant periodicity in detrended window                | Medium                |
| Wavelet               | Determine which periods are active now                            | High after base cycle |
| Hilbert / Phase       | Estimate current phase of a reliable cycle                        | Research              |
| Calendar Cycle        | Hour/day/month/quarter effects rolling through time               | Low/medium            |
| Event Cycle           | Time to/from CPI, NFP, FOMC etc.                                  | High risk-gate value  |

## 10.2 Session Cycle

```text
label each candle -> ASIA / LONDON_PREOPEN / LONDON / LONDON_NY_OVERLAP / NEW_YORK / US_LATE
aggregate by [session, regime, strategy_version]
metrics -> trades, win_rate, expectancy_R, profit_factor, avg_MFE, avg_MAE, false_breakout_rate
```

Session expectancy is learned from historical point-in-time signals, not assumed. Require a minimum sample count before using it as a positive factor.

## 10.3 Swing Duration Cycle

```text
for each confirmed impulse/correction:
store start, end, bars, hours, ATR-normalized amplitude, regime

current swing age -> historical percentile P10/P25/P50/P75/P90
example output: correction_age_percentile = 82
```

A mature correction can improve timing only when direction, support and momentum agree. Mature age alone does not trigger BUY.

## 10.4 ACF

```text
returns = log(close / close.shift(1))
rolling_window = configurable (e.g. 256/384/512 bars)
compute causal ACF -> candidate lags -> significance / stability
reject weak/noisy peaks
```

## 10.5 FFT / spectral analysis

```text
log price -> causal rolling window -> detrend -> normalize -> FFT -> power spectrum
output dominant_period, power_strength, signal_noise_ratio
repeat across multiple window lengths to estimate period stability
```

## 10.6 Wavelet

Use Continuous Wavelet Transform to measure time-localized energy by period. This answers whether a candidate 30-50 hour cycle is active now, rather than merely present somewhere in the full sample. The wavelet calculation must use only data available at timestamp t.

## 10.7 Phase estimation

Only estimate cycle phase if CycleReliability exceeds a configured threshold. If the dominant period is unstable, phase must be UNKNOWN. A phase score may support a setup but cannot override a bearish regime or broken structure.

## 10.8 Cycle reliability

```text
CycleReliability = f(
spectral_strength,
wavelet_strength,
period_stability,
cycles_observed,
amplitude_vs_noise,
regime_compatibility
)
```

## 10.9 Calendar cycle

Compute rolling hour-of-day, weekday, month and quarter effects. Do not hard-code seasonal beliefs. Store sample size, expectancy and stability across rolling subperiods. Low stability means zero/near-zero scoring contribution.

## 10.10 Event cycle / macro event gate

```text
features: minutes_to_event, minutes_after_event, event_type, importance, historical_volatility_multiplier
policy example: high-impact event within blackout window -> BUY_WINDOW prohibited -> WAIT
```

## 10.11 Time Cycle Score v1

| **Component**           | **Initial weight** | **Important guardrail**              |
|-------------------------|--------------------|--------------------------------------|
| Session expectancy      | 15                 | Minimum sample size required.        |
| Swing duration maturity | 20                 | Must align with direction/structure. |
| ACF                     | 10                 | Significance and rolling stability.  |
| FFT                     | 10                 | Detrended rolling window only.       |
| Wavelet                 | 20                 | Causal/current-window computation.   |
| Phase alignment         | 15                 | Only if cycle reliability is high.   |
| Calendar                | 5                  | Rolling stability required.          |
| Event timing            | 5                  | May act as hard risk gate.           |

These are starting weights only. They must be validated by walk-forward ablation and may be reduced to zero if they do not improve out-of-sample expectancy.

# 11. Direction Score and Timing Score

## 11.1 Why two scores

| **DESIGN PRINCIPLE** A market can be strongly bullish while the current entry timing is poor. The application must be able to say BULLISH - WAIT. |
|---------------------------------------------------------------------------------------------------------------------------------------------------|

## 11.2 Initial Direction Score

| **Component**             | **Initial weight** |
|---------------------------|--------------------|
| Regime quality            | 15                 |
| Daily trend               | 10                 |
| 4H trend                  | 10                 |
| Market structure          | 20                 |
| Pullback/breakout quality | 10                 |
| Momentum                  | 10                 |
| Volume                    | 5                  |
| XAU confirmation          | 10                 |
| Macro                     | 5                  |
| XAUT basis                | 5                  |

## 11.3 Timing Score

Timing combines entry proximity, 15m/1H confirmation and the Time Cycle Engine. Suggested components include distance to support/entry zone, momentum turn, volume response, session expectancy, swing-age maturity, reliable cycle phase and event risk.

## 11.4 Scores are not probabilities

A Direction Score of 90 is a normalized rule quality score, not a claim of 90% profit probability. If an ML model is later calibrated, display its probability separately.

# 12. Live Signal State Machine

```text
NO_TRADE -> AVOID -> WATCH -> READY -> BUY_WINDOW -> ACTIVE/PAPER -> COMPLETED
^ | |
| v v
+------ INVALIDATED <-+
```

## 12.1 Suggested transitions

| **From**   | **To**      | **Example conditions**                                                |
|------------|-------------|-----------------------------------------------------------------------|
| AVOID      | WATCH       | Direction above watch threshold and data healthy.                     |
| WATCH      | READY       | Entry zone near, regime/structure valid, no hard risk gate.           |
| READY      | BUY_WINDOW  | Direction and Timing pass thresholds, confirmation closed, RR passes. |
| READY      | INVALIDATED | Structure break or invalidation close.                                |
| BUY_WINDOW | INVALIDATED | Entry thesis broken before/after paper entry.                         |
| BUY_WINDOW | COMPLETED   | TP/SL/time barrier observed for paper outcome.                        |

## 12.2 Decision cadence

Full recalculation occurs at the close of relevant 15m/1H/4H/1D candles. Between closes, live price may trigger informational alerts such as ENTRY_ZONE_REACHED or INVALIDATION_TOUCHED, but it must not silently create a new confirmed BUY decision using incomplete candles.

# 13. XAU Confirmation and XAUT Basis

## 13.1 Gold confirmation

Treat a reliable gold reference as primary directional confirmation. Compare regime, structure, momentum and volatility of XAU with XAUT. Divergence penalizes the XAUT signal rather than being ignored.

## 13.2 Basis

| basis_pct = (xaut_price - gold_reference_price) / gold_reference_price \* 100 |
|-------------------------------------------------------------------------------|

Also compute rolling z-score and percentile. An unusually expensive XAUT premium can downgrade a bullish setup to WAIT. Thresholds must be learned/validated rather than hard-coded forever.

# 14. Macro and Derivatives Layers

## 14.1 Macro V1

Keep macro deliberately simple: DXY trend, yield/real-yield proxy and major event risk. Convert these to TAILWIND / NEUTRAL / HEADWIND. Macro should filter a technical setup, not become an opaque prediction engine.

## 14.2 Derivatives V2

If reliable XAUT derivatives data is available, add funding rate, open interest, OI delta, price/OI divergence and basis. Do not block V1 on derivatives availability.

# 15. Risk Planning Engine

## 15.1 Required output even without auto-trading

Every BUY_WINDOW must define entry zone, invalidation/stop, TP1, TP2 and reward/risk. Without these, signal quality cannot be objectively measured.

## 15.2 Stop logic

```text
structure_stop = below confirmed support / swing invalidation
atr_stop = entry_reference - k * ATR
final_stop = conservative structure-aware combination + configurable buffer
```

## 15.3 Take-profit logic

```text
TP1 -> first meaningful resistance or >= 1R
TP2 -> next structure target / ATR expansion target
minimum_RR_gate -> configurable, initial research target about 1.8; preferred >= 2.0
```

## 15.4 Setup rejection

A technically strong signal is still WAIT/AVOID if the feasible stop makes reward/risk unacceptable.

# 16. Signal Explainability

## 16.1 SignalComponent

Each signal stores component, raw value, normalized score, weight, weighted score and human-readable reason. The dashboard renders positive, neutral and negative reasons.

```text
Example:
Market Structure | HH-HL + bullish BOS | score 92 | weight 20 | +18.4
Time Cycle | session strong, mature correction, reliable trough phase | 85 | weight ...
XAUT Basis | premium percentile 94 | penalty -...
```

## 16.2 Required explanation format

```text
WHY THIS SETUP
+ 1D and 4H directional trend aligned
+ 4H higher-low structure remains valid
+ XAU confirms bullish direction
+ correction age is in favorable historical percentile
+ London/NY overlap has positive historical expectancy
- XAUT premium is elevated

FINAL: READY / BUY_WINDOW / WAIT
```

# 17. Backtesting Engine

## 17.1 Non-negotiable architecture

| **ONE ENGINE** The backtester feeds historical point-in-time MarketContext objects to the same engine.analyze() function used by live analysis. Do not create a simplified second set of trading rules for backtests. |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## 17.2 Backtest flow

```text
historical candles -> point-in-time features -> engine.analyze(t) -> candidate signal
-> simulated entry logic -> spread/fee/slippage -> TP/SL/time barrier -> trade outcome -> metrics
```

## 17.3 Costs

- Spread

- Exchange fee if applicable

- Slippage assumption

- Delay between signal and feasible manual entry

- Optional conservative price buffer

## 17.4 Time-series splits

```text
TRAIN -> VALIDATION -> TEST
then WALK FORWARD:
window 1: train A, validate B, test C
window 2: train B, validate C, test D
...
NO random shuffle
```

## 17.5 Purging and embargo

If labels look 24 hours into the future, remove overlapping samples around train/validation/test boundaries. The training set may not indirectly include information from the test label horizon.

## 17.6 Core metrics

| **Metric**                                | **Why it matters**                          |
|-------------------------------------------|---------------------------------------------|
| Trade count                               | Prevents conclusions from tiny samples.     |
| Win rate                                  | Descriptive but not sufficient.             |
| Average win/loss in R                     | Defines payoff asymmetry.                   |
| Expectancy                                | Average expected R per trade.               |
| Profit factor                             | Gross profit / gross loss.                  |
| Max drawdown                              | Practical downside profile.                 |
| Sharpe / Sortino                          | Risk-adjusted behavior.                     |
| MFE / MAE                                 | Entry/stop quality.                         |
| Consecutive losses                        | Operational tolerance.                      |
| Performance by regime/session/cycle phase | Determines where the system actually works. |

## 17.7 Ablation tests

```text
Baseline: direction/structure only
+ session cycle
+ swing-duration cycle
+ FFT/ACF
+ wavelet/phase
+ macro
+ XAUT basis
+ ML meta filter

Keep a layer only if it improves robust out-of-sample metrics, not only in-sample win rate.
```

# 18. Signal Outcome and Labeling

## 18.1 Triple barrier style label

```text
At signal time define:
upper barrier = TP / +R threshold
lower barrier = stop / -1R
time barrier = configured horizon

label = TP_FIRST | SL_FIRST | TIMEOUT
```

## 18.2 Outcome fields

```text
price_after_1h/4h/12h/24h
MFE, MAE
tp1_hit_at, tp2_hit_at, stop_hit_at
first_barrier_hit, realized_R, timeout_result
```

These outcomes power both performance analytics and the later ML meta-label dataset.

# 19. Machine Learning Meta-Filter - Only After Rules Work

## 19.1 Objective

ML does not invent BUY signals. The deterministic rule engine proposes a candidate. ML estimates whether this candidate should be accepted or rejected based on historical setup characteristics.

| RULE ENGINE -\> CANDIDATE -\> ML META MODEL -\> ACCEPT / REJECT -\> RISK GATE -\> FINAL |
|-----------------------------------------------------------------------------------------|

## 19.2 Model order

1.  Logistic Regression baseline.

2.  XGBoost candidate.

3.  LightGBM candidate.

4.  Keep the simplest model that wins robustly out of sample.

## 19.3 Example features

```text
regime, direction_score, trend slopes, EMA distances, RSI state, MACD delta,
ATR percentile, volume zscore, structure state, support distance,
session, swing_age_percentile, dominant_cycle_period, cycle_reliability, cycle_phase,
XAU trend/return, XAUT basis zscore, macro state, event distance
```

## 19.4 Probability calibration

If the model exposes probability, calibrate it using Platt scaling or isotonic calibration and measure Brier score/calibration curves. Display ML probability separately from rule-quality scores.

# 20. Celery and Scheduling Design

## 20.1 Queues

| market_data \| analysis \| backtest \| machine_learning \| maintenance |
|------------------------------------------------------------------------|

## 20.2 Core tasks

| **Task**                 | **Queue**        | **Trigger**                         |
|--------------------------|------------------|-------------------------------------|
| fetch_market_data        | market_data      | Scheduled / source cadence.         |
| validate_latest_candles  | market_data      | After ingestion.                    |
| analyze_closed_candle    | analysis         | After a candle is confirmed closed. |
| update_signal_outcomes   | analysis         | Scheduled after signal horizons.    |
| run_backtest             | backtest         | User/admin request.                 |
| build_walkforward_report | backtest         | After backtest batches.             |
| train_meta_model         | machine_learning | Explicit research action only.      |
| system_health_check      | maintenance      | Frequent schedule.                  |

## 20.3 Idempotency

Use unique database constraints and task-level idempotency keys. Re-running analysis for the same instrument/timeframe/candle/engine version must not create duplicate signals.

# 21. Django Service Layer

## 21.1 Required services

```text
apps/market_data/services.py -> ingestion orchestration
apps/signals/services.py -> build MarketContext, call engine, persist immutable result
apps/backtests/services.py -> create run, dispatch Celery job, persist report
apps/machine_learning/services.py -> dataset registry/model registry
apps/alerts/services.py -> format non-execution notifications
```

## 21.2 Repository abstraction

Engine input should be assembled by repositories/services. For example CandleRepository.load_window(instrument, timeframe, end_at, bars) returns data ending exactly at end_at. This makes point-in-time testing explicit.

# 22. API Blueprint

| **Method / Path**             | **Purpose**                                 |
|-------------------------------|---------------------------------------------|
| GET /api/v1/analysis/current/ | Current combined analysis.                  |
| GET /api/v1/signals/current/  | Current signal state and plan.              |
| GET /api/v1/signals/history/  | Immutable signal history.                   |
| GET /api/v1/cycles/current/   | Cycle components, reliability and phase.    |
| GET /api/v1/market/candles/   | Chart data with bounded range.              |
| GET /api/v1/backtests/        | Backtest runs and status.                   |
| POST /api/v1/backtests/       | Create research backtest job.               |
| GET /api/v1/system/health/    | Feed/worker/db health.                      |
| GET /api/v1/config/active/    | Read active immutable engine configuration. |

## 22.1 Example current signal JSON

```text
{
"symbol": "XAUTUSDT",
"state": "READY",
"direction_score": 88.0,
"timing_score": 76.0,
"regime": "BULL_TREND",
"entry_zone": {"min": null, "max": null},
"stop_loss": null,
"tp1": null,
"tp2": null,
"risk_reward": null,
"data_quality": 100,
"engine_version": "1.0.0",
"reasons": []
}
```

Null is preferable to invented data. If a plan cannot be computed safely, return WAIT/AVOID with an explanation.

# 23. Dashboard Blueprint

## 23.1 Navigation

| OVERVIEW \| LIVE ANALYSIS \| TIME CYCLE \| SIGNALS \| BACKTEST LAB \| MODEL LAB \| DATA QUALITY \| ENGINE SETTINGS \| SYSTEM HEALTH \| AUDIT LOG |
|--------------------------------------------------------------------------------------------------------------------------------------------------|

## 23.2 Overview

Show XAUT price, freshness timestamp, Direction Score, Timing Score, Regime, Signal State, Entry/Stop/TP plan, XAU confirmation, Time Cycle summary, event risk and top reasons. Never hide the last-analysis timestamp.

## 23.3 Time Cycle research page

- Dominant period and reliability

- Current phase with UNKNOWN state when unreliable

- Swing age percentile

- Session expectancy table by regime

- Wavelet power visualization

- Rolling cycle stability

- Performance by phase bucket

- Ablation: cycle-on versus cycle-off

## 23.4 Backtest Lab

Inputs: date range, engine/config version, instrument, timeframes, thresholds, minimum RR, allowed regimes and cost assumptions. Backtests run as Celery jobs and display progress/status rather than blocking an HTTP request.

# 24. Alerts - No Execution

## 24.1 Alert types

| WATCH_CREATED \| READY \| ENTRY_ZONE_REACHED \| BUY_WINDOW \| INVALIDATED \| DATA_STALE \| EVENT_RISK |
|-------------------------------------------------------------------------------------------------------|

## 24.2 Alert payload

Include timestamp, signal state, direction/timing scores, current price, entry zone if known, invalidation, targets, top reasons, last data timestamp and a reminder that action remains manual.

# 25. Security and Permissions

## 25.1 Exchange access policy

| **NO TRADING KEY** V1-V2 must use public/read-only market data. Do not store an API key with order or withdrawal permission. |
|------------------------------------------------------------------------------------------------------------------------------|

## 25.2 Application roles

| **Role** | **Allowed**                                                                 |
|----------|-----------------------------------------------------------------------------|
| Admin    | Manage sources/config versions, launch research jobs, user management.      |
| Analyst  | View analysis, run backtests/model experiments, cannot alter audit history. |
| Viewer   | Read-only dashboard/history.                                                |

## 25.3 Security baseline

- Django CSRF protection

- Secure cookies and HTTPS in production

- Environment-based secrets

- No secrets in Git

- Rate-limit externally exposed APIs if applicable

- Django admin restricted

- Append-only audit events for configuration changes

- Database backups and migration discipline

# 26. Observability and System Health

## 26.1 Health checks

| **Component**          | **Health condition**                             |
|------------------------|--------------------------------------------------|
| XAUT feed              | Recent closed candle available within tolerance. |
| XAU feed               | Fresh confirmation data.                         |
| PostgreSQL             | Connection and simple query pass.                |
| Redis                  | Ping and queue access pass.                      |
| Celery analysis worker | Heartbeat recent.                                |
| Celery backtest worker | Heartbeat recent.                                |
| Scheduler              | Last expected periodic task present.             |
| Signal pipeline        | Recent analysis completed successfully.          |

## 26.2 Structured logs

| event=signal_generated symbol=XAUTUSDT candle=... direction=88 timing=84 state=READY engine=1.0.0 config=... |
|--------------------------------------------------------------------------------------------------------------|

Do not rely on print statements. Correlate a live signal pipeline using a run/correlation UUID.

# 27. Testing Strategy

## 27.1 Unit tests

- EMA/ATR/RSI calculations against known fixtures

- Regime classification edge cases

- Causal swing detection

- Support/resistance zone construction

- Session labels across DST transitions

- Swing-duration percentile

- ACF / FFT no-signal behavior on noise

- Cycle reliability thresholds

- Signal state transitions

- Risk/reward calculations

## 27.2 Anti-look-ahead test - mandatory

```text
1. Run engine at historical T80 using candles <= T80.
2. Save result.
3. Dramatically modify candles T81..T100.
4. Run engine again at T80.
5. Result MUST be byte/field equivalent for all point-in-time fields.
```

## 27.3 Integration tests

- Ingestion -\> candle persistence -\> analysis task -\> signal persistence

- Duplicate task rerun produces one signal only

- Stale data causes hard gate

- Config version change produces a new auditable result

- API returns latest valid immutable snapshot

## 27.4 Regression fixtures

Maintain a frozen historical fixture. Refactors that materially alter backtest results require an explicit review rather than being treated as automatically better.

# 28. Coding Standards for the AI Agent

| **Area**     | **Rule**                                                                                                  |
|--------------|-----------------------------------------------------------------------------------------------------------|
| Typing       | Type all public engine functions and dataclasses.                                                         |
| Money/price  | Use Decimal at persistence/business boundaries; float/NumPy allowed inside controlled numerical routines. |
| Time         | Timezone-aware UTC timestamps only.                                                                       |
| Constants    | Thresholds in versioned configuration, not scattered magic numbers.                                       |
| Functions    | Pure calculation functions where possible.                                                                |
| Side effects | Keep network/database side effects outside engine math.                                                   |
| Exceptions   | Domain-specific exceptions; do not silently swallow data errors.                                          |
| Logging      | Structured, contextual logging.                                                                           |
| Migrations   | One coherent schema change per migration set.                                                             |
| Docs         | Docstrings explain assumptions, causality and units.                                                      |
| Tests        | Every bug fix adds a regression test.                                                                     |

# 29. Phased Implementation Plan and Definition of Done

## Phase 0 - Foundation

Build Django project, settings split, PostgreSQL, Redis, Celery, Docker Compose, authentication, logging and CI/test harness.

| **DONE WHEN** docker compose starts cleanly; Django migrations pass; Celery worker/beat connect; pytest smoke suite passes; production settings do not expose DEBUG or secrets. |
|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 1 - Data Engine

Implement Instrument, MarketSource, MarketCandle, ingestion adapters, historical importer, data quality validation and freshness health.

| **DONE WHEN** Historical XAUT and gold data can be imported without duplicates; UTC/timeframe consistency is proven; malformed and stale data are detected by tests. |
|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 2 - Core Indicators / Regime / Structure

Implement features, deterministic regime, causal swings, BOS and support/resistance zones.

| **DONE WHEN** All calculations have unit fixtures; anti-look-ahead structure tests pass; snapshots can be reproduced for any historical timestamp. |
|----------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 3 - Time Cycle Engine

Implement session cycle, swing duration, ACF, FFT, then wavelet and reliable phase estimation. Add cycle snapshots and research page.

| **DONE WHEN** Each cycle component can return UNKNOWN/LOW_RELIABILITY; DST tests pass; causal-cycle test passes; cycle-on/off ablation can be executed. |
|---------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 4 - Direction / Timing / State Machine

Implement versioned scoring, selective gate, WATCH/READY/BUY_WINDOW/INVALIDATED transitions and explanations.

| **DONE WHEN** A historical timestamp produces one deterministic explainable decision; duplicate analysis is idempotent; stale/event-risk gates prevent BUY_WINDOW. |
|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 5 - Risk Engine

Implement entry zone, structure/ATR invalidation, TP1/TP2 and RR gate.

| **DONE WHEN** No BUY_WINDOW persists without a valid risk plan; edge cases for zero ATR, insufficient structure and unacceptable RR return WAIT/AVOID safely. |
|---------------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 6 - Backtesting

Implement point-in-time replay, cost model, signal outcomes, metrics, walk-forward splits, purge/embargo and ablation reporting.

| **DONE WHEN** Live engine code is reused; anti-look-ahead regression passes; results report sample size, expectancy, PF, drawdown and regime/session/cycle breakdown. |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 7 - Dashboard / Alerts

Add server-rendered Django dashboard + Plotly and informational alerts.

| **DONE WHEN** User can see freshness, direction, timing, cycle, state, risk plan and reasons; alerts contain no order execution. |
|----------------------------------------------------------------------------------------------------------------------------------|

## Phase 8 - Live Paper Observation

Run production-like live analysis with no trades automatically placed. Record every signal and its later outcome.

| **DONE WHEN** Live decisions are stored immutably for a meaningful sample; live/paper behavior matches replay behavior within explainable data/cost differences. |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------|

## Phase 9 - ML Meta-Filter

Only now create point-in-time candidate dataset, baseline logistic model, then XGBoost/LightGBM and calibration.

| **DONE WHEN** Meta-filter improves walk-forward robustness beyond the deterministic baseline after costs; model registry and rollback are implemented. |
|--------------------------------------------------------------------------------------------------------------------------------------------------------|

# 30. Initial Configuration Blueprint

Values below are research defaults and must not be treated as proven profitable parameters. Store them in EngineConfig v0.x and tune only through defined backtests.

| **Config key**                 | **Initial research concept**                |
|--------------------------------|---------------------------------------------|
| analysis_timeframes            | 1D, 4H, 1H, 15m                             |
| direction_watch_threshold      | ~70 research starting point                 |
| direction_ready_threshold      | ~75 research starting point                 |
| timing_ready_threshold         | ~70 research starting point                 |
| buy_window_direction_threshold | ~80 research starting point                 |
| buy_window_timing_threshold    | ~80 research starting point                 |
| min_data_quality               | high / near-complete                        |
| min_rr                         | ~1.8 research starting point                |
| preferred_rr                   | \>= 2.0 research preference                 |
| event_blackout                 | configurable by event importance            |
| cycle_min_reliability          | must be high enough to permit phase scoring |
| stale_data_action              | force WAIT/NO_TRADE                         |

# 31. Core Python Interfaces

## 31.1 MarketContext

```text
@dataclass(frozen=True)
class MarketContext:
as_of: datetime
instrument: str
frames: dict[str, pd.DataFrame]
gold_frames: dict[str, pd.DataFrame]
macro: MacroContext | None
event_context: EventContext | None
config: EngineConfigData
```

## 31.2 AnalysisResult

```text
@dataclass(frozen=True)
class AnalysisResult:
as_of: datetime
regime: RegimeResult
structure: StructureResult
cycle: CycleResult
direction: ScoreResult
timing: ScoreResult
risk: RiskPlan | None
state: SignalState
reasons: tuple[Reason, ...]
diagnostics: dict[str, Any]
```

## 31.3 Top-level engine

```text
class XautSignalEngine:
def analyze(self, context: MarketContext) -> AnalysisResult:
validate_point_in_time(context)
features = self.feature_engine.compute(context)
regime = self.regime_engine.compute(features)
structure = self.structure_engine.compute(context, features)
cycle = self.cycle_engine.compute(context, features, regime, structure)
direction = self.direction_engine.score(...)
timing = self.timing_engine.score(...)
state = self.gate.decide(...)
risk = self.risk_engine.plan(...) if state permits else None
return AnalysisResult(...)
```

# 32. Celery Live Analysis Pseudocode

```text
@shared_task(queue="analysis")
def analyze_closed_candle(instrument_id, timeframe, candle_ts, engine_version):
key = make_idempotency_key(...)
if already_processed(key): return existing_id

context = build_point_in_time_context(end_at=candle_ts)
quality = evaluate_data_quality(context)
if quality.hard_fail:
result = no_trade_due_to_data(...)
else:
result = engine_registry.get(engine_version).analyze(context)

return persist_immutable_analysis_and_signal(result, quality, key)
```

# 33. Backtest Pseudocode

```text
for decision_time in historical_decision_times:
context = repository.build_context(end_at=decision_time)
result = engine.analyze(context)
if result.state == BUY_WINDOW:
trade = simulator.simulate(
result.risk,
future_market_data_visible_only_to_simulator,
fees=..., spread=..., slippage=...
)
recorder.add(trade)

report = metrics.compute(recorder.trades)
```

Future prices are available only to the execution/outcome simulator after the decision has been frozen. They are never visible to feature/scoring code.

# 34. Acceptance Tests the AI Agent Must Demonstrate

| **ID** | **Test**           | **Pass condition**                                                        |
|--------|--------------------|---------------------------------------------------------------------------|
| A01    | Future mutation    | Changing candles after T cannot change analysis at T.                     |
| A02    | DST session        | London/NY session labels remain correct around DST transitions.           |
| A03    | Duplicate analysis | Same idempotency key creates no duplicate Signal.                         |
| A04    | Stale data         | Stale XAUT/XAU data prevents BUY_WINDOW.                                  |
| A05    | Cycle unreliable   | Low reliability produces UNKNOWN phase and no positive phase bonus.       |
| A06    | Event risk         | High-impact blackout window downgrades otherwise valid BUY to WAIT.       |
| A07    | Risk gate          | RR below minimum prevents BUY_WINDOW.                                     |
| A08    | Immutable audit    | Old config/signal records are not rewritten after a new config activates. |
| A09    | Same engine        | Live and backtest import the same XautSignalEngine implementation.        |
| A10    | Ablation           | Cycle-on/off report can be generated for the same historical split.       |
| A11    | API freshness      | Current API exposes last analysis and market timestamp.                   |
| A12    | No execution       | Codebase contains no enabled exchange order execution path in V1-V2.      |

# 35. Master Prompt for the AI Coding Agent

| **COPY THIS INTO THE AGENT** Use the following specification as the governing engineering contract. Build phase by phase. Do not optimize for speed of feature count; optimize for correctness, causality, reproducibility, testing and auditability. |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

```text
You are the principal Python/Django engineer for the XAUT Signal Intelligence project.

GOAL
Build a full-Python Django decision-support system that analyzes XAUT live and produces NO_TRADE / AVOID / WATCH / READY / BUY_WINDOW signals. It must never place orders in V1-V2.

NON-NEGOTIABLES
1. Use Django 5.2 LTS baseline, PostgreSQL, Redis, Celery and pure-Python engine modules.
2. Keep engine/ independent from Django ORM. Live and backtest must call the same engine code.
3. Enforce point-in-time correctness. No future candle may leak into features, swing confirmation, cycle estimation, labels or scores.
4. Primary decisions use closed 15m/1H/4H/1D candles. Live ticks may only monitor entry/invalidation zones.
5. Store immutable Signal, SignalComponent, EngineConfig/version and audit data.
6. Build Data Quality hard gates before signal generation.
7. Build deterministic Regime, Structure, Direction and Timing engines before ML.
8. Implement Time Cycle in stages: Session -> Swing Duration -> ACF -> FFT -> Wavelet -> reliable phase -> calendar/event context. Any unreliable cycle must return UNKNOWN rather than fabricate precision.
9. Every BUY_WINDOW requires a valid entry zone, invalidation/stop, TP1/TP2 and minimum reward/risk.
10. Backtesting must include fees/spread/slippage assumptions, walk-forward splits, purge/embargo where labels overlap, and ablation tests.
11. Optimize for positive expectancy, profit factor, drawdown and robustness - not a promised 90% win rate.
12. Add ML only as a meta-filter after deterministic candidates work out of sample.
13. No exchange key with trading/withdrawal permission. No order execution function in V1-V2.

WORKING METHOD
- Implement one phase at a time.
- Before coding each phase, list models/modules/interfaces/tests to be created.
- Write unit tests for mathematical behavior and causality.
- Run tests and show evidence before declaring the phase complete.
- Keep migrations small and auditable.
- Prefer typed dataclasses, pure functions and explicit configuration.
- Never silently catch data-quality or numerical errors.
- If an assumption is not specified, choose the safest research default and document it.

START WITH PHASE 0 ONLY. Do not start ML, fancy UI or order execution.
```

# 36. Final Engineering Principles

- Accuracy alone is not the objective. Positive expectancy and robust risk-adjusted behavior are the objective.

- Time Cycle is a timing/abstention layer. It must prove its value through ablation tests.

- A good engine is allowed to produce very few BUY signals.

- Unknown is a valid output. Fabricated confidence is not.

- Research results must survive point-in-time replay and walk-forward testing.

- Live price is for monitoring; confirmed decisions require the designed candle-close logic.

- Every signal must be explainable, versioned and reproducible.

- Auto-trading is intentionally outside V1-V2.

| **IMPLEMENTATION TARGET** At the end of the initial build, a user can open the Django web app and see whether XAUT is currently AVOID / WATCH / READY / BUY_WINDOW, why, how strong the direction is, whether timing/time-cycle is favorable, where the setup is invalidated, and how the same logic performed historically - without the application being able to place a trade. |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
