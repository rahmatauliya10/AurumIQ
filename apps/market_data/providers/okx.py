"""OKX Market Data Provider for XAUT-USDT public candles, tickers, and instruments."""
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any
import requests
import structlog
from .base import MarketDataProvider, RawCandle, ProviderHealth, TickerSnapshot

logger = structlog.get_logger(__name__)


class OKXProvider(MarketDataProvider):
    """OKX Public REST Market Data Adapter."""

    BASE_URL = "https://www.okx.com"
    TIMEFRAME_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1H",
        "4h": "4H",
        "1d": "1D",
    }
    TIMEFRAME_MINUTES = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "okx"

    def map_timeframe(self, timeframe: str) -> str:
        interval = self.TIMEFRAME_MAP.get(timeframe)
        if not interval:
            raise ValueError(f"Unsupported timeframe '{timeframe}' for OKX provider.")
        return interval

    def check_symbol_status(self, symbol: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Query OKX public instruments API to check if symbol is active ('live') vs 'suspend'.
        """
        okx_inst_id = symbol.replace("/", "-").upper()
        if "-" not in okx_inst_id and okx_inst_id.endswith("USDT"):
            okx_inst_id = f"{okx_inst_id[:-4]}-USDT"

        url = f"{self._base_url}/api/v5/public/instruments"
        params = {"instType": "SPOT", "instId": okx_inst_id}

        try:
            res = requests.get(url, params=params, timeout=self._timeout)
            res.raise_for_status()
            data = res.json()
            inst_list = data.get("data", [])
            if not inst_list:
                return False, "NOT_FOUND", {}
            inst_info = inst_list[0]
            state = inst_info.get("state", "unknown")
            is_tradable = (state == "live")
            return is_tradable, state, inst_info
        except Exception as e:
            logger.warning("okx_check_symbol_status_failed", symbol=symbol, error=str(e))
            return False, f"ERROR: {e}", {}

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch historical candles from OKX endpoint."""
        interval = self.map_timeframe(timeframe)
        url = f"{self._base_url}/api/v5/market/candles"
        
        okx_inst_id = symbol.replace("/", "-").upper()
        if "-" not in okx_inst_id and okx_inst_id.endswith("USDT"):
            okx_inst_id = f"{okx_inst_id[:-4]}-USDT"

        start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        
        params = {
            "instId": okx_inst_id,
            "bar": interval,
            "after": str(end_ms),
            "before": str(start_ms - 1),
            "limit": "100",
        }

        try:
            response = requests.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()
            res_json = response.json()
            if res_json.get("code") != "0":
                raise RuntimeError(f"OKX API error: {res_json.get('msg')}")
            data = res_json.get("data", [])
        except requests.exceptions.RequestException as e:
            logger.error("okx_fetch_candles_failed", symbol=symbol, error=str(e))
            raise RuntimeError(f"OKX fetch_candles error: {e}") from e

        candles: list[RawCandle] = []
        tf_minutes = self.TIMEFRAME_MINUTES.get(timeframe, 15)
        bar_duration = timedelta(minutes=tf_minutes)
        now_utc = datetime.now(timezone.utc)

        for item in data:
            # OKX schema: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            open_ms = int(item[0])
            o, h, l, c, v = item[1], item[2], item[3], item[4], item[5]
            confirm = str(item[8]) if len(item) > 8 else "1"

            ts_open = datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc)
            ts_close = ts_open + bar_duration
            
            # P1-01: confirm == "1" represents completed candle; confirm == "0" is incomplete
            is_closed = (confirm == "1") and (ts_close <= now_utc)

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

        # OKX returns newest first; sort ascending chronologically
        candles.sort(key=lambda c: c.timestamp_open)
        return candles

    def health_check(self) -> ProviderHealth:
        """Probe OKX connectivity and verify XAUT-USDT instrument status."""
        url = f"{self._base_url}/api/v5/public/time"
        t0 = time.perf_counter()
        now = datetime.now(timezone.utc)
        
        try:
            response = requests.get(url, timeout=self._timeout)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if response.status_code == 200 and response.json().get("code") == "0":
                # Check symbol status
                is_tradable, state_str, _ = self.check_symbol_status("XAUT-USDT")
                if not is_tradable and state_str != "NOT_FOUND":
                    return ProviderHealth(
                        provider_id=self.provider_id,
                        status="DEGRADED",
                        latency_ms=latency_ms,
                        checked_at=now,
                        error_message=f"Instrument state is {state_str}",
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
        """Fetch current bid/ask ticker."""
        url = f"{self._base_url}/api/v5/market/ticker"
        okx_inst_id = symbol.replace("/", "-").upper()
        if "-" not in okx_inst_id and okx_inst_id.endswith("USDT"):
            okx_inst_id = f"{okx_inst_id[:-4]}-USDT"
        params = {"instId": okx_inst_id}

        try:
            response = requests.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()
            res_json = response.json()
            data = res_json.get("data", [])
            if not data:
                return None
            ticker = data[0]
            bid = Decimal(str(ticker["bidPx"]))
            ask = Decimal(str(ticker["askPx"]))
            last = Decimal(str(ticker["last"]))
            return TickerSnapshot(
                symbol=symbol,
                bid=bid,
                ask=ask,
                last=last,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning("okx_fetch_ticker_failed", symbol=symbol, error=str(e))
            return None
