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
    ExperimentalCycleSnapshotRecord,
)
from engine.core.types import (
    FeatureSnapshot,
    RegimeResult,
    StructureResult,
    Cycle3ASnapshot,
    Cycle3BExperimentalSnapshot,
)

logger = structlog.get_logger(__name__)


class AnalysisPersistenceService:
    """
    Decoupled bridge that persists pure Python engine data structures
    into Django analysis models with snapshot version immutability.
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
        cycle_3b: Optional[Cycle3BExperimentalSnapshot] = None,
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
                "session_sample_quality": cycle_3a.session.sample_quality.value,
                "session_effective_n": cycle_3a.session.effective_n,
                "swing_market_age_bars": cycle_3a.swing_duration.market_age_bars,
                "swing_known_age_bars": cycle_3a.swing_duration.known_age_bars,
                "swing_maturity_score": cycle_3a.swing_duration.maturity_score,
                "swing_sample_quality": cycle_3a.swing_duration.sample_quality.value,
                "calendar_seasonality_score": cycle_3a.calendar.seasonality_score,
                "calendar_stability_score": cycle_3a.calendar.stability_score,
                "calendar_sample_quality": cycle_3a.calendar.sample_quality.value,
                "macro_pit_value": cycle_3a.macro_event.point_in_time_value,
                "macro_active_event": cycle_3a.macro_event.active_event_name,
                "macro_feed_healthy": cycle_3a.macro_event.is_feed_healthy,
                "local_times": cycle_3a.session.local_times,
            }
            # Snapshot Version Immutability (P3A-13): cycle_version is part of unique composite key
            CycleSnapshotRecord.objects.update_or_create(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=cycle_3a.timestamp,
                cycle_version=cycle_3a.cycle_version,
                defaults={
                    "session": cycle_3a.session.session.value,
                    "session_progress_pct": cycle_3a.session.progress_pct,
                    "is_high_liquidity": cycle_3a.session.is_high_liquidity,
                    "bars_since_last_swing": cycle_3a.swing_duration.known_age_bars,
                    "pullback_age_percentile": cycle_3a.swing_duration.pullback_age_percentile,
                    "is_mature_pullback": cycle_3a.swing_duration.is_mature,
                    "is_blocked_by_event": cycle_3a.is_blocked_by_event,
                    "cycle_score_3a": cycle_3a.cycle_score_3a,
                    "details": details,
                },
            )

        if cycle_3b:
            details_3b = {
                "acf_is_significant": cycle_3b.acf.is_significant,
                "acf_confidence_bound": cycle_3b.acf.confidence_bound,
                "fft_spectral_entropy": cycle_3b.fft.spectral_entropy,
                "fft_top_frequencies": cycle_3b.fft.psd_top_frequencies,
                "wavelet_coi_contamination": cycle_3b.wavelet.coi_contamination_pct,
                "wavelet_is_clean_endpoint": cycle_3b.wavelet.is_clean_endpoint,
                "hilbert_amplitude": cycle_3b.hilbert.instantaneous_amplitude,
                "hilbert_phase_velocity": cycle_3b.hilbert.phase_velocity,
                "hilbert_is_endpoint_reliable": cycle_3b.hilbert.is_endpoint_reliable,
                "reliability_reasons": cycle_3b.reliability.reasons,
            }
            ExperimentalCycleSnapshotRecord.objects.update_or_create(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=cycle_3b.timestamp,
                experimental_version=cycle_3b.experimental_version,
                defaults={
                    "dominant_period_bars": cycle_3b.reliability.dominant_period_bars,
                    "acf_dominant_lag": cycle_3b.acf.dominant_lag,
                    "acf_correlation": cycle_3b.acf.autocorrelation,
                    "fft_dominant_period": cycle_3b.fft.dominant_period,
                    "fft_power_ratio": cycle_3b.fft.power_ratio,
                    "wavelet_dominant_period": cycle_3b.wavelet.dominant_scale_period,
                    "wavelet_energy_ratio": cycle_3b.wavelet.energy_ratio,
                    "hilbert_phase": cycle_3b.hilbert.instantaneous_phase,
                    "hilbert_stability": cycle_3b.hilbert.phase_stability,
                    "method_agreement_pct": cycle_3b.reliability.method_agreement_pct,
                    "reliability_score": cycle_3b.reliability.reliability_score,
                    "reliability_status": cycle_3b.reliability.reliability_status.value,
                    "production_weight": cycle_3b.production_weight,
                    "promotion_status": cycle_3b.promotion_status.value,
                    "details": details_3b,
                },
            )
