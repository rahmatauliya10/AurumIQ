"""Smoke tests for Django setup, settings, URLs, and health endpoints."""
import pytest
from django.conf import settings
from django.urls import reverse
from django.test import Client
from engine.core.interfaces import CandleRepository
from engine.core.types import CandleData


@pytest.mark.unit
def test_django_settings_loaded():
    """Verify core Django settings are properly loaded."""
    assert settings.SECRET_KEY is not None
    assert settings.TIME_ZONE == "UTC"
    assert "apps.accounts.apps.AccountsConfig" in settings.INSTALLED_APPS
    assert settings.ENGINE_VERSION == "1.0.0"


@pytest.mark.unit
def test_health_check_endpoint():
    """Verify the health check HTTP endpoint returns status ok."""
    client = Client()
    response = client.get(reverse("health_check"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "xaut-signal-intelligence"}


@pytest.mark.unit
def test_engine_protocol_runtime_checkable():
    """Verify CandleRepository Protocol is decoupled and runtime checkable."""
    assert isinstance(CandleRepository, type)
    
    class DummyRepo:
        def get_first_bar_open_after(self, timestamp, timeframe):
            return None
        def load_window(self, instrument, timeframe, end_at, bars):
            return []
        def load_resolution_candles(self, start, end, timeframe="1m"):
            return []

    dummy = DummyRepo()
    assert isinstance(dummy, CandleRepository)
