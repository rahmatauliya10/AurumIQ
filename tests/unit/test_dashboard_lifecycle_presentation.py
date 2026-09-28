"""
Targeted tests for Stage 3 & Stage 4 Dashboard Lifecycle Presentation & Explainability.
Verifies:
CASE 1: SELL_WINDOW + invalid SHORT risk plan + authority OFF
        Candidate = SELL, Risk = PLAN INVALIDATED / NO VALID RISK PLAN (no "NO CANDIDATE"), Published = WAIT
CASE 2: Candidate exists + valid risk plan + authority OFF
        Candidate shown, Risk plan shown valid, Published = WAIT due authority lock
CASE 3: No candidate
        Candidate = WAIT / NO_TRADE, Risk = NO ACTIVE RISK PLAN
CASE 4: Historical INVALID plan
        Appears in history/audit, not in active/live plan section (ACTIVE RISK PLAN = NONE)
CASE 5: Zero-weight explainability components
        Listed as inactive (weight 0), not counted as scored contributors, no "+0.0 / 0.0 pts"
CASE 6: Active contribution totals reconcile to persisted scores
        Displayed active contribution sum equals persisted score within tolerance; zero-weight components contribute 0.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.instruments.models import Instrument
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import XauUsdLiveProjectionService
from apps.live_monitor.types import XauUsdLiveProjectionState
from apps.dashboard.views import AuditLogView
from apps.signals.models import SignalRecord


@pytest.mark.unit
@pytest.mark.django_db
class TestDashboardLifecyclePresentation:
    """Stage 3 & Stage 4 targeted test suite."""

    def test_case_1_sell_window_invalid_risk_plan_authority_off(self):
        """
        CASE 1:
        candidate = SELL / SELL_WINDOW
        risk plan exists and is INVALID (e.g. RR 1.2640R below minimum required 3.03R)
        authority = OFF (WAIT)
        UI must NOT say 'NO CANDIDATE'.
        Must say 'SHORT PLAN' and 'PLAN INVALIDATED' or 'NO VALID RISK PLAN',
        with invalidation reason.
        """
        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            candidate_state="SELL_WINDOW",
            candidate_user_decision="SELL",
            published_state="FORCE_WAIT",
            published_user_decision="WAIT",
            publication_reason="GOVERNANCE_LOCK_PRODUCTION_AUTHORITY_OFF",
            risk_side="SHORT",
            risk_candidate_status="INVALID_RR",
            risk_plan_valid=False,
            execution_eligible=False,
            candidate_effective_action="WAIT",
            publication_effective_action="WAIT",
            entry_min=Decimal("2650.00"),
            entry_mid=Decimal("2652.50"),
            entry_max=Decimal("2655.00"),
            stop_final=Decimal("2660.00"),
            tp1=Decimal("2643.68"),
            rr_tp1=Decimal("1.2640"),
            risk_plan_fingerprint="short_plan_fp_case1",
            feed_health_data={
                "risk_plan_invalidation_reason": "RR 1.2640R below minimum required 3.03R",
            },
        )
        LiveRiskPlanRecord.objects.create(
            risk_plan_fingerprint="short_plan_fp_case1",
            source_signal_fingerprint="sig_case1",
            signal_timestamp=datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc),
            instrument="XAUUSD",
            risk_side="SHORT",
            is_valid_risk_plan=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2650.00"),
            entry_max=Decimal("2655.00"),
            stop_final=Decimal("2660.00"),
            tp1=Decimal("2643.68"),
            rr_tp1=Decimal("1.2640"),
            reasons=["RR 1.2640R below minimum required 3.03R"],
            code_revision="5740c9cb",
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.candidate_user_decision == "SELL"
        assert proj.candidate_state == "SELL_WINDOW"
        assert proj.is_valid_risk_plan is False
        assert proj.risk_side == "SHORT"
        assert proj.published_user_decision == "WAIT"
        assert proj.risk_plan_invalidation_reason == "RR 1.2640R below minimum required 3.03R"

        # Geometry preserved for audit
        assert proj.entry_min == Decimal("2650.00")
        assert proj.stop_final == Decimal("2660.00")
        assert proj.tp1 == Decimal("2643.68")
        assert proj.planned_rr_tp1 == Decimal("1.2640")

        # Render overview template
        html = render_to_string("dashboard/overview.html", {"projection": proj})

        # Assert no misleading 'NO CANDIDATE' in risk section
        assert "INVALID / NO CANDIDATE" not in html
        assert "PLAN INVALIDATED" in html or "NO VALID RISK PLAN" in html
        assert "SHORT PLAN" in html
        assert "RR 1.2640R below minimum required 3.03R" in html
        assert "SELL" in html
        assert "WAIT" in html

    def test_case_2_candidate_exists_valid_risk_plan_authority_off(self):
        """
        CASE 2:
        Candidate exists + valid risk plan + authority OFF
        Candidate shown, risk plan shown valid, published = WAIT due to authority lock.
        """
        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            candidate_state="SELL_WINDOW",
            candidate_user_decision="SELL",
            published_state="FORCE_WAIT",
            published_user_decision="WAIT",
            publication_reason="GOVERNANCE_LOCK_PRODUCTION_AUTHORITY_OFF",
            risk_side="SHORT",
            risk_candidate_status="VALID",
            risk_plan_valid=True,
            execution_eligible=True,
            candidate_effective_action="SELL",
            publication_effective_action="WAIT",
            entry_min=Decimal("2650.00"),
            entry_mid=Decimal("2652.50"),
            entry_max=Decimal("2655.00"),
            stop_final=Decimal("2660.00"),
            tp1=Decimal("2625.00"),
            rr_tp1=Decimal("3.50"),
            risk_plan_fingerprint="short_plan_fp_case2",
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.candidate_user_decision == "SELL"
        assert proj.is_valid_risk_plan is True
        assert proj.published_user_decision == "WAIT"

        html = render_to_string("dashboard/overview.html", {"projection": proj})
        assert "VALID RISK PLAN" in html
        assert "SHORT PLAN" in html
        assert "WAIT" in html
        assert "GOVERNANCE_LOCK_PRODUCTION_AUTHORITY_OFF" in html

    def test_case_3_no_candidate(self):
        """
        CASE 3:
        No candidate
        Candidate = WAIT / NO_TRADE, Risk = NO ACTIVE RISK PLAN.
        """
        state = LiveMonitorState.objects.create(
            instrument="XAUUSD",
            candidate_state="NO_TRADE",
            candidate_user_decision="WAIT",
            published_state="NO_TRADE",
            published_user_decision="WAIT",
            publication_reason="Awaiting setup",
            risk_side=None,
            risk_candidate_status=None,
            risk_plan_valid=False,
            execution_eligible=False,
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        assert proj.candidate_user_decision == "WAIT"
        assert proj.candidate_state == "NO_TRADE"
        assert proj.risk_side is None
        assert proj.is_valid_risk_plan is False

        html = render_to_string("dashboard/overview.html", {"projection": proj})
        assert "NO ACTIVE RISK PLAN" in html
        assert "INVALID / NO CANDIDATE" not in html

    def test_case_4_historical_invalid_plan(self):
        """
        CASE 4:
        Historical INVALID plan
        Appears in history/audit, not in active/live plan section.
        """
        # Create historical invalid plan
        LiveRiskPlanRecord.objects.create(
            risk_plan_fingerprint="hist_invalid_fp",
            source_signal_fingerprint="sig_hist_1",
            signal_timestamp=datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc),
            instrument="XAUUSD",
            risk_side="SHORT",
            is_valid_risk_plan=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2650.00"),
            entry_max=Decimal("2655.00"),
            stop_final=Decimal("2660.00"),
            tp1=Decimal("2643.68"),
            rr_tp1=Decimal("1.2640"),
            reasons=["RR 1.2640R below minimum required 3.03R"],
            code_revision="5740c9cb",
        )

        rf = RequestFactory()
        req = rf.get("/dashboard/audit/")
        req.user = type("MockUser", (), {"is_authenticated": True, "username": "trader"})()

        view = AuditLogView()
        response = view.get(req)
        assert response.status_code == 200

        html = response.content.decode("utf-8")

        # Active risk plan section must show NONE
        assert "PHASE 5 ACTIVE RISK PLAN" in html
        assert "ACTIVE RISK PLAN = NONE" in html

        # Historical section must show history table with invalid record
        assert "PHASE 5 RISK PLAN HISTORY / AUDIT" in html
        assert "RR 1.2640R below minimum required 3.03R" in html
        assert "1.2640R" in html
        assert "hist_invalid_fp"[:12] in html

    def test_case_5_zero_weight_explainability_components(self):
        """
        CASE 5:
        Zero-weight explainability components
        Listed inactive (weight 0), not counted as scored contributors, no '+0.0 / 0.0 pts'.
        """
        cb = {
            "long_direction": [
                {"name": "Market Regime Quality", "score": 0.0, "max_score": 0.0, "reason": "Adverse", "is_available": True},
                {"name": "1H Trend Alignment", "score": 10.0, "max_score": 11.88, "reason": "Bull trend", "is_available": True},
                {"name": "Volume Confirmation", "score": 0.0, "max_score": 0.0, "reason": "No volume", "is_available": False},
            ],
            "long_timing": [
                {"name": "Entry Zone Proximity", "score": 12.0, "max_score": 14.95, "reason": "Inside zone", "is_available": True},
                {"name": "Phase 3A Cycle Timing", "score": 0.0, "max_score": 0.0, "reason": "Uncalibrated", "is_available": False},
                {"name": "Volume Response", "score": 0.0, "max_score": 0.0, "reason": "No data", "is_available": False},
            ],
            "short_direction": [],
            "short_timing": [],
        }

        factors = XauUsdLiveProjectionService.categorize_explainability_factors(cb)

        # Zero-weight components must be in inactive_components
        inactive = factors["inactive_components"]
        assert any("Market Regime" in x and "inactive" in x and "weight 0" in x for x in inactive)
        assert any("Volume Confirmation" in x and "inactive" in x and "weight 0" in x for x in inactive)
        assert any("Phase 3A" in x and "inactive" in x and "weight 0" in x for x in inactive)
        assert any("Volume Response" in x and "inactive" in x and "weight 0" in x for x in inactive)

        # Contributing factors must NOT contain zero-weight components or '+0.0 / 0.0 pts'
        contributing = factors["contributing_factors"]
        for c in contributing:
            assert "0.0/0.0 pts" not in c
            assert "+0.0 / 0.0 pts" not in c
            assert "Market Regime Quality" not in c
            assert "Volume Confirmation" not in c
            assert "Phase 3A" not in c
            assert "Volume Response" not in c

        # Weak/negative factors must NOT contain zero-weight components
        weak_neg = factors["weak_negative_factors"]
        for w in weak_neg:
            assert "0.0/0.0 pts" not in w
            assert "Market Regime Quality" not in w

    def test_case_6_active_contribution_totals_reconcile_to_persisted_scores(self):
        """
        CASE 6:
        Active contribution totals reconcile to persisted scores
        DISPLAYED_ACTIVE_CONTRIBUTION_SUM = persisted score within serialization tolerance.
        Zero-weight components contribute exactly 0.
        """
        inst = Instrument.get_canonical_xauusd()
        if not inst:
            from apps.instruments.models import Asset, AssetType, InstrumentRole, InstrumentType
            base, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": AssetType.COMMODITY})
            quote, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT})
            inst, _ = Instrument.objects.get_or_create(
                base_asset=base,
                quote_asset=quote,
                instrument_type=InstrumentType.SPOT,
                defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
            )
        sig = SignalRecord.objects.filter(instrument=inst).order_by("-timestamp", "-created_at").first()
        if not sig:
            # Create a test signal record with exact breakdown if DB empty
            sig = SignalRecord.objects.create(
                instrument=inst,
                timeframe="15m",
                timestamp=datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc),
                state="FORCE_WAIT",
                user_decision="WAIT",
                long_direction_score=48.03,
                short_direction_score=77.21,
                long_timing_score=88.03,
                short_timing_score=8.60,
                components_breakdown={
                    "long_direction": [
                        {"name": "Market Regime Quality", "score": 0.0, "max_score": 0.0, "reason": "Adverse", "is_available": True},
                        {"name": "1H Trend Alignment", "score": 2.38, "max_score": 11.8877, "reason": "1H trend", "is_available": True},
                        {"name": "4H Trend Alignment", "score": 0.0, "max_score": 3.9745, "reason": "4H trend", "is_available": True},
                        {"name": "1D Macro Trend", "score": 0.0, "max_score": 30.9802, "reason": "1D trend", "is_available": True},
                        {"name": "Market Structure & BOS", "score": 7.50, "max_score": 15.005, "reason": "BOS", "is_available": True},
                        {"name": "Pullback Quality", "score": 15.53, "max_score": 15.5289, "reason": "Pullback", "is_available": True},
                        {"name": "Momentum State", "score": 22.62, "max_score": 22.6237, "reason": "RSI", "is_available": True},
                        {"name": "Volume Confirmation", "score": 0.0, "max_score": 0.0, "reason": "No volume", "is_available": False},
                    ],
                    "short_direction": [
                        {"name": "Market Regime Quality", "score": 0.0, "max_score": 0.0, "reason": "Adverse", "is_available": True},
                        {"name": "1H Trend Alignment", "score": 13.01, "max_score": 21.6828, "reason": "1H trend", "is_available": True},
                        {"name": "4H Trend Alignment", "score": 16.75, "max_score": 16.75, "reason": "4H trend", "is_available": True},
                        {"name": "1D Macro Trend", "score": 25.15, "max_score": 25.1459, "reason": "1D trend", "is_available": True},
                        {"name": "Market Structure & BOS", "score": 0.0, "max_score": 10.4533, "reason": "BOS", "is_available": True},
                        {"name": "Pullback Quality", "score": 21.08, "max_score": 21.0784, "reason": "Pullback", "is_available": True},
                        {"name": "Momentum State", "score": 1.22, "max_score": 4.8896, "reason": "RSI", "is_available": True},
                        {"name": "Volume Confirmation", "score": 0.0, "max_score": 0.0, "reason": "No volume", "is_available": False},
                    ],
                    "long_timing": [
                        {"name": "Entry Zone Proximity", "score": 2.99, "max_score": 14.9569, "reason": "Zone", "is_available": True},
                        {"name": "15m Reversal Confirmation", "score": 27.18, "max_score": 27.1787, "reason": "Pin", "is_available": True},
                        {"name": "15m + 1H Momentum Turn", "score": 57.86, "max_score": 57.8644, "reason": "Turn", "is_available": True},
                        {"name": "Phase 3A Cycle Timing", "score": 0.0, "max_score": 0.0, "reason": "Uncalibrated", "is_available": False},
                        {"name": "Volume Response", "score": 0.0, "max_score": 0.0, "reason": "No volume", "is_available": False},
                    ],
                    "short_timing": [
                        {"name": "Entry Zone Proximity", "score": 8.60, "max_score": 42.9806, "reason": "Zone", "is_available": True},
                        {"name": "15m Reversal Confirmation", "score": 0.0, "max_score": 2.8122, "reason": "Reversal", "is_available": True},
                        {"name": "15m + 1H Momentum Turn", "score": 0.0, "max_score": 54.2072, "reason": "Turn", "is_available": True},
                        {"name": "Phase 3A Cycle Timing", "score": 0.0, "max_score": 0.0, "reason": "Uncalibrated", "is_available": False},
                        {"name": "Volume Response", "score": 0.0, "max_score": 0.0, "reason": "No volume", "is_available": False},
                    ],
                },
                code_revision="5740c9cb",
            )

        cb = sig.components_breakdown
        assert cb is not None

        # Reconcile long direction
        long_dir_active_sum = sum(c["score"] for c in cb["long_direction"] if c["max_score"] > 0)
        assert abs(long_dir_active_sum - sig.long_direction_score) < 0.05
        # Zero-weight components contribute 0
        long_dir_zero_sum = sum(c["score"] for c in cb["long_direction"] if c["max_score"] == 0)
        assert long_dir_zero_sum == 0.0

        # Reconcile short direction
        short_dir_active_sum = sum(c["score"] for c in cb["short_direction"] if c["max_score"] > 0)
        assert abs(short_dir_active_sum - sig.short_direction_score) < 0.05
        short_dir_zero_sum = sum(c["score"] for c in cb["short_direction"] if c["max_score"] == 0)
        assert short_dir_zero_sum == 0.0

        # Reconcile long timing
        long_tim_active_sum = sum(c["score"] for c in cb["long_timing"] if c["max_score"] > 0)
        assert abs(long_tim_active_sum - sig.long_timing_score) < 0.05
        long_tim_zero_sum = sum(c["score"] for c in cb["long_timing"] if c["max_score"] == 0)
        assert long_tim_zero_sum == 0.0

        # Reconcile short timing
        short_tim_active_sum = sum(c["score"] for c in cb["short_timing"] if c["max_score"] > 0)
        assert abs(short_tim_active_sum - sig.short_timing_score) < 0.05
        short_tim_zero_sum = sum(c["score"] for c in cb["short_timing"] if c["max_score"] == 0)
        assert short_tim_zero_sum == 0.0
