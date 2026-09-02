"""Pydantic schemas for the read-only MT5 market data bridge."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health diagnostic status of the MT5 bridge and local terminal connection."""
    status: str = Field(..., description="Service status: OK, DEGRADED, or NOT_CONNECTED")
    connected: bool = Field(..., description="True if local MT5 terminal IPC connection is active")
    terminal_build: Optional[int] = Field(None, description="MetaTrader 5 terminal build number")
    terminal_name: Optional[str] = Field(None, description="Connected terminal binary name")
    timestamp: datetime = Field(..., description="UTC timestamp of the diagnostic check")
    error: Optional[str] = Field(None, description="Detailed diagnostic error message if any")


class ProviderResponse(BaseModel):
    """Safe metadata attribution describing the connected market data source."""
    provider: str = Field("mt5_exness_demo", description="AurumIQ provider identifier")
    broker: str = Field("Exness", description="Broker company name")
    environment: str = Field("DEMO", description="Account trading environment: DEMO or REAL")
    server: str = Field(..., description="Sanitized non-sensitive trade server name")
    canonical_instrument: str = Field("XAUUSD", description="Canonical AurumIQ instrument symbol")
    provider_symbol: str = Field(..., description="Actual discovered broker symbol on terminal")
    read_only: bool = Field(True, description="Strictly true; trading endpoints are completely absent")


class SymbolResponse(BaseModel):
    """Instrument metadata and contract specifications on the connected broker."""
    canonical_symbol: str = Field("XAUUSD", description="Canonical internal symbol")
    broker_symbol: str = Field(..., description="Broker terminal symbol")
    description: str = Field(..., description="Symbol long description")
    path: str = Field(..., description="Terminal symbol tree category / path")
    currency_base: str = Field(..., description="Base asset (e.g. XAU)")
    currency_profit: str = Field(..., description="Profit / quote asset (e.g. USD)")
    digits: int = Field(..., description="Decimal precision digits")
    point: float = Field(..., description="Minimum price change increment (point)")
    spread: int = Field(..., description="Current spread in points")
    trade_mode: str = Field(..., description="Trading mode description (DISABLED, FULL, etc.)")
    volume_min: float = Field(..., description="Minimum transaction lot size")
    volume_max: float = Field(..., description="Maximum transaction lot size")
    volume_step: float = Field(..., description="Lot size step increment")


class QuoteResponse(BaseModel):
    """Instantaneous live quote ticker snapshot for spread and liquidity monitoring."""
    canonical_symbol: str = Field("XAUUSD", description="Canonical instrument")
    broker_symbol: str = Field(..., description="Broker terminal symbol")
    timestamp: datetime = Field(..., description="Timezone-aware UTC timestamp of quote")
    bid: Decimal = Field(..., description="Authoritative bid price")
    ask: Decimal = Field(..., description="Authoritative ask price")
    mid: Decimal = Field(..., description="Calculated mid price (bid + ask) / 2")
    spread_absolute: Decimal = Field(..., description="Absolute spread (ask - bid)")
    spread_bps: Decimal = Field(..., description="Relative spread in basis points: (ask - bid) / mid * 10000")
    last: Optional[Decimal] = Field(None, description="Last transaction price if reported")
    volume: Optional[Decimal] = Field(None, description="Tick volume if reported")


class BarItem(BaseModel):
    """Standardized single candlestick record."""
    timestamp_open: datetime = Field(..., description="Interval start UTC timestamp")
    timestamp_close: datetime = Field(..., description="Interval end UTC timestamp")
    open: Decimal = Field(..., description="Authoritative opening price")
    high: Decimal = Field(..., description="Authoritative highest price")
    low: Decimal = Field(..., description="Authoritative lowest price")
    close: Decimal = Field(..., description="Authoritative closing price")
    tick_volume: int = Field(..., description="Tick frequency count within interval")
    real_volume: int = Field(0, description="Centralized traded volume if reported")
    spread: int = Field(0, description="Reported terminal spread at interval start in points")
    is_closed: bool = Field(True, description="True if bar is finalized")
    volume_evidence: str = Field("TICK_VOLUME", description="Volume classification: TICK_VOLUME or UNAVAILABLE")


class BarsResponse(BaseModel):
    """Collection of historical candlesticks for analytical research and backtesting."""
    canonical_symbol: str = Field("XAUUSD", description="Canonical instrument")
    broker_symbol: str = Field(..., description="Broker terminal symbol")
    timeframe: str = Field(..., description="Timeframe interval string (15m, 1h, etc.)")
    count: int = Field(..., description="Total bars delivered")
    bars: List[BarItem] = Field(..., description="Chronologically sorted bar records")


class TickItem(BaseModel):
    """Granular point-in-time bid/ask tick event."""
    timestamp: datetime = Field(..., description="Timezone-aware UTC tick timestamp")
    bid: Decimal = Field(..., description="Bid price")
    ask: Decimal = Field(..., description="Ask price")
    mid: Decimal = Field(..., description="Mid price")
    spread_absolute: Decimal = Field(..., description="Absolute spread (ask - bid)")
    spread_bps: Decimal = Field(..., description="Spread in basis points")
    last: Optional[Decimal] = Field(None, description="Last traded price")
    volume: Optional[Decimal] = Field(None, description="Tick trade volume")
    flags: int = Field(0, description="MT5 TICK_FLAG bitmask")


class TicksResponse(BaseModel):
    """Collection of historical bid/ask ticks for spread distribution and empirical analysis."""
    canonical_symbol: str = Field("XAUUSD", description="Canonical instrument")
    broker_symbol: str = Field(..., description="Broker terminal symbol")
    count: int = Field(..., description="Total ticks delivered")
    ticks: List[TickItem] = Field(..., description="Chronologically sorted ticks")


class HistoryTimeframeCapability(BaseModel):
    """Historical data capability for one timeframe."""
    accessible: bool = Field(..., description="True if bars can be queried")
    earliest: Optional[datetime] = Field(None, description="Earliest available UTC bar timestamp")
    latest: Optional[datetime] = Field(None, description="Latest available UTC bar timestamp")
    sample_count: int = Field(0, description="Sample count retrieved during probe")


class HistoryCapabilityResponse(BaseModel):
    """Overview of server historical data boundaries across all analytical timeframes."""
    canonical_symbol: str = Field("XAUUSD", description="Canonical instrument")
    broker_symbol: str = Field(..., description="Broker terminal symbol")
    timeframes: Dict[str, HistoryTimeframeCapability] = Field(..., description="Capability per timeframe")
