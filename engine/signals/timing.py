from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from engine.core.types import (
    CandleData,
    ComponentScore,
    Cycle3ASnapshot,
    DualSideTimingResult,
    FeatureSnapshot,
    MacroEventContext,
    SideTimingScoreResult,
    SignalSide,
    StructureResult,
    TimingScoreResult,
)
from engine.cycles.profile import CalibrationStatus as Cycle3ACalibrationStatus, Cycle3AProfile
from engine.signals.profile import normalize_xauusd_target


def calculate_timing_score(
    latest_closed_candle: Optional[CandleData],
    features_15m: Optional[FeatureSnapshot],
    structure_15m: Optional[StructureResult],
    cycle_3a: Optional[Cycle3ASnapshot] = None,
    macro_context: Optional[MacroEventContext] = None,
    prev_features_15m: Optional[FeatureSnapshot] = None,
    config_version: str = "cfg-2026-v1",
) -> TimingScoreResult:
    """
    Calculate deterministic Timing Score (0.0 to 100.0) from closed-candle timing triggers.

    Version 1.0 Weights Allocation (Total = 100):
      - Entry Zone Proximity / ATR: 25 pts
      - Closed 15m Reversal Confirmation: 20 pts
      - 15m / 1H Momentum Turn: 15 pts
      - Phase 3A Robust Timing: 25 pts
      - Volume Response: 10 pts
      - Macro Event Safety: 5 pts

    STRICT INVARIANT: Phase 3B contribution is 0.0 pts.
    """
    components: List[ComponentScore] = []

    # 1. Entry Zone Proximity / ATR (Max 25 pts)
    zone_score = 0.0
    zone_reason = "No active support zones available"
    zone_avail = False

    if structure_15m is not None and structure_15m.zones and latest_closed_candle and features_15m and features_15m.atr14:
        active_support_zones = [z for z in structure_15m.zones if z.zone_type == "SUPPORT" and z.is_active]
        if active_support_zones:
            zone_avail = True
            current_close = float(latest_closed_candle.close)
            atr = float(features_15m.atr14) if features_15m.atr14 > 0 else 1.0

            # Find closest support zone high
            min_dist = float("inf")
            for z in active_support_zones:
                dist = abs(current_close - float(z.price_high))
                if dist < min_dist:
                    min_dist = dist

            atr_distance = min_dist / atr
            if atr_distance <= 0.5:
                zone_score = 25.0
                zone_reason = f"Price at key support boundary ({round(atr_distance, 2)} ATR)"
            elif atr_distance <= 1.0:
                zone_score = 15.0
                zone_reason = f"Price near support zone ({round(atr_distance, 2)} ATR)"
            elif atr_distance <= 1.5:
                zone_score = 8.0
                zone_reason = f"Price within moderate support reach ({round(atr_distance, 2)} ATR)"
            else:
                zone_score = 0.0
                zone_reason = f"Price extended from support zone ({round(atr_distance, 2)} ATR > 1.5)"

    components.append(
        ComponentScore(
            name="Entry Zone Proximity",
            score=round(zone_score, 2),
            max_score=25.0,
            reason=zone_reason,
            is_available=zone_avail,
        )
    )

    # 2. Closed 15m Reversal Confirmation (Max 20 pts)
    reversal_score = 0.0
    reversal_reason = "No closed 15m candle available"
    reversal_avail = latest_closed_candle is not None and latest_closed_candle.is_closed

    if reversal_avail and latest_closed_candle:
        o = float(latest_closed_candle.open)
        h = float(latest_closed_candle.high)
        l = float(latest_closed_candle.low)
        c = float(latest_closed_candle.close)
        candle_range = h - l

        if candle_range > 0:
            body = abs(c - o)
            lower_wick = min(o, c) - l
            upper_wick = h - max(o, c)

            # Bullish Pin Bar / Hammer (lower wick >= 2 * body and close in top 35%)
            is_pin_bar = (lower_wick >= 1.8 * body) and ((c - l) / candle_range >= 0.60)
            is_bullish_close = (c > o) and ((c - l) / candle_range >= 0.65)

            if is_pin_bar:
                reversal_score = 20.0
                reversal_reason = f"Bullish pin-bar rejection closed ({round(lower_wick / (body + 1e-6), 1)}x lower wick)"
            elif is_bullish_close:
                reversal_score = 14.0
                reversal_reason = "Strong bullish candle close off low"
            elif c >= o:
                reversal_score = 6.0
                reversal_reason = "Mild positive candle close"
            else:
                reversal_score = 0.0
                reversal_reason = "Bearish rejection / unfavorable candle shape"
        else:
            reversal_reason = "Flat zero-range candle"

    components.append(
        ComponentScore(
            name="15m Reversal Confirmation",
            score=round(reversal_score, 2),
            max_score=20.0,
            reason=reversal_reason,
            is_available=reversal_avail,
        )
    )

    # 3. 15m / 1H Momentum Turn (Max 15 pts)
    turn_score = 0.0
    turn_reasons = []
    turn_avail = features_15m is not None

    if features_15m is not None:
        # MACD histogram upward tick
        if features_15m.macd_hist is not None:
            prev_hist = float(prev_features_15m.macd_hist) if prev_features_15m and prev_features_15m.macd_hist is not None else None
            curr_hist = float(features_15m.macd_hist)
            if curr_hist > 0 and (prev_hist is None or curr_hist >= prev_hist):
                turn_score += 8.0
                turn_reasons.append("MACD histogram accelerating positive (+8.0 pts)")
            elif prev_hist is not None and curr_hist > prev_hist:
                turn_score += 4.0
                turn_reasons.append("MACD histogram turning upward (+4.0 pts)")

        # RSI momentum upward cross
        if features_15m.rsi14 is not None:
            prev_rsi = prev_features_15m.rsi14 if prev_features_15m and prev_features_15m.rsi14 is not None else None
            curr_rsi = features_15m.rsi14
            if curr_rsi >= 45.0 and (prev_rsi is None or curr_rsi >= prev_rsi):
                turn_score += 7.0
                turn_reasons.append(f"RSI ticking upward ({round(curr_rsi, 1)}) (+7.0 pts)")
            elif curr_rsi >= 40.0:
                turn_score += 3.0
                turn_reasons.append(f"RSI stable ({round(curr_rsi, 1)}) (+3.0 pts)")

    components.append(
        ComponentScore(
            name="Momentum Turn",
            score=round(turn_score, 2),
            max_score=15.0,
            reason="; ".join(turn_reasons) if turn_reasons else "Missing momentum turn evidence",
            is_available=turn_avail,
        )
    )

    # 4. Phase 3A Robust Timing (Max 25 pts)
    # Scaled from cycle_score_3a (0..100) -> 25.0 max
    p3a_score = 0.0
    p3a_reason = "No Phase 3A cycle snapshot available"
    p3a_avail = cycle_3a is not None

    if cycle_3a is not None:
        raw_3a = cycle_3a.cycle_score_3a
        p3a_score = round(25.0 * (raw_3a / 100.0), 2)
        p3a_reason = f"Phase 3A Robust Time score {raw_3a}/100 -> {p3a_score}/25.0 pts (Session: {cycle_3a.session.session.value})"

    components.append(
        ComponentScore(
            name="Phase 3A Robust Time Cycle",
            score=p3a_score,
            max_score=25.0,
            reason=p3a_reason,
            is_available=p3a_avail,
        )
    )

    # 5. Volume Response (Max 10 pts)
    vol_resp_score = 0.0
    vol_resp_reason = "No volume response data"
    vol_resp_avail = features_15m is not None and features_15m.volume_ratio_20 is not None

    if vol_resp_avail and features_15m:
        v_rat = features_15m.volume_ratio_20 or 0.0
        if v_rat >= 1.3:
            vol_resp_score = 10.0
            vol_resp_reason = f"Strong volume surge off support ({round(v_rat, 2)}x SMA20)"
        elif v_rat >= 1.0:
            vol_resp_score = 5.0
            vol_resp_reason = f"Moderate volume response ({round(v_rat, 2)}x SMA20)"
        else:
            vol_resp_score = 0.0
            vol_resp_reason = f"Subdued volume on trigger candle ({round(v_rat, 2)}x SMA20)"

    components.append(
        ComponentScore(
            name="Volume Reversal Response",
            score=round(vol_resp_score, 2),
            max_score=10.0,
            reason=vol_resp_reason,
            is_available=vol_resp_avail,
        )
    )

    # 6. Macro Event Safety (Max 5 pts)
    macro_score = 0.0
    macro_reason = "No macro event feed available"
    macro_avail = False

    if macro_context is not None:
        if not macro_context.is_feed_healthy:
            macro_score = 0.0
            macro_reason = "Macro event feed unavailable or unhealthy"
            macro_avail = False
        elif macro_context.is_in_blackout:
            macro_score = 0.0
            macro_reason = f"Active macro blackout for {macro_context.active_event_name or 'event'}"
            macro_avail = True
        elif macro_context.minutes_to_next_event is not None and macro_context.minutes_to_next_event <= 60:
            macro_score = 2.0
            macro_reason = f"High-impact event in {macro_context.minutes_to_next_event} minutes"
            macro_avail = True
        else:
            macro_score = 5.0
            macro_reason = "Clear market window (no high-impact macro blackout)"
            macro_avail = True

    components.append(
        ComponentScore(
            name="Macro Event Safety",
            score=round(macro_score, 2),
            max_score=5.0,
            reason=macro_reason,
            is_available=macro_avail,
        )
    )

    # Total Score Calculation
    total = sum(c.score for c in components)
    total_clamped = float(round(max(0.0, min(100.0, total)), 2))
    is_ready = total_clamped >= 70.0

    return TimingScoreResult(
        total_score=total_clamped,
        max_score=100.0,
        components=tuple(components),
        is_timing_ready=is_ready,
        config_version=config_version,
    )


