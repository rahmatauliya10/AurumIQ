# AurumIQ — XAUUSD Empirical Friction Evidence & Calibration Architecture

> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)
> **Active Target Venue:** `EXNESS`
> **Last Verified Baseline SHA:** `2ee19143aa98ccf15f25b1dec0ec4c2f2fc0bfc0` (PR #22 Documentation Truth Sealed & Merged; Post-Merge CI Green)
> **Historical Baseline Main SHA:** `57f6de1405d0df8548182a166d245f1a3173363d` (Phase 7 Baseline Provenance)
> **Friction Status:** `EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED`
> **Hard Readiness Gate:** `READINESS_GATE = CANDLES_READY_EMPIRICAL_FRICTION_MISSING` (`passed = False`, `is_production_authorized = False`, `production_weight = 0.0`, `decision = WAIT`)
> **Governing Specifications:** PR #20 (Empirical Friction Provenance Seal @ `92b0bd6`), PR #21 (Isolated Standard Cent Scope @ `fcbe1a9`)
> **Governing Report & Artifact:** [`XAUUSD_EMPIRICAL_FRICTION_EVIDENCE_REPORT.md`](./XAUUSD_EMPIRICAL_FRICTION_EVIDENCE_REPORT.md) & [`xauusd_empirical_friction_manifest.json`](../../artifacts/calibration/xauusd_empirical_friction_manifest.json)

---

## 1. Governance Mandate & Six-Category Model

In quantitative backtesting, risk planning, and empirical policy evaluation for spot gold (`XAUUSD`), transaction frictions exert an overwhelming influence on real expectancy. Under Phase 5/6 and Pre-Phase-8 calibration hardening governance (Directives 1–18, R17–R20):

1. **Zero Silent Fallback Defaults:** Values for contract geometry, trading fees, financing swap points, spreads, and execution slippage cannot be guessed, approximated, or defaulted.
2. **Six Mandatory Evidence Categories:** All empirical friction parameters resolve exclusively from six verified evidence categories:
   - **Category 1: Legal Entity Scope** (`legal_entity_code`, `regulator`, `license`)
   - **Category 2: Contract Geometry** (`digits`, `point_size`, `tick_size`, `tick_value`, `contract_size`, `volume_min`, `volume_max`, `volume_step`)
   - **Category 3: Commission Policy** (`account_tier`, `commission_usd_per_lot_per_side`, `commission_formula`)
   - **Category 4: Financing / Swap Policy** (`swap_long_points`, `swap_short_points`, `rollover_schedule`, `triple_swap_weekday`, `actual_account_swap_free_status`)
   - **Category 5: Empirical Bid/Ask Spread Distribution** (`base_spread_bps`, `stress_spread_bps`, $N \ge 1000$, $\ge 5$ distinct dates, 4 sessions)
   - **Category 6: Execution Slippage Telemetry** (`base_slippage_bps`, `stress_slippage_bps`, $N \ge 30$, directional adverse displacement)
3. **Fail-Closed Blocking:** If any required category is absent or unverified, the system transitions to `EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED` with gate `CANDLES_READY_EMPIRICAL_FRICTION_MISSING`, locking production authority to `FALSE / 0.0 / WAIT`.

---

## 2. Execution Profile Isolation & Artifact Scope

AurumIQ enforces strict partitioning between execution profiles and analytical market data:

1. **Analytical vs Execution Boundary:**
   - **Analytical Market Data:** Canonical 15m, 1H, 4H, and 1D candles, indicators, and regime models derive strictly from Twelve Data for `XAUUSD`.
   - **Execution & Friction Evidence:** Sourced from broker-specific exports (Exness) and strictly partitioned by account tier.
2. **Profile Decoupling Rule:**
   ```text
   account_tier != broker_symbol != account_currency
   ```
   - Supported tiers: `STANDARD`, `STANDARD_CENT`, `RAW_SPREAD`.
   - Broker execution symbols are explicit scope (e.g. `STANDARD + expected_broker_symbol=XAUUSDm`, `STANDARD_CENT + expected_broker_symbol=XAUUSDc`).
   - Account currency is explicit declared scope (`USD`, `USC`), defaults to `None`/`UNKNOWN`, carries zero evidence qualification authority, and introduces no conversion engine.
3. **Artifact Scope Truth:**
   - The canonical manifest [`artifacts/calibration/xauusd_empirical_friction_manifest.json`](../../artifacts/calibration/xauusd_empirical_friction_manifest.json) is explicitly bound to `account_tier: "STANDARD"` with symbol `XAUUSD`.
   - `STANDARD_CENT` maintains an isolated output path (`artifacts/calibration/xauusd_standard_cent_empirical_friction_manifest.json`).
   - `STANDARD` evidence artifacts must **NEVER** be mutated or described as representing `STANDARD_CENT`, and vice versa.

---

## 3. Elimination of Phase 8 Slippage Circularity

Prior exploratory notes incorrectly suggested compiling slippage telemetry from Phase 8 paper trading. This circularity is explicitly prohibited:

```text
[INVALID CIRCULARITY - FORBIDDEN]
Calibration Qualification ──► Requires Slippage ──► Compiled from Phase 8 Paper ──► Blocked by Calibration Gate

[GOVERNED CAUSAL LINEAGE - ACTIVE TRUTH]
Real MT5 Execution Fill Telemetry (N >= 30)
         │
         ▼
Qualify Category 6 (Slippage Telemetry)
         │
         ▼
Empirical Friction Calibration Qualified (All 6 Categories)
         │
         ▼
Unblock Calibration Gate ──► Authorize Phase 8 Live Paper Observation
```

- **Slippage Evidence Source:** Sourced strictly from authentic broker execution fill telemetry ($N \ge 30$ real order fills, broker execution reports, or institutional FIX telemetry).
- **Phase 8 Prerequisite:** Phase 8 live paper observation is strictly downstream of calibration qualification and cannot execute until all six friction categories (including slippage) are sealed.

---

## 4. Current Friction Evidence Inventory Status

| Evidence Category | Target Parameters | Status | Governance Rule & Finding |
| :--- | :--- | :---: | :--- |
| **1. Legal Entity Scope** | `legal_entity_code`, `regulator`, `license` | `LEGAL_ENTITY_EVIDENCE_MISSING` | Requires verified account agreement snapshot. |
| **2. Contract Geometry** | `point_size`, `tick_size`, `contract_size`, volumes | `CONTRACT_SPEC_EVIDENCE_MISSING` | Requires MT5 contract spec export. Zero silent defaults. |
| **3. Commission Policy** | Native commission per lot per side, formula | `COMMISSION_EVIDENCE_MISSING` | Requires broker fee schedule snapshot. Zero silent defaults. |
| **4. Financing Policy** | Swap long/short points, triple-swap day, swap-free status | `FINANCING_EVIDENCE_MISSING` | Requires broker swap schedule snapshot. Zero silent defaults. |
| **5. Spread Distribution** | Base & stress spread bps ($N \ge 1000$, 5 days, 4 sessions) | `SPREAD_EMPIRICAL_EVIDENCE_MISSING` | Requires authentic MT5 tick history export. |
| **6. Slippage Telemetry** | Directional adverse slippage bps ($N \ge 30$) | `SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING` | Requires authentic MT5 execution telemetry fills. Not sourced from Phase 8. |

**Overall Gate Status:**
```text
STATUS:   EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED
GATE:     CANDLES_READY_EMPIRICAL_FRICTION_MISSING
WEIGHT:   0.0
DECISION: WAIT
```

---

## 5. Next Steps for Calibration Unblocking

To advance from `CANDLES_READY_EMPIRICAL_FRICTION_MISSING` toward empirical qualification:
1. Provide authoritative Exness account agreement snapshot resolving `legal_entity_code`.
2. Provide authoritative MT5 contract specification snapshot for the designated account tier.
3. Provide authoritative broker fee schedule snapshot.
4. Provide authoritative broker financing swap schedule snapshot.
5. Provide authentic Exness tick history export covering $\ge 5$ distinct trading days and all 4 sessions.
6. Provide authentic Exness execution telemetry fills ($N \ge 30$) from verified order executions.
