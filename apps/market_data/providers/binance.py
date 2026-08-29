"""Binance Market Data Provider for XAUT/USDT public klines, book tickers, and exchangeInfo."""
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any
import requests
import structlog
from .base import MarketDataProvider, RawCandle, ProviderHealth, TickerSnapshot

logger = structlog.get_logger(__name__)


class BinanceProvider(MarketDataProvider):
    """Binance Public REST Market Data Adapter."""

    BASE_URL = "https://api.binance.com"
    TIMEFRAME_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "binance"

    def map_timeframe(self, timeframe: str) -> str:
        interval = self.TIMEFRAME_MAP.get(timeframe)
        if not interval:
            raise ValueError(f"Unsupported timeframe '{timeframe}' for Binance provider.")
        return interval

    def check_symbol_status(self, symbol: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Query Binance exchangeInfo to check if symbol is active ('TRADING') vs 'BREAK'/'HALT'.
        """
        clean_symbol = symbol.replace("/", "").replace("-", "").upper()
        url = f"{self._base_url}/api/v3/exchangeInfo"
        params = {"symbol": clean_symbol}
        
        try:
            res = requests.get(url, params=params, timeout=self._timeout)
            res.raise_for_status()
            data = res.json()
            symbols = data.get("symbols", [])
            if not symbols:
                return False, "NOT_FOUND", {}
            s_info = symbols[0]
            status = s_info.get("status", "UNKNOWN")
            is_tradable = (status == "TRADING")
            return is_tradable, status, s_info
        except Exception as e:
            logger.warning("binance_check_symbol_status_failed", symbol=symbol, error=str(e))
            return False, f"ERROR: {e}", {}

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch historical klines from Binance public endpoint."""
        interval = self.map_timeframe(timeframe)
        url = f"{self._base_url}/api/v3/klines"
        
        start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        
        params = {
            "symbol": symbol.replace("/", "").replace("-", "").upper(),
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }

        try:
            response = requests.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error("binance_fetch_candles_failed", symbol=symbol, error=str(e))
            raise RuntimeError(f"Binance fetch_candles error: {e}") from e

        candles: list[RawCandle] = []
        now_utc = datetime.now(timezone.utc)

        for item in data:
            open_ms, o, h, l, c, v, close_ms = item[0], item[1], item[2], item[3], item[4], item[5], item[6]
            ts_open = datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc)
            ts_close = datetime.fromtimestamp(close_ms / 1000.0, tz=timezone.utc)
            
            # A candle is strictly closed only if its close timestamp has completely passed
            is_closed = ts_close <= now_utc

            candles.append(
                RawCandle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp_open=ts_open,
                    timestamp_close=ts_close,
                    open=Decimal(str(o)),
                    high=Decimal(str(h)),
                    low=Decimal(str(l)),
                    close=Decimal(str(c)),
                    volume=Decimal(str(v)),
                    is_closed=is_closed,
                    source=self.provider_id,
                )
            )

        return candles

    def health_check(self) -> ProviderHealth:
        """Probe Binance connectivity and verify XAUTUSDT symbol status."""
        url = f"{self._base_url}/api/v3/ping"
        t0 = time.perf_counter()
        now = datetime.now(timezone.utc)
        
        try:
            response = requests.get(url, timeout=self._timeout)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if response.status_code == 200:
                # Also verify default symbol status
                is_tradable, status_str, _ = self.check_symbol_status("XAUTUSDT")
                if not is_tradable and status_str != "NOT_FOUND":
                    return ProviderHealth(
                        provider_id=self.provider_id,
                        status="DEGRADED",
                        latency_ms=latency_ms,
                        checked_at=now,
                        error_message=f"Symbol status is {status_str}",
                    )
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status="HEALTHY",
                    latency_ms=latency_ms,
                    checked_at=now,
                )
            return ProviderHealth(
                provider_id=self.provider_id,
                status="DEGRADED",
                latency_ms=latency_ms,
                checked_at=now,
                error_message=f"HTTP {response.status_code}",
            )
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return ProviderHealth(
                provider_id=self.provider_id,
                status="UNHEALTHY",
                latency_ms=latency_ms,
                checked_at=now,
                error_message=str(e),
            )

    def fetch_ticker(self, symbol: str) -> Optional[TickerSnapshot]:
        """Fetch current bid/ask book ticker."""
        url = f"{self._base_url}/api/v3/ticker/bookTicker"
        clean_symbol = symbol.replace("/", "").replace("-", "").upper()
        params = {"symbol": clean_symbol}
        
        try:
            response = requests.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
            bid = Decimal(str(data["bidPrice"]))
            ask = Decimal(str(data["askPrice"]))
            last = (bid + ask) / 2
            return TickerSnapshot(
                symbol=symbol,
                bid=bid,
                ask=ask,
                last=last,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning("binance_fetch_ticker_failed", symbol=symbol, error=str(e))
            return None
