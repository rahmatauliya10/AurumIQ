"""Management command to backfill historical market candles."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.instruments.models import Instrument, MarketListing, ListingStatus
from apps.market_data.models import MarketCandle, CandleQualityFlag
from apps.market_data.providers.registry import registry
from apps.market_data.normalization import QuoteNormalizer


class Command(BaseCommand):
    help = "Backfill historical candlestick data for a specified instrument and timeframes."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="XAUT/USDT", help="Instrument symbol e.g. XAUT/USDT")
        parser.add_argument(
            "--timeframes",
            type=str,
            default="15m,1h,4h,1d",
            help="Comma-separated timeframes (e.g. 15m,1h,4h,1d,1m,5m)",
        )
        parser.add_argument("--days", type=int, default=30, help="Days of history to backfill")
        parser.add_argument("--provider", type=str, default=None, help="Explicit provider (binance, okx, etc.)")

    def handle(self, *args, **options):
        symbol = options["symbol"]
        tfs = [t.strip() for t in options["timeframes"].split(",")]
        days = options["days"]
        chosen_provider = options["provider"]

        parts = symbol.split("/")
        if len(parts) != 2:
            raise CommandError(f"Invalid symbol '{symbol}'. Use format BASE/QUOTE (e.g. XAUT/USDT).")

        instrument = Instrument.objects.filter(
            base_asset__code=parts[0], quote_asset__code=parts[1]
        ).first()
        if not instrument:
            raise CommandError(f"Instrument '{symbol}' not found in database. Run seed_instruments first.")

        # Resolve listing
        qs = MarketListing.objects.filter(instrument=instrument, status=ListingStatus.ACTIVE)
        if chosen_provider:
            listing = qs.filter(provider=chosen_provider.lower()).first()
        else:
            listing = qs.order_by("fallback_priority").first()

        if not listing:
            raise CommandError(f"No active market listing found for {symbol}.")

        provider = registry.get(listing.provider)
        usdt_rate_provider = registry.get("usdt_usd")
        current_usdt_rate = getattr(usdt_rate_provider, "get_current_rate", lambda: Decimal("1.0"))()
        normalizer = QuoteNormalizer()

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)

        self.stdout.write(
            f"Backfilling {symbol} from {listing.provider.upper()} ({listing.provider_symbol}) "
            f"for timeframes {tfs} ({days} days)..."
        )

        total_saved = 0
        for tf in tfs:
            try:
                candles = provider.fetch_candles(
                    symbol=listing.provider_symbol,
                    timeframe=tf,
                    start=start_time,
                    end=end_time,
                )
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Failed to fetch {tf} candles: {e}"))
                continue

            with transaction.atomic():
                for raw in candles:
                    norm = normalizer.normalize_price(raw.close, current_usdt_rate)
                    MarketCandle.objects.update_or_create(
                        instrument=instrument,
                        source=listing.provider,
                        timeframe=tf,
                        timestamp_open=raw.timestamp_open,
                        defaults={
                            "timestamp_close": raw.timestamp_close,
                            "open": raw.open,
                            "high": raw.high,
                            "low": raw.low,
                            "close": raw.close,
                            "volume": raw.volume,
                            "quote_rate": norm.rate,
                            "close_usd": norm.normalized_price,
                            "is_closed": raw.is_closed,
                            "data_quality_flag": CandleQualityFlag.OK,
                        },
                    )
                    total_saved += 1

            self.stdout.write(f"  - [{tf}] processed {len(candles)} candles.")

        self.stdout.write(self.style.SUCCESS(f"Backfill complete. Total {total_saved} candles upserted."))
