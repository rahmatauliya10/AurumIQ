"""Deterministic market regime classification engine."""
from typing import Optional
from engine.core.types import FeatureSnapshot, RegimeResult, RegimeType, RegimeThresholdProfile
from engine.core.config import EngineConfigData


class RegimeEngine:
    """
    Classifies market regime into deterministic, explainable states:
      - BULL_TREND: Full bullish stack (EMA20 > EMA50 > EMA200), positive slope, ADX >= adx_trend_threshold, RSI >= rsi_bull_threshold
      - BEAR_TREND: Full bearish stack (EMA20 < EMA50 < EMA200), negative slope, ADX >= adx_trend_threshold, RSI < rsi_bear_threshold
      - HIGH_VOLATILITY: High realized volatility, abnormal ATR %, or extreme BB expansion
      - RANGE: Low directional ADX (< adx_trend_threshold) with flat/neutral EMA slope (< slope_boundary)
      - TRANSITION: Structural crossover or conflicting momentum/trend signals
      - UNKNOWN: Insufficient lookback (< 200 bars) or uncalibrated instrument profile (CALIBRATION_REQUIRED)
    """

    def __init__(
        self,
        config: Optional[EngineConfigData] = None,
        profile: Optional[RegimeThresholdProfile] = None,
    ):
        self.config = config or EngineConfigData()
        self.profile = profile or RegimeThresholdProfile.legacy_xaut_profile()

    @classmethod
    def for_xauusd(
        cls,
        profile: Optional[RegimeThresholdProfile] = None,
        config: Optional[EngineConfigData] = None,
    ) -> "RegimeEngine":
        """Factory creating RegimeEngine configured for XAUUSD (uncalibrated fail-closed by default)."""
        return cls(
            config=config,
            profile=profile or RegimeThresholdProfile.uncalibrated_xauusd_profile(),
        )

    @classmethod
    def for_legacy_xaut(
        cls,
        profile: Optional[RegimeThresholdProfile] = None,
        config: Optional[EngineConfigData] = None,
    ) -> "RegimeEngine":
        """Factory creating RegimeEngine for historical XAUT baseline."""
        return cls(
            config=config,
            profile=profile or RegimeThresholdProfile.legacy_xaut_profile(),
        )

    def classify(self, features: FeatureSnapshot, instrument: Optional[str] = None) -> RegimeResult:
        """
        Classify the regime deterministically based on feature snapshot.
        If instrument is XAUUSD and no empirical calibration profile is configured,
        returns fail-neutral UNKNOWN with CALIBRATION_REQUIRED.
        """
        # 1. Check calibration status
        norm_inst = (instrument or "").upper().replace("/", "").replace("_", "")
        if norm_inst in ["XAUUSD", "GOLD"] and self.profile.name == "LEGACY_XAUT_REFERENCE":
            return RegimeResult(
                regime=RegimeType.UNKNOWN,
                confidence=0.0,
                timestamp=features.timestamp,
                details={
                    "reason": "CALIBRATION_REQUIRED",
                    "profile": "XAUUSD_UNCALIBRATED",
                    "message": "XAUUSD empirical regime thresholds not configured.",
                },
            )

        if not self.profile.is_calibrated:
            return RegimeResult(
                regime=RegimeType.UNKNOWN,
                confidence=0.0,
                timestamp=features.timestamp,
                details={
                    "reason": "CALIBRATION_REQUIRED",
                    "profile": self.profile.name,
                    "message": f"Regime threshold profile '{self.profile.name}' is uncalibrated.",
                },
            )

        # 2. Check for sufficient lookback
        if features.ema200 is None or features.adx is None or features.rsi14 is None:
            return RegimeResult(
                regime=RegimeType.UNKNOWN,
                confidence=0.0,
                timestamp=features.timestamp,
                details={"reason": "Insufficient historical lookback bars for 200-period indicators."},
            )

        # 3. High Volatility Check
        is_high_vol = False
        vol_reasons = []
        if features.realized_vol_20 is not None and features.realized_vol_20 > self.profile.high_vol_realized_pct:
            is_high_vol = True
            vol_reasons.append(f"Realized Volatility ({features.realized_vol_20:.2f}%) > {self.profile.high_vol_realized_pct:.1f}%")
        if features.atr_pct is not None and features.atr_pct > self.profile.high_vol_atr_pct:
            is_high_vol = True
            vol_reasons.append(f"ATR % ({features.atr_pct:.2f}%) > {self.profile.high_vol_atr_pct:.1f}%")
        if features.bb_bandwidth is not None and features.bb_bandwidth > self.profile.high_vol_bb_bandwidth_pct:
            is_high_vol = True
            vol_reasons.append(f"BB Bandwidth ({features.bb_bandwidth:.2f}%) > {self.profile.high_vol_bb_bandwidth_pct:.1f}%")

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

        # 4. Bull Trend
        if (
            alignment == 1
            and slope > 0.0
            and adx >= self.profile.adx_trend_threshold
            and rsi >= self.profile.rsi_bull_threshold
        ):
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

        # 5. Bear Trend
        if (
            alignment == -1
            and slope < 0.0
            and adx >= self.profile.adx_trend_threshold
            and rsi < self.profile.rsi_bear_threshold
        ):
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

        # 6. Range
        if adx < self.profile.adx_trend_threshold and abs(slope) < self.profile.slope_boundary:
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

        # 7. Transition (Conflicted signals / Crossovers)
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

