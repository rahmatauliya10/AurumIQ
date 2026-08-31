"""Selective Gate state machine and deterministic user-decision mapping (Phase 4)."""
from typing import Any, List, Optional, Tuple

from engine.core.types import (
    CandidateGateResult,
    CandleData,
    DirectionScoreResult,
    FeedCriticality,
    FeedHealthStatus,
    HardGateEvaluation,
    MacroEventContext,
    RegimeResult,
    RegimeType,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    StructureResult,
    StructureType,
    TimingScoreResult,
    UserDecision,
    XauUsdHardGateEvaluation,
)


def evaluate_hard_gates(
    is_feed_stale: bool = False,
    is_provider_transition: bool = False,
    is_macro_blackout: bool = False,
    is_missing_xau: bool = False,
    is_missing_normalization: bool = False,
    is_unclosed_candle: bool = False,
) -> HardGateEvaluation:
    """
    Evaluate independent hard blockers overriding numerical scores.
    """
    reasons: List[str] = []

    if is_unclosed_candle:
        reasons.append("Analysis candle at evaluation timestamp is unclosed.")
    if is_feed_stale:
        reasons.append("Market feed delay exceeded allowable threshold (Stale Feed).")
    if is_provider_transition:
        reasons.append("Primary market data provider in TRANSITION / secondary consensus unconfirmed.")
    if is_macro_blackout:
        reasons.append("Active high-impact macroeconomic event blackout window in progress.")
    if is_missing_xau:
        reasons.append("Missing verified canonical XAU/USD gold reference feed.")
    if is_missing_normalization:
        reasons.append("Missing USDT/USD peg normalization rate reference.")

    is_blocked = len(reasons) > 0
    override_state = SignalState.FORCE_WAIT if is_blocked else None

    return HardGateEvaluation(
        is_blocked=is_blocked,
        override_state=override_state,
        block_reasons=tuple(reasons),
        is_stale_data=is_feed_stale,
        is_provider_transition=is_provider_transition,
        is_macro_blackout=is_macro_blackout,
        is_missing_xau=is_missing_xau,
        is_missing_normalization=is_missing_normalization,
        is_unclosed_candle=is_unclosed_candle,
    )


def evaluate_selective_gate(
    direction: DirectionScoreResult,
    timing: TimingScoreResult,
    regime: Optional[RegimeResult],
    structure: Optional[StructureResult],
    hard_gate: HardGateEvaluation,
    is_reversal_confirmed: bool = False,
    is_near_support: bool = False,
    is_data_sufficient: bool = True,
) -> Tuple[SignalState, UserDecision]:
    """
    Evaluate deterministic Selective Gate state machine with strict precedence.

    Deterministic Collision Precedence Hierarchy:
      0. CLOSED-CANDLE / HARD SAFETY CHECK (Overrides all downstream conditions)
         - Stale critical feed, provider transition, macro blackout,
           missing canonical XAU reference, missing USDT normalization,
           or unclosed decision candle
         -> FORCE_WAIT (user_decision = WAIT)

      1. ANALYSIS INITIALIZATION CHECK
         - Feeds are healthy, but historical candle lookback / features /
           regime / structure context are uninitialized or insufficient (< 32 bars)
         -> NO_TRADE (user_decision = WAIT)

      2. HOSTILE / PROHIBITED MARKET CONDITIONS
         - Feeds healthy + sufficient data, but market regime is BEAR_TREND /
           HIGH_VOLATILITY or market structure is hostile (LL/LH breakdown)
         -> AVOID (user_decision = AVOID)

      3. NUMERICAL SCORE & TRIGGER STATES
         - Direction >= 80.0, Timing >= 80.0, Closed 15m Reversal -> BUY_WINDOW (BUY)
         - Direction >= 75.0, Timing >= 70.0, Near Support Zone   -> READY (WAIT)
         - Direction >= 70.0, Structure Intact                    -> WATCH (WAIT)
         - Default below threshold                                -> NO_TRADE (WAIT)
    """
    # 0. Hard Safety & Closed-Candle Gate (Top Precedence)
    if hard_gate.is_blocked:
        return SignalState.FORCE_WAIT, UserDecision.WAIT

    # 1. Critical Analysis Initialization Guard (Feeds healthy, but data history insufficient)
    if not is_data_sufficient or regime is None or structure is None:
        return SignalState.NO_TRADE, UserDecision.WAIT

    # 2. Hostile / Prohibited Market Conditions (AVOID)
    if regime.regime in (RegimeType.BEAR_TREND, RegimeType.HIGH_VOLATILITY):
        return SignalState.AVOID, UserDecision.AVOID
    if structure.structure_type in (StructureType.LL, StructureType.LH) and direction.total_score < 40.0:
        return SignalState.AVOID, UserDecision.AVOID

    dir_score = direction.total_score
    tim_score = timing.total_score

    # 3. Numerical Score States
    # Level A: BUY_WINDOW Qualification
    if dir_score >= 80.0 and tim_score >= 80.0 and is_reversal_confirmed:
        return SignalState.BUY_WINDOW, UserDecision.BUY

    # Level B: READY Qualification
    if dir_score >= 75.0 and tim_score >= 70.0 and is_near_support:
        return SignalState.READY, UserDecision.WAIT

    # Level C: WATCH Qualification
    if dir_score >= 70.0:
        return SignalState.WATCH, UserDecision.WAIT

    # Level D: Sub-threshold baseline
    return SignalState.NO_TRADE, UserDecision.WAIT


