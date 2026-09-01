# Phase 0: Foundation & Infrastructure Scaffolding

> **Historical XAUT Baseline Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Historical Source:** `main` @ `0bd9dbe38ea41594377f0fb0ce4b539b1037ac9a`  
> **Current XAUUSD Target Status:** 🟢 **REUSABLE**

---

## XAUUSD Migration Addendum

### 1. Reusability Assessment
The Phase 0 foundational architecture (Django 5.2 LTS, 5 Celery priority queues, Redis, PostgreSQL 16, and pure Python protocol boundaries in `engine/core/`) is instrument-agnostic and 100% reusable for the `XAUUSD` migration target.

### 2. Post-Baseline Reusable Infrastructure Hardening (Gate A)
The following account and audit durability hardening was implemented on top of the Phase 0 baseline:
1. **Audit Durability:** `UserManagementAuditLog.target_user` enforces `on_delete=models.PROTECT` (migration `0003_alter_usermanagementauditlog_target_user.py`) to prevent cascading audit deletion.
2. **Authoritative Effective Admin Invariant:**  
   $$\text{Effective Active Admin} \iff \text{is\_active} = \text{True} \land (\text{is\_superuser} = \text{True} \lor \text{profile.role} = \text{ADMIN})$$
3. **Pessimistic Concurrency Locking:** Multi-row locking via `select_for_update().order_by("id")` prevents concurrent admin actions from leaving zero active administrators.
4. **Django Admin Bypass Closure:** Disabled hard delete permissions (`has_delete_permission = False`), removed bulk deletion action `delete_selected`, added read-only `UserManagementAuditLogAdmin`, and protected admin save hooks (`save_model`, `save_formset`).

---

## Historical XAUT Frozen Specification (Verbatim Baseline)

> **Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Baseline Commit SHA:** `f3f8bbb2ab41a208e4ce8016bb5be3a3fe9d4314`  
> **Primary Goal:** Establish rock-solid Django 5.2 LTS, Celery (5 queues), Redis, PostgreSQL, and Docker Compose foundation with strict protocol boundaries.

### 1. Scope & Deliverables

#### A. Scaffolding & Settings
- Multi-environment settings split:
  - `config/settings/base.py`: Core configurations, Redis cache, 5 Celery queue topologies, JSON structlog.
  - `config/settings/development.py`: Debug mode enabled, readable console logs.
  - `config/settings/production.py`: Security hardening (SSL redirect, HSTS 1 year, secure cookies, strict headers).
  - `config/settings/testing.py`: Fast in-memory SQLite, eager Celery execution, MD5 password hashing.
- Entrypoints: `manage.py`, `config/wsgi.py`, `config/asgi.py`, `config/urls.py` (with `/health/` container probe).

#### B. Celery Topology (5 Dedicated Queues)
- `market_data`: Ingestion and data quality checking.
- `analysis`: Signal generation and state machine transitions.
- `backtest`: Historical simulation and walk-forward jobs.
- `machine_learning`: Meta-filter model training.
- `maintenance`: System health and heartbeat checks (default queue).

#### C. Access Control & User Roles (`apps/accounts/`)
- `UserProfile` model extending standard Django `User` via `post_save` signal.
- Role enumeration with Least-Privilege default:
  - `VIEWER` (Default): Read-only dashboard access.
  - `ANALYST`: Research, backtesting lab, and model experimentation.
  - `ADMIN`: Source management, engine configuration, user admin.

#### D. Pure-Python Engine Boundary (R9)
- `engine/core/interfaces.py`: `CandleRepository` defined as a pure `typing.Protocol` with zero Django ORM dependencies.
- `engine/core/types.py`: `CandleData` frozen value object.

#### E. Container & Orchestration (`docker/`)
- `docker/Dockerfile`: Python 3.13-slim image, non-root user (`appuser:1000`).
- `docker/docker-compose.yml`: 5 interconnected services (`xaut_postgres`, `xaut_redis`, `xaut_web`, `xaut_celery_worker`, `xaut_celery_beat` — *Historical / Legacy service naming preserved for infrastructure stability*).

### 2. File Manifest

```text
config/
├── __init__.py
├── asgi.py
├── celery.py
├── urls.py
├── wsgi.py
└── settings/
    ├── __init__.py
    ├── base.py
    ├── development.py
    ├── production.py
    └── testing.py
apps/
├── __init__.py
└── accounts/
    ├── __init__.py
    ├── admin.py
    ├── apps.py
    ├── models.py
    └── migrations/
        ├── __init__.py
        └── 0001_initial.py
engine/
├── __init__.py
└── core/
    ├── __init__.py
    ├── interfaces.py
    └── types.py
docker/
├── Dockerfile
└── docker-compose.yml
tests/
├── __init__.py
├── conftest.py
└── unit/
    ├── __init__.py
    ├── test_accounts.py
    ├── test_celery.py
    └── test_smoke.py
.dockerignore
.env.example
.gitignore
manage.py
pyproject.toml
pytest.ini
requirements-lock.txt
```

### 3. Verification & Definition of Done

- [x] Docker Compose config validated (`docker compose -f docker/docker-compose.yml config` exits 0).
- [x] Settings split correctly isolates test, dev, and production.
- [x] All 5 Celery queues explicitly mapped and active on worker.
- [x] `UserProfile` migration created with `VIEWER` least-privilege default.
- [x] Unit test suite (`test_smoke.py`, `test_accounts.py`, `test_celery.py`) passing.
- [x] `requirements-lock.txt` generated and committed.
- [x] Zero trading logic, indicators, or signal calculations present.
- [x] Sealed with commit `f3f8bbb2ab41a208e4ce8016bb5be3a3fe9d4314`.
