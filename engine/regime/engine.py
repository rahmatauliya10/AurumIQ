"""Deterministic market regime classification engine."""
from typing import Optional
from engine.core.types import FeatureSnapshot, RegimeResult, RegimeType
from engine.core.config import EngineConfigData


class RegimeEngine:
    """
    Classifies market regime into deterministic, explainable states:
      - BULL_TREND: Full bullish stack (EMA20 > EMA50 > EMA200), positive slope, ADX >= 20, RSI >= 50
      - BEAR_TREND: Full bearish stack (EMA20 < EMA50 < EMA200), negative slope, ADX >= 20, RSI < 50
      - HIGH_VOLATILITY: High realized volatility, abnormal ATR %, or extreme BB expansion
      - RANGE: Low directional ADX (< 20) with flat/neutral EMA slope
      - TRANSITION: Structural crossover or conflicting momentum/trend signals
      - UNKNOWN: Insufficient lookback (< 200 bars)
    """

    def __init__(self, config: Optional[EngineConfigData] = None):
        self.config = config or EngineConfigData()

    def classify(self, features: FeatureSnapshot) -> RegimeResult:
        """
        Classify the regime deterministically based on feature snapshot.
        """
        # 1. Check for sufficient lookback
        if features.ema200 is None or features.adx is None or features.rsi14 is None:
            return RegimeResult(
                regime=RegimeType.UNKNOWN,
                confidence=0.0,
                timestamp=features.timestamp,
                details={"reason": "Insufficient historical lookback bars for 200-period indicators."},
            )

        # 2. High Volatility Check
        is_high_vol = False
        vol_reasons = []
        if features.realized_vol_20 is not None and features.realized_vol_20 > 5.0:
            is_high_vol = True
            vol_reasons.append(f"Realized Volatility ({features.realized_vol_20:.2f}%) > 5.0%")
        if features.atr_pct is not None and features.atr_pct > 3.0:
            is_high_vol = True
            vol_reasons.append(f"ATR % ({features.atr_pct:.2f}%) > 3.0%")
        if features.bb_bandwidth is not None and features.bb_bandwidth > 15.0:
            is_high_vol = True
            vol_reasons.append(f"BB Bandwidth ({features.bb_bandwidth:.2f}%) > 15.0%")

        if is_high_vol:
            return RegimeResult(
                regime=RegimeType.HIGH_VOLATILITY,
                confidence=0.85,
                timestamp=features.timestamp,
                details={"reasons": vol_reasons},
            )

        adx = features.adx
        rsi = features.rsi14
        slope = features.ema_slope_20 or 0.0
        alignment = features.ema_alignment

        # 3. Bull Trend
        if alignment == 1 and slope > 0.0 and adx >= self.config.adx_trend_threshold and rsi >= 50.0:
            confidence = min(1.0, 0.60 + (adx / 100.0))
            return RegimeResult(
                regime=RegimeType.BULL_TREND,
                confidence=round(confidence, 2),
                timestamp=features.timestamp,
                details={
                    "alignment": alignment,
                    "ema_slope": slope,
                    "adx": adx,
                    "rsi": rsi,
                },
            )

        # 4. Bear Trend
        if alignment == -1 and slope < 0.0 and adx >= self.config.adx_trend_threshold and rsi < 50.0:
            confidence = min(1.0, 0.60 + (adx / 100.0))
            return RegimeResult(
                regime=RegimeType.BEAR_TREND,
                confidence=round(confidence, 2),
                timestamp=features.timestamp,
                details={
                    "alignment": alignment,
                    "ema_slope": slope,
                    "adx": adx,
                    "rsi": rsi,
                },
            )

        # 5. Range
        if adx < self.config.adx_trend_threshold and abs(slope) < 0.05:
            confidence = 0.80
            return RegimeResult(
                regime=RegimeType.RANGE,
                confidence=confidence,
                timestamp=features.timestamp,
                details={
                    "adx": adx,
                    "ema_slope": slope,
                    "reason": "ADX below trend threshold with neutral EMA slope.",
                },
            )

        # 6. Transition (Conflicted signals / Crossovers)
        return RegimeResult(
            regime=RegimeType.TRANSITION,
            confidence=0.65,
            timestamp=features.timestamp,
            details={
                "alignment": alignment,
                "ema_slope": slope,
                "adx": adx,
                "rsi": rsi,
                "reason": "Mixed momentum/trend alignment indicating market transition.",
            },
        )
