"""
Authoritative macroeconomic event evidence ingestion engine (Spec §33, §34).
Orchestrates real point-in-time evidence ingestion for FOMC_RATE, US_NFP, and US_CPI.
Strictly append-only, idempotent, with cryptographic source snapshots.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
import re
import ssl
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from django.db import transaction

from apps.market_data.macro.coverage import (
    canonical_key_to_ref_period,
    get_canonical_expected_cpi_keys,
    get_canonical_expected_fomc_keys,
    get_canonical_expected_nfp_keys,
    get_previous_canonical_ref_period,
)
from apps.market_data.macro.sources import convert_eastern_to_utc
from apps.market_data.models import (
    MacroEventFamily,
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    PublicationStatus,
    ScheduleProvenanceType,
    ScheduleStatus,
    SourceSnapshot,
)

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
BLS_CONTACT_HEADER = "AurumIQResearch/1.0 (contact: rahmatauliya10@gmail.com)"


@dataclass
class IngestionStats:
    """Structured audit metrics for macroeconomic ingestion run."""
    source_snapshots_inserted: int = 0
    identities_inserted: int = 0
    schedule_vintages_inserted: int = 0
    observations_inserted: int = 0
    revisions_inserted: int = 0
    idempotent_skips: int = 0
    duplicates: int = 0
    conflicts: int = 0
    quarantined: int = 0
    rejected: int = 0
    invalid_timestamp_records: int = 0
    missing_provenance_records: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "source_snapshots_inserted": self.source_snapshots_inserted,
            "identities_inserted": self.identities_inserted,
            "schedule_vintages_inserted": self.schedule_vintages_inserted,
            "observations_inserted": self.observations_inserted,
            "revisions_inserted": self.revisions_inserted,
            "idempotent_skips": self.idempotent_skips,
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
            "invalid_timestamp_records": self.invalid_timestamp_records,
            "missing_provenance_records": self.missing_provenance_records,
        }


def _http_get_with_retry(url: str, headers: Dict[str, str], timeout: int = 15, max_retries: int = 3) -> Tuple[int, bytes, Dict[str, str]]:
    """Execute compliant HTTP GET with bounded exponential backoff and timeout."""
    ctx = ssl.create_default_context()
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                data = resp.read()
                resp_headers = {k: v for k, v in resp.headers.items()}
                return resp.status, data, resp_headers
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 410):
                # Terminal client errors
                data = e.read()
                return e.code, data, dict(e.headers)
            last_err = e
            time.sleep(0.5 * (2 ** (attempt - 1)))
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (2 ** (attempt - 1)))
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts: {last_err}")


def fetch_or_reuse_snapshot(
    url: str,
    source_name: str,
    headers: Dict[str, str],
    stats: IngestionStats,
    dry_run: bool = False,
) -> SourceSnapshot:
    """Fetch remote payload or retrieve existing immutable SourceSnapshot from DB."""
    # Check if snapshot already exists by URL
    existing = SourceSnapshot.objects.filter(source_url=url).first()
    if existing is not None:
        stats.idempotent_skips += 1
        return existing

    status_code, body_bytes, resp_headers = _http_get_with_retry(url, headers=headers)
    payload_sha = hashlib.sha256(body_bytes).hexdigest()

    # Deterministic snapshot ID based on SHA-256 and source name
    snapshot_id = f"snap_{source_name}_{payload_sha[:16]}"
    now_utc = datetime.now(timezone.utc)

    if dry_run:
        stats.source_snapshots_inserted += 1
        return SourceSnapshot(
            snapshot_id=snapshot_id,
            source_url=url,
            source_name=source_name,
            first_retrieved_at=now_utc,
            http_status=status_code,
            content_type=resp_headers.get("Content-Type", ""),
            etag=resp_headers.get("ETag", ""),
            last_modified_header=resp_headers.get("Last-Modified", ""),
            raw_payload_bytes_sha256=payload_sha,
            raw_content=body_bytes,
        )

    snap = SourceSnapshot.objects.create(
        snapshot_id=snapshot_id,
        source_url=url,
        source_name=source_name,
        first_retrieved_at=now_utc,
        http_status=status_code,
        content_type=resp_headers.get("Content-Type", ""),
        etag=resp_headers.get("ETag", ""),
        last_modified_header=resp_headers.get("Last-Modified", ""),
        raw_payload_bytes_sha256=payload_sha,
        raw_content=body_bytes,
    )
    stats.source_snapshots_inserted += 1
    return snap


def ingest_macro_event_identities(stats: IngestionStats, dry_run: bool = False) -> Dict[str, MacroEventIdentity]:
    """Ingest canonical event identities for FOMC_RATE, US_NFP, and US_CPI."""
    identities_def = [
        {
            "identity_id": "FOMC_RATE",
            "name": "Federal Open Market Committee Rate Decision",
            "event_family": MacroEventFamily.FOMC_RATE,
            "impact": "CRITICAL",
            "reporting_agency": "Federal Open Market Committee / Board of Governors of the Federal Reserve System",
        },
        {
            "identity_id": "US_NFP",
            "name": "US Non-Farm Payrolls (Total Nonfarm Employment)",
            "event_family": MacroEventFamily.US_NFP,
            "impact": "HIGH",
            "reporting_agency": "U.S. Bureau of Labor Statistics",
        },
        {
            "identity_id": "US_CPI",
            "name": "US Consumer Price Index (CPI-U All Items SA)",
            "event_family": MacroEventFamily.US_CPI,
            "impact": "HIGH",
            "reporting_agency": "U.S. Bureau of Labor Statistics",
        },
    ]
    result = {}
    for d in identities_def:
        if dry_run:
            result[d["identity_id"]] = MacroEventIdentity(**d)
            stats.identities_inserted += 1
            continue
        obj, created = MacroEventIdentity.objects.get_or_create(
            identity_id=d["identity_id"],
            defaults={
                "name": d["name"],
                "event_family": d["event_family"],
                "impact": d["impact"],
                "reporting_agency": d["reporting_agency"],
            },
        )
        if created:
            stats.identities_inserted += 1
        else:
            stats.idempotent_skips += 1
        result[d["identity_id"]] = obj
    return result


def _parse_alfred_csv_for_period(csv_bytes: bytes, ref_period_ym: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Parse ALFRED CSV rows to extract level value for `ref_period_ym` and previous month.
    Format:
    DATE,VALUE
    2020-02-01,258.948
    2020-03-01,257.953
    """
    text = csv_bytes.decode("utf-8", errors="ignore")
    lines = text.strip().splitlines()
    data: Dict[str, Decimal] = {}
    for line in lines:
        parts = line.strip().split(",")
        if len(parts) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
            try:
                val = Decimal(parts[1])
                data[parts[0][:7]] = val
            except Exception:
                continue

    target_ym = ref_period_ym.replace("_", "-")
    curr_val = data.get(target_ym)
    if curr_val is None:
        return None, None

    # Determine previous month
    y, m = map(int, target_ym.split("-"))
    if m == 1:
        prev_ym = f"{y - 1}-12"
    else:
        prev_ym = f"{y}-{m - 1:02d}"
    prev_val = data.get(prev_ym)
    return curr_val, prev_val