# --- Phase 4 XAUUSD Dual-Side Timing Scoring Engine ---

def extract_xauusd_phase3a_score(
    cycle_3a: Optional[Cycle3ASnapshot],
    cycle_3a_profile: Optional[Cycle3AProfile],
    decision_timeframe: str = "15m",
) -> float:
    """
    Extract Phase 3A Timing contribution for XAUUSD with strict profile authority evidence.
    Returns 0.0 unless explicit XAUUSD PRODUCTION_FROZEN profile authority is proven.
    """
    if cycle_3a is None or cycle_3a_profile is None:
        return 0.0
    try:
        if normalize_xauusd_target(cycle_3a_profile.target_instrument) != "XAUUSD":
            return 0.0
    except ValueError:
        return 0.0
    if cycle_3a_profile.calibration_status != Cycle3ACalibrationStatus.PRODUCTION_FROZEN:
        return 0.0
    if cycle_3a.profile_name != cycle_3a_profile.name:
        return 0.0
    if cycle_3a.calibration_status != cycle_3a_profile.calibration_status.value:
        return 0.0
    return float(round(cycle_3a.cycle_score_3a, 2))


def calculate_xauusd_dual_timing(
    candle_15m: Optional[CandleData],
    features_15m: Optional[FeatureSnapshot],
    structure_15m: Optional[StructureResult],
    features_1h: Optional[FeatureSnapshot] = None,
    cycle_3a: Optional[Cycle3ASnapshot] = None,
    cycle_3a_profile: Optional[Cycle3AProfile] = None,
    profile: Optional[Any] = None,
) -> DualSideTimingResult:
    """
    Calculate independent Long and Short Timing Scores (0.0 to 100.0) from closed-candle timing triggers.
    Strict Invariants:
      1. Macro Safety is strictly excluded from scoring (handled only in Hard Safety Gate).
      2. Phase 3A contribution requires proven Cycle3AProfile XAUUSD PRODUCTION_FROZEN authority.
      3. If profile is uncalibrated or None, evaluates descriptive components with is_valid=False and total_score=None.
    """
    is_calibrated = (
        profile is not None
        and hasattr(profile, "is_fully_configured")
        and profile.is_fully_configured
    )
    cfg_version = getattr(profile, "name", "XAUUSD_UNCALIBRATED") if profile else "XAUUSD_UNCALIBRATED"

    long_policy = getattr(profile, "long_timing", None) if profile else None
    short_policy = getattr(profile, "short_timing", None) if profile else None

    def _evaluate_side(
        side: SignalSide,
        policy: Any,
    ) -> SideTimingScoreResult:
        components: List[ComponentScore] = []

        w_zone = getattr(policy, "weight_entry_zone", None) if is_calibrated and policy else 25.0
        w_rev = getattr(policy, "weight_reversal_confirmation_15m", None) if is_calibrated and policy else 25.0
        w_mom = getattr(policy, "weight_momentum_turn_15m_1h", None) if is_calibrated and policy else 20.0
        w_p3a = getattr(policy, "weight_phase3a", None) if is_calibrated and policy else 20.0
        w_vol = getattr(policy, "weight_volume_response", None) if is_calibrated and policy else 10.0

        # 1. Entry Zone Proximity
        zone_score = 0.0
        zone_reason = f"No active {side.value} entry zone proximity available"
        zone_avail = False

        if features_15m is not None and features_15m.atr14 and candle_15m is not None:
            atr = float(features_15m.atr14)
            c_close = float(candle_15m.close)
            if side == SignalSide.LONG:
                ref_p = float(features_15m.ema20) if features_15m.ema20 else c_close
                dist_atr = abs(c_close - ref_p) / atr if atr > 0 else 999.0
                if dist_atr <= 0.5:
                    zone_score = w_zone
                    zone_reason = f"Ideal buy zone proximity ({round(dist_atr, 2)} ATR from EMA20)"
                    zone_avail = True
                elif dist_atr <= 1.0:
                    zone_score = w_zone * 0.60
                    zone_reason = f"Acceptable buy zone proximity ({round(dist_atr, 2)} ATR from EMA20)"
                    zone_avail = True
                else:
                    zone_score = w_zone * 0.20
                    zone_reason = f"Stretched from buy zone ({round(dist_atr, 2)} ATR)"
                    zone_avail = True
            else:  # SHORT
                ref_p = float(features_15m.ema20) if features_15m.ema20 else c_close
                dist_atr = abs(c_close - ref_p) / atr if atr > 0 else 999.0
                if dist_atr <= 0.5:
                    zone_score = w_zone
                    zone_reason = f"Ideal sell zone proximity ({round(dist_atr, 2)} ATR from EMA20)"
                    zone_avail = True
                elif dist_atr <= 1.0:
                    zone_score = w_zone * 0.60
                    zone_reason = f"Acceptable sell zone proximity ({round(dist_atr, 2)} ATR from EMA20)"
                    zone_avail = True
                else:
                    zone_score = w_zone * 0.20
                    zone_reason = f"Stretched from sell zone ({round(dist_atr, 2)} ATR)"
                    zone_avail = True

        components.append(
            ComponentScore(
                name="Entry Zone Proximity",
                score=round(zone_score, 2) if is_calibrated else 0.0,
                max_score=w_zone if is_calibrated else 0.0,
                reason=zone_reason,
                is_available=zone_avail,
            )
        )

        # 2. Closed 15m Reversal Confirmation
        rev_score = 0.0
        rev_reason = "No candle data for reversal check"
        rev_avail = candle_15m is not None

        if candle_15m is not None:
            o_p = float(candle_15m.open)
            h_p = float(candle_15m.high)
            l_p = float(candle_15m.low)
            c_p = float(candle_15m.close)
            candle_range = h_p - l_p

            if candle_range > 0:
                if side == SignalSide.LONG:
                    lower_wick = min(o_p, c_p) - l_p
                    lower_wick_pct = lower_wick / candle_range
                    close_in_upper = (c_p - l_p) / candle_range
                    if lower_wick_pct >= 0.35 and close_in_upper >= 0.60:
                        rev_score = w_rev
                        rev_reason = f"Strong bullish rejection pin ({round(lower_wick_pct * 100, 1)}% wick)"
                    elif c_p > o_p:
                        rev_score = w_rev * 0.60
                        rev_reason = "Bullish candle close"
                    else:
                        rev_score = 0.0
                        rev_reason = "Bearish candle close on trigger"
                else:  # SHORT
                    upper_wick = h_p - max(o_p, c_p)
                    upper_wick_pct = upper_wick / candle_range
                    close_in_lower = (h_p - c_p) / candle_range
                    if upper_wick_pct >= 0.35 and close_in_lower >= 0.60:
                        rev_score = w_rev
                        rev_reason = f"Strong bearish rejection pin ({round(upper_wick_pct * 100, 1)}% wick)"
                    elif c_p < o_p:
                        rev_score = w_rev * 0.60
                        rev_reason = "Bearish candle close"
                    else:
                        rev_score = 0.0
                        rev_reason = "Bullish candle close on trigger"

        components.append(
            ComponentScore(
                name="15m Reversal Confirmation",
                score=round(rev_score, 2) if is_calibrated else 0.0,
                max_score=w_rev if is_calibrated else 0.0,
                reason=rev_reason,
                is_available=rev_avail,
            )
        )

        # 3. 15m + 1H Momentum Turn
        mom_score = 0.0
        mom_reasons = []
        mom_avail = features_15m is not None

        if features_15m is not None:
            if features_15m.rsi14 is not None:
                rsi15 = features_15m.rsi14
                if side == SignalSide.LONG:
                    if 45.0 <= rsi15 <= 65.0:
                        mom_score += w_mom * 0.50
                        mom_reasons.append(f"15m RSI bull zone ({round(rsi15, 1)})")
                    elif rsi15 < 45.0 and features_15m.roc12 and features_15m.roc12 > 0:
                        mom_score += w_mom * 0.30
                        mom_reasons.append(f"15m RSI recovering ({round(rsi15, 1)})")
                else:  # SHORT
                    if 35.0 <= rsi15 <= 55.0:
                        mom_score += w_mom * 0.50
                        mom_reasons.append(f"15m RSI bear zone ({round(rsi15, 1)})")
                    elif rsi15 > 55.0 and features_15m.roc12 and features_15m.roc12 < 0:
                        mom_score += w_mom * 0.30
                        mom_reasons.append(f"15m RSI turning down ({round(rsi15, 1)})")

            if features_1h is not None and features_1h.rsi14 is not None:
                rsi1h = features_1h.rsi14
                if side == SignalSide.LONG and rsi1h >= 45.0:
                    mom_score += w_mom * 0.50
                    mom_reasons.append(f"1H RSI supportive ({round(rsi1h, 1)})")
                elif side == SignalSide.SHORT and rsi1h <= 55.0:
                    mom_score += w_mom * 0.50
                    mom_reasons.append(f"1H RSI supportive ({round(rsi1h, 1)})")

        components.append(
            ComponentScore(
                name="15m + 1H Momentum Turn",
                score=round(mom_score, 2) if is_calibrated else 0.0,
                max_score=w_mom if is_calibrated else 0.0,
                reason="; ".join(mom_reasons) if mom_reasons else "Missing momentum turn evidence",
                is_available=mom_avail,
            )
        )

        # 4. Phase 3A Cycle Timing
        p3a_pts = extract_xauusd_phase3a_score(cycle_3a, cycle_3a_profile, "15m")
        p3a_score = round(p3a_pts * (w_p3a / 100.0), 2)
        p3a_reason = f"Phase 3A contribution ({round(p3a_score, 2)}/{w_p3a} pts)" if p3a_pts > 0 else "Phase 3A uncalibrated or unavailable"
        p3a_avail = p3a_pts > 0

        components.append(
            ComponentScore(
                name="Phase 3A Cycle Timing",
                score=p3a_score if is_calibrated else 0.0,
                max_score=w_p3a if is_calibrated else 0.0,
                reason=p3a_reason,
                is_available=p3a_avail,
            )
        )

        # 5. Volume Response
        vol_score = 0.0
        vol_reason = "No volume response data available"
        vol_avail = features_15m is not None and features_15m.volume_ratio_20 is not None

        if vol_avail and features_15m:
            v_ratio = features_15m.volume_ratio_20 or 0.0
            if v_ratio >= 1.2:
                vol_score = w_vol
                vol_reason = f"Strong trigger volume response ({round(v_ratio, 2)}x)"
            elif v_ratio >= 0.9:
                vol_score = w_vol * 0.60
                vol_reason = f"Normal trigger volume response ({round(v_ratio, 2)}x)"
            else:
                vol_score = w_vol * 0.20
                vol_reason = f"Weak trigger volume response ({round(v_ratio, 2)}x)"

        components.append(
            ComponentScore(
                name="Volume Response",
                score=round(vol_score, 2) if is_calibrated else 0.0,
                max_score=w_vol if is_calibrated else 0.0,
                reason=vol_reason,
                is_available=vol_avail,
            )
        )

        if not is_calibrated:
            return SideTimingScoreResult(
                side=side,
                total_score=None,
                max_score=0.0,
                components=tuple(components),
                is_valid=False,
                is_timing_ready=False,
                config_version=cfg_version,
            )

        total = sum(c.score for c in components)
        total_clamped = float(round(max(0.0, min(100.0, total)), 2))
        th_ready = getattr(profile.long_gate if side == SignalSide.LONG else profile.short_gate, "threshold_ready_timing", 70.0)
        is_ready = total_clamped >= (th_ready or 70.0)

        return SideTimingScoreResult(
            side=side,
            total_score=total_clamped,
            max_score=100.0,
            components=tuple(components),
            is_valid=True,
            is_timing_ready=is_ready,
            config_version=cfg_version,
        )

    long_res = _evaluate_side(SignalSide.LONG, long_policy)
    short_res = _evaluate_side(SignalSide.SHORT, short_policy)

    return DualSideTimingResult(
        long_timing=long_res,
        short_timing=short_res,
        is_calibrated=is_calibrated,
    )

