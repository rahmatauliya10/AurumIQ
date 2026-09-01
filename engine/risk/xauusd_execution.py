"""
Side-aware causal entry execution simulation for XAUUSD (Phase 5).
Implements explicit bid/ask quote mechanics, next-bar open with synthetic spread,
limit-order protection, strict quote/candle validation, and lossless evidence fingerprints.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import Optional, Sequence

from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    QuoteData,
    RiskSide,
    SideAwareFillResult,
)
from engine.risk.xauusd_fingerprints import (
    canonical_utc_timestamp,
    compute_candle_evidence_fingerprint,
    compute_execution_fingerprint,
    compute_quote_evidence_fingerprint,
)
from engine.risk.xauusd_policy import XauUsdExecutionPolicy


def validate_xauusd_quote(quote: QuoteData) -> bool:
    """
    Strict validation for QuoteData in XAUUSD Phase 5 execution simulation.
    """
    if quote.timestamp.tzinfo is None or quote.timestamp.utcoffset() is None:
        return False
    if not isinstance(quote.bid, Decimal) or not quote.bid.is_finite() or quote.bid <= Decimal("0"):
        return False
    if not isinstance(quote.ask, Decimal) or not quote.ask.is_finite() or quote.ask <= Decimal("0"):
        return False
    if quote.bid > quote.ask:
        return False
    return True


def validate_xauusd_candle(candle: CandleData) -> bool:
    """
    Strict geometric, numeric, and temporal validation for CandleData in XAUUSD Phase 5.
    Used by execution models and intrabar resolvers.
    """
    if candle.timestamp_open.tzinfo is None or candle.timestamp_open.utcoffset() is None:
        return False
    if candle.timestamp_close.tzinfo is None or candle.timestamp_close.utcoffset() is None:
        return False
    if candle.timestamp_close <= candle.timestamp_open:
        return False

    prices = (candle.open, candle.high, candle.low, candle.close)
    for p in prices:
        if not isinstance(p, Decimal) or not p.is_finite() or p <= Decimal("0"):
            return False

    if not isinstance(candle.volume, Decimal) or not candle.volume.is_finite() or candle.volume < Decimal("0"):
        return False

    if candle.low > candle.high:
        return False
    if candle.high < candle.open or candle.high < candle.close:
        return False
    if candle.low > candle.open or candle.low > candle.close:
        return False

    return True


class SideAwareEntryExecutionModel:
    """
    Simulates point-in-time entry order execution for LONG and SHORT XAUUSD trades.

    Strict Invariants:
      1. Earliest execution timestamp: signal_generated_at + latency_seconds.
      2. MARKET LONG uses ASK + adverse slippage (UP).
      3. MARKET SHORT uses BID - adverse slippage (DOWN).
      4. Spread is counted once: observed for quote-based execution, synthetic for NEXT_BAR_OPEN.
      5. LIMIT LONG triggers on ASK <= limit_price; fill never worse than limit.
      6. LIMIT SHORT triggers on BID >= limit_price; fill never worse than limit.
      7. All invalid quotes/candles are rejected before execution.
      8. Preserves deterministic execution_fingerprint and lossless evidence provenance.
    """

    def __init__(
        self,
        code_revision: str,
        default_policy: Optional[XauUsdExecutionPolicy] = None,
        policy_fingerprint: str = "NONE",
    ):
        if not code_revision or not isinstance(code_revision, str) or not code_revision.strip():
            raise ValueError("code_revision is required for execution provenance.")
        self.code_revision = code_revision.strip()
        self.default_policy = default_policy if default_policy is not None else XauUsdExecutionPolicy()
        self.policy_fingerprint = policy_fingerprint

    def simulate_market_after_signal(
        self,
        side: RiskSide,
        signal_generated_at: datetime,
        quotes: Sequence[QuoteData],
        source_phase4_fingerprint: str,
        policy: Optional[XauUsdExecutionPolicy] = None,
    ) -> SideAwareFillResult:
        """
        Simulate market order execution at first available valid quote >= earliest_exec_ts.
        """
        active_policy = policy if policy is not None else self.default_policy
        if not active_policy.is_configured_for(EntryExecutionPolicy.MARKET_AFTER_SIGNAL):
            raise ValueError("Execution policy is not configured for MARKET_AFTER_SIGNAL.")

        lat = active_policy.latency_seconds if active_policy.latency_seconds is not None else 0.0
        sl_pct = active_policy.slippage_pct if active_policy.slippage_pct is not None else Decimal("0.00")

        sig_ts_utc = signal_generated_at.astimezone(timezone.utc)
        earliest_exec_ts = sig_ts_utc + timedelta(seconds=lat)

        # Filter valid quotes and enforce chronological sort
        valid_quotes = [
            q for q in quotes
            if validate_xauusd_quote(q) and q.timestamp.astimezone(timezone.utc) >= earliest_exec_ts
        ]
        valid_quotes.sort(key=lambda q: q.timestamp.astimezone(timezone.utc))

        if not valid_quotes:
            exec_fp = compute_execution_fingerprint(
                source_phase4_fingerprint=source_phase4_fingerprint,
                side=side,
                execution_policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
                signal_timestamp=sig_ts_utc,
                earliest_exec_ts=earliest_exec_ts,
                is_filled=False,
                raw_executable_price=None,
                fill_price=None,
                fill_timestamp=None,
                observed_spread=Decimal("0.00"),
                synthetic_spread=Decimal("0.00"),
                adverse_slippage=Decimal("0.00"),
                source_evidence_type=None,
                source_evidence_fingerprint=None,
                phase5_policy_fingerprint=self.policy_fingerprint,
                code_revision=self.code_revision,
            )
            return SideAwareFillResult(
                side=side,
                fill_policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
                raw_executable_price=None,
                fill_price=None,
                fill_timestamp=None,
                latency_seconds=lat,
                observed_spread=Decimal("0.00"),
                synthetic_spread=Decimal("0.00"),
                adverse_slippage=Decimal("0.00"),
                is_filled=False,
                reason="No eligible valid market quotes available on or after earliest_exec_ts.",
                source_evidence_type=None,
                source_evidence_fingerprint=None,
                execution_fingerprint=exec_fp,
            )

        first_quote = valid_quotes[0]
        observed_spread = (first_quote.ask - first_quote.bid).quantize(Decimal("0.01"))
        synthetic_spread = Decimal("0.00")

        if side == RiskSide.LONG:
            raw_fill = first_quote.ask
            adverse_slippage = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
            final_price = (raw_fill + adverse_slippage).quantize(Decimal("0.01"))
        else:
            raw_fill = first_quote.bid
            adverse_slippage = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
            final_price = (raw_fill - adverse_slippage).quantize(Decimal("0.01"))

        evidence_fp = compute_quote_evidence_fingerprint(first_quote)
        fill_ts = first_quote.timestamp.astimezone(timezone.utc)

        exec_fp = compute_execution_fingerprint(
            source_phase4_fingerprint=source_phase4_fingerprint,
            side=side,
            execution_policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
            signal_timestamp=sig_ts_utc,
            earliest_exec_ts=earliest_exec_ts,
            is_filled=True,
            raw_executable_price=raw_fill,
            fill_price=final_price,
            fill_timestamp=fill_ts,
            observed_spread=observed_spread,
            synthetic_spread=synthetic_spread,
            adverse_slippage=adverse_slippage,
            source_evidence_type="QUOTE",
            source_evidence_fingerprint=evidence_fp,
            phase5_policy_fingerprint=self.policy_fingerprint,
            code_revision=self.code_revision,
        )

        return SideAwareFillResult(
            side=side,
            fill_policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
            raw_executable_price=raw_fill,
            fill_price=final_price,
            fill_timestamp=fill_ts,
            latency_seconds=lat,
            observed_spread=observed_spread,
            synthetic_spread=synthetic_spread,
            adverse_slippage=adverse_slippage,
            is_filled=True,
            reason=f"Filled {side.value} market order at {fill_ts.isoformat()} (raw={raw_fill}, slippage={adverse_slippage}).",
            source_evidence_type="QUOTE",
            source_evidence_fingerprint=evidence_fp,
            execution_fingerprint=exec_fp,
        )

    def simulate_next_bar_open(
        self,
        side: RiskSide,
        signal_generated_at: datetime,
        candles: Sequence[CandleData],
        source_phase4_fingerprint: str,
        policy: Optional[XauUsdExecutionPolicy] = None,
    ) -> SideAwareFillResult:
        """
        Simulate fill on the open of the first subsequent bar >= earliest_exec_ts.
        """
        active_policy = policy if policy is not None else self.default_policy
        if not active_policy.is_configured_for(EntryExecutionPolicy.NEXT_BAR_OPEN):
            raise ValueError("Execution policy is not configured for NEXT_BAR_OPEN.")

        lat = active_policy.latency_seconds if active_policy.latency_seconds is not None else 0.0
        sp_pct = active_policy.synthetic_spread_pct if active_policy.synthetic_spread_pct is not None else Decimal("0.00")
        sl_pct = active_policy.slippage_pct if active_policy.slippage_pct is not None else Decimal("0.00")

        sig_ts_utc = signal_generated_at.astimezone(timezone.utc)
        earliest_exec_ts = sig_ts_utc + timedelta(seconds=lat)

        valid_bars = [
            c for c in candles
            if validate_xauusd_candle(c) and c.timestamp_open.astimezone(timezone.utc) >= earliest_exec_ts
        ]
        valid_bars.sort(key=lambda c: c.timestamp_open.astimezone(timezone.utc))

        if not valid_bars:
            exec_fp = compute_execution_fingerprint(
                source_phase4_fingerprint=source_phase4_fingerprint,
                side=side,
                execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
                signal_timestamp=sig_ts_utc,
                earliest_exec_ts=earliest_exec_ts,
                is_filled=False,
                raw_executable_price=None,
                fill_price=None,
                fill_timestamp=None,
                observed_spread=Decimal("0.00"),
                synthetic_spread=Decimal("0.00"),
                adverse_slippage=Decimal("0.00"),
                source_evidence_type=None,
                source_evidence_fingerprint=None,
                phase5_policy_fingerprint=self.policy_fingerprint,
                code_revision=self.code_revision,
            )
            return SideAwareFillResult(
                side=side,
                fill_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
                raw_executable_price=None,
                fill_price=None,
                fill_timestamp=None,
                latency_seconds=lat,
                observed_spread=Decimal("0.00"),
                synthetic_spread=Decimal("0.00"),
                adverse_slippage=Decimal("0.00"),
                is_filled=False,
                reason="No eligible valid subsequent bar open found on or after earliest_exec_ts.",
                source_evidence_type=None,
                source_evidence_fingerprint=None,
                execution_fingerprint=exec_fp,
            )

        first_bar = valid_bars[0]
        raw_fill = first_bar.open
        spread_amount = (raw_fill * (sp_pct / Decimal("100"))).quantize(Decimal("0.01"))
        slippage_amount = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))

        if side == RiskSide.LONG:
            final_price = (raw_fill + spread_amount + slippage_amount).quantize(Decimal("0.01"))
        else:
            final_price = (raw_fill - spread_amount - slippage_amount).quantize(Decimal("0.01"))

        evidence_fp = compute_candle_evidence_fingerprint(first_bar)
        fill_ts = first_bar.timestamp_open.astimezone(timezone.utc)

        exec_fp = compute_execution_fingerprint(
            source_phase4_fingerprint=source_phase4_fingerprint,
            side=side,
            execution_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
            signal_timestamp=sig_ts_utc,
            earliest_exec_ts=earliest_exec_ts,
            is_filled=True,
            raw_executable_price=raw_fill,
            fill_price=final_price,
            fill_timestamp=fill_ts,
            observed_spread=Decimal("0.00"),
            synthetic_spread=spread_amount,
            adverse_slippage=slippage_amount,
            source_evidence_type="CANDLE",
            source_evidence_fingerprint=evidence_fp,
            phase5_policy_fingerprint=self.policy_fingerprint,
            code_revision=self.code_revision,
        )

        return SideAwareFillResult(
            side=side,
            fill_policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
            raw_executable_price=raw_fill,
            fill_price=final_price,
            fill_timestamp=fill_ts,
            latency_seconds=lat,
            observed_spread=Decimal("0.00"),
            synthetic_spread=spread_amount,
            adverse_slippage=slippage_amount,
            is_filled=True,
            reason=f"Filled {side.value} next bar open @ {fill_ts.isoformat()} (raw={raw_fill}, spread={spread_amount}, slippage={slippage_amount}).",
            source_evidence_type="CANDLE",
            source_evidence_fingerprint=evidence_fp,
            execution_fingerprint=exec_fp,
        )

    def simulate_limit_zone(
        self,
        side: RiskSide,
        signal_generated_at: datetime,
        limit_price: Decimal,
        source_phase4_fingerprint: str,
        quotes: Optional[Sequence[QuoteData]] = None,
        candles: Optional[Sequence[CandleData]] = None,
        policy: Optional[XauUsdExecutionPolicy] = None,
    ) -> SideAwareFillResult:
        """
        Simulate limit order fill occurring strictly after post-activation touches.
        """
        active_policy = policy if policy is not None else self.default_policy
        if not active_policy.is_configured_for(EntryExecutionPolicy.LIMIT_ZONE):
            raise ValueError("Execution policy is not configured for LIMIT_ZONE.")

        lat = active_policy.latency_seconds if active_policy.latency_seconds is not None else 0.0
        sl_pct = active_policy.slippage_pct if active_policy.slippage_pct is not None else Decimal("0.00")

        sig_ts_utc = signal_generated_at.astimezone(timezone.utc)
        earliest_exec_ts = sig_ts_utc + timedelta(seconds=lat)

        # 1. Quote-level resolution
        if quotes is not None:
            valid_quotes = [
                q for q in quotes
                if validate_xauusd_quote(q) and q.timestamp.astimezone(timezone.utc) >= earliest_exec_ts
            ]
            valid_quotes.sort(key=lambda q: q.timestamp.astimezone(timezone.utc))

            for q in valid_quotes:
                if side == RiskSide.LONG and q.ask <= limit_price:
                    raw_fill = q.ask
                    slippage = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
                    final_price = min(limit_price, raw_fill + slippage).quantize(Decimal("0.01"))
                    evidence_fp = compute_quote_evidence_fingerprint(q)
                    fill_ts = q.timestamp.astimezone(timezone.utc)
                    observed_spread = (q.ask - q.bid).quantize(Decimal("0.01"))

                    exec_fp = compute_execution_fingerprint(
                        source_phase4_fingerprint=source_phase4_fingerprint,
                        side=side,
                        execution_policy=EntryExecutionPolicy.LIMIT_ZONE,
                        signal_timestamp=sig_ts_utc,
                        earliest_exec_ts=earliest_exec_ts,
                        is_filled=True,
                        raw_executable_price=raw_fill,
                        fill_price=final_price,
                        fill_timestamp=fill_ts,
                        observed_spread=observed_spread,
                        synthetic_spread=Decimal("0.00"),
                        adverse_slippage=slippage,
                        source_evidence_type="QUOTE",
                        source_evidence_fingerprint=evidence_fp,
                        phase5_policy_fingerprint=self.policy_fingerprint,
                        code_revision=self.code_revision,
                    )
                    return SideAwareFillResult(
                        side=side,
                        fill_policy=EntryExecutionPolicy.LIMIT_ZONE,
                        raw_executable_price=raw_fill,
                        fill_price=final_price,
                        fill_timestamp=fill_ts,
                        latency_seconds=lat,
                        observed_spread=observed_spread,
                        synthetic_spread=Decimal("0.00"),
                        adverse_slippage=slippage,
                        is_filled=True,
                        reason=f"LONG limit filled via post-activation quote @ {fill_ts.isoformat()}.",
                        source_evidence_type="QUOTE",
                        source_evidence_fingerprint=evidence_fp,
                        execution_fingerprint=exec_fp,
                    )

                elif side == RiskSide.SHORT and q.bid >= limit_price:
                    raw_fill = q.bid
                    slippage = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
                    final_price = max(limit_price, raw_fill - slippage).quantize(Decimal("0.01"))
                    evidence_fp = compute_quote_evidence_fingerprint(q)
                    fill_ts = q.timestamp.astimezone(timezone.utc)
                    observed_spread = (q.ask - q.bid).quantize(Decimal("0.01"))

                    exec_fp = compute_execution_fingerprint(
                        source_phase4_fingerprint=source_phase4_fingerprint,
                        side=side,
                        execution_policy=EntryExecutionPolicy.LIMIT_ZONE,
                        signal_timestamp=sig_ts_utc,
                        earliest_exec_ts=earliest_exec_ts,
                        is_filled=True,
                        raw_executable_price=raw_fill,
                        fill_price=final_price,
                        fill_timestamp=fill_ts,
                        observed_spread=observed_spread,
                        synthetic_spread=Decimal("0.00"),
                        adverse_slippage=slippage,
                        source_evidence_type="QUOTE",
                        source_evidence_fingerprint=evidence_fp,
                        phase5_policy_fingerprint=self.policy_fingerprint,
                        code_revision=self.code_revision,
                    )
                    return SideAwareFillResult(
                        side=side,
                        fill_policy=EntryExecutionPolicy.LIMIT_ZONE,
                        raw_executable_price=raw_fill,
                        fill_price=final_price,
                        fill_timestamp=fill_ts,
                        latency_seconds=lat,
                        observed_spread=observed_spread,
                        synthetic_spread=Decimal("0.00"),
                        adverse_slippage=slippage,
                        is_filled=True,
                        reason=f"SHORT limit filled via post-activation quote @ {fill_ts.isoformat()}.",
                        source_evidence_type="QUOTE",
                        source_evidence_fingerprint=evidence_fp,
                        execution_fingerprint=exec_fp,
                    )

        # 2. Candle-level resolution
        if candles is not None:
            has_midbar = False
            eligible_bars = []
            for c in candles:
                if not validate_xauusd_candle(c):
                    continue
                c_open = c.timestamp_open.astimezone(timezone.utc)
                c_close = c.timestamp_close.astimezone(timezone.utc)
                if c_open < earliest_exec_ts < c_close:
                    has_midbar = True
                elif c_open >= earliest_exec_ts:
                    eligible_bars.append(c)

            eligible_bars.sort(key=lambda c: c.timestamp_open.astimezone(timezone.utc))

            for bar in eligible_bars:
                touched = (bar.low <= limit_price) if side == RiskSide.LONG else (bar.high >= limit_price)
                if touched:
                    evidence_fp = compute_candle_evidence_fingerprint(bar)
                    fill_ts = bar.timestamp_open.astimezone(timezone.utc)

                    exec_fp = compute_execution_fingerprint(
                        source_phase4_fingerprint=source_phase4_fingerprint,
                        side=side,
                        execution_policy=EntryExecutionPolicy.LIMIT_ZONE,
                        signal_timestamp=sig_ts_utc,
                        earliest_exec_ts=earliest_exec_ts,
                        is_filled=True,
                        raw_executable_price=limit_price,
                        fill_price=limit_price,
                        fill_timestamp=fill_ts,
                        observed_spread=Decimal("0.00"),
                        synthetic_spread=Decimal("0.00"),
                        adverse_slippage=Decimal("0.00"),
                        source_evidence_type="CANDLE",
                        source_evidence_fingerprint=evidence_fp,
                        phase5_policy_fingerprint=self.policy_fingerprint,
                        code_revision=self.code_revision,
                    )
                    return SideAwareFillResult(
                        side=side,
                        fill_policy=EntryExecutionPolicy.LIMIT_ZONE,
                        raw_executable_price=limit_price,
                        fill_price=limit_price,
                        fill_timestamp=fill_ts,
                        latency_seconds=lat,
                        observed_spread=Decimal("0.00"),
                        synthetic_spread=Decimal("0.00"),
                        adverse_slippage=Decimal("0.00"),
                        is_filled=True,
                        reason=f"{side.value} limit touched on bar @ {fill_ts.isoformat()}.",
                        source_evidence_type="CANDLE",
                        source_evidence_fingerprint=evidence_fp,
                        execution_fingerprint=exec_fp,
                    )

            if has_midbar and not eligible_bars:
                exec_fp = compute_execution_fingerprint(
                    source_phase4_fingerprint=source_phase4_fingerprint,
                    side=side,
                    execution_policy=EntryExecutionPolicy.LIMIT_ZONE,
                    signal_timestamp=sig_ts_utc,
                    earliest_exec_ts=earliest_exec_ts,
                    is_filled=False,
                    raw_executable_price=None,
                    fill_price=None,
                    fill_timestamp=None,
                    observed_spread=Decimal("0.00"),
                    synthetic_spread=Decimal("0.00"),
                    adverse_slippage=Decimal("0.00"),
                    source_evidence_type=None,
                    source_evidence_fingerprint=None,
                    phase5_policy_fingerprint=self.policy_fingerprint,
                    code_revision=self.code_revision,
                )
                return SideAwareFillResult(
                    side=side,
                    fill_policy=EntryExecutionPolicy.LIMIT_ZONE,
                    raw_executable_price=None,
                    fill_price=None,
                    fill_timestamp=None,
                    latency_seconds=lat,
                    observed_spread=Decimal("0.00"),
                    synthetic_spread=Decimal("0.00"),
                    adverse_slippage=Decimal("0.00"),
                    is_filled=False,
                    reason="Cannot infer limit fill from mid-bar parent candle without intrabar timestamps.",
                    source_evidence_type=None,
                    source_evidence_fingerprint=None,
                    execution_fingerprint=exec_fp,
                )

        exec_fp = compute_execution_fingerprint(
            source_phase4_fingerprint=source_phase4_fingerprint,
            side=side,
            execution_policy=EntryExecutionPolicy.LIMIT_ZONE,
            signal_timestamp=sig_ts_utc,
            earliest_exec_ts=earliest_exec_ts,
            is_filled=False,
            raw_executable_price=None,
            fill_price=None,
            fill_timestamp=None,
            observed_spread=Decimal("0.00"),
            synthetic_spread=Decimal("0.00"),
            adverse_slippage=Decimal("0.00"),
            source_evidence_type=None,
            source_evidence_fingerprint=None,
            phase5_policy_fingerprint=self.policy_fingerprint,
            code_revision=self.code_revision,
        )
        return SideAwareFillResult(
            side=side,
            fill_policy=EntryExecutionPolicy.LIMIT_ZONE,
            raw_executable_price=None,
            fill_price=None,
            fill_timestamp=None,
            latency_seconds=lat,
            observed_spread=Decimal("0.00"),
            synthetic_spread=Decimal("0.00"),
            adverse_slippage=Decimal("0.00"),
            is_filled=False,
            reason="Limit price not touched after activation timestamp.",
            source_evidence_type=None,
            source_evidence_fingerprint=None,
            execution_fingerprint=exec_fp,
        )
