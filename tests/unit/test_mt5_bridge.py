"""Comprehensive unit tests for the MT5 Read-Only Market Data Bridge.

Validates read-only safety boundaries, timeframe normalization, AST audit against
trade execution calls, and route schemas using mocks without requiring a live terminal.
"""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import os
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from tools.mt5_bridge.config import Mt5BridgeConfig
from tools.mt5_bridge.timeframe import (
    normalize_timeframe_str,
    map_timeframe_to_mt5,
    get_timeframe_delta,
    TIMEFRAME_DELTAS,
    TIMEFRAME_TO_MT5_CONSTANT,
)
from tools.mt5_bridge.schemas import (
    BarItem,
    BarsResponse,
    HealthResponse,
    ProviderResponse,
    QuoteResponse,
    SymbolResponse,
    TickItem,
    TicksResponse,
)
from tools.mt5_bridge.adapter import Mt5ReadOnlyAdapter
from tools.mt5_bridge.main import app


# -----------------------------------------------------------------------------
# 1. AST & Static Safety Tests (Zero Trade Execution APIs)
# -----------------------------------------------------------------------------

def test_mt5_bridge_has_no_trade_execution_capability():
    """
    GOVERNANCE REQUIREMENT:
    Verify via AST parsing that tools/mt5_bridge/ contains zero references
    to forbidden trade execution, order placement, or position modification APIs.
    """
    forbidden_tokens = {
        "order_send",
        "order_check",
        "order_calc_margin",
        "order_calc_profit",
        "positions_total",
        "TRADE_ACTION_DEAL",
        "TRADE_ACTION_PENDING",
        "TRADE_ACTION_SLTP",
        "TRADE_ACTION_MODIFY",
        "TRADE_ACTION_CLOSE_BY",
    }

    bridge_dir = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "mt5_bridge")
    assert os.path.isdir(bridge_dir), f"Directory {bridge_dir} must exist"

    found_violations = []

    for root, _, files in os.walk(bridge_dir):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Text check
                for tok in forbidden_tokens:
                    if tok in content:
                        found_violations.append(f"{file} contains token '{tok}'")

                # AST check
                tree = ast.parse(content, filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Attribute) and node.attr in forbidden_tokens:
                        found_violations.append(f"{file} calls forbidden attribute '{node.attr}' at line {node.lineno}")
                    elif isinstance(node, ast.Name) and node.id in forbidden_tokens:
                        found_violations.append(f"{file} references forbidden symbol '{node.id}' at line {node.lineno}")

    assert len(found_violations) == 0, f"Trade execution capabilities detected in bridge:\n" + "\n".join(found_violations)


def test_fastapi_has_only_read_only_get_routes():
    """Verify FastAPI application registers only GET endpoints (no POST/PUT/DELETE)."""
    for route in app.routes:
        methods = getattr(route, "methods", set())
        assert methods.issubset({"GET", "HEAD", "OPTIONS"}), f"Forbidden route method {methods} on path {route.path}"


def test_config_forbids_binding_to_public_interfaces():
    """Security rule: config must reject binding to 0.0.0.0."""
    with patch.dict(os.environ, {"MT5_BRIDGE_HOST": "0.0.0.0"}):
        with pytest.raises(ValueError, match="0.0.0.0 is strictly forbidden"):
            Mt5BridgeConfig.from_env()


# -----------------------------------------------------------------------------
# 2. Timeframe Mapping & UTC Normalization
# -----------------------------------------------------------------------------

def test_timeframe_normalization_and_mapping():
    """Verify mapping of 1m, 5m, 15m, 1h, 4h, 1d to MT5 constants and deltas."""
    assert normalize_timeframe_str("15M") == "15m"
    assert normalize_timeframe_str("h1") == "1h"
    assert normalize_timeframe_str("4H") == "4h"
    assert normalize_timeframe_str("D1") == "1d"
    assert normalize_timeframe_str("M1") == "1m"
    assert normalize_timeframe_str("5m") == "5m"

    assert map_timeframe_to_mt5("15m") == 15
    assert map_timeframe_to_mt5("1h") == 16385
    assert map_timeframe_to_mt5("4h") == 16388
    assert map_timeframe_to_mt5("1d") == 16408

    assert get_timeframe_delta("15m") == timedelta(minutes=15)
    assert get_timeframe_delta("1h") == timedelta(hours=1)
    assert get_timeframe_delta("4h") == timedelta(hours=4)
    assert get_timeframe_delta("1d") == timedelta(days=1)

    with pytest.raises(ValueError, match="Unsupported timeframe"):
        normalize_timeframe_str("37m")


