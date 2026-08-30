# Phase 1: Ingestion Pipeline & Data Engine Specification

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟡 **MIGRATION REQUIRED (IMPLEMENTATION PENDING PHASE 1)**

---

## XAUUSD Migration Addendum

### 1. Target Scope & Architecture
- **Primary Signal Instrument:** `XAU/USD` (Canonical: `XAUUSD` Spot Gold denominated in USD).
- **Primary Spot Provider:** Implementation pending Phase 1 (`XAU-P1-01`).
- **Secondary Independent Spot Provider:** Implementation pending Phase 1 (`XAU-P1-02`).
- **USDT/USD Normalization Role:** Preserved strictly for historical XAUT baseline audit; **not** a primary dependency for future spot XAUUSD processing.
- **Provider Threshold Status:** Divergence thresholds, outlier boundaries, and failover parameters for XAUUSD spot feeds are **NOT FROZEN / REVALIDATION REQUIRED** based on empirical Phase 1 broker feed characteristics.

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Baseline Commit SHA:** `6bfb233e615ee48a819e1fb2a8de78367d97f8a9`  
> **Primary Goal:** Multi-source market data ingestion, resilient normalization ($XAUT_{USD} = XAUT_{USDT} \times USDTUSD$), temporal outlier quarantine, and point-in-time candle persistence with zero future look-ahead.

### 1. Data Model Topology (`apps/market_data/models.py`)

#### 3-Tier Hierarchy (Asset $\rightarrow$ Instrument $\rightarrow$ MarketListing)
```python
class Asset(models.Model):
    symbol = models.CharField(max_length=16, unique=True) # XAUT, USDT, USD, XAU, PAXG
    asset_type = models.CharField(max_length=16)          # CRYPTO, FIAT, COMMODITY

class InstrumentRole(models.TextChoices):
    PRIMARY_SIGNAL = "PRIMARY_SIGNAL", "Primary Signal Instrument (XAUT/USD)"
    GOLD_REFERENCE = "GOLD_REFERENCE", "Canonical Commodity Spot Reference (XAU/USD)"
    GOLD_CONFIRMATION = "GOLD_CONFIRMATION", "Tokenized Physical Proxy (PAXG/USD)"
    QUOTE_NORMALIZATION_PROXY = "QUOTE_NORMALIZATION_PROXY", "Stablecoin Proxy Normalization Rate (USDT/USDC)"
    MACRO = "MACRO", "Macro USD Filter (DXY / Yields)"

class Instrument(models.Model):
    base_asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="base_instruments")
    quote_asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="quote_instruments")
    instrument_type = models.CharField(max_length=16) # SPOT, FUTURES, INDEX
    role = models.CharField(max_length=32, choices=InstrumentRole.choices)

class MarketListing(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="listings")
    provider = models.CharField(max_length=32)        # binance, okx, gold_reference, usdt_usd
    provider_symbol = models.CharField(max_length=64) # XAUTUSDT, XAUT-USDT, XAUUSD, USDCUSDT
    status = models.CharField(max_length=16)          # ACTIVE, HALTED, DELISTED
    tick_size = models.DecimalField(max_digits=12, decimal_places=6)
    lot_size = models.DecimalField(max_digits=12, decimal_places=6)
    fallback_priority = models.IntegerField(default=0)
```

#### Temporal Provider Health Tracking
```python
class ProviderHealthSnapshot(models.Model):
    listing = models.ForeignKey(MarketListing, on_delete=models.CASCADE, related_name="health_snapshots")
    status = models.CharField(max_length=16) # HEALTHY, DEGRADED, UNHEALTHY, QUARANTINED, NOT_CONFIGURED
    checked_at = models.DateTimeField(db_index=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)
    reason = models.TextField(blank=True)
```

### 2. Multi-Provider Ingestion Architecture (`apps/market_data/`)

#### Abstract Provider Interface (`providers/base.py`)
```python
class MarketDataProvider(ABC):
    @abstractmethod
    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[RawCandle]: ...
    
    @abstractmethod
    def health_check(self) -> ProviderHealth: ...
    
    @property
    @abstractmethod
    def provider_id(self) -> str: ...
    
    def check_symbol_status(self, symbol: str) -> tuple[bool, str, dict]: ...
    def fetch_ticker(self, symbol: str) -> TickerSnapshot | None: return None
```

#### Adapters Implemented
1. **`BinanceProvider`** (`providers/binance.py`): Public klines endpoint `/api/v3/klines` for XAUTUSDT, exchangeInfo symbol status validation (`TRADING` vs `HALT`/`BREAK`).
2. **`OKXProvider`** (`providers/okx.py`): Public candles endpoint `/api/v5/market/candles` with strict `confirm == "1"` closed check, public instruments status check.
3. **`GoldReferenceProvider`** (`providers/gold_reference.py`): Strictly canonical spot XAU/USD gold reference. Reports `NOT_CONFIGURED` if no true commodity feed exists; raises error if proxy substitution is attempted.
4. **`PaxgConfirmationProvider`** (`providers/gold_reference.py`): Secondary tokenized physical gold proxy (`PAXG/USDT`) for `GOLD_CONFIRMATION` role only.
5. **`UsdtUsdRateProvider`** (`providers/usdt_usd.py`): Inverse Binance `USDCUSDT` rate proxy (`USDT_USDC_PROXY`). Never silently defaults to 1.0; supports historical Point-in-Time rate lookup.

### 3. Normalization & Market Integrity Engines

