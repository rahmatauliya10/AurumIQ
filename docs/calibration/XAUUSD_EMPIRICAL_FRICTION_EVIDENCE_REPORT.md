# AURUMIQ — XAUUSD EMPIRICAL FRICTION EVIDENCE AUDIT REPORT

> **Protocol Version:** Calibration Plan V3 Final Seal  
> **Target Venue:** `EXNESS`  
> **Account Tier:** `STANDARD`  
> **Symbol:** `XAUUSD`  
> **Audit Timestamp:** `2026-09-04 17:25:27 UTC`  
> **Overall Friction Decision:** `EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED`  
> **Hard Readiness Gate:** `CANDLES_READY_EMPIRICAL_FRICTION_MISSING`  
> **Production Authority:** `FALSE / 0.0 / WAIT`  

---

## 1. Executive Summary

In accordance with Pre-Phase-8 Empirical Friction Calibration Governance (Directives 1-15), execution frictions for `XAUUSD` under target venue `EXNESS` have been evaluated strictly against genuine, persisted evidence.

The architecture eliminates all hard-coded legal entity assumptions, separates MT5 point size from trade tick size, replaces fixed reference-price fees with native notional conversions, enforces append-only models and bindings, and validates sample sufficiency across multiple sessions and distinct trading dates.

Because genuine MT5 tick history exports and account-specific legal agreements have not yet been ingested into the governed production environment, the platform strictly enforces **FAIL-CLOSED** semantics:

```text
STATUS:   EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED
GATE:     CANDLES_READY_EMPIRICAL_FRICTION_MISSING
WEIGHT:   0.0
DECISION: WAIT
```

---

## 2. Friction Evidence Inventory Audit

| Component | Target Metric | Status Classification | Governance Rule & Finding |
| :--- | :--- | :---: | :--- |
| **Legal Entity Scope** | `legal_entity_code`, `regulator`, `license` | `LEGAL_ENTITY_EVIDENCE_MISSING` | Directive 1: No generic assumptions. Sourced only from account agreement or Personal Area metadata. |
| **Contract Geometry** | `point_size`, `tick_size`, `contract_size` | `OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE` | Directive 4: `point_size=0.01` and `trade_tick_size=0.01` stored independently. Contract size = 100 oz. |
| **Commission Policy** | `commission_usd_per_lot_per_side` | `OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE` | Directives 3, 6, 8: Standard = $0.00/lot. Raw Spread = $3.50/lot/side. Converted dynamically via execution notional. |
| **Financing Policy** | Swap points, rollover schedule | `OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE` | Directive 11: Long = -34.80, Short = +12.40. Triple Wednesday. Rollover Summer 21:00 / Winter 22:00 UTC. Swap-free separated from tier. |
| **Spread Distribution** | `base_spread_bps`, `stress_spread_bps` | `SPREAD_EMPIRICAL_EVIDENCE_MISSING` | Directives 2, 9: Requires verified MT5 tick export ($N \ge 1000$, $\ge 5$ distinct dates, 4 sessions). Absent -> fail closed. |
| **Slippage Telemetry** | `base_slippage_bps`, `stress_slippage_bps` | `SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING` | Directive 10: Directional slippage requires live/paper execution telemetry. Absent -> fail closed. |

---

## 3. Dynamic Commission Conversion Formulation

Native commission is persisted strictly in USD per lot per side:

$$\text{notional\_usd} = \text{volume\_lots} \times \text{contract\_size} \times P_{\text{execution}}$$
$$\text{fee\_usd} = \text{volume\_lots} \times \text{commission\_usd\_per\_lot\_per\_side}$$
$$\text{fee\_bps} = \left( \frac{\text{fee\_usd}}{\text{notional\_usd}} \right) \times 10{,}000 = \left( \frac{\text{commission\_usd\_per\_lot\_per\_side}}{\text{contract\_size} \times P_{\text{execution}}} \right) \times 10{,}000$$

- **Standard Account Tier:**
  $$\text{commission} = \$0.00 \implies \text{fee\_bps} = 0.0000\text{ bps}$$
- **Raw Spread Account Tier (Illustrative at \$2,500 Gold):**
  $$\text{fee\_bps} = \left( \frac{3.50}{100 \times 2500} \right) \times 10{,}000 = 0.1400\text{ bps per side (NON\_GATING\_ILLUSTRATIVE\_EXAMPLE)}$$

---

## 4. Prior Evidence Invariance Verification

Prior frozen evidence remains 100% bit-for-bit invariant:
- **Macro Fingerprint:** `d9d2ebb4c6ec11fafc4ffce35090d64a5eaa05a3e024da4148b3900cf6370823`
- **Phase-6 15m Fingerprint:** `2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9`
- **Readiness 6-TF Fingerprint:** `d5d8f7a20cf820f177ccafb99d60d09cf503e5a80eee95a89bc7cf02334764b9`
- **Total Historical Spot Candles:** `3,096,312` (zero rows mutated)

---

## 5. Next Steps for Unblocking

To advance from `CANDLES_READY_EMPIRICAL_FRICTION_MISSING` to `CANDLES_READY_QUOTE_EVIDENCE_MISSING`:
1. Provide authoritative Exness account agreement snapshot resolving `legal_entity_code`.
2. Provide authentic Exness MT5 tick history export covering $\ge 5$ distinct trading days and all 4 sessions.
3. Run `python manage.py ingest_xauusd_empirical_friction --legal-entity-file <path> --tick-file <path>`.
