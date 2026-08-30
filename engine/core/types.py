"""Core dataclasses, enums, and pure value objects for the trading signal engine."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple, Dict, Any, List


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


class SessionType(str, Enum):
    """Trading session classifications evaluated with exact local DST awareness."""
    ASIA = "ASIA"
    LONDON_PREOPEN = "LONDON_PREOPEN"
    LONDON = "LONDON"
    LONDON_NY_OVERLAP = "LONDON_NY_OVERLAP"
    NEW_YORK = "NEW_YORK"
    US_LATE = "US_LATE"


class EventImpact(str, Enum):
    """Macroeconomic event market impact rating."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PromotionStatus(str, Enum):
    """Promotion eligibility status for Phase 3B experimental spectral features."""
    NOT_EVALUATED = "NOT_EVALUATED"
    BASELINE_NOT_EMPIRICAL = "BASELINE_NOT_EMPIRICAL"
    INSUFFICIENT_TRADES = "INSUFFICIENT_TRADES"
    FAILED = "FAILED"
    PROMOTABLE = "PROMOTABLE"


class ReliabilityStatus(str, Enum):
    """Cycle spectral consensus and stability classification."""
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    UNRELIABLE = "UNRELIABLE"


# --- Phase 4 Decision & State Machine Enums ---

class UserDecision(str, Enum):
    """User-facing deterministic trading recommendation."""
    BUY = "BUY"
    WAIT = "WAIT"
    AVOID = "AVOID"


class SignalState(str, Enum):
    """Internal finite state machine resolution states."""
    NO_TRADE = "NO_TRADE"
    AVOID = "AVOID"
    WATCH = "WATCH"
    READY = "READY"
    BUY_WINDOW = "BUY_WINDOW"
    FORCE_WAIT = "FORCE_WAIT"


class VolumeEvidenceType(str, Enum):
    """Volume semantic evidence types."""
    REAL_VOLUME = "REAL_VOLUME"
    TICK_VOLUME = "TICK_VOLUME"
    PROXY_VOLUME = "PROXY_VOLUME"
    UNAVAILABLE = "UNAVAILABLE"


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
    quote_rate: Optional[Decimal] = None
    close_usd: Optional[Decimal] = None
    volume_evidence: VolumeEvidenceType = VolumeEvidenceType.UNAVAILABLE


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
class VolumeFeatureResult:
    """Outcome of volume feature extraction with explicit semantic labeling."""
    evidence_type: VolumeEvidenceType
    is_usable: bool
    ratio: Optional[float] = None
    zscore: Optional[float] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class RegimeThresholdProfile:
    """
    Immutable specification of numerical thresholds for regime classification.
    Tracks calibration status to prevent uncalibrated instruments (e.g. XAUUSD)
    from silently inheriting legacy reference boundaries.
    """
    name: str = "LEGACY_XAUT_REFERENCE"
    is_calibrated: bool = True
    adx_trend_threshold: float = 20.0
    slope_boundary: float = 0.05
    high_vol_realized_pct: float = 5.0
    high_vol_atr_pct: float = 3.0
    high_vol_bb_bandwidth_pct: float = 15.0
    rsi_bull_threshold: float = 50.0
    rsi_bear_threshold: float = 50.0
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def legacy_xaut_profile(cls) -> "RegimeThresholdProfile":
        """Historical XAUT verified reference profile."""
        return cls(
            name="LEGACY_XAUT_REFERENCE",
            is_calibrated=True,
            adx_trend_threshold=20.0,
            slope_boundary=0.05,
            high_vol_realized_pct=5.0,
            high_vol_atr_pct=3.0,
            high_vol_bb_bandwidth_pct=15.0,
            rsi_bull_threshold=50.0,
            rsi_bear_threshold=50.0,
        )

    @classmethod
    def uncalibrated_xauusd_profile(cls) -> "RegimeThresholdProfile":
        """Explicitly uncalibrated profile for XAUUSD (requires empirical calibration)."""
        return cls(
            name="XAUUSD_UNCALIBRATED",
            is_calibrated=False,
            details={
                "calibration_status": "CALIBRATION_REQUIRED",
                "reason": "XAUUSD empirical regime thresholds not configured.",
            },
        )


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
    volume_evidence: VolumeEvidenceType = VolumeEvidenceType.UNAVAILABLE
    volume_usable: bool = False
    volume_reason: Optional[str] = None


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


