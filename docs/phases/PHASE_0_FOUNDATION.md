# Phase 0: Foundation & Infrastructure Scaffolding

> **Status:** ✅ **COMPLETED, VERIFIED & FROZEN**  
> **Baseline Commit SHA:** `f3f8bbb2ab41a208e4ce8016bb5be3a3fe9d4314`  
> **Hardening Reference:** `fix/pre-xauusd-account-audit-hardening`  
> **Primary Goal:** Establish rock-solid Django 5.2 LTS, Celery (5 queues), Redis, PostgreSQL, and Docker Compose foundation with strict protocol boundaries and durable user management auditing.

---

## 1. Scope & Deliverables

### A. Scaffolding & Settings
- Multi-environment settings split:
  - `config/settings/base.py`: Core configurations, Redis cache, 5 Celery queue topologies, JSON structlog.
  - `config/settings/development.py`: Debug mode enabled, readable console logs.
  - `config/settings/production.py`: Security hardening (SSL redirect, HSTS 1 year, secure cookies, strict headers).
  - `config/settings/testing.py`: Fast in-memory SQLite, eager Celery execution, MD5 password hashing.
- Entrypoints: `manage.py`, `config/wsgi.py`, `config/asgi.py`, `config/urls.py` (with `/health/` container probe).

### B. Celery Topology (5 Dedicated Queues)
- `market_data`: Ingestion and data quality checking.
- `analysis`: Signal generation and state machine transitions.
- `backtest`: Historical simulation and walk-forward jobs.
- `machine_learning`: Meta-filter model training.
- `maintenance`: System health and heartbeat checks (default queue).

### C. Hardened Access Control & User Roles (`apps/accounts/`)
- `UserProfile` model extending standard Django `User` with explicit role assignment:
  - `VIEWER` (Default): Read-only dashboard and analytics access.
  - `ANALYST`: Research, backtesting lab, and model experimentation.
  - `ADMIN`: Source management, engine configuration, user lifecycle administration.
- **Authoritative Effective Admin Semantics**:
  $$\text{Effective Active Admin} \iff \text{is\_active} = \text{True} \land (\text{is\_superuser} = \text{True} \lor \text{profile.role} = \text{ADMIN})$$
- **Deterministic Multi-Row Locking**: Invariant validation executes within `transaction.atomic()` with `select_for_update().order_by("id")` to eliminate concurrency race conditions.
- **Audit Durability**: `UserManagementAuditLog.target_user` configured with `on_delete=models.PROTECT`. No hard user deletion in Django Admin.

### D. Pure-Python Engine Boundary (R9)
- `engine/core/interfaces.py`: `CandleRepository` defined as a pure `typing.Protocol` with zero Django ORM dependencies.
- `engine/core/types.py`: `CandleData` frozen value object (instrument-agnostic).

### E. Container & Orchestration (`docker/`)
- `docker/Dockerfile`: Python 3.13-slim image, non-root user (`appuser:1000`).
- `docker/Dockerfile.prod`: Multi-stage production container build.
- `docker/docker-compose.yml`: 5 interconnected development services (`postgres`, `redis`, `web`, `celery_worker`, `celery_beat`).
- `docker/docker-compose.prod.yml`: Hardened production container deployment.

---

## 2. File Manifest

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
    ├── services.py
    ├── views.py
    └── migrations/
        ├── __init__.py
        ├── 0001_initial.py
        ├── 0002_userprofile_department_usermanagementauditlog.py
        └── 0003_alter_usermanagementauditlog_target_user.py
engine/
├── __init__.py
└── core/
    ├── __init__.py
    ├── interfaces.py
    └── types.py
docker/
├── Dockerfile
├── Dockerfile.prod
├── docker-compose.yml
└── docker-compose.prod.yml
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

---

## 3. Verification & Definition of Done

- [x] Docker Compose config validated (`docker compose -f docker/docker-compose.yml config` exits 0).
- [x] Production Docker image builds cleanly (`docker build -f docker/Dockerfile.prod -t aurumiq:ci .`).
- [x] Settings split correctly isolates test, dev, and production.
- [x] All 5 Celery queues explicitly mapped and active on worker.
- [x] `UserProfile` migration created with `VIEWER` least-privilege default.
- [x] Hardened last-active admin lockout prevention and audit durability verified.
- [x] Unit test suite (`test_smoke.py`, `test_accounts.py`, `test_celery.py`) passing 100%.
- [x] Zero trading logic, indicators, or signal calculations present.
- [x] Sealed with baseline commit `f3f8bbb2ab41a208e4ce8016bb5be3a3fe9d4314` and Stage 1 hardening.
