"""Selective Gate state machine and deterministic user-decision mapping (Phase 4)."""
from typing import List, Optional, Tuple

from engine.core.types import (
    CandleData,
    DirectionScoreResult,
    HardGateEvaluation,
    MacroEventContext,
    RegimeResult,
    RegimeType,
    SignalState,
    StructureResult,
    StructureType,
    TimingScoreResult,
    UserDecision,
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
