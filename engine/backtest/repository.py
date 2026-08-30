"""Point-in-time in-memory dataset repository strictly preventing lookahead bias."""
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from engine.core.types import (
    CandleData,
    MacroEventContext,
    QuoteData,
)


def _to_utc(dt: datetime) -> datetime:
    """Normalize datetime to UTC."""
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PointInTimeDataset:
    """
    Pure Python repository storing historical candles, quotes, reference feeds, and macro events.

    Strict Invariants (P6-C1):
      1. get_closed_candles(tf, as_of) returns ONLY closed candles with timestamp_close <= as_of.
      2. Appending/mutating data > as_of NEVER affects queries for as_of.
      3. Outcome simulation may query subsequent data in chronological slices [start_ts, end_ts].
      4. Zero external database/ORM dependencies.
    """

    def __init__(
        self,
        candles_15m: Optional[Sequence[CandleData]] = None,
        candles_1h: Optional[Sequence[CandleData]] = None,
        candles_4h: Optional[Sequence[CandleData]] = None,
        candles_1d: Optional[Sequence[CandleData]] = None,
        candles_5m: Optional[Sequence[CandleData]] = None,
        candles_1m: Optional[Sequence[CandleData]] = None,
        quotes: Optional[Sequence[QuoteData]] = None,
        xau_references: Optional[Sequence[Tuple[datetime, Decimal, bool]]] = None,
        usdt_rates: Optional[Sequence[Tuple[datetime, Decimal]]] = None,
        macro_events: Optional[Sequence[Tuple[datetime, MacroEventContext]]] = None,
    ):
        self._candles: Dict[str, List[CandleData]] = {
            "15m": sorted(list(candles_15m or []), key=lambda c: _to_utc(c.timestamp_open)),
            "1h": sorted(list(candles_1h or []), key=lambda c: _to_utc(c.timestamp_open)),
            "4h": sorted(list(candles_4h or []), key=lambda c: _to_utc(c.timestamp_open)),
            "1d": sorted(list(candles_1d or []), key=lambda c: _to_utc(c.timestamp_open)),
            "5m": sorted(list(candles_5m or []), key=lambda c: _to_utc(c.timestamp_open)),
            "1m": sorted(list(candles_1m or []), key=lambda c: _to_utc(c.timestamp_open)),
        }
        self._quotes: List[QuoteData] = sorted(list(quotes or []), key=lambda q: _to_utc(q.timestamp))
        self._xau_references: List[Tuple[datetime, Decimal, bool]] = sorted(
            [(_to_utc(t), p, b) for t, p, b in (xau_references or [])],
            key=lambda x: x[0]
        )
        self._usdt_rates: List[Tuple[datetime, Decimal]] = sorted(
            [(_to_utc(t), r) for t, r in (usdt_rates or [])],
            key=lambda x: x[0]
        )
        self._macro_events: List[Tuple[datetime, MacroEventContext]] = sorted(
            [(_to_utc(t), ctx) for t, ctx in (macro_events or [])],
            key=lambda x: x[0]
        )

    def add_candle(self, timeframe: str, candle: CandleData) -> None:
        """Add a candle to the internal store in chronological order."""
        tf = timeframe.lower()
        if tf not in self._candles:
            self._candles[tf] = []
        self._candles[tf].append(candle)
        self._candles[tf].sort(key=lambda c: _to_utc(c.timestamp_open))

    def get_closed_candles(self, timeframe: str, as_of: datetime) -> List[CandleData]:
        """
        Retrieve closed candles strictly on or before as_of (P6-01, P6-03).
        Invariants:
          - c.timestamp_close <= as_of
          - c.is_closed == True
        """
        tf = timeframe.lower()
        as_of_utc = _to_utc(as_of)
        raw_list = self._candles.get(tf, [])
        return [
            c for c in raw_list
            if c.is_closed and _to_utc(c.timestamp_close) <= as_of_utc
        ]

    def get_intrabar_candles(
        self,
        timeframe: str,
        start_ts: datetime,
        end_ts: datetime,
        as_of: Optional[datetime] = None,
    ) -> List[CandleData]:
        """
        Retrieve execution/intrabar lower-TF candles strictly within [start_ts, end_ts].
        """
        tf = timeframe.lower()
        s_utc = _to_utc(start_ts)
        e_utc = _to_utc(end_ts)
        as_of_utc = _to_utc(as_of) if as_of else None
        raw_list = self._candles.get(tf, [])

        return [
            c for c in raw_list
            if _to_utc(c.timestamp_open) >= s_utc
            and _to_utc(c.timestamp_close) <= e_utc
            and (as_of_utc is None or _to_utc(c.timestamp_close) <= as_of_utc)
        ]

    def get_quotes(
        self,
        start_ts: datetime,
        end_ts: datetime,
        as_of: Optional[datetime] = None,
    ) -> List[QuoteData]:
        """Retrieve timestamped quotes within [start_ts, end_ts]."""
        s_utc = _to_utc(start_ts)
        e_utc = _to_utc(end_ts)
        as_of_utc = _to_utc(as_of) if as_of else None

        return [
            q for q in self._quotes
            if _to_utc(q.timestamp) >= s_utc
            and _to_utc(q.timestamp) <= e_utc
            and (as_of_utc is None or _to_utc(q.timestamp) <= as_of_utc)
        ]

    def get_xau_reference(self, as_of: datetime) -> Tuple[Optional[Decimal], Optional[bool], Optional[datetime]]:
        """Retrieve the latest point-in-time XAU reference price <= as_of."""
        as_of_utc = _to_utc(as_of)
        eligible = [x for x in self._xau_references if x[0] <= as_of_utc]
        if not eligible:
            return None, None, None
        latest = eligible[-1]
        return latest[1], latest[2], latest[0]

    def get_usdt_rate(self, as_of: datetime) -> Tuple[Optional[Decimal], Optional[datetime]]:
        """Retrieve the latest point-in-time USDT normalization rate <= as_of."""
        as_of_utc = _to_utc(as_of)
        eligible = [x for x in self._usdt_rates if x[0] <= as_of_utc]
        if not eligible:
            return None, None
        latest = eligible[-1]
        return latest[1], latest[0]

    def get_macro_context(self, as_of: datetime) -> Optional[MacroEventContext]:
        """Retrieve the latest point-in-time MacroEventContext <= as_of."""
        as_of_utc = _to_utc(as_of)
        eligible = [x for x in self._macro_events if x[0] <= as_of_utc]
        if not eligible:
            return None
        return eligible[-1][1]

    def compute_dataset_hash(self) -> str:
        """Compute SHA-256 digest of dataset content for run provenance."""
        h = hashlib.sha256()
        for tf in ["15m", "1h", "4h", "1d", "5m", "1m"]:
            bars = self._candles.get(tf, [])
            h.update(f"tf:{tf}:count:{len(bars)}".encode("utf-8"))
            if bars:
                h.update(
                    f"first:{bars[0].timestamp_open.isoformat()}:last:{bars[-1].timestamp_close.isoformat()}".encode("utf-8")
                )
        h.update(f"quotes:{len(self._quotes)}:xau:{len(self._xau_references)}:usdt:{len(self._usdt_rates)}".encode("utf-8"))
        return h.hexdigest()
