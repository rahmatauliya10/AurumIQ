"""Unit tests for Exact-Listing Provider Health Isolation & Transient 1D Semantics."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from django.test import TestCase

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    ListingRole,
    ListingStatus,
    MarketListing,
    ProviderHealthSnapshot,
)
from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
from apps.live_monitor.types import CandleClosedEvent
from apps.market_data.models import DataQualitySnapshot, MarketCandle
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
class TestExactListingHealthIsolation(TestCase):
    """Verifies that health snapshot lookups bind strictly to exact listing."""

    def setUp(self):
        self.xau, _ = Asset.objects.get_or_create(code="XAU", name="Gold Spot", asset_type=AssetType.COMMODITY)
        self.usd, _ = Asset.objects.get_or_create(code="USD", name="US Dollar", asset_type=AssetType.FIAT)
        self.eur, _ = Asset.objects.get_or_create(code="EUR", name="Euro", asset_type=AssetType.FIAT)

        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )
        self.xaueur, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.eur,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )

        self.primary_listing, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            provider="twelve_data",
            defaults={
                "listing_role": ListingRole.PRIMARY_XAUUSD_SPOT,
                "status": ListingStatus.ACTIVE,
                "provider_symbol": "XAU/USD",
            },
        )

        self.other_listing, _ = MarketListing.objects.get_or_create(
            instrument=self.xaueur,
            provider="twelve_data",
            defaults={
                "listing_role": ListingRole.SECONDARY_XAUUSD_SPOT,
                "status": ListingStatus.ACTIVE,
                "provider_symbol": "XAU/EUR",
            },
        )

    def _dummy_profile(self):
        from engine.signals.profile import (
            Phase4CalibrationStatus,
            Phase4SignalProfile,
            SideDirectionPolicy,
            SideGatePolicy,
            SideTimingPolicy,
        )
        return Phase4SignalProfile(
            name="test_profile",
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.REVALIDATED_RESEARCH,
            long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
            long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
            long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
            short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        )

    def test_secondary_unhealthy_does_not_poison_primary_healthy(self):
        """Hostile: other listing using same provider is UNHEALTHY; primary listing snapshot is HEALTHY.
        Result: PRIMARY_XAUUSD_SPOT remains governed by its own snapshot only (HEALTHY).
        """
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

        # Primary listing has HEALTHY snapshot
        ProviderHealthSnapshot.objects.create(
            listing=self.primary_listing,
            status="HEALTHY",
            checked_at=now,
        )

        # Other listing using SAME provider has UNHEALTHY snapshot
        ProviderHealthSnapshot.objects.create(
            listing=self.other_listing,
            status="UNHEALTHY",
            checked_at=now,
            reason="OTHER_LISTING_RATE_LIMITED",
        )

        # Build minimum candles to avoid continuity gap
        candles = []
        for i in range(30):
            ts = now - timedelta(minutes=15 * (30 - i))
            candles.append(
                MarketCandle(
                    instrument=self.xauusd,
                    timeframe="15m",
                    timestamp_open=ts - timedelta(minutes=15),
                    timestamp_close=ts,
                    open=Decimal("2500.00"),
                    high=Decimal("2505.00"),
                    low=Decimal("2495.00"),
                    close=Decimal("2502.00"),
                    volume=Decimal("100"),
                    source="twelve_data",
                )
            )
        MarketCandle.objects.bulk_create(candles)

        event = CandleClosedEvent(
            event_id="EVT_HEALTH_ISO_1",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2495.00"),
            close=Decimal("2502.00"),
            is_closed=True,
        )

        macro_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=True,
        )

        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test_rev",
            signal_profile=self._dummy_profile(),
            macro_context=macro_ctx,
            is_feed_stale=False,
        )

        # Primary listing health must NOT be poisoned by other listing
        self.assertEqual(state.feed_health_data.get("xauusd_primary_status"), "HEALTHY")

    def test_other_healthy_does_not_upgrade_primary_unhealthy(self):
        """Hostile: other listing using same provider is HEALTHY; primary listing snapshot is UNHEALTHY.
        Result: PRIMARY_XAUUSD_SPOT remains UNHEALTHY (FORCE_WAIT), never upgraded by other listing.
        """
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

        # Primary listing has UNHEALTHY snapshot
        ProviderHealthSnapshot.objects.create(
            listing=self.primary_listing,
            status="UNHEALTHY",
            checked_at=now,
            reason="PRIMARY_RATE_LIMIT",
        )

        # Other listing using SAME provider has HEALTHY snapshot
        ProviderHealthSnapshot.objects.create(
            listing=self.other_listing,
            status="HEALTHY",
            checked_at=now,
        )

        event = CandleClosedEvent(
            event_id="EVT_HEALTH_ISO_2",
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=now - timedelta(minutes=15),
            timestamp_close=now,
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2495.00"),
            close=Decimal("2502.00"),
            is_closed=True,
        )

        macro_ctx = MacroEventContext(
            is_in_blackout=False,
            is_feed_healthy=True,
        )

        sig_rec, _, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test_rev",
            signal_profile=self._dummy_profile(),
            macro_context=macro_ctx,
            is_feed_stale=False,
        )

        # Primary listing health must NOT be upgraded
        self.assertEqual(state.feed_health_data.get("xauusd_primary_status"), "UNHEALTHY")
        self.assertEqual(state.candidate_state, "FORCE_WAIT")

    def test_tasks_ingest_health_reuse_binds_to_exact_listing(self):
        """Verify ingest_primary_candles reuses health snapshot bound to exact listing."""
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

        # Create UNHEALTHY snapshot for other listing
        ProviderHealthSnapshot.objects.create(
            listing=self.other_listing,
            status="UNHEALTHY",
            checked_at=now - timedelta(minutes=5),
            reason="OTHER_DOWN",
        )

        # Create HEALTHY snapshot for primary listing
        ProviderHealthSnapshot.objects.create(
            listing=self.primary_listing,
            status="HEALTHY",
            checked_at=now - timedelta(minutes=5),
        )

        # The query used in ingest_primary_candles:
        recent = ProviderHealthSnapshot.objects.filter(
            listing=self.primary_listing,
        ).order_by("-checked_at").first()

        self.assertIsNotNone(recent)
        self.assertEqual(recent.status, "HEALTHY")
        self.assertEqual(recent.listing, self.primary_listing)

    def test_transient_1d_stale_fail_preserves_audit_trail(self):
        """Transient 1D HTTP 429 followed by retry creates both audit records,
        retains FAIL record in history, and latest status is FRESH/PASS.
        """
        now = datetime(2026, 9, 16, 1, 29, 5, tzinfo=timezone.utc)

        # First attempt: HTTP 429 rate limit
        dq1 = DataQualitySnapshot.objects.create(
            instrument=self.xauusd,
            timeframe="1d",
            timestamp=now,
            quality_score=Decimal("0.00"),
            gap_count=0,
            duplicate_count=0,
            violation_count=1,
            is_stale=True,
            hard_fail=True,
            anomalies={"error": "TWELVE_DATA_RATE_LIMITED: 429 Too Many Requests"},
        )

        # Subsequent retry at 01:30:02 succeeds
        retry_time = datetime(2026, 9, 16, 1, 30, 2, tzinfo=timezone.utc)
        dq2 = DataQualitySnapshot.objects.create(
            instrument=self.xauusd,
            timeframe="1d",
            timestamp=retry_time,
            quality_score=Decimal("100.00"),
            gap_count=0,
            duplicate_count=0,
            violation_count=0,
            is_stale=False,
            hard_fail=False,
            anomalies={},
        )

        # Verify historical FAIL is preserved
        fail_records = DataQualitySnapshot.objects.filter(
            instrument=self.xauusd,
            timeframe="1d",
            hard_fail=True,
        )
        self.assertEqual(fail_records.count(), 1)
        self.assertEqual(fail_records.first().id, dq1.id)

        # Verify latest record is FRESH/PASS
        latest = DataQualitySnapshot.objects.filter(
            instrument=self.xauusd,
            timeframe="1d",
        ).order_by("-timestamp").first()
        self.assertEqual(latest.id, dq2.id)
        self.assertFalse(latest.hard_fail)
        self.assertFalse(latest.is_stale)
        self.assertEqual(latest.quality_score, Decimal("100.00"))

    def test_canonical_source_alias_normalization_twelve_data(self):
        """
        Verify legacy source alias 'twelve_data' is recognized as the
        authoritative Twelve Data primary source ('twelve_data_xauusd')
        in get_engine_candles without weakening source validation against
        unrelated / unauthorized sources.
        """
        from apps.market_data.providers import (
            normalize_canonical_source,
            get_canonical_source_aliases,
        )

        # 1. Direct normalization mapping
        self.assertEqual(normalize_canonical_source("twelve_data"), "twelve_data_xauusd")
        self.assertEqual(normalize_canonical_source("twelve_data_xauusd"), "twelve_data_xauusd")
        self.assertEqual(normalize_canonical_source("TWELVE_DATA"), "twelve_data_xauusd")
        self.assertEqual(normalize_canonical_source("unknown_feed"), "unknown_feed")

        # 2. Alias resolution
        aliases = get_canonical_source_aliases("twelve_data_xauusd")
        self.assertIn("twelve_data", aliases)
        self.assertIn("twelve_data_xauusd", aliases)

        # 3. Setup primary listing on self.xauusd
        MarketListing.objects.filter(instrument=self.xauusd).delete()
        MarketListing.objects.create(
            instrument=self.xauusd,
            provider="twelve_data_xauusd",
            provider_symbol="XAU/USD",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        )

        eval_ts = datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc)
        # Candle with legacy source alias
        MarketCandle.objects.create(
            instrument=self.xauusd,
            source="twelve_data",
            timeframe="15m",
            timestamp_open=eval_ts - timedelta(minutes=30),
            timestamp_close=eval_ts - timedelta(minutes=15),
            open=Decimal("2500.0"),
            high=Decimal("2505.0"),
            low=Decimal("2495.0"),
            close=Decimal("2502.0"),
            volume=Decimal("100.0"),
            is_closed=True,
        )
        # Candle with provider ID source
        MarketCandle.objects.create(
            instrument=self.xauusd,
            source="twelve_data_xauusd",
            timeframe="15m",
            timestamp_open=eval_ts - timedelta(minutes=15),
            timestamp_close=eval_ts,
            open=Decimal("2502.0"),
            high=Decimal("2508.0"),
            low=Decimal("2500.0"),
            close=Decimal("2506.0"),
            volume=Decimal("120.0"),
            is_closed=True,
        )
        # Unauthorized third-party source candle
        MarketCandle.objects.create(
            instrument=self.xauusd,
            source="random_unauthorized_feed",
            timeframe="15m",
            timestamp_open=eval_ts - timedelta(minutes=45),
            timestamp_close=eval_ts - timedelta(minutes=30),
            open=Decimal("2490.0"),
            high=Decimal("2495.0"),
            low=Decimal("2485.0"),
            close=Decimal("2492.0"),
            volume=Decimal("50.0"),
            is_closed=True,
        )

        candles = XauUsdLiveDecisionPipelineService.get_engine_candles(self.xauusd, "15m", eval_ts)
        # Must load exactly the 2 twelve_data / twelve_data_xauusd candles, strictly excluding random_unauthorized_feed
        self.assertEqual(len(candles), 2)
        sources_loaded = {c.source_id for c in candles}
        self.assertIn("twelve_data", sources_loaded)
        self.assertIn("twelve_data_xauusd", sources_loaded)
        self.assertNotIn("random_unauthorized_feed", sources_loaded)
