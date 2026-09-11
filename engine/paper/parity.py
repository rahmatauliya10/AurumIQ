"""Live vs Replay Parity Auditor with 3-tier reporting (BUY, SELL, COMBINED)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from engine.paper.guards import assert_paper_execution_safety
from engine.paper.types import (
    DimensionParitySummary,
    LiveReplayParityReport,
    PaperParityItemComparison,
    ParityDimension,
)


class Phase8ParityAuditor:
    """
    Compares live paper observations against point-in-time recomputed decisions.

    Strict Invariants:
      1. Evaluates BUY, SELL, and COMBINED dimensions separately.
      2. Compares: signal side, decision, scores, entry eligibility, friction model version, and outcome.
      3. Zero Mutation: Replay never mutates live observations.
    """

    def __init__(self, max_score_tolerance: float = 1e-4, max_fill_tolerance: Decimal = Decimal("0.05")):
        assert_paper_execution_safety("parity")
        self.max_score_tolerance = max_score_tolerance
        self.max_fill_tolerance = max_fill_tolerance

    def compare_item(
        self,
        observation_id: str,
        side: str,
        decision_timestamp: datetime,
        live_decision: str,
        replay_decision: str,
        live_score: Optional[float],
        replay_score: Optional[float],
        live_entry_eligible: bool,
        replay_entry_eligible: bool,
        live_friction_model_id: str,
        replay_friction_model_id: str,
        live_outcome: str,
        replay_outcome: str,
        live_fill: Optional[Decimal] = None,
        replay_fill: Optional[Decimal] = None,
    ) -> PaperParityItemComparison:
        """Compare a single live observation against its replay counterpart."""
        norm_side = side.upper().strip()

        dec_match = (str(live_decision).upper() == str(replay_decision).upper())
        elig_match = (bool(live_entry_eligible) == bool(replay_entry_eligible))
        fric_match = (str(live_friction_model_id).strip() == str(replay_friction_model_id).strip())
        outc_match = (str(live_outcome).upper() == str(replay_outcome).upper())

        score_disc = 0.0
        if live_score is not None and replay_score is not None:
            score_disc = abs(float(live_score) - float(replay_score))
        elif live_score != replay_score:
            score_disc = 1.0

        fill_diff: Optional[Decimal] = None
        if live_fill is not None and replay_fill is not None:
            fill_diff = abs(live_fill - replay_fill)

        return PaperParityItemComparison(
            observation_id=observation_id,
            side=norm_side,
            decision_timestamp=decision_timestamp,
            live_decision=live_decision,
            replay_decision=replay_decision,
            decision_match=dec_match,
            live_score=live_score,
            replay_score=replay_score,
            score_discrepancy=score_disc,
            live_entry_eligible=live_entry_eligible,
            replay_entry_eligible=replay_entry_eligible,
            eligibility_match=elig_match,
            live_friction_model_id=live_friction_model_id,
            replay_friction_model_id=replay_friction_model_id,
            friction_model_match=fric_match,
            live_outcome=live_outcome,
            replay_outcome=replay_outcome,
            outcome_match=outc_match,
            fill_difference=fill_diff,
        )

    def summarize_dimension(
        self,
        dimension: ParityDimension,
        items: Sequence[PaperParityItemComparison],
    ) -> DimensionParitySummary:
        """Compute parity summary metrics for a given subset of comparison items."""
        total = len(items)
        if total == 0:
            return DimensionParitySummary(
                dimension=dimension,
                total_evaluated=0,
                decision_matches=0,
                decision_match_rate=1.0,
                score_matches=0,
                score_match_rate=1.0,
                eligibility_matches=0,
                eligibility_match_rate=1.0,
                friction_model_matches=0,
                friction_model_match_rate=1.0,
                outcome_matches=0,
                outcome_match_rate=1.0,
                max_fill_difference=Decimal("0"),
                is_parity_passed=True,
            )

        dec_matches = sum(1 for x in items if x.decision_match)
        score_matches = sum(1 for x in items if x.score_discrepancy <= self.max_score_tolerance)
        elig_matches = sum(1 for x in items if x.eligibility_match)
        fric_matches = sum(1 for x in items if x.friction_model_match)
        outc_matches = sum(1 for x in items if x.outcome_match)

        max_fill = Decimal("0")
        for x in items:
            if x.fill_difference is not None and x.fill_difference > max_fill:
                max_fill = x.fill_difference

        dec_rate = dec_matches / total
        score_rate = score_matches / total
        elig_rate = elig_matches / total
        fric_rate = fric_matches / total
        outc_rate = outc_matches / total

        passed = (
            dec_rate >= 1.0
            and score_rate >= 1.0
            and elig_rate >= 1.0
            and fric_rate >= 1.0
            and outc_rate >= 1.0
            and max_fill <= self.max_fill_tolerance
        )

        return DimensionParitySummary(
            dimension=dimension,
            total_evaluated=total,
            decision_matches=dec_matches,
            decision_match_rate=dec_rate,
            score_matches=score_matches,
            score_match_rate=score_rate,
            eligibility_matches=elig_matches,
            eligibility_match_rate=elig_rate,
            friction_model_matches=fric_matches,
            friction_model_match_rate=fric_rate,
            outcome_matches=outc_matches,
            outcome_match_rate=outc_rate,
            max_fill_difference=max_fill,
            is_parity_passed=passed,
        )

    def audit_parity(
        self,
        comparisons: Sequence[PaperParityItemComparison],
    ) -> LiveReplayParityReport:
        """Generate consolidated 3-tier parity audit report."""
        buy_items = [x for x in comparisons if x.side.upper() == "BUY"]
        sell_items = [x for x in comparisons if x.side.upper() == "SELL"]

        buy_summary = self.summarize_dimension(ParityDimension.BUY, buy_items)
        sell_summary = self.summarize_dimension(ParityDimension.SELL, sell_items)
        comb_summary = self.summarize_dimension(ParityDimension.COMBINED, comparisons)

        overall = "PASS" if (buy_summary.is_parity_passed and sell_summary.is_parity_passed and comb_summary.is_parity_passed) else "FAIL"
        if len(comparisons) == 0:
            overall = "PENDING"

        return LiveReplayParityReport(
            buy_parity=buy_summary,
            sell_parity=sell_summary,
            combined_parity=comb_summary,
            overall_status=overall,
            generated_at=datetime.now(timezone.utc),
        )
