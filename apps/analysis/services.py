"""Service bridge translating pure engine results into Django ORM records."""
from decimal import Decimal
from typing import Optional
from django.db import transaction
import structlog
from apps.instruments.models import Instrument
from apps.analysis.models import (
    FeatureSnapshotRecord,
    RegimeSnapshotRecord,
    StructureSnapshotRecord,
    CycleSnapshotRecord,
)
from engine.core.types import FeatureSnapshot, RegimeResult, StructureResult, Cycle3ASnapshot

logger = structlog.get_logger(__name__)


class AnalysisPersistenceService:
    """
    Decoupled bridge that persists pure Python engine data structures
    into Django analysis models.
    """

    @staticmethod
    @transaction.atomic
    def save_analysis_snapshots(
        instrument: Instrument,
        timeframe: str,
        features: Optional[FeatureSnapshot] = None,
        regime: Optional[RegimeResult] = None,
        structure: Optional[StructureResult] = None,
        cycle_3a: Optional[Cycle3ASnapshot] = None,
        feature_version: str = "feat-2026-v1",
    ) -> None:
        """Persist feature, regime, structure, and cycle snapshots atomically."""
        if features:
            FeatureSnapshotRecord.objects.update_or_create(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=features.timestamp,
                defaults={
                    "ema20": features.ema20,
                    "ema50": features.ema50,
                    "ema200": features.ema200,
                    "ema_slope_20": features.ema_slope_20,
                    "ema_alignment": features.ema_alignment,
                    "adx": features.adx,
                    "plus_di": features.plus_di,
                    "minus_di": features.minus_di,
                    "rsi14": features.rsi14,
                    "macd_line": features.macd_line,
                    "macd_signal": features.macd_signal,
                    "macd_hist": features.macd_hist,
                    "roc12": features.roc12,
                    "atr14": features.atr14,
                    "atr_pct": features.atr_pct,
                    "bb_upper": features.bb_upper,
                    "bb_middle": features.bb_middle,
                    "bb_lower": features.bb_lower,
                    "bb_bandwidth": features.bb_bandwidth,
                    "realized_vol_20": features.realized_vol_20,
                    "volume_ratio_20": features.volume_ratio_20,
                    "volume_zscore_20": features.volume_zscore_20,
                    "feature_version": feature_version,
                },
            )

        if regime:
            RegimeSnapshotRecord.objects.update_or_create(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=regime.timestamp,
                defaults={
                    "regime": regime.regime.value,
                    "confidence": Decimal(str(regime.confidence)),
                    "details": regime.details,
                    "feature_version": feature_version,
                },
            )

        if structure:
            zones_data = [
                {
                    "zone_type": z.zone_type,
                    "price_low": str(z.price_low),
                    "price_high": str(z.price_high),
                    "touches": z.touches,
                    "is_active": z.is_active,
                }
                for z in structure.zones
            ]
            StructureSnapshotRecord.objects.update_or_create(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=structure.timestamp,
                defaults={
                    "structure_type": structure.structure_type.value,
                    "bos": structure.bos.value,
                    "last_swing_high_price": structure.last_swing_high.price if structure.last_swing_high else None,
                    "last_swing_low_price": structure.last_swing_low.price if structure.last_swing_low else None,
                    "active_zones": zones_data,
                    "feature_version": feature_version,
                },
            )

        if cycle_3a:
            details = {
                "session_expectancy_score": cycle_3a.session.expectancy_score,
                "swing_maturity_score": cycle_3a.swing_duration.maturity_score,
                "calendar_seasonality_score": cycle_3a.calendar.seasonality_score,
                "calendar_stability_score": cycle_3a.calendar.stability_score,
                "macro_pit_value": cycle_3a.macro_event.point_in_time_value,
                "macro_active_event": cycle_3a.macro_event.active_event_name,
                "local_times": cycle_3a.session.local_times,
            }
            CycleSnapshotRecord.objects.update_or_create(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=cycle_3a.timestamp,
                defaults={
                    "session": cycle_3a.session.session.value,
                    "session_progress_pct": cycle_3a.session.progress_pct,
                    "is_high_liquidity": cycle_3a.session.is_high_liquidity,
                    "bars_since_last_swing": cycle_3a.swing_duration.bars_since_last_swing,
                    "pullback_age_percentile": cycle_3a.swing_duration.pullback_age_percentile,
                    "is_mature_pullback": cycle_3a.swing_duration.is_mature,
                    "is_blocked_by_event": cycle_3a.is_blocked_by_event,
                    "cycle_score_3a": cycle_3a.cycle_score_3a,
                    "details": details,
                    "cycle_version": cycle_3a.cycle_version,
                },
            )
