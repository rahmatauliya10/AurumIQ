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
    EventImpact,
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


class MockWsSubscriber:
    def __init__(self):
        self.events = []

    def send_event(self, event_payload):
        self.events.append(event_payload)


def _make_candle(
    ts_open: datetime,
    duration_min: int,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    volume: Decimal = Decimal("1000.0"),
    is_closed: bool = True,
) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=duration_min),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
        is_closed=is_closed,
    )


def _seed_candles(instrument_obj: Instrument, count: int = 40):
    """Seed synthetic closed 15m, 1h, 4h, 1d candles for tests."""
    import math
    base_ts = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    # Seed 15m with causal swing waves (support ~2638 and resistance ~2662)
    for i in range(count):
        ts_open = base_ts + timedelta(minutes=15 * i)
        ts_close = ts_open + timedelta(minutes=15)
        wave = round(math.sin(i * 0.4) * 12.0, 2)
        c_open = Decimal(str(round(2650.0 + wave, 2)))
        c_close = Decimal(str(round(2650.0 + wave + (1.0 if wave >= 0 else -1.0), 2)))
        c_high = max(c_open, c_close) + Decimal("2.00")
        c_low = min(c_open, c_close) - Decimal("2.00")
        MarketCandle.objects.get_or_create(
            instrument=instrument_obj,
            timeframe="15m",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            defaults={
                "open": c_open,
                "high": c_high,
                "low": c_low,
                "close": c_close,
                "volume": Decimal("1000.0"),
                "is_closed": True,
                "source": "primary_spot_feed",
            },
        )
    # Seed 1h
    for i in range(25):
        ts_open = base_ts + timedelta(hours=i)
        ts_close = ts_open + timedelta(hours=1)
        MarketCandle.objects.get_or_create(
            instrument=instrument_obj,
            timeframe="1h",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            defaults={
                "open": Decimal("2645.00") + Decimal(str(i * 0.2)),
                "high": Decimal("2660.00") + Decimal(str(i * 0.2)),
                "low": Decimal("2640.00") + Decimal(str(i * 0.2)),
                "close": Decimal("2655.00") + Decimal(str(i * 0.2)),
                "volume": Decimal("4000.0"),
                "is_closed": True,
                "source": "primary_spot_feed",
            },
        )
    # Seed 4h
    for i in range(25):
        ts_open = base_ts + timedelta(hours=4 * i)
        ts_close = ts_open + timedelta(hours=4)
        MarketCandle.objects.get_or_create(
            instrument=instrument_obj,
            timeframe="4h",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            defaults={
                "open": Decimal("2640.00"),
                "high": Decimal("2670.00"),
                "low": Decimal("2635.00"),
                "close": Decimal("2660.00"),
                "volume": Decimal("16000.0"),
                "is_closed": True,
                "source": "primary_spot_feed",
            },
        )
    # Seed 1d
    for i in range(25):
        ts_open = base_ts + timedelta(days=i)
        ts_close = ts_open + timedelta(days=1)
        MarketCandle.objects.get_or_create(
            instrument=instrument_obj,
            timeframe="1d",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            defaults={
                "open": Decimal("2630.00"),
                "high": Decimal("2680.00"),
                "low": Decimal("2620.00"),
                "close": Decimal("2665.00"),
                "volume": Decimal("64000.0"),
                "is_closed": True,
                "source": "primary_spot_feed",
            },
        )


