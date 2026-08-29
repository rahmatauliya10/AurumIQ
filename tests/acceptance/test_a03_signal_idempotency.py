"""
Acceptance Test A03: Signal Analysis Idempotency.

Invariant:
  - Rerunning closed-candle analysis produces the exact same analysis_fingerprint.
  - SignalRecord persistence uses get_or_create on analysis_fingerprint,
    guaranteeing exactly 1 database record exists for identical runs.
  - Corrected production input for the same timestamp produces a distinct fingerprint
    and a second distinct record without overwriting history.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import (
    CandleData,
    Cycle3ASnapshot,
    SessionContext,
    SessionType,
    SwingDurationContext,
    MacroEventContext,
    CalendarSeasonalityContext,
    SampleQuality,
)
from engine.signals.engine import XautSignalEngine


def generate_candle_series(length: int = 64, base_price: float = 2500.0) -> list[CandleData]:
    base_time = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(length):
        p = base_price + float(i) * 0.5
        ts_open = base_time + timedelta(minutes=15 * i)
        ts_close = base_time + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 2.0, 2))),
                low=Decimal(str(round(p - 1.5, 2))),
                close=Decimal(str(round(p, 2))),
                volume=Decimal("150.0"),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.django_db
def test_a03_signal_analysis_idempotency():
    xaut = Asset.objects.create(code="XAUT_A03", name="Tether Gold A03")
    usdt = Asset.objects.create(code="USDT_A03", name="Tether USD A03")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    candles = generate_candle_series(64)
    T = candles[-1].timestamp_close

    engine = XautSignalEngine(engine_version="4.0.0", config_version="cfg-2026-v1")

    # 1. Run Analysis Attempt 1
    snap1 = engine.analyze(
        candles_15m=candles,
        as_of=T,
        instrument=inst.symbol,
        xau_reference_price=Decimal("2530.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )
    rec1, created1 = SignalPersistenceService.save_signal_snapshot(inst, snap1)
    assert created1 is True

    # 2. Run Analysis Attempt 2 (Identical inputs)
    snap2 = engine.analyze(
        candles_15m=candles,
        as_of=T,
        instrument=inst.symbol,
        xau_reference_price=Decimal("2530.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )
    rec2, created2 = SignalPersistenceService.save_signal_snapshot(inst, snap2)
    assert created2 is False  # Not duplicated!
    assert snap1.analysis_fingerprint == snap2.analysis_fingerprint
    assert rec1.id == rec2.id

    # Verify count in DB is exactly 1
    assert SignalRecord.objects.filter(instrument=inst).count() == 1

    # 3. Re-run on same candle timestamp but with corrected production input (e.g. revised XAU reference)
    snap3 = engine.analyze(
        candles_15m=candles,
        as_of=T,
        instrument=inst.symbol,
        xau_reference_price=Decimal("2545.0"),  # Corrected / revised price
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )
    rec3, created3 = SignalPersistenceService.save_signal_snapshot(inst, snap3)

    assert created3 is True  # Second record created for corrected data
    assert snap3.analysis_fingerprint != snap1.analysis_fingerprint
    assert SignalRecord.objects.filter(instrument=inst).count() == 2

    # Original historical record rec1 is preserved completely unchanged
    rec1_refresh = SignalRecord.objects.get(id=rec1.id)
    assert rec1_refresh.analysis_fingerprint == snap1.analysis_fingerprint
