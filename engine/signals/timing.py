"""Timing Score calculation engine (Phase 4 Config Version 1.0)."""
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from engine.core.types import (
    CandleData,
    ComponentScore,
    Cycle3ASnapshot,
    FeatureSnapshot,
    MacroEventContext,
    StructureResult,
    TimingScoreResult,
)


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
