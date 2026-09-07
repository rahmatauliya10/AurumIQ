# AurumIQ — Stage C: Lean Codebase Audit & Runtime Classification

> **Document Status:** Authoritative Audit Report
> **Source Baseline SHA:** `2ee19143aa98ccf15f25b1dec0ec4c2f2fc0bfc0` (PR #22 Documentation Truth Sealed & Merged)
> **Branch:** `chore/lean-codebase-cleanup`
> **Audit Objective:** Systematically identify confirmed dead code, redundant compatibility aliases, inactive queue configurations, and security micro-gaps to simplify the runtime footprint without altering active analytical/risk behavior, data readiness gates, or historical audit baselines.

---

## 1. Executive Summary & Classification Methodology

AurumIQ has successfully unified its documentation, sealed its empirical friction governance (PR #20), isolated execution profile scopes (PR #21), and validated all 1,223 tests on GitHub CI.

To prevent speculative abstraction and accidental breakage of historical regression suites, every component in this audit is classified under strict criteria:

| Classification | Definition | Governance Action |
|---|---|---|
| **CORE_ACTIVE** | Actively executed in the primary `XAUUSD` signal, risk, data, or dashboard path. | **PRESERVE** unmodified. |
| **OPTIONAL_ACTIVE** | Secondary/fallback mechanism or optional bridge invoked under explicit configuration. | **PRESERVE**; ensure clean boundaries. |
| **RESEARCH_ONLY** | Experimental modules (e.g. Phase 3B spectral cycles) isolated with `weight = 0.0`. | **FREEZE**; zero runtime impact. |
| **LEGACY_COMPATIBILITY** | Historical frozen baseline (e.g. Phase 5/6 single-side XAUT) retained for acceptance audits. | **RETAIN**; do not use as active default. |
| **DEAD_CONFIRMED** | Proven to have 0 runtime callers, 0 test callers, and 0 migration dependencies. | **DELETE** safely. |
| **DUPLICATED_CONFIRMED** | Pure redundant alias or duplicate implementation of an existing canonical module. | **CONSOLIDATE / DELETE ALIAS**. |
| **UNKNOWN_USAGE** | Usage pattern cannot be definitively proven inactive with 100% confidence. | **STRICTLY PRESERVE** (Rule: Never delete unknown). |

---

## 2. Comprehensive Subsystem Audit Matrix

| Subsystem / Component | Path | Current Purpose | Runtime Callers | Test Callers | Classification | Safe to Change? | Proposed Action | Rationale |
|---|---|---|---|---|:---:|:---:|---|---|
| **Backtest Alias** | `engine/backtesting/__init__.py` | Compatibility re-export alias of `engine.backtest` | None (0) | None (0) | `DUPLICATED_CONFIRMED` / `DEAD_CONFIRMED` | **YES** | Delete `engine/backtesting/` directory | All 37 callers import from canonical `engine.backtest`. Alias has 0 external callers. |
| **Twelve Data Provider** | `apps/market_data/providers/twelve_data.py` | Primary market data ingestion adapter for canonical `XAUUSD` | Tasks, backfill commands | 4 test suites | `CORE_ACTIVE` | **NO** | Keep unchanged | Core analytical ingestion provider. |
| **XAUUSD Spot Providers** | `apps/market_data/providers/xauusd_spot.py`, `xauusd_secondary.py` | Direct spot gold reference adapters for Phase 1 contracts | Registry, tasks | Acceptance tests (`test_xau_p1_*`) | `CORE_ACTIVE` / `OPTIONAL_ACTIVE` | **NO** | Keep unchanged | Required by Phase 1 contract integrity tests. |
| **Historical Providers** | `binance.py`, `okx.py`, `gold_reference.py`, `usdt_usd.py` | Historical `XAUT/USDT` crypto exchange adapters | Registry fallback | Historical unit tests (`test_providers.py`) | `LEGACY_COMPATIBILITY` | **NO** (Keep files) | Preserve files; optimize default registration | Preserves historical regression evidence. |
| **Generic Risk Modules** | `engine/risk/execution.py`, `planner.py`, `stops.py`, `targets.py` | Historical Phase 5 single-side XAUT risk planning baseline | Historical replay/tasks | Historical acceptance tests (`test_a19`, `test_a25`, `test_a27`) | `LEGACY_COMPATIBILITY` | **NO** | Keep unchanged | Frozen historical baseline. Merging with dual-side XAUUSD would violate frozen contract specs. |
| **XAUUSD Risk Modules** | `engine/risk/xauusd_execution.py`, `xauusd_planner.py`, `xauusd_stops.py`, etc. | Active dual-side side-aware XAUUSD risk planner (H1–H74) | Live monitor, backtest runner | Hostile tests, contract tests | `CORE_ACTIVE` | **NO** | Keep unchanged | Production-critical mathematical planning engine. |
| **Generic Backtest Replay** | `engine/backtest/runner.py`, `replay.py`, `metrics.py`, `walkforward.py` | Historical Phase 6 backtest replay engine | Generic tasks | Historical acceptance tests (`test_phase6_acceptance`) | `LEGACY_COMPATIBILITY` | **NO** | Keep unchanged | Preserves Phase 6 frozen acceptance suite. |
| **XAUUSD Backtest Replay** | `engine/backtest/xauusd_runner.py`, `xauusd_replay.py`, etc. | Active point-in-time XAUUSD replay and fold engine | `apps/backtests/tasks.py` | Acceptance & unit tests | `CORE_ACTIVE` | **NO** | Keep unchanged | Active backtest execution pipeline. |
| **Live Monitor CSS/JS** | `static/css/dashboard.css`, `static/js/dashboard.js` | Styling and client script for live monitor interface | `templates/live_monitor/base.html` | UI manual/e2e | `CORE_ACTIVE` | **NO** | Keep unchanged | Used specifically by Live Monitor templates. |
| **Main Dashboard CSS/JS** | `static/dashboard/css/dashboard.css`, `static/dashboard/js/dashboard.js` | Styling and client script for administrative dashboard | `templates/dashboard/base.html` | Dashboard views | `CORE_ACTIVE` | **NO** | Keep unchanged | Distinct visual asset for overview, analysis, signals, and backtest views. |
| **Spectral Cycles** | `engine/cycles/experimental/` (`acf.py`, `fft.py`, `wavelet.py`, `hilbert.py`) | Phase 3B frequency domain cycle detection research | None in live decision | Targeted research tests (`test_phase3b_targeted.py`) | `RESEARCH_ONLY` | **NO** | Keep frozen (`weight = 0.0`) | Verified isolated; does not participate in active production signals. |
| **God Modules** | `apps/live_monitor/services.py` (1,731 LOC), `apps/market_data/readiness.py` (1,447 LOC), `friction/artifact_parsers.py` (1,280 LOC) | Monolithic domain service modules | Live pipelines, readiness gate, ingestion commands | Extensive test coverage | `CORE_ACTIVE` | **NO** | Keep intact | High cohesion; file size is not a defect. Splitting would introduce artificial service wrappers. |
| **MT5 Bridge Adapter** | `tools/mt5_bridge/adapter.py`, `main.py` | Optional read-only bridge for local MT5 tick and fill extraction | Standalone CLI | `test_mt5_bridge.py` | `OPTIONAL_ACTIVE` | **NO** | Keep read-only | Strictly optional; contains zero automated order execution code. |
| **Uvicorn ASGI Runtime** | `pyproject.toml`, `docker/Dockerfile.prod` | Production ASGI web server running Django | Production Docker container | Docker build CI | `CORE_ACTIVE` | **NO** | Keep in core dependencies | Required by `Dockerfile.prod` CMD: `uvicorn config.asgi:application`. Moving would break prod build. |
| **ML Libraries** | `scikit-learn`, `xgboost`, `lightgbm`, `optuna`, `pywavelets` | Machine learning stack for Phase 9 | Optional research | None in core runtime | `RESEARCH_ONLY` / `OPTIONAL_ML` | **NO** | Already optional in `pyproject.toml` | Already safely partitioned under `[project.optional-dependencies] ml`. |
| **Machine Learning Celery Queue** | `config/settings/base.py` (`CELERY_TASK_QUEUES["machine_learning"]`) | Task queue reserved for Phase 9 | None (0 tasks) | None (0) | `DEAD_CONFIRMED` | **YES** | Remove from active queue configuration | Unused queue consuming broker resources with 0 active workers or tasks. |
| **Client IP Resolution** | `apps/accounts/views.py` (`_get_client_ip`) | Extracts client IP for user management audit logging | Admin views | Account integration tests | `SECURITY_DEFECT` | **YES** | Harden to default to `REMOTE_ADDR` | Prevent IP spoofing via untrusted `HTTP_X_FORWARDED_FOR` headers. |
| **Admin User Creation Password Validation** | `apps/accounts/views.py` (`UserCreationView.post`) | Creates users via admin dashboard | Web admin | Account tests | `SECURITY_DEFECT` | **YES** | Add `validate_password()` call | Ensure `AUTH_PASSWORD_VALIDATORS` are strictly enforced for admin user creation. |
| **Project Identity Metadata** | `pyproject.toml` (`name = "xaut-signal-intelligence"`) | Package naming metadata | Packaging/build | Build system | `LEGACY_COMPATIBILITY` | **YES** | Update to `aurumiq` | Aligns project identity with post-XAUT institutional Gold Intelligence. |
| **Documentation SHA Governance** | Multiple documents (`README.md`, blueprint, `docs/phases/*`) | Records baseline SHA for audit integrity | Documentation readers | Governance audit | `GOVERNANCE_DRIFT` | **YES** | Replace "Current Main SHA" with "Last Verified Baseline SHA" | Stops endless circular documentation PR churn upon merge. |

---

## 3. High-Confidence Cleanup Action Plan

### 1. Delete `engine/backtesting/` (Zero Caller Alias)
- **Path:** `engine/backtesting/__init__.py`
- **Findings:**
  - Contains 15 lines re-exporting `from engine.backtest import *`.
  - Audited all 37 backtest importers in repository: 100% already import directly from `engine.backtest`.
  - Zero external callers exist for `engine.backtesting`.
- **Action:** Delete `engine/backtesting/` directory.

### 2. Defer Inactive `machine_learning` Celery Queue
- **Path:** `config/settings/base.py`
- **Findings:**
  - Audited all 12 `@shared_task` definitions across `apps/`.
  - Tasks strictly route to: `market_data`, `analysis`, `backtest`, `maintenance`.
  - Zero tasks route to `machine_learning`.
- **Action:** Remove `machine_learning` from `CELERY_TASK_QUEUES`. Keep comment noting Phase 9 will define it when authorized.

### 3. Security Micro-Hardening in `apps/accounts/views.py`
- **Path:** `apps/accounts/views.py`
- **Findings:**
  - `_get_client_ip()`: Directly reads `HTTP_X_FORWARDED_FOR.split(',')[0]` without verifying whether the request came from a trusted reverse proxy, allowing trivial header spoofing.
  - `UserCreationView.post()`: Invokes `User.objects.create_user()` without calling `django.contrib.auth.password_validation.validate_password()`, bypassing configured password complexity rules.
- **Action:**
  - Update `_get_client_ip()` to use `request.META.get("REMOTE_ADDR")` by default, only inspecting `HTTP_X_FORWARDED_FOR` if a trusted proxy setting is enabled.
  - Add explicit `validate_password(password, user=None)` check in `UserCreationView.post()`, returning clear validation errors if password requirements are not met.

### 4. Align Project Metadata in `pyproject.toml`
- **Path:** `pyproject.toml`
- **Findings:**
  - Name is still `xaut-signal-intelligence` with description `Research-grade XAUT Signal Intelligence decision-support web application`.
- **Action:**
  - Update to `name = "aurumiq"` and `description = "Institutional-grade multi-timeframe quantitative gold intelligence platform"`.
  - Maintain all runtime dependencies and optional groups (`dev`, `ml`).

### 5. Documentation Baseline SHA Governance Fix
- **Path:** `README.md`, `XAUUSD_Signal_Intelligence_Blueprint_Django_Python_v2.md`, `docs/phases/*`, `docs/calibration/*`
- **Findings:**
  - Documenting "Current Authoritative Main SHA" within a branch that gets merged creates an immediate post-merge contradiction (since the merge creates a new SHA).
- **Action:**
  - Standardize header badge to: `> **Last Verified Baseline SHA:** 2ee19143aa98ccf15f25b1dec0ec4c2f2fc0bfc0 (PR #22 Documentation Truth Sealed & Merged)`.

---

## 4. Branch Protection Governance Report

- **Target:** Branch `main` on GitHub repository `rahmatauliya10/AurumIQ`
- **Ruleset Identified:** Ruleset ID `21851214` (*"AurumIQ Main Protection"*), `enforcement: active`
- **Configured Rules:**
  - `deletion`: Active
  - `non_fast_forward`: Active
  - `pull_request`: Active (`required_approving_review_count: 0`, allowed methods: `merge`, `squash`, `rebase`)
  - `required_status_checks`: Active with context `Regression & Compliance Suite`
  - `bypass_actors`: `[]` (Empty)
- **Governance Recommendation:** Status checks are properly targeted to `Regression & Compliance Suite`. Keep ruleset intact; do not modify repository administration.

---

## 5. Items Preserved & Kept Intact (No Code Changes)

1. **Generic vs XAUUSD Modules:** Kept strictly separate. The generic modules are frozen historical regression baselines for single-side XAUT; the XAUUSD modules are the active dual-side spot gold engine. No inheritance or base classes invented.
2. **Static Assets:** Both `static/css/dashboard.css` and `static/dashboard/css/dashboard.css` are preserved because they serve two distinct template hierarchies (`live_monitor/base.html` vs `dashboard/base.html`).
3. **God Modules:** Preserved intact. High cohesion, low external leakage, and verified stability make splitting counter-productive.
4. **Phase 3B Research Modules:** Kept frozen with `production_weight = 0.0`.
5. **Uvicorn Dependency:** Preserved in core dependencies because it serves as the production ASGI entrypoint in `Dockerfile.prod`.
