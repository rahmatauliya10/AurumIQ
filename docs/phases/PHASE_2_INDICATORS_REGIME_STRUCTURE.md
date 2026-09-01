# Phase 2: Indicators, Market Regimes & Structural Analysis

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟡 **CORE ARCHITECTURE IMPLEMENTED (XAUUSD EMPIRICAL THRESHOLDS NOT FROZEN)**

---

## XAUUSD Migration Addendum

### 1. Spot Gold Volume Semantics & Evidence Types (`XAU-P2-01`)
Because spot gold trades over-the-counter (OTC) without a single centralized exchange, the engine explicitly distinguishes between volume representations:

```text
VolumeEvidenceType:
  - REAL_VOLUME    : Authenticated exchange-matched volume
  - TICK_VOLUME    : Broker tick frequency / quote update count
  - PROXY_VOLUME   : Regulated Gold Futures (GC) volume proxy
  - UNAVAILABLE    : Missing, unprovided, or malformed volume data
```

#### Volume Integrity Invariants
1. **Zero Fabrication:** The engine never fabricates, estimates, or synthesizes missing volume.
2. **Fail-Neutral Contribution:** If volume is `UNAVAILABLE` or invalid, its positive contribution to Direction and Timing scores is strictly `0.0` (`usable=False`, `ratio=None`, `zscore=None`).
3. **No Incompatible Mixing:** A rolling volume feature window must not combine differing semantics (e.g. TICK_VOLUME + REAL_VOLUME or PROXY_VOLUME); mixed windows return `is_usable=False` and `reason="MIXED_VOLUME_SEMANTICS"`.
4. **Proxy Volume Rules:** Regulated Gold Futures (GC) volume may only be utilized as an explicitly labeled proxy (`PROXY_VOLUME`) when reliable, point-in-time historical data exists. It does not overwrite spot volume or alter spot candle series.

### 2. Threshold Calibration Status & Profile Segregation
All numerical regime boundaries (ADX 20/25, ATR% 3.0%, Realized Volatility 5.0%, Bollinger Bandwidth 15.0%) documented in the historical specification represent **LEGACY BASELINE / DEFAULT REFERENCE VALUES ONLY**.

For the target XAUUSD instrument:
- **XAUUSD Empirical Regime Thresholds:** `NOT_CONFIGURED / NOT_FROZEN / REVALIDATION_REQUIRED`
- **Fail-Neutral Default:** Uncalibrated XAUUSD classifications evaluate to `RegimeType.UNKNOWN` with `details={"reason": "CALIBRATION_REQUIRED"}`.
- **Architectural Segregation:** Pure-Python `RegimeThresholdProfile` segregates mathematical indicator configuration from empirical instrument calibrations without Django coupling.


---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Baseline Commit SHA:** `8345c2be6cb2a781b0f55cf5fe6e9bcbbfe5e4fc`  
> **Primary Goal:** Construct pure-Python indicators, transparent regime classification, causal market structure (swings, BOS, zones), and statistical sample guards with zero look-ahead bias.

### 1. Feature Engineering Subsystems (`engine/features/`)

All calculations operate strictly on causal sequences ending at timestamp $T$. Zero future lookahead.

#### A. Trend Subsystem (`engine/features/trend.py`)
- **EMAs:** EMA20, EMA50, EMA200 (calculated via recursive smoothing $\alpha = \frac{2}{N+1}$ with initial SMA seed).
- **Normalized Slopes:** $\text{Slope}_{EMA} = \frac{EMA_t - EMA_{t-k}}{EMA_{t-k}} \times \frac{100}{k}$.
- **Alignment Flags:** Bullish stack ($+1$ for $\text{EMA}_{20} > \text{EMA}_{50} > \text{EMA}_{200}$), Bearish stack ($-1$), Mixed ($0$).
- **ADX & Directional Movement:** 14-period Wilder smoothed $+DI, -DI, ADX$.

#### B. Momentum Subsystem (`engine/features/momentum.py`)
- **RSI14:** Standard Wilder's smoothed Relative Strength Index.
- **MACD:** Fast EMA12, Slow EMA26, Signal EMA9, Histogram delta.
- **Rate of Change (ROC):** Normalized $k$-bar percentage momentum: $\frac{C_t - C_{t-k}}{C_{t-k}} \times 100$.

