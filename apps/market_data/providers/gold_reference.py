"""Canonical Gold Reference Provider (XAU/USD) and Secondary PAXG Confirmation Provider."""
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import requests
import structlog
from .base import MarketDataProvider, RawCandle, ProviderHealth, TickerSnapshot

logger = structlog.get_logger(__name__)


class GoldReferenceProvider(MarketDataProvider):
    """
    Canonical Gold Reference Provider (XAU/USD).
    Strictly provides underlying spot gold benchmark data.
    If no true canonical commodity feed is configured, reports NOT_CONFIGURED rather
    than silently substituting secondary tokenized proxies (P1-08).
    """

    def __init__(self, canonical_url: Optional[str] = None, timeout: float = 10.0):
        self._canonical_url = canonical_url
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "gold_reference"

    def is_configured(self) -> bool:
        return bool(self._canonical_url)

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch canonical spot XAU/USD gold benchmark candles."""
        if not self.is_configured():
            logger.error("canonical_gold_reference_not_configured")
            raise RuntimeError(
                "GOLD_REFERENCE_UNAVAILABLE: Canonical XAU/USD market feed is NOT_CONFIGURED. "
                "Per specification (P1-08), proxy substitution is strictly prohibited for canonical reference."
            )

        try:
            response = requests.get(
                self._canonical_url,
                params={
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw_items = response.json()
            candles: list[RawCandle] = []
            for item in raw_items:
                candles.append(
                    RawCandle(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp_open=datetime.fromisoformat(item["timestamp_open"]),
                        timestamp_close=datetime.fromisoformat(item["timestamp_close"]),
                        open=Decimal(str(item["open"])),
                        high=Decimal(str(item["high"])),
                        low=Decimal(str(item["low"])),
                        close=Decimal(str(item["close"])),
                        volume=Decimal(str(item.get("volume", 0))),
                        is_closed=bool(item.get("is_closed", True)),
                        source=self.provider_id,
                    )
                )
            return candles
        except Exception as e:
            logger.error("canonical_gold_reference_fetch_failed", error=str(e))
            raise RuntimeError(f"Canonical XAU/USD fetch error: {e}") from e

    def health_check(self) -> ProviderHealth:
        """Probe canonical gold reference connectivity."""
        now = datetime.now(timezone.utc)
        if not self.is_configured():
            return ProviderHealth(
                provider_id=self.provider_id,
                status="NOT_CONFIGURED",
                latency_ms=None,
                checked_at=now,
                error_message="Canonical XAU/USD endpoint is NOT_CONFIGURED. Awaiting direct commodity feed.",
            )

        t0 = time.perf_counter()
        try:
            res = requests.get(f"{self._canonical_url}/health", timeout=self._timeout)
            latency = int((time.perf_counter() - t0) * 1000)
            status = "HEALTHY" if res.status_code == 200 else "DEGRADED"
            return ProviderHealth(
                provider_id=self.provider_id,
                status=status,
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


class PaxgConfirmationProvider(MarketDataProvider):
    """
    Secondary Tokenized Gold Confirmation Provider (PAXG/USDT).
    Role: GOLD_CONFIRMATION (secondary proxy, NOT canonical XAU/USD).
    """

    BASE_URL = "https://api.binance.com"

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "paxg_confirmation"

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch secondary confirmation klines (PAXGUSDT) from Binance."""
        proxy_url = f"{self._base_url}/api/v3/klines"
        start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        params = {
            "symbol": "PAXGUSDT",
            "interval": timeframe,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }

        try:
            res = requests.get(proxy_url, params=params, timeout=self._timeout)
            res.raise_for_status()
            data = res.json()
        except requests.exceptions.RequestException as e:
            logger.error("paxg_proxy_fetch_failed", error=str(e))
            raise RuntimeError(f"PAXG confirmation proxy fetch error: {e}") from e

        candles = []
        now_utc = datetime.now(timezone.utc)
        for item in data:
            open_ms, o, h, l, c, v, close_ms = item[0], item[1], item[2], item[3], item[4], item[5], item[6]
            ts_open = datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc)
            ts_close = datetime.fromtimestamp(close_ms / 1000.0, tz=timezone.utc)
            candles.append(
                RawCandle(
                    symbol="PAXG/USDT",
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
        """Probe Binance connectivity for PAXG confirmation proxy."""
        now = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        try:
            res = requests.get(f"{self._base_url}/api/v3/ping", timeout=self._timeout)
            latency = int((time.perf_counter() - t0) * 1000)
            return ProviderHealth(
                provider_id=self.provider_id,
                status="HEALTHY" if res.status_code == 200 else "DEGRADED",
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
