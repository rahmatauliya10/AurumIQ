# Phase 1: Ingestion Pipeline & Data Engine Specification

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟡 `MIGRATION REQUIRED (IMPLEMENTATION PENDING PHASE 1)`  
> **Primary Goal:** Specify multi-provider market data ingestion, point-in-time candle validation, outlier quarantine, and provider health lifecycle management for the target `XAUUSD` instrument while preserving historical `XAUT` baseline data.

---

## 1. Provider Topology: Historical Baseline vs Target XAUUSD

### A. Historical XAUT Baseline (Verified & Preserved)
- **Primary Feeds:** Binance XAUT/USDT, OKX XAUT/USDT.
- **Normalization:** Normalized using Tether USD conversion rates ($XAUT_{USD} = XAUT_{USDT} \times USDTUSD$).
- **Status:** Retained permanently as `LEGACY` audit baseline.

### B. Target XAUUSD Provider Architecture (Pending Implementation)
- **Primary Spot Gold Feed:** Direct institutional spot `XAU/USD` quote feed (USD denominated).
- **Secondary Independent Feed:** Secondary institutional `XAU/USD` quote feed for cross-validation.
- **Optional Context Feeds:** Gold Futures (GC), US Dollar Index (DXY), US 10Y Yields, crypto gold references (XAUT/PAXG).
- **USDT/USD Role:** Preserved strictly for historical XAUT normalization; **not** a primary dependency for future spot XAUUSD processing.

```text
TARGET XAUUSD INGESTION TOPOLOGY (CONCEPTUAL SPECIFICATION)

┌────────────────────────────┐      ┌────────────────────────────┐
│ PRIMARY XAUUSD PROVIDER    │      │ SECONDARY XAUUSD PROVIDER  │
│ (Implementation Pending)   │      │ (Implementation Pending)   │
└─────────────┬──────────────┘      └─────────────┬──────────────┘
              │                                   │
              ▼                                   ▼
┌────────────────────────────────────────────────────────────────┐
│               POINT-IN-TIME INGESTION NORMALIZER               │
│  - Timeframe Resampling & Candle Timestamp Alignment           │
│  - Causal Closed-Bar Validation (timestamp_close <= as_of)     │
│  - Divergence & Outlier Quarantine (Thresholds TBD)            │
│  - 5-Point Provider Transition Lifecycle (A20)                 │
└────────────────────────────────┬───────────────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│                 IMMUTABLE REPOSITORY STORE                     │
│  - CandleRepository Protocol (PostgreSQL / In-Memory Mock)     │
│  - Append-Only Closed Candles (15m, 1H, 4H, 1D)                │
└────────────────────────────────────────────────────────────────┘
```

---

## 2. Ingestion & Quality Control Invariants

1. **Closed Candle Causality (R3):** Ingestion pipelines process strictly closed candles. Unclosed candles are rejected from the core decision pipeline.
2. **Provider Health & Failover Lifecycle (A20):**
   - Failover between providers requires verification of: 1) Price convergence, 2) Three consecutive healthy closed bars, 3) Normal spread conditions, 4) Zero bad ticks ($> 3\times$ ATR), and 5) Secondary reference consensus.
3. **Threshold Calibration Status:**
   - *Legacy XAUT Baseline Thresholds:* Divergence $\ge 0.50\%$, spread $\le 0.15\%$, USDT deviation $\ge 2.0\%$.
   - *Target XAUUSD Thresholds:* **NOT FROZEN / REVALIDATION REQUIRED** based on empirical Phase 1 broker feed characteristics.

---

## 3. Definition of Done Checklist

### Historical Baseline
- [x] Multi-provider protocol interface (`MarketDataProvider`) implemented.
- [x] Historical XAUT ingestion from Binance and OKX with USDT rate normalization verified.
- [x] Ingestion pipeline integration tests passing (`A15`, `A20`, `A21`).

### Target XAUUSD Scope (Pending Phase 1 Code Implementation)
- [ ] Implement primary direct `XAU/USD` spot provider adapter.
- [ ] Implement secondary independent `XAU/USD` spot provider adapter.
- [ ] Calibrate and freeze XAUUSD provider divergence and spread thresholds.
- [ ] Verify multi-provider outlier quarantine against live XAUUSD feeds.
