"""Management command to display Phase 8 operational status and telemetry."""
from datetime import datetime, timezone
from django.core.management.base import BaseCommand

from apps.live_monitor.models import PaperObservationRecord, Phase8OperationalState
from apps.live_monitor.paper_service import Phase8PaperService
from engine.paper.guards import assert_paper_execution_safety


class Command(BaseCommand):
    help = "Display Phase 8 operational status, continuity counters, and metrics."

    def handle(self, *args, **options):
        assert_paper_execution_safety("command:phase8_status")

        state = Phase8PaperService.get_or_create_operational_state()
        now_utc = datetime.now(timezone.utc)

        age_seconds = 0.0
        if state.observation_window_start:
            age_seconds = (now_utc - state.observation_window_start).total_seconds()
            age_days = age_seconds / 86400.0
        else:
            age_days = 0.0

        # Outcome counters from records
        tp1_n = PaperObservationRecord.objects.filter(outcome="TP1_FIRST").count()
        sl_n = PaperObservationRecord.objects.filter(outcome="SL_FIRST").count()
        timeout_n = PaperObservationRecord.objects.filter(outcome="TIMEOUT").count()
        unres_n = PaperObservationRecord.objects.filter(outcome="UNRESOLVED").count()
        skipped_n = PaperObservationRecord.objects.filter(outcome="SKIPPED").count()

        self.stdout.write("============================================================")
        self.stdout.write("AURUMIQ PHASE 8 -- OPERATIONAL OBSERVATION STATUS")
        self.stdout.write("============================================================")
        self.stdout.write(f"PHASE8_STATUS:                          {state.status}")
        self.stdout.write(f"OBSERVATION_DAY:                       {state.observation_day} / 14")
        self.stdout.write(f"COMPLETED_CALENDAR_DAYS:               {state.completed_calendar_days}")
        self.stdout.write(f"OBSERVATION_WINDOW_START:              {state.observation_window_start.isoformat() if state.observation_window_start else 'NOT_STARTED'}")
        self.stdout.write(f"OBSERVATION_WINDOW_AGE:                {age_days:.2f} days ({age_seconds:.0f} seconds)")
        self.stdout.write(f"LAST_SUCCESSFUL_EVALUATION:            {state.last_successful_evaluation_timestamp.isoformat() if state.last_successful_evaluation_timestamp else 'None'}")
        self.stdout.write("------------------------------------------------------------")
        self.stdout.write(f"SIGNALS_EVALUATED:                     {state.total_signals_evaluated}")
        self.stdout.write(f"BUY_COUNT:                             {state.buy_count}")
        self.stdout.write(f"SELL_COUNT:                            {state.sell_count}")
        self.stdout.write(f"NO_TRADE_COUNT:                        {state.no_trade_count}")
        self.stdout.write("------------------------------------------------------------")
        self.stdout.write(f"PAPER_POSITIONS_OPENED:                {state.paper_positions_opened}")
        self.stdout.write(f"PAPER_POSITIONS_CLOSED:                {state.paper_positions_closed}")
        self.stdout.write(f"OUTCOMES_PENDING:                      {state.outcomes_pending}")
        self.stdout.write(f"OUTCOMES_RESOLVED:                     {state.outcomes_resolved}")
        self.stdout.write(f"  - TP1_FIRST:                         {tp1_n}")
        self.stdout.write(f"  - SL_FIRST:                          {sl_n}")
        self.stdout.write(f"  - TIMEOUT:                           {timeout_n}")
        self.stdout.write(f"  - UNRESOLVED:                        {unres_n}")
        self.stdout.write(f"  - SKIPPED / NO_PLAN:                 {skipped_n}")
        self.stdout.write("------------------------------------------------------------")
        self.stdout.write(f"DATA_FRESHNESS:                        {state.data_freshness_status}")
        self.stdout.write(f"RUNTIME_ERRORS:                        {state.runtime_errors_count}")
        self.stdout.write(f"UNRESOLVED_INTEGRITY_FAILURES:         {state.unresolved_integrity_failures}")
        self.stdout.write(f"LIVE_REPLAY_PARITY_STATUS:             {state.live_replay_parity_status}")
        self.stdout.write("------------------------------------------------------------")
        self.stdout.write(f"PAPER_ONLY:                            {state.paper_only}")
        self.stdout.write(f"REAL_ORDER_EXECUTION:                  {state.real_order_execution}")
        self.stdout.write("PROFITABILITY_PROOF:                   NOT_CLAIMED")
        self.stdout.write("LIVE_MONEY_AUTHORIZATION:              NOT_GRANTED")
        self.stdout.write("============================================================")
