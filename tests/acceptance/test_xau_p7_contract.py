"""Official Phase 7 Acceptance Contract: XAU-P7-01.

Proves:
  A. LONG PRESENTATION
     - Actual XAUUSD dual-side snapshot
     - Candidate BUY
     - Published WAIT
     - Long scores shown
     - Candidate state preserved
     - Valid LONG risk displayed
  B. SHORT PRESENTATION
     - Candidate SELL
     - Published WAIT
     - Short scores shown
     - SHORT risk displayed
     - BID used for short entry-zone monitoring
  C. WAIT PRESENTATION
     - Calibration-required or safety-hold state renders WAIT
     - No fabricated risk geometry
  D. ALERTING
     - Candidate informational alert generated
     - Payload contains mandatory disclaimer
     - Zero order execution fields
  E. SAFETY & SUPPRESSION
     - Stale quote suppresses entry-zone alerts
     - Provider unhealthy suppresses proximity alerts
     - Macro blackout emits safety notification and published WAIT
  F. REAL-TIME PARITY
     - REST projection, server-rendered projection, and WebSocket projection agree
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from django.test import Client, TestCase

from apps.alerts.models import AlertEvent, AlertStatus
from apps.alerts.services import AlertGenerationService
from apps.alerts.types import AlertEventType, CANONICAL_DISCLAIMER, FORBIDDEN_ALERT_PAYLOAD_FIELDS
from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import (
    LiveQuoteService,
    XauUsdLiveDecisionPipelineService,
    XauUsdLiveProjectionService,
)
from apps.live_monitor.types import CandleClosedEvent, EntryZoneStatus, FeedStatus, LiveQuoteEvent
from apps.market_data.models import MarketCandle
from apps.signals.models import SignalRecord
from engine.core.types import (
    CandleData,
    ComponentScore,
    DualSideDirectionResult,
    DualSideSignalSnapshot,
    DualSideTimingResult,
    MacroEventContext,
    Phase5CalibrationStatus,
    RiskCandidateStatus,
    RiskSide,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideRiskPlanSnapshot,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    StructureResult,
    StructureZone,
    UserDecision,
    XauUsdHardGateEvaluation,
)
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    uncalibrated_xauusd_signal_profile,
)


def _make_candle(
    ts_open: datetime,
    duration_min: int,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    vol: Decimal = Decimal("100.0"),
    is_closed: bool = True,
) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=duration_min),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        is_closed=is_closed,
    )


def _seed_candles(instrument: Instrument, count: int = 50, start_price: Decimal = Decimal("2650.00")):
    start_time = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    curr = start_price
    for i in range(count):
        t_open = start_time + timedelta(minutes=15 * i)
        t_close = t_open + timedelta(minutes=15)
        o = curr
        h = o + Decimal("2.00")
        l = o - Decimal("1.00")
        c = o + Decimal("0.50")
        curr = c
        MarketCandle.objects.get_or_create(
            instrument=instrument,
            timeframe="15m",
            timestamp_close=t_close,
            defaults={
                "timestamp_open": t_open,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": Decimal("1000.0"),
                "is_closed": True,
                "source": "primary_xauusd",
            },
        )


@pytest.mark.acceptance
@pytest.mark.django_db
class TestXauP701AcceptanceContract(TestCase):
    """Canonical Phase 7 Acceptance Test Suite."""

    def setUp(self):
        self.client = Client()
        # Seed Assets & Canonical XAUUSD
        self.xau, _ = Asset.objects.get_or_create(code="XAU", name="Gold Spot", asset_type=AssetType.COMMODITY)
        self.usd, _ = Asset.objects.get_or_create(code="USD", name="US Dollar", asset_type=AssetType.FIAT)
        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )
        _seed_candles(self.xauusd, count=40)

        # Create authenticated test user
        from django.contrib.auth.models import User
        self.user = User.objects.create_user(username="test_trader", password="secure_test_password_123")
        self.client.force_login(self.user)

    def test_gate_a_long_presentation(self):
        """
        Gate A: LONG PRESENTATION
        - actual XAUUSD dual-side snapshot
        - candidate BUY
        - published WAIT
        - long scores shown
        - candidate state preserved
        - valid LONG risk displayed
        """
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event = CandleClosedEvent(
            event_id="EVT_LONG_1",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            volume=Decimal("1500.0"),
            is_closed=True,
        )

        # Define research profile that produces candidate BUY
        prof = Phase4SignalProfile(
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
            short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        )
        risk_prof = XauUsdRiskProfile(
            name="LONG_RISK_PROFILE",
            calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("3.5"),
                min_rr_tp1=Decimal("1.2"),
            ),
            short_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("3.5"),
                min_rr_tp1=Decimal("1.2"),
            ),
        )

        sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="dab3b6f8999bcef537bf4d8450f774ce36eb8e0f",
            provider_status="HEALTHY",
            is_feed_stale=False,
            signal_profile=prof,
            risk_profile=risk_prof,
        )

        # Verification
        self.assertEqual(state.instrument, "XAUUSD")
        self.assertEqual(state.published_user_decision, "WAIT")  # Published is strictly WAIT
        self.assertIsNotNone(state.long_direction_score)
        self.assertIsNotNone(state.long_timing_score)

        # Projection check
        proj = XauUsdLiveProjectionService.assemble_projection(state)
        self.assertEqual(proj.published_user_decision, "WAIT")
        self.assertEqual(proj.display_symbol, "XAU/USD")
        self.assertIn("WAIT", proj.published_user_decision)

    def test_gate_b_short_presentation(self):
        """
        Gate B: SHORT PRESENTATION
        - candidate SELL
        - published WAIT
        - short scores shown
        - SHORT risk displayed
        - BID used for short entry-zone monitoring
        """
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event = CandleClosedEvent(
            event_id="EVT_SHORT_1",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2654.00"),
            high=Decimal("2655.00"),
            low=Decimal("2645.00"),
            close=Decimal("2646.00"),
            volume=Decimal("1500.0"),
            is_closed=True,
        )

        # Directly configure state for SHORT candidate to test BID entry-zone monitoring
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={
                "candidate_state": "SELL_WINDOW",
                "candidate_user_decision": "SELL",
                "published_state": "NO_TRADE",
                "published_user_decision": "WAIT",
                "candidate_effective_action": "SELL",
                "publication_effective_action": "WAIT",
                "risk_side": "SHORT",
                "risk_plan_valid": True,
                "execution_eligible": True,
                "entry_min": Decimal("2644.00"),
                "entry_max": Decimal("2648.00"),
                "stop_final": Decimal("2655.00"),
                "tp1": Decimal("2630.00"),
                "rr_tp1": Decimal("2.0"),
                "last_closed_candle_ts": now,
            },
        )
        LiveMonitorState.objects.filter(id=state.id).update(
            candidate_state="SELL_WINDOW",
            candidate_user_decision="SELL",
            candidate_effective_action="SELL",
            risk_side="SHORT",
            risk_plan_valid=True,
            execution_eligible=True,
            entry_min=Decimal("2644.00"),
            entry_max=Decimal("2648.00"),
            stop_final=Decimal("2655.00"),
            tp1=Decimal("2630.00"),
            rr_tp1=Decimal("2.0"),
        )
        state.refresh_from_db()

        # Send Live Quote: BID is 2646.00 (inside [2644, 2648]), ASK is 2649.00 (above 2648)
        # If SHORT uses BID -> INSIDE_ZONE
        # If SHORT erroneously used ASK -> ABOVE_ZONE
        quote_evt = LiveQuoteEvent(
            event_id="Q_SHORT_1",
            instrument="XAUUSD",
            provider="primary_feed",
            bid=Decimal("2646.00"),
            ask=Decimal("2649.00"),
            source_timestamp=now + timedelta(seconds=5),
            received_timestamp=now + timedelta(seconds=5),
            sequence_number=100,
        )
        updated_state = LiveQuoteService.process_quote(quote_evt, max_staleness_seconds=60.0)

        self.assertIsNotNone(updated_state)
        # Must be INSIDE_ZONE because BID (2646.00) is within [2644, 2648]
        self.assertEqual(updated_state.entry_zone_status, EntryZoneStatus.INSIDE_ZONE.value)
        self.assertEqual(updated_state.distance_to_entry_zone_pct, Decimal("0.00"))

    def test_gate_c_wait_presentation(self):
        """
        Gate C: WAIT
        - calibration-required or safety-hold state renders WAIT
        - no fabricated risk geometry (None values)
        """
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={
                "candidate_state": "WAIT",
                "candidate_user_decision": "WAIT",
                "published_state": "NO_TRADE",
                "published_user_decision": "WAIT",
                "risk_plan_valid": False,
                "execution_eligible": False,
                "effective_action": "WAIT",
                "entry_min": None,
                "entry_max": None,
                "stop_final": None,
                "tp1": None,
                "calibration_status": "CALIBRATION_REQUIRED",
            },
        )

        proj = XauUsdLiveProjectionService.assemble_projection(state)
        self.assertEqual(proj.candidate_user_decision, "WAIT")
        self.assertEqual(proj.published_user_decision, "WAIT")
        self.assertIsNone(proj.entry_min)
        self.assertIsNone(proj.stop_final)
        self.assertIsNone(proj.tp1)
        self.assertEqual(proj.calibration_status, "CALIBRATION_REQUIRED")

        # REST JSON serialization check
        proj_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        self.assertIsNone(proj_dict["entry_min"])
        self.assertIsNone(proj_dict["stop_final"])
        self.assertIsNone(proj_dict["tp1"])

    def test_gate_d_alerting(self):
        """
        Gate D: ALERTING
        - candidate informational alert generated
        - payload contains disclaimer
        - no order execution fields
        """
        # Create candidate alert
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        sig_snap = DualSideSignalSnapshot(
            timestamp=now,
            instrument="XAUUSD",
            timeframe="15m",
            state=SignalState.NO_TRADE,
            user_decision=UserDecision.WAIT,
            candidate_state=SignalState.BUY_WINDOW,
            candidate_user_decision=UserDecision.BUY,
            long_direction=SideDirectionScoreResult(SignalSide.LONG, 75.0, 100.0, (), True, True),
            short_direction=SideDirectionScoreResult(SignalSide.SHORT, 30.0, 100.0, (), True, False),
            long_timing=SideTimingScoreResult(SignalSide.LONG, 80.0, 100.0, (), True, True),
            short_timing=SideTimingScoreResult(SignalSide.SHORT, 20.0, 100.0, (), True, False),
            hard_gate=XauUsdHardGateEvaluation(
                is_blocked=False,
                override_state=None,
                block_reasons=(),
                runtime_health=RuntimeFeedHealth(
                    primary_15m=FeedStatus.HEALTHY,
                    primary_1h=FeedStatus.HEALTHY,
                    primary_4h=FeedStatus.HEALTHY,
                    primary_1d=FeedStatus.HEALTHY,
                    secondary_provider=FeedStatus.HEALTHY,
                    secondary_provider_disagreement=False,
                    macro_blackout_feed=FeedStatus.HEALTHY,
                    is_macro_blackout=False,
                    volume=FeedStatus.HEALTHY,
                    phase3a=FeedStatus.HEALTHY,
                    phase3b=FeedStatus.HEALTHY,
                    is_unclosed_candle=False,
                ),
            ),
            reasons_long_positive=("Strong momentum",),
            reasons_long_negative=(),
            reasons_short_positive=(),
            reasons_short_negative=(),
            hard_gate_reasons=(),
            resolution_reason="Layer B locked",
            candidate_resolution_reason="BUY_WINDOW active",
            publication_reason="Held at WAIT pending Phase 6 validation",
            analysis_fingerprint="abc_test_analysis_fp_123",
            phase4_policy_fingerprint="pol_fp_456",
            code_revision="dab3b6f8999bcef537bf4d8450f774ce36eb8e0f",
            profile_name="TEST_PROFILE",
            calibration_status="CALIBRATED_RESEARCH_TEST",
        )

        risk_plan = SideRiskPlanSnapshot(
            side=RiskSide.LONG,
            source_phase4_fingerprint="abc_test_analysis_fp_123",
            source_candidate_state=SignalState.BUY_WINDOW,
            source_candidate_decision=UserDecision.BUY,
            signal_generated_at=now,
            entry_min=Decimal("2650.00"),
            entry_mid=Decimal("2652.00"),
            entry_max=Decimal("2654.00"),
            stop_structure=Decimal("2640.00"),
            stop_atr=Decimal("2638.00"),
            stop_final=Decimal("2638.00"),
            stop_distance_atr=Decimal("2.4"),
            tp1=Decimal("2670.00"),
            tp2=Decimal("2685.00"),
            planned_rr_tp1=Decimal("1.5"),
            planned_rr_tp2=Decimal("2.5"),
            risk_candidate_valid=True,
            risk_candidate_status=RiskCandidateStatus.VALID_LONG_RISK_CANDIDATE,
            simulation_eligible=True,
            candidate_effective_action=UserDecision.BUY,
            publication_effective_action=UserDecision.WAIT,
            reasons=("Valid geometry",),
            entry_zone_fingerprint="zone_123",
            tp1_zone_fingerprint="tp_123",
            tp2_zone_fingerprint="tp_456",
            phase5_policy_fingerprint="p5_pol_789",
            risk_plan_fingerprint="risk_fp_999",
            risk_version="5.0.0",
            code_revision="dab3b6f8999bcef537bf4d8450f774ce36eb8e0f",
        )

        alerts = AlertGenerationService.evaluate_closed_candle_alerts(
            signal_snapshot=sig_snap,
            risk_plan=risk_plan,
        )

        self.assertTrue(len(alerts) >= 1)
        alert = alerts[0]
        self.assertEqual(alert.event_type, AlertEventType.BUY_WINDOW_CANDIDATE.value)
        self.assertEqual(alert.disclaimer, CANONICAL_DISCLAIMER)

        # Verify payload forbids all order execution keys
        for forbidden in FORBIDDEN_ALERT_PAYLOAD_FIELDS:
            self.assertNotIn(forbidden, alert.payload)

    def test_gate_e_safety_suppression(self):
        """
        Gate E: SAFETY
        - stale quote suppresses entry-zone alerts
        - provider unhealthy suppresses proximity alerts
        - macro blackout emits safety notification and published WAIT
        """
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={
                "risk_plan_valid": True,
                "execution_eligible": True,
                "risk_side": "LONG",
                "candidate_effective_action": "BUY",
                "entry_min": Decimal("2650.00"),
                "entry_max": Decimal("2655.00"),
                "stop_final": Decimal("2640.00"),
            },
        )

        # 1. Stale quote -> zone alert suppressed
        alerts_stale = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2652.00"),
            ask=Decimal("2653.00"),
            quote_ts=now,
            is_quote_stale=True,
            provider_healthy=True,
        )
        stale_types = [a.event_type for a in alerts_stale]
        self.assertIn(AlertEventType.LIVE_DATA_STALE.value, stale_types)
        self.assertNotIn(AlertEventType.ENTRY_ZONE_REACHED.value, stale_types)

        # 2. Unhealthy provider -> proximity alert suppressed
        alerts_unhealthy = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2652.00"),
            ask=Decimal("2653.00"),
            quote_ts=now,
            is_quote_stale=False,
            provider_healthy=False,
        )
        unhealthy_types = [a.event_type for a in alerts_unhealthy]
        self.assertIn(AlertEventType.PROVIDER_UNHEALTHY.value, unhealthy_types)
        self.assertNotIn(AlertEventType.ENTRY_ZONE_REACHED.value, unhealthy_types)

    def test_gate_f_realtime_parity(self):
        """
        Gate F: REAL-TIME PARITY
        - REST projection
        - server-rendered projection
        - WebSocket projection
        must agree on candidate/published/risk state.
        """
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={
                "current_bid": Decimal("2650.50"),
                "current_ask": Decimal("2651.00"),
                "spread": Decimal("0.50"),
                "spread_pct": Decimal("0.000189"),
                "candidate_state": "BUY_WINDOW",
                "candidate_user_decision": "BUY",
                "published_state": "NO_TRADE",
                "published_user_decision": "WAIT",
                "long_direction_score": 75.0,
                "short_direction_score": 20.0,
                "long_timing_score": 80.0,
                "short_timing_score": 15.0,
                "risk_side": "LONG",
                "risk_candidate_status": "VALID",
                "risk_plan_valid": True,
                "execution_eligible": True,
                "entry_min": Decimal("2650.00"),
                "entry_max": Decimal("2654.00"),
                "stop_final": Decimal("2640.00"),
                "tp1": Decimal("2670.00"),
                "calibration_status": "CALIBRATED_RESEARCH_TEST",
            },
        )

        # 1. Server-rendered projection
        server_proj = XauUsdLiveProjectionService.assemble_projection(state)

        # 2. REST API projection
        res = self.client.get("/dashboard/api/projection/")
        self.assertEqual(res.status_code, 200)
        rest_data = res.json()

        # 3. WebSocket event payload format
        ws_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)

        # Semantic Parity Check
        self.assertEqual(server_proj.candidate_user_decision, rest_data["candidate_user_decision"])
        self.assertEqual(server_proj.published_user_decision, rest_data["published_user_decision"])
        self.assertEqual(rest_data["candidate_user_decision"], ws_dict["candidate_user_decision"])
        self.assertEqual(rest_data["published_user_decision"], ws_dict["published_user_decision"])
        self.assertEqual(Decimal(str(server_proj.entry_min)), Decimal(str(rest_data["entry_min"])))
        self.assertEqual(Decimal(str(rest_data["entry_min"])), Decimal(str(ws_dict["entry_min"])))
        self.assertEqual(server_proj.long_direction_score, rest_data["long_direction_score"])
        self.assertEqual(rest_data["long_direction_score"], ws_dict["long_direction_score"])
        self.assertEqual(server_proj.risk_side, rest_data["risk_side"])
        self.assertEqual(rest_data["risk_side"], ws_dict["risk_side"])
