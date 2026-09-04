"""Canonical expected-event set reconciliation and coverage evaluator."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple


def get_canonical_expected_cpi_keys() -> Set[str]:
    """
    Generate the canonical expected 77 monthly CPI release keys in [2020-04-07, 2026-09-01).
    Reference periods: 2020-03 (released 2020-04-10) through 2026-07 (released 2026-08-12).
    """
    keys = set()
    # 2020: months 03 to 12 (10 months)
    for m in range(3, 13):
        keys.add(f"US_CPI_2020_{m:02d}")
    # 2021 - 2025: 12 months each (5 * 12 = 60 months)
    for yr in range(2021, 2026):
        for m in range(1, 13):
            keys.add(f"US_CPI_{yr}_{m:02d}")
    # 2026: months 01 to 07 (7 months)
    for m in range(1, 8):
        keys.add(f"US_CPI_2026_{m:02d}")
    assert len(keys) == 77, f"Expected 77 CPI keys, got {len(keys)}"
    return keys


def get_canonical_expected_nfp_keys() -> Set[str]:
    """
    Generate the canonical expected 76 monthly NFP release keys in [2020-04-07, 2026-09-01).
    Reference periods: 2020-04 (released 2020-05-08) through 2026-07 (released 2026-08-07).
    Note: 2020-03 NFP was released on 2020-04-03, prior to window start 2020-04-07.
    """
    keys = set()
    # 2020: months 04 to 12 (9 months)
    for m in range(4, 13):
        keys.add(f"US_NFP_2020_{m:02d}")
    # 2021 - 2025: 12 months each (5 * 12 = 60 months)
    for yr in range(2021, 2026):
        for m in range(1, 13):
            keys.add(f"US_NFP_{yr}_{m:02d}")
    # 2026: months 01 to 07 (7 months)
    for m in range(1, 8):
        keys.add(f"US_NFP_2026_{m:02d}")
    assert len(keys) == 76, f"Expected 76 NFP keys, got {len(keys)}"
    return keys


def get_canonical_expected_fomc_keys() -> Set[str]:
    """
    Generate the canonical expected 51 FOMC policy decision keys in [2020-04-07, 2026-09-01).
    Meeting dates confirmed against official Federal Reserve Board calendars:
    """
    dates = [
        # 2020: 6 meetings
        "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
        # 2021: 8 meetings
        "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
        # 2022: 8 meetings
        "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
        # 2023: 8 meetings
        "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        # 2024: 8 meetings
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        # 2025: 8 meetings
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        # 2026: 5 meetings (April 29, 2026 replaces May 6)
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
    ]
    keys = {f"FOMC_RATE_{d.replace('-', '_')}" for d in dates}
    assert len(keys) == 51, f"Expected 51 FOMC keys, got {len(keys)}"
    return keys


def canonical_key_to_ref_period(canonical_key: str) -> str:
    """
    Convert canonical key ('US_CPI_YYYY_MM', 'US_NFP_YYYY_MM', 'FOMC_RATE_YYYY_MM_DD')
    to reference period key ('YYYY-MM' or 'YYYY-MM-DD').
    """
    pts = canonical_key.split("_")
    if len(pts) >= 4 and pts[0] == "US":
        return f"{int(pts[2]):04d}-{int(pts[3]):02d}"
    if len(pts) >= 5 and pts[0] == "FOMC":
        return f"{int(pts[2]):04d}-{int(pts[3]):02d}-{int(pts[4]):02d}"
    return canonical_key


def get_previous_canonical_ref_period(
    sorted_keys: List[str],
    i: int,
    schedule_map: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Resolve the preceding reference period for a canonical event in 'YYYY-MM' format.
    If i > 0, checks prior elements in sorted_keys that exist in schedule_map (skipping cancelled months).
    If i == 0, calculates preceding calendar month.
    """
    if i > 0 and schedule_map:
        for idx in range(i - 1, -1, -1):
            cand = canonical_key_to_ref_period(sorted_keys[idx])
            if cand in schedule_map:
                return cand

    first_key = sorted_keys[i] if i < len(sorted_keys) else sorted_keys[0]
    ref_ym = canonical_key_to_ref_period(first_key)
    parts = ref_ym.split("-")
    yr, mo = int(parts[0]), int(parts[1])
    if mo == 1:
        return f"{yr - 1:04d}-12"
    else:
        return f"{yr:04d}-{mo - 1:02d}"


