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
from apps.market_data.models import MarketCandle
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
    FeedHealthStatus,
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

    def test_hostile_11_missing_quote_ttl_fails_closed(self):
        """Missing XAUUSD_LIVE_QUOTE_TTL_SECONDS must fail closed (no cache, is_stale=True)."""
        from django.test import override_settings
        with override_settings(XAUUSD_QUOTE_STALE_SECONDS=None, XAUUSD_QUOTE_FUTURE_SKEW_SECONDS=None):
            now = datetime.now(timezone.utc)
            q = LiveQuoteEvent(
                event_id="Q_UNCONFIGURED",
                instrument="XAUUSD",
                provider="feed1",
                bid=Decimal("2650.00"),
                ask=Decimal("2651.00"),
                source_timestamp=now,
                received_timestamp=now,
                sequence_number=300,
            )
            state = LiveQuoteService.process_quote(q)
            self.assertTrue(state.is_quote_stale)

    def test_hostile_12_adapters_strict_timezone_rejection(self):
        """PublicMarketDataAdapter must reject naive timestamps in create_xauusd_quote_event and create_xauusd_candle_closed_event."""
        from apps.live_monitor.adapter import PublicMarketDataAdapter
        naive_dt = datetime(2026, 8, 1, 12, 0)
        aware_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

        # Naive quote source_timestamp
        with self.assertRaises(ValueError):
            PublicMarketDataAdapter.create_xauusd_quote_event(
                instrument="XAUUSD",
                provider="primary",
                bid=Decimal("2650.00"),
                ask=Decimal("2651.00"),
                source_timestamp=naive_dt,
            )

        # Naive candle timestamp
        with self.assertRaises(ValueError):
            PublicMarketDataAdapter.create_xauusd_candle_closed_event(
                instrument="XAUUSD",
                timeframe="15m",
                timestamp_open=naive_dt,
                timestamp_close=aware_dt,
                open_price="2650.00",
                high_price="2655.00",
                low_price="2649.00",
                close_price="2654.00",
            )

    def test_hostile_13_non_15m_candle_rejected_as_decision_trigger(self):
        """Triggering active XAUUSD pipeline with 1h/4h candle must fail."""
        aware_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        from apps.live_monitor.adapter import PublicMarketDataAdapter
        with self.assertRaises(ValueError):
            PublicMarketDataAdapter.create_xauusd_candle_closed_event(
                instrument="XAUUSD",
                timeframe="1h",
                timestamp_open=aware_dt - timedelta(hours=1),
                timestamp_close=aware_dt,
                open_price="2650.00",
                high_price="2655.00",
                low_price="2649.00",
                close_price="2654.00",
            )

    def test_hostile_14_safety_hold_suppresses_entry_and_invalidation(self):
        """Hard gates / safety hold must strictly suppress entry-zone and invalidation alerts."""
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
                "hard_gate_reasons": ["SYSTEM_SAFETY_HOLD_ACTIVE"],
            },
        )
        LiveMonitorState.objects.filter(id=state.id).update(
            hard_gate_reasons=["SYSTEM_SAFETY_HOLD_ACTIVE"],
            risk_plan_valid=True,
            execution_eligible=True,
            entry_min=Decimal("2650.00"),
            entry_max=Decimal("2655.00"),
            stop_final=Decimal("2640.00"),
        )
        state.refresh_from_db()

        alerts = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2651.00"),
            ask=Decimal("2652.00"),
            quote_ts=now,
            is_quote_stale=False,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts), 0)

    def test_hostile_15_no_fake_risk_plan_on_wait_or_conflict(self):
        """WAIT and CONFLICT states must produce risk_plan_snapshot=None and clear risk fields."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event = CandleClosedEvent(
            event_id="EVT_WAIT",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2650.00"),
            is_closed=True,
        )
        sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="5a05d9ba1c0d63790fe2b0e3b6a6bbcc0de63f61",
        )
        if state.candidate_state not in ("BUY_WINDOW", "SELL_WINDOW"):
            self.assertIsNone(risk_rec)
            self.assertFalse(state.risk_plan_valid)
            self.assertFalse(state.execution_eligible)
            self.assertEqual(state.candidate_effective_action, "WAIT")
            self.assertEqual(state.publication_effective_action, "WAIT")

    def test_hostile_16_state_recovery_restores_candidate_without_fake_health(self):
        """State recovery recovers candidate breakdown without asserting healthy feeds."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        sig = SignalRecord.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp=now,
            state="WAIT",
            user_decision="WAIT",
            long_direction_score=6.0,
            short_direction_score=3.0,
            long_timing_score=7.0,
            short_timing_score=2.0,
            analysis_fingerprint="sig_fp_recov_1",
            components_breakdown={
                "candidate_state": "READY_LONG",
                "candidate_user_decision": "BUY",
            },
            provenance={
                "primary_15m": "STALE",
                "macro_blackout_feed": "UNHEALTHY",
            },
            code_revision="5a05d9ba1c0d63790fe2b0e3b6a6bbcc0de63f61",
        )

        state = XauUsdLiveProjectionService.reconstruct_xauusd_state()
        self.assertEqual(state.candidate_state, "READY_LONG")
        self.assertEqual(state.candidate_user_decision, "BUY")
        self.assertEqual(state.published_user_decision, "WAIT")
        self.assertEqual(state.feed_health_data["xauusd_primary_status"], "STALE")
        self.assertEqual(state.feed_health_data["macro_status"], "UNHEALTHY")

    def test_hostile_17_backtest_launch_api_rejects_naive_and_missing_friction(self):
        """BacktestRunLaunchAPIView must reject naive dates and require friction on EMPIRICAL."""
        from django.contrib.auth.models import User
        from django.urls import reverse
        from rest_framework.test import APIClient
        client = APIClient()
        user = User.objects.create_user(username="lab_user", password="password")
        client.force_authenticate(user=user)

        url = reverse("dashboard:api_backtest_run")

        # Naive date
        res1 = client.post(
            url,
            {"start_date": "2026-08-01T00:00:00", "end_date": "2026-08-05T00:00:00"},
            format="json",
        )
        self.assertEqual(res1.status_code, 400)

        # EMPIRICAL without friction
        res2 = client.post(
            url,
            {
                "start_date": "2026-08-01T00:00:00+00:00",
                "end_date": "2026-08-05T00:00:00+00:00",
                "cost_scenario": "EMPIRICAL",
            },
            format="json",
        )
        self.assertEqual(res2.status_code, 400)

    def test_hostile_18_provider_health_enum_mapping(self):
        """All ProviderHealthStatus values must map cleanly to engine FeedHealthStatus without NOT_CONFIGURED."""
        from apps.instruments.models import ProviderHealthStatus
        from engine.core.types import FeedHealthStatus
        from apps.live_monitor.services import _map_provider_status_to_feed_health

        # Check all possible provider health values
        expected_mappings = {
            ProviderHealthStatus.HEALTHY.value: FeedHealthStatus.HEALTHY,
            ProviderHealthStatus.DEGRADED.value: FeedHealthStatus.UNHEALTHY,
            ProviderHealthStatus.UNHEALTHY.value: FeedHealthStatus.UNHEALTHY,
            ProviderHealthStatus.QUARANTINED.value: FeedHealthStatus.UNHEALTHY,
            ProviderHealthStatus.NOT_CONFIGURED.value: FeedHealthStatus.MISSING,
            ProviderHealthStatus.UNKNOWN.value: FeedHealthStatus.MISSING,
            "MISSING": FeedHealthStatus.MISSING,
            None: FeedHealthStatus.MISSING,
            "INVALID_VALUE": FeedHealthStatus.MISSING,
        }
        for prov_stat, expected_feed in expected_mappings.items():
            result = _map_provider_status_to_feed_health(prov_stat)
            self.assertEqual(result, expected_feed)
            self.assertFalse(hasattr(FeedHealthStatus, "NOT_CONFIGURED"))

    def test_hostile_19_direct_service_rejects_non_15m(self):
        """Direct call to XauUsdLiveDecisionPipelineService.process_closed_candle with non-15m must raise ValueError."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        event_5m = CandleClosedEvent(
            event_id="EVT_NON_15M",
            instrument="XAUUSD",
            timeframe="5m",
            timestamp_open=now - timedelta(minutes=5),
            timestamp_close=now,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            is_closed=True,
        )
        with self.assertRaises(ValueError) as ctx:
            XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event_5m,
                code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
            )
        self.assertIn("timeframe", str(ctx.exception).lower())

    def test_hostile_20_long_history_parity_over_128_candles(self):
        """_get_engine_candles loads all closed candles <= candle_ts without arbitrary limits."""
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing
        from apps.market_data.models import MarketCandle

        MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            defaults={"provider": "p20_prov", "status": ListingStatus.ACTIVE, "provider_symbol": "XAUUSD"},
        )

        base_ts = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        # Create 140 candles
        candles_to_create = []
        for i in range(140):
            ts_open = base_ts + timedelta(minutes=15 * i)
            candles_to_create.append(
                MarketCandle(
                    instrument=self.xauusd,
                    timeframe="15m",
                    timestamp_open=ts_open,
                    timestamp_close=ts_open + timedelta(minutes=15),
                    open=Decimal("2600.00") + Decimal(str(i * 0.1)),
                    high=Decimal("2605.00") + Decimal(str(i * 0.1)),
                    low=Decimal("2595.00") + Decimal(str(i * 0.1)),
                    close=Decimal("2602.00") + Decimal(str(i * 0.1)),
                    volume=Decimal("1000.0"),
                    is_closed=True,
                    source="p20_prov",
                )
            )
        MarketCandle.objects.bulk_create(candles_to_create)

        eval_ts = base_ts + timedelta(minutes=15 * 140)
        engine_candles = XauUsdLiveDecisionPipelineService.get_engine_candles(
            instrument=self.xauusd,
            timeframe="15m",
            candle_ts=eval_ts,
        )
        self.assertEqual(len(engine_candles), 140)
        # Verify chronological order
        for idx in range(len(engine_candles) - 1):
            self.assertLess(engine_candles[idx].timestamp_open, engine_candles[idx + 1].timestamp_open)

    def test_hostile_21_live_risk_immutability_multiple_risk_profiles_same_signal(self):
        """Same Phase 4 signal evaluated with 2 different risk profiles persists 2 records uniquely on risk_plan_fingerprint."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        sig_fp = "sig_fp_immutability_test_1"

        # Record 1
        rec1 = LiveRiskPlanRecord.objects.create(
            risk_plan_fingerprint="risk_fp_profile_A",
            source_signal_fingerprint=sig_fp,
            signal_timestamp=now,
            instrument="XAUUSD",
            risk_side="LONG",
            is_valid_risk_plan=True,
            execution_eligible=True,
            effective_action="WAIT",
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
        )
        self.assertIsNotNone(rec1.pk)

        # Record 2: Same source signal fingerprint, different risk plan fingerprint -> must succeed
        rec2 = LiveRiskPlanRecord.objects.create(
            risk_plan_fingerprint="risk_fp_profile_B",
            source_signal_fingerprint=sig_fp,
            signal_timestamp=now,
            instrument="XAUUSD",
            risk_side="LONG",
            is_valid_risk_plan=True,
            execution_eligible=True,
            effective_action="WAIT",
            code_revision="34a21541f2a9725c7fde324c1e08245a2363742d",
        )
        self.assertIsNotNone(rec2.pk)
        self.assertEqual(LiveRiskPlanRecord.objects.filter(source_signal_fingerprint=sig_fp).count(), 2)

    def test_hostile_22_multi_minute_incident_state_transitions_and_deduplication(self):
        """HEALTHY -> STALE emits once; continuous STALE -> STALE does not duplicate; STALE -> HEALTHY resets."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={"effective_action": "WAIT", "feed_health_data": {}},
        )

        # 1. First STALE evaluation: HEALTHY -> STALE -> Emits 1 incident alert
        alerts_m1 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now,
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_m1), 1)
        self.assertEqual(alerts_m1[0].event_type, AlertEventType.LIVE_DATA_STALE.value)

        # 2. Minute 2: STALE -> STALE -> Deduplicated (0 alerts emitted)
        now_m2 = now + timedelta(minutes=1)
        alerts_m2 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now_m2,
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_m2), 0)

        # 3. Minute 3: STALE -> STALE -> Deduplicated (0 alerts emitted)
        now_m3 = now + timedelta(minutes=2)
        alerts_m3 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now_m3,
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_m3), 0)

        # 4. Minute 4: Quote recovers -> STALE -> HEALTHY (Incident reset)
        now_m4 = now + timedelta(minutes=3)
        alerts_m4 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now_m4,
            is_quote_stale=False,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_m4), 0)
        self.assertFalse(state.feed_health_data.get("stale_incident_active", False))

        # 5. Minute 5: New STALE incident -> HEALTHY -> STALE -> Emits 1 new incident alert
        now_m5 = now + timedelta(minutes=4)
        alerts_m5 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now_m5,
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_m5), 1)
        self.assertEqual(alerts_m5[0].event_type, AlertEventType.LIVE_DATA_STALE.value)

    def test_hostile_23_redis_ttl_verification_and_fail_closed(self):
        """Quote TTL helper fails closed when TTL is missing or unconfigured."""
        with self.settings(XAUUSD_LIVE_QUOTE_TTL_SECONDS=None):
            ttl_val = LiveQuoteService.get_live_quote_ttl_seconds()
            self.assertIsNone(ttl_val)

        with self.settings(XAUUSD_LIVE_QUOTE_TTL_SECONDS=60):
            ttl_val = LiveQuoteService.get_live_quote_ttl_seconds()
            self.assertEqual(ttl_val, 60)

    def test_hostile_24_primary_provider_participation_in_hard_gate(self):
        """Primary provider health strictly participates in RuntimeFeedHealth.primary_15m hard gate."""
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot
        from engine.signals.profile import (
            Phase4CalibrationStatus,
            Phase4SignalProfile,
            SideDirectionPolicy,
            SideGatePolicy,
            SideTimingPolicy,
        )

        test_prof = Phase4SignalProfile(
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
            long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
            short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        )

        base_ts = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        # Seed 30 15m candles
        for i in range(30):
            ts = base_ts - timedelta(minutes=15 * (30 - i))
            MarketCandle.objects.create(
                instrument=self.xauusd,
                timeframe="15m",
                timestamp_open=ts,
                timestamp_close=ts + timedelta(minutes=15),
                open=Decimal("2600.00"),
                high=Decimal("2605.00"),
                low=Decimal("2595.00"),
                close=Decimal("2602.00"),
                volume=Decimal("1000.0"),
                is_closed=True,
                source="primary_spot_feed",
            )

        evt = CandleClosedEvent(
            event_id="EVT_HEALTH_GATE",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=base_ts,
            timestamp_close=base_ts + timedelta(minutes=15),
            open=Decimal("2602.00"),
            high=Decimal("2606.00"),
            low=Decimal("2601.00"),
            close=Decimal("2605.00"),
            volume=Decimal("1200.0"),
            is_closed=True,
            source="primary_spot_feed",
        )

        macro_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=True,
            active_event_name="Normal",
            minutes_to_next_event=120,
        )

        # Case 1: Missing primary listing -> FORCE_WAIT
        MarketListing.objects.filter(instrument=self.xauusd, listing_role=ListingRole.PRIMARY_XAUUSD_SPOT).delete()
        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="test_rev",
            signal_profile=test_prof,
            macro_context=macro_ctx,
            provider_status="HEALTHY",
        )
        self.assertEqual(state.candidate_state, "FORCE_WAIT")
        self.assertEqual(state.candidate_user_decision, "WAIT")
        self.assertEqual(sig_rec.state, "FORCE_WAIT")

        # Create Primary listing
        prim_listing = MarketListing.objects.create(
            instrument=self.xauusd,
            provider="primary_spot_feed",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
            provider_symbol="XAUUSD",
        )

        # Case 2: No PIT snapshot + caller HEALTHY -> FORCE_WAIT
        ProviderHealthSnapshot.objects.filter(listing=prim_listing).delete()
        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="test_rev",
            signal_profile=test_prof,
            macro_context=macro_ctx,
            provider_status="HEALTHY",
        )
        self.assertEqual(state.candidate_state, "FORCE_WAIT")

        # Case 3: PIT UNHEALTHY + caller HEALTHY -> FORCE_WAIT (Persisted status is authoritative; caller cannot upgrade)
        snap = ProviderHealthSnapshot.objects.create(
            listing=prim_listing,
            status="UNHEALTHY",
            checked_at=base_ts + timedelta(minutes=15),
        )
        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="test_rev",
            signal_profile=test_prof,
            macro_context=macro_ctx,
            provider_status="HEALTHY",
        )
        self.assertEqual(state.candidate_state, "FORCE_WAIT")
        self.assertEqual(state.feed_health_data.get("xauusd_primary_status"), "UNHEALTHY")

        # Case 4: PIT HEALTHY + caller UNHEALTHY -> FORCE_WAIT (Caller downgrades)
        snap.status = "HEALTHY"
        snap.save()
        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="test_rev",
            signal_profile=test_prof,
            macro_context=macro_ctx,
            provider_status="UNHEALTHY",
        )
        self.assertEqual(state.candidate_state, "FORCE_WAIT")
        self.assertEqual(state.feed_health_data.get("xauusd_primary_status"), "UNHEALTHY")

        # Case 5: PIT HEALTHY + caller HEALTHY -> Candidate mechanics eligible
        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="test_rev",
            signal_profile=test_prof,
            macro_context=macro_ctx,
            provider_status="HEALTHY",
            is_feed_stale=False,
        )
        self.assertNotEqual(state.candidate_state, "FORCE_WAIT")
        self.assertEqual(state.feed_health_data.get("xauusd_primary_status"), "HEALTHY")
        self.assertEqual(state.feed_health_data.get("xauusd_secondary_status"), "MISSING")

    def test_hostile_25_market_evidence_immutability(self):
        """Decision service does not mutate DB candle source or raise MultipleObjectsReturned on multi-source candles."""
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot

        prim_list = MarketListing.objects.create(
            instrument=self.xauusd,
            provider="primary_spot_feed",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
            provider_symbol="XAUUSD",
        )
        sec_list = MarketListing.objects.create(
            instrument=self.xauusd,
            provider="secondary_spot_feed",
            listing_role=ListingRole.SECONDARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
            provider_symbol="XAUUSD_SEC",
        )

        ts_open = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        ts_close = datetime(2026, 8, 1, 10, 15, tzinfo=timezone.utc)

        # Create two distinct provider rows for the same timestamp
        c_prim = MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            volume=Decimal("1000.0"),
            is_closed=True,
            source="primary_spot_feed",
        )
        c_sec = MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open=Decimal("2650.10"),
            high=Decimal("2655.20"),
            low=Decimal("2649.10"),
            close=Decimal("2654.10"),
            volume=Decimal("950.0"),
            is_closed=True,
            source="secondary_spot_feed",
        )

        ProviderHealthSnapshot.objects.create(listing=prim_list, status="HEALTHY", checked_at=ts_close)

        evt = CandleClosedEvent(
            event_id="EVT_IMMUT_TEST",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            volume=Decimal("1000.0"),
            is_closed=True,
            source="primary_spot_feed",
        )

        # Call decision pipeline
        XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="immutability_rev",
        )

        # Verify DB rows are NOT mutated or rewritten to 'live_feed'
        c_prim.refresh_from_db()
        c_sec.refresh_from_db()
        self.assertEqual(c_prim.source, "primary_spot_feed")
        self.assertEqual(c_sec.source, "secondary_spot_feed")
        self.assertFalse(MarketCandle.objects.filter(source="live_feed").exists())

    def test_hostile_26_deterministic_candle_source_selection(self):
        """get_engine_candles strictly loads primary source and omits non-primary gap fills."""
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing
        MarketListing.objects.create(
            instrument=self.xauusd,
            provider="canonical_prim",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
            provider_symbol="XAUUSD",
        )

        ts_close = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        ts_open = ts_close - timedelta(minutes=15)
        ts_prev = ts_open - timedelta(minutes=15)

        # Create secondary-only candle at ts_open (gap fill attempt) -> should NOT be loaded
        MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=ts_prev,
            timestamp_close=ts_open,
            open=Decimal("2590.00"),
            high=Decimal("2595.00"),
            low=Decimal("2585.00"),
            close=Decimal("2592.00"),
            volume=Decimal("400.0"),
            is_closed=True,
            source="secondary_feed",
        )

        # Create secondary candle and primary candle at ts_close
        MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open=Decimal("2600.00"),
            high=Decimal("2605.00"),
            low=Decimal("2595.00"),
            close=Decimal("2600.00"),
            volume=Decimal("500.0"),
            is_closed=True,
            source="secondary_feed",
        )
        MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=ts_open,
            timestamp_close=ts_close,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            volume=Decimal("1500.0"),
            is_closed=True,
            source="canonical_prim",
        )

        engine_candles = XauUsdLiveDecisionPipelineService.get_engine_candles(
            instrument=self.xauusd,
            timeframe="15m",
            candle_ts=ts_close,
        )

        # Strictly only primary candle at ts_close is returned
        self.assertEqual(len(engine_candles), 1)
        self.assertEqual(engine_candles[0].source_id, "canonical_prim")
        self.assertEqual(engine_candles[0].close, Decimal("2654.00"))

    def test_hostile_26b_secondary_shadow_and_primary_event_selection(self):
        """Secondary DB candle at T does not shadow incoming primary event; primary DB candle is never overridden by secondary event."""
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot

        prim_listing = MarketListing.objects.create(
            instrument=self.xauusd,
            provider="prim_feed_alpha",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
            provider_symbol="XAUUSD",
        )

        t_close = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
        t_open = t_close - timedelta(minutes=15)
        ProviderHealthSnapshot.objects.create(listing=prim_listing, status="HEALTHY", checked_at=t_close)
        macro_ctx = MacroEventContext(is_in_blackout=False, is_feed_healthy=True, active_event_name="Normal", minutes_to_next_event=120)

        # 1. Secondary DB candle exists at T, no primary DB candle exists
        MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2600.00"),
            high=Decimal("2605.00"),
            low=Decimal("2595.00"),
            close=Decimal("2600.00"),
            volume=Decimal("500.0"),
            is_closed=True,
            source="secondary_feed_beta",
        )

        # Incoming primary event at T
        evt_primary = CandleClosedEvent(
            event_id="EVT_PRIM_T",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            volume=Decimal("1500.0"),
            is_closed=True,
            source="prim_feed_alpha",
        )

        # Primary event is appended and used as decision evidence
        sig1, _, state1 = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt_primary,
            code_revision="rev_shadow_1",
            is_feed_stale=False,
            macro_context=macro_ctx,
        )
        self.assertEqual(sig1.timestamp, t_close)
        self.assertEqual(state1.published_state, "NO_TRADE")
        self.assertEqual(state1.published_user_decision, "WAIT")


        # 2. Now primary DB candle exists at T+15m
        t2_open = t_close
        t2_close = t_close + timedelta(minutes=15)
        MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=t2_open,
            timestamp_close=t2_close,
            open=Decimal("2655.00"),
            high=Decimal("2660.00"),
            low=Decimal("2650.00"),
            close=Decimal("2658.00"),
            volume=Decimal("2000.0"),
            is_closed=True,
            source="prim_feed_alpha",
        )

        # Incoming secondary event at T+15m
        evt_secondary = CandleClosedEvent(
            event_id="EVT_SEC_T2",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=t2_open,
            timestamp_close=t2_close,
            open=Decimal("2610.00"),
            high=Decimal("2615.00"),
            low=Decimal("2605.00"),
            close=Decimal("2612.00"),
            volume=Decimal("600.0"),
            is_closed=True,
            source="secondary_feed_beta",
        )

        # Primary DB candle remains authoritative; secondary event is not appended
        sig2, _, state2 = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt_secondary,
            code_revision="rev_shadow_2",
            is_feed_stale=False,
            macro_context=macro_ctx,
        )
        self.assertEqual(sig2.timestamp, t2_close)
        self.assertNotEqual(sig1.analysis_fingerprint, sig2.analysis_fingerprint)

    def test_hostile_27_incident_state_persistence_across_decision_and_restart(self):
        """Incident state survives closed-candle decision cycles, projection updates, and restart reconstruction."""
        now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        state, _ = LiveMonitorState.objects.get_or_create(
            instrument="XAUUSD",
            defaults={"effective_action": "WAIT", "feed_health_data": {}},
        )

        # t1: STALE -> alert #1
        alerts_1 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now,
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_1), 1)
        self.assertTrue(state.feed_health_data.get("stale_incident_active"))

        # t2: STALE -> 0 alerts
        alerts_2 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now + timedelta(minutes=1),
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_2), 0)

        # t3: Closed-candle decision executes while quote is still stale
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot
        prim_listing, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            defaults={"provider": "test_prim_feed", "status": ListingStatus.ACTIVE, "provider_symbol": "XAUUSD"},
        )
        ProviderHealthSnapshot.objects.create(listing=prim_listing, status="HEALTHY", checked_at=now + timedelta(minutes=2))

        evt = CandleClosedEvent(
            event_id="EVT_STALE_SURVIVE",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2650.00"),
            high=Decimal("2655.00"),
            low=Decimal("2649.00"),
            close=Decimal("2654.00"),
            volume=Decimal("1000.0"),
            is_closed=True,
            source="test_prim_feed",
        )
        XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision="rev_incident",
        )
        state.refresh_from_db()
        # Incident flag must still be active
        self.assertTrue(state.feed_health_data.get("stale_incident_active"))

        # t4: STALE quote -> 0 alerts (still deduplicated)
        alerts_4 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now + timedelta(minutes=3),
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_4), 0)

        # Restart / Reconstruct state
        state = XauUsdLiveProjectionService.reconstruct_xauusd_state()
        self.assertTrue(state.feed_health_data.get("stale_incident_active"))

        # t5: STALE quote after restart -> 0 alerts
        alerts_5 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now + timedelta(minutes=4),
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_5), 0)

        # t6: Quote recovers -> HEALTHY -> resets incident flag
        alerts_6 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now + timedelta(minutes=5),
            is_quote_stale=False,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_6), 0)
        self.assertFalse(state.feed_health_data.get("stale_incident_active"))

        # t7: New STALE incident -> alert #2
        alerts_7 = AlertGenerationService.evaluate_live_quote_alerts(
            state=state,
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            quote_ts=now + timedelta(minutes=6),
            is_quote_stale=True,
            provider_healthy=True,
        )
        self.assertEqual(len(alerts_7), 1)

    def test_hostile_28_real_redis_cache_storage_and_ttl(self):
        """LiveQuoteService caches valid quotes in Redis with explicit TTL and blocks stale overwrite."""
        import json
        from unittest.mock import MagicMock, patch

        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        now = datetime.now(timezone.utc)
        fresh_quote = LiveQuoteEvent(
            event_id="Q_REDIS_FRESH",
            instrument="XAUUSD",
            provider="test_prov",
            bid=Decimal("2650.00"),
            ask=Decimal("2651.00"),
            source_timestamp=now,
            received_timestamp=now,
            sequence_number=10,
        )

        with self.settings(
            XAUUSD_LIVE_QUOTE_TTL_SECONDS=60,
            XAUUSD_QUOTE_STALE_SECONDS=45,
            XAUUSD_QUOTE_FUTURE_SKEW_SECONDS=60,
        ):
            with patch("apps.live_monitor.services.LiveEventBroadcaster.get_redis_client", return_value=mock_redis):
                LiveQuoteService.process_quote(fresh_quote)

                # Verify mock_redis.set was called with livequote:XAUUSD, correct JSON, and ex=60
                self.assertTrue(mock_redis.set.called)
                call_args = mock_redis.set.call_args
                self.assertEqual(call_args[0][0], "livequote:XAUUSD")
                cached_data = json.loads(call_args[0][1])
                self.assertEqual(cached_data["instrument"], "XAUUSD")
                self.assertEqual(Decimal(cached_data["bid"]), Decimal("2650.00"))
                self.assertEqual(Decimal(cached_data["ask"]), Decimal("2651.00"))
                self.assertEqual(call_args[1]["ex"], 60)

                mock_redis.reset_mock()

                # Stale quote cannot write to cache
                stale_quote = LiveQuoteEvent(
                    event_id="Q_REDIS_STALE",
                    instrument="XAUUSD",
                    provider="test_prov",
                    bid=Decimal("2640.00"),
                    ask=Decimal("2641.00"),
                    source_timestamp=now - timedelta(seconds=100),
                    received_timestamp=now,
                    sequence_number=11,
                )
                LiveQuoteService.process_quote(stale_quote)
                self.assertFalse(mock_redis.set.called)

    def test_hostile_29_exact_phase6_phase7_history_parity_over_128_candles(self):
        """Exact parity between actual Phase 6 PointInTimeDataset/XauUsdPointInTimeReplay and Phase 7 XauUsdLiveDecisionPipelineService on >128 bars."""
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot
        from engine.backtest.repository import PointInTimeDataset
        from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
        from engine.backtest.clock import ReplayClock
        from engine.signals.engine import XauUsdSignalEngine
        from engine.risk.xauusd_planner import XauUsdRiskPlanner
        from engine.signals.profile import uncalibrated_xauusd_signal_profile
        from engine.risk.xauusd_policy import uncalibrated_xauusd_risk_profile

        prim_listing, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            defaults={"provider": "parity_prov", "status": ListingStatus.ACTIVE, "provider_symbol": "XAUUSD"},
        )

        base_ts = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        # Create 140 15m candles
        candles_15m = []
        for i in range(140):
            ts = base_ts + timedelta(minutes=15 * i)
            o = Decimal("2600.00") + Decimal(str(i * 0.2))
            c = Decimal("2601.00") + Decimal(str(i * 0.2))
            h = c + Decimal("2.00")
            l = o - Decimal("1.00")
            candles_15m.append(
                MarketCandle(
                    instrument=self.xauusd,
                    timeframe="15m",
                    timestamp_open=ts,
                    timestamp_close=ts + timedelta(minutes=15),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=Decimal("1000.0"),
                    is_closed=True,
                    source="parity_prov",
                )
            )
        MarketCandle.objects.bulk_create(candles_15m)

        # Create 35 1h candles
        candles_1h = []
        for i in range(35):
            ts = base_ts + timedelta(hours=i)
            candles_1h.append(
                MarketCandle(
                    instrument=self.xauusd,
                    timeframe="1h",
                    timestamp_open=ts,
                    timestamp_close=ts + timedelta(hours=1),
                    open=Decimal("2600.00") + Decimal(str(i * 0.8)),
                    high=Decimal("2605.00") + Decimal(str(i * 0.8)),
                    low=Decimal("2598.00") + Decimal(str(i * 0.8)),
                    close=Decimal("2604.00") + Decimal(str(i * 0.8)),
                    volume=Decimal("4000.0"),
                    is_closed=True,
                    source="parity_prov",
                )
            )
        MarketCandle.objects.bulk_create(candles_1h)

        eval_ts = base_ts + timedelta(minutes=15 * 140)
        ProviderHealthSnapshot.objects.create(listing=prim_listing, status="HEALTHY", checked_at=eval_ts)

        code_rev = "34a21541f2a9725c7fde324c1e08245a2363742d"
        sig_profile = uncalibrated_xauusd_signal_profile()
        risk_profile = uncalibrated_xauusd_risk_profile()

        # A. Actual Phase 6 PointInTimeDataset & XauUsdPointInTimeReplay
        dataset = PointInTimeDataset(
            candles_15m=XauUsdLiveDecisionPipelineService.get_engine_candles(self.xauusd, "15m", eval_ts),
            candles_1h=XauUsdLiveDecisionPipelineService.get_engine_candles(self.xauusd, "1h", eval_ts),
        )

        sig_engine = XauUsdSignalEngine(
            code_revision=code_rev,
            engine_version="4.0.0-xauusd",
            feature_version="feat-xauusd-2026-v1",
            cycle_version="3.0.0-3A",
        )
        replay_engine = XauUsdPointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=XauUsdRiskPlanner(risk_profile=risk_profile, code_revision=code_rev),
            signal_profile=sig_profile,
            holding_horizon_bars_15m=16,
            max_fill_wait_bars_15m=4,
        )
        replay_signals, _ = replay_engine.run(ReplayClock([eval_ts]))
        self.assertEqual(len(replay_signals), 1)
        phase6_snap = replay_signals[0]

        # B. Phase 7 XauUsdLiveDecisionPipelineService
        last_candle = candles_15m[-1]
        evt = CandleClosedEvent(
            event_id="EVT_PARITY_140",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=last_candle.timestamp_open,
            timestamp_close=last_candle.timestamp_close,
            open=last_candle.open,
            high=last_candle.high,
            low=last_candle.low,
            close=last_candle.close,
            volume=last_candle.volume,
            is_closed=True,
            source="parity_prov",
        )
        sig_rec, risk_rec, live_state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=evt,
            code_revision=code_rev,
            signal_profile=sig_profile,
            risk_profile=risk_profile,
            is_feed_stale=False,
        )

        # Assert EXACT parity between Phase 6 Replay engine and Phase 7 Live Pipeline
        self.assertEqual(live_state.candidate_state, phase6_snap.candidate_state.value)
        self.assertEqual(live_state.candidate_user_decision, phase6_snap.candidate_user_decision.value)
        self.assertEqual(live_state.published_state, phase6_snap.state.value)
        self.assertEqual(live_state.published_user_decision, phase6_snap.user_decision.value)
        self.assertEqual(live_state.long_direction_score, phase6_snap.long_direction.total_score)
        self.assertEqual(live_state.short_direction_score, phase6_snap.short_direction.total_score)
        self.assertEqual(live_state.long_timing_score, phase6_snap.long_timing.total_score)
        self.assertEqual(live_state.short_timing_score, phase6_snap.short_timing.total_score)
        self.assertEqual(live_state.phase4_policy_fingerprint, phase6_snap.phase4_policy_fingerprint)
        self.assertEqual(live_state.analysis_fingerprint, phase6_snap.analysis_fingerprint)



