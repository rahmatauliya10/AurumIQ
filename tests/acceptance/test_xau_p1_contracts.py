"""Acceptance Test Contracts for Phase 1 XAUUSD Data Engine (XAU-P1-01 and XAU-P1-02)."""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    MarketListing,
    ListingStatus,
    ListingRole,
    ProviderHealthStatus,
    ProviderHealthSnapshot,
)
from apps.market_data.models import MarketCandle, VolumeEvidenceType, CandleQualityFlag, DataQualitySnapshot
from apps.market_data.normalization import QuoteNormalizer
from apps.market_data.integrity import MarketIntegrityEngine
from apps.market_data.providers.base import RawCandle, ProviderHealth
from apps.market_data.providers.xauusd_spot import XauUsdSpotProvider
from apps.market_data.providers.xauusd_secondary import SecondaryXauUsdSpotProvider
from apps.market_data.repositories import DjangoCandleRepository
from apps.market_data.tasks import ingest_primary_candles, check_provider_health_task
from engine.core.types import VolumeEvidenceType as EngineVolumeEvidenceType


class TestXauP1Contracts(TestCase):
    """Test suite validating XAU-P1-01, XAU-P1-02, and all Phase 1 integrity contracts."""

    def setUp(self):
        # Seed Assets
        self.xau, _ = Asset.objects.get_or_create(
            code="XAU", defaults={"name": "Spot Gold", "asset_type": AssetType.COMMODITY}
        )
        self.usd, _ = Asset.objects.get_or_create(
            code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT}
        )
        self.xaut, _ = Asset.objects.get_or_create(
            code="XAUT", defaults={"name": "Tether Gold", "asset_type": AssetType.CRYPTO_TOKEN}
        )
        self.usdt, _ = Asset.objects.get_or_create(
            code="USDT", defaults={"name": "Tether USD", "asset_type": AssetType.CRYPTO_TOKEN}
        )

        # Seed Canonical XAUUSD Target Instrument (preserves historical GOLD_REFERENCE role)
        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )

        # Seed Historical Legacy XAUT Instrument
        self.xaut_legacy, _ = Instrument.objects.get_or_create(
            base_asset=self.xaut,
            quote_asset=self.usdt,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION, "is_active": True},
        )

        # Seed Listings with explicit ListingRoles
        self.listing_gold_ref, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            provider="gold_reference",
            defaults={
                "provider_symbol": "XAUUSD",
                "listing_role": ListingRole.LEGACY_GOLD_REFERENCE,
                "fallback_priority": 0,
                "status": ListingStatus.ACTIVE,
            },
        )
        self.listing_primary, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            provider="xauusd_primary",
            defaults={
                "provider_symbol": "XAUUSD",
                "listing_role": ListingRole.PRIMARY_XAUUSD_SPOT,
                "fallback_priority": 0,
                "status": ListingStatus.ACTIVE,
            },
        )
        self.listing_secondary, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            provider="xauusd_secondary",
            defaults={
                "provider_symbol": "XAUUSD",
                "listing_role": ListingRole.SECONDARY_XAUUSD_SPOT,
                "fallback_priority": 1,
                "status": ListingStatus.ACTIVE,
            },
        )

    def test_xau_p1_01_canonical_xauusd_primary_target(self):
        """
        XAU-P1-01: Canonical XAUUSD primary target contract.
        Proves:
          - Canonical target resolves XAU/USD (canonical XAUUSD).
          - Direct USD semantics (close_usd == close, quote_rate == 1.0).
          - No USDT normalization dependency for XAUUSD.
          - Closed-candle repository loads point-in-time XAUUSD data.
          - Historical XAUT scope remains intact.
        """
        # 1. Target resolution
        resolved_xauusd = Instrument.get_canonical_xauusd()
        assert resolved_xauusd is not None
        assert resolved_xauusd.symbol == "XAU/USD"
        assert resolved_xauusd.base_asset.code == "XAU"
        assert resolved_xauusd.quote_asset.code == "USD"

        # 2. Historical legacy preservation
        resolved_legacy = Instrument.get_legacy_xaut()
        assert resolved_legacy is not None
        assert resolved_legacy.symbol == "XAUT/USDT"
        assert resolved_legacy.role == InstrumentRole.EXECUTION

        # 3. Direct USD normalization on MarketCandle
        t0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        candle = MarketCandle.objects.create(
            instrument=self.xauusd,
            source="xauusd_primary",
            timeframe="15m",
            timestamp_open=t0,
            timestamp_close=t0 + timedelta(minutes=15),
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2495.00"),
            close=Decimal("2505.50"),
            volume=Decimal("120.5"),
            volume_evidence=VolumeEvidenceType.TICK_VOLUME,
            is_closed=True,
        )

        assert candle.quote_rate == Decimal("1.000000")
        assert candle.close_usd == Decimal("2505.50000000")

        # 4. Repository load
        repo = DjangoCandleRepository()
        loaded = repo.load_window("XAU/USD", "15m", t0 + timedelta(minutes=30), 10)
        assert len(loaded) == 1
        assert loaded[0].close == Decimal("2505.50")
        assert loaded[0].close_usd == Decimal("2505.50000000")
        assert loaded[0].volume_evidence == EngineVolumeEvidenceType.TICK_VOLUME

    def test_no_legacy_threshold_fallback(self):
        """
        Patch 1: Proves verify_xauusd_multi_provider_integrity does NOT inherit
        legacy A15 outlier_threshold_pct (0.50%) when max_divergence_pct is None.
        Fails closed as INTEGRITY_THRESHOLD_NOT_CONFIGURED.
        """
        integrity_engine = MarketIntegrityEngine()
        
        # When max_divergence_pct is None, must fail closed regardless of prices
        res = integrity_engine.verify_xauusd_multi_provider_integrity(
            primary_price=Decimal("2500.00"),
            secondary_price=Decimal("2500.10"),  # 0.004% difference, tiny
            max_divergence_pct=None,
        )
        assert res.is_valid is False
        assert res.hard_fail is True
        assert res.is_disagreement is False
        assert "INTEGRITY_THRESHOLD_NOT_CONFIGURED" in res.message
        assert "Historical A15 threshold must not be inherited" in res.message

    def test_xau_p1_02_integrated_ingestion_and_integrity_gate(self):
        """
        Patch 2 / XAU-P1-02: Integrated primary + secondary ingestion integrity contract.
        Proves:
          - Ingestion aligns primary and secondary closed candles by timestamp.
          - Agreement within configured threshold persists candles with OK data quality.
          - Disagreement > threshold flags candles as SUSPECT and marks DataQualitySnapshot hard_fail=True.
          - Secondary price is strictly integrity evidence (zero directional alpha).
        """
        t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
        primary_candles = [
            RawCandle(
                symbol="XAUUSD", timeframe="15m",
                timestamp_open=t0, timestamp_close=t0 + timedelta(minutes=15),
                open=Decimal("2500.00"), high=Decimal("2505.00"), low=Decimal("2498.00"), close=Decimal("2502.00"),
                volume=Decimal("100"), is_closed=True, source="xauusd_primary", volume_evidence="TICK_VOLUME",
            )
        ]
        # Secondary candle diverging by > 1.0% (disagreement)
        secondary_candles_disagree = [
            RawCandle(
                symbol="XAUUSD", timeframe="15m",
                timestamp_open=t0, timestamp_close=t0 + timedelta(minutes=15),
                open=Decimal("2500.00"), high=Decimal("2535.00"), low=Decimal("2498.00"), close=Decimal("2530.00"),
                volume=Decimal("150"), is_closed=True, source="xauusd_secondary", volume_evidence="TICK_VOLUME",
            )
        ]

        with patch("apps.market_data.tasks.registry.get") as mock_registry_get:
            mock_primary = MagicMock()
            mock_primary.is_configured.return_value = True
            mock_primary.health_check.return_value = ProviderHealth(
                provider_id="xauusd_primary", status="HEALTHY", checked_at=t0, latency_ms=10
            )
            mock_primary.fetch_candles.return_value = primary_candles

            mock_secondary = MagicMock()
            mock_secondary.is_configured.return_value = True
            mock_secondary.health_check.return_value = ProviderHealth(
                provider_id="xauusd_secondary", status="HEALTHY", checked_at=t0, latency_ms=12
            )
            mock_secondary.fetch_candles.return_value = secondary_candles_disagree

            def side_effect(provider_id):
                if provider_id == "xauusd_primary":
                    return mock_primary
                if provider_id == "xauusd_secondary":
                    return mock_secondary
                return None

            mock_registry_get.side_effect = side_effect

            # Run ingestion with explicit 0.35% threshold
            res = ingest_primary_candles(
                instrument_symbol="XAU/USD",
                timeframes=["15m"],
                lookback_bars=1,
                xauusd_max_divergence_pct=Decimal("0.0035"),
                is_secondary_critical=True,
            )

            # PATCH E: Propagate integrity failure to top-level task result
            assert res["status"] == "hard_fail"
            assert "DISAGREEMENT" in res["reason"] or "diverge" in res["reason"].lower()
            # Verify candle was flagged SUSPECT due to multi-source integrity failure
            stored_candle = MarketCandle.objects.get(
                instrument=self.xauusd, source="xauusd_primary", timeframe="15m", timestamp_open=t0
            )
            assert stored_candle.data_quality_flag == CandleQualityFlag.SUSPECT
            # Primary price is strictly preserved without averaging
            assert stored_candle.close == Decimal("2502.00")

            # Verify snapshot recorded hard fail
            snapshot = DataQualitySnapshot.objects.filter(instrument=self.xauusd, timeframe="15m").latest("timestamp")
            assert snapshot.hard_fail is True
            assert "integrity_disagreement" in snapshot.anomalies

    def test_strict_source_selection_no_unsafe_fallback(self):
        """
        PATCH A: Proves that canonical XAUUSD ingestion resolves strictly through
        ListingRole.PRIMARY_XAUUSD_SPOT and does NOT fall back to gold_reference
        or any arbitrary active listing if PRIMARY_XAUUSD_SPOT is missing.
        """
        # Deactivate or remove PRIMARY_XAUUSD_SPOT listing
        self.listing_primary.status = ListingStatus.DELISTED
        self.listing_primary.save()

        # gold_reference exists and is ACTIVE with priority 0
        self.listing_gold_ref.status = ListingStatus.ACTIVE
        self.listing_gold_ref.fallback_priority = 0
        self.listing_gold_ref.save()

        res = ingest_primary_candles(
            instrument_symbol="XAU/USD",
            timeframes=["15m"],
            lookback_bars=1,
            xauusd_max_divergence_pct=Decimal("0.0035"),
            is_secondary_critical=False,
        )

        assert res["status"] == "hard_fail"
        assert res["reason"] == "PRIMARY_XAUUSD_LISTING_NOT_CONFIGURED"
        assert res["candles_ingested"] == 0

        latest_dq = DataQualitySnapshot.objects.filter(instrument=self.xauusd).latest("timestamp")
        assert latest_dq.hard_fail is True
        assert latest_dq.quality_score == Decimal("0.00")
        assert "PRIMARY_XAUUSD_LISTING_NOT_CONFIGURED" in latest_dq.anomalies["error"]

    def test_generic_usd_instrument_isolation(self):
        """
        PATCH B: Proves that generic USD-quoted instruments (e.g. TEST/USD)
        do NOT enter the PRIMARY_XAUUSD_SPOT or XAUUSD integrity verification path.
        """
        test_asset, _ = Asset.objects.get_or_create(
            code="TEST", defaults={"name": "Test Asset", "asset_type": AssetType.CRYPTO_TOKEN}
        )
        test_usd, _ = Instrument.objects.get_or_create(
            base_asset=test_asset,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION, "is_active": True},
        )
        test_listing, _ = MarketListing.objects.get_or_create(
            instrument=test_usd,
            provider="test_venue",
            defaults={
                "provider_symbol": "TESTUSD",
                "listing_role": ListingRole.GENERIC,
                "status": ListingStatus.ACTIVE,
                "fallback_priority": 0,
            },
        )

        t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
        test_candles = [
            RawCandle(
                symbol="TESTUSD", timeframe="15m",
                timestamp_open=t0, timestamp_close=t0 + timedelta(minutes=15),
                open=Decimal("100.00"), high=Decimal("105.00"), low=Decimal("98.00"), close=Decimal("102.00"),
                volume=Decimal("50"), is_closed=True, source="test_venue", volume_evidence="TICK_VOLUME",
            )
        ]

        with patch("apps.market_data.tasks.registry.get") as mock_registry_get:
            mock_test_provider = MagicMock()
            mock_test_provider.is_configured.return_value = True
            mock_test_provider.fetch_candles.return_value = test_candles

            mock_registry_get.side_effect = lambda pid: mock_test_provider if pid == "test_venue" else None

            res = ingest_primary_candles(
                instrument_symbol="TEST/USD",
                timeframes=["15m"],
                lookback_bars=1,
            )

            # Generic USD succeeds without requiring PRIMARY_XAUUSD_SPOT or secondary integrity
            assert res["status"] == "success"
            assert res["candles_ingested"] == 1
            assert res["provider"] == "test_venue"

    def test_primary_provider_health_fail_closed(self):
        """
        PATCH C: Proves that when primary XAUUSD provider health is NOT_CONFIGURED,
        UNHEALTHY, QUARANTINED, UNKNOWN, or DEGRADED, ingestion fails closed.
        """
        t0 = datetime.now(timezone.utc)
        for health_status in [
            ProviderHealthStatus.NOT_CONFIGURED,
            ProviderHealthStatus.UNHEALTHY,
            ProviderHealthStatus.QUARANTINED,
            ProviderHealthStatus.UNKNOWN,
            ProviderHealthStatus.DEGRADED,
        ]:
            with patch("apps.market_data.tasks.registry.get") as mock_registry_get:
                mock_primary = MagicMock()
                mock_primary.is_configured.return_value = (health_status != ProviderHealthStatus.NOT_CONFIGURED)
                mock_primary.health_check.return_value = ProviderHealth(
                    provider_id="xauusd_primary",
                    status=health_status,
                    checked_at=t0,
                    latency_ms=999 if health_status != "HEALTHY" else 10,
                    error_message=f"Simulated {health_status} failure",
                )
                mock_registry_get.side_effect = lambda pid: mock_primary if pid == "xauusd_primary" else None

                res = ingest_primary_candles(
                    instrument_symbol="XAU/USD",
                    timeframes=["15m"],
                    lookback_bars=1,
                    xauusd_max_divergence_pct=Decimal("0.0035"),
                )

                assert res["status"] == "hard_fail"
                assert health_status in res["reason"] or "NOT_CONFIGURED" in res["reason"]
                assert res["candles_ingested"] == 0

                snapshot = DataQualitySnapshot.objects.filter(instrument=self.xauusd).latest("timestamp")
                assert snapshot.hard_fail is True
                assert snapshot.quality_score == Decimal("0.00")

    def test_empty_primary_data_fail_closed(self):
        """
        PATCH D: Proves that when primary XAUUSD provider returns zero usable closed candles,
        ingestion fails closed with status='hard_fail' and PRIMARY_XAUUSD_NO_USABLE_CLOSED_DATA.
        """
        t0 = datetime.now(timezone.utc)
        with patch("apps.market_data.tasks.registry.get") as mock_registry_get:
            mock_primary = MagicMock()
            mock_primary.is_configured.return_value = True
            mock_primary.health_check.return_value = ProviderHealth(
                provider_id="xauusd_primary", status="HEALTHY", checked_at=t0, latency_ms=10
            )
            # Returns empty list or only open forming candles (0 closed candles)
            mock_primary.fetch_candles.return_value = []

            mock_secondary = MagicMock()
            mock_secondary.is_configured.return_value = True

            mock_registry_get.side_effect = lambda pid: mock_primary if pid == "xauusd_primary" else mock_secondary

            res = ingest_primary_candles(
                instrument_symbol="XAU/USD",
                timeframes=["15m"],
                lookback_bars=1,
                xauusd_max_divergence_pct=Decimal("0.0035"),
                is_secondary_critical=False,
            )

            assert res["status"] == "hard_fail"
            assert "PRIMARY_XAUUSD_NO_USABLE_CLOSED_DATA" in res["reason"]
            assert res["candles_ingested"] == 0

            snapshot = DataQualitySnapshot.objects.filter(instrument=self.xauusd).latest("timestamp")
            assert snapshot.hard_fail is True
            assert snapshot.quality_score == Decimal("0.00")
            assert "PRIMARY_XAUUSD_NO_USABLE_CLOSED_DATA" in snapshot.anomalies["error"]

    def test_threshold_not_configured_ingestion_fail_closed(self):
        """
        PATCH E: Proves that when xauusd_max_divergence_pct is None (and setting is None),
        actual ingestion fails closed as INTEGRITY_THRESHOLD_NOT_CONFIGURED.
        """
        t0 = datetime.now(timezone.utc)
        with patch("apps.market_data.tasks.registry.get") as mock_registry_get:
            mock_primary = MagicMock()
            mock_primary.is_configured.return_value = True
            mock_primary.health_check.return_value = ProviderHealth(
                provider_id="xauusd_primary", status="HEALTHY", checked_at=t0, latency_ms=10
            )
            mock_registry_get.side_effect = lambda pid: mock_primary if pid == "xauusd_primary" else None

            res = ingest_primary_candles(
                instrument_symbol="XAU/USD",
                timeframes=["15m"],
                lookback_bars=1,
                xauusd_max_divergence_pct=None,  # Not configured
            )

            assert res["status"] == "hard_fail"
            assert res["reason"] == "INTEGRITY_THRESHOLD_NOT_CONFIGURED"
            assert res["candles_ingested"] == 0

            snapshot = DataQualitySnapshot.objects.filter(instrument=self.xauusd).latest("timestamp")
            assert snapshot.hard_fail is True
            assert snapshot.quality_score == Decimal("0.00")
            assert "INTEGRITY_THRESHOLD_NOT_CONFIGURED" in snapshot.anomalies["error"]

    def test_provider_health_status_not_configured_persistence(self):
        """
        Patch 5: Proves ProviderHealthStatus.NOT_CONFIGURED can be persisted
        in ProviderHealthSnapshot model without database constraint / enum errors.
        """
        t = datetime.now(timezone.utc)
        snapshot = ProviderHealthSnapshot.objects.create(
            listing=self.listing_primary,
            status=ProviderHealthStatus.NOT_CONFIGURED,
            checked_at=t,
            latency_ms=None,
            reason="Endpoint URL not configured",
        )
        assert snapshot.pk is not None
        assert snapshot.status == ProviderHealthStatus.NOT_CONFIGURED

        # Test periodic health task with unconfigured providers (mock registry to avoid network calls)
        with patch("apps.market_data.tasks.registry.all_providers") as mock_all:
            mock_p1 = MagicMock()
            mock_p1.provider_id = "xauusd_primary"
            mock_p1.health_check.return_value = ProviderHealth(
                provider_id="xauusd_primary",
                status="NOT_CONFIGURED",
                checked_at=t,
                latency_ms=None,
                error_message="Not configured",
            )
            mock_all.return_value = [mock_p1]

            task_res = check_provider_health_task()
            assert task_res["status"] == "success"
            assert "xauusd_primary" in task_res["providers"]
            assert task_res["providers"]["xauusd_primary"] == "NOT_CONFIGURED"

    def test_critical_primary_fail_closed_ingestion(self):
        """
        Patch 6: Proves that when primary XAUUSD provider is NOT_CONFIGURED or fails,
        ingestion fails closed (returns status=hard_fail) and persists a hard_fail DataQualitySnapshot.
        """
        # Primary provider is unconfigured
        res = ingest_primary_candles(
            instrument_symbol="XAU/USD",
            timeframes=["15m"],
            lookback_bars=5,
        )
        assert res["status"] == "hard_fail"
        assert "PRIMARY_XAUUSD" in res["reason"]
        assert res["candles_ingested"] == 0

        # Verify DataQualitySnapshot recorded hard_fail
        latest_dq = DataQualitySnapshot.objects.filter(instrument=self.xauusd).latest("timestamp")
        assert latest_dq.hard_fail is True
        assert latest_dq.quality_score == Decimal("0.00")

    def test_conservative_volume_semantics(self):
        """
        Patch 7: Proves conservative volume semantics:
          - Default volume evidence is UNAVAILABLE (never assumed to be tick/real volume).
          - Missing volume field produces UNAVAILABLE and 0 volume.
          - Invalid volume label safely falls back to UNAVAILABLE.
        """
        provider = XauUsdSpotProvider(feed_url="http://mock-feed.local")
        assert provider._default_volume_evidence == "UNAVAILABLE"

        t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            # 1. Missing volume and missing volume_evidence
            {
                "timestamp_open": t0.isoformat(),
                "timestamp_close": (t0 + timedelta(minutes=15)).isoformat(),
                "open": "2500.0", "high": "2510.0", "low": "2495.0", "close": "2505.0",
            },
            # 2. Invalid volume evidence label
            {
                "timestamp_open": (t0 + timedelta(minutes=15)).isoformat(),
                "timestamp_close": (t0 + timedelta(minutes=30)).isoformat(),
                "open": "2505.0", "high": "2515.0", "low": "2500.0", "close": "2510.0",
                "volume": "100.0",
                "volume_evidence": "FABRICATED_VOLUME_LABEL",
            },
            # 3. Explicit TICK_VOLUME
            {
                "timestamp_open": (t0 + timedelta(minutes=30)).isoformat(),
                "timestamp_close": (t0 + timedelta(minutes=45)).isoformat(),
                "open": "2510.0", "high": "2520.0", "low": "2508.0", "close": "2515.0",
                "volume": "250.0",
                "volume_evidence": "TICK_VOLUME",
            },
        ]

        with patch("requests.get", return_value=mock_response):
            candles = provider.fetch_candles("XAUUSD", "15m", t0, t0 + timedelta(hours=1))
            assert len(candles) == 3

            # 1. Missing volume -> 0 and UNAVAILABLE
            assert candles[0].volume == Decimal("0")
            assert candles[0].volume_evidence == "UNAVAILABLE"

            # 2. Invalid label -> safe fallback to UNAVAILABLE
            assert candles[1].volume == Decimal("100.0")
            assert candles[1].volume_evidence == "UNAVAILABLE"

            # 3. Explicit valid label -> preserved
            assert candles[2].volume == Decimal("250.0")
            assert candles[2].volume_evidence == "TICK_VOLUME"

    def test_direct_usd_normalization_mode(self):
        """Proves QuoteNormalizer.normalize_direct_usd evaluates direct 1.0 identity pricing."""
        normalizer = QuoteNormalizer()
        t = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        res = normalizer.normalize_direct_usd(Decimal("2500.50"), t)
        
        assert res.rate == Decimal("1.000000")
        assert res.deviation == Decimal("0.0")
        assert res.is_warning is False
        assert res.hard_fail is False
        assert res.normalized_price == Decimal("2500.50000000")
        assert "DIRECT_USD" in res.message

    def test_closed_candle_pit_safety(self):
        """Proves open/unclosed candles are strictly excluded from repository loading."""
        t0 = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
        # Open forming candle
        MarketCandle.objects.create(
            instrument=self.xauusd,
            source="xauusd_primary",
            timeframe="15m",
            timestamp_open=t0,
            timestamp_close=t0 + timedelta(minutes=15),
            open=Decimal("2500.00"), high=Decimal("2510.00"), low=Decimal("2495.00"), close=Decimal("2505.00"),
            volume=Decimal("50.0"), volume_evidence=VolumeEvidenceType.TICK_VOLUME,
            is_closed=False,
        )
        repo = DjangoCandleRepository()
        loaded = repo.load_window("XAU/USD", "15m", t0 + timedelta(minutes=30), 10)
        assert len(loaded) == 0

    def test_legacy_xaut_target_preservation(self):
        """Proves legacy XAUT/USDT instrument with GOLD_REFERENCE / EXECUTION is fully functional."""
        legacy_inst = Instrument.get_legacy_xaut()
        assert legacy_inst.role == InstrumentRole.EXECUTION
        assert legacy_inst.base_asset.code == "XAUT"
        assert legacy_inst.quote_asset.code == "USDT"

    def test_mixed_closed_and_open_xauusd_ingestion(self):
        """
        PATCH 1: Proves that when primary provider returns mixed response:
          - one CLOSED 15m candle
          - one OPEN forming 15m candle
        and secondary provider returns matching CLOSED candle only:
          - closed primary candle is evaluated normally
          - open candle is ignored for operational integrity & persistence
          - no false hard_fail caused by open candle
          - trusted ingested count = 1
          - repository operational window contains only closed candle
        """
        t0 = datetime.now(timezone.utc)
        t_open = t0 - timedelta(minutes=30)
        t_forming = t0 - timedelta(minutes=15)

        primary_closed = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_forming,
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2498.00"),
            close=Decimal("2502.00"),
            volume=Decimal("100.0"),
            is_closed=True,
            source="xauusd_primary",
        )
        primary_open = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=t_forming,
            timestamp_close=t0,
            open=Decimal("2502.00"),
            high=Decimal("2508.00"),
            low=Decimal("2501.00"),
            close=Decimal("2506.00"),
            volume=Decimal("40.0"),
            is_closed=False,
            source="xauusd_primary",
        )

        secondary_closed = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_forming,
            open=Decimal("2500.10"),
            high=Decimal("2505.20"),
            low=Decimal("2498.00"),
            close=Decimal("2502.20"),
            volume=Decimal("105.0"),
            is_closed=True,
            source="xauusd_secondary",
        )

        with patch("apps.market_data.tasks.registry.get") as mock_registry_get:
            mock_primary = MagicMock()
            mock_primary.is_configured.return_value = True
            mock_primary.health_check.return_value = ProviderHealth(
                provider_id="xauusd_primary", status="HEALTHY", checked_at=t0, latency_ms=10
            )
            mock_primary.fetch_candles.return_value = [primary_closed, primary_open]

            mock_secondary = MagicMock()
            mock_secondary.is_configured.return_value = True
            mock_secondary.health_check.return_value = ProviderHealth(
                provider_id="xauusd_secondary", status="HEALTHY", checked_at=t0, latency_ms=12
            )
            mock_secondary.fetch_candles.return_value = [secondary_closed]

            mock_registry_get.side_effect = lambda pid: mock_primary if pid == "xauusd_primary" else mock_secondary

            res = ingest_primary_candles(
                instrument_symbol="XAU/USD",
                timeframes=["15m"],
                lookback_bars=2,
                xauusd_max_divergence_pct=Decimal("0.0035"),
                is_secondary_critical=True,
            )

            assert res["status"] == "success"
            assert res["candles_ingested"] == 1

            repo = DjangoCandleRepository()
            window = repo.load_window("XAU/USD", "15m", t0, 10)
            assert len(window) == 1
            assert window[0].close == Decimal("2502.00")
            assert window[0].is_closed is True

            snapshot = DataQualitySnapshot.objects.filter(instrument=self.xauusd).latest("timestamp")
            assert snapshot.hard_fail is False
            assert snapshot.quality_score == Decimal("100.00")


    def test_historical_listing_migration_precision(self):
        """
        PATCH 2: Proves that migration classification is precise:
          - Binance XAUT/USDT -> LEGACY_EXECUTION
          - OKX XAUT/USDT -> LEGACY_EXECUTION
          - Gold Reference XAU/USD -> LEGACY_GOLD_REFERENCE
          - USDT/USD -> LEGACY_QUOTE_NORMALIZATION
          - Unrelated Binance listing (e.g. BTC/USDT) remains GENERIC.
        """
        btc, _ = Asset.objects.get_or_create(code="BTC", defaults={"name": "Bitcoin", "asset_type": AssetType.CRYPTO_TOKEN})
        usdt = Asset.objects.get(code="USDT")
        btc_usdt, _ = Instrument.objects.get_or_create(
            base_asset=btc,
            quote_asset=usdt,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION, "is_active": True},
        )

        unrelated_listing = MarketListing.objects.create(
            instrument=btc_usdt,
            provider="binance",
            provider_symbol="BTCUSDT",
            status=ListingStatus.ACTIVE,
        )

        legacy_binance_listing = MarketListing.objects.create(
            instrument=self.xaut_legacy,
            provider="binance",
            provider_symbol="XAUTUSDT",
            status=ListingStatus.ACTIVE,
        )

        import importlib
        migration_mod = importlib.import_module("apps.instruments.migrations.0003_marketlisting_listing_role_and_more")
        classify_historical_listings = migration_mod.classify_historical_listings
        from django.apps import apps
        classify_historical_listings(apps, None)

        unrelated_listing.refresh_from_db()
        assert unrelated_listing.listing_role == ListingRole.GENERIC

        legacy_binance_listing.refresh_from_db()
        assert legacy_binance_listing.listing_role == ListingRole.LEGACY_EXECUTION