#### 1. Point-in-Time Quote Normalization (`normalization.py` - R19 / A21 / P1-03 / P1-04 / P1-09)
$$\text{Deviation} = |USDTUSD - 1.0|$$
- If deviation $\ge 2.0\%$: CRITICAL $\rightarrow$ Hard block `BUY_WINDOW`.
- If deviation $\ge 0.5\%$: WARNING $\rightarrow$ Penalize data quality score.
- **Point-in-Time Synchronized**: For candle at timestamp $T$, only rate observations with $T_{rate} \le T$ are permitted. Future rates are strictly forbidden.
- **Zero-Fallback Rule**: Missing or failed rate feed returns `None` and activates hard fail; **never defaults to 1.0**.
- Formula: $XAUT_{USD} = XAUT_{USDT} \times USDTUSD$.

#### 2. 5-Point Provider Transition Lifecycle (`integrity.py` - A20 / P1-06)
When primary provider fails over (e.g. Binance $\rightarrow$ OKX):
- Engine enforces **FORCE_WAIT** until ALL 5 criteria pass:
  1. Price basis difference $\le 0.30\%$.
  2. 3 consecutive closed candles are healthy.
  3. Bid-ask spread within normal envelope ($\le 0.15\%$).
  4. Zero bad ticks ($> 3\times$ ATR).
  5. Secondary reference consensus confirms level ($\le 0.35\%$ divergence).

#### 3. Outlier Quarantine & 2-Source Disagreement Policy (`integrity.py` - A15 / P1-05)
- **If $\ge 3$ sources**: Multi-source median filter quarantines any source deviating $> 0.50\%$.
- **If $== 2$ sources**: Divergence $> 0.50\%$ triggers `TWO_SOURCE_DISAGREEMENT` and `FORCE_WAIT`. No arbitrary single-source quarantine without consensus.

#### 4. XAUT/XAU Basis Gate (`integrity.py` - A17 / P1-08B)
- Divergence $> 3.0\%$ between normalized $XAUT_{USD}$ and spot $XAU_{USD}$ triggers hard fail.
- Missing canonical spot XAU/USD price triggers `GOLD_REFERENCE_UNAVAILABLE` and blocks `BUY_WINDOW`.

### 4. Timeframe Storage & Decoupled Repository

- **`MarketCandle`**: Strict UTC point-in-time OHLCV table with `close_usd`, `quote_rate`, and `is_closed`.
- **`DjangoCandleRepository`**: Implements pure `engine.core.interfaces.CandleRepository` Protocol. Strictly filters `is_closed=True` in `load_window()` (P1-02), preventing forming/open bars from leaking into indicators.

### 5. Acceptance & Targeted Verification Tests

| Test ID | Test Name | Assertion Criteria | Status |
|---|---|---|:---:|
| **A15** | Provider Outlier Quarantine | Outlier provider ($> 0.5\%$ from median) quarantined; excluded from analysis. | ✅ PASS |
| **A17** | XAUT/XAU Integrity Gate | Extreme basis spike or missing XAU price triggers hard gate. | ✅ PASS |
| **A20** | Provider Transition Continuity | 5-point transition lifecycle enforces `FORCE_WAIT` until secondary consensus confirms. | ✅ PASS |
| **A21** | Quote Currency Normalization | Formula $XAUT_{USD} = XAUT_{USDT} \times USDTUSD$ verified at math and ORM layers. | ✅ PASS |
| **P1-01** | OKX Closed-Candle Gate | OKX `confirm="0"` strictly marked `is_closed=False`. | ✅ PASS |
| **P1-02** | Open Candle Exclusion | Open/incomplete candles strictly excluded from `load_window()`. | ✅ PASS |
| **P1-03** | PIT Rate Alignment | XAUT at $T$ uses rate with $T_{rate} \le T$, never future rate. | ✅ PASS |
| **P1-04** | Stale USDT Rate Gate | Stale rate warns; critically stale rate (>24h) hard fails. | ✅ PASS |
| **P1-05** | 2-Source Disagreement | 2 diverging sources force wait; zero arbitrary quarantine. | ✅ PASS |
| **P1-06** | 5th Continuity Criterion | Secondary reference divergence rejects transition. | ✅ PASS |
| **P1-07** | Symbol Status Discovery | Provider symbol `HALT`/`BREAK` flags health `DEGRADED`. | ✅ PASS |
| **P1-08A** | PAXG Not Canonical XAU | PAXG registered strictly as `GOLD_CONFIRMATION` proxy. | ✅ PASS |
| **P1-08B** | Missing XAU Blocks BUY | Missing spot XAU/USD triggers `GOLD_REFERENCE_UNAVAILABLE`. | ✅ PASS |
| **P1-09A** | No Fallback to 1.0 | Missing USDT rate returns `None` and activates hard fail. | ✅ PASS |
| **P1-09B** | Historical Rate Requirement | Historical normalization requires historical rate series. | ✅ PASS |
| **INTEG** | End-to-End Ingestion | Provider fetch $\rightarrow$ normalize $\rightarrow$ validate $\rightarrow$ persist $\rightarrow$ repository load. | ✅ PASS |

### 6. Definition of Done Checklist

- [x] `Asset`, `Instrument`, `MarketListing` models created and seeded.
- [x] `ProviderHealthSnapshot` temporal tracking active.
- [x] Binance, OKX, XAU Reference, PAXG Proxy, and USDT/USD providers implemented.
- [x] `MarketIntegrityEngine` with 2-way USDT peg check and 5-point transition verifier active.
- [x] `DjangoCandleRepository` implemented fulfilling pure `CandleRepository` Protocol with closed-candle filtering.
- [x] Acceptance tests **A15, A17, A20, A21**, targeted tests **P1-01 to P1-09**, and end-to-end integration test passing (40/40 tests).
- [x] Historical backfill command (`backfill_candles`) functional for 15m/1H/4H/1D and 1m/5m.
- [x] Sealed with commit `6bfb233e615ee48a819e1fb2a8de78367d97f8a9`.
