# Phase 0: Foundation Architecture & Multi-Asset Protocol Boundary

> **Historical XAUT Baseline Status:** ✅ `VERIFIED / FROZEN`  
> **Current XAUUSD Target Status:** 🟢 `REUSABLE`  
> **Primary Goal:** Provide a rock-solid, institutional-grade infrastructure with multi-asset protocol boundaries, append-only PostgreSQL schemas, 5 dedicated Celery task queues, hardened RBAC with effective-admin invariants, and strict dependency inversion separating pure calculation engines from Django framework infrastructure.

---

## 1. System Topology & Protocol Boundary

```text
┌────────────────────────────────────────────────────────┐
│               DJANGO APP LAYER (apps/*)                │
│  - REST API & Server-Rendered UI Views                 │
│  - PostgreSQL 16 (Append-Only Audit & Ingestion Tables)│
│  - Celery 5.x Workers (5 Priority Queues)              │
│  - Redis Pub/Sub (Live Presentation Transport)         │
└───────────────────────────┬────────────────────────────┘
                            │ (Dependency Inversion via Protocols)
                            ▼
┌────────────────────────────────────────────────────────┐
│           PURE PYTHON ENGINE CORE (engine/*)           │
│  - Pure Data Classes (CandleData, MarketContext)       │
│  - Pure Abstract Protocols (CandleRepository, Provider)│
│  - Mathematical Calculation Libraries (NumPy / SciPy)  │
│  - Zero Django / ORM / Celery / Redis Imports (R9)     │
└────────────────────────────────────────────────────────┘
```

### Protocol Purity Contract (R9)
The `engine/` package is completely decoupled from Django. Inversion of control is achieved via Python `typing.Protocol` interfaces:
- `CandleRepository`: Pure protocol defining point-in-time candle retrieval.
- `MarketDataProvider`: Pure protocol defining provider ingestion behavior.
- `SignalEngine`: Pure protocol defining immutable market context analysis.

---

## 2. Infrastructure Specifications

### A. Celery 5.x Priority Queues
| Queue Name | Concurrency / Routing | Purpose |
|---|---|---|
| `critical` | High Priority | Immediate operational fail-safes and health state transitions |
| `ingestion` | Dedicated Ingestion Worker | Multi-provider candle fetching and normalization tasks |
| `signals` | Serialized Decision Worker | Point-in-time closed-bar signal evaluation and persistence |
| `backtest` | Background Worker Pool | Asynchronous walk-forward backtesting and ablation runs |
| `default` | General Worker | Audit logging, notifications, and maintenance tasks |

### B. Hardened Account & Audit Invariants (Stage 1 RBAC)
1. **Audit Durability:** `UserManagementAuditLog.target_user` enforces `on_delete=models.PROTECT` to preserve complete audit history.
2. **Authoritative Effective Admin Semantics:**  
   $$\text{Effective Active Admin} \iff \text{is\_active} = \text{True} \land (\text{is\_superuser} = \text{True} \lor \text{profile.role} = \text{ADMIN})$$
3. **Pessimistic Locking & Concurrency Protection:** Multi-row locking via `select_for_update().order_by("id")` prevents race conditions from disabling the last remaining active administrator.
4. **Django Admin Bypass Closure:** Hard delete permissions are disabled (`has_delete_permission = False`), bulk deletion actions are removed, and `save_model` / `save_formset` hooks enforce admin invariants.

---

## 3. Definition of Done Checklist

- [x] Foundation repository structure and Django 5.2 configuration established.
- [x] 5 Celery priority queues configured with Redis broker.
- [x] PostgreSQL schemas initialized with JSONB and indexed timestamp fields.
- [x] Pure `typing.Protocol` boundaries enforced (AST verified zero Django imports in `engine/`).
- [x] RBAC roles (`VIEWER`, `ANALYST`, `ADMIN`) and protected audit logging implemented.
- [x] Last-active admin lockout protection verified under multi-threaded concurrency.
