"""Direction Score calculation engine (Phase 4 Config Version 1.0)."""
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from engine.core.types import (
    BosType,
    ComponentScore,
    DirectionScoreResult,
    DualSideDirectionResult,
    FeatureSnapshot,
    RegimeResult,
    RegimeType,
    SideDirectionScoreResult,
    SignalSide,
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


# --- Phase 4 XAUUSD Dual-Side Direction Scoring Engine ---

def calculate_xauusd_dual_direction(
    regime: Optional[RegimeResult],
    features_15m: Optional[FeatureSnapshot],
    structure_15m: Optional[StructureResult],
    features_1h: Optional[FeatureSnapshot] = None,
    features_4h: Optional[FeatureSnapshot] = None,
    features_1d: Optional[FeatureSnapshot] = None,
    profile: Optional[Any] = None,
) -> DualSideDirectionResult:
    """
    Calculate independent Long and Short Direction Scores (0.0 to 100.0) from closed-candle evidence.
    Strict Invariants:
      1. Short is NOT (100 - Long). Each side evaluates its own evidence.
      2. No timeframe substitution: missing 1H, 4H, or 1D receives 0.0 pts (no fallback to 15m).
      3. Zero legacy XAU reference alignment or USDT basis quality scoring.
      4. If profile is uncalibrated or None, evaluates descriptive components with is_valid=False and total_score=None.
    """
    from engine.core.types import (
        DualSideDirectionResult,
        SideDirectionScoreResult,
        SignalSide,
    )

    is_calibrated = (
        profile is not None
        and hasattr(profile, "is_fully_configured")
        and profile.is_fully_configured
    )
    cfg_version = getattr(profile, "name", "XAUUSD_UNCALIBRATED") if profile else "XAUUSD_UNCALIBRATED"

    long_policy = getattr(profile, "long_direction", None) if profile else None
    short_policy = getattr(profile, "short_direction", None) if profile else None

    # Helper to calculate one side
    def _evaluate_side(
        side: SignalSide,
        policy: Any,
    ) -> SideDirectionScoreResult:
        components: List[ComponentScore] = []

        w_regime = getattr(policy, "weight_regime", None) if is_calibrated and policy else 15.0
        w_1h = getattr(policy, "weight_trend_1h", None) if is_calibrated and policy else 10.0
        w_4h = getattr(policy, "weight_trend_4h", None) if is_calibrated and policy else 10.0
        w_1d = getattr(policy, "weight_trend_1d", None) if is_calibrated and policy else 10.0
        w_struct = getattr(policy, "weight_structure_bos", None) if is_calibrated and policy else 20.0
        w_pullback = getattr(policy, "weight_pullback", None) if is_calibrated and policy else 15.0
        w_mom = getattr(policy, "weight_momentum", None) if is_calibrated and policy else 10.0
        w_vol = getattr(policy, "weight_volume", None) if is_calibrated and policy else 10.0

        # 1. Market Regime Quality
        regime_score = 0.0
        regime_reason = "No regime data available"
        regime_avail = regime is not None

        if regime is not None:
            conf = float(regime.confidence)
            if side == SignalSide.LONG:
                if regime.regime == RegimeType.BULL_TREND:
                    regime_score = round(w_regime * conf, 2)
                    regime_reason = f"Bull trend confirmed with {round(conf * 100, 1)}% confidence"
                elif regime.regime == RegimeType.RANGE:
                    regime_score = round((w_regime / 3.0) * conf, 2)
                    regime_reason = f"Range market detected ({round(conf * 100, 1)}% confidence)"
                else:
                    regime_score = 0.0
                    regime_reason = f"Adverse or transition regime for long ({regime.regime.value})"
            else:  # SHORT
                if regime.regime == RegimeType.BEAR_TREND:
                    regime_score = round(w_regime * conf, 2)
                    regime_reason = f"Bear trend confirmed with {round(conf * 100, 1)}% confidence"
                elif regime.regime == RegimeType.RANGE:
                    regime_score = round((w_regime / 3.0) * conf, 2)
                    regime_reason = f"Range market detected ({round(conf * 100, 1)}% confidence)"
                else:
                    regime_score = 0.0
                    regime_reason = f"Adverse or transition regime for short ({regime.regime.value})"

        components.append(
            ComponentScore(
                name="Market Regime Quality",
                score=regime_score if is_calibrated else 0.0,
                max_score=w_regime if is_calibrated else 0.0,
                reason=regime_reason,
                is_available=regime_avail,
            )
        )

        # 2. 1H Trend Alignment (Strict: No fallback to 15m)
        score_1h = 0.0
        reason_1h = "1H trend feed unavailable"
        avail_1h = features_1h is not None

        if avail_1h and features_1h:
            target_align = 1 if side == SignalSide.LONG else -1
            if features_1h.ema_alignment == target_align:
                score_1h += w_1h * 0.60
            if features_1h.ema_slope_20 is not None:
                if (side == SignalSide.LONG and features_1h.ema_slope_20 > 0.0) or (side == SignalSide.SHORT and features_1h.ema_slope_20 < 0.0):
                    score_1h += w_1h * 0.20
            if features_1h.adx is not None and features_1h.adx > 20.0:
                p_di = features_1h.plus_di or 0.0
                m_di = features_1h.minus_di or 0.0
                if (side == SignalSide.LONG and p_di > m_di) or (side == SignalSide.SHORT and m_di > p_di):
                    score_1h += w_1h * 0.20
            reason_1h = f"1H trend aligned {side.value} ({round(score_1h, 2)}/{w_1h} pts)"

        components.append(
            ComponentScore(
                name="1H Trend Alignment",
                score=round(score_1h, 2) if is_calibrated else 0.0,
                max_score=w_1h if is_calibrated else 0.0,
                reason=reason_1h,
                is_available=avail_1h,
            )
        )

        # 3. 4H Trend Alignment (Strict: No fallback)
        score_4h = 0.0
        reason_4h = "4H trend feed unavailable"
        avail_4h = features_4h is not None

        if avail_4h and features_4h:
            target_align = 1 if side == SignalSide.LONG else -1
            if features_4h.ema_alignment == target_align:
                score_4h += w_4h * 0.60
            if features_4h.ema_slope_20 is not None:
                if (side == SignalSide.LONG and features_4h.ema_slope_20 > 0.0) or (side == SignalSide.SHORT and features_4h.ema_slope_20 < 0.0):
                    score_4h += w_4h * 0.20
            if features_4h.adx is not None and features_4h.adx > 20.0:
                p_di = features_4h.plus_di or 0.0
                m_di = features_4h.minus_di or 0.0
                if (side == SignalSide.LONG and p_di > m_di) or (side == SignalSide.SHORT and m_di > p_di):
                    score_4h += w_4h * 0.20
            reason_4h = f"4H trend aligned {side.value} ({round(score_4h, 2)}/{w_4h} pts)"

        components.append(
            ComponentScore(
                name="4H Trend Alignment",
                score=round(score_4h, 2) if is_calibrated else 0.0,
                max_score=w_4h if is_calibrated else 0.0,
                reason=reason_4h,
                is_available=avail_4h,
            )
        )

        # 4. 1D Macro Trend Alignment (Strict: No fallback)
        score_1d = 0.0
        reason_1d = "1D macro trend feed unavailable"
        avail_1d = features_1d is not None

        if avail_1d and features_1d:
            target_align = 1 if side == SignalSide.LONG else -1
            if features_1d.ema_alignment == target_align:
                score_1d += w_1d * 0.60
            if features_1d.ema_slope_20 is not None:
                if (side == SignalSide.LONG and features_1d.ema_slope_20 > 0.0) or (side == SignalSide.SHORT and features_1d.ema_slope_20 < 0.0):
                    score_1d += w_1d * 0.20
            if features_1d.adx is not None and features_1d.adx > 20.0:
                p_di = features_1d.plus_di or 0.0
                m_di = features_1d.minus_di or 0.0
                if (side == SignalSide.LONG and p_di > m_di) or (side == SignalSide.SHORT and m_di > p_di):
                    score_1d += w_1d * 0.20
            reason_1d = f"1D macro trend aligned {side.value} ({round(score_1d, 2)}/{w_1d} pts)"

        components.append(
            ComponentScore(
                name="1D Macro Trend",
                score=round(score_1d, 2) if is_calibrated else 0.0,
                max_score=w_1d if is_calibrated else 0.0,
                reason=reason_1d,
                is_available=avail_1d,
            )
        )

        # 5. Market Structure & BOS
        struct_score = 0.0
        struct_reason = "No structure data available"
        struct_avail = structure_15m is not None

        if structure_15m is not None:
            st = structure_15m.structure_type
            bos = structure_15m.bos

            if side == SignalSide.LONG:
                if st in (StructureType.HH, StructureType.HL):
                    struct_score += w_struct * 0.50
                    struct_reason = f"Bullish structure confirmed ({st.value})"
                elif st == StructureType.CONSOLIDATION:
                    struct_score += w_struct * 0.20
                    struct_reason = "Structure in consolidation"
                else:
                    struct_reason = f"Bearish structure for long ({st.value})"

                if bos == BosType.BULLISH:
                    struct_score += w_struct * 0.50
                    struct_reason += " + Bullish BOS break"
                elif bos == BosType.BEARISH:
                    struct_score = max(0.0, struct_score - (w_struct * 0.25))
                    struct_reason += " - Bearish BOS breakdown"
            else:  # SHORT
                if st in (StructureType.LH, StructureType.LL):
                    struct_score += w_struct * 0.50
                    struct_reason = f"Bearish structure confirmed ({st.value})"
                elif st == StructureType.CONSOLIDATION:
                    struct_score += w_struct * 0.20
                    struct_reason = "Structure in consolidation"
                else:
                    struct_reason = f"Bullish structure hostile for short ({st.value})"

                if bos == BosType.BEARISH:
                    struct_score += w_struct * 0.50
                    struct_reason += " + Bearish BOS breakdown"
                elif bos == BosType.BULLISH:
                    struct_score = max(0.0, struct_score - (w_struct * 0.25))
                    struct_reason += " - Bullish BOS break"

        components.append(
            ComponentScore(
                name="Market Structure & BOS",
                score=round(struct_score, 2) if is_calibrated else 0.0,
                max_score=w_struct if is_calibrated else 0.0,
                reason=struct_reason,
                is_available=struct_avail,
            )
        )

        # 6. Pullback Quality
        pb_score = 0.0
        pb_reason = "No active swing pullback calculation available"
        pb_avail = False

        if structure_15m is not None and structure_15m.last_swing_high and structure_15m.last_swing_low and features_15m and features_15m.ema20:
            high_p = float(structure_15m.last_swing_high.price)
            low_p = float(structure_15m.last_swing_low.price)
            range_p = high_p - low_p
            if range_p > 0:
                pb_avail = True
                curr_p = float(features_15m.ema20)
                if side == SignalSide.LONG:
                    retracement = (high_p - curr_p) / range_p
                else:  # SHORT
                    retracement = (curr_p - low_p) / range_p

                if 0.382 <= retracement <= 0.618:
                    pb_score = w_pullback
                    pb_reason = f"Golden ratio pullback depth ({round(retracement * 100, 1)}%)"
                elif 0.236 <= retracement < 0.382 or 0.618 < retracement <= 0.786:
                    pb_score = w_pullback * 0.50
                    pb_reason = f"Moderate pullback depth ({round(retracement * 100, 1)}%)"
                else:
                    pb_score = 0.0
                    pb_reason = f"Overextended or shallow retracement ({round(retracement * 100, 1)}%)"

        components.append(
            ComponentScore(
                name="Pullback Quality",
                score=round(pb_score, 2) if is_calibrated else 0.0,
                max_score=w_pullback if is_calibrated else 0.0,
                reason=pb_reason,
                is_available=pb_avail,
            )
        )

        # 7. Momentum State
        mom_score = 0.0
        mom_reasons = []
        mom_avail = features_15m is not None

        if features_15m is not None:
            if features_15m.rsi14 is not None:
                rsi = features_15m.rsi14
                if side == SignalSide.LONG:
                    if 45.0 <= rsi <= 65.0:
                        mom_score += w_mom * 0.50
                        mom_reasons.append(f"RSI14 healthy bull ({round(rsi, 1)})")
                    elif 35.0 <= rsi < 45.0:
                        mom_score += w_mom * 0.25
                        mom_reasons.append(f"RSI14 recovering ({round(rsi, 1)})")
                else:  # SHORT
                    if 35.0 <= rsi <= 55.0:
                        mom_score += w_mom * 0.50
                        mom_reasons.append(f"RSI14 healthy bear ({round(rsi, 1)})")
                    elif 55.0 < rsi <= 65.0:
                        mom_score += w_mom * 0.25
                        mom_reasons.append(f"RSI14 turning down ({round(rsi, 1)})")

            if features_15m.macd_hist is not None and features_15m.macd_line is not None and features_15m.macd_signal is not None:
                if side == SignalSide.LONG:
                    if features_15m.macd_hist > 0 and features_15m.macd_line >= features_15m.macd_signal:
                        mom_score += w_mom * 0.50
                        mom_reasons.append("MACD bullish expansion")
                    elif features_15m.macd_hist > 0:
                        mom_score += w_mom * 0.25
                        mom_reasons.append("MACD histogram positive")
                else:  # SHORT
                    if features_15m.macd_hist < 0 and features_15m.macd_line <= features_15m.macd_signal:
                        mom_score += w_mom * 0.50
                        mom_reasons.append("MACD bearish expansion")
                    elif features_15m.macd_hist < 0:
                        mom_score += w_mom * 0.25
                        mom_reasons.append("MACD histogram negative")

        components.append(
            ComponentScore(
                name="Momentum State",
                score=round(mom_score, 2) if is_calibrated else 0.0,
                max_score=w_mom if is_calibrated else 0.0,
                reason="; ".join(mom_reasons) if mom_reasons else "Missing momentum indicators",
                is_available=mom_avail,
            )
        )

        # 8. Volume Confirmation
        vol_score = 0.0
        vol_reason = "No volume data available"
        vol_avail = features_15m is not None and features_15m.volume_ratio_20 is not None

        if vol_avail and features_15m:
            v_ratio = features_15m.volume_ratio_20 or 0.0
            if v_ratio >= 1.2:
                vol_score = w_vol
                vol_reason = f"Volume expansion (ratio {round(v_ratio, 2)}x)"
            elif v_ratio >= 0.9:
                vol_score = w_vol * 0.60
                vol_reason = f"Steady volume support (ratio {round(v_ratio, 2)}x)"
            else:
                vol_score = w_vol * 0.20
                vol_reason = f"Low volume participation (ratio {round(v_ratio, 2)}x)"

        components.append(
            ComponentScore(
                name="Volume Confirmation",
                score=round(vol_score, 2) if is_calibrated else 0.0,
                max_score=w_vol if is_calibrated else 0.0,
                reason=vol_reason,
                is_available=vol_avail,
            )
        )

        if not is_calibrated:
            return SideDirectionScoreResult(
                side=side,
                total_score=None,
                max_score=0.0,
                components=tuple(components),
                is_valid=False,
                is_direction_ready=False,
                config_version=cfg_version,
            )

        total = sum(c.score for c in components)
        total_clamped = float(round(max(0.0, min(100.0, total)), 2))
        th_watch = getattr(profile.long_gate if side == SignalSide.LONG else profile.short_gate, "threshold_watch_direction", 70.0)
        is_ready = total_clamped >= (th_watch or 70.0)

        return SideDirectionScoreResult(
            side=side,
            total_score=total_clamped,
            max_score=100.0,
            components=tuple(components),
            is_valid=True,
            is_direction_ready=is_ready,
            config_version=cfg_version,
        )

    long_res = _evaluate_side(SignalSide.LONG, long_policy)
    short_res = _evaluate_side(SignalSide.SHORT, short_policy)

    return DualSideDirectionResult(
        long_direction=long_res,
        short_direction=short_res,
        is_calibrated=is_calibrated,
    )

