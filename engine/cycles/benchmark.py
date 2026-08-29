"""Baseline backtest performance benchmark recorder for Phase 3A."""
from datetime import datetime, timezone
from engine.core.types import BaselineBenchmark


def record_baseline_benchmark(
    profit_factor: float,
    expectancy_r: float,
    max_drawdown_pct: float,
    trade_count: int,
    timestamp: datetime = None,
    is_empirical: bool = False,
) -> BaselineBenchmark:
    """
    Record the baseline performance benchmark of Phase 2 + Phase 3A.
    This creates the empirical hurdle required for Phase 3B experimental features.
    """
    ts = timestamp or datetime.now(timezone.utc)
    return BaselineBenchmark(
        base_profit_factor=float(round(profit_factor, 4)),
        base_expectancy_r=float(round(expectancy_r, 4)),
        base_max_drawdown=float(round(max_drawdown_pct, 4)),
        base_trade_count=int(trade_count),
        recorded_at=ts,
        is_empirical=is_empirical,
    )
