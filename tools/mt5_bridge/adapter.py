"""MetaTrader 5 Python client adapter for read-only market data retrieval.

Enforces strict segregation: NO trading functions, NO execution APIs, NO account balance exposure.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Tuple

from .config import Mt5BridgeConfig
from .schemas import (
    BarItem,
    BarsResponse,
    HealthResponse,
    HistoryCapabilityResponse,
    HistoryTimeframeCapability,
    ProviderResponse,
    QuoteResponse,
    SymbolResponse,
    TickItem,
    TicksResponse,
)
from .timeframe import (
    TIMEFRAME_DELTAS,
    get_timeframe_delta,
    map_timeframe_to_mt5,
    normalize_timeframe_str,
)

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    HAS_MT5_PACKAGE = True
except ImportError:
    mt5 = None
    HAS_MT5_PACKAGE = False


class Mt5ReadOnlyAdapter:
    """
    Official MetaTrader 5 Python read-only data bridge adapter.
    Exposes only market telemetry, quote, bar, and tick data.
    Trading, execution, order placement, and position modification are strictly excluded.
    """

    def __init__(self, config: Optional[Mt5BridgeConfig] = None):
        self.config = config or Mt5BridgeConfig.from_env()
        self._connected = False
        self._is_connected_override: Optional[bool] = None
        self._discovered_symbol: Optional[str] = None
        self._terminal_build: Optional[int] = None
        self._terminal_name: Optional[str] = None
        self._server_name: str = "NOT_CONNECTED"
        self._is_demo: bool = True
        self._broker_company: str = self.config.broker_name

    @property
    def is_connected(self) -> bool:
        """Check if terminal connection is currently active."""
        if self._is_connected_override is not None:
            return self._is_connected_override
        if not HAS_MT5_PACKAGE or not self._connected:
            return False
        try:
            term = mt5.terminal_info()
            return term is not None and getattr(term, "connected", False)
        except Exception:
            return False

    def initialize(self) -> Tuple[bool, str]:
        """Initialize connection to local MetaTrader 5 terminal without credentials."""
        if not HAS_MT5_PACKAGE:
            msg = "MetaTrader5 Python package is not installed."
            logger.warning(msg)
            return False, msg

        try:
            init_kwargs = {}
            if self.config.terminal_path:
                init_kwargs["path"] = self.config.terminal_path

            success = mt5.initialize(**init_kwargs)
            if not success:
                err = mt5.last_error()
                msg = f"mt5.initialize() failed: error {err}"
                logger.warning(msg)
                self._connected = False
                return False, msg

            self._connected = True
            term = mt5.terminal_info()
            if term:
                self._terminal_build = getattr(term, "build", None)
                self._terminal_name = getattr(term, "name", "MetaTrader 5")

            # Extract safe non-sensitive account metadata
            acc = mt5.account_info()
            if acc:
                self._server_name = getattr(acc, "server", "UNKNOWN_SERVER")
                self._broker_company = getattr(acc, "company", self.config.broker_name)
                # Trade mode: 0=DEMO, 1=CONTEST, 2=REAL
                trade_mode = getattr(acc, "trade_mode", 0)
                self._is_demo = (trade_mode != 2)

            logger.info(
                "MT5 terminal successfully initialized",
                extra={"server": self._server_name, "build": self._terminal_build},
            )
            return True, "OK"
        except Exception as e:
            self._connected = False
            msg = f"Unexpected error during mt5.initialize(): {e}"
            logger.exception(msg)
            return False, msg

    def shutdown(self) -> None:
        """Disconnect and release terminal IPC handles."""
        if HAS_MT5_PACKAGE and self._connected:
            try:
                mt5.shutdown()
            except Exception as e:
                logger.warning(f"Error during mt5.shutdown(): {e}")
        self._connected = False

    def get_health(self) -> HealthResponse:
        """Return diagnostic health check of bridge and terminal connection."""
        now = datetime.now(timezone.utc)
        connected = self.is_connected
        status = "OK" if connected else "NOT_CONNECTED"
        error_msg = None if connected else "Terminal connection not active or terminal not found."

        return HealthResponse(
            status=status,
            connected=connected,
            terminal_build=self._terminal_build,
            terminal_name=self._terminal_name,
            timestamp=now,
            error=error_msg,
        )

    def discover_gold_symbol(self) -> str:
        """
        Dynamically discover the authoritative Gold/USD symbol on the connected Exness terminal.
        Does not assume 'XAUUSD'; checks 'XAUUSDm', 'XAUUSDc', etc.
        Guarantees it represents spot gold versus USD and NOT XAUT or futures.
        """
        if self._discovered_symbol:
            return self._discovered_symbol

        if not self.is_connected:
            return None

        symbols = mt5.symbols_get()
        if not symbols:
            return None

        candidates: List[Tuple[str, int]] = []
        for s in symbols:
            name = s.name
            upper_name = name.upper()
            base = getattr(s, "currency_base", "").upper()
            profit = getattr(s, "currency_profit", "").upper()
            path = getattr(s, "path", "")

            # Exclude crypto-pegged gold, tokenized gold, and futures
            if "XAUT" in upper_name or "TETHER" in path.upper():
                continue
            if "FUTURE" in path.upper() or "ETF" in path.upper():
                continue

            # Check if symbol represents Gold vs USD
            is_gold_base = (base == "XAU" or "XAU" in upper_name or "GOLD" in upper_name)
            is_usd_quote = (profit == "USD" or upper_name.endswith("USD") or "USD" in upper_name)

            if is_gold_base and is_usd_quote:
                # Priority: exact XAUUSD -> 100, standard suffix (m, c, etc.) -> 80, others -> 50
                if upper_name == "XAUUSD":
                    prio = 100
                elif upper_name.startswith("XAUUSD"):
                    prio = 80
                else:
                    prio = 50
                candidates.append((name, prio))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            chosen = candidates[0][0]
            self._discovered_symbol = chosen
            logger.info(f"Discovered Exness gold symbol: '{chosen}' (canonical: XAUUSD)")
            return chosen

        # Return None when not discovered (never fabricate canonical as broker symbol)
        return None

    def get_provider_metadata(self) -> ProviderResponse:
        """Return safe attribution metadata excluding all account/personal information."""
        symbol = self.discover_gold_symbol()
        return ProviderResponse(
            provider=self.config.provider_id,
            broker=self._broker_company,
            environment="DEMO" if self._is_demo else "REAL",
            server=self._server_name,
            canonical_instrument=self.config.canonical_instrument,
            provider_symbol=symbol,
            read_only=True,
        )

    def get_symbol_info(self, symbol: Optional[str] = None) -> SymbolResponse:
        """Retrieve instrument contract specifications from the terminal."""
        if not self.is_connected:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Terminal is not connected.")

        target = symbol or self.discover_gold_symbol()
        if not target:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Gold symbol is not discovered.")
        info = mt5.symbol_info(target)
        if not info:
            raise ValueError(f"Symbol '{target}' not found on terminal.")

        # Ensure symbol is selected in Market Watch for telemetry
        if not info.visible:
            mt5.symbol_select(target, True)
            info = mt5.symbol_info(target)

        # Map trade mode
        trade_modes = {0: "DISABLED", 1: "LONGONLY", 2: "SHORTONLY", 3: "CLOSEONLY", 4: "FULL"}
        mode_str = trade_modes.get(getattr(info, "trade_mode", 4), "UNKNOWN")

        return SymbolResponse(
            canonical_symbol=self.config.canonical_instrument,
            broker_symbol=info.name,
            description=getattr(info, "description", "Gold vs US Dollar"),
            path=getattr(info, "path", "Forex/Metals"),
            currency_base=getattr(info, "currency_base", "XAU"),
            currency_profit=getattr(info, "currency_profit", "USD"),
            digits=getattr(info, "digits", 2),
            point=getattr(info, "point", 0.01),
            spread=getattr(info, "spread", 0),
            trade_mode=mode_str,
            volume_min=float(getattr(info, "volume_min", 0.01)),
            volume_max=float(getattr(info, "volume_max", 100.0)),
            volume_step=float(getattr(info, "volume_step", 0.01)),
        )

    def get_live_quote(self, symbol: Optional[str] = None) -> QuoteResponse:
        """Fetch instantaneous bid/ask ticker snapshot and calculate spread metrics."""
        if not self.is_connected:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Terminal is not connected.")

        target = symbol or self.discover_gold_symbol()
        if not target:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Gold symbol is not discovered.")
        tick = mt5.symbol_info_tick(target)
        if not tick:
            raise RuntimeError(f"Failed to retrieve live tick for symbol '{target}'.")

        bid = Decimal(str(tick.bid))
        ask = Decimal(str(tick.ask))
        last = Decimal(str(tick.last)) if getattr(tick, "last", 0) > 0 else None
        vol = Decimal(str(tick.volume)) if getattr(tick, "volume", 0) > 0 else None

        if bid <= Decimal("0") or ask <= Decimal("0"):
            raise ValueError(f"Invalid non-positive quote prices received: bid={bid}, ask={ask}")
        if ask < bid:
            raise ValueError(f"Crossed market quote violation: ask={ask} < bid={bid}")

        mid = (bid + ask) / Decimal("2")
        spread_abs = ask - bid
        spread_bps = (spread_abs / mid) * Decimal("10000")

        # Timestamp conversion to UTC
        raw_ts = getattr(tick, "time_msc", None)
        if raw_ts:
            ts_utc = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
        else:
            ts_utc = datetime.fromtimestamp(tick.time, tz=timezone.utc)

        return QuoteResponse(
            canonical_symbol=self.config.canonical_instrument,
            broker_symbol=target,
            timestamp=ts_utc,
            bid=bid,
            ask=ask,
            mid=mid,
            spread_absolute=spread_abs,
            spread_bps=spread_bps,
            last=last,
            volume=vol,
        )

    def get_bars(
        self,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
        symbol: Optional[str] = None,
    ) -> BarsResponse:
        """Fetch historical closed bars within range or limit."""
        if not self.is_connected:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Terminal is not connected.")

        target = symbol or self.discover_gold_symbol()
        if not target:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Gold symbol is not discovered.")
        norm_tf = normalize_timeframe_str(timeframe)
        mt5_tf = map_timeframe_to_mt5(norm_tf)
        tf_delta = get_timeframe_delta(norm_tf)

        if start is not None and end is not None:
            # Enforce UTC awareness
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            rates = mt5.copy_rates_range(target, mt5_tf, start, end)
        else:
            rates = mt5.copy_rates_from_pos(target, mt5_tf, 0, limit)

        if rates is None or len(rates) == 0:
            return BarsResponse(
                canonical_symbol=self.config.canonical_instrument,
                broker_symbol=target,
                timeframe=norm_tf,
                count=0,
                bars=[],
            )

        bar_items: List[BarItem] = []
        for r in rates:
            # r has fields: time, open, high, low, close, tick_volume, spread, real_volume
            t_open = datetime.fromtimestamp(r["time"], tz=timezone.utc)
            t_close = t_open + tf_delta

            o = Decimal(str(r["open"]))
            h = Decimal(str(r["high"]))
            l = Decimal(str(r["low"]))
            c = Decimal(str(r["close"]))
            tick_vol = int(r["tick_volume"])
            real_vol = int(r.get("real_volume", 0)) if "real_volume" in r.dtype.names else 0
            spread = int(r.get("spread", 0)) if "spread" in r.dtype.names else 0

            # Determine volume classification
            vol_ev = "TICK_VOLUME" if tick_vol > 0 else "UNAVAILABLE"

            bar_items.append(
                BarItem(
                    timestamp_open=t_open,
                    timestamp_close=t_close,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    tick_volume=tick_vol,
                    real_volume=real_vol,
                    spread=spread,
                    is_closed=True,
                    volume_evidence=vol_ev,
                )
            )

        return BarsResponse(
            canonical_symbol=self.config.canonical_instrument,
            broker_symbol=target,
            timeframe=norm_tf,
            count=len(bar_items),
            bars=bar_items,
        )

    def get_ticks(
        self,
        start: datetime,
        end: datetime,
        limit: int = 1000,
        symbol: Optional[str] = None,
    ) -> TicksResponse:
        """Fetch historical bid/ask ticks for spread and execution study."""
        if not self.is_connected:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Terminal is not connected.")

        target = symbol or self.discover_gold_symbol()
        if not target:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Gold symbol is not discovered.")
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # mt5.COPY_TICKS_INFO = 1 (bid and ask price changes)
        ticks = mt5.copy_ticks_range(target, start, end, mt5.COPY_TICKS_INFO)
        if ticks is None or len(ticks) == 0:
            return TicksResponse(
                canonical_symbol=self.config.canonical_instrument,
                broker_symbol=target,
                count=0,
                ticks=[],
            )

        tick_items: List[TickItem] = []
        for t in ticks[:limit]:
            # t has: time, bid, ask, last, volume, time_msc, flags, volume_real
            msc = getattr(t, "time_msc", None)
            if msc:
                t_dt = datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc)
            else:
                t_dt = datetime.fromtimestamp(t["time"], tz=timezone.utc)

            bid = Decimal(str(t["bid"]))
            ask = Decimal(str(t["ask"]))
            last_val = getattr(t, "last", 0.0)
            vol_val = getattr(t, "volume", 0.0)
            flags = int(getattr(t, "flags", 0))

            if bid > 0 and ask > 0 and ask >= bid:
                mid = (bid + ask) / Decimal("2")
                spread_abs = ask - bid
                spread_bps = (spread_abs / mid) * Decimal("10000")
            else:
                mid = bid
                spread_abs = Decimal("0")
                spread_bps = Decimal("0")

            tick_items.append(
                TickItem(
                    timestamp=t_dt,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    spread_absolute=spread_abs,
                    spread_bps=spread_bps,
                    last=Decimal(str(last_val)) if last_val > 0 else None,
                    volume=Decimal(str(vol_val)) if vol_val > 0 else None,
                    flags=flags,
                )
            )

        return TicksResponse(
            canonical_symbol=self.config.canonical_instrument,
            broker_symbol=target,
            count=len(tick_items),
            ticks=tick_items,
        )

    def get_history_capability(self, symbol: Optional[str] = None) -> HistoryCapabilityResponse:
        """Probe the server history depth for all standard analytical timeframes."""
        if not self.is_connected:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Terminal is not connected.")

        target = symbol or self.discover_gold_symbol()
        if not target:
            raise RuntimeError("EXNESS_MT5_LOCAL_TERMINAL_NOT_READY: Gold symbol is not discovered.")
        capabilities: Dict[str, HistoryTimeframeCapability] = {}

        for tf_str in TIMEFRAME_DELTAS.keys():
            try:
                mt5_tf = map_timeframe_to_mt5(tf_str)
                rates = mt5.copy_rates_from_pos(target, mt5_tf, 0, 10)
                if rates is not None and len(rates) > 0:
                    earliest_dt = datetime.fromtimestamp(rates[0]["time"], tz=timezone.utc)
                    latest_dt = datetime.fromtimestamp(rates[-1]["time"], tz=timezone.utc)
                    capabilities[tf_str] = HistoryTimeframeCapability(
                        accessible=True,
                        earliest=earliest_dt,
                        latest=latest_dt,
                        sample_count=len(rates),
                    )
                else:
                    capabilities[tf_str] = HistoryTimeframeCapability(
                        accessible=False, earliest=None, latest=None, sample_count=0
                    )
            except Exception:
                capabilities[tf_str] = HistoryTimeframeCapability(
                    accessible=False, earliest=None, latest=None, sample_count=0
                )

        return HistoryCapabilityResponse(
            canonical_symbol=self.config.canonical_instrument,
            broker_symbol=target,
            timeframes=capabilities,
        )
