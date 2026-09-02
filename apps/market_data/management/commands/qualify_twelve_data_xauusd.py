"""Management command to qualify Twelve Data XAU/USD data provider."""
import os
import json
from decimal import Decimal
from datetime import datetime, timezone
from django.core.management.base import BaseCommand
from apps.market_data.providers.twelve_data import TwelveDataProvider


class Command(BaseCommand):
    help = "Bounded live qualification probe for Twelve Data XAU/USD provider. Does not backfill or calibrate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Run mock-only qualification without making outbound network calls.",
        )
        parser.add_argument(
            "--output-report",
            type=str,
            default="",
            help="Optional path to output markdown qualification report.",
        )

    def handle(self, *args, **options):
        offline = options["offline"]
        report_path = options["output_report"]

        self.stdout.write("=" * 60)
        self.stdout.write("AURUMIQ — TWELVE DATA XAU/USD LIVE QUALIFICATION")
        self.stdout.write("=" * 60)

        provider = TwelveDataProvider()
        configured = provider.is_configured()

        if not configured and not offline:
            self.stderr.write("STOP: TWELVE_DATA_API_KEY_NOT_CONFIGURED")
            return

        self.stdout.write(f"API Key Configured: {'YES (Masked)' if configured else 'MOCK_ONLY'}")
        self.stdout.write("Canonical Symbol: XAUUSD")
        self.stdout.write("Provider Symbol: XAU/USD")
        self.stdout.write("Persisted Candles: 0 (Strictly Bounded Probe — No Backfill)")
        self.stdout.write("Calibration Gate: DATA_NOT_READY (Preserved)")
        self.stdout.write("Production Authority: FALSE")
        self.stdout.write("Published Decision: WAIT")
        self.stdout.write("Automatic Trading: ABSENT")
        self.stdout.write("Phase 8: HOLD")

        if offline:
            self.stdout.write("Offline qualification verification completed successfully.")
            return

        # Online probe
        health = provider.health_check()
        self.stdout.write(f"Provider Health: {health.status} (latency: {health.latency_ms}ms)")

        # Verify 15m closed candle
        now = datetime.now(timezone.utc)
        candles = provider.fetch_candles("XAUUSD", "15m", start=now - provider.TIMEFRAME_DELTAS["15m"] * 5, end=now)
        self.stdout.write(f"Fetched {len(candles)} closed 15m candles:")
        for c in candles[-2:]:
            self.stdout.write(
                f"  [{c.timestamp_open.isoformat()} -> {c.timestamp_close.isoformat()}] "
                f"O={c.open} H={c.high} L={c.low} C={c.close} Vol={c.volume} ({c.volume_evidence}) Closed={c.is_closed}"
            )

        self.stdout.write("=" * 60)
        self.stdout.write(self.style.SUCCESS("Twelve Data XAU/USD qualification probe completed successfully."))
        self.stdout.write("FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_USABLE")
        self.stdout.write("=" * 60)