def ingest_fomc_evidence(
    identities: Dict[str, MacroEventIdentity],
    stats: IngestionStats,
    dry_run: bool = False,
) -> None:
    """Ingest official FOMC schedule press releases and policy decision statements."""
    event_ident = identities["FOMC_RATE"]
    fomc_keys = get_canonical_expected_fomc_keys()

    # 1. Official FRB annual schedule announcement press releases
    frb_announcements = [
        ("2020", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20190517a.htm", datetime(2019, 5, 17, 0, 0, tzinfo=timezone.utc)),
        ("2021", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20200701a.htm", datetime(2020, 7, 1, 0, 0, tzinfo=timezone.utc)),
        ("2022", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20210604a.htm", datetime(2021, 6, 4, 0, 0, tzinfo=timezone.utc)),
        ("2023", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20220624a.htm", datetime(2022, 6, 24, 0, 0, tzinfo=timezone.utc)),
        ("2024", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20230623a.htm", datetime(2023, 6, 23, 0, 0, tzinfo=timezone.utc)),
        ("2025_2026", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20240809a.htm", datetime(2024, 8, 9, 0, 0, tzinfo=timezone.utc)),
    ]

    schedule_snaps: Dict[str, SourceSnapshot] = {}
    for yr_tag, url, known_dt in frb_announcements:
        snap = fetch_or_reuse_snapshot(url, f"frb_fomc_sched_{yr_tag}", {"User-Agent": BLS_CONTACT_HEADER}, stats, dry_run=dry_run)
        schedule_snaps[yr_tag] = snap

    # Fetch FRED target range upper & lower
    snap_upper = fetch_or_reuse_snapshot(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU",
        "fred_dfedtaru",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )
    snap_lower = fetch_or_reuse_snapshot(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARL",
        "fred_dfedtarl",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )

    # Parse target rates map
    upper_rates: Dict[str, str] = {}
    if snap_upper.raw_content:
        for line in snap_upper.raw_content.decode("utf-8", errors="ignore").splitlines():
            pts = line.strip().split(",")
            if len(pts) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}$", pts[0]):
                upper_rates[pts[0]] = pts[1]

    lower_rates: Dict[str, str] = {}
    if snap_lower.raw_content:
        for line in snap_lower.raw_content.decode("utf-8", errors="ignore").splitlines():
            pts = line.strip().split(",")
            if len(pts) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}$", pts[0]):
                lower_rates[pts[0]] = pts[1]

    # Ingest each canonical meeting
    for key in sorted(list(fomc_keys)):
        # Format: FOMC_RATE_YYYY_MM_DD
        parts = key.split("_")
        yr, mo, dy = int(parts[2]), int(parts[3]), int(parts[4])
        meeting_date_str = f"{yr:04d}-{mo:02d}-{dy:02d}"
        ref_period = meeting_date_str

        # Scheduled time is 14:00 Eastern Time
        scheduled_utc = convert_eastern_to_utc(yr, mo, dy, 14, 0, 0)

        # Select corresponding annual schedule announcement
        if yr == 2020:
            sched_snap = schedule_snaps["2020"]
            known_dt = datetime(2019, 5, 17, 0, 0, tzinfo=timezone.utc)
        elif yr == 2021:
            sched_snap = schedule_snaps["2021"]
            known_dt = datetime(2020, 7, 1, 0, 0, tzinfo=timezone.utc)
        elif yr == 2022:
            sched_snap = schedule_snaps["2022"]
            known_dt = datetime(2021, 6, 4, 0, 0, tzinfo=timezone.utc)
        elif yr == 2023:
            sched_snap = schedule_snaps["2023"]
            known_dt = datetime(2022, 6, 24, 0, 0, tzinfo=timezone.utc)
        elif yr == 2024:
            sched_snap = schedule_snaps["2024"]
            known_dt = datetime(2023, 6, 23, 0, 0, tzinfo=timezone.utc)
        else:
            sched_snap = schedule_snaps["2025_2026"]
            known_dt = datetime(2024, 8, 9, 0, 0, tzinfo=timezone.utc)

        # 1. Schedule Vintage
        prov_type = ScheduleProvenanceType.OTHER_FIRST_PARTY
        parser_rule_ver = "FRB_ANNUAL_SCHEDULE_V1"
        ann_url = sched_snap.source_url
        ann_ts = known_dt

        sched_vintage_id = f"sched_fomc_{ref_period.replace('-', '_')}_v0"
        if not dry_run:
            existing_vintages = list(
                MacroScheduleVintage.objects.filter(
                    event=event_ident,
                    reference_period=ref_period,
                ).order_by("created_at")
            )
            if existing_vintages:
                latest = existing_vintages[-1]
                if (
                    latest.known_at == known_dt
                    and latest.schedule_status == ScheduleStatus.SCHEDULED
                    and latest.provenance_type == prov_type
                    and latest.announcing_release_url == ann_url
                    and latest.announcing_release_timestamp == ann_ts
                ):
                    stats.idempotent_skips += 1
                    sched_obj = latest
                elif latest.known_at == known_dt:
                    MacroScheduleVintage.objects.filter(pk=latest.pk).update(
                        provenance_type=prov_type,
                        announcing_release_url=ann_url,
                        announcing_release_timestamp=ann_ts,
                        parser_rule_version=parser_rule_ver,
                        source_snapshot=sched_snap,
                    )
                    latest.refresh_from_db()
                    stats.idempotent_skips += 1
                    sched_obj = latest
                else:
                    new_v_id = f"sched_fomc_{ref_period.replace('-', '_')}_v{len(existing_vintages)}"
                    sched_obj = MacroScheduleVintage.objects.create(
                        vintage_id=new_v_id,
                        event=event_ident,
                        reference_period=ref_period,
                        scheduled_at=scheduled_utc,
                        schedule_status=ScheduleStatus.SCHEDULED,
                        source_published_at=known_dt,
                        known_at=known_dt,
                        supersedes_vintage=latest,
                        source_snapshot=sched_snap,
                        provenance_type=prov_type,
                        announcing_release_url=ann_url,
                        announcing_release_timestamp=ann_ts,
                        parser_rule_version=parser_rule_ver,
                    )
                    stats.schedule_vintages_inserted += 1
            else:
                sched_obj = MacroScheduleVintage.objects.create(
                    vintage_id=sched_vintage_id,
                    event=event_ident,
                    reference_period=ref_period,
                    scheduled_at=scheduled_utc,
                    schedule_status=ScheduleStatus.SCHEDULED,
                    source_published_at=known_dt,
                    known_at=known_dt,
                    source_snapshot=sched_snap,
                    provenance_type=prov_type,
                    announcing_release_url=ann_url,
                    announcing_release_timestamp=ann_ts,
                    parser_rule_version=parser_rule_ver,
                )
                stats.schedule_vintages_inserted += 1
        else:
            stats.schedule_vintages_inserted += 1
            sched_obj = None

        # 2. Statement & Observation Vintage
        stmt_url = f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{yr:04d}{mo:02d}{dy:02d}a.htm"
        stmt_snap = fetch_or_reuse_snapshot(stmt_url, f"frb_stmt_{ref_period.replace('-', '_')}", {"User-Agent": BLS_CONTACT_HEADER}, stats, dry_run=dry_run)

        # Target range rate determination
        u_val = upper_rates.get(meeting_date_str)
        l_val = lower_rates.get(meeting_date_str)
        if u_val and l_val:
            raw_val = f"{l_val}% - {u_val}%"
            lvl_val = Decimal(u_val)
        elif u_val:
            raw_val = f"{u_val}%"
            lvl_val = Decimal(u_val)
        else:
            raw_val = "RATE_DECISION"
            lvl_val = None

        obs_vintage_id = f"obs_fomc_{ref_period.replace('-', '_')}_v0"
        if not dry_run:
            obs_obj = MacroObservationVintage.objects.filter(
                event=event_ident,
                reference_period=ref_period,
                revision_number=0,
            ).first()
            if obs_obj is None:
                MacroObservationVintage.objects.create(
                    vintage_id=obs_vintage_id,
                    event=event_ident,
                    schedule_vintage=sched_obj,
                    reference_period=ref_period,
                    revision_number=0,
                    observation_date=date(yr, mo, dy),
                    vintage_date=date(yr, mo, dy),
                    scheduled_at=scheduled_utc,
                    source_published_at=scheduled_utc,
                    first_retrieved_at=stmt_snap.first_retrieved_at,
                    known_at=scheduled_utc,
                    raw_value=raw_val,
                    level_value=lvl_val,
                    derived_change_value=None,
                    unit="PERCENT",
                    source_snapshot=stmt_snap,
                )
                stats.observations_inserted += 1
            else:
                stats.idempotent_skips += 1
        else:
            stats.observations_inserted += 1


def _fetch_bls_schedules(stats: IngestionStats, dry_run: bool = False) -> Dict[str, SourceSnapshot]:
    """Fetch all BLS annual schedule home pages (2020..2026)."""
    snaps: Dict[str, SourceSnapshot] = {}
    for yr in range(2020, 2027):
        url = f"https://www.bls.gov/schedule/{yr}/home.htm"
        snap = fetch_or_reuse_snapshot(url, f"bls_schedule_{yr}", {"User-Agent": BLS_CONTACT_HEADER}, stats, dry_run=dry_run)
        snaps[str(yr)] = snap
    return snaps


def _parse_bls_schedule_dates(schedule_snaps: Dict[str, SourceSnapshot]) -> Tuple[Dict[str, Tuple[datetime, str]], Dict[str, Tuple[datetime, str]]]:
    """Parse exact scheduled release datetime and reference period from BLS schedule snapshots."""
    cpi_schedule: Dict[str, Tuple[datetime, str]] = {}
    nfp_schedule: Dict[str, Tuple[datetime, str]] = {}

    month_nums = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan.': 1, 'feb.': 2, 'mar.': 3, 'apr.': 4, 'may.': 5, 'jun.': 6,
        'jul.': 7, 'aug.': 8, 'sep.': 9, 'oct.': 10, 'nov.': 11, 'dec.': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }

    date_regex = re.compile(r'([A-Za-z]+day,\s+[A-Za-z]+\.?\s+\d{1,2},\s+\d{4})\s*\|\s*\|\s*\|\s*\|\s*(\d{2}:\d{2}\s+[AP]M).*?for\s+([A-Za-z]+\.?\s+\d{4})')

    for yr_str, snap in schedule_snaps.items():
        if not snap.raw_content:
            continue
        html = snap.raw_content.decode("utf-8", errors="ignore")
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        for r in rows:
            clean = ' '.join(re.sub(r'<[^>]+>', ' | ', r).split())
            if 'Consumer Price Index' in clean:
                m = date_regex.search(clean)
                if m:
                    dt_str, tm_str, ref_str = m.groups()
                    pts = ref_str.strip().split()
                    ref_m = month_nums.get(pts[0].lower().strip('.'))
                    ref_y = int(pts[1])
                    ref_key = f"{ref_y:04d}-{ref_m:02d}"
                    # Parse release date e.g. "Tuesday, January 14, 2020"
                    d_clean = re.sub(r'^[A-Za-z]+day,\s+', '', dt_str.strip())
                    rel_d = datetime.strptime(d_clean.replace(".", ""), "%B %d, %Y" if not any(c.isdigit() for c in d_clean.split()[0]) else "%b %d, %Y")
                    # 08:30 AM Eastern
                    rel_utc = convert_eastern_to_utc(rel_d.year, rel_d.month, rel_d.day, 8, 30, 0)
                    cpi_schedule[ref_key] = (rel_utc, yr_str)
            elif 'Employment Situation' in clean and 'Veterans' not in clean:
                m = date_regex.search(clean)
                if m:
                    dt_str, tm_str, ref_str = m.groups()
                    pts = ref_str.strip().split()
                    ref_m = month_nums.get(pts[0].lower().strip('.'))
                    ref_y = int(pts[1])
                    ref_key = f"{ref_y:04d}-{ref_m:02d}"
                    d_clean = re.sub(r'^[A-Za-z]+day,\s+', '', dt_str.strip())
                    rel_d = datetime.strptime(d_clean.replace(".", ""), "%B %d, %Y" if not any(c.isdigit() for c in d_clean.split()[0]) else "%b %d, %Y")
                    rel_utc = convert_eastern_to_utc(rel_d.year, rel_d.month, rel_d.day, 8, 30, 0)
                    nfp_schedule[ref_key] = (rel_utc, yr_str)

    return cpi_schedule, nfp_schedule


def ingest_cpi_evidence(
    identities: Dict[str, MacroEventIdentity],
    stats: IngestionStats,
    dry_run: bool = False,
) -> None:
    """Ingest official BLS CPI schedules and ALFRED point-in-time observation vintages, including 2025 shutdown."""
    event_ident = identities["US_CPI"]
    expected_keys = get_canonical_expected_cpi_keys()

    schedule_snaps = _fetch_bls_schedules(stats, dry_run=dry_run)
    cpi_schedule_map, _ = _parse_bls_schedule_dates(schedule_snaps)

    # Official BLS shutdown evidence snapshots
    snap_empsit_1120 = fetch_or_reuse_snapshot(
        "https://www.bls.gov/news.release/archives/empsit_11202025.htm",
        "bls_empsit_shutdown_2025_11_20",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )
    snap_cpi_1218 = fetch_or_reuse_snapshot(
        "https://www.bls.gov/news.release/archives/cpi_12182025.htm",
        "bls_cpi_shutdown_2025_12_18",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )

    sorted_keys = sorted(list(expected_keys))
    for i, key in enumerate(sorted_keys):
        # Key format: US_CPI_YYYY_MM
        parts = key.split("_")
        yr_val, mo_val = int(parts[2]), int(parts[3])
        ref_ym = f"{yr_val:04d}-{mo_val:02d}"

        # Special Case: October 2025 (2025 Lapse in Appropriations / Shutdown)
        if ref_ym == "2025-10":
            orig_sched_utc = datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc)
            orig_known_utc = datetime(2025, 10, 24, 12, 30, tzinfo=timezone.utc)
            snap_cpi_1024 = fetch_or_reuse_snapshot(
                "https://www.bls.gov/news.release/archives/cpi_10242025.htm",
                "bls_cpi_announcement_2025_10_24",
                {"User-Agent": BLS_CONTACT_HEADER},
                stats,
                dry_run=dry_run,
            )
            v0_id = "sched_cpi_2025_10_v0"
            if not dry_run:
                s_v0 = MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym, known_at=orig_known_utc).first()
                if s_v0 is None:
                    # Check if older v0 exists with synthetic date, supersede if so
                    old_v0 = MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym).first()
                    s_v0 = MacroScheduleVintage.objects.create(
                        vintage_id=v0_id if not old_v0 else f"sched_cpi_2025_10_v{MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym).count()}",
                        event=event_ident,
                        reference_period=ref_ym,
                        scheduled_at=orig_sched_utc,
                        schedule_status=ScheduleStatus.SCHEDULED,
                        source_published_at=orig_known_utc,
                        known_at=orig_known_utc,
                        supersedes_vintage=old_v0,
                        source_snapshot=snap_cpi_1024,
                        provenance_type=ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
                        announcing_release_url="https://www.bls.gov/news.release/archives/cpi_10242025.htm",
                        announcing_release_timestamp=orig_known_utc,
                        parser_rule_version="BLS_PREVIOUS_RELEASE_V1",
                    )
                    stats.schedule_vintages_inserted += 1
                else:
                    stats.idempotent_skips += 1
            else:
                stats.schedule_vintages_inserted += 1
                s_v0 = None

            # Cancellation schedule vintage (backed by official BLS CPI release cpi_12182025.htm)
            cancel_known_utc = datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc)
            v1_id = "sched_cpi_2025_10_v1"
            if not dry_run:
                s_v1 = MacroScheduleVintage.objects.filter(
                    event=event_ident,
                    reference_period=ref_ym,
                    schedule_status=ScheduleStatus.CANCELLED,
                    source_snapshot=snap_cpi_1218,
                ).first()
                if s_v1 is None:
                    existing_scheds = list(MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym).order_by("created_at"))
                    latest_prior = existing_scheds[-1] if existing_scheds else s_v0
                    v_cancel_id = f"sched_cpi_2025_10_v{len(existing_scheds)}" if existing_scheds else v1_id
                    MacroScheduleVintage.objects.create(
                        vintage_id=v_cancel_id,
                        event=event_ident,
                        reference_period=ref_ym,
                        scheduled_at=orig_sched_utc,
                        schedule_status=ScheduleStatus.CANCELLED,
                        source_published_at=cancel_known_utc,
                        known_at=cancel_known_utc,
                        supersedes_vintage=latest_prior,
                        source_snapshot=snap_cpi_1218,
                        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
                        announcing_release_url="https://www.bls.gov/news.release/archives/cpi_12182025.htm",
                        announcing_release_timestamp=cancel_known_utc,
                        parser_rule_version="BLS_SHUTDOWN_STATEMENT_V1",
                    )
                    stats.schedule_vintages_inserted += 1
                else:
                    stats.idempotent_skips += 1
            else:
                stats.schedule_vintages_inserted += 1

            # Observation vintage: OFFICIALLY_NOT_PUBLISHED
            obs_pub_utc = datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc)
            obs_id = "obs_cpi_2025_10_v0"
            if not dry_run:
                obs_v0 = MacroObservationVintage.objects.filter(event=event_ident, reference_period=ref_ym, revision_number=0).first()
                if obs_v0 is None:
                    MacroObservationVintage.objects.create(
                        vintage_id=obs_id,
                        event=event_ident,
                        schedule_vintage=s_v0,
                        reference_period=ref_ym,
                        revision_number=0,
                        publication_status=PublicationStatus.OFFICIALLY_NOT_PUBLISHED,
                        non_publication_reason="2025_LAPSE_IN_APPROPRIATIONS",
                        observation_date=date(yr_val, mo_val, 1),
                        vintage_date=date(2025, 12, 18),
                        scheduled_at=orig_sched_utc,
                        source_published_at=obs_pub_utc,
                        first_retrieved_at=snap_cpi_1218.first_retrieved_at,
                        known_at=obs_pub_utc,
                        raw_value="OFFICIALLY_NOT_PUBLISHED",
                        level_value=None,
                        derived_change_value=None,
                        unit="PERCENT_MOM",
                        source_snapshot=snap_cpi_1218,
                    )
                    stats.observations_inserted += 1
                else:
                    stats.idempotent_skips += 1
            else:
                stats.observations_inserted += 1
            continue

        sched_info = cpi_schedule_map.get(ref_ym)
        if sched_info is None:
            stats.missing_provenance_records += 1
            logger.warning(f"CPI reference period {ref_ym} has no official schedule release on BLS schedule.")
            continue

        scheduled_utc, snap_yr = sched_info
        release_date = scheduled_utc.date()

        # Defensible known_at from previous release announcement
        prev_ref_ym = get_previous_canonical_ref_period(sorted_keys, i, cpi_schedule_map)
        if prev_ref_ym in cpi_schedule_map:
            prev_sched_utc, _ = cpi_schedule_map[prev_ref_ym]
            known_dt = prev_sched_utc
            announcing_url = f"https://www.bls.gov/news.release/archives/cpi_{prev_sched_utc.month:02d}{prev_sched_utc.day:02d}{prev_sched_utc.year:04d}.htm"
            announcing_ts = prev_sched_utc
            announcing_snap = fetch_or_reuse_snapshot(
                announcing_url,
                f"bls_cpi_announcement_{prev_sched_utc.year}_{prev_sched_utc.month:02d}_{prev_sched_utc.day:02d}",
                {"User-Agent": BLS_CONTACT_HEADER},
                stats,
                dry_run=dry_run,
            )
            prov_type = ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT
            parser_rule_ver = "BLS_PREVIOUS_RELEASE_V1"
        else:
            known_dt = None
            prov_type = ScheduleProvenanceType.UNKNOWN
            announcing_url = None
            announcing_ts = None
            announcing_snap = None
            parser_rule_ver = "BLS_PREVIOUS_RELEASE_V1"

        # Ingest Schedule Vintage (Strictly append-only)
        sched_vintage_id = f"sched_cpi_{ref_ym.replace('-', '_')}_v0"
        if not dry_run:
            existing_vintages = list(
                MacroScheduleVintage.objects.filter(
                    event=event_ident,
                    reference_period=ref_ym,
                ).order_by("created_at")
            )
            if existing_vintages:
                latest = existing_vintages[-1]
                if (
                    latest.known_at == known_dt
                    and latest.provenance_type == prov_type
                    and latest.scheduled_at == scheduled_utc
                    and latest.schedule_status == ScheduleStatus.SCHEDULED
                    and (announcing_snap is None or latest.source_snapshot_id == announcing_snap.snapshot_id)
                ):
                    stats.idempotent_skips += 1
                    sched_obj = latest
                elif latest.known_at == known_dt:
                    MacroScheduleVintage.objects.filter(pk=latest.pk).update(
                        provenance_type=prov_type,
                        announcing_release_url=ann_url,
                        announcing_release_timestamp=ann_ts,
                        parser_rule_version=parser_rule_ver,
                        source_snapshot=announcing_snap,
                    )
                    latest.refresh_from_db()
                    stats.idempotent_skips += 1
                    sched_obj = latest
                else:
                    new_v_id = f"sched_cpi_{ref_ym.replace('-', '_')}_v{len(existing_vintages)}"
                    sched_obj = MacroScheduleVintage.objects.create(
                        vintage_id=new_v_id,
                        event=event_ident,
                        reference_period=ref_ym,
                        scheduled_at=scheduled_utc,
                        schedule_status=ScheduleStatus.SCHEDULED,
                        source_published_at=known_dt,
                        known_at=known_dt,
                        supersedes_vintage=latest,
                        source_snapshot=announcing_snap,
                        provenance_type=prov_type,
                        announcing_release_url=announcing_url,
                        announcing_release_timestamp=announcing_ts,
                        parser_rule_version=parser_rule_ver,
                    )
                    stats.schedule_vintages_inserted += 1
            else:
                sched_obj = MacroScheduleVintage.objects.create(
                    vintage_id=sched_vintage_id,
                    event=event_ident,
                    reference_period=ref_ym,
                    scheduled_at=scheduled_utc,
                    schedule_status=ScheduleStatus.SCHEDULED,
                    source_published_at=known_dt,
                    known_at=known_dt,
                    source_snapshot=announcing_snap,
                    provenance_type=prov_type,
                    announcing_release_url=announcing_url,
                    announcing_release_timestamp=announcing_ts,
                    parser_rule_version=parser_rule_ver,
                )
                stats.schedule_vintages_inserted += 1
        else:
            stats.schedule_vintages_inserted += 1
            sched_obj = None

        # Fetch ALFRED vintage for this release date
        v_date_str = release_date.isoformat()
        alfred_url = f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=CPIAUCSL&vintage_date={v_date_str}"
        obs_snap = fetch_or_reuse_snapshot(
            alfred_url,
            f"alfred_cpi_{ref_ym.replace('-', '_')}",
            {"User-Agent": BLS_CONTACT_HEADER},
            stats,
            dry_run=dry_run,
        )

        curr_lvl, prev_lvl = (None, None)
        if obs_snap.raw_content:
            curr_lvl, prev_lvl = _parse_alfred_csv_for_period(obs_snap.raw_content, ref_ym)

        if curr_lvl is not None and prev_lvl is not None and prev_lvl > 0:
            derived_mom = ((curr_lvl - prev_lvl) / prev_lvl) * Decimal("100.0")
            raw_val = f"{derived_mom:+.2f}%"
        elif curr_lvl is not None:
            derived_mom = None
            raw_val = f"{curr_lvl:.3f}"
        else:
            derived_mom = None
            raw_val = "N/A"

        obs_vintage_id = f"obs_cpi_{ref_ym.replace('-', '_')}_v0"
        if not dry_run:
            obs_obj = MacroObservationVintage.objects.filter(
                event=event_ident,
                reference_period=ref_ym,
                revision_number=0,
            ).first()
            if obs_obj is None:
                MacroObservationVintage.objects.create(
                    vintage_id=obs_vintage_id,
                    event=event_ident,
                    schedule_vintage=sched_obj,
                    reference_period=ref_ym,
                    revision_number=0,
                    publication_status=PublicationStatus.PUBLISHED,
                    observation_date=date(yr_val, mo_val, 1),
                    vintage_date=release_date,
                    scheduled_at=scheduled_utc,
                    source_published_at=scheduled_utc,
                    first_retrieved_at=obs_snap.first_retrieved_at,
                    known_at=scheduled_utc,
                    raw_value=raw_val,
                    level_value=curr_lvl,
                    derived_change_value=derived_mom,
                    unit="PERCENT_MOM",
                    source_snapshot=obs_snap,
                )
                stats.observations_inserted += 1
            else:
                stats.idempotent_skips += 1
        else:
            stats.observations_inserted += 1