# --- Phase 3A: Robust Time Cycle Data Contracts ---

@dataclass(frozen=True)
class SessionExpectancyEntry:
    """Empirical historical expectancy for a specific (Session, Regime) bucket."""
    session: SessionType
    regime: RegimeType
    sample_count: int
    effective_n: float
    win_rate: float
    expectancy_r: float
    is_statistically_significant: bool


@dataclass(frozen=True)
class CalendarEffectEntry:
    """Empirical historical expectancy and stability for a calendar bucket."""
    bucket: str  # e.g. "DOW_2_HOUR_14" or "MONTH_END"
    sample_count: int
    effective_n: float
    win_rate: float
    expectancy_r: float
    stability: float
    is_statistically_significant: bool


@dataclass(frozen=True)
class SessionContext:
    """DST-aware session classification and statistical expectancy metrics."""
    session: SessionType
    progress_pct: float
    is_high_liquidity: bool
    local_times: Dict[str, str]
    expectancy_score: float = 0.0
    sample_quality: SampleQuality = SampleQuality.INSUFFICIENT
    effective_n: float = 0.0


@dataclass(frozen=True)
class SwingDurationContext:
    """Causal swing age and correction maturity percentiles."""
    market_age_bars: int
    market_age_hours: float
    known_age_bars: int
    known_age_hours: float
    pullback_age_percentile: Optional[float]
    is_mature: bool
    maturity_score: float = 0.0
    sample_quality: SampleQuality = SampleQuality.INSUFFICIENT
    effective_n: float = 0.0


@dataclass(frozen=True)
class MacroEvent:
    """Macroeconomic release event with point-in-time revision tracking."""
    event_id: str
    name: str
    scheduled_at: datetime
    released_at: datetime
    initial_value: Optional[str]
    revised_at: Optional[datetime] = None
    revised_value: Optional[str] = None
    impact: EventImpact = EventImpact.HIGH


@dataclass(frozen=True)
class MacroEventContext:
    """Point-in-Time macro event proximity and blackout gating."""
    is_in_blackout: bool
    minutes_to_next_event: Optional[int] = None
    minutes_since_last_event: Optional[int] = None
    active_event_name: Optional[str] = None
    point_in_time_value: Optional[str] = None
    is_feed_healthy: bool = False


@dataclass(frozen=True)
class CalendarSeasonalityContext:
    """Calendar flows and rolling stability evaluated score."""
    day_of_week: int  # 0=Monday .. 6=Sunday
    day_name: str
    hour_utc: int
    month: int
    is_month_end_flow: bool
    stability_score: float
    seasonality_score: float = 0.0
    sample_quality: SampleQuality = SampleQuality.INSUFFICIENT
    effective_n: float = 0.0


@dataclass(frozen=True)
class Cycle3ASnapshot:
    """Immutable consolidated Phase 3A Robust Time Cycle snapshot."""
    timestamp: datetime
    session: SessionContext
    swing_duration: SwingDurationContext
    macro_event: MacroEventContext
    calendar: CalendarSeasonalityContext
    is_blocked_by_event: bool
    cycle_score_3a: float
    cycle_version: str = "3.0.0-3A"


@dataclass(frozen=True)
class BaselineBenchmark:
    """Baseline backtest performance metrics for Phase 3A + Phase 2 hurdle."""
    base_profit_factor: float
    base_expectancy_r: float
    base_max_drawdown: float
    base_trade_count: int
    recorded_at: datetime
    is_empirical: bool = False


# --- Phase 3B: Experimental Spectral & Cycle Research Data Contracts ---

@dataclass(frozen=True)
class AcfResult:
    """Causal Autocorrelation Function analysis output."""
    dominant_lag: Optional[int]
    autocorrelation: float
    is_significant: bool
    confidence_bound: float
    acf_series: Tuple[float, ...]
    effective_n: float
    sample_quality: SampleQuality


@dataclass(frozen=True)
class FftResult:
    """Causal Discrete Fourier Transform spectral analysis output."""
    dominant_period: Optional[float]
    dominant_frequency: Optional[float]
    power_ratio: float
    spectral_entropy: float
    psd_top_frequencies: Tuple[Tuple[float, float], ...]  # (frequency, power)
    is_cycle_detected: bool


