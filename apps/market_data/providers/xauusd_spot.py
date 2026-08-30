"""Primary Institutional Spot Gold (XAU/USD) Market Data Provider."""
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Any, Dict, Tuple
import requests
import structlog
from .base import MarketDataProvider, RawCandle, ProviderHealth, TickerSnapshot

logger = structlog.get_logger(__name__)


def _get_setting_or_env(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        from django.conf import settings
        return getattr(settings, key, os.environ.get(key, default))
    except Exception:
        return os.environ.get(key, default)


VALID_VOLUME_EVIDENCE = {"REAL_VOLUME", "TICK_VOLUME", "PROXY_VOLUME", "UNAVAILABLE"}


class XauUsdSpotProvider(MarketDataProvider):
    """
    Primary Spot Gold (XAU/USD) Data Provider.
    Config-driven and vendor-abstracted (e.g. institutional spot ECN / broker stream).
    If no production feed is configured, reports NOT_CONFIGURED and fails closed.
    Never silently substitutes a crypto-gold proxy for direct spot XAU/USD.
    """

    def __init__(
        self,
        feed_url: Optional[str] = None,
        api_key: Optional[str] = None,
        default_volume_evidence: str = "UNAVAILABLE",
        timeout: float = 10.0,
    ):
        self._feed_url = feed_url or _get_setting_or_env("XAUUSD_PRIMARY_FEED_URL")
        self._api_key = api_key or _get_setting_or_env("XAUUSD_PRIMARY_API_KEY")
        self._default_volume_evidence = (
            default_volume_evidence if default_volume_evidence in VALID_VOLUME_EVIDENCE else "UNAVAILABLE"
        )
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "xauusd_primary"

    def is_configured(self) -> bool:
        return bool(self._feed_url)

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch closed spot XAU/USD candles within [start, end] window."""
        if not self.is_configured():
            logger.error("primary_xauusd_not_configured")
            raise RuntimeError(
                "PRIMARY_XAUUSD_UNAVAILABLE: Primary spot XAU/USD market feed is NOT_CONFIGURED. "
                "Per specification (XAU-P1-01), proxy substitution is strictly prohibited."
            )

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = requests.get(
                self._feed_url,
                headers=headers,
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
                raw_vol_evidence = item.get("volume_evidence")
                if raw_vol_evidence and raw_vol_evidence in VALID_VOLUME_EVIDENCE:
                    vol_evidence = raw_vol_evidence
                else:
                    vol_evidence = self._default_volume_evidence

                raw_vol = item.get("volume")
                if raw_vol is None or str(raw_vol).strip() == "":
                    vol = Decimal("0")
                    vol_evidence = "UNAVAILABLE"
                else:
                    try:
                        vol = Decimal(str(raw_vol))
                    except Exception:
                        vol = Decimal("0")
                        vol_evidence = "UNAVAILABLE"

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
                        volume=vol,
                        is_closed=bool(item.get("is_closed", True)),
                        source=self.provider_id,
                        volume_evidence=vol_evidence,
                    )
                )
            return candles
        except Exception as e:
            logger.error("primary_xauusd_fetch_failed", error=str(e))
            raise RuntimeError(f"Primary XAU/USD fetch error: {e}") from e

    def fetch_ticker(self, symbol: str) -> Optional[TickerSnapshot]:
        """Fetch real-time spot quote snapshot for spread monitoring."""
        if not self.is_configured():
            return None

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            res = requests.get(
                f"{self._feed_url}/ticker",
                headers=headers,
                params={"symbol": symbol},
                timeout=self._timeout,
            )
            res.raise_for_status()
            data = res.json()
            return TickerSnapshot(
                symbol=symbol,
                bid=Decimal(str(data["bid"])),
                ask=Decimal(str(data["ask"])),
                last=Decimal(str(data.get("last", data["ask"]))),
                timestamp=datetime.fromisoformat(data["timestamp"]),
            )
        except Exception as e:
            logger.warning("primary_xauusd_ticker_failed", error=str(e))
            return None

    def health_check(self) -> ProviderHealth:
        """Probe primary spot XAU/USD connectivity."""
        now = datetime.now(timezone.utc)
        if not self.is_configured():
            return ProviderHealth(
                provider_id=self.provider_id,
                status="NOT_CONFIGURED",
                latency_ms=None,
                checked_at=now,
                error_message="Primary spot XAU/USD endpoint is NOT_CONFIGURED. Awaiting production provider binding.",
            )

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        t0 = time.perf_counter()
        try:
            res = requests.get(f"{self._feed_url}/health", headers=headers, timeout=self._timeout)
            latency = int((time.perf_counter() - t0) * 1000)
            status = "HEALTHY" if res.status_code == 200 else "DEGRADED"
            return ProviderHealth(
                provider_id=self.provider_id,
                status=status,
                latency_ms=latency,
                checked_at=now,
                error_message="" if status == "HEALTHY" else f"HTTP status {res.status_code}",
            )
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            return ProviderHealth(
                provider_id=self.provider_id,
                status="UNHEALTHY",
                latency_ms=latency,
                checked_at=now,
                error_message=str(e),
            )
