# AurumIQ — Stage C: Lean Codebase Audit & Runtime Classification

> **Document Status:** Authoritative Audit Report (PR #23 Targeted Lean Runtime Remediation)
> **Source Baseline SHA:** `2ee19143aa98ccf15f25b1dec0ec4c2f2fc0bfc0` (PR #22 Documentation Truth Sealed & Merged)
> **Branch:** `chore/lean-codebase-cleanup`
> **Audit Objective:** Systematically eliminate legacy runtime defaults, seal active XAUUSD operational defaults, cleanly isolate historical compatibility layers, remove unused Celery queue overhead, and harden account security boundaries without altering mathematical risk invariants or historical audit baselines.

---

## 1. Executive Summary & Classification Methodology

Following independent audit review of PR #23, remaining active legacy defaults in presentation, market data ingestion, and provider registration have been remediated. Current operational runtime is strictly sealed to canonical `XAUUSD`.

Every component in the audited cleanup candidate set is classified under strict, mutually exclusive operational tiers:

| Operational Tier | Definition | Runtime Status |
|---|---|---|
| **CURRENT XAUUSD DEFAULT** | Actively executed in the canonical XAUUSD signal, risk, ingestion, or dashboard path by default. | **ACTIVE DEFAULT** |
| **LEGACY AVAILABLE EXPLICITLY** | Historical modules, providers, or endpoints retained for regression and audit evidence; invoked only when explicitly requested or registered. | **EXPLICIT ONLY** |
| **LEGACY ACTIVE DEFAULT** | Legacy code unintentionally executed on the default operational path. | **ELIMINATED (0 remaining in audited paths)** |
| **RESEARCH_ONLY** | Experimental modules (e.g. Phase 3B spectral cycles) isolated with `production_weight = 0.0`. | **FROZEN / ISOLATED** |
| **DEAD_CONFIRMED** | Proven to have 0 runtime callers, 0 test callers, and 0 migration dependencies. | **DELETED** |
| **UNKNOWN_USAGE** | Usage pattern cannot be definitively proven inactive with 100% confidence. | **NONE in audited cleanup candidate set** |

---

## 2. Comprehensive Subsystem Audit Matrix

| Subsystem / Component | Path | Current Purpose | Runtime Callers | Test Callers | Classification | Operational Status | Remediated Action |
|---|---|---|---|---|:---:|:---:|---|
| **Root Routing** | `config/urls.py` (`path("")`) | Entrypoint for application root URL | Browser / HTTP root | Smoke & routing tests | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Redirects (302) to `dashboard:overview` (`/dashboard/`). Legacy live monitor moved exclusively to `/live/`. |
| **Current Dashboard** | `apps/dashboard/*` | Primary institutional XAUUSD presentation and analytics dashboard | Web navigation | Dashboard test suite | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Server-rendered views and REST APIs for XAUUSD live projection and lab. |
| **Live Monitor Presentation** | `apps/live_monitor/views.py` (Dashboard & history views) | Historical XAUT presentation endpoints | Explicit `/live/` path | `test_auth_flow.py` | `LEGACY_COMPATIBILITY` | **LEGACY AVAILABLE EXPLICITLY** | Retained strictly under `/live/` and `/live/history/` as historical compatibility surface. |
| **Live Monitor Shared Infrastructure** | `apps/live_monitor/services.py`, `models.py` | Shared projection and state services (`XauUsdLiveProjectionService`, `LiveMonitorState`) | Dashboard, live pipelines | Phase 7 acceptance, hostile tests | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Preserved intact; shared core infrastructure. |
| **Dashboard Static Assets** | `static/dashboard/*` | Styling and client scripts for active XAUUSD dashboard | `templates/dashboard/base.html` | Dashboard views | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Active frontend presentation assets. |
| **Live Monitor Static Assets** | `static/js/dashboard.js`, `static/css/dashboard.css` | Client scripts for historical XAUT live monitor | `templates/live_monitor/base.html` | Legacy UI tests | `LEGACY_COMPATIBILITY` | **LEGACY AVAILABLE EXPLICITLY** | Retained for explicit `/live/` compatibility surface. |
| **Ingestion Tasks Defaults** | `apps/market_data/tasks.py` (`ingest_primary_candles`, `ingest_resolution_candles`) | Celery candle ingestion tasks | Celery beat, management commands | Ingestion test suites | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Default `instrument_symbol` sealed to `"XAU/USD"`. Historical `"XAUT/USDT"` remains possible only when explicitly passed. |
| **Primary Provider** | `apps/market_data/providers/twelve_data.py` | Authoritative analytical spot gold market data provider | Ingestion tasks | Twelve Data tests | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Registered by default in global `ProviderRegistry`. |
| **Secondary Provider** | `apps/market_data/providers/xauusd_secondary.py` | Independent secondary spot gold integrity verification feed | Ingestion tasks | Integrity tests | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Registered by default in global `ProviderRegistry`. |
| **Legacy Exchange Providers** | `binance.py`, `okx.py`, `gold_reference.py`, `usdt_usd.py` | Historical XAUT/USDT exchange adapters | Explicit test registration | `test_providers.py`, `test_ingestion_pipeline.py` | `LEGACY_COMPATIBILITY` | **LEGACY AVAILABLE EXPLICITLY** | Not imported or registered by active `apps/market_data/providers/registry.py`. Instantiated/registered only when explicitly required in historical test scopes. |
| **Placeholder Spot Provider** | `apps/market_data/providers/xauusd_spot.py` | Pre-TwelveData mock placeholder | Explicit test instantiation | `test_xauusd_phase1.py` | `LEGACY_COMPATIBILITY` | **LEGACY AVAILABLE EXPLICITLY** | Removed from default registration (MarketListing is HALTED). |
| **Provider Health Task** | `apps/market_data/tasks.py` (`check_provider_health_task`) | Periodic health probe for registered providers | Celery maintenance queue | Health task tests | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Loops `registry.all_providers()`, probing only active default XAUUSD providers (`TwelveData`, `SecondaryXauUsd`). Legacy providers are NOT probed. |
| **Celery Queues: Active** | `config/settings/base.py` (`market_data`, `analysis`, `backtest`, `maintenance`) | Asynchronous task execution queues | 12 active tasks | Celery tests | `CORE_ACTIVE` | **CURRENT XAUUSD DEFAULT** | Preserved with active tasks and worker configuration. |
| **Celery Queues: Machine Learning** | `config/settings/base.py` (`machine_learning`) | Task queue reserved for Phase 9 | None (0 active tasks) | None | `RESEARCH_ONLY` | **DEFERRED** | Removed from active `CELERY_TASK_QUEUES` and worker `-Q` arguments. Phase 9 remains HOLD. |
| **Celery Queues: Alerts** | `config/settings/base.py` (`alerts`) | Task queue reserved for asynchronous alerts | None (alerts are synchronous) | None | `RESEARCH_ONLY` | **DEFERRED** | Removed from active `CELERY_TASK_QUEUES` and worker `-Q` arguments. |
| **Dead Backtest Alias** | `engine/backtesting/__init__.py` | Compatibility re-export alias of `engine.backtest` | None (0) | None (0) | `DEAD_CONFIRMED` | **DELETED** | Deleted directory. All callers import from `engine.backtest`. |
| **User Password Validation** | `apps/accounts/views.py` (`UserCreateView.post`) | Admin user creation validation | Web admin | `test_accounts.py` | `SECURITY_HARDENING` | **CURRENT XAUUSD DEFAULT** | Builds candidate `User` and invokes `validate_password(password, user=candidate_user)`, enforcing `UserAttributeSimilarityValidator`. Hostile test added. |
| **Client IP Resolution** | `apps/accounts/views.py` (`_get_client_ip`) | IP extraction for audit logging | Admin views | `test_accounts.py` | `SECURITY_HARDENING` | **CURRENT XAUUSD DEFAULT** | Defaults to `REMOTE_ADDR`, only inspecting `X-Forwarded-For` when `TRUST_REVERSE_PROXY` is explicitly enabled. |

---

## 3. Detailed Remediation Evidence

### 1. Presentation Root Boundary Seal
- **Before:** Root `/` routed directly to `apps.live_monitor.urls`, rendering legacy XAUT/USDT UI and filtering `base_asset__code="XAUT"`.
- **After:** Root `/` issues an HTTP 302 redirect to `dashboard:overview` (`/dashboard/`), immediately landing users on the active XAUUSD institutional overview.
- **Historical Preservation:** Historical Live Monitor is preserved intact at `/live/` and `/live/history/`.
- **Verification:** Tested in `tests/unit/test_routing_remediation.py` and `tests/integration/test_auth_flow.py`.

### 2. Ingestion Task Defaults & Historical XAUT Compatibility Truth
- **Before:** `ingest_primary_candles()` and `ingest_resolution_candles()` defaulted to `instrument_symbol="XAUT/USDT"`.
- **After:** Defaults sealed to `instrument_symbol="XAU/USD"`.
- **Historical Truth:** Historical XAUT execution path is preserved for frozen regression and audit harnesses where required legacy providers are explicitly registered.
- **Fail-Closed Contract:** Explicit XAUT invocation without registered legacy providers fails closed deterministically with `reason: "LEGACY_PROVIDER_NOT_REGISTERED"` (no uncaught `KeyError`).
- **CLI Truth:** `backfill_candles` management command help primarily describes `XAU/USD` and clearly states legacy `XAUT/USDT` requires explicit historical provider registration.
- **Verification:** Tested in `tests/unit/test_market_data_defaults.py` (fail-closed and explicit registration tests) and `tests/integration/test_ingestion_pipeline.py`.

### 3. Provider Auto-Registration & Active Registry Lean Import Elimination
- **Before:** Global `ProviderRegistry` automatically imported and instantiated Binance, OKX, GoldReference, UsdtUsdRate, and XauUsdSpot providers on module load, and exposed dead helper `get_configured_gold_reference_url()`.
- **After:** Active registry module `apps/market_data/providers/registry.py` strictly imports and initializes ONLY:
  - `TwelveDataProvider` (`twelve_data_xauusd`, active primary)
  - `SecondaryXauUsdSpotProvider` (`xauusd_secondary`, active secondary integrity feed)
- **Dead Helper Eliminated:** `get_configured_gold_reference_url()` and associated unused imports (`os`, `Optional`) removed.
- **Historical Preservation:** Legacy provider classes remain in their respective source files. Historical tests (`test_ingestion_pipeline.py`, `test_providers.py`) import and register them explicitly in test scope.
- **Health Probing:** `check_provider_health_task()` loops `registry.all_providers()` and therefore probes only active XAUUSD providers. Legacy providers are never probed by default.
- **Verification:** Tested in `tests/unit/test_market_data_defaults.py`.

### 4. Celery Queue Truth
- **Before:** `CELERY_TASK_QUEUES` declared 6 queues: `market_data`, `analysis`, `backtest`, `machine_learning`, `maintenance`, `alerts`.
- **Audit Findings:**
  - 12 active tasks strictly map to: `market_data`, `analysis`, `backtest`, `maintenance`.
  - `machine_learning`: 0 active tasks (Phase 9 on HOLD).
  - `alerts`: 0 queued tasks (alerts generate synchronously via `AlertGenerationService`).
- **After:** `machine_learning` and `alerts` removed from `CELERY_TASK_QUEUES`, `docker-compose.yml`, and `docker-compose.prod.yml`.
- **Test Updated:** `tests/unit/test_celery.py::test_celery_queues_configured` updated to assert active queues and ensure deferred queues remain absent.

### 5. Account Password Validation Security Completion
- **Before:** `validate_password(password)` called without `user`, bypassing `UserAttributeSimilarityValidator`.
- **After:** An unsaved candidate `User(username=username, email=email, first_name=first_name, last_name=last_name)` is built and passed: `validate_password(password, user=candidate_user)`.
- **Verification:** Hostile test `test_user_creation_view_rejects_password_similar_to_user_attributes` added to `tests/unit/test_accounts.py` proving passwords similar to user attributes are rejected.

### 6. Service Identity & Metadata Alignment
- **Before:** `/health/` response returned `"service": "xaut-signal-intelligence"`.
- **After:** Aligned to `"service": "aurumiq"` in `config/urls.py` and smoke tests.

---

## 4. Remediation Metrics (Compared to Baseline `2ee19143...`)

| Metric | Measurement |
|---|---|
| **PYTHON_FILES_BEFORE** | 343 |
| **PYTHON_FILES_AFTER** | 344 (343 - 1 deleted alias `engine/backtesting/__init__.py`, 2 focused new test suites added) |
| **FILES_REMOVED** | 1 (`engine/backtesting/__init__.py`) |
| **RUNTIME_COMPONENTS_DEACTIVATED** | 5 legacy providers from default global registration; 2 unused Celery queues |
| **LEGACY_PROVIDER_DEFAULTS_REMOVED** | 5 (`binance`, `okx`, `gold_reference`, `usdt_usd`, `xauusd_primary`) |
| **LEGACY_PROVIDERS_IMPORTED_BY_ACTIVE_REGISTRY** | false |
| **LEGACY_PROVIDERS_AUTO_REGISTERED** | false |
| **DEAD_GOLD_REFERENCE_REGISTRY_HELPER_REMOVED** | true |
| **LEGACY_UI_DEFAULT_REMOVED** | 1 (Root `/` redirected to active `/dashboard/`) |
| **LEGACY_INGESTION_DEFAULTS_REMOVED** | 2 (`ingest_primary_candles`, `ingest_resolution_candles` defaulted to XAU/USD) |
| **HEALTH_SERVICE_IDENTITY** | aurumiq |
| **CELERY_QUEUES_DEFERRED** | 2 (`machine_learning`, `alerts`) |
| **SECURITY_FIXES** | 2 (Client IP `REMOTE_ADDR` hardening + Candidate user password similarity validation) |
| **UNKNOWN_USAGE** | 0 in audited cleanup candidate set |