# --- Phase 4 XAUUSD Safety Gate & Layer A Candidate Mechanics ---

def evaluate_xauusd_hard_gates(
    runtime_health: Optional[RuntimeFeedHealth],
    profile: Optional[Any] = None,
) -> XauUsdHardGateEvaluation:
    """
    Evaluate independent hard blockers overriding numerical scores for XAUUSD.
    Enforces generic policy-driven feed criticality with fail-safe defaults (not assumed healthy).
    """
    rfh = runtime_health if runtime_health is not None else RuntimeFeedHealth()
    feed_policy = getattr(profile, "feed_policy", None)

    reasons: List[str] = []

    # 1. Check unclosed candle
    if rfh.is_unclosed_candle:
        reasons.append("Analysis candle at evaluation timestamp is unclosed.")

    # 2. Check primary 15m feed criticality
    p15_crit = getattr(feed_policy, "primary_15m", FeedCriticality.CRITICAL) if feed_policy else FeedCriticality.CRITICAL
    if p15_crit == FeedCriticality.CRITICAL:
        if rfh.primary_15m != FeedHealthStatus.HEALTHY:
            reasons.append(f"Critical feed primary_15m is {rfh.primary_15m.value} (must be HEALTHY).")

    # 3. Check macro blackout feed & active blackout state
    macro_crit = getattr(feed_policy, "macro_blackout", FeedCriticality.CRITICAL) if feed_policy else FeedCriticality.CRITICAL
    if macro_crit == FeedCriticality.CRITICAL:
        if rfh.macro_blackout_feed != FeedHealthStatus.HEALTHY:
            reasons.append(f"Critical macro blackout feed is {rfh.macro_blackout_feed.value}.")
        if rfh.is_macro_blackout:
            reasons.append("Active high-impact macroeconomic event blackout window in progress.")

    is_blocked = len(reasons) > 0
    override_state = SignalState.FORCE_WAIT if is_blocked else None

    return XauUsdHardGateEvaluation(
        is_blocked=is_blocked,
        override_state=override_state,
        block_reasons=tuple(reasons),
        runtime_health=rfh,
    )


