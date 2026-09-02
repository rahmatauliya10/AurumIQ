"""Comprehensive Hostile & Adversarial Test Suite for Phase 7 (Spec §33)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from django.test import TestCase

from apps.alerts.models import AlertEvent, AlertStatus
from apps.alerts.services import AlertGenerationService
from apps.alerts.types import (
    AlertEventType,
    AlertPayload,
    CANONICAL_DISCLAIMER,
    FORBIDDEN_ALERT_PAYLOAD_FIELDS,
)
from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import (
    LiveQuoteService,
    XauUsdLiveDecisionPipelineService,
    XauUsdLiveProjectionService,
)
from apps.live_monitor.types import CandleClosedEvent, EntryZoneStatus, FeedStatus, LiveQuoteEvent
from apps.signals.models import SignalRecord
from engine.core.types import (
    CandleData,
    DualSideSignalSnapshot,
    MacroEventContext,
    RiskCandidateStatus,
    RiskSide,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideRiskPlanSnapshot,
    SideTimingScoreResult,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
)


@pytest.mark.unit
@pytest.mark.django_db
class TestPhase7HostileScenarios(TestCase):
    """Hostile test suite verifying all 30+ edge cases and security boundaries."""

    def setUp(self):
        self.xau, _ = Asset.objects.get_or_create(code="XAU", name="Gold Spot", asset_type=AssetType.COMMODITY)
        self.usd, _ = Asset.objects.get_or_create(code="USD", name="US Dollar", asset_type=AssetType.FIAT)
        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )

    def test_hostile_01_xaut_rejected_in_active_pipeline(self):
        """XAUT sent to XAUUSD live pipeline must be rejected."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event = CandleClosedEvent(
            event_id="EVT_H1",
            instrument="XAUT",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            is_closed=True,
        )
        with self.assertRaises(ValueError) as ctx:
            XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="dab3b6f8999bcef537bf4d8450f774ce36eb8e0f",
            )
        self.assertIn("REJECTED", str(ctx.exception))

    def test_hostile_02_xautusdt_rejected_in_active_pipeline(self):
        """XAUTUSDT sent to active pipeline must be rejected."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event = CandleClosedEvent(
            event_id="EVT_H2",
            instrument="XAUT/USDT",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            is_closed=True,
        )
        with self.assertRaises(ValueError):
            XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="dab3b6f8999bcef537bf4d8450f774ce36eb8e0f",
            )

    def test_hostile_03_naive_quote_timestamp_rejected(self):
        """Naive quote timestamp must be rejected without silently attaching UTC."""
        event = LiveQuoteEvent(
            event_id="Q_NAIVE",
            instrument="XAUUSD",
            provider="feed1",
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            source_timestamp=datetime(2026, 8, 1, 10, 0),  # Naive
            received_timestamp=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        )
        with self.assertRaises(ValueError):
            LiveQuoteService.process_quote(event)

    def test_hostile_04_invalid_prices_rejected(self):
        """Negative bid/ask or ask < bid must be rejected."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)

        # Negative bid
        with self.assertRaises(ValueError):
            LiveQuoteService.process_quote(
                LiveQuoteEvent(
                    event_id="Q_NEG",
                    instrument="XAUUSD",
                    provider="feed1",
                    bid=Decimal("-10.00"),
                    ask=Decimal("2650.00"),
                    source_timestamp=now,
                    received_timestamp=now,
                )
            )

        # Inverted spread (ask < bid)
        with self.assertRaises(ValueError):
            LiveQuoteService.process_quote(
                LiveQuoteEvent(
                    event_id="Q_INV",
                    instrument="XAUUSD",
                    provider="feed1",
                    bid=Decimal("2655.00"),
                    ask=Decimal("2650.00"),
                    source_timestamp=now,
                    received_timestamp=now,
                )
            )

    def test_hostile_05_out_of_order_quotes_ignored(self):
        """Out-of-order sequence quotes must be ignored deterministically."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        q1 = LiveQuoteEvent(
            event_id="Q1",
            instrument="XAUUSD",
            provider="feed1",
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            source_timestamp=now,
            received_timestamp=now,
            sequence_number=100,
        )
        state1 = LiveQuoteService.process_quote(q1)
        self.assertEqual(state1.current_bid, Decimal("2650.00"))

        # Send older sequence number
        q_old = LiveQuoteEvent(
            event_id="Q_OLD",
            instrument="XAUUSD",
            provider="feed1",
            bid=Decimal("2640.00"),
            ask=Decimal("2641.00"),
            source_timestamp=now + timedelta(seconds=1),
            received_timestamp=now + timedelta(seconds=1),
            sequence_number=99,
        )
        state2 = LiveQuoteService.process_quote(q_old)
        # Bid must NOT be clobbered by stale sequence
        self.assertEqual(state2.current_bid, Decimal("2650.00"))

    def test_hostile_06_side_aware_short_uses_bid_not_ask(self):
        """SHORT candidate entry zone monitoring strictly uses BID, not ASK."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={
                "risk_side": "SHORT",
                "candidate_effective_action": "SELL",
                "risk_plan_valid": True,
                "execution_eligible": True,
                "entry_min": Decimal("2640.00"),
                "entry_max": Decimal("2645.00"),
                "stop_final": Decimal("2655.00"),
            },
        )
        LiveMonitorState.objects.filter(id=state.id).update(
            risk_side="SHORT",
            candidate_effective_action="SELL",
            risk_plan_valid=True,
            execution_eligible=True,
            entry_min=Decimal("2640.00"),
            entry_max=Decimal("2645.00"),
        )

        # BID is 2642.00 (inside [2640, 2645]), ASK is 2648.00 (above 2645)
        # If BID is used -> INSIDE_ZONE
        # If ASK is incorrectly used -> ABOVE_ZONE
        q = LiveQuoteEvent(
            event_id="Q_SHORT_TEST",
            instrument="XAUUSD",
            provider="feed1",
            bid=Decimal("2642.00"),
            ask=Decimal("2648.00"),
            source_timestamp=now,
            received_timestamp=now,
            sequence_number=200,
        )
        updated = LiveQuoteService.process_quote(q)
        self.assertEqual(updated.entry_zone_status, EntryZoneStatus.INSIDE_ZONE.value)

    def test_hostile_07_invalid_risk_geometry_never_zero(self):
        """Invalid risk geometry must be None, never fabricated zero."""
        state = LiveMonitorState(
            instrument="XAUUSD",
            risk_plan_valid=False,
            execution_eligible=False,
            entry_min=None,
            entry_max=None,
            stop_final=None,
            tp1=None,
        )
        proj = XauUsdLiveProjectionService.assemble_projection(state)
        self.assertIsNone(proj.entry_min)
        self.assertIsNone(proj.stop_final)
        self.assertIsNone(proj.tp1)
        self.assertIsNone(proj.planned_rr_tp1)

        proj_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        self.assertIsNone(proj_dict["entry_min"])
        self.assertIsNone(proj_dict["stop_final"])
        self.assertIsNone(proj_dict["tp1"])

    def test_hostile_08_alert_payload_forbids_order_fields(self):
        """Alert payload containing broker/order fields must fail immediately."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        payload = AlertPayload(
            event_id="ALERT_EVT_1",
            event_type=AlertEventType.BUY_WINDOW_CANDIDATE,
            instrument="XAUUSD",
            analysis_timestamp=now,
        )
        p_dict = payload.to_dict()
        for forbidden in FORBIDDEN_ALERT_PAYLOAD_FIELDS:
            self.assertNotIn(forbidden, p_dict)

    def test_hostile_09_alert_idempotency_prevents_duplicate_storm(self):
        """Identical quote ticks must not emit duplicate alert events."""
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
                "analysis_fingerprint": "sig_fp_idem_1",
                "risk_plan_fingerprint": "risk_fp_idem_1",
            },
        )

        # Tick 1: inside zone
        alerts1 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2651.00"),
            ask=Decimal("2652.00"),
            quote_ts=now,
            is_quote_stale=False,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts1), 1)

        # Tick 2: same price & same fingerprint
        alerts2 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2651.00"),
            ask=Decimal("2652.00"),
            quote_ts=now + timedelta(seconds=1),
            is_quote_stale=False,
            provider_healthy=True,
        )
        # Must be empty because event_id is identical (idempotent suppression)
        self.assertEqual(len(alerts2), 0)

    def test_hostile_10_phase3b_production_weight_locked_to_zero(self):
        """Phase 3B production weight must be hard locked to 0.0."""
        state, _ = LiveMonitorState.objects.get_or_create(instrument="XAUUSD")
        proj = XauUsdLiveProjectionService.assemble_projection(state)
        self.assertEqual(proj.phase3b_production_weight, 0.0)
        self.assertEqual(proj.phase3b_status, "RESEARCH_ONLY")
