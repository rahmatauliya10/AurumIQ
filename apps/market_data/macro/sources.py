"""Macroeconomic data source adapter interfaces and conflict resolution rules."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from apps.market_data.models import (
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    ScheduleStatus,
    SourceSnapshot,
)

NY_TZ = ZoneInfo("America/New_York")


def convert_eastern_to_utc(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    """Explicitly converts Eastern Time (EDT/EST) to timezone-aware UTC datetime."""
    local_dt = datetime(year, month, day, hour, minute, second, tzinfo=NY_TZ)
    return local_dt.astimezone(timezone.utc)


def compute_payload_sha256(raw_bytes: bytes) -> str:
    """Compute deterministic cryptographic SHA-256 hash over raw payload bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


class ConflictResolution(str, Enum):
    """Conflict resolution actions enforcing immutability and data integrity."""
    IDEMPOTENT_SKIP = "IDEMPOTENT_SKIP"
    QUARANTINE = "QUARANTINE"
    APPEND_REVISION = "APPEND_REVISION"


def resolve_conflict_action(
    existing_sha256: Optional[str],
    candidate_sha256: str,
    is_later_official_publication: bool = False,
) -> ConflictResolution:
    """
    Evaluate deterministic conflict resolution rule:
    1. same immutable source-version key + same hash = idempotent skip;
    2. same immutable source-version key + different bytes = quarantine;
    3. later official vintage/publication for the same reference period = valid append-only revision.
    """
    if existing_sha256 is None:
        return ConflictResolution.APPEND_REVISION

    if is_later_official_publication:
        return ConflictResolution.APPEND_REVISION

    if existing_sha256.lower() == candidate_sha256.lower():
        return ConflictResolution.IDEMPOTENT_SKIP

    return ConflictResolution.QUARANTINE


@dataclass(frozen=True)
class ParsedObservationRecord:
    """Structured observation parsed from authoritative source payload."""
    event_id: str
    reference_period: str
    revision_number: int
    scheduled_at: datetime
    source_published_at: datetime
    vintage_date: Optional[date]
    known_at: datetime
    raw_value: str
    level_value: Optional[Decimal]
    derived_change_value: Optional[Decimal]
    unit: str


@dataclass(frozen=True)
class ParsedScheduleRecord:
    """Structured schedule vintage parsed from official calendar announcement."""
    event_id: str
    reference_period: str
    scheduled_at: datetime
    schedule_status: ScheduleStatus
    source_published_at: Optional[datetime]
    known_at: datetime


class BaseMacroSourceAdapter(ABC):
    """
    Abstract contract for macroeconomic authoritative data providers.
    Enforces interface separation without running full/production ingestion.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the source provider (e.g. 'alfred_bls', 'frb_fomc')."""
        pass

    @property
    @abstractmethod
    def target_family(self) -> str:
        """Target macro event family ('US_CPI', 'US_NFP', 'FOMC_RATE')."""
        pass

    @abstractmethod
    def parse_schedule_vintage(self, raw_bytes: bytes, metadata: Dict[str, Any]) -> List[ParsedScheduleRecord]:
        """Parse raw announcement payload into structured schedule vintages."""
        pass

    @abstractmethod
    def parse_observation_vintage(self, raw_bytes: bytes, metadata: Dict[str, Any]) -> List[ParsedObservationRecord]:
        """Parse raw release payload into structured observation vintages."""
        pass
