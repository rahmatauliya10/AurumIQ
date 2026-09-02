"""
AurumIQ — Controlled Twelve Data Historical Backfill Command
Specialized backward pagination, defensive persistence, and fail-closed quota safety for XAU/USD.
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
from apps.market_data.readiness import parse_strict_iso_datetime


class Command(BaseCommand):
    help = "Execute controlled historical backward backfill of XAU/USD spot analytical candles from Twelve Data."

    SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            default="2026-06-01T00:00:00Z",
            help="Start UTC timestamp (inclusive, strict ISO 8601 with timezone designator e.g. 2026-06-01T00:00:00Z)",
        )
        parser.add_argument(
            "--end",
            type=str,
            default="2026-09-01T00:00:00Z",
            help="End UTC timestamp (exclusive, strict ISO 8601 with timezone designator e.g. 2026-09-01T00:00:00Z)",
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
            help="Daily API credit ceiling guard (default: 700, leaving safety reserve from plan limit)",
        )
        parser.add_argument(
            "--operational-credit-reserve",
            type=int,
            default=10,
            help="Operational credit buffer reserved for telemetry/health checks (default: 10)",
        )
        parser.add_argument(
            "--usage-recheck-every",
            type=int,
            default=25,
            help="Query /api_usage telemetry every N historical HTTP attempts (default: 25)",
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
        daily_credit_ceiling = options["daily_credit_ceiling"]
        operational_reserve = options["operational_credit_reserve"]
        usage_recheck_every = options["usage_recheck_every"]
        resume = options["resume"]
        dry_run = options["dry_run"]

        self._log("=" * 70)
        self._log("AURUMIQ -- TWELVE DATA CONTROLLED HISTORICAL BACKFILL")
        self._log("=" * 70)

        # 1. Parse timestamps strictly (reject naive timestamps, normalize offsets to UTC)
        try:
            start_utc = parse_strict_iso_datetime(start_str)
        except Exception as e:
            raise CommandError(f"INVALID_START_TIMESTAMP: {e}") from e

        try:
            end_utc = parse_strict_iso_datetime(end_str)
        except Exception as e:
            raise CommandError(f"INVALID_END_TIMESTAMP: {e}") from e

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

        self._log(f"Target Instrument: {instrument.symbol}")
        self._log(f"Authoritative Provider: {primary_listing.provider} ({primary_listing.provider_symbol})")
        self._log(f"Window: {start_utc.isoformat()} -> {end_utc.isoformat()} (Exclusive)")
        self._log(f"Timeframes: {timeframes}")

        # 4. Fail-closed daily credit guard at startup (applies to dry-run too)
        try:
            usage_info = provider.get_api_usage()
            daily_usage = int(usage_info["daily_usage"])
            plan_daily_limit = int(usage_info["plan_daily_limit"])
            self._log(f"Daily API Credit Usage: {daily_usage} / {plan_daily_limit} used today")
        except Exception as e:
            self._log(f"  [Error] Failed to verify /api_usage credit guard: {e}", style=self.style.ERROR)
            raise CommandError(f"DAILY_CREDIT_GUARD_CHECK_FAILED: {e}") from e

        safe_ceiling = min(daily_credit_ceiling, plan_daily_limit)
        available_historical_credits = max(0, safe_ceiling - daily_usage - operational_reserve)
        effective_request_budget = min(max_requests, available_historical_credits)

        self._log(f"Safe Daily Ceiling: {safe_ceiling} (Configured: {daily_credit_ceiling}, Plan Limit: {plan_daily_limit})")
        self._log(f"Operational Reserve: {operational_reserve} | Available Historical Credits: {available_historical_credits}")
        self._log(f"Configured Invocation Cap: {max_requests} | Effective Request Budget: {effective_request_budget} calls max (Pacing: {pace_seconds}s)")
        self._log(f"Usage Recheck Cadence: every {usage_recheck_every} attempts")
        self._log(f"Dry Run: {dry_run} | Resume Mode: {resume}")
        self._log("-" * 70)

        if effective_request_budget <= 0:
            self._log("\nSTOP CLEANLY: TWELVE_DATA_DAILY_CREDIT_GUARD_REACHED", style=self.style.WARNING)
            self._log(f"Daily credit usage ({daily_usage}) + reserve ({operational_reserve}) >= safe ceiling ({safe_ceiling}).")
            self._log("No safe API credits remain in today's historical allowance. Halting before making any historical data requests.")
            return

        historical_http_attempts = 0
        logical_pages_completed = 0
        transient_retry_attempts = 0
        summary_results = {}
        budget_reached = False
        credit_guard_stopped = False
        rate_limit_exceeded = False

        # Sort timeframes largest-to-smallest to secure macroscopic anchors first (1d, 4h, 1h, 15m, 5m, 1m)
        tf_order = ["1d", "4h", "1h", "15m", "5m", "1m"]
        sorted_timeframes = sorted(timeframes, key=lambda x: tf_order.index(x) if x in tf_order else 99)

        for tf in sorted_timeframes:
            self._log(f"\n>>> Processing Timeframe: [{tf}]")
            tf_persisted = 0
            current_end = end_utc
            timeframe_status = "IN_PROGRESS"

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
                raw_candles = None
                page_succeeded = False

                for attempt in range(1, 4):
                    # Pre-attempt budget check: every outbound attempt consumes the budget
                    if historical_http_attempts >= effective_request_budget:
                        if historical_http_attempts >= available_historical_credits:
                            self._log(f"  [Credit Guard] Reached available daily credit allowance ({available_historical_credits}). Halting.", style=self.style.WARNING)
                            credit_guard_stopped = True
                        else:
                            self._log(f"  [Budget] Reached request budget ({effective_request_budget}). Halting.", style=self.style.WARNING)
                        budget_reached = True
                        break

                    # Periodic usage re-check
                    if historical_http_attempts > 0 and (historical_http_attempts % usage_recheck_every == 0):
                        self._log(f"  [Telemetry Recheck] Verifying /api_usage after {historical_http_attempts} attempts...")
                        try:
                            recheck_info = provider.get_api_usage()
                            cur_usage = int(recheck_info["daily_usage"])
                            cur_plan_limit = int(recheck_info["plan_daily_limit"])
                            cur_safe_ceiling = min(daily_credit_ceiling, cur_plan_limit)
                            remaining_allowed = max(0, cur_safe_ceiling - cur_usage - operational_reserve)
                            self._log(f"  [Telemetry Recheck] Current Usage: {cur_usage}/{cur_plan_limit}, Remaining Safe Allowance: {remaining_allowed}")
                            if remaining_allowed <= 0:
                                self._log(f"  [Credit Guard] Periodic re-check detected daily usage ({cur_usage}) + reserve ({operational_reserve}) >= safe ceiling ({cur_safe_ceiling}). Halting.", style=self.style.WARNING)
                                credit_guard_stopped = True
                                budget_reached = True
                                break
                        except Exception as e:
                            self._log(f"  [Error] Periodic /api_usage recheck failed: {e}", style=self.style.ERROR)
                            raise CommandError(f"DAILY_CREDIT_GUARD_CHECK_FAILED: {e}") from e

                    # Outbound HTTP attempt accounting BEFORE call
                    historical_http_attempts += 1
                    if attempt > 1:
                        transient_retry_attempts += 1
                        self._log(f"  Page {page_num}: Transient retry attempt {attempt} (Attempt #{historical_http_attempts}/{effective_request_budget})...")
                    else:
                        self._log(f"  Page {page_num}: Fetching up to {page_size} bars ending at {current_end.isoformat()} (Attempt #{historical_http_attempts}/{effective_request_budget})...")

                    try:
                        raw_candles = provider.fetch_historical_page(
                            symbol=primary_listing.provider_symbol,
                            timeframe=tf,
                            end=current_end,
                            outputsize=page_size,
                        )
                        page_succeeded = True
                        logical_pages_completed += 1
                        break
                    except Exception as e:
                        err_str = str(e)
                        # HTTP 429 rate limit is NOT a transient retry: fail closed immediately
                        if "429" in err_str or "RATE_LIMIT" in err_str:
                            self._log(f"  [Rate Limit] HTTP 429 encountered: {e}. Halting immediately without retry.", style=self.style.ERROR)
                            rate_limit_exceeded = True
                            raise CommandError(f"TWELVE_DATA_RATE_LIMIT_EXCEEDED: {e}") from e

                        is_transient = any(t in err_str.lower() for t in ["timed out", "timeout", "prematurely", "connection", "http_failure"])
                        if is_transient and attempt < 3:
                            self._log(f"  [Transient Retry] Attempt {attempt} failed ({e}). Retrying in 10s...", style=self.style.WARNING)
                            time.sleep(10)
                            continue
                        self._log(f"  [Error] Failed to fetch page for [{tf}]: {e}", style=self.style.ERROR)
                        raise CommandError(f"BACKFILL_PAGE_FAILED: {e}") from e

                if budget_reached:
                    timeframe_status = "PARTIAL"
                    break

                if not page_succeeded or raw_candles is None:
                    timeframe_status = "FAILED"
                    break

                # Infinite loop guard: check monotonicity on raw candles returned
                if raw_candles:
                    earliest_returned = raw_candles[0].timestamp_open
                    if earliest_returned >= current_end:
                        self._log(f"  [Loop Guard] Earliest candle {earliest_returned} >= current_end {current_end}. Non-monotonic progress. Breaking.", style=self.style.ERROR)
                        timeframe_status = "LOOP_GUARD_STOPPED"
                        break

                # Filter candles strictly inside [start_utc, current_end)
                valid_candles = [
                    c for c in raw_candles
                    if c.timestamp_open >= start_utc and c.timestamp_open < current_end
                ]

                if not valid_candles:
                    if current_end > start_utc:
                        self._log(
                            f"  [Boundary] No older candles returned prior to {current_end.isoformat()} while requested start is {start_utc.isoformat()}. "
                            f"Reached provider historical boundary before target.",
                            style=self.style.WARNING,
                        )
                        timeframe_status = "PROVIDER_BOUNDARY_BEFORE_TARGET"
                    else:
                        timeframe_status = "COMPLETED"
                    break

                earliest_in_page = valid_candles[0].timestamp_open
                latest_in_page = valid_candles[-1].timestamp_open

                self._log(f"  Page {page_num} received: {len(valid_candles)} valid candles [{earliest_in_page.isoformat()} -> {latest_in_page.isoformat()}].")

                # Atomic persistence with defensive candle validation
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
                    timeframe_status = "COMPLETED"
                    break

                # Pacing sleep
                if pace_seconds > 0 and historical_http_attempts < effective_request_budget:
                    time.sleep(pace_seconds)

            if timeframe_status == "IN_PROGRESS":
                timeframe_status = "COMPLETED" if current_end <= start_utc else "PARTIAL"

            summary_results[tf] = {
                "status": timeframe_status,
                "new_candles": tf_persisted,
                "current_boundary": current_end.isoformat(),
            }

            if budget_reached or rate_limit_exceeded:
                break

        self._log("\n" + "=" * 70)
        self._log("BACKFILL EXECUTION SUMMARY")
        self._log("=" * 70)
        self._log(f"Historical HTTP Attempts: {historical_http_attempts} / {effective_request_budget}")
        self._log(f"Logical Pages Completed: {logical_pages_completed}")
        self._log(f"Transient Retry Attempts: {transient_retry_attempts}")
        for tf, res in summary_results.items():
            self._log(f"  Timeframe [{tf:4s}]: {res['status']} | Saved: {res['new_candles']} candles | Earliest: {res.get('current_boundary', res.get('earliest', 'N/A'))}")

        all_complete = (
            len(summary_results) == len(sorted_timeframes)
            and all(res.get("status") in ("COMPLETED", "ALREADY_COVERED") for res in summary_results.values())
        )

        if rate_limit_exceeded:
            self._log("\nFINAL STATUS: TWELVE_DATA_RATE_LIMIT_EXCEEDED", style=self.style.ERROR)
        elif credit_guard_stopped:
            self._log("\nFINAL STATUS: TWELVE_DATA_DAILY_CREDIT_GUARD_REACHED", style=self.style.WARNING)
        elif budget_reached:
            self._log("\nFINAL STATUS: BACKFILL_REQUEST_BUDGET_REACHED", style=self.style.WARNING)
        elif not all_complete:
            self._log("\nFINAL STATUS: TWELVE_DATA_BACKFILL_PARTIAL", style=self.style.WARNING)
        elif dry_run:
            self._log("\nFINAL STATUS: DRY_RUN_COMPLETE", style=self.style.SUCCESS)
        else:
            self._log("\nFINAL STATUS: TWELVE_DATA_BACKFILL_COMPLETE", style=self.style.SUCCESS)

    def _persist_candles(
        self,
        instrument: Instrument,
        source: str,
        timeframe: str,
        candles: List[RawCandle],
    ) -> int:
        """
        Atomically persist a batch of validated closed candles.

        Strict Defensive Invariants:
        1. source == 'twelve_data_xauusd'
        2. candle.symbol in ('XAUUSD', 'XAU/USD')
        3. candle.timeframe == requested timeframe
        4. candle.is_closed is True
        5. timestamp_open and timestamp_close are timezone-aware UTC
        6. timestamp_close > timestamp_open (Amendment 1)
        7. Positive OHLC prices
        8. Valid OHLC geometry: low <= open <= high and low <= close <= high (bearish candles allowed)
        """
        if source != "twelve_data_xauusd":
            raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Expected source 'twelve_data_xauusd', got '{source}'.")

        candle_objs = []
        for c in candles:
            # 1. Closed state
            if not getattr(c, "is_closed", False):
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Candle at {getattr(c, 'timestamp_open', None)} is not closed.")

            # 2. Timezone awareness
            if c.timestamp_open is None or c.timestamp_open.tzinfo is None or c.timestamp_open.utcoffset() is None:
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Naive or missing timestamp_open: {c.timestamp_open}.")
            if c.timestamp_close is None or c.timestamp_close.tzinfo is None or c.timestamp_close.utcoffset() is None:
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Naive or missing timestamp_close: {c.timestamp_close}.")

            # 3. Timestamp ordering (Amendment 1: timestamp_close > timestamp_open)
            if c.timestamp_close <= c.timestamp_open:
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: timestamp_close ({c.timestamp_close}) <= timestamp_open ({c.timestamp_open}).")

            # 4. Identity validation: symbol & timeframe (Amendment 5)
            raw_symbol = getattr(c, "symbol", "")
            if raw_symbol not in ("XAUUSD", "XAU/USD"):
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Invalid candle symbol '{raw_symbol}', expected XAUUSD.")

            raw_tf = getattr(c, "timeframe", "")
            if raw_tf != timeframe:
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Timeframe mismatch: candle has '{raw_tf}', expected '{timeframe}'.")

            # 5. Positive OHLC prices
            if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Non-positive price at {c.timestamp_open}: O={c.open}, H={c.high}, L={c.low}, C={c.close}.")

            # 6. Valid OHLC geometry: low <= min(open, close) and high >= max(open, close)
            if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high):
                raise CommandError(f"RAW_CANDLE_VALIDATION_FAILED: Invalid OHLC geometry at {c.timestamp_open}: O={c.open}, H={c.high}, L={c.low}, C={c.close}.")

            candle_objs.append(
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
            )

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
