"""Market Integrity Engine, Outlier Quarantine (A15), XAUT/XAU Gate (A17), and Continuity Verifier (A20)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
import statistics
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TransitionCheckResult:
    """Result of 5-point provider failover continuity verification."""
    is_verified: bool
    source_switch: bool
    force_wait: bool
    healthy_closed_candles_count: int
    basis_difference_pct: Decimal
    spread_pct: Decimal
    has_bad_ticks: bool
    secondary_consensus_passed: bool
    reasons: list[str]


class ProviderContinuityVerifier:
    """
    5-Point Provider Transition Lifecycle (A20).
    Enforces FORCE_WAIT upon provider failover until ALL 5 criteria pass:
      1. Price basis difference between old and new provider <= 0.30%
      2. >= 3 consecutive closed candles are healthy
      3. Bid-ask spread <= normal envelope (<= 0.15%)
      4. Zero bad ticks (> 3x ATR)
      5. Secondary reference / cross-provider consensus confirms the new level (<= 0.35% diff)
    """

    MAX_BASIS_DIFF_PCT = Decimal("0.0030")       # 0.30%
    MAX_SPREAD_PCT = Decimal("0.0015")           # 0.15%
    MAX_CONSENSUS_DIFF_PCT = Decimal("0.0035")   # 0.35%
    REQUIRED_CONSECUTIVE_CANDLES = 3

    def __init__(
        self,
        max_basis_diff_pct: Decimal = MAX_BASIS_DIFF_PCT,
        max_spread_pct: Decimal = MAX_SPREAD_PCT,
        max_consensus_diff_pct: Decimal = MAX_CONSENSUS_DIFF_PCT,
        required_candles: int = REQUIRED_CONSECUTIVE_CANDLES,
    ):
        self.max_basis_diff_pct = max_basis_diff_pct
        self.max_spread_pct = max_spread_pct
        self.max_consensus_diff_pct = max_consensus_diff_pct
        self.required_candles = required_candles

    def verify_transition(
        self,
        old_provider_price: Optional[Decimal],
        new_provider_price: Decimal,
        consecutive_healthy_candles: int,
        bid: Optional[Decimal] = None,
        ask: Optional[Decimal] = None,
        has_bad_ticks: bool = False,
        secondary_reference_price: Optional[Decimal] = None,
        is_source_switch: bool = False,
    ) -> TransitionCheckResult:
        """Evaluate full 5-point continuity criteria during failover."""
        reasons: list[str] = []
        force_wait = False
        secondary_consensus_passed = True

        # If there is an active source switch
        if is_source_switch:
            force_wait = True

        # Point 1: Basis Difference between old & new provider
        basis_diff_pct = Decimal("0.0")
        if old_provider_price and old_provider_price > 0:
            basis_diff_pct = abs(new_provider_price - old_provider_price) / old_provider_price
            if basis_diff_pct > self.max_basis_diff_pct:
                force_wait = True
                reasons.append(
                    f"Failover basis jump ({basis_diff_pct * 100:.2f}%) exceeds allowed limit ({self.max_basis_diff_pct * 100:.2f}%)."
                )

        # Point 2: Consecutive Healthy Closed Candles
        if consecutive_healthy_candles < self.required_candles:
            force_wait = True
            reasons.append(
                f"Insufficient healthy closed candles ({consecutive_healthy_candles}/{self.required_candles}) from new provider."
            )

        # Point 3: Bid-Ask Spread Envelope
        spread_pct = Decimal("0.0")
        if bid and ask and ask > 0:
            spread_pct = (ask - bid) / ask
            if spread_pct > self.max_spread_pct:
                force_wait = True
                reasons.append(
                    f"Spread envelope ({spread_pct * 100:.2f}%) exceeds normal threshold ({self.max_spread_pct * 100:.2f}%)."
                )

        # Point 4: Bad Ticks
        if has_bad_ticks:
            force_wait = True
            reasons.append("Anomaly / bad tick detected (>3x ATR).")

        # Point 5: Secondary Reference Consensus Confirmation
        if secondary_reference_price is not None and secondary_reference_price > 0:
            consensus_diff_pct = abs(new_provider_price - secondary_reference_price) / secondary_reference_price
            if consensus_diff_pct > self.max_consensus_diff_pct:
                force_wait = True
                secondary_consensus_passed = False
                reasons.append(
                    f"Secondary reference consensus divergence ({consensus_diff_pct * 100:.2f}%) "
                    f"exceeds limit ({self.max_consensus_diff_pct * 100:.2f}%)."
                )

        is_verified = not force_wait and (consecutive_healthy_candles >= self.required_candles)

        if force_wait:
            logger.warning(
                "provider_transition_force_wait",
                reasons=reasons,
                is_source_switch=is_source_switch,
                basis_diff_pct=float(basis_diff_pct),
                secondary_passed=secondary_consensus_passed,
            )

        return TransitionCheckResult(
            is_verified=is_verified,
            source_switch=is_source_switch,
            force_wait=force_wait,
            healthy_closed_candles_count=consecutive_healthy_candles,
            basis_difference_pct=basis_diff_pct,
            spread_pct=spread_pct,
            has_bad_ticks=has_bad_ticks,
            secondary_consensus_passed=secondary_consensus_passed,
            reasons=reasons,
        )


@dataclass(frozen=True)
class QuarantineEvaluation:
    """Evaluation result for provider outlier detection."""
    quarantined_providers: list[str]
    valid_providers: list[str]
    median_price: Decimal
    deviations: Dict[str, Decimal]
    is_two_source_disagreement: bool = False
    force_wait: bool = False
    message: str = "OK"


@dataclass(frozen=True)
class XautXauIntegrityResult:
    """Integrity check result comparing normalized XAUT vs spot XAU gold."""
    basis_pct: Decimal
    is_valid: bool
    hard_fail: bool
    message: str


class MarketIntegrityEngine:
    """
    Core integrity engine enforcing:
      - A15: Provider Outlier Quarantine (>= 3 sources: median outlier; == 2 sources: safe disagreement policy)
      - A17: XAUT/XAU Integrity Gate (extreme unnormalized basis blocks BUY_WINDOW)
      - OHLC Logical Verification
    """

    OUTLIER_THRESHOLD_PCT = Decimal("0.0050")     # 0.50% basis deviation
    XAUT_XAU_MAX_BASIS_PCT = Decimal("0.0300")   # 3.00% basis deviation limit

    def __init__(
        self,
        outlier_threshold_pct: Decimal = OUTLIER_THRESHOLD_PCT,
        max_xaut_xau_basis_pct: Decimal = XAUT_XAU_MAX_BASIS_PCT,
    ):
        self.outlier_threshold_pct = outlier_threshold_pct
        self.max_xaut_xau_basis_pct = max_xaut_xau_basis_pct
        self.continuity_verifier = ProviderContinuityVerifier()

    def evaluate_provider_outliers(
        self,
        provider_prices: Dict[str, Decimal],
    ) -> QuarantineEvaluation:
        """
        A15: Robust Multi-Source Outlier Detection.
        - If >= 3 independent sources: Multi-source median outlier detection. Quarantines sources > 0.50%.
        - If == 2 sources: Computes pair divergence. If > 0.50%, flags TWO_SOURCE_DISAGREEMENT and FORCE_WAIT.
          Does NOT arbitrarily quarantine either provider without consensus.
        - If == 1 source: Valid with single-source advisory.
        """
        if not provider_prices:
            return QuarantineEvaluation([], [], Decimal("0"), {}, False, False, "No prices provided.")

        num_sources = len(provider_prices)
        prices = list(provider_prices.values())

        # CASE: Exactly 2 sources (P1-05 Two-provider disagreement safety)
        if num_sources == 2:
            pids = list(provider_prices.keys())
            p1, p2 = provider_prices[pids[0]], provider_prices[pids[1]]
            min_p = min(p1, p2)
            div_pct = abs(p1 - p2) / min_p if min_p > 0 else Decimal("0")
            deviations = {pids[0]: div_pct / 2, pids[1]: div_pct / 2}
            median_p = (p1 + p2) / 2

            if div_pct > self.outlier_threshold_pct:
                msg = (
                    f"DISAGREEMENT: 2 sources ({pids[0]}={p1}, {pids[1]}={p2}) diverge by {div_pct * 100:.2f}% > "
                    f"{self.outlier_threshold_pct * 100:.2f}%. Enforcing FORCE_WAIT (no arbitrary quarantine)."
                )
                logger.warning("two_provider_disagreement_force_wait", p1=pids[0], p2=pids[1], divergence=float(div_pct))
                return QuarantineEvaluation(
                    quarantined_providers=[],
                    valid_providers=[],
                    median_price=median_p,
                    deviations=deviations,
                    is_two_source_disagreement=True,
                    force_wait=True,
                    message=msg,
                )
            else:
                return QuarantineEvaluation(
                    quarantined_providers=[],
                    valid_providers=pids,
                    median_price=median_p,
                    deviations=deviations,
                    is_two_source_disagreement=False,
                    force_wait=False,
                    message=f"OK: 2 sources agree within {div_pct * 100:.2f}%.",
                )

        # CASE: >= 3 sources (Standard multi-source median quarantine)
        median_price = Decimal(str(statistics.median(prices)))
        quarantined: list[str] = []
        valid: list[str] = []
        deviations: Dict[str, Decimal] = {}

        for pid, price in provider_prices.items():
            if median_price > 0:
                dev = abs(price - median_price) / median_price
            else:
                dev = Decimal("0")
            deviations[pid] = dev

            if dev > self.outlier_threshold_pct:
                quarantined.append(pid)
                logger.warning(
                    "provider_quarantined_outlier",
                    provider=pid,
                    price=float(price),
                    median=float(median_price),
                    deviation_pct=float(dev * 100),
                )
            else:
                valid.append(pid)

        return QuarantineEvaluation(
            quarantined_providers=quarantined,
            valid_providers=valid,
            median_price=median_price,
            deviations=deviations,
            is_two_source_disagreement=False,
            force_wait=len(valid) == 0,
            message="OK" if not quarantined else f"Quarantined {len(quarantined)} outlier provider(s).",
        )

    def verify_xaut_xau_basis(
        self,
        xaut_usd_price: Optional[Decimal],
        xau_usd_price: Optional[Decimal],
    ) -> XautXauIntegrityResult:
        """
        A17: Validate basis between normalized XAUT (USD) and spot XAU gold reference (USD).
        Formula: Basis = |XAUT_USD - XAU_USD| / XAU_USD
        """
        if xau_usd_price is None or xau_usd_price <= 0:
            msg = "GOLD_REFERENCE_UNAVAILABLE: Canonical spot XAU/USD gold reference price is missing. BUY_WINDOW blocked."
            logger.critical("canonical_gold_reference_missing")
            return XautXauIntegrityResult(
                basis_pct=Decimal("1.0"),
                is_valid=False,
                hard_fail=True,
                message=msg,
            )

        if xaut_usd_price is None or xaut_usd_price <= 0:
            return XautXauIntegrityResult(
                basis_pct=Decimal("1.0"),
                is_valid=False,
                hard_fail=True,
                message="Invalid non-positive price for XAUT.",
            )

        basis_pct = abs(xaut_usd_price - xau_usd_price) / xau_usd_price
        hard_fail = basis_pct > self.max_xaut_xau_basis_pct

        if hard_fail:
            msg = (
                f"A17 CRITICAL: XAUT/XAU basis divergence ({basis_pct * 100:.2f}%) exceeds "
                f"maximum limit ({self.max_xaut_xau_basis_pct * 100:.2f}%). Hard gate activated: BUY_WINDOW blocked."
            )
            logger.critical("xaut_xau_basis_spike_hard_fail", basis_pct=float(basis_pct))
        else:
            msg = f"OK: XAUT/XAU basis healthy ({basis_pct * 100:.2f}%)."

        return XautXauIntegrityResult(
            basis_pct=basis_pct,
            is_valid=not hard_fail,
            hard_fail=hard_fail,
            message=msg,
        )

    @staticmethod
    def validate_candle_ohlc(
        open_: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: Decimal,
    ) -> tuple[bool, list[str]]:
        """Validate logical OHLC integrity."""
        errors: list[str] = []
        if low > high:
            errors.append(f"Low ({low}) > High ({high})")
        if open_ > high or open_ < low:
            errors.append(f"Open ({open_}) outside [Low={low}, High={high}]")
        if close > high or close < low:
            errors.append(f"Close ({close}) outside [Low={low}, High={high}]")
        if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
            errors.append("Non-positive OHLC price value")
        if volume < 0:
            errors.append(f"Negative volume ({volume})")

        return len(errors) == 0, errors
