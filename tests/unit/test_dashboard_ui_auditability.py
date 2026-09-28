"""
Targeted test suite for UI / Auditability Cleanup (Amendment 8).
Validates:
1. Entry Zone Semantics: INVALID historical risk plan + no valid active plan = NO_ACTIVE_ZONE.
2. Signal History Presentation: Exposes analytical candidate_state (e.g. WATCH_SHORT) without masking as NO_TRADE.
3. Server-side Pagination for Signals History (default 50 rows).
4. Server-side Pagination for Phase 3A Robust Time Cycle Snapshots (default 50 rows).
5. Server-side Pagination for Alert Events (51 records => page 1 = 50, page 2 = 1, stable ordering, zero duplicates).
6. Server-side Pagination for Risk Plan History (default 25 rows).
7. Compact Invalidation Reason with expandable full audit detail.
8. Sidebar Toggle / Collapse markup, accessibility, tooltips, and rendering across all 8 dashboard pages.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.test import RequestFactory

from apps.alerts.models import AlertEvent
from apps.analysis.models import CycleSnapshotRecord
from apps.dashboard.views import (
    AuditLogView,
    BacktestLabView,
    DataIntegrityView,
    LiveAnalysisView,
    OverviewView,
    SignalsHistoryView,
    SystemHealthView,
    TimeCycleLabView,
)
from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import XauUsdLiveProjectionService
from apps.live_monitor.types import EntryZoneStatus
from apps.signals.models import SignalRecord


@pytest.mark.unit
@pytest.mark.django_db
class TestDashboardUIAuditability:
    """Targeted tests for UI and Auditability cleanup."""

    @pytest.fixture(autouse=True)
    def setup_base(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="test_audit_user", password="secret_password")
        base, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": AssetType.COMMODITY})
        quote, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT})
        self.inst, _ = Instrument.objects.get_or_create(
            base_asset=base,
            quote_asset=quote,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )
        self.now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

    # =========================================================================
    # 1 — ENTRY ZONE SEMANTICS
    # =========================================================================
    def test_case_1_entry_zone_semantics_no_active_zone_on_invalid_history(self):
        """
        Verify:
        INVALID historical risk plan + no valid active plan = NO_ACTIVE_ZONE.
        Historical geometry must not be converted into an active trading zone.
        """
        # Create an INVALID historical risk plan
        LiveRiskPlanRecord.objects.create(
            instrument="XAUUSD",
            signal_timestamp=self.now - timedelta(minutes=15),
            risk_side="SHORT",
            is_valid_risk_plan=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2500.0000"),
            entry_max=Decimal("2505.0000"),
            stop_final=Decimal("2515.0000"),
            tp1=Decimal("2480.0000"),
            reasons=["Stop distance (1.53421 ATR) exceeds maximum allowable threshold (1.42 ATR)."],
            risk_plan_fingerprint="fp_invalid_hist_01",
        )

        # Ensure LiveMonitorState has no valid active plan
        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            current_ask=Decimal("2502.0000"),
            current_bid=Decimal("2501.5000"),
            risk_plan_valid=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_zone_status=EntryZoneStatus.NO_ACTIVE_ZONE.value,
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.entry_zone_status == EntryZoneStatus.NO_ACTIVE_ZONE

        # Verify Overview renders NO_ACTIVE_ZONE
        req = self.factory.get("/dashboard/overview/")
        req.user = self.user
        resp = OverviewView.as_view()(req)
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")
        assert "NO_ACTIVE_ZONE" in html

        # Verify Audit Log confirms ACTIVE RISK PLAN = NONE
        req_audit = self.factory.get("/dashboard/audit/")
        req_audit.user = self.user
        resp_audit = AuditLogView.as_view()(req_audit)
        assert resp_audit.status_code == 200
        html_audit = resp_audit.content.decode("utf-8")
        assert "ACTIVE RISK PLAN = NONE" in html_audit

    # =========================================================================
    # 2 — SIGNAL HISTORY PRESENTATION
    # =========================================================================
    def test_case_2_candidate_state_presentation_watch_short(self):
        """
        Verify:
        Decision = WAIT, State = WATCH_SHORT
        Table displays Candidate Decision = WAIT, Candidate State = WATCH_SHORT.
        Must NOT be masked as NO_TRADE.
        """
        sig = SignalRecord.objects.create(
            instrument=self.inst,
            timeframe="15m",
            timestamp=self.now,
            state="NO_TRADE",
            user_decision="WAIT",
            long_direction_score=0.0,
            long_timing_score=19.3,
            short_direction_score=47.0,
            short_timing_score=35.7,
            calibration_status="CALIBRATION_REQUIRED",
            analysis_fingerprint="fp_candidate_state_test_01",
            components_breakdown={
                "candidate_state": "WATCH_SHORT",
                "candidate_user_decision": "WAIT",
                "candidate_resolution_reason": "SHORT_WATCH",
            },
        )

        assert sig.candidate_state == "WATCH_SHORT"
        assert sig.candidate_user_decision == "WAIT"
        assert sig.candidate_resolution_reason == "SHORT_WATCH"

        req = self.factory.get("/dashboard/signals/")
        req.user = self.user
        resp = SignalsHistoryView.as_view()(req)
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")

        # Must display WATCH_SHORT in the candidate state column
        assert "WATCH_SHORT" in html
        assert "SHORT_WATCH" in html

    def test_case_2b_legacy_signal_lacking_candidate_state(self):
        """
        Verify:
        Legacy record lacking candidate_state in components_breakdown
        returns None and renders '-' (never fabricated).
        """
        sig_legacy = SignalRecord.objects.create(
            instrument=self.inst,
            timeframe="15m",
            timestamp=self.now - timedelta(hours=1),
            state="NO_TRADE",
            user_decision="WAIT",
            analysis_fingerprint="fp_legacy_signal_02",
            components_breakdown={},
        )

        assert sig_legacy.candidate_state is None
        assert sig_legacy.candidate_user_decision == "WAIT"

        req = self.factory.get("/dashboard/signals/")
        req.user = self.user
        resp = SignalsHistoryView.as_view()(req)
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")
        assert "—" in html

    # =========================================================================
    # 3 — SERVER-SIDE PAGINATION: SIGNALS HISTORY
    # =========================================================================
    def test_case_3_signals_history_server_side_pagination(self):
        """
        Verify Signals History server-side pagination:
        - Default 50 rows/page
        - Query parameters page and page_size (25, 50, 100)
        - Newest-first deterministic ordering
        """
        records = []
        for i in range(55):
            records.append(
                SignalRecord(
                    instrument=self.inst,
                    timeframe="15m",
                    timestamp=self.now - timedelta(minutes=15 * i),
                    state="NO_TRADE",
                    user_decision="WAIT",
                    analysis_fingerprint=f"fp_page_sig_{i:03d}",
                    components_breakdown={"candidate_state": "NO_TRADE", "candidate_user_decision": "WAIT"},
                )
            )
        SignalRecord.objects.bulk_create(records)

        # Page 1 default (50 rows)
        req = self.factory.get("/dashboard/signals/")
        req.user = self.user
        resp = SignalsHistoryView.as_view()(req)
        assert resp.status_code == 200
        html1 = resp.content.decode("utf-8")
        assert "55 Total Signals" in html1
        assert "Page 1 of 2" in html1
        assert "fp_page_sig_000" in html1
        assert "fp_page_sig_054" not in html1

        # Page 2 (5 rows)
        req2 = self.factory.get("/dashboard/signals/?page=2")
        req2.user = self.user
        resp2 = SignalsHistoryView.as_view()(req2)
        assert resp2.status_code == 200
        html2 = resp2.content.decode("utf-8")
        assert "Page 2 of 2" in html2
        assert "fp_page_sig_054" in html2
        assert "fp_page_sig_000" not in html2

        # Custom page size 25
        req_25 = self.factory.get("/dashboard/signals/?page=1&page_size=25")
        req_25.user = self.user
        resp_25 = SignalsHistoryView.as_view()(req_25)
        html_25 = resp_25.content.decode("utf-8")
        assert "Page 1 of 3" in html_25

    # =========================================================================
    # 4 — SERVER-SIDE PAGINATION: PHASE 3A TIME CYCLES
    # =========================================================================
    def test_case_4_phase3a_server_side_pagination(self):
        """
        Verify Phase 3A Robust Time Cycle Snapshots server-side pagination:
        - Default 50 rows/page
        - Query parameters page and page_size
        """
        snapshots = []
        for i in range(52):
            snapshots.append(
                CycleSnapshotRecord(
                    instrument=self.inst,
                    timeframe="15m",
                    timestamp=self.now - timedelta(minutes=15 * i),
                    session="LONDON",
                    session_progress_pct=Decimal("50.0"),
                    is_high_liquidity=True,
                    is_mature_pullback=False,
                    is_blocked_by_event=False,
                    cycle_score_3a=Decimal("65.50"),
                    cycle_version="3.0.0-3A",
                )
            )
        CycleSnapshotRecord.objects.bulk_create(snapshots)

        # Page 1 (50 snapshots)
        req = self.factory.get("/dashboard/cycles/")
        req.user = self.user
        resp = TimeCycleLabView.as_view()(req)
        assert resp.status_code == 200
        html1 = resp.content.decode("utf-8")
        assert "52 Total Snapshots" in html1
        assert "Page 1 of 2" in html1

        # Page 2 (2 snapshots)
        req2 = self.factory.get("/dashboard/cycles/?page=2")
        req2.user = self.user
        resp2 = TimeCycleLabView.as_view()(req2)
        assert resp2.status_code == 200
        html2 = resp2.content.decode("utf-8")
        assert "Page 2 of 2" in html2

    # =========================================================================
    # 5 — SERVER-SIDE PAGINATION: ALERT EVENTS (51 RECORDS BOUNDARY & STABLE ORDER)
    # =========================================================================
    def test_case_5_alert_events_pagination_51_records_and_boundary(self):
        """
        Verify:
        51 records:
        => page 1 = newest 50
        => page 2 = remaining 1
        No duplicates across boundary.
        Stable ordering when timestamps are identical by using deterministic secondary ordering key (-id).
        """
        alerts = []
        fixed_ts = self.now
        for i in range(51):
            alerts.append(
                AlertEvent(
                    event_id=f"EVT_51_TEST_{i:03d}",
                    event_type="WATCH_SHORT_CREATED",
                    instrument="XAUUSD",
                    candidate_state="WATCH_SHORT",
                    candidate_user_decision="WAIT",
                    created_at=fixed_ts,  # Identical timestamp to test deterministic secondary sort (-id)
                )
            )
        AlertEvent.objects.bulk_create(alerts)

        # Direct paginator verification matching view logic
        paginator = Paginator(AlertEvent.objects.all().order_by("-created_at", "-id"), 50)
        page1 = paginator.get_page(1)
        page2 = paginator.get_page(2)
        assert len(page1.object_list) == 50
        assert len(page2.object_list) == 1

        # Disjoint check
        page1_ids = {a.id for a in page1.object_list}
        page2_ids = {a.id for a in page2.object_list}
        assert page1_ids.isdisjoint(page2_ids)
        assert len(page1_ids) + len(page2_ids) == 51

        # Stable descending ID check
        page1_id_list = [a.id for a in page1.object_list]
        assert page1_id_list == sorted(page1_id_list, reverse=True)

        # HTTP View test
        req = self.factory.get("/dashboard/audit/?alerts_page=1")
        req.user = self.user
        resp = AuditLogView.as_view()(req)
        assert resp.status_code == 200
        html1 = resp.content.decode("utf-8")
        assert "51 Total Events" in html1
        assert "Page 1 of 2" in html1

        req2 = self.factory.get("/dashboard/audit/?alerts_page=2")
        req2.user = self.user
        resp2 = AuditLogView.as_view()(req2)
        assert resp2.status_code == 200
        html2 = resp2.content.decode("utf-8")
        assert "Page 2 of 2" in html2

    # =========================================================================
    # 6 — SERVER-SIDE PAGINATION: RISK PLAN HISTORY
    # =========================================================================
    def test_case_6_risk_plan_history_pagination(self):
        """
        Verify Phase 5 Risk Plan History server-side pagination:
        - Default 25 rows/page
        - Boundary handling
        """
        plans = []
        for i in range(30):
            plans.append(
                LiveRiskPlanRecord(
                    instrument="XAUUSD",
                    signal_timestamp=self.now - timedelta(minutes=15 * i),
                    risk_side="SHORT",
                    is_valid_risk_plan=False,
                    execution_eligible=False,
                    effective_action="WAIT",
                    risk_plan_fingerprint=f"fp_risk_page_{i:03d}",
                    reasons=["RR 1.4351R < min 3.03R"],
                )
            )
        LiveRiskPlanRecord.objects.bulk_create(plans)

        paginator = Paginator(LiveRiskPlanRecord.objects.filter(instrument="XAUUSD").order_by("-signal_timestamp", "-id"), 25)
        p1 = paginator.get_page(1)
        p2 = paginator.get_page(2)
        assert len(p1.object_list) == 25
        assert len(p2.object_list) == 5

        # View rendering test
        req = self.factory.get("/dashboard/audit/?risk_page=1")
        req.user = self.user
        resp = AuditLogView.as_view()(req)
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")
        assert "30 Total Plans" in html

    # =========================================================================
    # 7 — COMPACT INVALIDATION REASON PRESENTATION
    # =========================================================================
    def test_case_7_compact_invalidation_reason_expansion(self):
        """
        Verify compact reason generation and expandable full detail:
        - STOP_DISTANCE 1.534 ATR > MAX 1.42 ATR
        - RR 1.4351R < MIN 3.03R
        - Complete unmutated persisted reason retained in full detail
        """
        full_stop_reason = "Stop distance (1.53421 ATR) exceeds maximum allowable threshold (1.42 ATR)."
        full_rr_reason = "Nearest confirmed support at 4282.11163 yields RR 1.435123 below minimum required threshold 3.03."

        r1 = LiveRiskPlanRecord.objects.create(
            instrument="XAUUSD",
            signal_timestamp=self.now,
            risk_side="SHORT",
            is_valid_risk_plan=False,
            execution_eligible=False,
            effective_action="WAIT",
            risk_plan_fingerprint="fp_reason_01",
            reasons=[full_stop_reason],
        )

        r2 = LiveRiskPlanRecord.objects.create(
            instrument="XAUUSD",
            signal_timestamp=self.now - timedelta(minutes=15),
            risk_side="SHORT",
            is_valid_risk_plan=False,
            execution_eligible=False,
            effective_action="WAIT",
            risk_plan_fingerprint="fp_reason_02",
            reasons=[full_rr_reason],
        )

        assert r1.compact_invalidation_reason == "STOP_DISTANCE 1.534 ATR > MAX 1.42 ATR"
        assert r1.full_invalidation_reason == full_stop_reason

        assert r2.compact_invalidation_reason == "RR 1.4351R < MIN 3.03R"
        assert r2.full_invalidation_reason == full_rr_reason

        # Verify rendered HTML contains details/summary structure and both compact and full text
        req = self.factory.get("/dashboard/audit/")
        req.user = self.user
        resp = AuditLogView.as_view()(req)
        html = resp.content.decode("utf-8")

        assert "reason-details" in html
        assert "reason-summary" in html
        assert "[View detail]" in html
        assert "STOP_DISTANCE 1.534 ATR &gt; MAX 1.42 ATR" in html
        assert full_stop_reason in html
        assert "RR 1.4351R &lt; MIN 3.03R" in html
        assert full_rr_reason in html

    # =========================================================================
    # 8 — SIDEBAR TOGGLE / COLLAPSE MARKUP & ACCESSIBILITY
    # =========================================================================
    def test_case_8_sidebar_toggle_markup_and_accessibility_across_pages(self):
        """
        Verify:
        - Sidebar toggle button exists with id='sidebar-toggle'
        - aria-label='Toggle navigation sidebar' and aria-expanded='true'
        - Nav items expose title tooltips for collapsed mode
        - Active tab has 'active' class
        - All 8 dashboard pages render successfully with sidebar structure
        """
        pages = [
            ("overview", OverviewView, "/dashboard/overview/"),
            ("live_analysis", LiveAnalysisView, "/dashboard/analysis/"),
            ("time_cycle", TimeCycleLabView, "/dashboard/cycles/"),
            ("signals_history", SignalsHistoryView, "/dashboard/signals/"),
            ("backtest_lab", BacktestLabView, "/dashboard/backtest/"),
            ("data_integrity", DataIntegrityView, "/dashboard/data-integrity/"),
            ("system_health", SystemHealthView, "/dashboard/system-health/"),
            ("audit_log", AuditLogView, "/dashboard/audit/"),
        ]

        for tab_name, view_cls, path in pages:
            req = self.factory.get(path)
            req.user = self.user
            resp = view_cls.as_view()(req)
            assert resp.status_code == 200, f"Page {tab_name} returned {resp.status_code}"
            html = resp.content.decode("utf-8")

            # Check sidebar element
            assert '<aside class="sidebar" id="sidebar">' in html, f"Missing sidebar in {tab_name}"
            # Check toggle button and accessibility attributes
            assert 'id="sidebar-toggle"' in html, f"Missing sidebar toggle button in {tab_name}"
            assert 'aria-label="Toggle navigation sidebar"' in html, f"Missing aria-label in {tab_name}"
            assert 'aria-expanded="true"' in html, f"Missing aria-expanded in {tab_name}"
            # Check collapsed hydration script
            assert "sidebar-is-collapsed" in html, f"Missing FOUC hydration script in {tab_name}"
            # Check nav item tooltips
            assert 'title="OVERVIEW"' in html, f"Missing nav item tooltip in {tab_name}"
            assert 'title="LIVE ANALYSIS"' in html, f"Missing nav item tooltip in {tab_name}"
            assert 'title="AUDIT LOG"' in html, f"Missing nav item tooltip in {tab_name}"
            # Check active class present on the current tab
            assert 'class="nav-item active"' in html, f"Active nav item not marked in {tab_name}"
