"""Intrabar ambiguity resolution engine with chronological lower-timeframe replay (Phase 5)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.core.types import (
    BarrierHitType,
    CandleData,
    IntrabarPolicy,
    IntrabarResolutionResult,
)


def _validate_sequence(
    candles: Sequence[CandleData],
    expected_step_seconds: int,
    parent_candle: CandleData,
    fill_ts: datetime,
) -> Tuple[bool, Optional[str], List[CandleData]]:
    """
    Validate a lower-timeframe candle grid before replay.

    Enforces:
      1. Original chronological ordering (no out-of-order lists).
      2. Containment within parent candle [open, close].
      3. Exact duration (e.g. 900s for 15m, 300s for 5m, 60s for 1m).
      4. Closed candles only.
      5. Duplicate timestamp rejection.
      6. Overlap rejection.
      7. Initial coverage at or before fill_ts.
      8. Grid continuity (zero gaps).
    """
    if not candles:
        return False, "Candle sequence is empty.", []

    # Sliced in original input sequence order within parent boundary
    p_open = parent_candle.timestamp_open
    p_close = parent_candle.timestamp_close

    bars: List[CandleData] = []
    for c in candles:
        # Check containment
        if c.timestamp_open < p_open or c.timestamp_close > p_close:
            return False, f"Candle ({c.timestamp_open.isoformat()} - {c.timestamp_close.isoformat()}) outside parent boundary ({p_open.isoformat()} - {p_close.isoformat()}).", []
        bars.append(c)

    if not bars:
        return False, "No candles within parent interval.", []

    # 1. Original chronological ordering check
    for i in range(len(bars) - 1):
        if bars[i].timestamp_open >= bars[i + 1].timestamp_open:
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
        if bars[i].timestamp_open == bars[i + 1].timestamp_open or bars[i].timestamp_close == bars[i + 1].timestamp_close:
            return False, f"Duplicate timestamp detected at {bars[i].timestamp_open.isoformat()}.", []
        if bars[i + 1].timestamp_open < bars[i].timestamp_close:
            return False, f"Overlapping candles detected: {bars[i].timestamp_close.isoformat()} > {bars[i+1].timestamp_open.isoformat()}.", []

    # 4. Initial coverage check
    first_bar = bars[0]
    if first_bar.timestamp_open > fill_ts:
        return False, f"Missing initial coverage: first bar starts at {first_bar.timestamp_open.isoformat()} > fill_ts {fill_ts.isoformat()}.", []

    # 5. Continuity check (no gaps)
    for i in range(len(bars) - 1):
        if bars[i + 1].timestamp_open > bars[i].timestamp_close:
            return False, f"Gap detected between {bars[i].timestamp_close.isoformat()} and {bars[i+1].timestamp_open.isoformat()}.", []

    return True, None, bars


class IntrabarResolver:
    """
    Resolves ambiguous candles where High >= TP and Low <= SL within the same bar.

    Resolution Hierarchy:
      Parent 4H/1H:
        Pre-validate 15m grid integrity -> 15m replay
        -> if 15m child is ambiguous:
           Pre-validate 1m/5m child slice -> 1m preferred -> 5m fallback
        -> if unresolved / malformed:
           CONSERVATIVE_SL_FIRST

      Parent 15m (A22):
        Pre-validate 1m/5m grid integrity -> 1m preferred -> 5m fallback
        -> if unresolved / malformed:
           CONSERVATIVE_SL_FIRST
    """

    def resolve(
        self,
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
    ) -> IntrabarResolutionResult:
        """
        Evaluate barrier resolution for a trade within parent_candle.
        """
        # 1. Non-ambiguous quick path
        touches_tp = parent_candle.high >= tp_price
        touches_sl = parent_candle.low <= sl_price

        if not (touches_tp and touches_sl):
            if touches_tp:
                return IntrabarResolutionResult(
                    barrier_hit=BarrierHitType.TP_FIRST,
                    exit_price=tp_price,
                    exit_timestamp=parent_candle.timestamp_close,
                    policy_applied=policy,
                    replay_bars_count=0,
                    reasons=("Non-ambiguous bar: Only TP barrier touched.",),
                )
            if touches_sl:
                return IntrabarResolutionResult(
                    barrier_hit=BarrierHitType.SL_FIRST,
                    exit_price=sl_price,
                    exit_timestamp=parent_candle.timestamp_close,
                    policy_applied=policy,
                    replay_bars_count=0,
                    reasons=("Non-ambiguous bar: Only SL barrier touched.",),
                )
            return IntrabarResolutionResult(
                barrier_hit=BarrierHitType.UNRESOLVED,
                exit_price=parent_candle.close,
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=policy,
                replay_bars_count=0,
                reasons=("Neither TP nor SL barrier touched during parent candle.",),
            )

        # 2. Ambiguous Bar (touches both TP and SL) - Evaluate Policy
        if policy == IntrabarPolicy.CONSERVATIVE_SL_FIRST:
            return IntrabarResolutionResult(
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=sl_price,
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                replay_bars_count=0,
                reasons=("Conservative policy: SL assumed hit first.",),
            )

        if policy == IntrabarPolicy.WORST_CASE:
            exit_price = (sl_price - worst_case_adverse_gap).quantize(Decimal("0.01"))
            return IntrabarResolutionResult(
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=exit_price,
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=IntrabarPolicy.WORST_CASE,
                replay_bars_count=0,
                reasons=("Worst-case policy: SL assumed hit first with adverse penalty gap.",),
            )

        if policy == IntrabarPolicy.SKIP_AMBIGUOUS:
            return IntrabarResolutionResult(
                barrier_hit=BarrierHitType.SKIPPED,
                exit_price=Decimal("0"),
                exit_timestamp=parent_candle.timestamp_close,
                policy_applied=IntrabarPolicy.SKIP_AMBIGUOUS,
                replay_bars_count=0,
                reasons=("Ambiguous trade excluded from sample (SKIP_AMBIGUOUS).",),
            )

        # 3. LOWER_TIMEFRAME_REPLAY Policy
        fill_ts = fill_timestamp or parent_candle.timestamp_open

        # For 4H/1H parent: try 15m first with strict pre-validation (P5-26)
        if parent_timeframe in ("4h", "1h"):
            if lower_tf_candles_15m:
                ok_15m, err_15m, valid_15m = _validate_sequence(
                    lower_tf_candles_15m,
                    expected_step_seconds=900,
                    parent_candle=parent_candle,
                    fill_ts=fill_ts,
                )
                if not ok_15m:
                    # Untrusted 15m sequence: must never select an ambiguous child by list position.
                    # Fail safe to CONSERVATIVE_SL_FIRST.
                    return IntrabarResolutionResult(
                        barrier_hit=BarrierHitType.SL_FIRST,
                        exit_price=sl_price,
                        exit_timestamp=parent_candle.timestamp_close,
                        policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                        replay_bars_count=0,
                        reasons=(f"15m parent grid malformed ({err_15m}); fell back to CONSERVATIVE_SL_FIRST.",),
                    )

                # Validated 15m sequence: chronologically inspect children
                for bar in valid_15m:
                    if bar.timestamp_close <= fill_ts:
                        continue
                    b_tp = bar.high >= tp_price
                    b_sl = bar.low <= sl_price
                    if b_tp and not b_sl:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.TP_FIRST,
                            exit_price=tp_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_15m),
                            reasons=(f"Resolved TP-first via 15m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_sl and not b_tp:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_15m),
                            reasons=(f"Resolved SL-first via 15m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_tp and b_sl:
                        # Ambiguous 15m bar: slice its own 1m/5m bars only, then recurse
                        c_1m = (
                            [c for c in lower_tf_candles_1m if c.timestamp_open >= bar.timestamp_open and c.timestamp_close <= bar.timestamp_close]
                            if lower_tf_candles_1m else None
                        )
                        c_5m = (
                            [c for c in lower_tf_candles_5m if c.timestamp_open >= bar.timestamp_open and c.timestamp_close <= bar.timestamp_close]
                            if lower_tf_candles_5m else None
                        )
                        sub_res = self.resolve(
                            parent_candle=bar,
                            tp_price=tp_price,
                            sl_price=sl_price,
                            fill_timestamp=fill_ts,
                            lower_tf_candles_1m=c_1m,
                            lower_tf_candles_5m=c_5m,
                            parent_timeframe="15m",
                            policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                        )
                        return sub_res

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
                    if bar.timestamp_close <= fill_ts:
                        continue
                    b_tp = bar.high >= tp_price
                    b_sl = bar.low <= sl_price
                    if b_tp and not b_sl:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.TP_FIRST,
                            exit_price=tp_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_1m),
                            reasons=(f"Resolved TP-first via 1m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_sl and not b_tp:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_1m),
                            reasons=(f"Resolved SL-first via 1m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_tp and b_sl:
                        # Ambiguous even at 1m resolution -> Fail-safe to SL_FIRST
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                            replay_bars_count=len(valid_1m),
                            reasons=("1m candle remained ambiguous; failed safe to SL_FIRST.",),
                        )
            else:
                # 1m sequence was provided but failed integrity/coverage validation
                if not lower_tf_candles_5m:
                    return IntrabarResolutionResult(
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
                    if bar.timestamp_close <= fill_ts:
                        continue
                    b_tp = bar.high >= tp_price
                    b_sl = bar.low <= sl_price
                    if b_tp and not b_sl:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.TP_FIRST,
                            exit_price=tp_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_5m),
                            reasons=(f"Resolved TP-first via 5m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_sl and not b_tp:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                            replay_bars_count=len(valid_5m),
                            reasons=(f"Resolved SL-first via 5m bar @ {bar.timestamp_close.isoformat()}.",),
                        )
                    if b_tp and b_sl:
                        return IntrabarResolutionResult(
                            barrier_hit=BarrierHitType.SL_FIRST,
                            exit_price=sl_price,
                            exit_timestamp=bar.timestamp_close,
                            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                            replay_bars_count=len(valid_5m),
                            reasons=("5m candle remained ambiguous; failed safe to SL_FIRST.",),
                        )
            else:
                return IntrabarResolutionResult(
                    barrier_hit=BarrierHitType.SL_FIRST,
                    exit_price=sl_price,
                    exit_timestamp=parent_candle.timestamp_close,
                    policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                    replay_bars_count=0,
                    reasons=(f"5m candle grid incomplete or malformed ({err_5m}); fell back to CONSERVATIVE_SL_FIRST.",),
                )

        # 4. Fallback: Lower-TF data missing -> Auto fallback to CONSERVATIVE_SL_FIRST (A14)
        return IntrabarResolutionResult(
            barrier_hit=BarrierHitType.SL_FIRST,
            exit_price=sl_price,
            exit_timestamp=parent_candle.timestamp_close,
            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
            replay_bars_count=0,
            reasons=("Lower-TF data unavailable; fallen back to CONSERVATIVE_SL_FIRST.",),
        )