# -----------------------------------------------------------------------------
# 3. Spread Calculation & Quote Validation
# -----------------------------------------------------------------------------

def test_quote_response_spread_calculation_and_validation():
    """Verify relative spread bps calculation and validation rules."""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    bid = Decimal("2500.00")
    ask = Decimal("2500.25")
    mid = (bid + ask) / Decimal("2")
    spread_abs = ask - bid
    spread_bps = (spread_abs / mid) * Decimal("10000")

    quote = QuoteResponse(
        canonical_symbol="XAUUSD",
        broker_symbol="XAUUSDm",
        timestamp=now,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_absolute=spread_abs,
        spread_bps=spread_bps,
    )

    assert quote.bid == Decimal("2500.00")
    assert quote.ask == Decimal("2500.25")
    assert quote.mid == Decimal("2500.125")
    assert quote.spread_absolute == Decimal("0.25")
    assert round(quote.spread_bps, 4) == round(Decimal("0.99995"), 4)


def test_quote_probe_rejects_crossed_market_or_negative_price():
    """Verify adapter rejects crossed market (ask < bid) or zero/negative prices."""
    adapter = Mt5ReadOnlyAdapter()
    adapter._connected = True
    adapter._is_connected_override = True

    mock_tick = MagicMock()
    mock_tick.bid = 2500.50
    mock_tick.ask = 2500.00  # Crossed market
    mock_tick.time = int(datetime.now(timezone.utc).timestamp())
    mock_tick.last = 0.0
    mock_tick.volume = 0.0

    with patch("tools.mt5_bridge.adapter.mt5.symbol_info_tick", return_value=mock_tick):
        with pytest.raises(ValueError, match="Crossed market quote violation"):
            adapter.get_live_quote("XAUUSD")

    mock_tick.bid = -10.0
    mock_tick.ask = 2500.00
    with patch("tools.mt5_bridge.adapter.mt5.symbol_info_tick", return_value=mock_tick):
        with pytest.raises(ValueError, match="Invalid non-positive quote prices"):
            adapter.get_live_quote("XAUUSD")


# -----------------------------------------------------------------------------
# 4. Sensitive Metadata Exclusion & Provider Attribution
# -----------------------------------------------------------------------------

def test_provider_metadata_excludes_sensitive_account_data():
    """Verify ProviderResponse contains safe metadata only and no login/balance."""
    adapter = Mt5ReadOnlyAdapter()
    adapter._connected = True
    adapter._is_connected_override = True
    adapter._server_name = "Exness-Trial"
    adapter._broker_company = "Exness Technologies Ltd"
    adapter._is_demo = True
    adapter._discovered_symbol = "XAUUSDm"

    meta = adapter.get_provider_metadata()

    assert meta.provider == "mt5_exness_demo"
    assert meta.broker == "Exness Technologies Ltd"
    assert meta.environment == "DEMO"
    assert meta.canonical_instrument == "XAUUSD"
    assert meta.provider_symbol == "XAUUSDm"
    assert meta.read_only is True

    # Check that model schema has no sensitive fields
    fields = ProviderResponse.model_fields.keys()
    for forbidden in ("login", "account", "balance", "equity", "password", "credit", "margin"):
        assert forbidden not in fields


# -----------------------------------------------------------------------------
# 5. Symbol Discovery (Canonical XAUUSD vs Broker Suffix)
# -----------------------------------------------------------------------------

