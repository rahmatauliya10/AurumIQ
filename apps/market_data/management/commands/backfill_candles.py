"""Management command to backfill historical market candles with strict validation and idempotency."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.instruments.models import Instrument, MarketListing, ListingStatus, ListingRole
from apps.market_data.models import MarketCandle, CandleQualityFlag, VolumeEvidenceType
from apps.market_data.providers.registry import registry
from apps.market_data.normalization import QuoteNormalizer


def parse_iso_or_none(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    cleaned = val.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Command(BaseCommand):
    help = "Backfill historical candlestick data for a specified instrument and timeframes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            type=str,
            default="XAU/USD",
            help="Instrument symbol e.g. XAU/USD (or canonical XAUUSD, XAUT/USDT)",
        )
        parser.add_argument(
            "--timeframes",
            type=str,
            default="15m,1h,4h,1d,5m,1m",
            help="Comma-separated timeframes (e.g. 15m,1h,4h,1d,5m,1m)",
        )
        parser.add_argument("--days", type=int, default=30, help="Days of history to backfill (ignored if --start is provided)")
        parser.add_argument("--start", type=str, default=None, help="Start UTC timestamp (ISO 8601 e.g. 2024-01-01T00:00:00Z)")
        parser.add_argument("--end", type=str, default=None, help="End UTC timestamp (ISO 8601 e.g. 2026-09-01T00:00:00Z)")
        parser.add_argument("--provider", type=str, default=None, help="Explicit provider ID (e.g. xauusd_primary, binance)")

    def handle(self, *args, **options):
        raw_symbol = options["symbol"].strip().upper()
        # Canonical normalization: XAUUSD -> XAU/USD, XAUTUSDT -> XAUT/USDT
        if "/" not in raw_symbol:
            if raw_symbol == "XAUUSD":
                raw_symbol = "XAU/USD"
            elif raw_symbol == "XAUTUSDT":
                raw_symbol = "XAUT/USDT"

        parts = raw_symbol.split("/")
        if len(parts) != 2:
            raise CommandError(f"Invalid symbol '{raw_symbol}'. Use format BASE/QUOTE (e.g. XAU/USD).")

        instrument = Instrument.objects.filter(
            base_asset__code=parts[0], quote_asset__code=parts[1]
        ).first()
        if not instrument:
            raise CommandError(f"Instrument '{raw_symbol}' not found in database. Run seed_instruments first.")

        tfs = [t.strip() for t in options["timeframes"].split(",") if t.strip()]
        days = options["days"]
        chosen_provider = options["provider"]

        # 1. Resolve listing
        qs = MarketListing.objects.filter(instrument=instrument, status=ListingStatus.ACTIVE)
        if chosen_provider:
            listing = qs.filter(provider=chosen_provider.lower()).first()
        elif parts[0] == "XAU" and parts[1] == "USD":
            # Spot Gold must resolve PRIMARY_XAUUSD_SPOT
            listing = qs.filter(listing_role=ListingRole.PRIMARY_XAUUSD_SPOT).first() or qs.order_by("fallback_priority").first()
        else:
            listing = qs.order_by("fallback_priority").first()

        if not listing:
            raise CommandError(f"No active market listing found for {raw_symbol}.")

        # 2. Check provider configuration
        try:
            provider = registry.get(listing.provider)
        except KeyError:
            raise CommandError(f"Provider '{listing.provider}' is not registered in ProviderRegistry.")

        if not provider.is_configured():
            if listing.provider == "xauusd_primary":
                raise CommandError(
                    "PRIMARY_XAUUSD_UNCONFIGURED: Provider 'xauusd_primary' requires environment configuration. "
                    "Please configure XAUUSD_PRIMARY_FEED_URL and optionally XAUUSD_PRIMARY_API_KEY in your environment."
                )
            raise CommandError(f"Market data provider '{listing.provider}' is NOT_CONFIGURED.")

        # 3. Determine time boundaries
        end_time = parse_iso_or_none(options["end"]) or datetime.now(timezone.utc)
        start_time = parse_iso_or_none(options["start"]) or (end_time - timedelta(days=days))

        if start_time >= end_time:
            raise CommandError(f"Start time ({start_time.isoformat()}) must be before end time ({end_time.isoformat()}).")

        # 4. Determine quote normalization
        is_direct_usd = (instrument.quote_asset.code == "USD")
        if not is_direct_usd:
            usdt_rate_provider = registry.get("usdt_usd")
            current_usdt_rate = getattr(usdt_rate_provider, "get_current_rate", lambda: Decimal("1.0"))()
            normalizer = QuoteNormalizer()
        else:
            current_usdt_rate = Decimal("1.000000")
            normalizer = None

        self.stdout.write(
            f"Backfilling {raw_symbol} from {listing.provider.upper()} ({listing.provider_symbol}) "
            f"for timeframes {tfs} from {start_time.isoformat()} to {end_time.isoformat()}..."
        )

        total_saved = 0
        total_quarantined = 0

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

            tf_saved = 0
            with transaction.atomic():
                for raw in candles:
                    # Enforce UTC awareness
                    t_open = raw.timestamp_open
                    if t_open.tzinfo is None:
                        t_open = t_open.replace(tzinfo=timezone.utc)
                    else:
                        t_open = t_open.astimezone(timezone.utc)

                    t_close = raw.timestamp_close
                    if t_close.tzinfo is None:
                        t_close = t_close.replace(tzinfo=timezone.utc)
                    else:
                        t_close = t_close.astimezone(timezone.utc)

                    # Validate OHLC consistency
                    is_valid_ohlc = (
                        raw.open > 0
                        and raw.high > 0
                        and raw.low > 0
                        and raw.close > 0
                        and raw.high >= raw.low
                        and raw.high >= max(raw.open, raw.close)
                        and raw.low <= min(raw.open, raw.close)
                        and t_close > t_open
                    )

                    flag = CandleQualityFlag.OK if is_valid_ohlc else CandleQualityFlag.SUSPECT
                    if not is_valid_ohlc:
                        total_quarantined += 1

                    if is_direct_usd:
                        q_rate = Decimal("1.000000")
                        c_usd = raw.close.quantize(Decimal("0.00000001"))
                    else:
                        norm = normalizer.normalize_price(raw.close, current_usdt_rate)
                        q_rate = norm.rate
                        c_usd = norm.normalized_price

                    vol_ev = getattr(raw, "volume_evidence", VolumeEvidenceType.UNAVAILABLE)
                    if vol_ev not in [c.value for c in VolumeEvidenceType]:
                        vol_ev = VolumeEvidenceType.UNAVAILABLE

                    MarketCandle.objects.update_or_create(
                        instrument=instrument,
                        source=listing.provider,
                        timeframe=tf,
                        timestamp_open=t_open,
                        defaults={
                            "timestamp_close": t_close,
                            "open": raw.open,
                            "high": raw.high,
                            "low": raw.low,
                            "close": raw.close,
                            "volume": raw.volume,
                            "volume_evidence": vol_ev,
                            "quote_rate": q_rate,
                            "close_usd": c_usd,
                            "is_closed": raw.is_closed,
                            "data_quality_flag": flag,
                        },
                    )
                    tf_saved += 1
                    total_saved += 1

            self.stdout.write(f"  - [{tf}] processed {len(candles)} candles (saved/updated: {tf_saved}).")

        msg = f"Backfill complete. Total {total_saved} candles upserted ({total_quarantined} suspect/quarantined)."
        self.stdout.write(self.style.SUCCESS(msg))
