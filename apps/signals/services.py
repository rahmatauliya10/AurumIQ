from typing import Tuple
from apps.instruments.models import Instrument
from apps.signals.models import SignalRecord
from engine.core.types import DualSideSignalSnapshot, SignalSnapshot


class SignalPersistenceService:
    """Service to safely persist immutable SignalSnapshot and DualSideSignalSnapshot instances."""

    @staticmethod
    def save_signal_snapshot(
        instrument: Instrument,
        snapshot: SignalSnapshot,
    ) -> Tuple[SignalRecord, bool]:
        """
        Idempotently persist a SignalSnapshot (Historical XAUT).

        Uses get_or_create to guarantee:
          - Identical analysis -> returns existing record without modification (A03).
          - New config / corrected data -> creates new immutable record (A08).
        """
        components_dict = {
            "direction": [
                {
                    "name": c.name,
                    "score": c.score,
                    "max_score": c.max_score,
                    "reason": c.reason,
                    "is_available": c.is_available,
                }
                for c in snapshot.direction.components
            ],
            "timing": [
                {
                    "name": c.name,
                    "score": c.score,
                    "max_score": c.max_score,
                    "reason": c.reason,
                    "is_available": c.is_available,
                }
                for c in snapshot.timing.components
            ],
        }

        provenance_dict = {
            "is_stale_data": snapshot.hard_gate.is_stale_data,
            "is_provider_transition": snapshot.hard_gate.is_provider_transition,
            "is_macro_blackout": snapshot.hard_gate.is_macro_blackout,
            "is_missing_xau": snapshot.hard_gate.is_missing_xau,
            "is_missing_normalization": snapshot.hard_gate.is_missing_normalization,
        }

        record, created = SignalRecord.objects.get_or_create(
            analysis_fingerprint=snapshot.analysis_fingerprint,
            defaults={
                "instrument": instrument,
                "timeframe": snapshot.timeframe,
                "timestamp": snapshot.timestamp,
                "state": snapshot.state.value,
                "user_decision": snapshot.user_decision.value,
                "direction_score": snapshot.direction.total_score,
                "timing_score": snapshot.timing.total_score,
                "reasons_positive": list(snapshot.reasons_positive),
                "reasons_negative": list(snapshot.reasons_negative),
                "hard_gate_reasons": list(snapshot.hard_gate_reasons),
                "components_breakdown": components_dict,
                "provenance": provenance_dict,
                "research_fingerprint": snapshot.research_fingerprint,
                "engine_version": snapshot.engine_version,
                "config_version": snapshot.config_version,
                "feature_version": snapshot.feature_version,
                "cycle_version": snapshot.cycle_version,
                "code_revision": snapshot.code_revision,
            },
        )
        return record, created

    @staticmethod
    def save_dual_side_snapshot(
        instrument: Instrument,
        snapshot: DualSideSignalSnapshot,
    ) -> Tuple[SignalRecord, bool]:
        """
        Idempotently persist a Phase 4 XAUUSD DualSideSignalSnapshot.
        Sets legacy direction_score and timing_score to None (NULL in DB).
        Populates dual-side direction/timing scores, profile name, and policy fingerprint.
        """
        def _fmt_comps(comps):
            return [
                {
                    "name": c.name,
                    "score": c.score,
                    "max_score": c.max_score,
                    "reason": c.reason,
                    "is_available": c.is_available,
                }
                for c in comps
            ]

        components_dict = {
            "long_direction": _fmt_comps(snapshot.long_direction.components),
            "short_direction": _fmt_comps(snapshot.short_direction.components),
            "long_timing": _fmt_comps(snapshot.long_timing.components),
            "short_timing": _fmt_comps(snapshot.short_timing.components),
            "candidate_state": snapshot.candidate_state.value,
            "candidate_user_decision": snapshot.candidate_user_decision.value,
            "candidate_resolution_reason": snapshot.candidate_resolution_reason,
            "publication_reason": snapshot.publication_reason,
        }

        rfh = snapshot.hard_gate.runtime_health
        provenance_dict = {
            "primary_15m": rfh.primary_15m.value,
            "primary_1h": rfh.primary_1h.value,
            "primary_4h": rfh.primary_4h.value,
            "primary_1d": rfh.primary_1d.value,
            "secondary_provider": rfh.secondary_provider.value,
            "secondary_provider_disagreement": rfh.secondary_provider_disagreement,
            "macro_blackout_feed": rfh.macro_blackout_feed.value,
            "is_macro_blackout": rfh.is_macro_blackout,
            "volume": rfh.volume.value,
            "phase3a": rfh.phase3a.value,
            "phase3b": rfh.phase3b.value,
            "is_unclosed_candle": rfh.is_unclosed_candle,
            "candidate_resolution_reason": snapshot.candidate_resolution_reason,
            "publication_reason": snapshot.publication_reason,
        }

        reasons_pos = list(snapshot.reasons_long_positive) + list(snapshot.reasons_short_positive)
        reasons_neg = list(snapshot.reasons_long_negative) + list(snapshot.reasons_short_negative)

        record, created = SignalRecord.objects.get_or_create(
            analysis_fingerprint=snapshot.analysis_fingerprint,
            defaults={
                "instrument": instrument,
                "timeframe": snapshot.timeframe,
                "timestamp": snapshot.timestamp,
                "state": snapshot.state.value,
                "user_decision": snapshot.user_decision.value,
                "direction_score": None,  # Strictly NULL for XAUUSD
                "timing_score": None,     # Strictly NULL for XAUUSD
                "long_direction_score": snapshot.long_direction.total_score,
                "short_direction_score": snapshot.short_direction.total_score,
                "long_timing_score": snapshot.long_timing.total_score,
                "short_timing_score": snapshot.short_timing.total_score,
                "profile_name": snapshot.profile_name,
                "calibration_status": snapshot.calibration_status,
                "resolution_reason": snapshot.publication_reason or snapshot.resolution_reason,
                "phase4_policy_fingerprint": snapshot.phase4_policy_fingerprint,
                "reasons_positive": reasons_pos,
                "reasons_negative": reasons_neg,
                "hard_gate_reasons": list(snapshot.hard_gate_reasons),
                "components_breakdown": components_dict,
                "provenance": provenance_dict,
                "research_fingerprint": snapshot.research_fingerprint,
                "engine_version": snapshot.engine_version,
                "config_version": snapshot.config_version,
                "feature_version": snapshot.feature_version,
                "cycle_version": snapshot.cycle_version,
                "code_revision": snapshot.code_revision,
            },
        )
        return record, created

