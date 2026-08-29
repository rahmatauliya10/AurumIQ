"""Unit test for AnalysisPersistenceService bridge."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.analysis.models import (
    FeatureSnapshotRecord,
    RegimeSnapshotRecord,
    StructureSnapshotRecord,
)
from apps.analysis.services import AnalysisPersistenceService
from engine.core.types import (
    FeatureSnapshot,
    RegimeResult,
    RegimeType,
    StructureResult,
    StructureType,
    BosType,
    SwingPoint,
    SwingType,
    StructureZone,
)


@pytest.mark.unit
@pytest.mark.django_db
def test_analysis_persistence_service_saves_snapshots():
    """Verify that pure engine dataclasses are persisted to Django ORM without leaking Django into engine."""
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

    # 1. Feature Snapshot
    features = FeatureSnapshot(
        timestamp=now,
        ema20=Decimal("2505.50"), ema50=Decimal("2495.00"), ema200=Decimal("2450.00"),
        ema_slope_20=0.12, ema_alignment=1, adx=26.5, plus_di=32.0, minus_di=12.0,
        rsi14=62.5, macd_line=Decimal("6.5"), macd_signal=Decimal("5.0"), macd_hist=Decimal("1.5"), roc12=3.2,
        atr14=Decimal("12.50"), atr_pct=0.5, bb_upper=Decimal("2525.00"), bb_middle=Decimal("2505.00"),
        bb_lower=Decimal("2485.00"), bb_bandwidth=1.6, realized_vol_20=1.2, volume_ratio_20=1.1, volume_zscore_20=0.4,
    )

    # 2. Regime Result
    regime = RegimeResult(
        regime=RegimeType.BULL_TREND,
        confidence=0.86,
        timestamp=now,
        details={"adx": 26.5, "rsi": 62.5},
    )

    # 3. Structure Result
    swing = SwingPoint(
        index=10,
        timestamp=now,
        detected_at=now,
        price=Decimal("2510.00"),
        swing_type=SwingType.HIGH,
        is_confirmed=True,
    )
    zone = StructureZone(
        zone_type="RESISTANCE",
        price_low=Decimal("2508.00"),
        price_high=Decimal("2512.00"),
        created_at=now,
        touches=2,
        is_active=True,
    )
    structure = StructureResult(
        timestamp=now,
        structure_type=StructureType.HH,
        bos=BosType.BULLISH,
        last_swing_high=swing,
        last_swing_low=None,
        swings=(swing,),
        zones=(zone,),
    )

    # Execute service persistence
    AnalysisPersistenceService.save_analysis_snapshots(
        instrument=inst,
        timeframe="15m",
        features=features,
        regime=regime,
        structure=structure,
    )

    # Assert database records
    f_rec = FeatureSnapshotRecord.objects.get(instrument=inst, timeframe="15m", timestamp=now)
    assert f_rec.ema20 == Decimal("2505.50000000")
    assert f_rec.rsi14 == 62.5
    assert f_rec.adx == 26.5

    r_rec = RegimeSnapshotRecord.objects.get(instrument=inst, timeframe="15m", timestamp=now)
    assert r_rec.regime == "BULL_TREND"
    assert r_rec.confidence == Decimal("0.8600")

    s_rec = StructureSnapshotRecord.objects.get(instrument=inst, timeframe="15m", timestamp=now)
    assert s_rec.structure_type == "HH"
    assert s_rec.bos == "BULLISH"
    assert s_rec.last_swing_high_price == Decimal("2510.00000000")
    assert len(s_rec.active_zones) == 1
    assert s_rec.active_zones[0]["zone_type"] == "RESISTANCE"
