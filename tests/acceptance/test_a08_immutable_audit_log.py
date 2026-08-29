"""
Acceptance Test A08: Immutable Audit Log.

Invariant:
  - Evaluating signals under ConfigVersion A persists Signal A.
  - Activating ConfigVersion B generates Signal B with a distinct analysis_fingerprint.
  - Historical Signal A remains completely unmodified and queryable in the database.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.signals.models import SignalRecord
from apps.signals.services import SignalPersistenceService
from engine.core.types import CandleData
from engine.signals.engine import XautSignalEngine


def generate_candle_series(length: int = 64) -> list[CandleData]:
    base_time = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(length):
        p = 2500.0 + float(i)
        ts_open = base_time + timedelta(minutes=15 * i)
        ts_close = base_time + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 2.0, 2))),
                low=Decimal(str(round(p - 1.0, 2))),
                close=Decimal(str(round(p, 2))),
                volume=Decimal("100.0"),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.django_db
def test_a08_immutable_audit_log():
    xaut = Asset.objects.create(code="XAUT_A08", name="Tether Gold A08")
    usdt = Asset.objects.create(code="USDT_A08", name="Tether USD A08")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    candles = generate_candle_series(64)
    T = candles[-1].timestamp_close

    # 1. Generate signal with Config A
    engine_a = XautSignalEngine(config_version="cfg-2026-v1")
    snap_a = engine_a.analyze(
        candles_15m=candles,
        as_of=T,
        instrument=inst.symbol,
        xau_reference_price=Decimal("2560.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )
    rec_a, _ = SignalPersistenceService.save_signal_snapshot(inst, snap_a)

    # 2. Activate Config B
    engine_b = XautSignalEngine(config_version="cfg-2026-v2")
    snap_b = engine_b.analyze(
        candles_15m=candles,
        as_of=T,
        instrument=inst.symbol,
        xau_reference_price=Decimal("2560.0"),
        xau_reference_is_bullish=True,
        usdt_rate=Decimal("1.0"),
    )
    rec_b, _ = SignalPersistenceService.save_signal_snapshot(inst, snap_b)

    # Verify both records exist and have distinct provenance
    assert rec_a.analysis_fingerprint != rec_b.analysis_fingerprint
    assert rec_a.config_version == "cfg-2026-v1"
    assert rec_b.config_version == "cfg-2026-v2"

    # Querying DB confirms record A is untouched
    persisted_a = SignalRecord.objects.get(config_version="cfg-2026-v1")
    persisted_b = SignalRecord.objects.get(config_version="cfg-2026-v2")

    assert persisted_a.analysis_fingerprint == snap_a.analysis_fingerprint
    assert persisted_b.analysis_fingerprint == snap_b.analysis_fingerprint
