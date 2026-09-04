"""Deterministic cryptographic fingerprinting for macroeconomic event evidence (Spec §33, §34)."""
import hashlib
from typing import List, Tuple
from apps.market_data.models import MacroObservationVintage, MacroScheduleVintage


def compute_macro_evidence_fingerprint() -> str:
    """
    Compute deterministic SHA-256 fingerprint over all persisted macro schedules and observations.

    Guarantees:
    1. Independent of database-generated autoincrement primary keys.
    2. Sorted strictly and deterministically by canonical composite keys:
       - Schedules: (event__event_family, event_id, reference_period, known_at, scheduled_at, vintage_id)
       - Observations: (event__event_family, event_id, reference_period, revision_number, known_at, vintage_id)
    3. Canonical field serialization with explicit delimiter:
       SCHED|family|event_id|ref_period|status|scheduled_at_iso|known_at_iso|source_snapshot_sha256
       OBS|family|event_id|ref_period|rev_num|published_at_iso|known_at_iso|raw_val|level_val|change_val|source_snapshot_sha256
    """
    lines: List[str] = []

    # 1. Schedules
    schedules = (
        MacroScheduleVintage.objects.select_related("event", "source_snapshot")
        .order_by("event__event_family", "event_id", "reference_period", "known_at", "scheduled_at", "vintage_id")
    )
    for s in schedules:
        snap_sha = s.source_snapshot.raw_payload_bytes_sha256 if s.source_snapshot else "NO_SNAPSHOT"
        sched_iso = s.scheduled_at.isoformat() if s.scheduled_at else ""
        known_iso = s.known_at.isoformat() if s.known_at else ""
        prov_type = getattr(s, "provenance_type", "UNKNOWN") or "UNKNOWN"
        ann_url = getattr(s, "announcing_release_url", "") or ""
        line = f"SCHED|{s.event.event_family}|{s.event_id}|{s.reference_period}|{s.schedule_status}|{sched_iso}|{known_iso}|{prov_type}|{ann_url}|{snap_sha}"
        lines.append(line)

    # 2. Observations
    observations = (
        MacroObservationVintage.objects.select_related("event", "source_snapshot")
        .order_by("event__event_family", "event_id", "reference_period", "revision_number", "known_at", "vintage_id")
    )
    for o in observations:
        snap_sha = o.source_snapshot.raw_payload_bytes_sha256 if o.source_snapshot else "NO_SNAPSHOT"
        pub_iso = o.source_published_at.isoformat() if o.source_published_at else ""
        known_iso = o.known_at.isoformat() if o.known_at else ""
        pub_status = getattr(o, "publication_status", "PUBLISHED")
        reason = getattr(o, "non_publication_reason", "") or ""
        raw_v = o.raw_value or ""
        lvl_v = f"{o.level_value:.4f}" if o.level_value is not None else ""
        chg_v = f"{o.derived_change_value:.4f}" if o.derived_change_value is not None else ""
        line = f"OBS|{o.event.event_family}|{o.event_id}|{o.reference_period}|{o.revision_number}|{pub_status}|{reason}|{pub_iso}|{known_iso}|{raw_v}|{lvl_v}|{chg_v}|{snap_sha}"
        lines.append(line)

    if not lines:
        return hashlib.sha256(b"EMPTY_MACRO_EVIDENCE_STREAM").hexdigest()

    canonical_payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(canonical_payload).hexdigest()