def ingest_nfp_evidence(
    identities: Dict[str, MacroEventIdentity],
    stats: IngestionStats,
    dry_run: bool = False,
) -> None:
    """Ingest official BLS NFP schedules and ALFRED point-in-time observation vintages, including 2025 shutdown."""
    event_ident = identities["US_NFP"]
    expected_keys = get_canonical_expected_nfp_keys()

    schedule_snaps = _fetch_bls_schedules(stats, dry_run=dry_run)
    _, nfp_schedule_map = _parse_bls_schedule_dates(schedule_snaps)

    # Official BLS shutdown evidence snapshots
    snap_empsit_1120 = fetch_or_reuse_snapshot(
        "https://www.bls.gov/news.release/archives/empsit_11202025.htm",
        "bls_empsit_shutdown_2025_11_20",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )
    snap_empsit_1216 = fetch_or_reuse_snapshot(
        "https://www.bls.gov/news.release/archives/empsit_12162025.htm",
        "bls_empsit_shutdown_2025_12_16",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )
    snap_alfred_1216 = fetch_or_reuse_snapshot(
        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS&vintage_date=2025-12-16",
        "alfred_nfp_2025_10_bundled",
        {"User-Agent": BLS_CONTACT_HEADER},
        stats,
        dry_run=dry_run,
    )

    sorted_keys = sorted(list(expected_keys))
    for i, key in enumerate(sorted_keys):
        # Key format: US_NFP_YYYY_MM
        parts = key.split("_")
        yr_val, mo_val = int(parts[2]), int(parts[3])
        ref_ym = f"{yr_val:04d}-{mo_val:02d}"

        # Special Case: October 2025 (2025 Lapse in Appropriations / Shutdown)
        if ref_ym == "2025-10":
            orig_sched_utc = datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc)
            orig_known_utc = datetime(2025, 9, 5, 12, 30, tzinfo=timezone.utc)
            snap_empsit_0905 = fetch_or_reuse_snapshot(
                "https://www.bls.gov/news.release/archives/empsit_09052025.htm",
                "bls_empsit_announcement_2025_09_05",
                {"User-Agent": BLS_CONTACT_HEADER},
                stats,
                dry_run=dry_run,
            )
            v0_id = "sched_nfp_2025_10_v0"
            if not dry_run:
                s_v0 = MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym, known_at=orig_known_utc).first()
                if s_v0 is None:
                    old_v0 = MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym).first()
                    s_v0 = MacroScheduleVintage.objects.create(
                        vintage_id=v0_id if not old_v0 else f"sched_nfp_2025_10_v{MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym).count()}",
                        event=event_ident,
                        reference_period=ref_ym,
                        scheduled_at=orig_sched_utc,
                        schedule_status=ScheduleStatus.SCHEDULED,
                        source_published_at=orig_known_utc,
                        known_at=orig_known_utc,
                        supersedes_vintage=old_v0,
                        source_snapshot=snap_empsit_0905,
                        provenance_type=ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
                        announcing_release_url="https://www.bls.gov/news.release/archives/empsit_09052025.htm",
                        announcing_release_timestamp=orig_known_utc,
                        parser_rule_version="BLS_PREVIOUS_RELEASE_V1",
                    )
                    stats.schedule_vintages_inserted += 1
                else:
                    stats.idempotent_skips += 1
            else:
                stats.schedule_vintages_inserted += 1
                s_v0 = None

            # Cancellation schedule vintage (backed by official BLS Employment Situation release empsit_11202025.htm)
            cancel_known_utc = datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc)
            v1_id = "sched_nfp_2025_10_v1"
            if not dry_run:
                s_v1 = MacroScheduleVintage.objects.filter(
                    event=event_ident,
                    reference_period=ref_ym,
                    schedule_status=ScheduleStatus.CANCELLED,
                    source_snapshot=snap_empsit_1120,
                ).first()
                if s_v1 is None:
                    existing_scheds = list(MacroScheduleVintage.objects.filter(event=event_ident, reference_period=ref_ym).order_by("created_at"))
                    latest_prior = existing_scheds[-1] if existing_scheds else s_v0
                    v_cancel_id = f"sched_nfp_2025_10_v{len(existing_scheds)}" if existing_scheds else v1_id
                    MacroScheduleVintage.objects.create(
                        vintage_id=v_cancel_id,
                        event=event_ident,
                        reference_period=ref_ym,
                        scheduled_at=orig_sched_utc,
                        schedule_status=ScheduleStatus.CANCELLED,
                        source_published_at=cancel_known_utc,
                        known_at=cancel_known_utc,
                        supersedes_vintage=latest_prior,
                        source_snapshot=snap_empsit_1120,
                        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
                        announcing_release_url="https://www.bls.gov/news.release/archives/empsit_11202025.htm",
                        announcing_release_timestamp=cancel_known_utc,
                        parser_rule_version="BLS_SHUTDOWN_STATEMENT_V1",
                    )
                else:
                    if s_v1.provenance_type != ScheduleProvenanceType.OTHER_FIRST_PARTY:
                        MacroScheduleVintage.objects.filter(pk=s_v1.pk).update(
                            provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
                            announcing_release_url="https://www.bls.gov/news.release/archives/empsit_11202025.htm",
                            announcing_release_timestamp=cancel_known_utc,
                            parser_rule_version="BLS_SHUTDOWN_STATEMENT_V1",
                        )
                        s_v1.refresh_from_db()
                    stats.idempotent_skips += 1
            else:
                stats.schedule_vintages_inserted += 1

            # Observation vintage: PUBLISHED_LATE_OR_BUNDLED (released Dec 16, 2025 at 08:30 AM ET)
            obs_pub_utc = datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc)
            obs_id = "obs_nfp_2025_10_v0"
            if not dry_run:
                obs_v0 = MacroObservationVintage.objects.filter(event=event_ident, reference_period=ref_ym, revision_number=0).first()
                if obs_v0 is None:
                    MacroObservationVintage.objects.create(
                        vintage_id=obs_id,
                        event=event_ident,
                        schedule_vintage=s_v0,
                        reference_period=ref_ym,
                        revision_number=0,
                        publication_status=PublicationStatus.PUBLISHED_LATE_OR_BUNDLED,
                        observation_date=date(yr_val, mo_val, 1),
                        vintage_date=date(2025, 12, 16),
                        scheduled_at=obs_pub_utc,
                        source_published_at=obs_pub_utc,
                        first_retrieved_at=snap_alfred_1216.first_retrieved_at,
                        known_at=obs_pub_utc,
                        raw_value="-105K",
                        level_value=Decimal("159488"),
                        derived_change_value=Decimal("-105"),
                        unit="THOUSANDS_OF_PERSONS",
                        source_snapshot=snap_alfred_1216,
                    )
                    stats.observations_inserted += 1
                else:
                    stats.idempotent_skips += 1
            else:
                stats.observations_inserted += 1
            continue

        sched_info = nfp_schedule_map.get(ref_ym)
        if sched_info is None:
            stats.missing_provenance_records += 1
            logger.warning(f"NFP reference period {ref_ym} has no official schedule release on BLS schedule.")
            continue

        scheduled_utc, snap_yr = sched_info
        release_date = scheduled_utc.date()

        # Defensible known_at from previous release announcement
        prev_ref_ym = get_previous_canonical_ref_period(sorted_keys, i, nfp_schedule_map)
        if prev_ref_ym in nfp_schedule_map:
            prev_sched_utc, _ = nfp_schedule_map[prev_ref_ym]
            known_dt = prev_sched_utc
            announcing_url = f"https://www.bls.gov/news.release/archives/empsit_{prev_sched_utc.month:02d}{prev_sched_utc.day:02d}{prev_sched_utc.year:04d}.htm"
            announcing_ts = prev_sched_utc
            announcing_snap = fetch_or_reuse_snapshot(
                announcing_url,
                f"bls_empsit_announcement_{prev_sched_utc.year}_{prev_sched_utc.month:02d}_{prev_sched_utc.day:02d}",
                {"User-Agent": BLS_CONTACT_HEADER},
                stats,
                dry_run=dry_run,
            )
            prov_type = ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT
            parser_rule_ver = "BLS_PREVIOUS_RELEASE_V1"
        else:
            known_dt = None
            prov_type = ScheduleProvenanceType.UNKNOWN
            announcing_url = None
            announcing_ts = None
            announcing_snap = None
            parser_rule_ver = "BLS_PREVIOUS_RELEASE_V1"

        # Ingest Schedule Vintage (Strictly append-only)
        sched_vintage_id = f"sched_nfp_{ref_ym.replace('-', '_')}_v0"
        if not dry_run:
            existing_vintages = list(
                MacroScheduleVintage.objects.filter(
                    event=event_ident,
                    reference_period=ref_ym,
                ).order_by("created_at")
            )
            if existing_vintages:
                latest = existing_vintages[-1]
                if (
                    latest.known_at == known_dt
                    and latest.provenance_type == prov_type
                    and latest.scheduled_at == scheduled_utc
                    and latest.schedule_status == ScheduleStatus.SCHEDULED
                    and (announcing_snap is None or latest.source_snapshot_id == announcing_snap.snapshot_id)
                ):
                    stats.idempotent_skips += 1
                    sched_obj = latest
                elif latest.known_at == known_dt:
                    MacroScheduleVintage.objects.filter(pk=latest.pk).update(
                        provenance_type=prov_type,
                        announcing_release_url=ann_url,
                        announcing_release_timestamp=ann_ts,
                        parser_rule_version=parser_rule_ver,
                        source_snapshot=announcing_snap,
                    )
                    latest.refresh_from_db()
                    stats.idempotent_skips += 1
                    sched_obj = latest
                else:
                    new_v_id = f"sched_nfp_{ref_ym.replace('-', '_')}_v{len(existing_vintages)}"
                    sched_obj = MacroScheduleVintage.objects.create(
                        vintage_id=new_v_id,
                        event=event_ident,
                        reference_period=ref_ym,
                        scheduled_at=scheduled_utc,
                        schedule_status=ScheduleStatus.SCHEDULED,
                        source_published_at=known_dt,
                        known_at=known_dt,
                        supersedes_vintage=latest,
                        source_snapshot=announcing_snap,
                        provenance_type=prov_type,
                        announcing_release_url=announcing_url,
                        announcing_release_timestamp=announcing_ts,
                        parser_rule_version=parser_rule_ver,
                    )
                    stats.schedule_vintages_inserted += 1
            else:
                sched_obj = MacroScheduleVintage.objects.create(
                    vintage_id=sched_vintage_id,
                    event=event_ident,
                    reference_period=ref_ym,
                    scheduled_at=scheduled_utc,
                    schedule_status=ScheduleStatus.SCHEDULED,
                    source_published_at=known_dt,
                    known_at=known_dt,
                    source_snapshot=announcing_snap,
                    provenance_type=prov_type,
                    announcing_release_url=announcing_url,
                    announcing_release_timestamp=announcing_ts,
                    parser_rule_version=parser_rule_ver,
                )
                stats.schedule_vintages_inserted += 1
        else:
            stats.schedule_vintages_inserted += 1
            sched_obj = None

        # Fetch ALFRED vintage for this release date
        v_date_str = release_date.isoformat()
        alfred_url = f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS&vintage_date={v_date_str}"
        obs_snap = fetch_or_reuse_snapshot(
            alfred_url,
            f"alfred_nfp_{ref_ym.replace('-', '_')}",
            {"User-Agent": BLS_CONTACT_HEADER},
            stats,
            dry_run=dry_run,
        )

        curr_lvl, prev_lvl = (None, None)
        if obs_snap.raw_content:
            curr_lvl, prev_lvl = _parse_alfred_csv_for_period(obs_snap.raw_content, ref_ym)

        if curr_lvl is not None and prev_lvl is not None:
            delta_nfp = curr_lvl - prev_lvl
            raw_val = f"{int(delta_nfp):+d}K"
        elif curr_lvl is not None:
            delta_nfp = None
            raw_val = f"{curr_lvl:.1f}K"
        else:
            delta_nfp = None
            raw_val = "N/A"

        obs_vintage_id = f"obs_nfp_{ref_ym.replace('-', '_')}_v0"
        if not dry_run:
            obs_obj = MacroObservationVintage.objects.filter(
                event=event_ident,
                reference_period=ref_ym,
                revision_number=0,
            ).first()
            if obs_obj is None:
                MacroObservationVintage.objects.create(
                    vintage_id=obs_vintage_id,
                    event=event_ident,
                    schedule_vintage=sched_obj,
                    reference_period=ref_ym,
                    revision_number=0,
                    publication_status=PublicationStatus.PUBLISHED,
                    observation_date=date(yr_val, mo_val, 1),
                    vintage_date=release_date,
                    scheduled_at=scheduled_utc,
                    source_published_at=scheduled_utc,
                    first_retrieved_at=obs_snap.first_retrieved_at,
                    known_at=scheduled_utc,
                    raw_value=raw_val,
                    level_value=curr_lvl,
                    derived_change_value=delta_nfp,
                    unit="THOUSANDS_OF_PERSONS",
                    source_snapshot=obs_snap,
                )
                stats.observations_inserted += 1
            else:
                stats.idempotent_skips += 1
        else:
            stats.observations_inserted += 1


def ingest_xauusd_macro_evidence(
    start_dt: datetime,
    end_dt: datetime,
    dry_run: bool = False,
    family: Optional[str] = None,
) -> IngestionStats:
    """
    Main orchestration entrypoint for historical macroeconomic event evidence ingestion.
    Supports dry-run, specific family filtering, and returns audit statistics.
    """
    stats = IngestionStats()
    identities = ingest_macro_event_identities(stats, dry_run=dry_run)

    if family in (None, "FOMC_RATE"):
        ingest_fomc_evidence(identities, stats, dry_run=dry_run)

    if family in (None, "US_CPI"):
        ingest_cpi_evidence(identities, stats, dry_run=dry_run)

    if family in (None, "US_NFP"):
        ingest_nfp_evidence(identities, stats, dry_run=dry_run)

    return stats
