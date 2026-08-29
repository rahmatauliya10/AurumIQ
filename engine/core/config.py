"""Configuration data structures and research defaults for the pure trading engine."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class EngineConfigData:
    """Consolidated configuration parameters for indicators, regime, structure, and sample guard."""
    
    # Trend indicators
    ema_fast_period: int = 20
    ema_mid_period: int = 50
    ema_slow_period: int = 200
    adx_period: int = 14
    adx_trend_threshold: float = 20.0
    
    # Momentum indicators
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    roc_period: int = 12
    
    # Volatility indicators
    atr_period: int = 14
    bollinger_period: int = 20
    bollinger_num_std: float = 2.0
    realized_vol_period: int = 20
    high_vol_zscore_threshold: float = 2.0
    
    # Volume indicators
    volume_lookback: int = 20
    volume_spike_zscore: float = 2.0
    
    # Causal Market Structure
    swing_left_bars: int = 3
    swing_right_bars: int = 3
    zone_atr_factor: Decimal = Decimal("0.5")
    max_active_zones: int = 5
    
    # Effective Sample Guard (A16)
    min_sample_threshold: int = 30
    low_quality_threshold: int = 60
    medium_quality_threshold: int = 100
    max_hhi_discount: float = 0.50
    max_clustering_discount: float = 0.50
