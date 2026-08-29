"""Causal Market Structure Engine coordinating swings, BOS, and zones."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from engine.core.types import (
    CandleData,
    StructureResult,
    StructureType,
    BosType,
    SwingPoint,
    SwingType,
)
from engine.core.config import EngineConfigData
from .causal_swings import detect_causal_swings
from .zones import cluster_structure_zones


class CausalStructureEngine:
    """
    Causal Market Structure Engine enforcing zero lookahead bias:
      - Swing points confirmed strictly after right_bars confirmation
      - Deterministic HH / HL / LH / LL classification
      - Break of Structure (BOS) signals
      - ATR-normalized Support & Resistance zones
    """

    def __init__(self, config: Optional[EngineConfigData] = None):
        self.config = config or EngineConfigData()

    def analyze(
        self,
        candles: Sequence[CandleData],
        atr: Optional[Decimal] = None,
    ) -> StructureResult:
        """
        Analyze causal market structure from historical candles strictly up to T.
        """
        if not candles:
            return StructureResult(
                timestamp=datetime.now(timezone.utc),
                structure_type=StructureType.UNKNOWN,
                bos=BosType.NONE,
                last_swing_high=None,
                last_swing_low=None,
                swings=(),
                zones=(),
            )

        latest_candle = candles[-1]
        target_timestamp = latest_candle.timestamp_close if latest_candle.is_closed else latest_candle.timestamp_open
        current_close = latest_candle.close_usd if latest_candle.close_usd is not None else latest_candle.close

        # 1. Detect Causal Swings
        swings = detect_causal_swings(
            candles,
            left_bars=self.config.swing_left_bars,
            right_bars=self.config.swing_right_bars,
        )

        swing_highs = [s for s in swings if s.swing_type == SwingType.HIGH]
        swing_lows = [s for s in swings if s.swing_type == SwingType.LOW]

        last_high = swing_highs[-1] if swing_highs else None
        last_low = swing_lows[-1] if swing_lows else None

        # 2. Structure Type Hierarchy (HH/HL/LH/LL)
        structure_type = StructureType.UNKNOWN
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            prev_high, curr_high = swing_highs[-2], swing_highs[-1]
            prev_low, curr_low = swing_lows[-2], swing_lows[-1]

            is_hh = curr_high.price > prev_high.price
            is_hl = curr_low.price > prev_low.price
            is_lh = curr_high.price < prev_high.price
            is_ll = curr_low.price < prev_low.price

            if is_hh and is_hl:
                structure_type = StructureType.HH  # Bullish trending structure
            elif is_lh and is_ll:
                structure_type = StructureType.LL  # Bearish trending structure
            elif is_hl:
                structure_type = StructureType.HL
            elif is_lh:
                structure_type = StructureType.LH
            else:
                structure_type = StructureType.CONSOLIDATION

        # 3. Break of Structure (BOS)
        # Causality Invariant: Close-based BOS requires the candle to be fully closed.
        bos = BosType.NONE
        if latest_candle.is_closed:
            if last_high and current_close > last_high.price:
                bos = BosType.BULLISH
            elif last_low and current_close < last_low.price:
                bos = BosType.BEARISH

        # 4. Support & Resistance Zones
        zones = cluster_structure_zones(
            swings,
            atr=atr,
            zone_atr_factor=self.config.zone_atr_factor,
            max_zones=self.config.max_active_zones,
        )

        return StructureResult(
            timestamp=target_timestamp,
            structure_type=structure_type,
            bos=bos,
            last_swing_high=last_high,
            last_swing_low=last_low,
            swings=tuple(swings),
            zones=zones,
        )
