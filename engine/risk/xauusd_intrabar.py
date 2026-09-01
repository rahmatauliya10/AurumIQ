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
        worst_case_adverse_gap: Optional[Decimal] = None,
    ) -> SideIntrabarResolutionResult:
        """
        Evaluate barrier resolution for a trade within parent_candle.
        """
        # 0. Strict parent candle validation
        if not validate_xauusd_candle(parent_candle):
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.UNRESOLVED,
                exit_price=None,
                exit_timestamp=None,
                policy_applied=policy,
                replay_bars_count=0,
                reasons=("Parent candle failed strict validation (OHLC/timestamps).",),
            )

        p_open_ts = parent_candle.timestamp_open.astimezone(timezone.utc)
        effective_fill_ts = (
            fill_timestamp.astimezone(timezone.utc)
            if fill_timestamp is not None
            else p_open_ts
        )

        if fill_timestamp is not None:
            if fill_timestamp.tzinfo is None or fill_timestamp.tzinfo.utcoffset(fill_timestamp) is None:
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.UNRESOLVED,
                    exit_price=None,
                    exit_timestamp=None,
                    policy_applied=policy,
                    replay_bars_count=0,
                    reasons=("fill_timestamp must be timezone aware with non-None utcoffset.",),
                )

        # 1. Determine Barrier Hits on Parent Candle
        if side == RiskSide.LONG:
            tp_hit = parent_candle.high >= tp_price
            sl_hit = parent_candle.low <= sl_price
        else:
            tp_hit = parent_candle.low <= tp_price
            sl_hit = parent_candle.high >= sl_price

        # Case A: Neither hit
        if not tp_hit and not sl_hit:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.NONE,
                exit_price=None,
                exit_timestamp=None,
                policy_applied=policy,
                replay_bars_count=0,
                reasons=("Neither TP nor SL barrier was touched on parent bar.",),
            )

        # Case B: Clear Single Hit (Non-ambiguous)
        if tp_hit and not sl_hit:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.TP_FIRST,
                exit_price=tp_price,
                exit_timestamp=parent_candle.timestamp_close.astimezone(timezone.utc),
                policy_applied=policy,
                replay_bars_count=0,
                reasons=(f"{side.value} TP reached without SL touch.",),
            )

        if sl_hit and not tp_hit:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=sl_price,
                exit_timestamp=parent_candle.timestamp_close.astimezone(timezone.utc),
                policy_applied=policy,
                replay_bars_count=0,
                reasons=(f"{side.value} SL reached without TP touch.",),
            )

        # Case C: Ambiguous Collision (Both TP and SL hit)
        if policy == IntrabarPolicy.CONSERVATIVE_SL_FIRST:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=sl_price,
                exit_timestamp=parent_candle.timestamp_close.astimezone(timezone.utc),
                policy_applied=policy,
                replay_bars_count=0,
                reasons=("Ambiguous collision resolved by CONSERVATIVE_SL_FIRST.",),
            )

        if policy == IntrabarPolicy.WORST_CASE:
            if (
                worst_case_adverse_gap is None
                or not isinstance(worst_case_adverse_gap, Decimal)
                or not worst_case_adverse_gap.is_finite()
                or worst_case_adverse_gap < Decimal("0")
            ):
                raise ValueError("worst_case_adverse_gap must be a non-negative finite Decimal when policy is WORST_CASE.")

            if side == RiskSide.LONG:
                exit_px = sl_price - worst_case_adverse_gap
            else:
                exit_px = sl_price + worst_case_adverse_gap

            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=exit_px,
                exit_timestamp=parent_candle.timestamp_close.astimezone(timezone.utc),
                policy_applied=policy,
                replay_bars_count=0,
                reasons=(f"Ambiguous collision resolved by WORST_CASE (exit={exit_px}).",),
            )

        if policy == IntrabarPolicy.SKIP_AMBIGUOUS:
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SKIPPED,
                exit_price=None,
                exit_timestamp=None,
                policy_applied=policy,
                replay_bars_count=0,
                reasons=("Ambiguous collision skipped by SKIP_AMBIGUOUS policy.",),
            )

        # Policy == LOWER_TIMEFRAME_REPLAY
        if parent_timeframe in ("4H", "1H"):
            if lower_tf_candles_15m:
                v_ok, v_err, valid_15m = _validate_sequence(
                    lower_tf_candles_15m, 900, parent_candle, effective_fill_ts
                )
                if v_ok:
                    res_15m = self._replay_bars(side, valid_15m, tp_price, sl_price)
                    if res_15m is not None:
                        if res_15m.barrier_hit != BarrierHitType.SL_FIRST or not any("ambiguous" in r for r in res_15m.reasons):
                            return res_15m
                        # If a 15m child bar is ambiguous, try drilling into 1m / 5m
                        if lower_tf_candles_1m:
                            v_1m_ok, _, valid_1m = _validate_sequence(
                                lower_tf_candles_1m, 60, parent_candle, effective_fill_ts
                            )
                            if v_1m_ok:
                                res_1m = self._replay_bars(side, valid_1m, tp_price, sl_price)
                                if res_1m is not None and not any("ambiguous" in r for r in res_1m.reasons):
                                    return res_1m
                        if lower_tf_candles_5m:
                            v_5m_ok, _, valid_5m = _validate_sequence(
                                lower_tf_candles_5m, 300, parent_candle, effective_fill_ts
                            )
                            if v_5m_ok:
                                res_5m = self._replay_bars(side, valid_5m, tp_price, sl_price)
                                if res_5m is not None and not any("ambiguous" in r for r in res_5m.reasons):
                                    return res_5m

            # Fallback for parent 4H/1H if lower TF is malformed or unresolved
            return SideIntrabarResolutionResult(
                side=side,
                barrier_hit=BarrierHitType.SL_FIRST,
                exit_price=sl_price,
                exit_timestamp=parent_candle.timestamp_close.astimezone(timezone.utc),
                policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
                replay_bars_count=0,
                reasons=("15m/1m/5m replay unavailable or unresolved; fallen back to CONSERVATIVE_SL_FIRST.",),
            )

        # Parent timeframe == 15m
        if lower_tf_candles_1m:
            v_ok, v_err, valid_1m = _validate_sequence(
                lower_tf_candles_1m, 60, parent_candle, effective_fill_ts
            )
            if v_ok:
                res = self._replay_bars(side, valid_1m, tp_price, sl_price)
                if res is not None and not any("ambiguous" in r for r in res.reasons):
                    return res

        if lower_tf_candles_5m:
            v_ok, v_err, valid_5m = _validate_sequence(
                lower_tf_candles_5m, 300, parent_candle, effective_fill_ts
            )
            if v_ok:
                res = self._replay_bars(side, valid_5m, tp_price, sl_price)
                if res is not None and not any("ambiguous" in r for r in res.reasons):
                    return res

        # Fallback to CONSERVATIVE_SL_FIRST if sub-bar replay is unavailable or still ambiguous
        return SideIntrabarResolutionResult(
            side=side,
            barrier_hit=BarrierHitType.SL_FIRST,
            exit_price=sl_price,
            exit_timestamp=parent_candle.timestamp_close.astimezone(timezone.utc),
            policy_applied=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
            replay_bars_count=0,
            reasons=("1m/5m lower TF sequence unavailable or unresolved; fallen back to CONSERVATIVE_SL_FIRST.",),
        )

    def _replay_bars(
        self,
        side: RiskSide,
        bars: Sequence[CandleData],
        tp_price: Decimal,
        sl_price: Decimal,
    ) -> Optional[SideIntrabarResolutionResult]:
        """
        Replay lower-timeframe bars in strict chronological order.
        """
        for i, bar in enumerate(bars, start=1):
            if side == RiskSide.LONG:
                tp = bar.high >= tp_price
                sl = bar.low <= sl_price
            else:
                tp = bar.low <= tp_price
                sl = bar.high >= sl_price

            if tp and not sl:
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.TP_FIRST,
                    exit_price=tp_price,
                    exit_timestamp=bar.timestamp_close.astimezone(timezone.utc),
                    policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                    replay_bars_count=i,
                    reasons=(f"{side.value} TP hit first on sub-bar @ {bar.timestamp_open.isoformat()}.",),
                )
            if sl and not tp:
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.SL_FIRST,
                    exit_price=sl_price,
                    exit_timestamp=bar.timestamp_close.astimezone(timezone.utc),
                    policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                    replay_bars_count=i,
                    reasons=(f"{side.value} SL hit first on sub-bar @ {bar.timestamp_open.isoformat()}.",),
                )
            if tp and sl:
                # Sub-bar is itself ambiguous
                return SideIntrabarResolutionResult(
                    side=side,
                    barrier_hit=BarrierHitType.SL_FIRST,
                    exit_price=sl_price,
                    exit_timestamp=bar.timestamp_close.astimezone(timezone.utc),
                    policy_applied=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
                    replay_bars_count=i,
                    reasons=(f"Sub-bar itself ambiguous @ {bar.timestamp_open.isoformat()}.",),
                )

        return None
