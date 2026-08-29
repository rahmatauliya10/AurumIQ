"""Persistence service for SignalRecord (Phase 4)."""
from typing import Tuple
from apps.instruments.models import Instrument
from apps.signals.models import SignalRecord
from engine.core.types import SignalSnapshot


class SignalPersistenceService:
    """Service to safely persist immutable SignalSnapshot instances."""

    @staticmethod
    def save_signal_snapshot(
        instrument: Instrument,
        snapshot: SignalSnapshot,
    ) -> Tuple[SignalRecord, bool]:
        """
        Idempotently persist a SignalSnapshot.

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