#### C. Volatility Subsystem (`engine/features/volatility.py`)
- **ATR14:** True Range rolling Wilder average.
- **ATR %:** $\frac{\text{ATR}_{14}}{\text{Close}} \times 100$.
- **Bollinger Bands:** 20-period SMA $\pm 2\sigma$, Bandwidth percentage.
- **Realized Volatility:** Rolling population standard deviation ($ddof=0$) of 20-period log returns in percentage points (%).

#### D. Volume Subsystem (`engine/features/volume.py`)
- **Volume Ratio:** $\frac{\text{Volume}_t}{\text{SMA}_{20}(\text{Volume})}$.
- **Volume Z-Score:** Standardized volume: $\frac{\text{Volume}_t - \mu}{\sigma}$.

### 2. Deterministic Regime Engine (`engine/regime/`)

Transparent, rule-based classification into 6 explicit states:
$$\text{MarketRegime} \in \{\text{BULL\_TREND}, \text{BEAR\_TREND}, \text{RANGE}, \text{HIGH\_VOLATILITY}, \text{TRANSITION}, \text{UNKNOWN}\}$$

- **`UNKNOWN`:** Insufficient lookback (< 200 bars).
- **`HIGH_VOLATILITY`:** Realized Volatility $> 5.0\%$, $\text{ATR}\% > 3.0\%$, or $\text{BB Bandwidth} > 15.0\%$.
- **`BULL_TREND`:** EMA20 > EMA50 > EMA200, slope $> 0$, ADX $\ge 20$, RSI $\ge 50$.
- **`BEAR_TREND`:** EMA20 < EMA50 < EMA200, slope $< 0$, ADX $\ge 20$, RSI $< 50$.
- **`RANGE`:** ADX $< 20$, $|\text{slope}| < 0.05$.
- **`TRANSITION`:** Mixed alignment, conflicting momentum/trend signals, or EMA crossover in progress.

### 3. Causal Market Structure Engine (`engine/structure/`)

#### Causal Swings (`engine/structure/causal_swings.py`)
- **Causality Invariant:** A swing high/low at candle $i$ requiring $L=3, R=3$ bars is knowable **strictly at timestamp $i+R$ close**.
- Every swing stores:
  - `timestamp`: When the peak/trough actually occurred ($i$).
  - `detected_at`: When the swing became causally confirmed ($i+R$ candle close).
  - Future candles beyond $T$ have zero effect on confirmed swings up to $T$.

#### Hierarchy & Break of Structure (BOS) (`engine/structure/engine.py`)
- Higher High (HH), Higher Low (HL), Lower High (LH), Lower Low (LL).
- **Bullish BOS:** Closed candle close breaks above the most recent confirmed swing high.
- **Bearish BOS:** Closed candle close breaks below the most recent confirmed swing low.

#### ATR-Aware Support / Resistance Zones (`engine/structure/zones.py`)
- Support and resistance are constructed as **price zones** ($\text{Level} \pm 0.5 \times ATR$), never arbitrary single-dollar points.

### 4. Statistical Sample Guard with Normalized HHI (`engine/guards/sample_guard.py`)

No pattern with a small sample size may inject false confidence into the system.

#### Normalized Herfindahl-Hirschman Index (HHI) & Autocorrelation Discount
For $k$ unique observed regimes with share $s_i = \frac{n_i}{N}$:
$$HHI = \sum_{i=1}^k s_i^2, \quad HHI_{\min} = \frac{1}{k}$$
$$HHI_{\text{norm}} = \frac{HHI - HHI_{\min}}{1.0 - HHI_{\min}} \quad (\text{for } k > 1)$$

$$n_{eff} = n \times (1 - \text{HHI\_discount}) \times (1 - \text{clustering\_discount})$$

#### Tiered Confidence Table (R16 & A16)
| Effective $n_{eff}$ | Confidence Level | Weight Multiplier | Action |
|---|---|---|---|
| $< 30$ | `INSUFFICIENT` | **0.0** | Positive score contribution completely blocked (`is_blocked = True`) |
| $30 - 59$ | `LOW` | **0.5** | Heavily discounted |
| $60 - 99$ | `MEDIUM` | **0.8** | Moderate confidence |
| $\ge 100$ | `HIGH` | **1.0** | Full confidence weight allowed |

### 5. Django Persistence Service Bridge (`apps/analysis/`)