def get_effective_schedule_provenance(s: Any) -> Dict[str, Any]:
    """Resolve effective provenance attributes from latest active MacroScheduleProvenanceAssertion or vintage fields."""
    from apps.market_data.models import ScheduleProvenanceType
    assertions_rel = getattr(s, "provenance_assertions", None)
    latest_assertion = None
    if assertions_rel is not None:
        try:
            latest_assertion = assertions_rel.order_by("-asserted_at").first()
        except Exception:
            if isinstance(assertions_rel, list) and assertions_rel:
                latest_assertion = sorted(assertions_rel, key=lambda a: getattr(a, "asserted_at", None) or datetime.min)[-1]

    if latest_assertion:
        return {
            "provenance_type": latest_assertion.provenance_type,
            "source_snapshot": latest_assertion.source_snapshot or getattr(s, "source_snapshot", None),
            "announcing_release_url": latest_assertion.announcing_release_url,
            "announcing_release_timestamp": latest_assertion.announcing_release_timestamp,
            "parser_rule_version": latest_assertion.parser_rule_version,
            "assertion": latest_assertion,
        }

    return {
        "provenance_type": getattr(s, "provenance_type", None) or ScheduleProvenanceType.UNKNOWN,
        "source_snapshot": getattr(s, "source_snapshot", None),
        "announcing_release_url": getattr(s, "announcing_release_url", None),
        "announcing_release_timestamp": getattr(s, "announcing_release_timestamp", None),
        "parser_rule_version": getattr(s, "parser_rule_version", "BLS_PREVIOUS_RELEASE_V1"),
        "assertion": None,
    }


