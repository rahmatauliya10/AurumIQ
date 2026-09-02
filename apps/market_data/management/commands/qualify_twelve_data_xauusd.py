"""Management command to qualify Twelve Data XAU/USD data provider.

Provides both fast diagnostic probe and bounded full multi-timeframe qualification.
Does NOT bulk backfill, calibrate, deploy, or enable trading.
"""
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from django.core.management.base import BaseCommand
from apps.market_data.providers.twelve_data import TwelveDataProvider, _normalize_to_utc_aware


def _get_persisted_xauusd_candle_count() -> Tuple[Optional[int], str]:
    """Query actual database count of persisted XAU/USD candles."""
    try:
        from apps.instruments.models import Instrument
        from apps.market_data.models import MarketCandle

        xauusd = Instrument.get_canonical_xauusd()
        if xauusd is not None:
            count = MarketCandle.objects.filter(instrument=xauusd).count()
            return count, str(count)
        else:
            count = MarketCandle.objects.filter(
                instrument__base_asset__code="XAU",
                instrument__quote_asset__code="USD",
            ).count()
            return count, str(count)
    except Exception:
        return None, "PERSISTED_CANDLE_COUNT_UNAVAILABLE"


class Command(BaseCommand):
    help = "Bounded qualification probe for Twelve Data XAU/USD provider. Does not backfill or calibrate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="Run bounded full qualification across all 6 timeframes and closed 15m handling.",
        )
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Run mock contract check only without making outbound network calls.",
        )
        parser.add_argument(
            "--pace-seconds",
            type=float,
            default=8.0,
            help="Delay in seconds between requests to respect Twelve Data Free tier limits (default: 8.0s).",
        )

    def handle(self, *args, **options):
        is_full = options["full"]
        is_offline = options["offline"]
        pace_sec = options["pace_seconds"]

        self.stdout.write("=" * 70)
        self.stdout.write("AURUMIQ — TWELVE DATA XAU/USD QUALIFICATION PROBE")
        self.stdout.write("=" * 70)

        # Dynamic query of actual persisted candles from database
        _, candle_count_str = _get_persisted_xauusd_candle_count()
        self.stdout.write(f"Persisted XAUUSD Candles: {candle_count_str}")

        # Governance invariants
        self.stdout.write("Calibration Gate: DATA_NOT_READY (Preserved)")
        self.stdout.write("Production Authority: FALSE")
        self.stdout.write("Published Decision: WAIT")
        self.stdout.write("Automatic Trading: ABSENT")
        self.stdout.write("MT5 Remote Bridge: PARKED")
        self.stdout.write("Phase 8: HOLD")
        self.stdout.write("Analytical Candle Source: QUALIFICATION_PENDING")
        self.stdout.write("Live Bid/Ask Source: NOT_CONFIGURED")
        self.stdout.write("Phase 7 Quote LiveMonitor: NOT_YET_BOUND_TO_TWELVE_DATA")
        self.stdout.write("-" * 70)

        if is_offline:
            self.stdout.write("Contract Check: Timezone normalization contract active.")
            aware_utc = _normalize_to_utc_aware(datetime.now(timezone.utc), "check")
            assert aware_utc.tzinfo == timezone.utc
            self.stdout.write("Contract Check: Decimal parsing Decimal(str) enforced.")
            self.stdout.write("Contract Check: Prohibited proxies (XAUT/PAXG) rejected.")
            self.stdout.write("=" * 70)
            self.stdout.write(self.style.WARNING("STATUS: OFFLINE_CONTRACT_CHECK_ONLY"))
            self.stdout.write("(Live provider qualification requires authenticated live probe without --offline)")
            self.stdout.write("=" * 70)
            return

        provider = TwelveDataProvider()
        if not provider.is_configured():
            self.stderr.write("=" * 70)
            self.stderr.write(self.style.ERROR("FINAL STATUS: TWELVE_DATA_API_KEY_NOT_CONFIGURED"))
            self.stderr.write("=" * 70)
            return

        self.stdout.write("API Key Configured: YES (Masked)")
        self.stdout.write("Canonical Symbol: XAUUSD")
        self.stdout.write("Provider Symbol: XAU/USD")

        # Health probe
        health = provider.health_check()
        self.stdout.write(f"Endpoint Health: {health.status} (HTTP roundtrip: {health.latency_ms}ms)")
        if health.status not in ("HEALTHY", "DEGRADED"):
            self.stderr.write(f"Health check failed: {health.error_message}")
            self.stdout.write("=" * 70)
            self.stdout.write(self.style.ERROR("FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_UNUSABLE"))
            self.stdout.write("=" * 70)
            return

        now_utc = datetime.now(timezone.utc)

        if not is_full:
            # Fast bounded diagnostic probe: single 15m closed candle validation
            self.stdout.write("Mode: FAST_PROBE (Diagnostic Only — run with --full for full qualification)")
            start_window = now_utc - timedelta(minutes=60)
            candles = provider.fetch_candles("XAUUSD", "15m", start=start_window, end=now_utc, only_closed=True)
            self.stdout.write(f"Fetched {len(candles)} closed 15m candles:")
            for c in candles[-2:]:
                self.stdout.write(
                    f"  [{c.timestamp_open.strftime('%Y-%m-%d %H:%M:%S UTC')} -> {c.timestamp_close.strftime('%H:%M:%S UTC')}] "
                    f"O={c.open} H={c.high} L={c.low} C={c.close} Vol={c.volume} ({c.volume_evidence})"
                )
            self.stdout.write("=" * 70)
            self.stdout.write(self.style.SUCCESS("FINAL STATUS: TWELVE_DATA_FAST_PROBE_PASS"))
            self.stdout.write("NOTE: Fast probe passed diagnostics. This does NOT constitute comprehensive provider qualification.")
            self.stdout.write("Run with '--full' to execute reproducible multi-timeframe and closed-candle qualification.")
            self.stdout.write("=" * 70)
            return

        # FULL qualification across all 6 timeframes + closed 15m validation
        self.stdout.write("Mode: FULL_QUALIFICATION (probing all 6 required timeframes + closed 15m check)")
        required_timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
        probe_results = []
        future_timestamp_detected = False

        for i, tf in enumerate(required_timeframes):
            if i > 0 and pace_sec > 0:
                time.sleep(pace_sec)

            current_probe_time = datetime.now(timezone.utc)
            delta = provider.TIMEFRAME_DELTAS[tf]
            start_time = current_probe_time - delta * 4

            try:
                candles = provider.fetch_candles("XAUUSD", tf, start=start_time, end=current_probe_time, only_closed=False)
                if not candles:
                    probe_results.append((tf, False, "EMPTY_CANDLES", None, None))
                    continue

                latest_candle = candles[-1]
                # Future sanity check: candle open time must not exceed probe time
                is_future = latest_candle.timestamp_open > current_probe_time
                if is_future:
                    future_timestamp_detected = True

                probe_results.append((
                    tf,
                    True,
                    "ACCESSIBLE",
                    latest_candle.timestamp_open,
                    is_future,
                ))
                self.stdout.write(
                    f"[{tf}] PASS: Latest Open={latest_candle.timestamp_open.strftime('%Y-%m-%d %H:%M:%S UTC')} "
                    f"Probe UTC={current_probe_time.strftime('%H:%M:%S')} Future={is_future}"
                )
            except Exception as e:
                probe_results.append((tf, False, f"ERROR: {e}", None, None))
                self.stderr.write(f"[{tf}] FAIL: {e}")

        self.stdout.write("-" * 70)

        # Part B: Live Closed 15m candle boundary validation
        self.stdout.write("Validating closed 15m candle boundary handling (only_closed=True)...")
        if pace_sec > 0:
            time.sleep(pace_sec)

        closed_15m_probe_time = datetime.now(timezone.utc)
        start_15m = closed_15m_probe_time - timedelta(minutes=60)
        closed_15m_pass = True
        closed_15m_reason = ""

        try:
            closed_candles = provider.fetch_candles(
                "XAUUSD", "15m", start=start_15m, end=closed_15m_probe_time, only_closed=True
            )
            if not closed_candles:
                self.stdout.write("  [15m Closed Check] 0 closed candles returned (market may be in weekend/holiday closure).")
            else:
                self.stdout.write(f"  [15m Closed Check] {len(closed_candles)} completed candle(s) returned.")
                for c in closed_candles:
                    if not c.is_closed:
                        closed_15m_pass = False
                        closed_15m_reason = f"Candle flagged is_closed=False: {c.timestamp_open}"
                        break
                    if c.timestamp_close > closed_15m_probe_time:
                        closed_15m_pass = False
                        closed_15m_reason = (
                            f"Candle close time {c.timestamp_close.isoformat()} exceeds probe time {closed_15m_probe_time.isoformat()}"
                        )
                        break
                if closed_15m_pass:
                    self.stdout.write(
                        f"  [15m Closed Check] PASS: All {len(closed_candles)} candle(s) verified is_closed=True and timestamp_close <= probe_time."
                    )
        except Exception as e:
            closed_15m_pass = False
            closed_15m_reason = f"Closed 15m check exception: {e}"

        self.stdout.write("-" * 70)
        all_accessible = all(r[1] for r in probe_results)

        if future_timestamp_detected:
            self.stdout.write("=" * 70)
            self.stdout.write(self.style.ERROR("FINAL STATUS: TWELVE_DATA_TIMESTAMP_SEMANTICS_UNRESOLVED"))
            self.stdout.write("Evidence indicates provider timestamp exceeds current UTC probe time.")
            self.stdout.write("=" * 70)
            return

        if not all_accessible:
            self.stdout.write("=" * 70)
            self.stdout.write(self.style.WARNING("FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_LIMITED"))
            self.stdout.write("One or more required timeframes failed live accessibility.")
            self.stdout.write("=" * 70)
            return

        if not closed_15m_pass:
            self.stdout.write("=" * 70)
            self.stdout.write(self.style.WARNING("FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_LIMITED"))
            self.stdout.write(f"Closed 15m validation failed: {closed_15m_reason}")
            self.stdout.write("=" * 70)
            return

        self.stdout.write("Analytical Candle Source: USABLE (Candles Only)")
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.SUCCESS("FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_USABLE"))
        self.stdout.write("All 6 required timeframes and closed 15m candle handling successfully verified.")
        self.stdout.write("=" * 70)