- Models: `FeatureSnapshotRecord`, `RegimeSnapshotRecord`, `StructureSnapshotRecord` (each tracking `feature_version`).
- Service: `AnalysisPersistenceService` translates pure engine dataclasses into database records.
- Boundary: `engine/*` contains **zero Django imports** (verified via AST audit).

### 6. Phase 2 Acceptance & Targeted Test Suite

| Test ID | Test Name | Assertion Criteria | Status |
|---|---|---|:---:|
| **A01** | Future Mutation Invariance | Modifying/spiking future candles at $t > T$ produces 100% byte-identical analysis result at $T$. | ✅ PASS |
| **A16** | Minimum Sample Guard Gate | Any setup with $n < 30$ or $n_{eff} < 30$ receives `weight_multiplier = 0.0` and `is_blocked = True`. | ✅ PASS |
| **P2-01** | Swing Confirmation Causality | Candidate swing at $i$ ($L=3, R=3$) is completely unavailable at $i+2$; becomes confirmed strictly when $i+3$ closes with `detected_at = bar[i+3].timestamp_close`. | ✅ PASS |
| **P2-02** | BOS Cannot Precede Structure | Break of structure cannot trigger off unconfirmed candidate swings. | ✅ PASS |
| **P2-03** | Regime Volatility Boundary Units | RV 4.99% vs 5.01%, ATR% 2.99% vs 3.01%, BB 14.99% vs 15.01% boundary tests pass. | ✅ PASS |
| **P2-04** | Normalized EMA Slope Scale Invariant | Prices scaled by 100x produce identical percentage slope values ($< 10^{-7}$). | ✅ PASS |
| **P2-05** | Effective-N Overlap/Cluster Collapse | 100 overlapping signals in single regime collapse to $n_{eff} < 10 \ll 100$. | ✅ PASS |
| **P2-06** | Effective-N Independent Sample | 100 independent signals across balanced regimes retain $n_{eff} = 100$. | ✅ PASS |
| **P2-07** | Flat-Price Indicator Edge Cases | Flat prices yield ATR=0, BB bandwidth=0, RV=0, RSI=50 without division by zero. | ✅ PASS |
| **P2-08** | Numerical Fixture Parity | Exact numerical parity against standard mathematical fixtures for EMA, RSI, MACD, ATR, ADX, BB. | ✅ PASS |
| **P2-09** | Confirmation Timestamp Causality | 15m candle (10:00-10:15): swing unavailable at 10:14:59, available at 10:15:00 with `detected_at = 10:15:00`. | ✅ PASS |
| **P2-10** | BOS Event Timestamp Causality | Intra-bar breach at 10:06/10:10 yields BOS NONE; candle close at 10:15 confirms Bullish BOS at 10:15:00. | ✅ PASS |
| **P2-11** | Realized Volatility Definition | Raw rolling % population std dev (ddof=0) of 20-period log returns verified with manual step-by-step mathematical fixture. | ✅ PASS |
| **P2-12** | Exact Indicator Parity Fixtures | Step-by-step exact numerical fixtures for RSI14 (96.30%), MACD(12,26,9), and ADX14 (100.0%, +DI 20.0%). | ✅ PASS |
| **P2-13** | Realized Volatility DDof Semantics | Population std dev (ddof=0, denominator 20) vs sample std dev (ddof=1, denominator 19) distinguished and verified explicitly. | ✅ PASS |
| **PURITY** | AST Engine Purity Audit | All modules in `engine/` contain 0 Django imports. | ✅ PASS |
| **PERSIST** | Analysis Persistence Bridge | Pure engine dataclasses persist cleanly to ORM models with `feature_version`. | ✅ PASS |

### 7. Definition of Done Checklist

- [x] All indicators match manually verified mathematical fixtures (P2-08, P2-11, P2-12, P2-13).
- [x] `RegimeEngine` transparently computes regime with verified boundary units (P2-03, P2-04).
- [x] `CausalStructureEngine` constructs causal swings, BOS events, and ATR-normalized zones with strict timing gates and candle-close timestamp causality (P2-01, P2-02, P2-09, P2-10).
- [x] `EffectiveSampleEstimator` correctly decomposes overlap, temporal clustering, and normalized HHI (P2-05, P2-06).
- [x] Acceptance tests **A01 and A16** and targeted tests **P2-01 to P2-13** passing.
- [x] Engine AST purity verified (zero Django imports in `engine/`).
- [x] All **73/73 tests passing** in Docker test suite (`docker compose -f docker/docker-compose.yml exec web pytest -v`).
