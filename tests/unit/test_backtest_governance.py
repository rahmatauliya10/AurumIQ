"""
Unit and Integration Tests for Phase 6 / Backtest Lab Governance Hard-Guard.

Verifies strict invariants across:
  - Canonical governance policy resolution
  - Half-open interval boundary semantics
  - API endpoint hard block (BacktestRunLaunchAPIView)
  - Celery task defense-in-depth (run_xauusd_backtest_task)
  - Zero MarketCandle queries on rejected windows (MARKETCANDLE_QUERY_COUNT = 0)
  - Zero OOS data access during testing (OOS_ACCESS_THIS_STEP = 0)

Test Matrix Cases A through J (Prompt Section 8).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.utils import timezone as django_tz

from apps.dashboard.api import BacktestRunLaunchAPIView
from apps.dashboard.views import BacktestLabView
from apps.live_monitor.models import Phase8OperationalState
from apps.market_data.models import MarketCandle
from engine.backtest.xauusd_governance import (
    BacktestGovernanceError,
    BacktestGovernanceSpec,
    NoApprovedResearchWindowError,
    PartitionWindow,
    resolve_backtest_governance_spec,
    validate_backtest_window_governance,
)


@pytest.fixture
def canonical_gov_spec():
    """Authoritative governance spec loaded from canonical selection policy."""
    spec = resolve_backtest_governance_spec()
    assert spec.is_approved_configured is True
    assert spec.approved_window is not None
    return spec


@pytest.fixture
def auth_user():
    """Create an authenticated dashboard user."""
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="research_tester", defaults={"email": "tester@aurumiq.internal"})
    return user


@pytest.fixture
def auth_client(auth_user):
    """Authenticated Django test client."""
    client = Client()
    client.force_login(auth_user)
    return client


# ==============================================================================
# CASE A: Window fully inside approved research partition => ACCEPT
# ==============================================================================
def test_case_a_approved_window_accepted(canonical_gov_spec):
    """Window fully inside approved research partition [historical_start, earliest_oos_start) must be accepted."""
    start_dt = datetime(2022, 1, 1, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc)

    # Invariant: does not raise
    validate_backtest_window_governance(start_dt, end_dt, spec=canonical_gov_spec)


# ==============================================================================
# CASE B: Window fully inside protected OOS => REJECT before task creation
# ==============================================================================
def test_case_b_fully_inside_protected_oos_rejected(canonical_gov_spec):
    """Window fully inside protected walk-forward OOS partition must be strictly rejected."""
    start_dt = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2025, 7, 1, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
        validate_backtest_window_governance(start_dt, end_dt, spec=canonical_gov_spec)


# ==============================================================================
# CASE C: Window starts approved but ends inside protected OOS => REJECT
# ==============================================================================
def test_case_c_starts_approved_ends_inside_oos_rejected(canonical_gov_spec):
    """Window starting in approved partition but bleeding into protected OOS must be rejected."""
    oos_start = canonical_gov_spec.approved_window.end
    start_dt = oos_start - timedelta(days=30)
    end_dt = oos_start + timedelta(days=10)

    with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
        validate_backtest_window_governance(start_dt, end_dt, spec=canonical_gov_spec)


# ==============================================================================
# CASE D: Window starts protected and ends approved/other => REJECT
# ==============================================================================
def test_case_d_starts_protected_ends_other_rejected(canonical_gov_spec):
    """Window starting inside protected OOS and extending past OOS into quarantine must be rejected."""
    oos_start = canonical_gov_spec.approved_window.end
    start_dt = oos_start + timedelta(days=10)
    end_dt = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
        validate_backtest_window_governance(start_dt, end_dt, spec=canonical_gov_spec)


# ==============================================================================
# CASE E: Window merely touches protected boundary => DETERMINISTIC RESULT
# ==============================================================================
def test_case_e_touches_boundary_canonical_semantics(canonical_gov_spec):
    """
    Under canonical half-open [start, end) interval semantics:
    - If end == oos_start: [start, oos_start) does NOT include oos_start => ACCEPT
    - If end == oos_start + 1s: [start, oos_start + 1s) intersects [oos_start, ...) => REJECT
    """
    oos_start = canonical_gov_spec.approved_window.end
    start_dt = canonical_gov_spec.approved_window.start + timedelta(days=10)

    # 1. Merely touches boundary at end_dt == oos_start: Must be ACCEPTED
    end_exact = oos_start
    validate_backtest_window_governance(start_dt, end_exact, spec=canonical_gov_spec)

    # 2. Bleeds past boundary by 1 second: Must be REJECTED
    end_leaked = oos_start + timedelta(seconds=1)
    with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
        validate_backtest_window_governance(start_dt, end_leaked, spec=canonical_gov_spec)

    # 3. Window starting before historical_start: Must be REJECTED (outside approved window)
    start_before = canonical_gov_spec.approved_window.start - timedelta(seconds=1)
    with pytest.raises(BacktestGovernanceError, match="outside the approved research partition"):
        validate_backtest_window_governance(start_before, start_dt, spec=canonical_gov_spec)


# ==============================================================================
# CASE F: Window intersects protected Phase 8 / observation period => REJECT
# ==============================================================================
def test_case_f_intersects_observation_period_rejected(canonical_gov_spec):
    """Window intersecting ongoing / post-remediation Phase 8 observation partition must be rejected."""
    # Find observation partition
    obs_part = next(p for p in canonical_gov_spec.protected_partitions if p.name == "PROTECTED_OBSERVATION_PARTITION")
    start_dt = obs_part.start + timedelta(days=1)
    end_dt = start_dt + timedelta(days=2)

    with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
        validate_backtest_window_governance(start_dt, end_dt, spec=canonical_gov_spec)


# ==============================================================================
# CASE G: Manually crafted API request bypassing browser controls => REJECT
# ==============================================================================
@pytest.mark.django_db
def test_case_g_api_hard_block_prohibited_window(auth_client):
    """
    Manually crafted API request with prohibited OOS window must be rejected at API layer
    BEFORE Celery task creation, with MARKETCANDLE_QUERY_COUNT = 0.
    """
    payload = {
        "ablation_id": "BASELINE",
        "start_date": "2025-06-01T00:00:00+00:00",
        "end_date": "2025-07-01T00:00:00+00:00",
        "cost_scenario": "IDEALIZED",
        "code_revision": "test_craft_api",
        "dataset_hash": "dummy_hash_for_test",
        "holding_horizon_bars_15m": 5,
        "max_fill_wait_bars_15m": 2,
    }

    with patch("apps.backtests.tasks.run_xauusd_backtest_task.delay") as mock_delay:
        with patch.object(MarketCandle.objects, "filter", wraps=MarketCandle.objects.filter) as spy_filter:
            response = auth_client.post("/dashboard/api/backtest/run/", payload, content_type="application/json")

            assert response.status_code == 400
            data = response.json()
            assert "intersects a protected research partition" in data.get("error", "")

            # Invariant: Celery task was NOT enqueued
            mock_delay.assert_not_called()

            # Invariant: Zero MarketCandle queries occurred
            assert spy_filter.call_count == 0


# ==============================================================================
# CASE H: Direct Celery task invocation with prohibited window => REJECT
# ==============================================================================
@pytest.mark.django_db
def test_case_h_direct_celery_task_prohibited_window_defense_in_depth():
    """
    Direct invocation of run_xauusd_backtest_task with prohibited window must abort
    immediately before profile resolution or any MarketCandle query.
    """
    from apps.backtests.tasks import run_xauusd_backtest_task

    with patch.object(MarketCandle.objects, "filter", wraps=MarketCandle.objects.filter) as spy_filter:
        with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
            run_xauusd_backtest_task(
                start_time_iso="2025-06-01T00:00:00+00:00",
                end_time_iso="2025-07-01T00:00:00+00:00",
                dataset_hash="hash_h",
                code_revision="rev_h",
                cost_scenario="IDEALIZED",
                holding_horizon_bars_15m=5,
                max_fill_wait_bars_15m=2,
            )

        # Invariant: Aborted before MarketCandle.objects evaluation
        assert spy_filter.call_count == 0


# ==============================================================================
# CASE I: No approved research window configured => Launch disabled / safe reject
# ==============================================================================
@pytest.mark.django_db
def test_case_i_no_approved_research_window_configured(auth_client, rf, auth_user):
    """
    When policy cannot resolve approved research window:
    - Backend API rejects safely with NO_APPROVED_RESEARCH_WINDOW_CONFIGURED
    - Task aborts with NoApprovedResearchWindowError
    - BacktestLabView sets launch_enabled = False
    """
    unconfigured_spec = BacktestGovernanceSpec(
        policy_id="NONE",
        is_approved_configured=False,
        approved_window=None,
        protected_partitions=(),
        error_code="NO_APPROVED_RESEARCH_WINDOW_CONFIGURED",
    )

    with patch("engine.backtest.xauusd_governance.resolve_backtest_governance_spec", return_value=unconfigured_spec):
        # 1. API endpoint rejects with 400
        payload = {
            "ablation_id": "BASELINE",
            "start_date": "2023-01-01T00:00:00+00:00",
            "end_date": "2023-06-01T00:00:00+00:00",
            "cost_scenario": "IDEALIZED",
            "code_revision": "test_i",
            "dataset_hash": "dummy_i",
            "holding_horizon_bars_15m": 5,
            "max_fill_wait_bars_15m": 2,
        }
        res = auth_client.post("/dashboard/api/backtest/run/", payload, content_type="application/json")
        assert res.status_code == 400
        assert res.json().get("error") == "NO_APPROVED_RESEARCH_WINDOW_CONFIGURED"

        # 2. Celery task aborts before any candle query
        from apps.backtests.tasks import run_xauusd_backtest_task
        with patch.object(MarketCandle.objects, "filter") as spy_filter:
            with pytest.raises(NoApprovedResearchWindowError, match="NO_APPROVED_RESEARCH_WINDOW_CONFIGURED"):
                run_xauusd_backtest_task(
                    start_time_iso="2023-01-01T00:00:00+00:00",
                    end_time_iso="2023-06-01T00:00:00+00:00",
                    dataset_hash="hash_i",
                    code_revision="rev_i",
                    cost_scenario="IDEALIZED",
                    holding_horizon_bars_15m=5,
                    max_fill_wait_bars_15m=2,
                )
            assert spy_filter.call_count == 0

        # 3. View context sets launch_enabled = False
        request = rf.get("/dashboard/backtest/")
        request.user = auth_user
        view = BacktestLabView()
        response = view.get(request)
        assert response.status_code == 200
        assert "NO_APPROVED_RESEARCH_WINDOW_CONFIGURED" in response.content.decode("utf-8")
        assert "disabled" in response.content.decode("utf-8")


# ==============================================================================
# CASE J: Approved window => Normal task flow remains available
# ==============================================================================
@pytest.mark.django_db
def test_case_j_approved_window_allows_task_flow(auth_client):
    """
    Valid approved window passes governance validation and dispatches Celery task normally.
    """
    payload = {
        "ablation_id": "BASELINE",
        "start_date": "2023-01-01T00:00:00+00:00",
        "end_date": "2023-06-01T00:00:00+00:00",
        "cost_scenario": "IDEALIZED",
        "code_revision": "test_j_valid",
        "dataset_hash": "dummy_j_hash",
        "holding_horizon_bars_15m": 5,
        "max_fill_wait_bars_15m": 2,
        "execution_policy": "NEXT_BAR_OPEN",
        "intrabar_policy": "WORST_CASE",
    }

    mock_async_res = MagicMock()
    mock_async_res.id = "task-uuid-approved-12345"

    with patch("apps.backtests.tasks.run_xauusd_backtest_task.delay", return_value=mock_async_res) as mock_delay:
        response = auth_client.post("/dashboard/api/backtest/run/", payload, content_type="application/json")
        assert response.status_code == 202
        data = response.json()
        assert data.get("status") == "ENQUEUED"
        assert data.get("task_id") == "task-uuid-approved-12345"
        mock_delay.assert_called_once()


# ==============================================================================
# DYNAMIC OBSERVATION WINDOW PROTECTION TEST
# ==============================================================================
@pytest.mark.django_db
def test_dynamic_observation_window_protection():
    """
    Dynamic observation window in Phase8OperationalState is honored if earlier than canonical policy.
    Zero MarketCandle queries are made during resolution.
    """
    state, _ = Phase8OperationalState.objects.get_or_create(id=1)
    dynamic_start = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
    state.observation_window_start = dynamic_start
    state.save()

    with patch.object(MarketCandle.objects, "filter") as spy_candle:
        spec = resolve_backtest_governance_spec()
        # MarketCandle was never queried during resolution
        assert spy_candle.call_count == 0

        obs_part = next(p for p in spec.protected_partitions if p.name == "PROTECTED_OBSERVATION_PARTITION")
        assert obs_part.start == dynamic_start

        # Window intersecting this dynamic partition must be rejected
        req_start = datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc)
        req_end = datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc)
        with pytest.raises(BacktestGovernanceError, match="intersects a protected research partition"):
            validate_backtest_window_governance(req_start, req_end, spec=spec)