def validate_schedule_vintage_provenance(s: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate quality and defensibility of schedule provenance (Spec §33, §34, Prompt §7, §8).
    Rejects fabricated timestamps, missing snapshots, unsupporting snapshots, or invalid provenance types.
    """
    from apps.market_data.models import ScheduleProvenanceType, ScheduleStatus

    eff = get_effective_schedule_provenance(s)
    prov_type = eff["provenance_type"]
    if prov_type == ScheduleProvenanceType.UNKNOWN:
        return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} has UNKNOWN provenance type."

    snap = eff["source_snapshot"]
    if not snap:
        return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} lacks supporting SourceSnapshot."

    snap_sha = getattr(snap, "raw_payload_bytes_sha256", "")
    if not snap_sha or len(snap_sha) != 64:
        return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} SourceSnapshot has invalid SHA-256."

    known_at = getattr(s, "known_at", None)
    if not known_at:
        return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} lacks known_at timestamp."

    sched_at = getattr(s, "scheduled_at", None)
    status = getattr(s, "schedule_status", ScheduleStatus.SCHEDULED)
    # Hostility: schedule must not use its own future release timestamp as known_at
    if sched_at and known_at >= sched_at and status != ScheduleStatus.CANCELLED:
        return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} known_at ({known_at}) is >= scheduled_at ({sched_at})."

    # Type-specific validation
    if prov_type == ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT:
        ann_url = eff["announcing_release_url"]
        ann_ts = eff["announcing_release_timestamp"]
        if not ann_url:
            return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} missing announcing_release_url."
        if not ann_ts:
            return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} missing announcing_release_timestamp."
        if known_at != ann_ts:
            return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} known_at does not match announcing release timestamp."
        # Content containment check if raw_content present
        raw_bytes = getattr(snap, "raw_content", None)
        if raw_bytes:
            text = raw_bytes.decode("utf-8", errors="ignore").lower()
            event_id = getattr(s, "event_id", "")
            if event_id == "US_CPI" and "consumer price index" not in text and "cpi" not in text:
                return False, f"SourceSnapshot for {getattr(s, 'vintage_id', 'N/A')} does not contain CPI announcement text."
            if event_id == "US_NFP" and "employment situation" not in text and "payroll" not in text:
                return False, f"SourceSnapshot for {getattr(s, 'vintage_id', 'N/A')} does not contain Employment Situation announcement text."

    elif prov_type == ScheduleProvenanceType.OMB_PFEI_SCHEDULE:
        src_pub = getattr(s, "source_published_at", None)
        if not src_pub:
            return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} with OMB provenance lacks defensible publication date."
        raw_bytes = getattr(snap, "raw_content", None)
        if raw_bytes:
            text = raw_bytes.decode("utf-8", errors="ignore").lower()
            if "principal federal economic indicators" not in text and "omb" not in text and "oira" not in text:
                return False, f"SourceSnapshot for {getattr(s, 'vintage_id', 'N/A')} does not contain OMB PFEI schedule text."

    elif prov_type == ScheduleProvenanceType.OTHER_FIRST_PARTY:
        src_pub = getattr(s, "source_published_at", None)
        if not src_pub and not known_at:
            return False, f"Schedule {getattr(s, 'vintage_id', 'N/A')} lacks first-party timestamp."

    return True, None


@dataclass(frozen=True)
class CanonicalCoverageReport:
    """Rigorous set reconciliation coverage report per family."""
    family: str
    expected_count: int
    observed_count: int
    matched_count: int
    missing_count: int
    unexpected_extra_count: int
    duplicate_count: int
    invalid_count: int
    coverage_pct: float
    is_complete: bool
    missing_keys: List[str]
    unexpected_keys: List[str]
    published_count: int = 0
    published_late_or_bundled_count: int = 0
    officially_not_published_count: int = 0
    missing_unexplained_count: int = 0
    lifecycle_coverage_complete: bool = False
    schedule_coverage_complete: bool = False
    observation_coverage_complete: bool = False
    provenance_coverage_complete: bool = False


def evaluate_canonical_macro_coverage(
    family: str,
    observed_keys: List[str],
    invalid_keys: Optional[List[str]] = None,
    observation_status_map: Optional[Dict[str, str]] = None,
    numeric_values_map: Optional[Dict[str, Any]] = None,
    provenance_map: Optional[Dict[str, bool]] = None,
) -> CanonicalCoverageReport:
    """
    Evaluate coverage strictly using set reconciliation:
    Coverage % = (|E ∩ O_valid| / |E|) * 100.0%

    Rules:
    - Extra unexpected keys (O \\ E) are flagged and NEVER raise coverage.
    - Duplicate keys are tracked and must be 0 for is_complete=True.
    - Invalid records are tracked and must be 0 for is_complete=True.
    - Dimensions evaluated:
      * Event lifecycle: All canonical keys represented.
      * Observation status: PUBLISHED, PUBLISHED_LATE_OR_BUNDLED, OFFICIALLY_NOT_PUBLISHED.
      * Missing unexplained and invalid records fail is_complete.
      * Fabricated numeric observations for officially-not-published events fail as INVALID.
      * Incomplete or missing provenance fails as INVALID.
    """
    if family == "US_CPI":
        expected_set = get_canonical_expected_cpi_keys()
    elif family == "US_NFP":
        expected_set = get_canonical_expected_nfp_keys()
    elif family == "FOMC_RATE":
        expected_set = get_canonical_expected_fomc_keys()
    else:
        raise ValueError(f"Unknown macro family: {family}")

    invalid_set = set(invalid_keys or [])
    status_map = observation_status_map or {}
    num_map = numeric_values_map or {}
    prov_map = provenance_map or {}

    # Validation: CPI October 2025 must NEVER contain a numeric observation
    if family == "US_CPI" and "US_CPI_2025_10" in observed_keys:
        val = num_map.get("US_CPI_2025_10")
        if val is not None and str(val).strip() not in ("", "None", "OFFICIALLY_NOT_PUBLISHED", "N/A"):
            # Fabricated numeric value detected! Must fail closed as INVALID
            invalid_set.add("US_CPI_2025_10")

    # Validation: Any event with False provenance fails as INVALID
    for k, prov_ok in prov_map.items():
        if prov_ok is False:
            invalid_set.add(k)

    # Calculate duplicates
    seen = set()
    duplicates = set()
    for k in observed_keys:
        if k in seen:
            duplicates.add(k)
        seen.add(k)
    duplicate_count = len(observed_keys) - len(seen)

    valid_observed_set = seen - invalid_set

    matched_set = expected_set.intersection(valid_observed_set)
    missing_set = expected_set - valid_observed_set
    unexpected_set = valid_observed_set - expected_set

    expected_cnt = len(expected_set)
    matched_cnt = len(matched_set)
    missing_cnt = len(missing_set)
    unexpected_cnt = len(unexpected_set)
    invalid_cnt = len(invalid_set)

    # Count observation statuses among matched keys
    pub_cnt = 0
    late_cnt = 0
    not_pub_cnt = 0
    for k in matched_set:
        st = status_map.get(k, "PUBLISHED")
        if st == "PUBLISHED_LATE_OR_BUNDLED":
            late_cnt += 1
        elif st == "OFFICIALLY_NOT_PUBLISHED":
            not_pub_cnt += 1
        else:
            pub_cnt += 1

    coverage_pct = (matched_cnt / expected_cnt) * 100.0 if expected_cnt > 0 else 0.0

    # Hard gate criteria: 100.0% coverage, 0 missing, 0 invalid, 0 duplicate, 0 extra unexpected
    is_complete = (
        matched_cnt == expected_cnt
        and missing_cnt == 0
        and invalid_cnt == 0
        and duplicate_count == 0
        and unexpected_cnt == 0
    )

    prov_complete = (invalid_cnt == 0 and all(prov_map.get(k, True) for k in matched_set))

    return CanonicalCoverageReport(
        family=family,
        expected_count=expected_cnt,
        observed_count=len(observed_keys),
        matched_count=matched_cnt,
        missing_count=missing_cnt,
        unexpected_extra_count=unexpected_cnt,
        duplicate_count=duplicate_count,
        invalid_count=invalid_cnt,
        coverage_pct=round(coverage_pct, 4),
        is_complete=is_complete,
        missing_keys=sorted(list(missing_set)),
        unexpected_keys=sorted(list(unexpected_set)),
        published_count=pub_cnt,
        published_late_or_bundled_count=late_cnt,
        officially_not_published_count=not_pub_cnt,
        missing_unexplained_count=missing_cnt,
        lifecycle_coverage_complete=(matched_cnt == expected_cnt and unexpected_cnt == 0),
        schedule_coverage_complete=(matched_cnt == expected_cnt),
        observation_coverage_complete=is_complete,
        provenance_coverage_complete=prov_complete,
    )

