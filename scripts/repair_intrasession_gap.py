"""Bounded 52-bar Intrasession Data Repair Script (Twelve Data XAU/USD 15m).

Repairs the confirmed 52-bar intrasession gap:
  Gap start (prev close): 2026-09-14 03:00:00 UTC
  Gap end (next open):   2026-09-14 16:00:00 UTC
  Missing intervals:     52 bars (15m each)

Strict Guarantees:
  1. No outputsize parameter (uses explicit start_date and end_date per Twelve Data docs).
  2. Pre-write exact set equality verification (EXPECTED_COUNT == 52, PROVIDER_RETURNED == 52).
  3. No synthetic candles, no interpolation.
  4. Idempotent update_or_create.
  5. Post-write verification: MISSING_EXPECTED_TIMESTAMPS == 0, UNRESOLVED_INTRASESSION_GAPS == 0.
  6. Direct validation of primary_15m == HEALTHY in XauUsdLiveDecisionPipelineService.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import django

# Setup Django environment
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from apps.instruments.models import Instrument, InstrumentRole, InstrumentType, ListingRole, MarketListing, ProviderHealthSnapshot
from apps.market_data.models import MarketCandle, VolumeEvidenceType
from apps.market_data.providers.twelve_data import TwelveDataProvider
from apps.dashboard.api import classify_candle_gap
from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
from engine.core.types import FeedHealthStatus


def main():
    print("=" * 70)
    print("AURUMIQ: BOUNDED 52-BAR INTRASESSION DATA REPAIR")
    print("=" * 70)

    # 1. Resolve Instrument and Primary Listing
    inst = Instrument.objects.filter(
        base_asset__code="XAU",
        quote_asset__code="USD",
        instrument_type=InstrumentType.SPOT,
    ).first()
    if not inst:
        raise RuntimeError("FATAL: Canonical XAU/USD instrument not found in database.")

    prim_listing = MarketListing.objects.filter(
        instrument=inst,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
    ).first()
    if not prim_listing:
        raise RuntimeError("FATAL: Primary XAU/USD listing not found in database.")

    # 2. Construct Expected Missing 52 Timestamps Set
    gap_start = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
    expected_opens = {
        gap_start + timedelta(minutes=15 * i) for i in range(52)
    }
    expected_closes = {
        gap_start + timedelta(minutes=15 * (i + 1)) for i in range(52)
    }

    print(f"EXPECTED_MISSING_TIMESTAMP_COUNT = {len(expected_opens)}")
    print(f"  First missing open: {min(expected_opens).isoformat()}")
    print(f"  Last missing open:  {max(expected_opens).isoformat()}")
    print(f"  Last missing close: {max(expected_closes).isoformat()}")

    # 3. Query Twelve Data Provider with explicit start_date and end_date (NO outputsize)
    provider = TwelveDataProvider()
    if not provider.is_configured():
        raise RuntimeError("FATAL: Twelve Data API key is not configured.")

    # Call Twelve Data with bounded range
    # start_date: 2026-09-14 03:00:00
    # end_date:   2026-09-14 16:00:00
    print("\nRequesting bounded Twelve Data /time_series (call 1 of max 5)...")
    provider_calls = 1

    # TwelveDataProvider.fetch_candles uses start_date and end_date WITHOUT outputsize!
    raw_candles = provider.fetch_candles(
        symbol=prim_listing.provider_symbol,
        timeframe="15m",
        start=gap_start,
        end=datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc),
        only_closed=True,
    )

    print(f"Raw candles returned by provider: {len(raw_candles)}")

    # 4. Filter and Validate Returned Candles against Expected Missing Set
    matched_missing_candles = []
    boundary_candles = []

    for c in raw_candles:
        if c.timestamp_open in expected_opens:
            matched_missing_candles.append(c)
        else:
            boundary_candles.append(c)

    provider_returned_count = len(matched_missing_candles)
    print(f"PROVIDER_RETURNED_EXPECTED_COUNT = {provider_returned_count}")
    print(f"Boundary candles returned (not missing): {len(boundary_candles)}")

    if provider_returned_count != 52:
        raise RuntimeError(
            f"FAIL-CLOSED: Expected 52 missing candles from provider, got {provider_returned_count}. "
            "Aborting repair without database writes."
        )

    # 5. Persist Missing Candles to PostgreSQL
    print("\nPersisting 52 validated candles to PostgreSQL...")
    written_count = 0
    for c in matched_missing_candles:
        candle_obj, created = MarketCandle.objects.update_or_create(
            instrument=inst,
            timeframe="15m",
            timestamp_close=c.timestamp_close,
            defaults={
                "source": prim_listing.provider,
                "timestamp_open": c.timestamp_open,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume or Decimal("0"),
                "volume_evidence": VolumeEvidenceType.UNAVAILABLE,
            },
        )
        written_count += 1

    print(f"CANDLES_WRITTEN_TO_DATABASE = {written_count}")

    # 6. Post-Write Verification
    persisted_opens = set(
        MarketCandle.objects.filter(
            instrument=inst,
            timeframe="15m",
            timestamp_open__in=expected_opens,
        ).values_list("timestamp_open", flat=True)
    )
    missing_after = expected_opens - persisted_opens
    missing_count_after = len(missing_after)

    print(f"MISSING_EXPECTED_TIMESTAMPS = {missing_count_after}")
    if missing_count_after != 0:
        raise RuntimeError(f"FAIL-CLOSED: {missing_count_after} timestamps still missing after write.")

    # 7. Re-run Continuity Audit on Latest 300 Window
    candles_300 = list(
        MarketCandle.objects.filter(
            instrument=inst,
            timeframe="15m",
        ).order_by("-timestamp_close")[:300]
    )
    candles_300.reverse()

    intrasession_gaps = []
    market_closure_gaps = []

    for i in range(1, len(candles_300)):
        c_prev = candles_300[i - 1]
        c_curr = candles_300[i]
        if c_curr.timestamp_open > c_prev.timestamp_close:
            classification = classify_candle_gap(c_prev.timestamp_close, c_curr.timestamp_open, "15m")
            if classification == "INTRASESSION_DATA_GAP":
                intrasession_gaps.append((c_prev.timestamp_close.isoformat(), c_curr.timestamp_open.isoformat()))
            else:
                market_closure_gaps.append((c_prev.timestamp_close.isoformat(), c_curr.timestamp_open.isoformat(), classification))

    print(f"\nCONTINUITY AUDIT RESULTS:")
    print(f"  UNRESOLVED_INTRASESSION_GAPS = {len(intrasession_gaps)}")
    print(f"  EXPECTED_MARKET_CLOSURE_GAPS = {len(market_closure_gaps)}: {market_closure_gaps}")

    if len(intrasession_gaps) > 0:
        raise RuntimeError(f"FAIL-CLOSED: Intrasession gaps remain: {intrasession_gaps}")

    # 8. Independent Verification via Pipeline Service
    from apps.live_monitor.types import CandleClosedEvent
    latest_candle = candles_300[-1]

    evt = CandleClosedEvent(
        event_id="EVT_POST_REPAIR_AUDIT",
        instrument="XAUUSD",
        timeframe="15m",
        timestamp_open=latest_candle.timestamp_open,
        timestamp_close=latest_candle.timestamp_close,
        open=latest_candle.open,
        high=latest_candle.high,
        low=latest_candle.low,
        close=latest_candle.close,
        volume=latest_candle.volume,
        is_closed=True,
        source=prim_listing.provider,
    )

    sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
        event=evt,
        code_revision="post_repair_audit",
    )

    primary_15m_status = state.feed_health_data.get("primary_15m")
    print(f"\nPIPELINE FEED HEALTH EVALUATION:")
    print(f"  PRIMARY_15M_AFTER = {primary_15m_status}")
    print(f"  CANDIDATE_STATE = {state.candidate_state}")
    print(f"  FEED_HEALTH_DATA = {state.feed_health_data}")

    if primary_15m_status != "HEALTHY":
        raise RuntimeError(f"FAIL-CLOSED: primary_15m evaluated to {primary_15m_status}, expected HEALTHY.")

    print("\nDATA REPAIR COMPLETED SUCCESSFULLY!")
    print(f"DATA_REPAIR_PROVIDER_CALLS = {provider_calls}")


if __name__ == "__main__":
    main()
