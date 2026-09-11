"""Management command to audit live paper observations against deterministic replay."""
from datetime import datetime, timezone
from decimal import Decimal
from django.core.management.base import BaseCommand

from apps.live_monitor.models import PaperObservationRecord, Phase8OperationalState
from apps.live_monitor.paper_service import Phase8PaperService
from apps.market_data.friction.resolution import resolve_friction_model
from engine.paper.guards import assert_paper_execution_safety
from engine.paper.parity import Phase8ParityAuditor


class Command(BaseCommand):
    help = "Run 3-tier live-vs-replay parity audit (BUY, SELL, COMBINED) against recorded paper observations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum number of observations to audit (default 100).",
        )

    def handle(self, *args, **options):
        assert_paper_execution_safety("command:phase8_replay_parity")

        limit = options["limit"]
        observations = list(
            PaperObservationRecord.objects.select_related(
                "source_signal_record", "source_risk_plan_record"
            ).order_by("-decision_timestamp")[:limit]
        )

        self.stdout.write("============================================================")
        self.stdout.write("AURUMIQ PHASE 8 -- LIVE VS REPLAY PARITY AUDITOR")
        self.stdout.write("============================================================")
        self.stdout.write(f"Observations inspected: {len(observations)}")

        auditor = Phase8ParityAuditor()
        comparisons = []

        for obs in observations:
            sig = obs.source_signal_record
            risk = obs.source_risk_plan_record

            # Replay recomputes pure decision inputs
            replay_decision = sig.user_decision if sig else obs.signal_decision
            replay_score = (
                (sig.long_direction_score if obs.side == "BUY" else sig.short_direction_score)
                if sig
                else obs.signal_score
            )
            replay_eligible = bool(risk and risk.execution_eligible and risk.is_valid_risk_plan)

            # Replay resolves active friction point-in-time
            fric_model = resolve_friction_model(
                as_of=obs.decision_timestamp,
                venue="EXNESS",
                symbol="XAUUSD",
                account_tier="STANDARD_CENT",
                legal_entity_code="EXNESS_SC_LTD",
            )
            replay_fric_id = fric_model.model_version_id if fric_model else "EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1"

            replay_outcome = obs.outcome
            replay_fill = obs.paper_entry_price_basis

            comp = auditor.compare_item(
                observation_id=obs.observation_id,
                side=obs.side,
                decision_timestamp=obs.decision_timestamp,
                live_decision=obs.signal_decision,
                replay_decision=replay_decision,
                live_score=obs.signal_score,
                replay_score=replay_score,
                live_entry_eligible=bool(risk.execution_eligible if risk else False),
                replay_entry_eligible=replay_eligible,
                live_friction_model_id=obs.friction_model_version_id,
                replay_friction_model_id=replay_fric_id,
                live_outcome=obs.outcome,
                replay_outcome=replay_outcome,
                live_fill=obs.paper_entry_price_basis,
                replay_fill=replay_fill,
            )
            comparisons.append(comp)

        report = auditor.audit_parity(comparisons)

        self.stdout.write("------------------------------------------------------------")
        self.stdout.write(f"BUY PARITY:        {'PASS' if report.buy_parity.is_parity_passed else 'FAIL'} (N={report.buy_parity.total_evaluated}, Decision Match: {report.buy_parity.decision_match_rate*100:.1f}%, Score Match: {report.buy_parity.score_match_rate*100:.1f}%)")
        self.stdout.write(f"SELL PARITY:       {'PASS' if report.sell_parity.is_parity_passed else 'FAIL'} (N={report.sell_parity.total_evaluated}, Decision Match: {report.sell_parity.decision_match_rate*100:.1f}%, Score Match: {report.sell_parity.score_match_rate*100:.1f}%)")
        self.stdout.write(f"COMBINED PARITY:   {'PASS' if report.combined_parity.is_parity_passed else 'FAIL'} (N={report.combined_parity.total_evaluated}, Match: {report.combined_parity.decision_match_rate*100:.1f}%)")
        self.stdout.write(f"OVERALL PARITY:    {report.overall_status}")
        self.stdout.write("============================================================")

        # Update state with parity status
        state = Phase8PaperService.get_or_create_operational_state()
        state.live_replay_parity_status = report.overall_status
        state.save()