def evaluate_xauusd_candidate_gate(
    long_direction: SideDirectionScoreResult,
    short_direction: SideDirectionScoreResult,
    long_timing: SideTimingScoreResult,
    short_timing: SideTimingScoreResult,
    hard_gate: XauUsdHardGateEvaluation,
    profile: Optional[Any] = None,
) -> CandidateGateResult:
    """
    Pure Layer A deterministic candidate mechanics for XAUUSD.
    Evaluates Long candidate tier, Short candidate tier, and symmetric conflict matrix.
    """
    if hard_gate.is_blocked:
        reason = "; ".join(hard_gate.block_reasons) if hard_gate.block_reasons else "HARD_GATE_BLOCKED"
        return CandidateGateResult(
            candidate_state=SignalState.FORCE_WAIT,
            candidate_user_decision=UserDecision.WAIT,
            resolution_reason=reason,
            is_candidate_valid=True,
        )

    if (
        profile is None
        or not hasattr(profile, "is_fully_configured")
        or not profile.is_fully_configured
        or not long_direction.is_valid
        or not short_direction.is_valid
        or not long_timing.is_valid
        or not short_timing.is_valid
    ):
        return CandidateGateResult(
            candidate_state=SignalState.NO_TRADE,
            candidate_user_decision=UserDecision.WAIT,
            resolution_reason="PROFILE_OR_SCORES_NOT_CONFIGURED",
            is_candidate_valid=False,
        )

    l_gate = profile.long_gate
    s_gate = profile.short_gate

    l_dir_score = long_direction.total_score or 0.0
    l_tim_score = long_timing.total_score or 0.0
    s_dir_score = short_direction.total_score or 0.0
    s_tim_score = short_timing.total_score or 0.0

    # Step 1: Classify Long Candidate Tier
    if l_dir_score >= l_gate.threshold_window_direction and l_tim_score >= l_gate.threshold_window_timing:
        long_tier = SignalState.BUY_WINDOW
    elif l_dir_score >= l_gate.threshold_ready_direction and l_tim_score >= l_gate.threshold_ready_timing:
        long_tier = SignalState.READY_LONG
    elif l_dir_score >= l_gate.threshold_watch_direction:
        long_tier = SignalState.WATCH_LONG
    else:
        long_tier = SignalState.NO_TRADE

    # Step 2: Classify Short Candidate Tier
    if s_dir_score >= s_gate.threshold_window_direction and s_tim_score >= s_gate.threshold_window_timing:
        short_tier = SignalState.SELL_WINDOW
    elif s_dir_score >= s_gate.threshold_ready_direction and s_tim_score >= s_gate.threshold_ready_timing:
        short_tier = SignalState.READY_SHORT
    elif s_dir_score >= s_gate.threshold_watch_direction:
        short_tier = SignalState.WATCH_SHORT
    else:
        short_tier = SignalState.NO_TRADE

    # Step 3: Symmetric Conflict Resolution Matrix (16 combinations)
    if long_tier == SignalState.BUY_WINDOW:
        if short_tier == SignalState.SELL_WINDOW:
            return CandidateGateResult(SignalState.CONFLICT, UserDecision.WAIT, "SAME_TIER_WINDOW_CONFLICT", True)
        elif short_tier == SignalState.READY_SHORT:
            return CandidateGateResult(SignalState.CONFLICT, UserDecision.WAIT, "WINDOW_VS_READY_CONFLICT", True)
        elif short_tier == SignalState.WATCH_SHORT:
            return CandidateGateResult(SignalState.BUY_WINDOW, UserDecision.BUY, "LONG_WINDOW_OVER_SHORT_WATCH", True)
        else:  # NO_TRADE
            return CandidateGateResult(SignalState.BUY_WINDOW, UserDecision.BUY, "LONG_QUALIFIED", True)

    elif long_tier == SignalState.READY_LONG:
        if short_tier == SignalState.SELL_WINDOW:
            return CandidateGateResult(SignalState.CONFLICT, UserDecision.WAIT, "READY_VS_WINDOW_CONFLICT", True)
        elif short_tier == SignalState.READY_SHORT:
            return CandidateGateResult(SignalState.CONFLICT, UserDecision.WAIT, "SAME_TIER_READY_CONFLICT", True)
        elif short_tier == SignalState.WATCH_SHORT:
            return CandidateGateResult(SignalState.READY_LONG, UserDecision.WAIT, "LONG_READY_OVER_SHORT_WATCH", True)
        else:  # NO_TRADE
            return CandidateGateResult(SignalState.READY_LONG, UserDecision.WAIT, "LONG_SETUP_DEVELOPING", True)

    elif long_tier == SignalState.WATCH_LONG:
        if short_tier == SignalState.SELL_WINDOW:
            return CandidateGateResult(SignalState.SELL_WINDOW, UserDecision.SELL, "SHORT_WINDOW_OVER_LONG_WATCH", True)
        elif short_tier == SignalState.READY_SHORT:
            return CandidateGateResult(SignalState.READY_SHORT, UserDecision.WAIT, "SHORT_READY_OVER_LONG_WATCH", True)
        elif short_tier == SignalState.WATCH_SHORT:
            return CandidateGateResult(SignalState.CONFLICT, UserDecision.WAIT, "SAME_TIER_WATCH_CONFLICT", True)
        else:  # NO_TRADE
            return CandidateGateResult(SignalState.WATCH_LONG, UserDecision.WAIT, "LONG_BIAS_DETECTED", True)

    else:  # long_tier == NO_TRADE
        if short_tier == SignalState.SELL_WINDOW:
            return CandidateGateResult(SignalState.SELL_WINDOW, UserDecision.SELL, "SHORT_QUALIFIED", True)
        elif short_tier == SignalState.READY_SHORT:
            return CandidateGateResult(SignalState.READY_SHORT, UserDecision.WAIT, "SHORT_SETUP_DEVELOPING", True)
        elif short_tier == SignalState.WATCH_SHORT:
            return CandidateGateResult(SignalState.WATCH_SHORT, UserDecision.WAIT, "SHORT_BIAS_DETECTED", True)
        else:  # NO_TRADE
            return CandidateGateResult(SignalState.NO_TRADE, UserDecision.WAIT, "NO_SETUP_ACTIVE", True)

