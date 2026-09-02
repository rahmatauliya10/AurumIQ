"""
AurumIQ — Controlled Twelve Data Historical Backfill Command
Specialized backward pagination and bulk persistence for XAU/USD.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import time
from typing import List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.instruments.models import Instrument, MarketListing, ListingRole, ListingStatus
from apps.market_data.models import MarketCandle, CandleQualityFlag, VolumeEvidenceType
from apps.market_data.providers.registry import registry
from apps.market_data.providers.base import RawCandle
from apps.market_data.providers.twelve_data import TwelveDataProvider


class Command(BaseCommand):
    help = "Execute controlled historical backward backfill of XAU/USD spot analytical candles from Twelve Data."

    SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            default="2026-06-01T00:00:00Z",
            help="Start UTC timestamp (inclusive, ISO 8601 e.g. 2026-06-01T00:00:00Z)",
        )
        parser.add_argument(
            "--end",
            type=str,
            default="2026-09-01T00:00:00Z",
            help="End UTC timestamp (exclusive, ISO 8601 e.g. 2026-09-01T00:00:00Z)",
        )
        parser.add_argument(
            "--timeframes",
            type=str,
            default="1m,5m,15m,1h,4h,1d",
            help="Comma-separated list of timeframes (e.g. 1m,5m,15m,1h,4h,1d)",
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=4900,
            help="Maximum candles per API page request (1..5000, default: 4900)",
        )
        parser.add_argument(
            "--max-api-requests",
            type=int,
            default=50,
            help="Strict maximum number of historical API requests allowed across the execution (default: 50)",
        )
        parser.add_argument(
            "--pace-seconds",
            type=float,
            default=8.0,
            help="Pacing sleep between consecutive API requests (seconds, default: 8.0)",
        )
        parser.add_argument(
            "--daily-credit-ceiling",
            type=int,
            default=700,
            help="Daily API credit ceiling guard (default: 700, leaving safety reserve from 800/day)",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume from earliest persisted candle if existing data is present",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and validate pages without committing to the database",
        )

    def _log(self, msg: str, style=None):
        out = style(msg) if style else msg
        self.stdout.write(out)
        self.stdout.flush()

    def handle(self, *args, **options):
        start_str = options["start"]
        end_str = options["end"]
        tfs_raw = options["timeframes"]
        page_size = options["page_size"]
        max_requests = options["max_api_requests"]
        pace_seconds = options["pace_seconds"]
        resume = options["resume"]
        dry_run = options["dry_run"]

        self._log("=" * 70)
        self._log("AURUMIQ -- TWELVE DATA CONTROLLED HISTORICAL BACKFILL PILOT")
        self._log("=" * 70)

        # 1. Parse timestamps
        try:
            start_utc = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            end_utc = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception as e:
            raise CommandError(f"INVALID_TIMESTAMP: Failed to parse start/end ISO timestamps: {e}")

        if start_utc >= end_utc:
            raise CommandError(f"INVALID_WINDOW: start ({start_utc.isoformat()}) must be strictly before end ({end_utc.isoformat()}).")

        if page_size <= 0 or page_size > 5000:
            raise CommandError(f"INVALID_PAGE_SIZE: page-size must be between 1 and 5000, got {page_size}.")

        parsed_tfs = []
        raw_list = tfs_raw if isinstance(tfs_raw, list) else str(tfs_raw).split(",")
        for raw_tf in raw_list:
            cleaned = raw_tf.strip().lower()
            if cleaned == "1":
                cleaned = "1d"  # Auto-correct PowerShell decimal literal '1d' evaluated as '1'
            if cleaned:
                parsed_tfs.append(cleaned)

        timeframes = parsed_tfs
        for tf in timeframes:
            if tf not in self.SUPPORTED_TIMEFRAMES:
                raise CommandError(f"UNSUPPORTED_TIMEFRAME: '{tf}'. Supported: {self.SUPPORTED_TIMEFRAMES}")

        # 2. Resolve canonical instrument & authoritative listing
        instrument = Instrument.get_canonical_xauusd()
        if not instrument:
            raise CommandError("CANONICAL_XAUUSD_NOT_FOUND: Canonical XAU/USD instrument does not exist. Run seed_instruments first.")

        primary_listing = MarketListing.objects.filter(
            instrument=instrument,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        ).first()

        if not primary_listing or primary_listing.provider != "twelve_data_xauusd":
            raise CommandError(
                f"INVALID_PRIMARY_LISTING: Authoritative active PRIMARY_XAUUSD_SPOT must be 'twelve_data_xauusd'. "
                f"Found: {primary_listing}."
            )

        # 3. Resolve Provider
        try:
            provider: TwelveDataProvider = registry.get("twelve_data_xauusd")
        except KeyError:
            raise CommandError("PROVIDER_NOT_REGISTERED: 'twelve_data_xauusd' is not in ProviderRegistry.")

        if not provider.is_configured():
            raise CommandError("TWELVE_DATA_NOT_CONFIGURED: Twelve Data API key is not configured in environment/.env.")

        daily_credit_ceiling = options["daily_credit_ceiling"]

        self._log(f"Target Instrument: {instrument.symbol}")
        self._log(f"Authoritative Provider: {primary_listing.provider} ({primary_listing.provider_symbol})")
        self._log(f"Window: {start_utc.isoformat()} -> {end_utc.isoformat()} (Exclusive)")
        self._log(f"Timeframes: {timeframes}")

        # Fail-closed daily credit guard at startup
        daily_usage = 0
        plan_daily_limit = 800
        try:
            usage_info = provider.get_api_usage()
            daily_usage = int(usage_info.get("daily_usage", 0))
            plan_daily_limit = int(usage_info.get("plan_daily_limit", 800))
            self._log(f"Daily API Credit Usage: {daily_usage} / {plan_daily_limit} used (Ceiling: {daily_credit_ceiling})")
        except Exception as e:
            if dry_run:
                self._log(f"  [Dry-Run Notice] Failed to query /api_usage: {e}", style=self.style.WARNING)
            else:
                self._log(f"  [Error] Failed to verify /api_usage credit guard: {e}", style=self.style.ERROR)
                raise CommandError(f"DAILY_CREDIT_GUARD_CHECK_FAILED: {e}") from e

        remaining_daily_credits = max(0, daily_credit_ceiling - daily_usage)
        effective_request_budget = min(max_requests, remaining_daily_credits)

        self._log(f"Configured Invocation Cap: {max_requests} | Remaining Daily Allowance: {remaining_daily_credits}")
        self._log(f"Effective Request Budget: {effective_request_budget} calls max (Pacing: {pace_seconds}s)")
        self._log(f"Dry Run: {dry_run} | Resume Mode: {resume}")
        self._log("-" * 70)

        if effective_request_budget <= 0:
            self._log("\nSTOP CLEANLY: TWELVE_DATA_DAILY_CREDIT_GUARD_REACHED", style=self.style.WARNING)
            self._log(f"Daily credit usage ({daily_usage}) has reached or exceeded the daily credit ceiling ({daily_credit_ceiling}).")
            self._log("No safe API credits remain in today's allowance. Halting before making any historical data requests.")
            return

        total_api_requests = 0
        summary_results = {}
        budget_reached = False
        credit_guard_stopped = False

        # Sort timeframes largest-to-smallest to secure macroscopic anchors first (1d, 4h, 1h, 15m, 5m, 1m)
        tf_order = ["1d", "4h", "1h", "15m", "5m", "1m"]
        sorted_timeframes = sorted(timeframes, key=lambda x: tf_order.index(x) if x in tf_order else 99)

        for tf in sorted_timeframes:
            self._log(f"\n>>> Processing Timeframe: [{tf}]")
            tf_persisted = 0
            current_end = end_utc

            if resume:
                earliest_persisted = MarketCandle.objects.filter(
                    instrument=instrument,
                    source=primary_listing.provider,
                    timeframe=tf,
                    timestamp_open__gte=start_utc,
                    timestamp_open__lt=end_utc,
                ).order_by("timestamp_open").first()

                if earliest_persisted:
                    self._log(f"  [Resume] Existing data found starting at {earliest_persisted.timestamp_open.isoformat()}.")
                    if earliest_persisted.timestamp_open <= start_utc:
                        self._log(f"  [Resume] Timeframe [{tf}] is already fully covered down to {start_utc.isoformat()}. Skipping.")
                        summary_results[tf] = {
                            "status": "ALREADY_COVERED",
                            "new_candles": 0,
                            "earliest": earliest_persisted.timestamp_open.isoformat(),
                            "latest": end_utc.isoformat(),
                        }
                        continue
                    current_end = earliest_persisted.timestamp_open

            page_num = 1
            while current_end > start_utc:
                if total_api_requests >= effective_request_budget:
                    if total_api_requests >= remaining_daily_credits:
                        self._log(f"  [Credit Guard] Reached daily credit ceiling guard ({daily_credit_ceiling}). Halting.", style=self.style.WARNING)
                        credit_guard_stopped = True
                    else:
                        self._log(f"  [Budget] Reached request budget ({effective_request_budget}). Halting.", style=self.style.WARNING)
                    budget_reached = True
                    break

                self._log(f"  Page {page_num}: Fetching up to {page_size} bars ending at {current_end.isoformat()} (Request #{total_api_requests + 1}/{effective_request_budget})...")
                
                raw_candles = None
                for attempt in range(1, 4):
                    try:
                        raw_candles = provider.fetch_historical_page(
                            symbol=primary_listing.provider_symbol,
                            timeframe=tf,
                            end=current_end,
                            outputsize=page_size,
                        )
                        break
                    except Exception as e:
                        err_str = str(e)
                        is_transient = any(t in err_str.lower() for t in ["timed out", "timeout", "prematurely", "connection", "http_failure"])
                        if is_transient and attempt < 3:
                            self._log(f"  [Transient Retry] Attempt {attempt} failed ({e}). Retrying in 10s...", style=self.style.WARNING)
                            time.sleep(10)
                            continue
                        self._log(f"  [Error] Failed to fetch page for [{tf}]: {e}", style=self.style.ERROR)
                        raise CommandError(f"BACKFILL_PAGE_FAILED: {e}") from e

                total_api_requests += 1

                # Filter candles strictly inside [start_utc, current_end)
                valid_candles = [
                    c for c in raw_candles
                    if c.timestamp_open >= start_utc and c.timestamp_open < current_end
                ]

                if not valid_candles:
                    self._log(f"  No older candles returned prior to {current_end.isoformat()}. Reached provider historical boundary.")
                    break

                earliest_in_page = valid_candles[0].timestamp_open
                latest_in_page = valid_candles[-1].timestamp_open

                self._log(f"  Page {page_num} received: {len(valid_candles)} valid candles [{earliest_in_page.isoformat()} -> {latest_in_page.isoformat()}].")

                # Infinite loop guard: earliest returned must be strictly earlier than current_end
                if earliest_in_page >= current_end:
                    self._log(f"  [Loop Guard] Earliest candle {earliest_in_page} >= current_end {current_end}. Breaking.", style=self.style.ERROR)
                    break

                # Atomic persistence
                if not dry_run:
                    saved_count = self._persist_candles(instrument, primary_listing.provider, tf, valid_candles)
                    tf_persisted += saved_count
                else:
                    tf_persisted += len(valid_candles)

                # Monotonic backward step
                current_end = earliest_in_page
                page_num += 1

                if current_end <= start_utc:
                    self._log(f"  Timeframe [{tf}] coverage reached target start boundary ({start_utc.isoformat()}).")
                    break

                # Pacing sleep
                if pace_seconds > 0 and total_api_requests < effective_request_budget:
                    time.sleep(pace_seconds)

            summary_results[tf] = {
                "status": "COMPLETED" if current_end <= start_utc else "PARTIAL",
                "new_candles": tf_persisted,
                "current_boundary": current_end.isoformat(),
            }

            if budget_reached:
                break

        self._log("\n" + "=" * 70)
        self._log("BACKFILL PILOT EXECUTION SUMMARY")
        self._log("=" * 70)
        self._log(f"Total API Requests Executed: {total_api_requests} / {effective_request_budget}")
        for tf, res in summary_results.items():
            self._log(f"  Timeframe [{tf:4s}]: {res['status']} | Saved: {res['new_candles']} candles | Earliest: {res.get('current_boundary', res.get('earliest', 'N/A'))}")

        if dry_run:
            self._log("\nFINAL STATUS: DRY_RUN_COMPLETE", style=self.style.SUCCESS)
        elif credit_guard_stopped:
            self._log("\nFINAL STATUS: TWELVE_DATA_DAILY_CREDIT_GUARD_REACHED", style=self.style.WARNING)
        elif budget_reached:
            self._log("\nFINAL STATUS: BACKFILL_REQUEST_BUDGET_REACHED", style=self.style.WARNING)
        else:
            self._log("\nFINAL STATUS: PILOT_BACKFILL_SUCCESS", style=self.style.SUCCESS)

    def _persist_candles(
        self,
        instrument: Instrument,
        source: str,
        timeframe: str,
        candles: List[RawCandle],
    ) -> int:
        """Atomically persist a batch of candles with deduplication."""
        candle_objs = [
            MarketCandle(
                instrument=instrument,
                source=source,
                timeframe=timeframe,
                timestamp_open=c.timestamp_open,
                timestamp_close=c.timestamp_close,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=Decimal("0"),
                volume_evidence=VolumeEvidenceType.UNAVAILABLE,
                quote_rate=Decimal("1.000000"),
                close_usd=c.close,
                is_closed=True,
                data_quality_flag=CandleQualityFlag.OK,
            )
            for c in candles
        ]

        with transaction.atomic():
            MarketCandle.objects.bulk_create(
                candle_objs,
                batch_size=1000,
                update_conflicts=True,
                unique_fields=["instrument", "source", "timeframe", "timestamp_open"],
                update_fields=[
                    "timestamp_close",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "volume_evidence",
                    "quote_rate",
                    "close_usd",
                    "is_closed",
                    "data_quality_flag",
                ],
            )
        return len(candle_objs)
