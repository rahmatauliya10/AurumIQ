# Phase 1: Data Engine, Multi-Provider Abstraction & Market Integrity

> **Status:** ⏳ **READY TO START (PENDING REVIEW OF PHASE 0)**  
> **Primary Goal:** Build resilient multi-exchange data ingestion with 3-tier domain modeling, temporal provider health monitoring, two-way stablecoin peg validation, and a 5-point continuity verification lifecycle.

---

## 1. Domain Modeling (`apps/instruments/`)

### 3-Tier Architecture (Asset -> Instrument -> MarketListing)
Decouple the abstract economic asset/pair from exchange-specific listings:

```python
class Asset(models.Model):
    code = models.CharField(max_length=16, unique=True) # XAUT, XAU, USDT, USD, DXY
    name = models.CharField(max_length=128)
    asset_type = models.CharField(max_length=32)        # CRYPTO_TOKEN, COMMODITY, FIAT, INDEX

class InstrumentRole(models.TextChoices):
    EXECUTION = "EXECUTION", "Execution Target (XAUT/USDT)"
    GOLD_REFERENCE = "GOLD_REFERENCE", "Primary Gold Directional Reference (XAU/USD)"
    GOLD_CONFIRMATION = "GOLD_CONFIRMATION", "Secondary Confirmation (Gold Futures)"
    QUOTE_NORMALIZATION = "QUOTE_NORMALIZATION", "Stablecoin Normalization Rate (USDT/USD)"
    MACRO = "MACRO", "Macro USD Filter (DXY / Yields)"

class Instrument(models.Model):
    base_asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="base_instruments")
    quote_asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="quote_instruments")
    instrument_type = models.CharField(max_length=16) # SPOT, FUTURES, INDEX
    role = models.CharField(max_length=32, choices=InstrumentRole.choices)

    class Meta:
        unique_together = ("base_asset", "quote_asset", "instrument_type")

class MarketListing(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="listings")
    provider = models.CharField(max_length=32)        # binance, okx, kraken
    provider_symbol = models.CharField(max_length=64) # XAUTUSDT, XAUT-USDT
    status = models.CharField(max_length=16)          # ACTIVE, HALTED, DELISTED
    tick_size = models.DecimalField(max_digits=12, decimal_places=6)
    lot_size = models.DecimalField(max_digits=12, decimal_places=6)
    fallback_priority = models.IntegerField(default=0)

    class Meta:
        unique_together = ("instrument", "provider")
```

### Temporal Provider Health Tracking
```python
class ProviderHealthSnapshot(models.Model):
    listing = models.ForeignKey(MarketListing, on_delete=models.CASCADE, related_name="health_snapshots")
    status = models.CharField(max_length=16) # HEALTHY, DEGRADED, UNHEALTHY, QUARANTINED, UNKNOWN
    checked_at = models.DateTimeField(db_index=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)
    reason = models.TextField(blank=True)
```

---

## 2. Multi-Provider Ingestion Architecture (`apps/market_data/`)

### Abstract Provider Interface (`providers/base.py`)
```python
class MarketDataProvider(ABC):
    @abstractmethod
    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[RawCandle]: ...
    
    @abstractmethod
    def health_check(self) -> ProviderHealth: ...
    
    @property
    @abstractmethod
    def provider_id(self) -> str: ...
    
    # Stubs for live monitoring
    def fetch_ticker(self, symbol: str) -> TickerSnapshot | None: return None
    async def stream_ticker(self, symbol: str) -> AsyncIterator[TickerSnapshot]: raise NotImplementedError
```

### Adapters Implemented
1. `BinanceProvider` (`providers/binance.py`): Public klines endpoint for XAUT/USDT.
2. `OKXProvider` (`providers/okx.py`): Public candles endpoint for XAUT-USDT fallback.
3. `GoldReferenceProvider` (`providers/gold_reference.py`): Primary XAU/USD gold reference data.
4. `UsdtUsdRateProvider` (`providers/usdt_usd.py`): Real-time USDT/USD rate stream for quote normalization.

---

## 3. Market Integrity Engine (`apps/market_data/integrity.py`)

### 1. Two-Way Stablecoin Normalization (R19)
$$\text{Deviation} = |USDTUSD - 1.0|$$
- If deviation $\ge 2.0\%$: CRITICAL $\rightarrow$ Hard block `BUY_WINDOW`.
- If deviation $\ge 0.5\%$: WARNING $\rightarrow$ Penalize data quality score.
- Normalizes execution price to USD: $XAUT_{USD} = XAUT_{USDT} \times USDTUSD$.

### 2. 5-Point Provider Transition Lifecycle (A20)
When primary provider fails over (e.g. Binance $\rightarrow$ OKX):
- State moves to `TRANSITION` $\rightarrow$ `VERIFYING`.
- Engine enforces **FORCE_WAIT** until:
  1. Price difference $\le \text{allowed basis}$ (e.g. $\le 0.30\%$).
  2. 3 consecutive closed candles are healthy.
  3. Bid-ask spread within normal envelope.
  4. Zero bad ticks ($> 3\times$ ATR).
  5. Secondary reference consensus confirms level.
- On verification $\rightarrow$ `VERIFIED` (normal analysis resumes).

### 3. Outlier Quarantine Filter (A15)
Any source deviating $> 0.5\%$ from multi-source median is quarantined and logged to `QuarantineRecord`.

---

## 4. Timeframe Storage & Resolution Data Separation

| Timeframe | Storage Table | Schedule | Consumers |
|---|---|---|---|
| **1D, 4H, 1H, 15m** | `MarketCandle` (Primary) | Real-time at close | Analysis Engine, State Machine |
| **5m, 1m** | `MarketCandle` (Resolution) | Low-priority batch | Backtest Simulator, MFE/MAE, Intrabar Resolver |

> **R4 & Anti-Creep Rule:** 1m/5m data is NEVER fed into feature calculation or Direction/Timing scoring.

---

## 5. Phase 1 Acceptance Test Suite

| Test ID | Test Name | Assertion Criteria |
|---|---|---|
| **A15** | Provider Outlier Quarantine | Outlier provider ($> 0.5\%$ basis from median) is quarantined; excluded from analysis. |
| **A17** | XAUT/XAU Integrity Gate | Severe unnormalized basis spike blocks `BUY_WINDOW` and forces `WAIT`. |
| **A20** | Provider Transition Continuity | Provider failover flags `source_switch=True`; blocks synthetic breakouts during transition. |
| **A21** | Quote Currency Normalization | Basis calculation rigorously uses $XAUT_{USD} = XAUT_{USDT} \times USDTUSD$. |

---

## 6. Definition of Done Checklist

- [ ] `Asset`, `Instrument`, `MarketListing` models created and seeded.
- [ ] `ProviderHealthSnapshot` temporal tracking active.
- [ ] Binance, OKX, XAU Reference, and USDT/USD providers implemented.
- [ ] `MarketIntegrityEngine` with 2-way USDT peg check and 5-point transition verifier active.
- [ ] `DjangoCandleRepository` implemented fulfilling `CandleRepository` Protocol.
- [ ] Acceptance tests **A15, A17, A20, A21** passing.
- [ ] Historical backfill script (`scripts/backfill_candles.py`) functional for 15m/1H/4H/1D and 1m/5m.
