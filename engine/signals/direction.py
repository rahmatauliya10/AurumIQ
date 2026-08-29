"""Direction Score calculation engine (Phase 4 Config Version 1.0)."""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from engine.core.types import (
    BosType,
    ComponentScore,
    DirectionScoreResult,
    FeatureSnapshot,
    RegimeResult,
    RegimeType,
    StructureResult,
    StructureType,
)


def calculate_direction_score(
    regime: Optional[RegimeResult],
    features_15m: Optional[FeatureSnapshot],
    structure_15m: Optional[StructureResult],
    features_4h: Optional[FeatureSnapshot] = None,
    features_1d: Optional[FeatureSnapshot] = None,
    xau_reference_is_bullish: Optional[bool] = None,
    xaut_basis_zscore: Optional[float] = None,
    config_version: str = "cfg-2026-v1",
) -> DirectionScoreResult:
    """
    Calculate deterministic Direction Score (0.0 to 100.0) from closed-candle multi-timeframe evidence.

    Version 1.0 Weights Allocation (Total = 100):
      - Market Regime Quality: 15 pts
      - 1D + 4H Trend Alignment: 20 pts
      - Confirmed Structure / BOS: 20 pts
      - Pullback Quality: 10 pts
      - Momentum State: 10 pts
      - Volume Confirmation: 5 pts
      - XAU Alignment & XAUT Basis Quality: 20 pts

    Invariant: Missing or non-available evidence strictly awards 0.0 points.
    """
    components: List[ComponentScore] = []

    # 1. Market Regime Quality (Max 15 pts)
    regime_score = 0.0
    regime_reason = "No regime data available"
    regime_avail = regime is not None

    if regime is not None:
        conf = float(regime.confidence)
        if regime.regime == RegimeType.BULL_TREND:
            regime_score = round(15.0 * conf, 2)
            regime_reason = f"Bull trend regime confirmed with {round(conf * 100, 1)}% confidence"
        elif regime.regime == RegimeType.RANGE:
            regime_score = round(5.0 * conf, 2)
            regime_reason = f"Range market regime detected ({round(conf * 100, 1)}% confidence)"
        else:
            regime_score = 0.0
            regime_reason = f"Hostile or transition regime ({regime.regime.value})"

    components.append(
        ComponentScore(
            name="Market Regime Quality",
            score=regime_score,
            max_score=15.0,
            reason=regime_reason,
            is_available=regime_avail,
            details={"regime": regime.regime.value if regime else None},
        )
    )

    # 2. 1D + 4H Trend Alignment (Max 20 pts)
    trend_score = 0.0
    trend_reasons = []
    trend_avail = False

    # 4H Component (Max 10 pts)
    if features_4h is not None:
        trend_avail = True
        score_4h = 0.0
        if features_4h.ema_alignment == 1:
            score_4h += 6.0
        if features_4h.ema_slope_20 is not None and features_4h.ema_slope_20 > 0.0:
            score_4h += 2.0
        if features_4h.adx is not None and features_4h.adx > 20.0 and (features_4h.plus_di or 0.0) > (features_4h.minus_di or 0.0):
            score_4h += 2.0
        trend_score += score_4h
        trend_reasons.append(f"4H trend contributes {score_4h}/10.0 pts")
    elif features_15m is not None and features_15m.ema_alignment == 1:
        # Fallback to short-term trend alignment if 4H not available
        trend_avail = True
        trend_score += 5.0
        trend_reasons.append("15m trend bullish (+5.0 pts)")

    # 1D Component (Max 10 pts)
    if features_1d is not None:
        trend_avail = True
        score_1d = 0.0
        if features_1d.ema_alignment == 1:
            score_1d += 6.0
        if features_1d.ema_slope_20 is not None and features_1d.ema_slope_20 > 0.0:
            score_1d += 2.0
        if features_1d.adx is not None and features_1d.adx > 20.0:
            score_1d += 2.0
        trend_score += score_1d
        trend_reasons.append(f"1D macro trend contributes {score_1d}/10.0 pts")

    components.append(
        ComponentScore(
            name="Trend Alignment",
            score=round(trend_score, 2),
            max_score=20.0,
            reason="; ".join(trend_reasons) if trend_reasons else "Missing multi-timeframe trend feeds",
            is_available=trend_avail,
        )
    )

    # 3. Confirmed Market Structure / BOS (Max 20 pts)
    struct_score = 0.0
    struct_reason = "No structure data available"
    struct_avail = structure_15m is not None

    if structure_15m is not None:
        st = structure_15m.structure_type
        bos = structure_15m.bos

        if st in (StructureType.HH, StructureType.HL):
            struct_score += 10.0
            struct_reason = f"Bullish structure confirmed ({st.value})"
        elif st == StructureType.CONSOLIDATION:
            struct_score += 4.0
            struct_reason = "Structure in consolidation"
        else:
            struct_reason = f"Bearish or lower-low structure ({st.value})"

        if bos == BosType.BULLISH:
            struct_score += 10.0
            struct_reason += " + Bullish BOS break confirmed"
        elif bos == BosType.BEARISH:
            struct_score = max(0.0, struct_score - 5.0)
            struct_reason += " - Bearish BOS breakdown"

    components.append(
        ComponentScore(
            name="Market Structure & BOS",
            score=round(struct_score, 2),
            max_score=20.0,
            reason=struct_reason,
            is_available=struct_avail,
        )
    )

    # 4. Pullback Quality (Max 10 pts)
    pullback_score = 0.0
    pullback_reason = "No active swing pullback calculation available"
    pullback_avail = False

    if structure_15m is not None and structure_15m.last_swing_high and structure_15m.last_swing_low:
        high_p = float(structure_15m.last_swing_high.price)
        low_p = float(structure_15m.last_swing_low.price)
        range_p = high_p - low_p
        if range_p > 0 and features_15m and features_15m.ema20:
            pullback_avail = True
            curr_p = float(features_15m.ema20)
            retracement = (high_p - curr_p) / range_p
            if 0.382 <= retracement <= 0.618:
                pullback_score = 10.0
                pullback_reason = f"Golden ratio pullback depth ({round(retracement * 100, 1)}%)"
            elif 0.236 <= retracement < 0.382 or 0.618 < retracement <= 0.786:
                pullback_score = 5.0
                pullback_reason = f"Moderate pullback depth ({round(retracement * 100, 1)}%)"
            else:
                pullback_score = 0.0
                pullback_reason = f"Overextended or shallow retracement ({round(retracement * 100, 1)}%)"

    components.append(
        ComponentScore(
            name="Pullback Quality",
            score=round(pullback_score, 2),
            max_score=10.0,
            reason=pullback_reason,
            is_available=pullback_avail,
        )
    )

    # 5. Momentum State (Max 10 pts)
    mom_score = 0.0
    mom_reasons = []
    mom_avail = features_15m is not None

    if features_15m is not None:
        if features_15m.rsi14 is not None:
            if 45.0 <= features_15m.rsi14 <= 65.0:
                mom_score += 5.0
                mom_reasons.append(f"RSI14 in healthy zone ({round(features_15m.rsi14, 1)})")
            elif 35.0 <= features_15m.rsi14 < 45.0:
                mom_score += 2.5
                mom_reasons.append(f"RSI14 recovering ({round(features_15m.rsi14, 1)})")
            elif features_15m.rsi14 > 70.0:
                mom_reasons.append(f"RSI14 overbought ({round(features_15m.rsi14, 1)})")
            else:
                mom_reasons.append(f"RSI14 depressed ({round(features_15m.rsi14, 1)})")

        if features_15m.macd_hist is not None and features_15m.macd_line is not None and features_15m.macd_signal is not None:
            if features_15m.macd_hist > 0 and features_15m.macd_line >= features_15m.macd_signal:
                mom_score += 5.0
                mom_reasons.append("MACD bullish histogram expansion")
            elif features_15m.macd_hist > 0:
                mom_score += 2.5
                mom_reasons.append("MACD histogram positive")

    components.append(
        ComponentScore(
            name="Momentum State",
            score=round(mom_score, 2),
            max_score=10.0,
            reason="; ".join(mom_reasons) if mom_reasons else "Missing momentum indicators",
            is_available=mom_avail,
        )
    )

    # 6. Volume Confirmation (Max 5 pts)
    vol_score = 0.0
    vol_reason = "No volume data available"
    vol_avail = features_15m is not None and features_15m.volume_ratio_20 is not None

    if vol_avail:
        v_ratio = features_15m.volume_ratio_20 or 0.0
        v_z = features_15m.volume_zscore_20 or 0.0
        if v_ratio >= 1.2:
            vol_score = 5.0
            vol_reason = f"Volume expansion on advance (ratio {round(v_ratio, 2)}x)"
        elif v_ratio >= 0.9:
            vol_score = 3.0
            vol_reason = f"Steady volume support (ratio {round(v_ratio, 2)}x)"
        else:
            vol_score = 1.0
            vol_reason = f"Low volume participation (ratio {round(v_ratio, 2)}x)"

    components.append(
        ComponentScore(
            name="Volume Confirmation",
            score=round(vol_score, 2),
            max_score=5.0,
            reason=vol_reason,
            is_available=vol_avail,
        )
    )

    # 7. XAU Alignment & XAUT Basis Quality (Max 20 pts)
    xau_score = 0.0
    xau_reasons = []
    xau_avail = xau_reference_is_bullish is not None or xaut_basis_zscore is not None

    # Canonical XAU/USD concordance (Max 10 pts)
    if xau_reference_is_bullish is True:
        xau_score += 10.0
        xau_reasons.append("Canonical XAU/USD trend aligned bullish (+10.0 pts)")
    elif xau_reference_is_bullish is False:
        xau_reasons.append("Canonical XAU/USD trend divergent/bearish (0.0 pts)")
    else:
        xau_reasons.append("Missing canonical XAU reference feed")

    # USDT-normalized XAUT Basis Quality (Max 10 pts)
    if xaut_basis_zscore is not None:
        abs_z = abs(xaut_basis_zscore)
        if abs_z <= 1.5:
            xau_score += 10.0
            xau_reasons.append(f"XAUT basis z-score stable ({round(xaut_basis_zscore, 2)}z)")
        elif abs_z <= 2.5:
            xau_score += 5.0
            xau_reasons.append(f"XAUT basis z-score moderate ({round(xaut_basis_zscore, 2)}z)")
        else:
            xau_reasons.append(f"XAUT premium basis elevated ({round(xaut_basis_zscore, 2)}z > 2.5)")
    else:
        xau_reasons.append("Missing USDT/USD normalization basis feed")

    components.append(
        ComponentScore(
            name="XAU Alignment & Basis Quality",
            score=round(xau_score, 2),
            max_score=20.0,
            reason="; ".join(xau_reasons),
            is_available=xau_avail,
        )
    )

    # Total Score Calculation
    total = sum(c.score for c in components)
    total_clamped = float(round(max(0.0, min(100.0, total)), 2))
    is_bullish = total_clamped >= 70.0

    return DirectionScoreResult(
        total_score=total_clamped,
        max_score=100.0,
        components=tuple(components),
        is_bullish=is_bullish,
        config_version=config_version,
    )
