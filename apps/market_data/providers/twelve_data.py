"""Twelve Data XAU/USD Market Data Provider Adapter.

Provides analytical spot gold market data directly from Twelve Data HTTPS API.
Strictly decoupled from execution venues (Exness) per AurumIQ data-readiness protocol.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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


def _sanitize_secret(text: str, secret: Optional[str] = None) -> str:
    """Mask any occurrence of API keys in log/error text."""
    if not text:
        return ""
    clean = re.sub(r"(apikey=)[^&]+", r"\1***MASKED***", text, flags=re.IGNORECASE)
    clean = re.sub(r"(api_key=)[^&]+", r"\1***MASKED***", clean, flags=re.IGNORECASE)
    if secret and secret in clean:
        clean = clean.replace(secret, "***MASKED***")
    return clean


_real_datetime = datetime


def _normalize_to_utc_aware(dt: Any, param_name: str) -> datetime:
    """
    Validate that datetime is timezone-aware and convert strictly to UTC.
    
    Rejects:
    - None or non-datetime instances
    - Naive datetimes (tzinfo is None)
    - Ambiguous tzinfo (dt.utcoffset() is None)
    
    Converts:
    - Aware UTC -> unchanged instant
    - Aware non-UTC offsets (e.g. +07:00, -05:00) -> exact UTC equivalent via astimezone()
    """
    if dt is None:
        raise ValueError(f"MISSING_DATETIME: '{param_name}' cannot be None.")
    if not isinstance(dt, _real_datetime):
        raise TypeError(f"INVALID_DATETIME_TYPE: '{param_name}' must be a datetime instance, got {type(dt).__name__}.")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"NAIVE_DATETIME_FORBIDDEN: '{param_name}' must be timezone-aware. "
            "Naive datetimes are strictly forbidden under AurumIQ canonical XAUUSD contracts."
        )
    return dt.astimezone(timezone.utc)



class TwelveDataProvider(MarketDataProvider):
    """
    Twelve Data Market Data Provider for Spot Gold (XAU/USD).
    
    Provider ID: twelve_data_xauusd
    Canonical Instrument: XAUUSD
    Provider Symbol: XAU/USD
    
    Fail-closed policy:
    - Missing API key
    - HTTP errors / timeout / 429 rate limit
    - Provider error responses
    - Symbol mismatch (never substitute crypto-gold or foreign symbols)
    - Interval mismatch
    - Corrupted or invalid OHLC geometry
    - Invalid timestamps
    """

    CANONICAL_SYMBOL = "XAUUSD"
    PROVIDER_SYMBOL = "XAU/USD"

    TIMEFRAME_MAP: Dict[str, str] = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
    }

    TIMEFRAME_DELTAS: Dict[str, timedelta] = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }

    PROHIBITED_SYMBOLS = {"XAUT", "XAUTUSDT", "PAXG", "PAXGUSDT", "XAUEUR"}

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.twelvedata.com",
        timeout: float = 45.0,
    ):
        self._api_key = api_key or _get_setting_or_env("TWELVE_DATA_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "twelve_data_xauusd"

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip())

    def _get_now_utc(self) -> datetime:
        """Internal helper returning current UTC time, overridable in tests."""
        return datetime.now(timezone.utc)

    def map_timeframe(self, timeframe: str) -> str:
        tf = timeframe.lower().strip()
        if tf not in self.TIMEFRAME_MAP:
            raise KeyError(
                f"UNSUPPORTED_TIMEFRAME: Timeframe '{timeframe}' is not supported by Twelve Data adapter. "
                f"Allowed: {sorted(list(self.TIMEFRAME_MAP.keys()))}"
            )
        return self.TIMEFRAME_MAP[tf]

    def _validate_symbol(self, symbol: str) -> str:
        norm = symbol.replace("/", "").replace("_", "").upper().strip()
        if norm in self.PROHIBITED_SYMBOLS:
            raise ValueError(
                f"PROHIBITED_SYMBOL_FALLBACK: Symbol '{symbol}' is a prohibited proxy or foreign asset. "
                "Substituting crypto-gold for spot XAUUSD is strictly forbidden."
            )
        if norm != self.CANONICAL_SYMBOL:
            raise ValueError(
                f"SYMBOL_MISMATCH: Expected canonical '{self.CANONICAL_SYMBOL}' or provider '{self.PROVIDER_SYMBOL}', "
                f"got '{symbol}'."
            )
        return self.PROVIDER_SYMBOL

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        only_closed: bool = True,
    ) -> list[RawCandle]:
        """
        Fetch authoritative spot XAU/USD candles within [start, end] window.
        
        Guarantees:
        - Strict direct Decimal(str) parsing (never float -> Decimal).
        - Direct UTC-aware timezone normalization.
        - Strict OHLC geometry enforcement.
        - Discards or flags in-progress candle so signal computation uses completed candles only.
        - Fail-closed on missing key, timeout, 429, API errors, symbol/interval mismatch.
        """
        if not self.is_configured():
            logger.error("twelve_data_not_configured")
            raise RuntimeError(
                "TWELVE_DATA_API_KEY_NOT_CONFIGURED: Twelve Data API key is not configured in environment/.env. "
                "Fail-closed per Section 10."
            )

        provider_symbol = self._validate_symbol(symbol)
        provider_interval = self.map_timeframe(timeframe)
        interval_delta = self.TIMEFRAME_DELTAS[timeframe.lower().strip()]

        # Validate and convert strictly to UTC-aware datetime
        start_utc = _normalize_to_utc_aware(start, "start")
        end_utc = _normalize_to_utc_aware(end, "end")

        if start_utc > end_utc:
            raise ValueError(
                f"INVALID_BOUNDED_WINDOW: 'start' ({start_utc.isoformat()}) must be less than or equal to 'end' ({end_utc.isoformat()})."
            )

        params = {
            "symbol": provider_symbol,
            "interval": provider_interval,
            "start_date": start_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "UTC",
            "order": "ASC",
            "apikey": self._api_key,
        }

        url = f"{self._base_url}/time_series"
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
        except requests.exceptions.Timeout as e:
            logger.error("twelve_data_request_timeout")
            raise RuntimeError("TWELVE_DATA_TIMEOUT: Twelve Data request timed out.") from e
        except requests.exceptions.RequestException as e:
            sanitized = _sanitize_secret(str(e), self._api_key)
            logger.error("twelve_data_http_failure", error=sanitized)
            raise RuntimeError(f"TWELVE_DATA_HTTP_FAILURE: {sanitized}") from e

        if resp.status_code == 429:
            logger.error("twelve_data_rate_limit_429")
            raise RuntimeError("TWELVE_DATA_RATE_LIMIT_EXCEEDED: HTTP 429 received from Twelve Data.")

        if resp.status_code != 200:
            sanitized_body = _sanitize_secret(resp.text[:200], self._api_key)
            raise RuntimeError(f"TWELVE_DATA_HTTP_ERROR: HTTP {resp.status_code} — {sanitized_body}")

        try:
            payload = resp.json()
        except Exception as e:
            raise RuntimeError("TWELVE_DATA_INVALID_JSON: Failed to parse JSON response from Twelve Data.") from e

        # Validate provider payload status
        status = payload.get("status")
        if status == "error":
            err_msg = _sanitize_secret(payload.get("message", "Unknown API error"), self._api_key)
            raise RuntimeError(f"TWELVE_DATA_API_ERROR: {err_msg}")

        # Validate meta fields
        meta = payload.get("meta")
        if not meta or not isinstance(meta, dict):
            raise RuntimeError("TWELVE_DATA_MALFORMED_PAYLOAD: Missing or invalid 'meta' field in response.")

        returned_symbol = meta.get("symbol")
        if returned_symbol != provider_symbol:
            raise ValueError(
                f"TWELVE_DATA_SYMBOL_MISMATCH: Expected meta.symbol '{provider_symbol}', got '{returned_symbol}'."
            )

        returned_interval = meta.get("interval")
        if returned_interval != provider_interval:
            raise ValueError(
                f"TWELVE_DATA_INTERVAL_MISMATCH: Expected meta.interval '{provider_interval}', got '{returned_interval}'."
            )

        raw_values = payload.get("values")
        if raw_values is None or not isinstance(raw_values, list):
            raise RuntimeError("TWELVE_DATA_MALFORMED_PAYLOAD: Missing or invalid 'values' array in response.")

        return self._parse_candle_rows(
            raw_values=raw_values,
            timeframe=timeframe,
            interval_delta=interval_delta,
            only_closed=only_closed,
            end_date_utc=end_utc,
        )

    def fetch_historical_page(
        self,
        symbol: str,
        timeframe: str,
        end: datetime,
        outputsize: int = 4900,
    ) -> list[RawCandle]:
        """
        Fetch a bounded historical page of closed candles ending at or before 'end'.

        Guarantees:
        - Max outputsize <= 5000 (default 4900 for safety margin)
        - Strictly UTC-aware end datetime
        - Direct Decimal string parsing
        - Strict OHLC geometry enforcement
        - Closed candles only
        - Chronologically ascending sort
        - Fail-closed on 429, timeout, error payloads
        """
        if not self.is_configured():
            logger.error("twelve_data_not_configured")
            raise RuntimeError(
                "TWELVE_DATA_API_KEY_NOT_CONFIGURED: Twelve Data API key is not configured in environment/.env."
            )

        if outputsize <= 0 or outputsize > 5000:
            raise ValueError(f"INVALID_OUTPUTSIZE: outputsize must be between 1 and 5000, got {outputsize}.")

        provider_symbol = self._validate_symbol(symbol)
        provider_interval = self.map_timeframe(timeframe)
        interval_delta = self.TIMEFRAME_DELTAS[timeframe.lower().strip()]

        end_utc = _normalize_to_utc_aware(end, "end")
        end_str = end_utc.strftime("%Y-%m-%d") if timeframe.lower().strip() == "1d" else end_utc.strftime("%Y-%m-%d %H:%M:%S")

        params = {
            "symbol": provider_symbol,
            "interval": provider_interval,
            "end_date": end_str,
            "outputsize": outputsize,
            "timezone": "UTC",
            "order": "ASC",
            "apikey": self._api_key,
        }

        url = f"{self._base_url}/time_series"
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
        except requests.exceptions.Timeout as e:
            logger.error("twelve_data_request_timeout")
            raise RuntimeError("TWELVE_DATA_TIMEOUT: Twelve Data request timed out.") from e
        except requests.exceptions.RequestException as e:
            sanitized = _sanitize_secret(str(e), self._api_key)
            logger.error("twelve_data_http_failure", error=sanitized)
            raise RuntimeError(f"TWELVE_DATA_HTTP_FAILURE: {sanitized}") from e

        if resp.status_code == 429:
            logger.error("twelve_data_rate_limit_429")
            raise RuntimeError("TWELVE_DATA_RATE_LIMIT_EXCEEDED: HTTP 429 received from Twelve Data.")

        if resp.status_code != 200:
            sanitized_body = _sanitize_secret(resp.text[:200], self._api_key)
            raise RuntimeError(f"TWELVE_DATA_HTTP_ERROR: HTTP {resp.status_code} — {sanitized_body}")

        try:
            payload = resp.json()
        except Exception as e:
            raise RuntimeError("TWELVE_DATA_INVALID_JSON: Failed to parse JSON response from Twelve Data.") from e

        status = payload.get("status")
        if status == "error":
            err_msg = _sanitize_secret(payload.get("message", "Unknown API error"), self._api_key)
            raise RuntimeError(f"TWELVE_DATA_API_ERROR: {err_msg}")

        meta = payload.get("meta")
        if not meta or not isinstance(meta, dict):
            raise RuntimeError("TWELVE_DATA_MALFORMED_PAYLOAD: Missing or invalid 'meta' field in response.")

        returned_symbol = meta.get("symbol")
        if returned_symbol != provider_symbol:
            raise ValueError(
                f"TWELVE_DATA_SYMBOL_MISMATCH: Expected meta.symbol '{provider_symbol}', got '{returned_symbol}'."
            )

        returned_interval = meta.get("interval")
        if returned_interval != provider_interval:
            raise ValueError(
                f"TWELVE_DATA_INTERVAL_MISMATCH: Expected meta.interval '{provider_interval}', got '{returned_interval}'."
            )

        raw_values = payload.get("values")
        if raw_values is None or not isinstance(raw_values, list):
            raise RuntimeError("TWELVE_DATA_MALFORMED_PAYLOAD: Missing or invalid 'values' array in response.")

        return self._parse_candle_rows(
            raw_values=raw_values,
            timeframe=timeframe,
            interval_delta=interval_delta,
            only_closed=True,
            end_date_utc=end_utc,
        )

    def _parse_candle_rows(
        self,
        raw_values: list,
        timeframe: str,
        interval_delta: timedelta,
        only_closed: bool = True,
        end_date_utc: Optional[datetime] = None,
    ) -> list[RawCandle]:
        """Parse raw Twelve Data candle rows into strictly validated, chronologically sorted RawCandle objects."""
        now_utc = self._get_now_utc()
        candles: list[RawCandle] = []

        for row in raw_values:
            if not isinstance(row, dict):
                raise ValueError("TWELVE_DATA_INVALID_CANDLE: Candle row is not a JSON object.")

            for required_key in ("datetime", "open", "high", "low", "close"):
                if required_key not in row:
                    raise ValueError(f"TWELVE_DATA_INVALID_CANDLE: Missing field '{required_key}' in candle.")

            # Parse datetime strictly into UTC
            dt_str = str(row["datetime"]).strip()
            try:
                if len(dt_str) == 10:  # Daily candle: YYYY-MM-DD
                    dt_open = _real_datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                else:  # Intraday candle: YYYY-MM-DD HH:MM:SS
                    dt_open = _real_datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise ValueError(f"TWELVE_DATA_INVALID_TIMESTAMP: Invalid timestamp format '{dt_str}'.") from e

            dt_close = dt_open + interval_delta

            # Bounded window filter: discard any candle starting at or after end_date_utc if requested
            if end_date_utc is not None and dt_open > end_date_utc:
                continue

            # Parse Decimal directly from string — float conversion strictly prohibited
            try:
                open_val = Decimal(str(row["open"]))
                high_val = Decimal(str(row["high"]))
                low_val = Decimal(str(row["low"]))
                close_val = Decimal(str(row["close"]))
            except (InvalidOperation, TypeError, ValueError) as e:
                raise ValueError(f"TWELVE_DATA_INVALID_DECIMAL: Failed to parse OHLC values for {dt_str}.") from e

            # OHLC Geometry constraints validation
            if not (open_val > 0 and high_val > 0 and low_val > 0 and close_val > 0):
                raise ValueError(f"TWELVE_DATA_INVALID_OHLC: Non-positive price at {dt_str}.")
            if not (high_val >= open_val and high_val >= close_val and high_val >= low_val):
                raise ValueError(f"TWELVE_DATA_INVALID_OHLC: High is not maximal at {dt_str}.")
            if not (low_val <= open_val and low_val <= close_val and low_val <= high_val):
                raise ValueError(f"TWELVE_DATA_INVALID_OHLC: Low is not minimal at {dt_str}.")

            # Closed candle evaluation: candle is closed iff close timestamp <= now_utc
            is_closed = dt_close <= now_utc

            if only_closed and not is_closed:
                # Discard in-progress candle from analytical set
                continue

            # Volume semantics: Twelve Data XAU/USD does not provide real trade volume
            vol_val = Decimal("0")
            if "volume" in row and row["volume"] is not None and str(row["volume"]).strip():
                try:
                    vol_val = Decimal(str(row["volume"]))
                except Exception:
                    vol_val = Decimal("0")

            candles.append(
                RawCandle(
                    symbol=self.CANONICAL_SYMBOL,
                    timeframe=timeframe.lower().strip(),
                    timestamp_open=dt_open,
                    timestamp_close=dt_close,
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    volume=vol_val,
                    is_closed=is_closed,
                    source=self.provider_id,
                    volume_evidence="UNAVAILABLE",
                )
            )

        candles.sort(key=lambda c: c.timestamp_open)
        return candles

    def fetch_ticker(self, symbol: str) -> Optional[TickerSnapshot]:
        """
        Spot gold ticker snapshot.
        
        Twelve Data XAU/USD is an analytical reference feed, not an Exness execution venue.
        Per Section 10 & 11, bid/ask and spread are NOT_AVAILABLE and NOT_CONFIGURED.
        Fabricating execution bid/ask quotes is prohibited.
        """
        return None

    def health_check(self) -> ProviderHealth:
        """Probe Twelve Data API endpoint health."""
        now = datetime.now(timezone.utc)
        if not self.is_configured():
            return ProviderHealth(
                provider_id=self.provider_id,
                status="NOT_CONFIGURED",
                latency_ms=None,
                checked_at=now,
                error_message="Twelve Data API key is NOT_CONFIGURED. Awaiting TWELVE_DATA_API_KEY.",
            )

        t0 = time.perf_counter()
        try:
            url = f"{self._base_url}/api_usage"
            resp = requests.get(url, params={"apikey": self._api_key}, timeout=self._timeout)
            latency = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status="HEALTHY",
                    latency_ms=latency,
                    checked_at=now,
                    error_message="",
                )
            elif resp.status_code == 429:
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status="DEGRADED",
                    latency_ms=latency,
                    checked_at=now,
                    error_message="Rate limit exceeded (HTTP 429).",
                )
            else:
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status="UNHEALTHY",
                    latency_ms=latency,
                    checked_at=now,
                    error_message=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            clean_err = _sanitize_secret(str(e), self._api_key)
            return ProviderHealth(
                provider_id=self.provider_id,
                status="UNHEALTHY",
                latency_ms=latency,
                checked_at=now,
                error_message=clean_err,
            )

    def get_api_usage(self) -> Dict[str, Any]:
        """
        Query Twelve Data /api_usage endpoint to obtain current plan limits and daily credits used.

        Returns dictionary with:
        - daily_usage: int (credits consumed today UTC)
        - plan_daily_limit: int (e.g. 800 for basic)
        - current_usage: int (per-minute usage)
        - plan_limit: int (per-minute limit)
        - plan_category: str (e.g. 'basic')

        Raises RuntimeError on network failure, 429, or non-200 responses.
        API key is strictly sanitized from all error messages.
        """
        if not self.is_configured():
            raise RuntimeError("TWELVE_DATA_UNCONFIGURED: API key is not configured.")

        url = f"{self._base_url}/api_usage"
        try:
            resp = requests.get(url, params={"apikey": self._api_key}, timeout=self._timeout)
        except requests.exceptions.RequestException as e:
            sanitized = _sanitize_secret(str(e), self._api_key)
            logger.error("twelve_data_api_usage_http_failure", error=sanitized)
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_HTTP_FAILURE: {sanitized}") from e

        if resp.status_code == 429:
            logger.warning("twelve_data_rate_limited", status=429)
            raise RuntimeError("TWELVE_DATA_RATE_LIMITED: 429 Too Many Requests")

        if resp.status_code != 200:
            sanitized = _sanitize_secret(resp.text, self._api_key)
            logger.error("twelve_data_api_usage_error", status_code=resp.status_code, response=sanitized)
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_ERROR: HTTP {resp.status_code} - {sanitized}")

        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_INVALID_JSON: {e}") from e

        if not isinstance(data, dict):
            raise RuntimeError("TWELVE_DATA_API_USAGE_MALFORMED: Response payload is not a JSON object.")

        if "daily_usage" not in data or data["daily_usage"] is None:
            raise RuntimeError("TWELVE_DATA_API_USAGE_MALFORMED: Missing required 'daily_usage' field.")

        try:
            daily_usage = int(data["daily_usage"])
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_MALFORMED: Malformed 'daily_usage': {e}") from e

        if daily_usage < 0:
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_MALFORMED: Negative 'daily_usage' ({daily_usage}).")

        if "plan_daily_limit" not in data or data["plan_daily_limit"] is None:
            raise RuntimeError("TWELVE_DATA_API_USAGE_MALFORMED: Missing required 'plan_daily_limit' field.")

        try:
            plan_daily_limit = int(data["plan_daily_limit"])
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_MALFORMED: Malformed 'plan_daily_limit': {e}") from e

        if plan_daily_limit <= 0:
            raise RuntimeError(f"TWELVE_DATA_API_USAGE_MALFORMED: Invalid 'plan_daily_limit' <= 0 ({plan_daily_limit}).")

        current_usage = 0
        if "current_usage" in data and data["current_usage"] is not None:
            try:
                current_usage = int(data["current_usage"])
                if current_usage < 0:
                    raise ValueError("current_usage is negative")
            except Exception as e:
                raise RuntimeError(f"TWELVE_DATA_API_USAGE_MALFORMED: Malformed 'current_usage': {e}") from e

        plan_limit = 8
        if "plan_limit" in data and data["plan_limit"] is not None:
            try:
                plan_limit = int(data["plan_limit"])
                if plan_limit <= 0:
                    raise ValueError("plan_limit <= 0")
            except Exception as e:
                raise RuntimeError(f"TWELVE_DATA_API_USAGE_MALFORMED: Malformed 'plan_limit': {e}") from e

        raw_plan_category = data.get("plan_category", "basic")
        if not raw_plan_category or not isinstance(raw_plan_category, str):
            raise RuntimeError("TWELVE_DATA_API_USAGE_MALFORMED: 'plan_category' is empty or invalid.")
        plan_category = raw_plan_category.strip()
        if not plan_category:
            raise RuntimeError("TWELVE_DATA_API_USAGE_MALFORMED: 'plan_category' is blank.")

        return {
            "daily_usage": daily_usage,
            "plan_daily_limit": plan_daily_limit,
            "current_usage": current_usage,
            "plan_limit": plan_limit,
            "plan_category": plan_category,
        }
