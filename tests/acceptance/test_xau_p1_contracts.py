"""Acceptance Test Contracts for Phase 1 XAUUSD Data Engine (XAU-P1-01 and XAU-P1-02)."""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from django.test import TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType, MarketListing
from apps.market_data.models import MarketCandle, VolumeEvidenceType, CandleQualityFlag
from apps.market_data.normalization import QuoteNormalizer, NormalizationCheckResult
from apps.market_data.integrity import MarketIntegrityEngine, XauUsdIntegrityResult
from apps.market_data.providers.xauusd_spot import XauUsdSpotProvider
from apps.market_data.providers.xauusd_secondary import SecondaryXauUsdSpotProvider
from apps.market_data.repositories import DjangoCandleRepository
from engine.core.types import VolumeEvidenceType as EngineVolumeEvidenceType


class TestXauP1Contracts(TestCase):
    """Test suite validating XAU-P1-01 and XAU-P1-02 acceptance contracts."""

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

        # Seed Canonical XAUUSD Target Instrument
        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.PRIMARY_XAUUSD, "is_active": True},
        )

        # Seed Historical Legacy XAUT Instrument
        self.xaut_legacy, _ = Instrument.objects.get_or_create(
            base_asset=self.xaut,
            quote_asset=self.usdt,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION, "is_active": True},
        )

        # Seed Listings
        self.listing_primary, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            provider="xauusd_primary",
            defaults={"provider_symbol": "XAUUSD", "fallback_priority": 0},
        )
        self.listing_secondary, _ = MarketListing.objects.get_or_create(
            instrument=self.xauusd,
            provider="xauusd_secondary",
            defaults={"provider_symbol": "XAUUSD", "fallback_priority": 1},
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

    def test_xau_p1_02_secondary_provider_disagreement_and_integrity(self):
        """
        XAU-P1-02: Secondary independent XAUUSD provider disagreement / integrity contract.
        Proves:
          - Primary and secondary provider roles are distinct.
          - Disagreement is treated as integrity evidence, NOT directional alpha.
          - Thresholds are configurable / not frozen.
          - Missing critical provider fails closed.
          - Proxy XAUT/PAXG cannot silently substitute direct XAUUSD.
        """
        integrity_engine = MarketIntegrityEngine()

        # Case 1: Agreement within threshold
        res_agree = integrity_engine.verify_xauusd_multi_provider_integrity(
            primary_price=Decimal("2500.00"),
            secondary_price=Decimal("2502.00"),
            max_divergence_pct=Decimal("0.0035"),  # 0.35%
        )
        assert res_agree.is_valid is True
        assert res_agree.hard_fail is False
        assert res_agree.is_disagreement is False

        # Case 2: Material Disagreement (> threshold)
        res_disagree = integrity_engine.verify_xauusd_multi_provider_integrity(
            primary_price=Decimal("2500.00"),
            secondary_price=Decimal("2520.00"),  # 0.80% divergence > 0.35%
            max_divergence_pct=Decimal("0.0035"),
        )
        assert res_disagree.is_valid is False
        assert res_disagree.hard_fail is True
        assert res_disagree.is_disagreement is True
        assert "DISAGREEMENT" in res_disagree.message
        assert "zero directional alpha" in res_disagree.message

        # Case 3: Missing Primary (Fails closed)
        res_missing_primary = integrity_engine.verify_xauusd_multi_provider_integrity(
            primary_price=None,
            secondary_price=Decimal("2500.00"),
        )
        assert res_missing_primary.hard_fail is True
        assert "PRIMARY_XAUUSD_UNAVAILABLE" in res_missing_primary.message

        # Case 4: Missing Critical Secondary (Fails closed)
        res_missing_secondary = integrity_engine.verify_xauusd_multi_provider_integrity(
            primary_price=Decimal("2500.00"),
            secondary_price=None,
            is_secondary_critical=True,
        )
        assert res_missing_secondary.hard_fail is True
        assert "SECONDARY_XAUUSD_UNAVAILABLE" in res_missing_secondary.message

        # Case 5: Proxy Substitution Rejected
        res_proxy = integrity_engine.verify_xauusd_multi_provider_integrity(
            primary_price=Decimal("2500.00"),
            secondary_price=Decimal("2501.00"),
            is_proxy_substitution=True,
        )
        assert res_proxy.hard_fail is True
        assert "Proxy substitution rejected" in res_proxy.message

    def test_volume_evidence_transport(self):
        """Proves volume semantics are transported faithfully without fabrication."""
        t0 = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        
        # Real volume
        c1 = MarketCandle.objects.create(
            instrument=self.xauusd,
            source="xauusd_primary",
            timeframe="1h",
            timestamp_open=t0,
            timestamp_close=t0 + timedelta(hours=1),
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2505.00"),
            volume=Decimal("5000.0"),
            volume_evidence=VolumeEvidenceType.PROXY_VOLUME,
            is_closed=True,
        )

        repo = DjangoCandleRepository()
        loaded = repo.load_window("XAU/USD", "1h", t0 + timedelta(hours=2), 1)
        assert loaded[0].volume_evidence == EngineVolumeEvidenceType.PROXY_VOLUME

    def test_provider_not_configured_fail_closed(self):
        """Proves unconfigured spot providers report NOT_CONFIGURED and fail closed safely."""
        provider = XauUsdSpotProvider(feed_url=None)
        assert provider.is_configured() is False
        
        health = provider.health_check()
        assert health.status == "NOT_CONFIGURED"

        with pytest.raises(RuntimeError, match="PRIMARY_XAUUSD_UNAVAILABLE"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc),
            )

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
