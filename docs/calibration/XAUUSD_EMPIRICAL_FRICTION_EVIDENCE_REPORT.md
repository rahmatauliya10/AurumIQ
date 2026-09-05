# AURUMIQ — XAUUSD EMPIRICAL FRICTION EVIDENCE AUDIT REPORT

> **Protocol Version:** Pre-Phase-8 Empirical Friction Hardening Seal  
> **Target Venue:** `EXNESS`  
> **Account Tier:** `STANDARD`  
> **Symbol:** `XAUUSD`  
> **Audit Timestamp:** `2026-09-05 03:43:52 UTC`  
> **Overall Friction Decision:** `EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED`  
> **Hard Readiness Gate:** `CANDLES_READY_EMPIRICAL_FRICTION_MISSING`  
> **Production Authority:** `FALSE / 0.0 / WAIT`  

---

## 1. Executive Summary

In accordance with Pre-Phase-8 Empirical Friction Calibration Hardening Governance (Directives 1-18), execution frictions for `XAUUSD` under target venue `EXNESS` have been evaluated strictly against genuine, persisted evidence with **ZERO silent defaults**.

The architecture closes all evidence-completeness loopholes:
- Removes all silent fallback defaults for contract geometry, commissions, and swap points.
- Enforces genuine source snapshots for legal entity, contract spec, commission schedules, and financing policies.
- Integrates production parsers for MT5 tick exports and MT5 execution telemetry.
- Enforces **MANDATORY execution slippage telemetry** (`SLIPPAGE_IS_MANDATORY = TRUE`).
- Prohibits incomplete models from receiving `ACTIVE` activation (downgraded to `DRAFT`).
- Enforces point-in-time activation resolution with scope validation.

Because genuine MT5 tick history exports, telemetry fills, and account-specific legal agreements have not yet been ingested into the governed production environment, the platform strictly enforces **FAIL-CLOSED** semantics:

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
| **Legal Entity Scope** | `legal_entity_code`, `regulator`, `license` | `LEGAL_ENTITY_EVIDENCE_MISSING` | Directive 10: Sourced strictly from verified account snapshot. |
| **Contract Geometry** | `point_size`, `tick_size`, `contract_size` | `CONTRACT_SPEC_EVIDENCE_MISSING` | Directive 4: Requires verified MT5 contract spec export. Zero silent defaults. |
| **Commission Policy** | `commission_usd_per_lot_per_side` | `COMMISSION_EVIDENCE_MISSING` | Directive 5: Requires verified fee schedule snapshot. Zero silent defaults. |
| **Financing Policy** | Swap points, rollover schedule | `FINANCING_EVIDENCE_MISSING` | Directive 3: Requires verified swap snapshot. Zero silent defaults. |
| **Spread Distribution** | `base_spread_bps`, `stress_spread_bps` | `SPREAD_EMPIRICAL_EVIDENCE_MISSING` | Directive 6: Requires verified MT5 tick export ($N \ge 1000$, $\ge 5$ distinct dates, 4 sessions). |
| **Slippage Telemetry** | `base_slippage_bps`, `stress_slippage_bps` | `SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING` | Directives 7 & 8: Directional slippage telemetry is MANDATORY ($N \ge 30$). |

---

## 3. Prior Evidence Invariance Verification

Prior frozen evidence remains 100% bit-for-bit invariant:
- **Macro Fingerprint:** `d9d2ebb4c6ec11fafc4ffce35090d64a5eaa05a3e024da4148b3900cf6370823`
- **Phase-6 15m Fingerprint:** `2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9`
- **Readiness 6-TF Fingerprint:** `d5d8f7a20cf820f177ccafb99d60d09cf503e5a80eee95a89bc7cf02334764b9`
- **Total Historical Spot Candles:** `3,096,312` (zero rows mutated)

---

## 4. Next Steps for Unblocking

To advance from `CANDLES_READY_EMPIRICAL_FRICTION_MISSING` to `CANDLES_READY_QUOTE_EVIDENCE_MISSING`:
1. Provide authoritative Exness account agreement snapshot resolving `legal_entity_code`.
2. Provide authoritative MT5 contract specification snapshot.
3. Provide authoritative MT5 fee schedule snapshot.
4. Provide authoritative MT5 financing swap schedule snapshot.
5. Provide authentic Exness MT5 tick history export covering $\ge 5$ distinct trading days and all 4 sessions.
6. Provide authentic Exness MT5 execution telemetry fills ($N \ge 30$).
