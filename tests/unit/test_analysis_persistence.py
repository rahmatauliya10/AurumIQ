"""Unit test for AnalysisPersistenceService bridge."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.analysis.models import (
    FeatureSnapshotRecord,
    RegimeSnapshotRecord,
    StructureSnapshotRecord,
    CycleSnapshotRecord,
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
    Cycle3ASnapshot,
    SessionContext,
    SessionType,
    SwingDurationContext,
    MacroEventContext,
    CalendarSeasonalityContext,
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

    # 4. Cycle 3A Snapshot
    cycle_3a = Cycle3ASnapshot(
        timestamp=now,
        session=SessionContext(
            session=SessionType.LONDON,
            progress_pct=80.0,
            is_high_liquidity=True,
            local_times={"UTC": "2026-08-29 12:00:00 UTC", "London": "2026-08-29 13:00:00 BST"},
            expectancy_score=12.0,
        ),
        swing_duration=SwingDurationContext(
            market_age_bars=20,
            market_age_hours=5.0,
            known_age_bars=16,
            known_age_hours=4.0,
            pullback_age_percentile=50.0,
            is_mature=False,
            maturity_score=15.0,
        ),
        macro_event=MacroEventContext(
            is_in_blackout=False,
            minutes_to_next_event=180,
            minutes_since_last_event=None,
            active_event_name=None,
            is_feed_healthy=True,
        ),
        calendar=CalendarSeasonalityContext(
            day_of_week=5,
            day_name="Saturday",
            hour_utc=12,
            month=8,
            is_month_end_flow=True,
            stability_score=0.85,
            seasonality_score=3.5,
        ),
        is_blocked_by_event=False,
        cycle_score_3a=35.5,
        cycle_version="3.0.0-3A",
    )

    # Execute service persistence
    AnalysisPersistenceService.save_analysis_snapshots(
        instrument=inst,
        timeframe="15m",
        features=features,
        regime=regime,
        structure=structure,
        cycle_3a=cycle_3a,
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

    c_rec = CycleSnapshotRecord.objects.get(instrument=inst, timeframe="15m", timestamp=now, cycle_version="3.0.0-3A")
    assert c_rec.session == "LONDON"
    assert c_rec.session_progress_pct == 80.0
    assert c_rec.is_high_liquidity is True
    assert c_rec.bars_since_last_swing == 16
    assert c_rec.cycle_score_3a == 35.5
    assert c_rec.cycle_version == "3.0.0-3A"


@pytest.mark.unit
@pytest.mark.django_db
def test_p3a_13_versioned_cycle_snapshot_immutability():
    """
    P3A-13: Versioned Cycle Snapshot Immutability.
    Verifies that saving a new cycle_version (e.g. 3.0.0-3A vs 3.1.0-3B) at the same timestamp
    creates distinct records and does not overwrite existing snapshots.
    """
    xaut = Asset.objects.create(code="XAUT2", name="Tether Gold 2")
    usdt = Asset.objects.create(code="USDT2", name="Tether USD 2")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    now = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)

    # 1. Save Phase 3A Baseline Version
    cycle_v1 = Cycle3ASnapshot(
        timestamp=now,
        session=SessionContext(session=SessionType.LONDON_NY_OVERLAP, progress_pct=50.0, is_high_liquidity=True, local_times={}),
        swing_duration=SwingDurationContext(market_age_bars=10, market_age_hours=2.5, known_age_bars=8, known_age_hours=2.0, pullback_age_percentile=75.0, is_mature=True, maturity_score=20.0),
        macro_event=MacroEventContext(is_in_blackout=False, minutes_to_next_event=150, minutes_since_last_event=None, active_event_name=None, is_feed_healthy=True),
        calendar=CalendarSeasonalityContext(day_of_week=2, day_name="Wednesday", hour_utc=14, month=8, is_month_end_flow=False, stability_score=0.9, seasonality_score=4.5),
        is_blocked_by_event=False,
        cycle_score_3a=29.5,
        cycle_version="3.0.0-3A",
    )
    AnalysisPersistenceService.save_analysis_snapshots(instrument=inst, timeframe="15m", cycle_3a=cycle_v1)

    # 2. Save Experimental Version (e.g. 3.1.0-3B) at the EXACT same timestamp
    cycle_v2 = Cycle3ASnapshot(
        timestamp=now,
        session=SessionContext(session=SessionType.LONDON_NY_OVERLAP, progress_pct=50.0, is_high_liquidity=True, local_times={}),
        swing_duration=SwingDurationContext(market_age_bars=10, market_age_hours=2.5, known_age_bars=8, known_age_hours=2.0, pullback_age_percentile=75.0, is_mature=True, maturity_score=20.0),
        macro_event=MacroEventContext(is_in_blackout=False, minutes_to_next_event=150, minutes_since_last_event=None, active_event_name=None, is_feed_healthy=True),
        calendar=CalendarSeasonalityContext(day_of_week=2, day_name="Wednesday", hour_utc=14, month=8, is_month_end_flow=False, stability_score=0.9, seasonality_score=4.5),
        is_blocked_by_event=False,
        cycle_score_3a=34.0,  # e.g. includes new spectral booster
        cycle_version="3.1.0-3B",
    )
    AnalysisPersistenceService.save_analysis_snapshots(instrument=inst, timeframe="15m", cycle_3a=cycle_v2)

    # Assert that BOTH snapshots exist concurrently and independently
    all_snapshots = CycleSnapshotRecord.objects.filter(instrument=inst, timeframe="15m", timestamp=now).order_by("cycle_version")
    assert all_snapshots.count() == 2

    rec_v1 = all_snapshots.get(cycle_version="3.0.0-3A")
    rec_v2 = all_snapshots.get(cycle_version="3.1.0-3B")

    assert rec_v1.cycle_score_3a == 29.5
    assert rec_v2.cycle_score_3a == 34.0


@pytest.mark.unit
@pytest.mark.django_db
def test_p3a_rehydrate_01_persistence_roundtrip_lossless():
    """
    P3A-REHYDRATE-01: Verify original Cycle3ASnapshot -> persistence -> rehydrate
    preserves all canonical Phase 3A fields losslessly without fabricated defaults.
    """
    from engine.core.types import SampleQuality
    xaut = Asset.objects.create(code="XAUT_RH", name="Tether Gold RH")
    usdt = Asset.objects.create(code="USDT_RH", name="Tether USD RH")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT)

    now = datetime(2026, 8, 29, 15, 30, tzinfo=timezone.utc)

    original_snap = Cycle3ASnapshot(
        timestamp=now,
        session=SessionContext(
            session=SessionType.LONDON_NY_OVERLAP,
            progress_pct=65.5,
            is_high_liquidity=True,
            local_times={"UTC": "15:30", "London": "16:30", "NY": "11:30"},
            expectancy_score=18.5,
            sample_quality=SampleQuality.HIGH,
            effective_n=150.0,
        ),
        swing_duration=SwingDurationContext(
            market_age_bars=15,
            market_age_hours=3.75,
            known_age_bars=12,
            known_age_hours=3.0,
            pullback_age_percentile=68.0,
            is_mature=True,
            maturity_score=22.5,
            sample_quality=SampleQuality.HIGH,
            effective_n=85.0,
        ),
        macro_event=MacroEventContext(
            is_in_blackout=False,
            minutes_to_next_event=120,
            minutes_since_last_event=45,
            active_event_name="FOMC Preview",
            point_in_time_value="HOLD",
            is_feed_healthy=True,
        ),
        calendar=CalendarSeasonalityContext(
            day_of_week=4,
            day_name="Friday",
            hour_utc=15,
            month=8,
            is_month_end_flow=True,
            stability_score=0.92,
            seasonality_score=4.8,
            sample_quality=SampleQuality.HIGH,
            effective_n=110.0,
        ),
        is_blocked_by_event=False,
        cycle_score_3a=42.5,
        cycle_version="3.0.0-3A",
    )

    AnalysisPersistenceService.save_analysis_snapshots(
        instrument=inst,
        timeframe="15m",
        cycle_3a=original_snap,
    )

    rec = CycleSnapshotRecord.objects.get(
        instrument=inst,
        timeframe="15m",
        timestamp=now,
        cycle_version="3.0.0-3A",
    )

    rehydrated = AnalysisPersistenceService.rehydrate_cycle_3a_snapshot(rec)

    # Invariants: 100% field parity
    assert rehydrated.timestamp == original_snap.timestamp
    assert rehydrated.cycle_version == original_snap.cycle_version
    assert rehydrated.cycle_score_3a == original_snap.cycle_score_3a
    assert rehydrated.is_blocked_by_event == original_snap.is_blocked_by_event

    # Session fields
    assert rehydrated.session.session == original_snap.session.session
    assert rehydrated.session.progress_pct == original_snap.session.progress_pct
    assert rehydrated.session.is_high_liquidity == original_snap.session.is_high_liquidity
    assert rehydrated.session.expectancy_score == original_snap.session.expectancy_score
    assert rehydrated.session.sample_quality == original_snap.session.sample_quality
    assert rehydrated.session.effective_n == original_snap.session.effective_n
    assert rehydrated.session.local_times == original_snap.session.local_times

    # Swing Duration fields
    assert rehydrated.swing_duration.market_age_bars == original_snap.swing_duration.market_age_bars
    assert rehydrated.swing_duration.market_age_hours == original_snap.swing_duration.market_age_hours
    assert rehydrated.swing_duration.known_age_bars == original_snap.swing_duration.known_age_bars
    assert rehydrated.swing_duration.known_age_hours == original_snap.swing_duration.known_age_hours
    assert rehydrated.swing_duration.pullback_age_percentile == original_snap.swing_duration.pullback_age_percentile
    assert rehydrated.swing_duration.is_mature == original_snap.swing_duration.is_mature
    assert rehydrated.swing_duration.maturity_score == original_snap.swing_duration.maturity_score
    assert rehydrated.swing_duration.sample_quality == original_snap.swing_duration.sample_quality
    assert rehydrated.swing_duration.effective_n == original_snap.swing_duration.effective_n

    # Calendar Seasonality fields
    assert rehydrated.calendar.day_of_week == original_snap.calendar.day_of_week
    assert rehydrated.calendar.day_name == original_snap.calendar.day_name
    assert rehydrated.calendar.hour_utc == original_snap.calendar.hour_utc
    assert rehydrated.calendar.month == original_snap.calendar.month
    assert rehydrated.calendar.is_month_end_flow == original_snap.calendar.is_month_end_flow
    assert rehydrated.calendar.stability_score == original_snap.calendar.stability_score
    assert rehydrated.calendar.seasonality_score == original_snap.calendar.seasonality_score
    assert rehydrated.calendar.sample_quality == original_snap.calendar.sample_quality
    assert rehydrated.calendar.effective_n == original_snap.calendar.effective_n

    # Macro Event fields
    assert rehydrated.macro_event.is_in_blackout == original_snap.macro_event.is_in_blackout
    assert rehydrated.macro_event.minutes_to_next_event == original_snap.macro_event.minutes_to_next_event
    assert rehydrated.macro_event.minutes_since_last_event == original_snap.macro_event.minutes_since_last_event
    assert rehydrated.macro_event.active_event_name == original_snap.macro_event.active_event_name
    assert rehydrated.macro_event.point_in_time_value == original_snap.macro_event.point_in_time_value
    assert rehydrated.macro_event.is_feed_healthy == original_snap.macro_event.is_feed_healthy