@dataclass(frozen=True)
class WaveletResult:
    """Causal Continuous Wavelet Transform multi-scale energy output."""
    dominant_scale_period: Optional[float]
    energy_ratio: float
    coi_contamination_pct: float
    is_clean_endpoint: bool
    scales_analyzed: Tuple[float, ...]
    trusted_lag_bars: int = 0


@dataclass(frozen=True)
class HilbertResult:
    """Causal Hilbert Transform instantaneous phase and amplitude output."""
    instantaneous_phase: float  # radians in [-pi, pi]
    instantaneous_amplitude: float
    phase_velocity: float
    phase_stability: float
    is_endpoint_reliable: bool


@dataclass(frozen=True)
class CycleReliabilityResult:
    """Multi-method spectral cycle consensus and reliability evaluation."""
    dominant_period_bars: Optional[float]
    acf_strength: float
    fft_power_ratio: float
    wavelet_scale_strength: float
    hilbert_phase: float
    phase_stability: float
    method_agreement_pct: float
    effective_n: float
    sample_quality: SampleQuality
    reliability_score: float  # [0.0, 100.0]
    reliability_status: ReliabilityStatus
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardFoldResult:
    """Performance metrics for an individual Walk-Forward out-of-sample fold."""
    fold_id: int
    profit_factor: float
    expectancy_r: float
    max_drawdown: float
    trade_count: int
    net_profit: float = 0.0


@dataclass(frozen=True)
class PromotionEvaluation:
    """Evaluation result from the Empirical Promotion Gate."""
    status: PromotionStatus
    is_promotable: bool
    baseline_pf: float
    experimental_pf: float
    pf_improvement_pct: float
    trade_count: int
    max_drawdown_pct: float
    dd_deterioration_pct: float
    walk_forward_folds_passed: int
    walk_forward_folds_total: int
    is_single_period_dependent: bool
    max_fold_profit_share_pct: float
    effective_n: float = 0.0
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Cycle3BExperimentalSnapshot:
    """Immutable snapshot of Phase 3B Experimental Spectral and Cycle analysis."""
    timestamp: datetime
    timeframe: str
    acf: AcfResult
    fft: FftResult
    wavelet: WaveletResult
    hilbert: HilbertResult
    reliability: CycleReliabilityResult
    experimental_version: str = "3.1.0-3B"
    production_weight: float = field(default=0.0, init=False)  # HARD LOCKED TO 0.0
    promotion_status: PromotionStatus = PromotionStatus.BASELINE_NOT_EMPIRICAL


# --- Phase 4: Direction Score, Timing Score & State Machine Contracts ---

@dataclass(frozen=True)
class ComponentScore:
    """Individual breakdown component for Direction or Timing Score."""
    name: str
    score: float
    max_score: float
    reason: str
    is_available: bool = True
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectionScoreResult:
    """Consolidated Direction Score result (0.0 - 100.0)."""
    total_score: float
    max_score: float
    components: Tuple[ComponentScore, ...]
    is_bullish: bool
    config_version: str = "cfg-2026-v1"


@dataclass(frozen=True)
class TimingScoreResult:
    """Consolidated Timing Score result (0.0 - 100.0)."""
    total_score: float
    max_score: float
    components: Tuple[ComponentScore, ...]
    is_timing_ready: bool
    config_version: str = "cfg-2026-v1"


@dataclass(frozen=True)
class HardGateEvaluation:
    """Independent hard blockers evaluation overriding numerical scores."""
    is_blocked: bool
    override_state: Optional[SignalState]
    block_reasons: Tuple[str, ...]
    is_stale_data: bool = False
    is_provider_transition: bool = False
    is_macro_blackout: bool = False
    is_missing_xau: bool = False
    is_missing_normalization: bool = False
    is_unclosed_candle: bool = False


