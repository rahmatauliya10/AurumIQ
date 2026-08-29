"""Stablecoin Quote Normalization Rate Provider (USDT/USD) & Proxy (USDT/USDC)."""
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List, Tuple
import requests
import structlog
from .base import MarketDataProvider, RawCandle, ProviderHealth

logger = structlog.get_logger(__name__)


class UsdtUsdRateProvider(MarketDataProvider):
    """
    USDT Quote Normalization Rate Provider.
    
    Roles:
      - Canonical USDT/USD (if direct fiat feed available)
      - USDT_USDC_PROXY via Binance USDC/USDT klines
      
    Safety Rules (P1-09):
      - Never silently defaults to Decimal("1.0") when feed fails
      - Returns None / UNAVAILABLE on fetch failure
      - Historical normalization fetches point-in-time rates <= candle timestamp
    """

    BASE_URL = "https://api.binance.com"

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "usdt_usd"

    def get_current_rate(self) -> Optional[Decimal]:
        """
        Fetch real-time proxy rate.
        Returns None if unavailable (NEVER silently defaults to 1.0).
        """
        url = f"{self._base_url}/api/v3/ticker/price"
        params = {"symbol": "USDCUSDT"}
        try:
            res = requests.get(url, params=params, timeout=self._timeout)
            res.raise_for_status()
            data = res.json()
            # 1 USDC = X USDT -> 1 USDT = (1 / X) USD approx
            price = Decimal(str(data["price"]))
            if price <= 0:
                logger.error("invalid_usdc_price", price=float(price))
                return None
            return (Decimal("1.0") / price).quantize(Decimal("0.000001"))
        except Exception as e:
            logger.error("usdt_rate_fetch_failed", error=str(e))
            return None

    def fetch_historical_rates(
        self,
        start: datetime,
        end: datetime,
        interval: str = "15m",
    ) -> List[Tuple[datetime, Decimal]]:
        """
        Fetch historical rate series for Point-in-Time quote normalization.
        Ensures backtests use historical rate on or before target timestamp.
        """
        url = f"{self._base_url}/api/v3/klines"
        start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        params = {
            "symbol": "USDCUSDT",
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }

        try:
            res = requests.get(url, params=params, timeout=self._timeout)
            res.raise_for_status()
            raw_klines = res.json()
            rates: List[Tuple[datetime, Decimal]] = []
            for item in raw_klines:
                open_ms = item[0]
                close_price = Decimal(str(item[4]))
                if close_price > 0:
                    rate = (Decimal("1.0") / close_price).quantize(Decimal("0.000001"))
                    ts = datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc)
                    rates.append((ts, rate))
            return rates
        except Exception as e:
            logger.error("usdt_historical_rates_failed", error=str(e))
            return []

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch USDC/USDT klines."""
        url = f"{self._base_url}/api/v3/klines"
        start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        params = {
            "symbol": "USDCUSDT",
            "interval": timeframe,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        try:
            res = requests.get(url, params=params, timeout=self._timeout)
            res.raise_for_status()
            data = res.json()
        except requests.exceptions.RequestException as e:
            logger.error("usdt_usd_fetch_candles_failed", error=str(e))
            raise RuntimeError(f"UsdtUsdRateProvider error: {e}") from e

        candles: list[RawCandle] = []
        now_utc = datetime.now(timezone.utc)
        for item in data:
            open_ms, o, h, l, c, v, close_ms = item[0], item[1], item[2], item[3], item[4], item[5], item[6]
            ts_open = datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc)
            ts_close = datetime.fromtimestamp(close_ms / 1000.0, tz=timezone.utc)
            candles.append(
                RawCandle(
                    symbol="USDT/USD",
                    timeframe=timeframe,
                    timestamp_open=ts_open,
                    timestamp_close=ts_close,
                    open=Decimal(str(o)),
                    high=Decimal(str(h)),
                    low=Decimal(str(l)),
                    close=Decimal(str(c)),
                    volume=Decimal(str(v)),
                    is_closed=ts_close <= now_utc,
                    source=self.provider_id,
                )
            )
        return candles

    def health_check(self) -> ProviderHealth:
        """Probe Binance connectivity for USDCUSDT rate."""
        now = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        try:
            rate = self.get_current_rate()
            latency = int((time.perf_counter() - t0) * 1000)
            if rate is None:
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status="UNHEALTHY",
                    latency_ms=latency,
                    checked_at=now,
                    error_message="Unable to fetch USDC/USDT rate from exchange.",
                )
            return ProviderHealth(
                provider_id=self.provider_id,
                status="HEALTHY",
                latency_ms=latency,
                checked_at=now,
            )
        except Exception as e:
            return ProviderHealth(
                provider_id=self.provider_id,
                status="UNHEALTHY",
                latency_ms=int((time.perf_counter() - t0) * 1000),
                checked_at=now,
                error_message=str(e),
            )
