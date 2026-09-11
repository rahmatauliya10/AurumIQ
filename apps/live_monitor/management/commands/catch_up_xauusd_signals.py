"""Management command to catch up unprocessed closed 15m candles into SignalRecords (Spec §Stage 1)."""
from django.core.management.base import BaseCommand
from apps.live_monitor.recovery import catch_up_unprocessed_xauusd_candles


class Command(BaseCommand):
    help = "Explicit auditable recovery of closed 15m XAUUSD candles lacking a SignalRecord."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of historical candles to catch up (default: 50).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show candles that need recovery without dispatching tasks.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]

        self.stdout.write(f"Starting XAUUSD signal catch-up (limit={limit}, dry_run={dry_run})...")
        res = catch_up_unprocessed_xauusd_candles(limit=limit, dry_run=dry_run)

        if res["status"] == "error":
            self.stderr.write(self.style.ERROR(f"Catch-up failed: {res['message']}"))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Catch-up completed: {res['recovered_count']} candles identified. "
                f"Status: {res['status']}"
            )
        )
        for ts in res.get("recovered_candles", []):
            self.stdout.write(f"  - {ts}")
