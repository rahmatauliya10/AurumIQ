"""
Side-aware intrabar ambiguity resolution engine for XAUUSD (Phase 5).
Implements side-aware barrier checks (LONG: high>=TP / low<=SL; SHORT: low<=TP / high>=SL),
strict parent and lower-TF candle validation, chronological lower-timeframe replay,
and conservative SL_FIRST / WORST_CASE fallback policies.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.core.types import (
    BarrierHitType,
    CandleData,
    IntrabarPolicy,
    RiskSide,
    SideIntrabarResolutionResult,
)
from engine.risk.xauusd_execution import validate_xauusd_candle


def _validate_sequence(
    candles: Sequence[CandleData],
    expected_step_seconds: int,
    parent_candle: CandleData,
    fill_ts: datetime,
) -> Tuple[bool, Optional[str], List[CandleData]]:
    """
    Validate lower-timeframe candle grid before replay.

    Enforces:
      1. Every bar passes strict geometric & numeric validation (validate_xauusd_candle).
      2. Containment within parent candle [open, close].
      3. Original chronological ordering.
      4. Exact duration (e.g. 900s for 15m, 300s for 5m, 60s for 1m).
      5. Closed candles only.
      6. No duplicate timestamps.
      7. No overlapping candles.
      8. Initial coverage at or before fill_ts.
      9. Grid continuity (zero gaps).
    """
    if not candles:
        return False, "Candle sequence is empty.", []

    p_open = parent_candle.timestamp_open.astimezone(timezone.utc)
    p_close = parent_candle.timestamp_close.astimezone(timezone.utc)

    bars: List[CandleData] = []
    for c in candles:
        if not validate_xauusd_candle(c):
            return False, f"Candle failed strict validation at {c.timestamp_open.isoformat()}.", []

        c_open = c.timestamp_open.astimezone(timezone.utc)
        c_close = c.timestamp_close.astimezone(timezone.utc)

        if c_open < p_open or c_close > p_close:
            return False, f"Candle ({c_open.isoformat()} - {c_close.isoformat()}) outside parent boundary ({p_open.isoformat()} - {p_close.isoformat()}).", []
        bars.append(c)

    if not bars:
        return False, "No candles within parent interval.", []

    # 1. Chronological order check
    for i in range(len(bars) - 1):
        if bars[i].timestamp_open.astimezone(timezone.utc) >= bars[i + 1].timestamp_open.astimezone(timezone.utc):
            return False, f"Candle sequence is not in chronological order: {bars[i].timestamp_open.isoformat()} >= {bars[i+1].timestamp_open.isoformat()}.", []

    # 2. Duration and Closed check
    for b in bars:
        duration = int((b.timestamp_close - b.timestamp_open).total_seconds())
        if duration != expected_step_seconds:
            return False, f"Candle duration {duration}s does not match expected {expected_step_seconds}s.", []
        if not b.is_closed:
            return False, f"Unclosed candle detected at {b.timestamp_open.isoformat()}.", []

    # 3. Duplicate and Overlap check
    for i in range(len(bars) - 1):
        b_cur_open = bars[i].timestamp_open.astimezone(timezone.utc)
        b_next_open = bars[i + 1].timestamp_open.astimezone(timezone.utc)
        b_cur_close = bars[i].timestamp_close.astimezone(timezone.utc)

        if b_cur_open == b_next_open or b_cur_close == bars[i + 1].timestamp_close.astimezone(timezone.utc):
            return False, f"Duplicate timestamp detected at {b_cur_open.isoformat()}.", []
        if b_next_open < b_cur_close:
            return False, f"Overlapping candles detected: {b_cur_close.isoformat()} > {b_next_open.isoformat()}.", []

    # 4. Initial coverage check
    first_bar_open = bars[0].timestamp_open.astimezone(timezone.utc)
    if first_bar_open > fill_ts:
        return False, f"Missing initial coverage: first bar starts at {first_bar_open.isoformat()} > fill_ts {fill_ts.isoformat()}.", []

    # 5. Continuity check (no gaps)
    for i in range(len(bars) - 1):
        b_cur_close = bars[i].timestamp_close.astimezone(timezone.utc)
        b_next_open = bars[i + 1].timestamp_open.astimezone(timezone.utc)
        if b_next_open > b_cur_close:
            return False, f"Gap detected between {b_cur_close.isoformat()} and {b_next_open.isoformat()}.", []

    return True, None, bars


class SideAwareIntrabarResolver:
    """
    Side-aware resolver for ambiguous candles where both TP and SL are touched.

    Barrier Rules:
      LONG:  TP hit = parent.high >= TP;  SL hit = parent.low <= SL
      SHORT: TP hit = parent.low <= TP;   SL hit = parent.high >= SL

    Resolution Hierarchy:
      Parent 4H/1H:
        15m pre-validation -> 15m replay
        -> if 15m child is ambiguous:
           1m pre-validation (preferred) -> 5m fallback
        -> if 15m malformed / unresolved:
           CONSERVATIVE_SL_FIRST

      Parent 15m:
        1m pre-validation (preferred) -> 5m fallback
        -> if malformed / unresolved:
           CONSERVATIVE_SL_FIRST
    """

    def resolve(
        self,
        side: RiskSide,
        parent_candle: CandleData,
        tp_price: Decimal,
        sl_price: Decimal,
        fill_timestamp: Optional[datetime] = None,
        lower_tf_candles_1m: Optional[Sequence[CandleData]] = None,
        lower_tf_candles_5m: Optional[Sequence[CandleData]] = None,
        lower_tf_candles_15m: Optional[Sequence[CandleData]] = None,
        parent_timeframe: str = "15m",
        policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        worst_case_adverse_gap: Decimal = Decimal("5.00"),
    ) -> SideIntrabarResolutionResult:
        """
        Evaluate barrier resolution for a trade within parent_candle.
        """
        # 0. Strict parent candle validation
        if not validate_xauusd_candle(parent_candle):
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.UNRESOLVED,
                exit_price=parent_candle.close if isinstance(parent_candle.close, Decimal) and parent_candle.close.is_finite() else Decimal("0"),
                exit_timestamp=parent_candle.timestamp_close if parent_candle.timestamp_close is not None else datetime.now(timezone.utc),
                policy_applied=policy,
                replay_bars_count=0,
                reasons=("Parent candle failed strict validation checks.",),
            )

        # 1. Side-aware barrier touch detection
        if side == RiskSide.LONG:
            touches_tp = parent_candle.high >= tp_price
            touches_sl = parent_candle.low <= sl_price
        else:
            touches_tp = parent_candle.low <= tp_price
            touches_sl = parent_candle.high >= sl_price

        # 2. Non-ambiguous path
        if not (touches_tp and touches_sl):
            if touches_tp:
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.TP_FIRST,
                    exit_price=tp_price,
                    exit_timestamp=parent_candle.timestamp_close,
                    policy_applied=policy,
                    replay_bars_count=0,
                    reasons=(f"Non-ambiguous bar: Only {side.value} TP barrier touched.",),
                )
            if touches_sl:
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.SL_FIRST,
                    exit_price=sl_price,
                    exit_timestamp=parent_candle.timestamp_close,
                    policy_applied=policy,
                    replay_bars_count=0,
                    reasons=(f"Non-ambiguous bar: Only {side.value} SL barrier touched.",),
                )
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.UNRESOLVED,
                exit_price=parent_candle.close,
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=policy,
                replay_bars_count=0,
                reasons=(f"Neither {side.value} TP nor SL barrier touched during parent candle.",),
            )

        # 3. Ambiguous Bar (touches both TP and SL)
        if policy == IntrabarPolicy.CONSERVATIVE_SL_FIRST:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=sl_price,
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                replay_bars_count=0,
                reasons=(f"Conservative policy: {side.value} SL assumed hit first.",),
            )

        if policy == IntrabarPolicy.WORST_CASE:
            if side == RiskSide.LONG:
                exit_price = (sl_price - worst_case_adverse_gap).quantize(Decimal("0.01"))
            else:
                exit_price = (sl_price + worst_case_adverse_gap).quantize(Decimal("0.01"))

            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=exit_price,
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=IntrabarPolicy.WORST_CASE,
                replay_bars_count=0,
                reasons=(f"Worst-case policy: {side.value} SL assumed hit first with adverse penalty gap.",),
            )

        if policy == IntrabarPolicy.SKIP_AMBIGUOUS:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SKIPPED,
                exit_price=Decimal("0.00"),
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=IntrabarPolicy.SKIP_AMBIGUOUS,
                replay_bars_count=0,
                reasons=("Ambiguous trade excluded from sample (SKIP_AMBIGUOUS).",),
            )

        # 4. LOWER_TIMEFRAME_REPLAY Policy
        fill_ts = (
            fill_timestamp.astimezone(timezone.utc)
            if fill_timestamp is not None
            else parent_candle.timestamp_open.astimezone(timezone.utc)
        )

        # For 4H/1H parent: try 15m first with pre-validation
        if parent_timeframe in ("4h", "1h"):
            if lower_tf_candles_15m:
                ok_15m, err_15m, valid_15m = _validate_sequence(
                    lower_tf_candles_15m,
                    expected_step_seconds=900,
                    parent_candle=parent_candle,
                    fill_ts=fill_ts,
                )
                if not ok_15m:
                    return SideIntrabarResolutionResult(
                        side=side,
                        barrier_hit=BarrierHitType.SL_FIRST,
                        exit_price=sl_price,
                        exit_timestamp=parent_candle.timestamp_close,
                        policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                        replay_bars_count=0,
                        reasons=(f"15m parent grid malformed ({err_15m}); fell back to CONSERVATIVE_SL_FIRST.",),
                    )

                for bar in valid_15m:
                    if bar.timestamp_close.astimezone(timezone.utc) <= fill_ts:
                        continue

                    if side == RiskSide.LONG:
                        b_tp = bar.high >= tp_price
                        b_sl = bar.low <= sl_price
                    else:
                        b_tp = bar.low <= tp_price
                        b_sl = bar.high >= sl_price

                    if b_tp and not b_sl:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.TP_FIRST,
                            exit_price=tp_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_15m),
                            reasons=(f"Resolved {side.value} TP-first via 15m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_sl and not b_tp:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_15m),
                            reasons=(f"Resolved {side.value} SL-first via 15m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_tp and b_sl:
                        # Ambiguous 15m bar: slice 1m/5m child bars and recurse
                        b_open = bar.timestamp_open.astimezone(timezone.utc)
                        b_close = bar.timestamp_close.astimezone(timezone.utc)
                        c_1m = (
                            [c for c in lower_tf_candles_1m if c.timestamp_open.astimezone(timezone.utc) >= b_open and c.timestamp_close.astimezone(timezone.utc) <= b_close]
                            if lower_tf_candles_1m else None
                        )
                        c_5m = (
                            [c for c in lower_tf_candles_5m if c.timestamp_open.astimezone(timezone.utc) >= b_open and c.timestamp_close.astimezone(timezone.utc) <= b_close]
                            if lower_tf_candles_5m else None
                        )
                        return self.resolve(
                            side=side,
                            parent_candle=bar,
                            tp_price=tp_price,
                            sl_price=sl_price,
                            fill_timestamp=fill_ts,
                            lower_tf_candles_1m=c_1m,
                            lower_tf_candles_5m=c_5m,
                            parent_timeframe="15m",
                            policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                        )

        # For 15m parent (or drilled 15m bar): 1m preferred -> 5m fallback
        if lower_tf_candles_1m:
            ok_1m, err_1m, valid_1m = _validate_sequence(
                lower_tf_candles_1m,
                expected_step_seconds=60,
                parent_candle=parent_candle,
                fill_ts=fill_ts,
            )
            if ok_1m:
                for bar in valid_1m:
                    if bar.timestamp_close.astimezone(timezone.utc) <= fill_ts:
                        continue

                    if side == RiskSide.LONG:
                        b_tp = bar.high >= tp_price
                        b_sl = bar.low <= sl_price
                    else:
                        b_tp = bar.low <= tp_price
                        b_sl = bar.high >= sl_price

                    if b_tp and not b_sl:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.TP_FIRST,
                            exit_price=tp_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_1m),
                            reasons=(f"Resolved {side.value} TP-first via 1m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_sl and not b_tp:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_1m),
                            reasons=(f"Resolved {side.value} SL-first via 1m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_tp and b_sl:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                            replay_bars_count=len(valid_1m),
                            reasons=(f"1m candle remained ambiguous for {side.value}; failed safe to SL_FIRST.",),
                        )
            else:
                if not lower_tf_candles_5m:
                    return SideIntrabarResolutionResult(
                        side=side,
                        barrier_hit=BarrierHitType.SL_FIRST,
                        exit_price=sl_price,
                        exit_timestamp=parent_candle.timestamp_close,
                        policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                        replay_bars_count=0,
                        reasons=(f"1m candle grid incomplete or malformed ({err_1m}); fell back to CONSERVATIVE_SL_FIRST.",),
                    )

        if lower_tf_candles_5m:
            ok_5m, err_5m, valid_5m = _validate_sequence(
                lower_tf_candles_5m,
                expected_step_seconds=300,
                parent_candle=parent_candle,
                fill_ts=fill_ts,
            )
            if ok_5m:
                for bar in valid_5m:
                    if bar.timestamp_close.astimezone(timezone.utc) <= fill_ts:
                        continue

                    if side == RiskSide.LONG:
                        b_tp = bar.high >= tp_price
                        b_sl = bar.low <= sl_price
                    else:
                        b_tp = bar.low <= tp_price
                        b_sl = bar.high >= sl_price

                    if b_tp and not b_sl:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.TP_FIRST,
                            exit_price=tp_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_5m),
                            reasons=(f"Resolved {side.value} TP-first via 5m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_sl and not b_tp:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_5m),
                            reasons=(f"Resolved {side.value} SL-first via 5m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_tp and b_sl:
                        return SideIntrabarResolutionResult(
                            side=side,
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                            replay_bars_count=len(valid_5m),
                            reasons=(f"5m candle remained ambiguous for {side.value}; failed safe to SL_FIRST.",),
                        )
            else:
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.SL_FIRST,
                    exit_price=sl_price,
                    exit_timestamp=parent_candle.timestamp_close,
                    policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                    replay_bars_count=0,
                    reasons=(f"5m candle grid incomplete or malformed ({err_5m}); fell back to CONSERVATIVE_SL_FIRST.",),
                )

        # Fallback if no lower-TF data available
        return SideIntrabarResolutionResult(
            side=side,
            barrier_hit=BarrierHitType.SL_FIRST,
            exit_price=sl_price,
            exit_timestamp=parent_candle.timestamp_close,
            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
            replay_bars_count=0,
            reasons=(f"Lower-TF data unavailable for {side.value}; fallen back to CONSERVATIVE_SL_FIRST.",),
        )
