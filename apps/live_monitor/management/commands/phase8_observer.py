"""Management command to execute Phase 8 Live Paper Observation cycles."""
from decimal import Decimal
from django.core.management.base import BaseCommand

from apps.live_monitor.paper_service import Phase8PaperService
from engine.paper.guards import assert_paper_execution_safety


class Command(BaseCommand):
    help = "Execute Phase 8 live paper observation cycle (PAPER EXECUTION ONLY, NO REAL ORDERS)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--step",
            action="store_true",
            help="Execute a single observation step against the latest closed candle.",
        )
        parser.add_argument(
            "--paper-volume-lots",
            type=float,
            default=None,
            help="Optional explicit paper volume lots (monetary PnL remains null/NOT_EVALUATED if omitted).",
        )

    def handle(self, *args, **options):
        assert_paper_execution_safety("command:phase8_observer")

        self.stdout.write("============================================================")
        self.stdout.write("AURUMIQ PHASE 8 -- LIVE PAPER OBSERVATION RUNNER")
        self.stdout.write("PAPER_ONLY = true | REAL_ORDER_EXECUTION = disabled")
        self.stdout.write("============================================================")

        paper_volume = Decimal(str(options["paper_volume_lots"])) if options.get("paper_volume_lots") is not None else None

        count, status_str = Phase8PaperService.step_observation_cycle()
        state = Phase8PaperService.get_or_create_operational_state()

        self.stdout.write(f"Observation cycle completed: {status_str} (processed: {count})")
        self.stdout.write(f"PHASE8_STATUS = {state.status}")
        self.stdout.write(f"OBSERVATION_DAY = {state.observation_day} / 14")
        self.stdout.write(f"OBSERVATION_WINDOW_START = {state.observation_window_start.isoformat() if state.observation_window_start else 'None'}")
        self.stdout.write(f"TOTAL_SIGNALS_EVALUATED = {state.total_signals_evaluated}")
        self.stdout.write(f"BUY_COUNT = {state.buy_count} | SELL_COUNT = {state.sell_count}")
        self.stdout.write(f"PAPER_POSITIONS_OPENED = {state.paper_positions_opened} | CLOSED = {state.paper_positions_closed}")
        self.stdout.write("REAL_ORDER_EXECUTION = disabled")
