"""Unit tests for Phase 1 XAUUSD adapters, registry, repository, and normalization."""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase
from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType, MarketListing
from apps.market_data.models import MarketCandle, VolumeEvidenceType, CandleQualityFlag
from apps.market_data.normalization import QuoteNormalizer
from apps.market_data.integrity import MarketIntegrityEngine
from apps.market_data.providers.base import RawCandle
from apps.market_data.providers.xauusd_spot import XauUsdSpotProvider
from apps.market_data.providers.xauusd_secondary import SecondaryXauUsdSpotProvider
from apps.market_data.providers.registry import registry
from apps.market_data.repositories import DjangoCandleRepository
from engine.core.types import VolumeEvidenceType as EngineVolumeEvidenceType


class TestXauUsdUnit(TestCase):
    """Unit test cases for XAUUSD ingestion infrastructure."""

    def test_registry_contains_xauusd_providers(self):
        """Verify registry contains primary and secondary XAUUSD spot providers."""
        assert registry.has("xauusd_primary") is True
        assert registry.has("xauusd_secondary") is True

        p1 = registry.get("xauusd_primary")
        assert isinstance(p1, XauUsdSpotProvider)
        assert p1.provider_id == "xauusd_primary"

        p2 = registry.get("xauusd_secondary")
        assert isinstance(p2, SecondaryXauUsdSpotProvider)
        assert p2.provider_id == "xauusd_secondary"

    def test_xauusd_spot_provider_fetch_mock(self):
        """Test XauUsdSpotProvider candle parsing when configured."""
        provider = XauUsdSpotProvider(feed_url="https://mock-feed.internal", api_key="secret-key")
        assert provider.is_configured() is True

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "timestamp_open": "2026-08-30T10:00:00+00:00",
                "timestamp_close": "2026-08-30T10:15:00+00:00",
                "open": "2500.10",
                "high": "2505.50",
                "low": "2498.00",
                "close": "2504.00",
                "volume": "1500",
                "volume_evidence": "TICK_VOLUME",
                "is_closed": True,
            }
        ]

        with patch("requests.get", return_value=mock_response):
            candles = provider.fetch_candles(
                "XAUUSD", "15m",
                datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 30, 10, 15, tzinfo=timezone.utc),
            )
            assert len(candles) == 1
            c = candles[0]
            assert c.symbol == "XAUUSD"
            assert c.close == Decimal("2504.00")
            assert c.volume_evidence == "TICK_VOLUME"
            assert c.source == "xauusd_primary"

    def test_secondary_xauusd_spot_provider_health_mock(self):
        """Test SecondaryXauUsdSpotProvider health check."""
        provider = SecondaryXauUsdSpotProvider(feed_url="https://mock-sec.internal")
        
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("requests.get", return_value=mock_response):
            health = provider.health_check()
            assert health.status == "HEALTHY"
            assert health.provider_id == "xauusd_secondary"
            assert health.latency_ms is not None

    def test_direct_usd_normalizer_invalid_price(self):
        """Test QuoteNormalizer.normalize_direct_usd handles non-positive prices safely."""
        normalizer = QuoteNormalizer()
        res_zero = normalizer.normalize_direct_usd(Decimal("0.0"))
        assert res_zero.hard_fail is True
        assert res_zero.normalized_price is None

        res_none = normalizer.normalize_direct_usd(None)
        assert res_none.hard_fail is True
        assert res_none.normalized_price is None
