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

    # Validation: OFFICIALLY_NOT_PUBLISHED must have defensible provenance
    for k, status in status_map.items():
        if status == "OFFICIALLY_NOT_PUBLISHED":
            if prov_map.get(k) is False:
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

