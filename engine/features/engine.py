"""Feature Engine coordinating multi-domain technical indicator extraction."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence, Optional
from engine.core.types import CandleData, FeatureSnapshot, VolumeEvidenceType
from engine.core.config import EngineConfigData
from .trend import calculate_ema, calculate_ema_slope, calculate_ema_alignment, calculate_adx
from .momentum import calculate_rsi, calculate_macd, calculate_roc
from .volatility import calculate_atr, calculate_bollinger_bands, calculate_realized_volatility
from .volume import calculate_volume_features


class FeatureEngine:
    """
    Pure Python feature extraction engine computing trend, momentum, volatility,
    and volume indicators from causal candlestick data.
    """

    def __init__(self, config: Optional[EngineConfigData] = None):
        self.config = config or EngineConfigData()

    def extract_features(self, candles: Sequence[CandleData]) -> FeatureSnapshot:
        """
        Extract complete technical feature snapshot from causal sequence of closed candles up to T.
        Enforces closed-candle safety: forming/open candles are strictly excluded from
        indicator calculation, target timestamp assignment, and volume feature extraction.
        """
        closed_candles = [c for c in candles if c.is_closed] if candles else []

        if not closed_candles:
            return FeatureSnapshot(
                timestamp=datetime.now(timezone.utc),
                ema20=None, ema50=None, ema200=None, ema_slope_20=None, ema_alignment=0,
                adx=None, plus_di=None, minus_di=None,
                rsi14=None, macd_line=None, macd_signal=None, macd_hist=None, roc12=None,
                atr14=None, atr_pct=None,
                bb_upper=None, bb_middle=None, bb_lower=None, bb_bandwidth=None,
                realized_vol_20=None,
                volume_ratio_20=None, volume_zscore_20=None,
                volume_evidence=VolumeEvidenceType.UNAVAILABLE,
                volume_usable=False,
                volume_reason="EMPTY_CANDLES",
            )

        latest_candle = closed_candles[-1]
        target_timestamp = latest_candle.timestamp_open

        # Extract series (using normalized USD close if present, else raw close)
        closes = [c.close_usd if c.close_usd is not None else c.close for c in closed_candles]
        highs = [c.high for c in closed_candles]
        lows = [c.low for c in closed_candles]


        # 1. Trend Features
        ema20_series = calculate_ema(closes, self.config.ema_fast_period)
        ema50_series = calculate_ema(closes, self.config.ema_mid_period)
        ema200_series = calculate_ema(closes, self.config.ema_slow_period)

        ema20 = ema20_series[-1] if ema20_series else None
        ema50 = ema50_series[-1] if ema50_series else None
        ema200 = ema200_series[-1] if ema200_series else None

        ema_slope_20 = calculate_ema_slope(ema20_series, lookback=5)
        ema_alignment = calculate_ema_alignment(ema20, ema50, ema200)

        adx, plus_di, minus_di = calculate_adx(highs, lows, closes, self.config.adx_period)

        # 2. Momentum Features
        rsi14 = calculate_rsi(closes, self.config.rsi_period)
        macd_line, macd_signal, macd_hist = calculate_macd(
            closes, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal
        )
        roc12 = calculate_roc(closes, self.config.roc_period)

        # 3. Volatility Features
        atr14 = calculate_atr(highs, lows, closes, self.config.atr_period)
        current_close = closes[-1]
        atr_pct = float((atr14 / current_close) * Decimal("100.0")) if (atr14 and current_close > 0) else None

        bb_upper, bb_middle, bb_lower, bb_bandwidth = calculate_bollinger_bands(
            closes, self.config.bollinger_period, self.config.bollinger_num_std
        )
        realized_vol_20 = calculate_realized_volatility(closes, self.config.realized_vol_period)

        # 4. Volume Features (with XAU-P2-01 semantic validation)
        vol_res = calculate_volume_features(closed_candles, self.config.volume_lookback)
        volume_ratio_20 = vol_res.ratio
        volume_zscore_20 = vol_res.zscore
        volume_evidence = vol_res.evidence_type
        volume_usable = vol_res.is_usable
        volume_reason = vol_res.reason

        return FeatureSnapshot(
            timestamp=target_timestamp,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            ema_slope_20=ema_slope_20,
            ema_alignment=ema_alignment,
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            rsi14=rsi14,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_hist=macd_hist,
            roc12=roc12,
            atr14=atr14,
            atr_pct=atr_pct,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            bb_bandwidth=bb_bandwidth,
            realized_vol_20=realized_vol_20,
            volume_ratio_20=volume_ratio_20,
            volume_zscore_20=volume_zscore_20,
            volume_evidence=volume_evidence,
            volume_usable=volume_usable,
            volume_reason=volume_reason,
        )