def test_gold_symbol_discovery_distinguishes_broker_suffixes_and_excludes_xaut():
    """Verify dynamic discovery finds XAUUSDm or XAUUSD while strictly rejecting XAUT."""
    adapter = Mt5ReadOnlyAdapter()
    adapter._connected = True
    adapter._is_connected_override = True

    mock_sym1 = MagicMock()
    mock_sym1.name = "XAUTUSDT"
    mock_sym1.path = "Crypto/Tether Gold"
    mock_sym1.currency_base = "XAUT"
    mock_sym1.currency_profit = "USDT"

    mock_sym2 = MagicMock()
    mock_sym2.name = "XAUUSDm"
    mock_sym2.path = "Forex/Metals/Standard"
    mock_sym2.currency_base = "XAU"
    mock_sym2.currency_profit = "USD"

    mock_sym3 = MagicMock()
    mock_sym3.name = "EURUSD"
    mock_sym3.path = "Forex"
    mock_sym3.currency_base = "EUR"
    mock_sym3.currency_profit = "USD"

    with patch("tools.mt5_bridge.adapter.mt5.symbols_get", return_value=[mock_sym1, mock_sym2, mock_sym3]):
        discovered = adapter.discover_gold_symbol()
        assert discovered == "XAUUSDm"
        assert adapter.config.canonical_instrument == "XAUUSD"


# -----------------------------------------------------------------------------
# 6. OHLC Bar Normalization & UTC Awareness
# -----------------------------------------------------------------------------

def test_bar_item_ohlc_consistency():
    """Verify BarItem validates OHLC relationships and timezone awareness."""
    t_open = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 2, 10, 15, tzinfo=timezone.utc)

    bar = BarItem(
        timestamp_open=t_open,
        timestamp_close=t_close,
        open=Decimal("2500.00"),
        high=Decimal("2505.50"),
        low=Decimal("2498.20"),
        close=Decimal("2502.10"),
        tick_volume=1240,
        real_volume=0,
        spread=15,
        is_closed=True,
        volume_evidence="TICK_VOLUME",
    )

    assert bar.high >= max(bar.open, bar.close)
    assert bar.low <= min(bar.open, bar.close)
    assert bar.timestamp_close > bar.timestamp_open
    assert bar.timestamp_open.tzinfo is not None


# -----------------------------------------------------------------------------
# 7. FastAPI Route Integration with TestClient (Mocked MT5)
# -----------------------------------------------------------------------------

def test_fastapi_endpoints_with_mocked_terminal():
    """Test all allowed GET routes via FastAPI TestClient."""
    from tools.mt5_bridge.main import adapter as global_adapter

    global_adapter._connected = True
    global_adapter._is_connected_override = True
    global_adapter._terminal_build = 4400
    global_adapter._terminal_name = "MetaTrader 5"
    global_adapter._server_name = "Exness-Trial"
    global_adapter._discovered_symbol = "XAUUSD"

    client = TestClient(app)

    # 1. /health
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "OK"
    assert data["connected"] is True

    # 2. /provider
    res = client.get("/provider")
    assert res.status_code == 200
    data = res.json()
    assert data["provider"] == "mt5_exness_demo"
    assert data["read_only"] is True
    assert "login" not in data

    # 3. /quote
    mock_tick = MagicMock()
    mock_tick.bid = 2510.00
    mock_tick.ask = 2510.20
    mock_tick.last = 2510.10
    mock_tick.volume = 15.0
    mock_tick.time = int(datetime.now(timezone.utc).timestamp())
    mock_tick.time_msc = int(datetime.now(timezone.utc).timestamp() * 1000)

    with patch("tools.mt5_bridge.adapter.mt5.symbol_info_tick", return_value=mock_tick):
        res = client.get("/quote")
        assert res.status_code == 200
        q = res.json()
        assert q["canonical_symbol"] == "XAUUSD"
        assert Decimal(q["bid"]) == Decimal("2510.00")
        assert Decimal(q["ask"]) == Decimal("2510.20")
        assert Decimal(q["spread_absolute"]) == Decimal("0.20")

    # 4. /bars with empty result
    with patch("tools.mt5_bridge.adapter.mt5.copy_rates_from_pos", return_value=[]):
        res = client.get("/bars?timeframe=15m&limit=10")
        assert res.status_code == 200
        bars_data = res.json()
        assert bars_data["count"] == 0
        assert bars_data["bars"] == []

    # 5. /ticks with empty result
    with patch("tools.mt5_bridge.adapter.mt5.copy_ticks_range", return_value=[]):
        t_start = "2026-09-01T00:00:00Z"
        t_end = "2026-09-01T01:00:00Z"
        res = client.get(f"/ticks?start={t_start}&end={t_end}&limit=50")
        assert res.status_code == 200
        ticks_data = res.json()
        assert ticks_data["count"] == 0
        assert ticks_data["ticks"] == []
