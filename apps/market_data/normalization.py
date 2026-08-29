"""Quote Currency Normalizer and Two-Way Stablecoin Peg Integrity Checker (R19 / A21)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence, Tuple
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class NormalizationCheckResult:
    """Result of quote normalization and peg stability evaluation."""
    rate: Optional[Decimal]
    deviation: Optional[Decimal]
    is_warning: bool
    hard_fail: bool
    normalized_price: Optional[Decimal]
    is_stale: bool
    rate_timestamp: Optional[datetime]
    message: str


class QuoteNormalizer:
    """
    Rigorously normalizes quote currencies (e.g. USDT -> USD), enforces Point-in-Time (PIT)
    rate selection (T_rate <= T_candle), and validates stablecoin peg stability.
    
    Safety Rules (P1-09):
      - Never silently defaults to 1.0 on missing data.
      - Missing or critically stale rate triggers hard fail and blocks BUY_WINDOW.
    """

    WARNING_DEVIATION_THRESHOLD = Decimal("0.0050")  # 0.50%
    CRITICAL_DEVIATION_THRESHOLD = Decimal("0.0200")  # 2.00%
    DEFAULT_MAX_STALENESS_SECONDS = 3600              # 1 Hour
    CRITICAL_MAX_STALENESS_SECONDS = 86400           # 24 Hours

    def __init__(
        self,
        warning_threshold: Decimal = WARNING_DEVIATION_THRESHOLD,
        critical_threshold: Decimal = CRITICAL_DEVIATION_THRESHOLD,
        max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS,
    ):
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.max_staleness_seconds = max_staleness_seconds

    def evaluate_peg(self, usdt_usd_rate: Optional[Decimal]) -> tuple[Optional[Decimal], bool, bool, str]:
        """
        Evaluate peg deviation for a given USDT/USD rate.
        Returns (deviation, is_warning, hard_fail, message).
        """
        if usdt_usd_rate is None or usdt_usd_rate <= 0:
            msg = "CRITICAL: Quote normalization rate unavailable (None/non-positive). Never default to 1.0."
            logger.critical("quote_rate_missing_hard_fail")
            return None, True, True, msg

        deviation = abs(usdt_usd_rate - Decimal("1.0"))
        hard_fail = deviation >= self.critical_threshold
        is_warning = deviation >= self.warning_threshold

        if hard_fail:
            msg = (
                f"CRITICAL: USDT peg de-anchored! Rate={usdt_usd_rate:.6f} "
                f"(Deviation={deviation * 100:.2f}% >= {self.critical_threshold * 100:.2f}%). Hard gate activated."
            )
            logger.critical("stablecoin_peg_critical_fail", rate=float(usdt_usd_rate), deviation=float(deviation))
        elif is_warning:
            msg = (
                f"WARNING: USDT peg anomalous. Rate={usdt_usd_rate:.6f} "
                f"(Deviation={deviation * 100:.2f}% >= {self.warning_threshold * 100:.2f}%)."
            )
            logger.warning("stablecoin_peg_warning", rate=float(usdt_usd_rate), deviation=float(deviation))
        else:
            msg = f"OK: Stablecoin peg healthy. Rate={usdt_usd_rate:.6f} (Deviation={deviation * 100:.2f}%)."

        return deviation, is_warning, hard_fail, msg

    def find_rate_as_of(
        self,
        target_timestamp: datetime,
        rate_history: Sequence[Tuple[datetime, Decimal]],
        max_staleness_seconds: Optional[int] = None,
    ) -> Tuple[Optional[Decimal], Optional[datetime], bool, bool, str]:
        """
        P1-03, P1-04, P1-09: Select latest USDT rate where rate_timestamp <= target_timestamp.
        Never uses future rates (timestamp > target_timestamp).
        Never defaults to 1.0 when missing.
        
        Returns: (rate, rate_timestamp, is_stale, is_hard_fail, message)
        """
        max_staleness = max_staleness_seconds or self.max_staleness_seconds
        
        # Filter strictly historical / concurrent observations (<= target_timestamp)
        valid_rates = [
            (ts, r) for ts, r in rate_history
            if ts.astimezone(timezone.utc) <= target_timestamp.astimezone(timezone.utc) and r is not None and r > 0
        ]

        if not valid_rates:
            msg = f"CRITICAL: No historical USDT/USD rate available on or before {target_timestamp.isoformat()}."
            logger.error("no_pit_rate_available", target=target_timestamp.isoformat())
            return None, None, True, True, msg

        # Pick the most recent rate before or at target_timestamp
        valid_rates.sort(key=lambda x: x[0])
        latest_ts, selected_rate = valid_rates[-1]
        
        age_seconds = (target_timestamp.astimezone(timezone.utc) - latest_ts.astimezone(timezone.utc)).total_seconds()
        is_stale = age_seconds > max_staleness
        is_critically_stale = age_seconds > self.CRITICAL_MAX_STALENESS_SECONDS

        if is_critically_stale:
            msg = f"CRITICAL: USDT/USD rate is severely stale ({age_seconds / 3600:.1f}h old > 24h). Hard gate active."
            return selected_rate, latest_ts, True, True, msg
        elif is_stale:
            msg = f"WARNING: USDT/USD rate is stale ({age_seconds:.0f}s old > {max_staleness}s)."
            return selected_rate, latest_ts, True, False, msg

        return selected_rate, latest_ts, False, False, "OK: PIT rate synchronized."

    def normalize_price(
        self,
        raw_price_usdt: Decimal,
        usdt_usd_rate: Optional[Decimal],
        rate_timestamp: Optional[datetime] = None,
        is_stale: bool = False,
    ) -> NormalizationCheckResult:
        """
        Normalize raw XAUT/USDT price to true USD reference price.
        Formula: Price_USD = Price_USDT * USDT_USD.
        If rate is None, hard_fail=True and normalized_price=None (never defaults to 1.0).
        """
        deviation, is_warning, hard_fail, msg = self.evaluate_peg(usdt_usd_rate)
        
        if usdt_usd_rate is None or hard_fail:
            return NormalizationCheckResult(
                rate=usdt_usd_rate,
                deviation=deviation,
                is_warning=True,
                hard_fail=True,
                normalized_price=None,
                is_stale=is_stale,
                rate_timestamp=rate_timestamp,
                message=msg,
            )

        normalized_price = (raw_price_usdt * usdt_usd_rate).quantize(Decimal("0.00000001"))
        
        if is_stale and not hard_fail:
            is_warning = True
            msg = f"{msg} (Rate is STALE)"

        return NormalizationCheckResult(
            rate=usdt_usd_rate,
            deviation=deviation,
            is_warning=is_warning,
            hard_fail=hard_fail,
            normalized_price=normalized_price,
            is_stale=is_stale,
            rate_timestamp=rate_timestamp,
            message=msg,
        )

    def normalize_price_pit(
        self,
        raw_price_usdt: Decimal,
        candle_timestamp: datetime,
        rate_history: Sequence[Tuple[datetime, Decimal]],
        max_staleness_seconds: Optional[int] = None,
    ) -> NormalizationCheckResult:
        """
        Point-in-time quote normalization ensuring zero future data lookahead.
        """
        rate, rate_ts, is_stale, pit_hard_fail, pit_msg = self.find_rate_as_of(
            candle_timestamp, rate_history, max_staleness_seconds
        )
        
        if rate is None:
            return NormalizationCheckResult(
                rate=None,
                deviation=None,
                is_warning=True,
                hard_fail=True,
                normalized_price=None,
                is_stale=is_stale,
                rate_timestamp=None,
                message=pit_msg,
            )

        base_res = self.normalize_price(
            raw_price_usdt=raw_price_usdt,
            usdt_usd_rate=rate,
            rate_timestamp=rate_ts,
            is_stale=is_stale,
        )
        
        combined_hard_fail = base_res.hard_fail or pit_hard_fail
        combined_warning = base_res.is_warning or is_stale
        combined_msg = f"{base_res.message} | {pit_msg}"

        return NormalizationCheckResult(
            rate=rate,
            deviation=base_res.deviation,
            is_warning=combined_warning,
            hard_fail=combined_hard_fail,
            normalized_price=base_res.normalized_price,
            is_stale=is_stale,
            rate_timestamp=rate_ts,
            message=combined_msg,
        )