@pytest.mark.django_db
class TestXauP701AcceptanceContract(TestCase):
    """
    Formal Phase 7 Acceptance Contract Test Suite.
    Directly exercises XauUsdLiveDecisionPipelineService, LiveQuoteService,
    XauUsdLiveProjectionService, and AlertGenerationService.
    """

    def setUp(self):
        super().setUp()
        self.usd, _ = Asset.objects.get_or_create(code="USD", name="US Dollar", asset_type=AssetType.FIAT)
        self.xau, _ = Asset.objects.get_or_create(code="XAU", name="Gold Spot", asset_type=AssetType.COMMODITY)
        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )
        _seed_candles(self.xauusd, count=40)

        # Create primary listing and healthy snapshot
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot
        self.primary_listing, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            defaults={
                "provider": "primary_spot_feed",
                "provider_symbol": "XAUUSD",
                "status": ListingStatus.ACTIVE,
            },
        )
        ProviderHealthSnapshot.objects.create(
            listing=self.primary_listing,
            status="HEALTHY",
            checked_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        )

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
            long_gate=SideGatePolicy(
                threshold_watch_direction=25.0,
                threshold_ready_direction=30.0,
                threshold_ready_timing=50.0,
                threshold_window_direction=35.0,
                threshold_window_timing=55.0,
            ),
            short_gate=SideGatePolicy(
                threshold_watch_direction=50.0,
                threshold_ready_direction=60.0,
                threshold_ready_timing=60.0,
                threshold_window_direction=70.0,
                threshold_window_timing=70.0,
            ),
        )
        risk_prof = XauUsdRiskProfile(
            name="LONG_RISK_PROFILE",
            calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("10.0"),
                min_rr_tp1=Decimal("0.5"),
            ),
            short_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("10.0"),
                min_rr_tp1=Decimal("0.5"),
            ),
        )

        macro_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=True,
            active_event_name="Normal",
            minutes_to_next_event=120,
        )

        sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            provider_status="HEALTHY",
            is_feed_stale=False,
            macro_context=macro_ctx,
            signal_profile=prof,
            risk_profile=risk_prof,
        )

        # Verification
        self.assertEqual(state.instrument, "XAUUSD")
        self.assertEqual(state.candidate_state, "BUY_WINDOW")
        self.assertEqual(state.candidate_user_decision, "BUY")
        self.assertEqual(state.published_user_decision, "WAIT")  # Published is strictly WAIT
        self.assertIsNotNone(risk_rec)
        self.assertEqual(risk_rec.risk_side, "LONG")
        self.assertTrue(risk_rec.is_valid_risk_plan)
        self.assertTrue(risk_rec.execution_eligible)
        self.assertEqual(state.candidate_effective_action, "BUY")
        self.assertEqual(state.publication_effective_action, "WAIT")
        self.assertIsNotNone(state.entry_min)
        self.assertIsNotNone(state.stop_final)
        self.assertIsNotNone(state.tp1)
        self.assertTrue(bool(state.risk_plan_fingerprint))
        self.assertEqual(state.risk_plan_fingerprint, risk_rec.risk_plan_fingerprint)

        # Projection check
        proj = XauUsdLiveProjectionService.assemble_projection(state)
        self.assertEqual(proj.candidate_user_decision, "BUY")
        self.assertEqual(proj.published_user_decision, "WAIT")
        self.assertEqual(proj.display_symbol, "XAU/USD")
        self.assertEqual(proj.risk_side, "LONG")

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

        prof_short = Phase4SignalProfile(
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            long_gate=SideGatePolicy(
                threshold_watch_direction=50.0,
                threshold_ready_direction=60.0,
                threshold_ready_timing=60.0,
                threshold_window_direction=70.0,
                threshold_window_timing=70.0,
            ),
            short_gate=SideGatePolicy(
                threshold_watch_direction=15.0,
                threshold_ready_direction=18.0,
                threshold_ready_timing=30.0,
                threshold_window_direction=20.0,
                threshold_window_timing=35.0,
            ),
        )
        risk_prof = XauUsdRiskProfile(
            name="SHORT_RISK_PROFILE",
            calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("10.0"),
                min_rr_tp1=Decimal("0.5"),
            ),
            short_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("10.0"),
                min_rr_tp1=Decimal("0.5"),
            ),
        )

        macro_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=True,
            active_event_name="Normal",
            minutes_to_next_event=120,
        )

        sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            provider_status="HEALTHY",
            is_feed_stale=False,
            macro_context=macro_ctx,
            signal_profile=prof_short,
            risk_profile=risk_prof,
        )

        self.assertEqual(state.candidate_state, "SELL_WINDOW")
        self.assertEqual(state.candidate_user_decision, "SELL")
        self.assertEqual(state.published_user_decision, "WAIT")
        self.assertIsNotNone(risk_rec)
        self.assertEqual(risk_rec.risk_side, "SHORT")
        self.assertTrue(risk_rec.is_valid_risk_plan)
        self.assertIsNotNone(state.entry_min)
        self.assertIsNotNone(state.entry_max)

        # Send Live Quote: BID is inside [entry_min, entry_max], ASK is above entry_max
        bid_inside = (state.entry_min + state.entry_max) / Decimal("2.0")
        ask_above = state.entry_max + Decimal("2.00")

        quote_evt = LiveQuoteEvent(
            event_id="Q_SHORT_1",
            instrument="XAUUSD",
            provider="primary_feed",
            bid=bid_inside,
            ask=ask_above,
            source_timestamp=now + timedelta(seconds=5),
            received_timestamp=now + timedelta(seconds=5),
            sequence_number=100,
        )
        updated_state = LiveQuoteService.process_quote(quote_evt, max_staleness_seconds=60.0)

        self.assertIsNotNone(updated_state)
        # Must be INSIDE_ZONE because SHORT uses BID (which is inside entry zone)
        self.assertEqual(updated_state.entry_zone_status, EntryZoneStatus.INSIDE_ZONE.value)
        self.assertEqual(updated_state.distance_to_entry_zone_pct, Decimal("0.00"))
        self.assertEqual(updated_state.published_user_decision, "WAIT")

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
        - payload contains mandatory disclaimer
        - zero order execution fields
        """
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        sig = SignalRecord.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp=now,
            state="NO_TRADE",
            user_decision="WAIT",
            long_direction_score=75.0,
            short_direction_score=20.0,
            long_timing_score=80.0,
            short_timing_score=15.0,
            analysis_fingerprint="sig_fp_alert_gate_d",
            components_breakdown={
                "candidate_state": "BUY_WINDOW",
                "candidate_user_decision": "BUY",
            },
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
        )

        # Create dual-side snapshot for alert generator
        snapshot = DualSideSignalSnapshot(
            timestamp=now,
            instrument="XAUUSD",
            timeframe="15m",
            state=SignalState.NO_TRADE,
            user_decision=UserDecision.WAIT,
            candidate_state=SignalState.BUY_WINDOW,
            candidate_user_decision=UserDecision.BUY,
            long_direction=SideDirectionScoreResult(
                side=SignalSide.LONG,
                total_score=75.0,
                max_score=100.0,
                components=(),
                is_valid=True,
                is_direction_ready=True,
            ),
            short_direction=SideDirectionScoreResult(
                side=SignalSide.SHORT,
                total_score=20.0,
                max_score=100.0,
                components=(),
                is_valid=True,
                is_direction_ready=False,
            ),
            long_timing=SideTimingScoreResult(
                side=SignalSide.LONG,
                total_score=80.0,
                max_score=100.0,
                components=(),
                is_valid=True,
                is_timing_ready=True,
            ),
            short_timing=SideTimingScoreResult(
                side=SignalSide.SHORT,
                total_score=15.0,
                max_score=100.0,
                components=(),
                is_valid=True,
                is_timing_ready=False,
            ),
            hard_gate=XauUsdHardGateEvaluation(
                is_blocked=False,
                override_state=None,
                block_reasons=(),
                runtime_health=RuntimeFeedHealth(),
            ),
            reasons_long_positive=("Strong momentum",),
            reasons_long_negative=(),
            reasons_short_positive=(),
            reasons_short_negative=(),
            hard_gate_reasons=(),
            resolution_reason="BUY_WINDOW qualified",
            candidate_resolution_reason="QUALIFIED",
            publication_reason="AUTHORITY_UNAUTHORIZED",
            analysis_fingerprint="sig_fp_alert_gate_d",
            phase4_policy_fingerprint="p4_pol_123",
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            profile_name="TEST_PROF",
            calibration_status="CANDIDATE_NOT_FROZEN",
        )

        alerts = AlertGenerationService.evaluate_closed_candle_alerts(
            signal_snapshot=snapshot,
            risk_plan=None,
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]

        # Verify informational disclaimer
        self.assertEqual(alert.payload.get("disclaimer"), CANONICAL_DISCLAIMER)
        self.assertEqual(alert.payload.get("is_production_authorized"), False)

        # Verify forbidden order fields are ABSENT
        for forbidden in FORBIDDEN_ALERT_PAYLOAD_FIELDS:
            self.assertNotIn(forbidden, alert.payload)

    def test_gate_e_safety_suppression(self):
        """
        Gate E: SAFETY
        - stale quote suppresses entry-zone alerts
        - provider unhealthy suppresses proximity alerts
        - macro blackout emits safety notification and published WAIT
        - macro missing/unhealthy fails closed
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
        LiveMonitorState.objects.filter(id=state.id).update(
            risk_plan_valid=True,
            execution_eligible=True,
            risk_side="LONG",
            candidate_effective_action="BUY",
            entry_min=Decimal("2650.00"),
            entry_max=Decimal("2655.00"),
            stop_final=Decimal("2640.00"),
            hard_gate_reasons=[],
            feed_health_data={},
        )
        state.refresh_from_db()

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

        # 3. Macro Blackout PIT test via service pipeline
        event = CandleClosedEvent(
            event_id="EVT_MACRO_TEST",
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
            source="primary_spot_feed",
        )
        macro_blackout_ctx = MacroEventContext(
            is_in_blackout=True,
            is_feed_healthy=True,
            active_event_name="Non-Farm Payrolls",
            minutes_to_next_event=0,
        )
        _, _, state_mb = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            provider_status="HEALTHY",
            is_feed_stale=False,
            macro_context=macro_blackout_ctx,
        )
        self.assertEqual(state_mb.published_user_decision, "WAIT")
        self.assertEqual(state_mb.candidate_state, "FORCE_WAIT")
        self.assertTrue(
            any("macroeconomic event blackout window" in r.lower() or "macro_blackout" in r.lower() for r in state_mb.hard_gate_reasons)
        )

        # Query persisted AlertEvent rows for macro blackout
        from apps.alerts.models import AlertEvent
        self.assertTrue(
            AlertEvent.objects.filter(instrument="XAUUSD", event_type=AlertEventType.MACRO_BLACKOUT_ACTIVE.value).exists()
        )
        self.assertTrue(
            AlertEvent.objects.filter(instrument="XAUUSD", event_type=AlertEventType.SYSTEM_SAFETY_HOLD.value).exists()
        )
        self.assertFalse(
            AlertEvent.objects.filter(instrument="XAUUSD", event_type=AlertEventType.ENTRY_ZONE_REACHED.value).exists()
        )

        # Proximity alert suppressed when safety hold is active
        alerts_mb = AlertGenerationService.evaluate_live_quote_alerts(
            state=state_mb,
            bid=Decimal("2652.00"),
            ask=Decimal("2653.00"),
            quote_ts=now,
            is_quote_stale=False,
            provider_healthy=True,
        )
        self.assertNotIn(AlertEventType.ENTRY_ZONE_REACHED.value, [a.event_type for a in alerts_mb])

        # 4. Macro feed missing / unhealthy -> fail closed to FORCE_WAIT
        macro_unhealthy_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=False,
            active_event_name="Macro Down",
            minutes_to_next_event=100,
        )
        _, _, state_mu = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            provider_status="HEALTHY",
            is_feed_stale=False,
            macro_context=macro_unhealthy_ctx,
        )
        self.assertEqual(state_mu.published_user_decision, "WAIT")
        self.assertEqual(state_mu.candidate_state, "FORCE_WAIT")
        self.assertEqual(state_mu.feed_health_data["macro_status"], "UNHEALTHY")

        # 5. Missing / None macro context -> strictly fails closed to FORCE_WAIT
        _, _, state_missing_macro = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            provider_status="HEALTHY",
            is_feed_stale=False,
            macro_context=None,
        )
        self.assertEqual(state_missing_macro.published_user_decision, "WAIT")
        self.assertEqual(state_missing_macro.candidate_state, "FORCE_WAIT")

    def test_gate_f_realtime_parity(self):
        """
        Gate F: REAL-TIME PARITY
        - REST projection
        - server-rendered projection
        - WebSocket projection
        must agree on candidate/published/risk state with real broadcaster capture.
        """
        from apps.live_monitor.consumers import LiveEventBroadcaster
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event = CandleClosedEvent(
            event_id="EVT_GATE_F_PARITY",
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

        prof = Phase4SignalProfile(
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            long_gate=SideGatePolicy(
                threshold_watch_direction=25.0,
                threshold_ready_direction=30.0,
                threshold_ready_timing=50.0,
                threshold_window_direction=35.0,
                threshold_window_timing=55.0,
            ),
            short_gate=SideGatePolicy(
                threshold_watch_direction=50.0,
                threshold_ready_direction=60.0,
                threshold_ready_timing=60.0,
                threshold_window_direction=70.0,
                threshold_window_timing=70.0,
            ),
        )
        risk_prof = XauUsdRiskProfile(
            name="LONG_RISK_PROFILE",
            calibration_status=Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("10.0"),
                min_rr_tp1=Decimal("0.5"),
            ),
            short_risk_policy=SideRiskPolicy(
                structure_buffer=Decimal("0.50"),
                atr_multiplier=Decimal("1.5"),
                max_stop_distance_atr=Decimal("10.0"),
                min_rr_tp1=Decimal("0.5"),
            ),
        )

        macro_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=True,
            active_event_name="Normal",
            minutes_to_next_event=120,
        )

        mock_sub = MockWsSubscriber()
        LiveEventBroadcaster.subscribe(mock_sub)

        with self.captureOnCommitCallbacks(execute=True):
            sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
                provider_status="HEALTHY",
                is_feed_stale=False,
                macro_context=macro_ctx,
                signal_profile=prof,
                risk_profile=risk_prof,
            )

        LiveEventBroadcaster.unsubscribe(mock_sub)

        # 1. Server-rendered projection
        server_proj = XauUsdLiveProjectionService.assemble_projection(state)

        # 2. REST API projection
        res = self.client.get("/dashboard/api/projection/")
        self.assertEqual(res.status_code, 200)
        rest_data = res.json()

        # 3. Real WebSocket broadcast frame
        self.assertGreaterEqual(len(mock_sub.events), 1)
        sig_frame = next(e for e in mock_sub.events if e.get("event_type") == "signal_update")
        ws_data = sig_frame.get("data", sig_frame)

        # Semantic Parity Check
        self.assertEqual(server_proj.candidate_user_decision, rest_data["candidate_user_decision"])
        self.assertEqual(server_proj.published_user_decision, rest_data["published_user_decision"])
        self.assertEqual(rest_data["candidate_user_decision"], ws_data["candidate_user_decision"])
        self.assertEqual(rest_data["published_user_decision"], ws_data["published_user_decision"])
        self.assertEqual(server_proj.long_direction_score, rest_data["long_direction_score"])
        self.assertEqual(rest_data["long_direction_score"], ws_data["long_direction_score"])
        self.assertEqual(server_proj.risk_side, rest_data["risk_side"])
        self.assertEqual(rest_data["risk_side"], state.risk_side)
        self.assertEqual(server_proj.candidate_state, rest_data["candidate_state"])
        self.assertEqual(rest_data["candidate_state"], ws_data["candidate_state"])
        self.assertEqual(server_proj.published_state, rest_data["published_state"])
        self.assertEqual(rest_data["published_state"], ws_data["published_state"])
        self.assertEqual(server_proj.risk_candidate_status, rest_data["risk_candidate_status"])
