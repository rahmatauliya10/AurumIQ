"""
Unit tests for Live Analysis dashboard presentation, template rendering, ASGI static handling,
and deterministic candle gap classification.
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from django.test import Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.conf import settings

from apps.dashboard.api import classify_candle_gap
from apps.dashboard.views import LiveAnalysisView
from apps.instruments.models import Instrument, Asset
from apps.market_data.models import MarketCandle

User = get_user_model()


@pytest.mark.django_db
def test_live_analysis_view_renders_authenticated(client):
    user = User.objects.create_user(username="analyst_test", password="password123")
    client.force_login(user)

    url = reverse("dashboard:analysis")
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Live Analysis" in content
    assert "plotly-chart-container" in content
    assert "dashboard.css" in content
    assert "data-gap-alert-container" in content
    assert "rangebreaks" in content


@pytest.mark.django_db
def test_chart_data_api_payload_structure(client):
    user = User.objects.create_user(username="api_tester", password="password123")
    client.force_login(user)

    # Ensure canonical instrument exists
    xau, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": "COMMODITY"})
    usd, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": "FIAT"})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=xau, quote_asset=usd, role="GOLD_REFERENCE",
        defaults={"instrument_type": "SPOT", "is_active": True}
    )

    now = datetime(2026, 9, 15, 6, 15, 0, tzinfo=timezone.utc)
    t_open = now - timedelta(minutes=15)
    MarketCandle.objects.create(
        instrument=inst,
        source="twelve_data",
        timeframe="15m",
        timestamp_open=t_open,
        timestamp_close=now,
        open=Decimal("2500.00"),
        high=Decimal("2505.00"),
        low=Decimal("2498.00"),
        close=Decimal("2502.50"),
        volume=Decimal("150.0"),
        is_closed=True,
        quote_rate=Decimal("1.0"),
        data_quality_flag="PASS"
    )

    url = reverse("dashboard:api_chart", kwargs={"timeframe": "15m"})
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "timestamps" in data
    assert "timestamps_close" in data
    assert "gaps" in data
    assert len(data["timestamps"]) == 1
    assert data["open"][0] == 2500.00
    assert data["close"][0] == 2502.50


def test_asgi_static_handler_in_debug(settings):
    settings.DEBUG = True
    from django.core.asgi import get_asgi_application
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
    base_app = get_asgi_application()
    app = ASGIStaticFilesHandler(base_app)
    assert isinstance(app, ASGIStaticFilesHandler)


def test_gap_classifier_weekend_market_closure():
    """Requirement 9A: Weekend market closure is classified as PROVIDER_MARKET_CLOSURE."""
    # Friday 21:00 UTC to Sunday 22:00 UTC
    fri_close = datetime(2026, 9, 11, 21, 0, 0, tzinfo=timezone.utc)
    sun_open = datetime(2026, 9, 13, 22, 0, 0, tzinfo=timezone.utc)
    classification = classify_candle_gap(fri_close, sun_open, "15m")
    assert classification == "PROVIDER_MARKET_CLOSURE"

    # Friday 15:30 UTC to Sunday 02:30 UTC (observed in Twelve Data dataset)
    fri_close_early = datetime(2026, 9, 11, 15, 30, 0, tzinfo=timezone.utc)
    sun_open_early = datetime(2026, 9, 13, 2, 30, 0, tzinfo=timezone.utc)
    assert classify_candle_gap(fri_close_early, sun_open_early, "15m") == "PROVIDER_MARKET_CLOSURE"


def test_gap_classifier_weekday_intrasession_data_loss():
    """Requirement 9B: Weekday data loss is classified as INTRASESSION_DATA_GAP, never market closure."""
    # Monday 03:00 UTC to Monday 16:00 UTC (13-hour gap)
    mon_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    mon_open = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)
    classification = classify_candle_gap(mon_close, mon_open, "15m")
    assert classification == "INTRASESSION_DATA_GAP"


def test_gap_classifier_unknown_large_weekday_gap():
    """Requirement 9C: Large unknown weekday gap is INTRASESSION_DATA_GAP (fail-closed, never collapsed)."""
    wed_close = datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)
    thu_open = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)
    classification = classify_candle_gap(wed_close, thu_open, "15m")
    assert classification == "INTRASESSION_DATA_GAP"


def test_gap_classifier_normal_continuous_candles():
    """Requirement 9D: Normal continuous candles have zero positive gap."""
    t1_close = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    t2_open = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    assert classify_candle_gap(t1_close, t2_open, "15m") == "NORMAL"


def test_gap_classifier_alignment_transition():
    """Requirement 3 & 9: 4h DST alignment shift is classified as PROVIDER_ALIGNMENT_TRANSITION."""
    t1_close = datetime(2026, 3, 9, 21, 0, 0, tzinfo=timezone.utc)
    t2_open = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)  # 3 hour gap instead of 4
    assert classify_candle_gap(t1_close, t2_open, "4h") == "PROVIDER_ALIGNMENT_TRANSITION"


def test_gap_classifier_unresolved_extreme_gap():
    """Requirement 3 & 9: Extreme anomaly (>14 days) is classified as UNRESOLVED (never collapsed)."""
    t1_close = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    t2_open = datetime(2026, 1, 20, 0, 0, 0, tzinfo=timezone.utc)
    assert classify_candle_gap(t1_close, t2_open, "15m") == "UNRESOLVED"


@pytest.mark.django_db
def test_hostile_chart_api_gap_segregation(client):
    """
    Requirements 9A-9F:
    Verify API correctly separates weekend closure from intrasession data loss,
    ensures DB timestamps are unchanged, and no synthetic candles are created.
    """
    user = User.objects.create_user(username="hostile_tester", password="password123")
    client.force_login(user)

    xau, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": "COMMODITY"})
    usd, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": "FIAT"})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=xau, quote_asset=usd, role="GOLD_REFERENCE",
        defaults={"instrument_type": "SPOT", "is_active": True}
    )

    # 1. Friday final candle (2026-09-11 21:00 close)
    t_fri_close = datetime(2026, 9, 11, 21, 0, 0, tzinfo=timezone.utc)
    t_fri_open = t_fri_close - timedelta(minutes=15)
    c1 = MarketCandle.objects.create(
        instrument=inst, source="twelve_data", timeframe="15m",
        timestamp_open=t_fri_open, timestamp_close=t_fri_close,
        open=Decimal("2500"), high=Decimal("2501"), low=Decimal("2499"), close=Decimal("2500"),
        volume=Decimal("100"), is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
    )

    # 2. Reopen candle on Sunday evening / early Monday (2026-09-14 03:00 close) -> Gap 1: PROVIDER_MARKET_CLOSURE
    t_mon1_open = datetime(2026, 9, 14, 2, 45, 0, tzinfo=timezone.utc)
    t_mon1_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    c2 = MarketCandle.objects.create(
        instrument=inst, source="twelve_data", timeframe="15m",
        timestamp_open=t_mon1_open, timestamp_close=t_mon1_close,
        open=Decimal("2501"), high=Decimal("2502"), low=Decimal("2500"), close=Decimal("2501"),
        volume=Decimal("100"), is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
    )

    # 3. Monday candle after 13-hour missing interval (2026-09-14 16:00 open) -> Gap 2: INTRASESSION_DATA_GAP
    t_mon2_open = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)
    t_mon2_close = t_mon2_open + timedelta(minutes=15)
    c3 = MarketCandle.objects.create(
        instrument=inst, source="twelve_data", timeframe="15m",
        timestamp_open=t_mon2_open, timestamp_close=t_mon2_close,
        open=Decimal("2503"), high=Decimal("2504"), low=Decimal("2502"), close=Decimal("2503"),
        volume=Decimal("100"), is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
    )

    url = reverse("dashboard:api_chart", kwargs={"timeframe": "15m"})
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()

    # Verify timestamps match exactly
    assert len(data["timestamps"]) == 3
    assert len(data["gaps"]) == 2

    # Gap 1: Weekend market closure
    gap_weekend = data["gaps"][0]
    assert gap_weekend["classification"] == "PROVIDER_MARKET_CLOSURE"
    assert gap_weekend["is_market_closure"] is True
    assert gap_weekend["prev_close"] == t_fri_close.isoformat()
    assert gap_weekend["next_open"] == t_mon1_open.isoformat()

    # Gap 2: Intrasession data gap (51 missing 15m intervals between 03:00 and 16:00 UTC)
    gap_intrasession = data["gaps"][1]
    assert gap_intrasession["classification"] == "INTRASESSION_DATA_GAP"
    assert gap_intrasession["is_market_closure"] is False
    assert gap_intrasession["prev_close"] == t_mon1_close.isoformat()
    assert gap_intrasession["next_open"] == t_mon2_open.isoformat()
    assert gap_intrasession["missed_intervals"] == 51

    # Requirement 9E & 9F: Database candles remained exactly 3, timestamps unmodified
    candles_db = list(MarketCandle.objects.filter(instrument=inst, timeframe="15m").order_by("timestamp_open"))
    assert len(candles_db) == 3
    assert candles_db[0].timestamp_open == t_fri_open
    assert candles_db[1].timestamp_open == t_mon1_open
    assert candles_db[2].timestamp_open == t_mon2_open