@dataclass(frozen=True)
class SignalSnapshot:
    """Immutable master signal decision snapshot with canonical fingerprint."""
    timestamp: datetime
    instrument: str
    timeframe: str
    state: SignalState
    user_decision: UserDecision
    direction: DirectionScoreResult
    timing: TimingScoreResult
    hard_gate: HardGateEvaluation
    reasons_positive: Tuple[str, ...]
    reasons_negative: Tuple[str, ...]
    hard_gate_reasons: Tuple[str, ...]
    analysis_fingerprint: str
    code_revision: str
    research_fingerprint: Optional[str] = None
    engine_version: str = "4.0.0"
    config_version: str = "cfg-2026-v1"
    feature_version: str = "feat-2026-v1"
    cycle_version: str = "3.0.0-3A"
    cycle_3b_informational: Optional[Cycle3BExperimentalSnapshot] = None


# --- Phase 5: Risk Engine & Causal Execution Contracts ---

class EntryExecutionPolicy(str, Enum):
    """Execution simulation policy for signal fill timing (Phase 5)."""
    NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
    MARKET_AFTER_SIGNAL = "MARKET_AFTER_SIGNAL"
    LIMIT_ZONE = "LIMIT_ZONE"


class IntrabarPolicy(str, Enum):
    """Policy for resolving intrabar ambiguity when High >= TP and Low <= SL (Phase 5)."""
    LOWER_TIMEFRAME_REPLAY = "LOWER_TIMEFRAME_REPLAY"
    CONSERVATIVE_SL_FIRST = "CONSERVATIVE_SL_FIRST"
    WORST_CASE = "WORST_CASE"
    SKIP_AMBIGUOUS = "SKIP_AMBIGUOUS"


class BarrierHitType(str, Enum):
    """Outcome of intrabar barrier evaluation."""
    TP_FIRST = "TP_FIRST"
    SL_FIRST = "SL_FIRST"
    UNRESOLVED = "UNRESOLVED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class QuoteData:
    """Timestamped bid/ask quote for market order execution simulation (Phase 5)."""
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    source: str = "orderbook"


@dataclass(frozen=True)
class FillResult:
    """Deterministic fill simulation output (Phase 5)."""
    fill_price: Decimal
    fill_timestamp: datetime
    policy: EntryExecutionPolicy
    latency_seconds: float
    spread_amount: Decimal
    slippage_amount: Decimal
    is_filled: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IntrabarResolutionResult:
    """Outcome of intrabar ambiguity resolution (Phase 5)."""
    barrier_hit: BarrierHitType
    exit_price: Decimal
    exit_timestamp: datetime
    policy_applied: IntrabarPolicy
    replay_bars_count: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RiskPlanSnapshot:
    """
    Immutable structure- and ATR-aware risk plan snapshot (Phase 5).
    Evaluated conditionally for BUY_WINDOW signals without altering Phase 4 audit trails.
    """
    source_signal_fingerprint: str
    signal_generated_at: datetime
    entry_min: Decimal
    entry_mid: Decimal
    entry_max: Decimal
    stop_structure: Decimal
    stop_atr: Decimal
    stop_final: Decimal
    stop_distance_atr: Decimal
    tp1: Decimal
    tp2: Decimal
    rr_tp1: Decimal
    rr_tp2: Decimal
    is_valid_risk_plan: bool
    execution_eligible: bool
    effective_action: UserDecision
    reasons: Tuple[str, ...]
    source_zone_id: Optional[str] = None
    source_zone_timestamp: Optional[datetime] = None
    risk_version: str = "5.0.0"
    execution_model_version: str = "5.0.0-exec-v1"
    config_version: str = "cfg-2026-v1"
    code_revision: str = "eae30005"

    @property
    def source_zone(self) -> Optional[str]:
        """Backward-compatible alias for source_zone_id."""
        return self.source_zone_id

    @property
    def source_zone_identity(self) -> Optional[str]:
        """Backward-compatible alias for source_zone_id."""
        return self.source_zone_id

    @property
    def entry_price_ideal(self) -> Decimal:
        """Backward-compatible alias for entry_mid."""
        return self.entry_mid

    @property
    def entry_limit_max(self) -> Decimal:
        """Backward-compatible alias for entry_max."""
        return self.entry_max

    @property
    def stop_loss_price(self) -> Decimal:
        """Backward-compatible alias for stop_final."""
        return self.stop_final

    @property
    def risk_reward_ratio(self) -> Decimal:
        """Backward-compatible alias for rr_tp1."""
        return self.rr_tp1

