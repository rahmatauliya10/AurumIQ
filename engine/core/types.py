"""Core dataclasses, enums, and pure value objects for the trading signal engine."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple, Dict, Any


class RegimeType(str, Enum):
    """Deterministic market regime classifications."""
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class SwingType(str, Enum):
    """Causal swing point classification."""
    HIGH = "HIGH"
    LOW = "LOW"


class StructureType(str, Enum):
    """Market structure trend hierarchy."""
    HH = "HH"  # Higher High
    HL = "HL"  # Higher Low
    LH = "LH"  # Lower High
    LL = "LL"  # Lower Low
    CONSOLIDATION = "CONSOLIDATION"
    UNKNOWN = "UNKNOWN"


class BosType(str, Enum):
    """Break of Structure signal."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NONE = "NONE"


class SampleQuality(str, Enum):
    """Statistical effective sample quality rating."""
    INSUFFICIENT = "INSUFFICIENT"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class CandleData:
    """Immutable OHLCV candlestick object strictly decoupled from Django."""
    timestamp_open: datetime
    timestamp_close: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool
    source_id: str = "default"
    quote_rate: Decimal = Decimal("1.0")
    close_usd: Optional[Decimal] = None


@dataclass(frozen=True)
class MarketContext:
    """Immutable context metadata for market analysis."""
    instrument: str
    timeframe: str
    as_of: datetime
    current_price: Decimal
    quote_rate: Optional[Decimal]
    lookback_bars: int


@dataclass(frozen=True)
class FeatureSnapshot:
    """Immutable snapshot of computed technical indicators and features."""
    timestamp: datetime
    # Trend
    ema20: Optional[Decimal]
    ema50: Optional[Decimal]
    ema200: Optional[Decimal]
    ema_slope_20: Optional[float]
    ema_alignment: int  # +1 Bullish, -1 Bearish, 0 Mixed
    adx: Optional[float]
    plus_di: Optional[float]
    minus_di: Optional[float]
    # Momentum
    rsi14: Optional[float]
    macd_line: Optional[Decimal]
    macd_signal: Optional[Decimal]
    macd_hist: Optional[Decimal]
    roc12: Optional[float]
    # Volatility
    atr14: Optional[Decimal]
    atr_pct: Optional[float]
    bb_upper: Optional[Decimal]
    bb_middle: Optional[Decimal]
    bb_lower: Optional[Decimal]
    bb_bandwidth: Optional[float]
    realized_vol_20: Optional[float]
    # Volume
    volume_ratio_20: Optional[float]
    volume_zscore_20: Optional[float]


@dataclass(frozen=True)
class RegimeResult:
    """Deterministic market regime classification output."""
    regime: RegimeType
    confidence: float
    timestamp: datetime
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SwingPoint:
    """Causal swing high or swing low point."""
    index: int
    timestamp: datetime
    detected_at: datetime
    price: Decimal
    swing_type: SwingType
    is_confirmed: bool


@dataclass(frozen=True)
class StructureZone:
    """ATR-normalized support or resistance bounding zone."""
    zone_type: str  # 'SUPPORT' or 'RESISTANCE'
    price_low: Decimal
    price_high: Decimal
    created_at: datetime
    touches: int
    is_active: bool


@dataclass(frozen=True)
class StructureResult:
    """Causal market structure analysis output."""
    timestamp: datetime
    structure_type: StructureType
    bos: BosType
    last_swing_high: Optional[SwingPoint]
    last_swing_low: Optional[SwingPoint]
    swings: Tuple[SwingPoint, ...]
    zones: Tuple[StructureZone, ...]


@dataclass(frozen=True)
class SampleEvaluation:
    """Evaluation result from the Statistical Effective Sample Guard (A16)."""
    n_raw: int
    independent_after_overlap: int
    temporal_clusters: int
    hhi_norm: float
    regime_discount: float
    clustering_discount: float
    effective_n: float
    quality: SampleQuality
    weight_multiplier: float
    is_blocked: bool
    message: str


# --- Future Phase Forward Contracts (Pure Data Contracts) ---

@dataclass(frozen=True)
class ScoreResult:
    """Directional and timing score evaluation contract."""
    direction_score: float
    timing_score: float
    confidence: float
    is_valid: bool
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskPlan:
    """Risk management plan contract."""
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
    risk_reward_ratio: Optional[float]
    position_size: Optional[Decimal]


@dataclass(frozen=True)
class AnalysisResult:
    """Consolidated master analysis result contract."""
    context: MarketContext
    features: FeatureSnapshot
    regime: RegimeResult
    structure: StructureResult
    sample_guard: SampleEvaluation
    score: Optional[ScoreResult] = None
    risk_plan: Optional[RiskPlan] = None
