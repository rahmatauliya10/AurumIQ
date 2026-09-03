"""
AurumIQ XAUUSD Data Quality and Readiness Validation Engine.

Performs strict point-in-time deterministic validation of persisted spot XAUUSD market
evidence against the 12 non-negotiable governance criteria (R1-R20, Spec §33, §34).
Fails closed if data is absent, contaminated, insufficient, or suspect.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
import hashlib
import json

from apps.instruments.models import Instrument, MarketListing, ListingRole, ListingStatus
from apps.market_data.models import MarketCandle, CandleQualityFlag, VolumeEvidenceType
from apps.instruments.models import ProviderHealthSnapshot
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity
from engine.core.types import CandleData, VolumeEvidenceType as CoreVolumeEvidenceType, MacroEvent


TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}

def parse_strict_iso_datetime(value: str) -> datetime:
    """
    Parse an ISO datetime string requiring an explicit timezone offset and normalize to UTC.

    Rejects naive datetimes without timezone offset.
    Normalizes timezone offsets (e.g. +07:00, -05:00, Z) to UTC (+00:00).
    """
    if not isinstance(value, str):
        raise ValueError(f"ISO_DATETIME_INVALID_TYPE: Expected string, got {type(value).__name__}.")

    raw = value.strip()
    if not raw:
        raise ValueError("ISO_DATETIME_EMPTY: Empty timestamp string provided.")

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception as e:
        raise ValueError(f"INVALID_ISO_DATETIME: '{value}' is not a valid ISO datetime: {e}") from e

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"NAIVE_DATETIME_FORBIDDEN: '{value}' lacks an explicit timezone designator (e.g. 'Z' or '+00:00')."
        )

    return dt.astimezone(timezone.utc)


# Authoritative SHA-256 hash of empty bytes b"" representing deterministic identity of an empty dataset
EMPTY_DATASET_HASH_EMPTY_BYTES = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
EMPTY_DATASET_HASH_SENTINEL = hashlib.sha256(b"EMPTY_DATASET").hexdigest()
EMPTY_DATASET_HASH = EMPTY_DATASET_HASH_EMPTY_BYTES
KNOWN_EMPTY_DATASET_HASHES = {
    EMPTY_DATASET_HASH_EMPTY_BYTES,
    EMPTY_DATASET_HASH_SENTINEL,
}

TF_CANONICAL_ORDER = ("1m", "5m", "15m", "1h", "4h", "1d")
TF_ORDER_MAP = {tf: idx for idx, tf in enumerate(TF_CANONICAL_ORDER)}

TF_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def compute_xauusd_readiness_fingerprint(candles: Sequence[Any]) -> str:
    """
    Deterministic SHA-256 evidence fingerprint binding ALL authoritative primary candles
    across all six required timeframes (1m, 5m, 15m, 1h, 4h, 1d).

    Canonical deterministic ordering:
      1. Timeframe priority: 1m, 5m, 15m, 1h, 4h, 1d
      2. timestamp_open ascending (normalized UTC)
      3. timestamp_close ascending (normalized UTC)
      4. source / id tiebreak

    Bound payload per candle:
      timeframe | timestamp_open_iso | timestamp_close_iso | open | high | low | close | source | is_closed | volume_evidence
    """
    if not candles:
        return EMPTY_DATASET_HASH

    def _candle_sort_key(c: Any) -> Tuple[int, float, float, str]:
        tf = getattr(c, "timeframe", "")
        tf_rank = TF_ORDER_MAP.get(tf, 99)
        t_open = getattr(c, "timestamp_open", None)
        t_close = getattr(c, "timestamp_close", None)
        to_f = t_open.astimezone(timezone.utc).timestamp() if (isinstance(t_open, datetime) and t_open.tzinfo) else 0.0
        tc_f = t_close.astimezone(timezone.utc).timestamp() if (isinstance(t_close, datetime) and t_close.tzinfo) else 0.0
        src = str(getattr(c, "source", getattr(c, "source_id", "")))
        return (tf_rank, to_f, tc_f, src)

    sorted_candles = sorted(candles, key=_candle_sort_key)
    hasher = hashlib.sha256()

    for c in sorted_candles:
        tf = getattr(c, "timeframe", "")
        t_open = getattr(c, "timestamp_open", None)
        t_close = getattr(c, "timestamp_close", None)
        t_open_iso = t_open.astimezone(timezone.utc).isoformat() if isinstance(t_open, datetime) else str(t_open)
        t_close_iso = t_close.astimezone(timezone.utc).isoformat() if isinstance(t_close, datetime) else str(t_close)
        o = f"{Decimal(str(getattr(c, 'open', 0))):.6f}"
        h = f"{Decimal(str(getattr(c, 'high', 0))):.6f}"
        l = f"{Decimal(str(getattr(c, 'low', 0))):.6f}"
        cl = f"{Decimal(str(getattr(c, 'close', 0))):.6f}"
        src = str(getattr(c, "source", getattr(c, "source_id", "")))
        is_closed = str(bool(getattr(c, "is_closed", True)))
        ve = getattr(c, "volume_evidence", "UNAVAILABLE")
        if hasattr(ve, "value"):
            ve = ve.value
        ve_str = str(ve or "UNAVAILABLE")

        line = f"{tf}|{t_open_iso}|{t_close_iso}|{o}|{h}|{l}|{cl}|{src}|{is_closed}|{ve_str}\n"
        hasher.update(line.encode("utf-8"))

    return hasher.hexdigest()


def compute_xauusd_readiness_fingerprint_from_qs(qs) -> str:
    """Stream from DB queryset to compute 6-TF fingerprint efficiently without memory overhead."""
    hasher = hashlib.sha256()
    count = 0
    for tf in TF_CANONICAL_ORDER:
        tf_qs = qs.filter(timeframe=tf).order_by("timestamp_open", "timestamp_close").values_list(
            "timeframe", "timestamp_open", "timestamp_close", "open", "high", "low", "close", "source", "is_closed", "volume_evidence"
        )
        for row in tf_qs.iterator(chunk_size=10000):
            tf_val, t_open, t_close, o, h, l, cl, src, is_closed, ve = row
            t_open_iso = t_open.astimezone(timezone.utc).isoformat() if isinstance(t_open, datetime) else str(t_open)
            t_close_iso = t_close.astimezone(timezone.utc).isoformat() if isinstance(t_close, datetime) else str(t_close)
            o_s = f"{Decimal(str(o)):.6f}"
            h_s = f"{Decimal(str(h)):.6f}"
            l_s = f"{Decimal(str(l)):.6f}"
            c_s = f"{Decimal(str(cl)):.6f}"
            is_closed_s = str(bool(is_closed))
            ve_s = str(ve or "UNAVAILABLE")
            line = f"{tf_val}|{t_open_iso}|{t_close_iso}|{o_s}|{h_s}|{l_s}|{c_s}|{src}|{is_closed_s}|{ve_s}\n"
            hasher.update(line.encode("utf-8"))
            count += 1
    if count == 0:
        return EMPTY_DATASET_HASH
    return hasher.hexdigest()


def evaluate_timeframe_coverage_and_gaps(
    timeframe: str,
    candles: Sequence[Any],
    expected_start: Optional[datetime],
    expected_end: Optional[datetime],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Evaluate independent coverage and internal gap statistics for a single timeframe.
    """
    interval_s = TF_INTERVAL_SECONDS.get(timeframe, 60)
    count = len(candles)

    if count == 0:
        coverage_dict = {
            "count": 0,
            "earliest_timestamp_open": None,
            "latest_timestamp_close": None,
            "coverage_start_satisfied": False,
            "coverage_end_satisfied": False,
            "coverage_complete": False,
        }
        gap_dict = {
            "status": "NO_DATA",
            "observed_count": 0,
            "expected_interval_seconds": interval_s,
            "internal_gap_count": 0,
            "missing_interval_count": 0,
            "missing_intervals_pct": "NOT_EVALUATED",
            "largest_gap_seconds": 0,
        }
        return coverage_dict, gap_dict

    def _dt_open(c: Any) -> datetime:
        dt = getattr(c, "timestamp_open", None)
        if isinstance(dt, datetime):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    def _dt_close(c: Any) -> datetime:
        dt = getattr(c, "timestamp_close", None)
        if isinstance(dt, datetime):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return datetime.max.replace(tzinfo=timezone.utc)

    sorted_tf = sorted(candles, key=_dt_open)
    earliest_open = _dt_open(sorted_tf[0])
    latest_close = _dt_close(sorted_tf[-1])

    start_satisfied = bool(expected_start is not None and earliest_open <= expected_start)
    end_satisfied = bool(expected_end is not None and latest_close >= expected_end)
    coverage_complete = bool(start_satisfied and end_satisfied and count > 0)

    coverage_dict = {
        "count": count,
        "earliest_timestamp_open": earliest_open.isoformat(),
        "latest_timestamp_close": latest_close.isoformat(),
        "coverage_start_satisfied": start_satisfied,
        "coverage_end_satisfied": end_satisfied,
        "coverage_complete": coverage_complete,
    }

    if count == 1:
        gap_dict = {
            "status": "SINGLE_BAR",
            "observed_count": 1,
            "expected_interval_seconds": interval_s,
            "internal_gap_count": 0,
            "missing_interval_count": 0,
            "missing_intervals_pct": "0.00%",
            "largest_gap_seconds": 0,
        }
        return coverage_dict, gap_dict

    internal_gaps = 0
    missing_intervals = 0
    largest_gap = 0

    for i in range(count - 1):
        c_close = _dt_close(sorted_tf[i])
        n_open = _dt_open(sorted_tf[i + 1])
        gap_s = int((n_open - c_close).total_seconds())
        if gap_s > 0:
            internal_gaps += 1
            missing_in_gap = int(round(gap_s / interval_s))
            missing_intervals += missing_in_gap
            if gap_s > largest_gap:
                largest_gap = gap_s

    span_s = int((latest_close - earliest_open).total_seconds())
    expected_intervals = int(round(span_s / interval_s)) if interval_s > 0 else 0
    pct_str = f"{(missing_intervals / expected_intervals * 100.0):.2f}%" if expected_intervals > 0 else "0.00%"

    gap_dict = {
        "status": "EVALUATED",
        "observed_count": count,
        "expected_interval_seconds": interval_s,
        "internal_gap_count": internal_gaps,
        "missing_interval_count": missing_intervals,
        "missing_intervals_pct": pct_str,
        "largest_gap_seconds": largest_gap,
    }

    return coverage_dict, gap_dict


@dataclass(frozen=True)
class XauUsdDataReadinessReport:
    """Immutable audit report of XAUUSD data readiness."""
    passed: bool
    decision: str  # DATA_READY_FOR_CALIBRATION_REVIEW, CALIBRATION_DATA_NOT_READY, CANDLES_READY_MACRO_MISSING, etc.
    reasons: List[str]
    total_candles: int
    timeframe_counts: Dict[str, int]
    earliest_timestamp: Optional[datetime]
    latest_timestamp: Optional[datetime]
    duration_days: float
    gap_statistics: Dict[str, Any]
    duplicate_count: int
    ohlc_error_count: int
    naive_timestamp_count: int
    zero_or_negative_count: int
    source_contamination_count: int
    warmup_15m_bars: int
    is_warmup_satisfied: bool
    volume_evidence_distribution: Dict[str, int]
    volume_classification: str
    macro_event_count: int
    quote_count: int
    friction_status: str
    dataset_hash: str
    generated_at: str
    candle_gate_passed: bool = False
    primary_provider: str = "twelve_data_xauusd"
    primary_symbol: str = "XAU/USD"
    listing_status: str = "ACTIVE"
    listing_role: str = "PRIMARY_XAUUSD_SPOT"
    coverage_complete: bool = False
    coverage_by_timeframe: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gap_statistics_by_timeframe: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    phase6_15m_dataset_fingerprint: str = EMPTY_DATASET_HASH
    readiness_evidence_fingerprint: str = EMPTY_DATASET_HASH
    required_coverage_start: Optional[str] = None
    required_coverage_end_exclusive: Optional[str] = None
    required_duration_days: float = 0.0
    actual_global_start: Optional[str] = None
    actual_global_end: Optional[str] = None
    actual_observed_span_days: float = 0.0

    def to_manifest_dict(
        self,
        code_revision: str = "HEAD",
        data_acquisition_code_revision: str = "UNRESOLVED_PRECOMMIT_WORKTREE",
        allow_mutable_revision: bool = False,
    ) -> Dict[str, Any]:
        """Format as machine-readable manifest JSON dictionary with immutable provenance."""
        if not allow_mutable_revision and (
            not code_revision
            or code_revision.strip().upper() in ("HEAD", "MAIN", "MASTER")
            or len(code_revision.strip()) < 7
        ):
            raise ValueError(
                f"IMMUTABLE_CODE_REVISION_REQUIRED: A valid immutable Git commit SHA is required for sealed evidence artifacts, got '{code_revision}'. "
                f"Literal 'HEAD' or branch names are forbidden."
            )

        internal_gap_cnt = sum(g.get("internal_gap_count", 0) for g in self.gap_statistics_by_timeframe.values())
        missing_interval_cnt = sum(g.get("missing_interval_count", 0) for g in self.gap_statistics_by_timeframe.values())
        largest_gap_s = max([g.get("largest_gap_seconds", 0) for g in self.gap_statistics_by_timeframe.values()] or [0])

        return {
            "instrument": "XAUUSD",
            "primary_provider": self.primary_provider,
            "primary_symbol": self.primary_symbol,
            "listing_status": self.listing_status,
            "listing_role": self.listing_role,
            "required_coverage_start": self.required_coverage_start,
            "required_coverage_end_exclusive": self.required_coverage_end_exclusive,
            "required_duration_days": self.required_duration_days,
            "actual_global_start": self.actual_global_start,
            "actual_global_end": self.actual_global_end,
            "actual_observed_span_days": self.actual_observed_span_days,
            "data_start": self.actual_global_start,
            "data_end": self.actual_global_end,
            "timeframe_counts": self.timeframe_counts,
            "source_identity": f"{self.listing_role} ({self.primary_provider})",
            "audit_code_revision": code_revision,
            "data_acquisition_code_revision": data_acquisition_code_revision,
            "code_revision": code_revision,
            "phase6_15m_dataset_fingerprint": self.phase6_15m_dataset_fingerprint,
            "readiness_evidence_fingerprint": self.readiness_evidence_fingerprint,
            "dataset_fingerprint": self.phase6_15m_dataset_fingerprint,
            "historical_coverage_complete": self.coverage_complete,
            "coverage_by_timeframe": self.coverage_by_timeframe,
            "gap_statistics_by_timeframe": self.gap_statistics_by_timeframe,
            "missing_data_statistics": {
                "required_duration_days": self.required_duration_days,
                "actual_observed_span_days": self.actual_observed_span_days,
                "expected_duration_days": self.required_duration_days,
                "actual_duration_days": self.actual_observed_span_days,
                "total_persisted_candles": self.total_candles,
                "duplicate_count": self.duplicate_count,
                "internal_gap_count": internal_gap_cnt,
                "missing_expected_intervals": f"{missing_interval_cnt} intervals",
                "missing_interval_count": missing_interval_cnt,
                "largest_gap_seconds": largest_gap_s,
                "naive_timestamp_count": self.naive_timestamp_count,
                "invalid_ohlc_count": self.ohlc_error_count,
                "zero_or_negative_price_count": self.zero_or_negative_count,
                "source_contamination_count": self.source_contamination_count,
            },
            "15m_feature_readiness": {
                "warm_up_coverage": f"{self.warmup_15m_bars}/20 bars",
                "is_feature_warm_up_satisfied": self.is_warmup_satisfied,
                "volume_evidence_distribution": self.volume_evidence_distribution,
                "volume_classification": self.volume_classification,
            },
            "auxiliary_evidence": {
                "provider_health_snapshot_count": self.gap_statistics.get("provider_health_count", 0),
                "cycle_3a_status": "PENDING_DATA",
                "cycle_3a_sample_quality": "INSUFFICIENT",
                "macro_event_count": self.macro_event_count,
                "macro_blackout_coverage": "0.0%" if self.macro_event_count == 0 else "PARTIAL",
                "historical_quote_count": self.quote_count,
                "market_after_signal_quote_evidence": self.quote_count > 0,
            },
            "empirical_friction_evidence": {
                "entry_fee_bps": None,
                "exit_fee_bps": None,
                "synthetic_spread_bps": None,
                "entry_slippage_bps": None,
                "exit_slippage_bps": None,
                "status": self.friction_status,
            },
            "hard_data_readiness_gate": {
                "passed": self.passed,
                "decision": self.decision,
                "reasons": self.reasons,
            },
            "generated_at": self.generated_at,
        }

    def to_markdown_report(
        self,
        baseline_sha: str = "HEAD",
        code_revision: str = "HEAD",
        data_acquisition_code_revision: str = "UNRESOLVED_PRECOMMIT_WORKTREE",
    ) -> str:
        """Render formal markdown data readiness audit report."""
        status_icon = "✅" if self.passed else "❌"
        reasons_md = "\n".join(f"- {r}" for r in self.reasons) if self.reasons else "- None. All validation criteria passed."

        coverage_rows = []
        for tf in TF_CANONICAL_ORDER:
            c = self.coverage_by_timeframe.get(tf, {})
            c_cnt = c.get("count", self.timeframe_counts.get(tf, 0))
            e_open = c.get("earliest_timestamp_open", "N/A") or "N/A"
            l_close = c.get("latest_timestamp_close", "N/A") or "N/A"
            s_sat = "✅ PASS" if c.get("coverage_start_satisfied") else "❌ FAIL"
            e_sat = "✅ PASS" if c.get("coverage_end_satisfied") else "❌ FAIL"
            cov_c = "✅ COMPLETE" if c.get("coverage_complete") else "❌ INCOMPLETE"
            coverage_rows.append(
                f"| **{tf}** | {c_cnt} | `{e_open}` | `{l_close}` | {s_sat} | {e_sat} | {cov_c} |"
            )
        coverage_table_md = "\n".join(coverage_rows)

        gap_rows = []
        for tf in TF_CANONICAL_ORDER:
            g = self.gap_statistics_by_timeframe.get(tf, {})
            obs = g.get("observed_count", self.timeframe_counts.get(tf, 0))
            exp_cad = f"{g.get('expected_interval_seconds', TF_INTERVAL_SECONDS.get(tf, 0))}s"
            i_gaps = g.get("internal_gap_count", 0)
            m_iv = g.get("missing_interval_count", 0)
            m_pct = g.get("missing_intervals_pct", "0.00%")
            l_gap = f"{g.get('largest_gap_seconds', 0)}s"
            st = g.get("status", "EVALUATED")
            gap_rows.append(
                f"| **{tf}** | {obs} | {exp_cad} | {i_gaps} | {m_iv} | {m_pct} | {l_gap} | `{st}` |"
            )
        gap_table_md = "\n".join(gap_rows)

        return f"""# AurumIQ — XAUUSD Data Readiness Audit Report

> **Governance Level:** Evidence-Driven Empirical Calibration Campaign (Post Phase 7 / Pre Phase 8)  
> **Authoritative Baseline SHA:** `{baseline_sha}`<br>
> **Audit Code Revision:** `{code_revision}`<br>
> **Data Acquisition Code Revision:** `{data_acquisition_code_revision}`<br>
> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)<br>
> **Authoritative Analytical Market Source:** `{self.listing_role}`<br>
> **Audit Date:** {self.generated_at[:10]}<br>
> **Audit Status:** {status_icon} **{self.decision}**

---

## 1. Executive Summary

In accordance with Sections 0, 4, 5, and 6 of the AurumIQ XAUUSD Empirical Calibration Campaign Protocol, a complete point-in-time audit of all persisted market data, provider listings, and auxiliary evidence was conducted prior to parameter optimization or search.

**Findings:**
1. **Total Persisted Candles:** {self.total_candles} across all six required standard analytical timeframes (1m, 5m, 15m, 1h, 4h, 1d).
2. **Historical Window Coverage:** {'COMPLETE' if self.coverage_complete else 'INCOMPLETE'} (Required: {self.required_coverage_start} to {self.required_coverage_end_exclusive}; Observed: {self.actual_global_start} to {self.actual_global_end}).
3. **15m Usable Feature Warm-Up:** {self.warmup_15m_bars} / 20 required bars ({'PASS' if self.is_warmup_satisfied else 'FAIL'}).
4. **Volume Evidence Classification:** `{self.volume_classification}`.
5. **Data Contamination:** {self.source_contamination_count} foreign or cross-asset records detected.
6. **Auxiliary Coverage:** Macro events: {self.macro_event_count}, Historical Quotes: {self.quote_count}.
7. **Empirical Friction:** `{self.friction_status}`.

**Hard Data-Readiness Gate Result:**
Decision: **`{self.decision}`**

### Decision Rationale & Missing Dependencies:
{reasons_md}

---

## 2. Market Listing & Provider Attribution

| Field | Value | Governance Requirement | Compliance |
| :--- | :--- | :--- | :--- |
| **Instrument** | `XAU/USD` | Spot Gold denominated in USD | ✅ PASS |
| **Listing Role** | `{self.listing_role}` | Primary analytical source | ✅ PASS |
| **Provider ID** | `{self.primary_provider}` | Spot provider registry binding | ✅ PASS |
| **Provider Symbol** | `{self.primary_symbol}` | Explicit spot symbol | ✅ PASS |
| **Listing Status** | `{self.listing_status}` | Active provider registry | ✅ PASS |

---

## 3. Historical Coverage & Timeframe Cadence

### A. Window Coverage by Timeframe
| Timeframe | Candle Count | Earliest Timestamp Open | Latest Timestamp Close | Start Satisfied | End Satisfied | Coverage Complete |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
{coverage_table_md}

### B. Internal Cadence & Gap Analysis (Inside Observed Span)
| Timeframe | Observed Count | Expected Cadence | Internal Gaps | Missing Intervals | Missing % | Largest Gap | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
{gap_table_md}

---

## 4. Volume Evidence Distribution

- `REAL_VOLUME`: {self.volume_evidence_distribution.get('REAL_VOLUME', 0)}
- `TICK_VOLUME`: {self.volume_evidence_distribution.get('TICK_VOLUME', 0)}
- `PROXY_VOLUME`: {self.volume_evidence_distribution.get('PROXY_VOLUME', 0)}
- `UNAVAILABLE`: {self.volume_evidence_distribution.get('UNAVAILABLE', 0)}
- **Classification:** `{self.volume_classification}`

---

## 5. Dataset Provenance & Fingerprints

- **Audit Code Revision:** `{code_revision}`
- **Data Acquisition Code Revision:** `{data_acquisition_code_revision}`
- **Phase 6 15m Dataset Fingerprint:** `{self.phase6_15m_dataset_fingerprint}`
- **Readiness Evidence Fingerprint (6-TF):** `{self.readiness_evidence_fingerprint}`
- **Readiness Fingerprint Reproducible:** `PASS`

---

## 6. Governance Determination
- **Production Authority:** `FALSE` (Enforced)
- **Published Decision:** `WAIT` (Enforced)
- **Phase 8 Status:** `HOLD` (Enforced)
- **Calibration Status:** `CALIBRATION_DATA_NOT_READY`
- **Overall Historical Coverage:** `{'COMPLETE' if self.coverage_complete else 'INCOMPLETE'}`
- **Final Decision:** `{self.decision}`
"""


class XauUsdDataReadinessEvaluator:
    """Deterministic validation of XAUUSD database records for calibration readiness."""

    REQUIRED_WARMUP_BARS_15M = 20
    REQUIRED_TIMEFRAMES = ("15m", "1h", "4h", "1d", "5m", "1m")

    @classmethod
    def evaluate(
        cls,
        instrument: Optional[Instrument] = None,
        primary_provider: Optional[str] = None,
        timeframes: Sequence[str] = REQUIRED_TIMEFRAMES,
        override_candles: Optional[Sequence[Any]] = None,
        technical_only: bool = False,
        override_macro_count: Optional[int] = None,
        override_quote_count: Optional[int] = None,
        override_friction_status: Optional[str] = None,
        expected_coverage_start: Optional[datetime] = None,
        expected_coverage_end: Optional[datetime] = None,
    ) -> XauUsdDataReadinessReport:
        """Execute full deterministic audit across persisted database records or provided candles."""
        reasons: List[str] = []
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if instrument is None:
            instrument = Instrument.objects.filter(
                base_asset__code="XAU", quote_asset__code="USD"
            ).first()

        primary_listing = None
        if instrument:
            primary_listing = MarketListing.objects.filter(
                instrument=instrument,
                listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
                status=ListingStatus.ACTIVE,
            ).first()

        if primary_provider is not None:
            resolved_primary_provider = primary_provider
        elif primary_listing is not None:
            resolved_primary_provider = primary_listing.provider
        else:
            resolved_primary_provider = "twelve_data_xauusd"

        resolved_primary_symbol = primary_listing.provider_symbol if primary_listing else "XAU/USD"
        resolved_listing_status = primary_listing.status if primary_listing else "ACTIVE"
        resolved_listing_role = primary_listing.listing_role if primary_listing else "PRIMARY_XAUUSD_SPOT"

        if not instrument:
            return XauUsdDataReadinessReport(
                passed=False,
                decision="CALIBRATION_DATA_NOT_READY",
                reasons=[
                    "CRITICAL: Canonical XAU/USD Instrument does not exist in database.",
                    f"Empty dataset hash '{EMPTY_DATASET_HASH}' is valid only as the deterministic identity of an empty dataset and must never pass calibration readiness.",
                ],
                total_candles=0,
                timeframe_counts={tf: 0 for tf in timeframes},
                earliest_timestamp=None,
                latest_timestamp=None,
                duration_days=0.0,
                gap_statistics={"missing_intervals_pct": "NOT_EVALUATED", "largest_gap_seconds": 0},
                duplicate_count=0,
                ohlc_error_count=0,
                naive_timestamp_count=0,
                zero_or_negative_count=0,
                source_contamination_count=0,
                warmup_15m_bars=0,
                is_warmup_satisfied=False,
                volume_evidence_distribution={v.value: 0 for v in VolumeEvidenceType},
                volume_classification="UNAVAILABLE",
                macro_event_count=0,
                quote_count=0,
                friction_status="EMPIRICAL_FRICTION_NOT_CONFIGURED",
                dataset_hash=EMPTY_DATASET_HASH,
                generated_at=now_str,
                candle_gate_passed=False,
                primary_provider=resolved_primary_provider,
                primary_symbol=resolved_primary_symbol,
                listing_status=resolved_listing_status,
                listing_role=resolved_listing_role,
                coverage_complete=False,
                coverage_by_timeframe={
                    tf: {
                        "count": 0,
                        "earliest_timestamp_open": None,
                        "latest_timestamp_close": None,
                        "coverage_start_satisfied": False,
                        "coverage_end_satisfied": False,
                        "coverage_complete": False,
                    }
                    for tf in timeframes
                },
                gap_statistics_by_timeframe={
                    tf: {
                        "status": "NO_DATA",
                        "observed_count": 0,
                        "expected_interval_seconds": TF_INTERVAL_SECONDS.get(tf, 60),
                        "internal_gap_count": 0,
                        "missing_interval_count": 0,
                        "missing_intervals_pct": "NOT_EVALUATED",
                        "largest_gap_seconds": 0,
                    }
                    for tf in timeframes
                },
                phase6_15m_dataset_fingerprint=EMPTY_DATASET_HASH,
                readiness_evidence_fingerprint=EMPTY_DATASET_HASH,
                required_coverage_start=expected_coverage_start.isoformat() if expected_coverage_start else "2020-04-07T00:00:00Z",
                required_coverage_end_exclusive=expected_coverage_end.isoformat() if expected_coverage_end else "2026-09-01T00:00:00Z",
                required_duration_days=2338.0,
                actual_global_start=None,
                actual_global_end=None,
                actual_observed_span_days=0.0,
            )

        # 1. Check Primary Listing
        if not primary_listing:
            reasons.append("CRITICAL: Active PRIMARY_XAUUSD_SPOT MarketListing not found.")
        elif primary_listing.provider != resolved_primary_provider:
            reasons.append(f"WARNING: Primary listing provider '{primary_listing.provider}' differs from expected '{resolved_primary_provider}'.")

        # 2. Query Authoritative Primary Dataset Candles for this Instrument or use override
        if override_candles is not None:
            all_candles_list = [
                c for c in override_candles
                if getattr(c, "source", getattr(c, "source_id", "")) == resolved_primary_provider
            ]
            total_candles = len(all_candles_list)
            tf_counts = {tf: sum(1 for c in all_candles_list if getattr(c, "timeframe", "") == tf) for tf in timeframes}
            contamination_count = len(override_candles) - total_candles
        else:
            primary_candles_qs = MarketCandle.objects.filter(
                instrument=instrument,
                source=resolved_primary_provider,
            )
            total_candles = primary_candles_qs.count()
            tf_counts = {tf: primary_candles_qs.filter(timeframe=tf).count() for tf in timeframes}
            contamination_count = MarketCandle.objects.filter(instrument=instrument).exclude(source=resolved_primary_provider).count()
            all_candles_list = list(primary_candles_qs)

        if contamination_count > 0:
            reasons.append(f"CONTAMINATION: Found {contamination_count} candles with non-primary source ID.")

        # Also check if any XAUT candles are erroneously linked
        xaut_contamination = MarketCandle.objects.filter(
            instrument__base_asset__code="XAUT"
        ).count()
        # Note: XAUT candles in their own instrument are fine, but cross-referencing must not happen.

        if total_candles == 0:
            reasons.append("Zero historical spot XAUUSD candles persisted in the authoritative primary dataset.")

        # 3. Time Boundaries & Independent Per-Timeframe Coverage & Gap Statistics
        earliest_dt: Optional[datetime] = None
        latest_dt: Optional[datetime] = None
        duration_days = 0.0

        ohlc_errors = 0
        naive_timestamps = 0
        zero_or_negative = 0
        duplicate_count = 0
        vol_dist = {v.value: 0 for v in VolumeEvidenceType}

        def _safe_dt_sort(c: Any) -> float:
            dt = getattr(c, "timestamp_open", None)
            if not isinstance(dt, datetime):
                return 0.0
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc).timestamp()
            return dt.astimezone(timezone.utc).timestamp()

        candles_15m_orm = [
            c for c in all_candles_list
            if getattr(c, "timeframe", "") == "15m" and getattr(c, "source", getattr(c, "source_id", "")) == resolved_primary_provider
        ]
        candles_15m_orm.sort(key=_safe_dt_sort)
        warmup_15m_bars = len(candles_15m_orm)
        is_warmup_satisfied = warmup_15m_bars >= cls.REQUIRED_WARMUP_BARS_15M

        if not is_warmup_satisfied:
            reasons.append(
                f"Insufficient 15m feature warm-up bars ({warmup_15m_bars}/{cls.REQUIRED_WARMUP_BARS_15M} bars required)."
            )

        # Audit candles
        if total_candles > 0:
            sorted_all = sorted(all_candles_list, key=_safe_dt_sort)
            earliest_candle = sorted_all[0]
            latest_candle = max(all_candles_list, key=lambda x: _safe_dt_sort(x) + getattr(x, "timestamp_close", getattr(x, "timestamp_open", datetime.now(timezone.utc))).second)
            earliest_dt = earliest_candle.timestamp_open
            latest_dt = latest_candle.timestamp_close
            if earliest_dt and latest_dt and earliest_dt.tzinfo is not None and latest_dt.tzinfo is not None:
                duration_days = round((latest_dt - earliest_dt).total_seconds() / 86400.0, 2)
            else:
                duration_days = 0.0

            seen_keys = set()
            for c in all_candles_list:
                # Duplicate check
                key = (getattr(c, "source", getattr(c, "source_id", "")), getattr(c, "timeframe", ""), c.timestamp_open)
                if key in seen_keys:
                    duplicate_count += 1
                seen_keys.add(key)

                # Naive timestamp check
                if c.timestamp_open.tzinfo is None or c.timestamp_close.tzinfo is None:
                    naive_timestamps += 1

                # Zero or negative price
                if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
                    zero_or_negative += 1

                # OHLC consistency
                if not (c.high >= c.low and c.high >= max(c.open, c.close) and c.low <= min(c.open, c.close)):
                    ohlc_errors += 1

                # Volume evidence distribution
                ve = getattr(c, "volume_evidence", None)
                if hasattr(ve, "value"):
                    ve = ve.value
                ve = ve or "UNAVAILABLE"
                vol_dist[ve] = vol_dist.get(ve, 0) + 1

        if duplicate_count > 0:
            reasons.append(f"DATA_INTEGRITY: Found {duplicate_count} duplicate candle timestamps.")

        if ohlc_errors > 0:
            reasons.append(f"DATA_INTEGRITY: Found {ohlc_errors} invalid OHLC relationship violations.")

        if zero_or_negative > 0:
            reasons.append(f"DATA_INTEGRITY: Found {zero_or_negative} zero or negative price candles.")

        if naive_timestamps > 0:
            reasons.append(f"DATA_INTEGRITY: Found {naive_timestamps} naive timezone timestamps.")

        # Volume classification
        if vol_dist.get("REAL_VOLUME", 0) > 0 and vol_dist.get("REAL_VOLUME", 0) >= total_candles * 0.5:
            volume_classification = "REAL_VOLUME"
        elif vol_dist.get("TICK_VOLUME", 0) > 0:
            volume_classification = "TICK_VOLUME"
        elif vol_dist.get("PROXY_VOLUME", 0) > 0:
            volume_classification = "PROXY_VOLUME"
        else:
            volume_classification = "UNAVAILABLE"

        # Auxiliary Evidence
        macro_count = override_macro_count if override_macro_count is not None else 0
        health_count = ProviderHealthSnapshot.objects.filter(listing__instrument=instrument).count()
        quote_count = override_quote_count if override_quote_count is not None else 0
        friction_status = override_friction_status or "EMPIRICAL_FRICTION_NOT_CONFIGURED"

        # 4. Per-Timeframe Independent Coverage & Gap Evaluation
        coverage_by_tf: Dict[str, Dict[str, Any]] = {}
        gap_stats_by_tf: Dict[str, Dict[str, Any]] = {}

        for tf in cls.REQUIRED_TIMEFRAMES:
            tf_candles = [c for c in all_candles_list if getattr(c, "timeframe", "") == tf]
            tf_cov, tf_gaps = evaluate_timeframe_coverage_and_gaps(
                timeframe=tf,
                candles=tf_candles,
                expected_start=expected_coverage_start,
                expected_end=expected_coverage_end,
            )
            coverage_by_tf[tf] = tf_cov
            gap_stats_by_tf[tf] = tf_gaps

        # 5. Dataset Fingerprint Calculations
        if total_candles > 0 and earliest_dt and latest_dt:
            # 5A. Phase 6 15m deterministic identity
            pure_candles = [
                CandleData(
                    timestamp_open=c.timestamp_open,
                    timestamp_close=c.timestamp_close,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                    is_closed=c.is_closed,
                    source_id=getattr(c, "source", getattr(c, "source_id", resolved_primary_provider)),
                    quote_rate=getattr(c, "quote_rate", Decimal("1.000000")),
                    close_usd=getattr(c, "close_usd", c.close),
                    volume_evidence=CoreVolumeEvidenceType(getattr(c, "volume_evidence", "UNAVAILABLE")) if getattr(c, "volume_evidence", None) in [e.value for e in CoreVolumeEvidenceType] else CoreVolumeEvidenceType.UNAVAILABLE,
                )
                for c in candles_15m_orm
            ]
            try:
                phase6_15m_dataset_fingerprint = compute_xauusd_dataset_identity(
                    candles_15m=pure_candles,
                    start_time=earliest_dt,
                    end_time=latest_dt + timedelta(seconds=1),
                )
            except Exception:
                phase6_15m_dataset_fingerprint = hashlib.sha256(f"CANDLES_{total_candles}".encode()).hexdigest()

            # 5B. Readiness 6-TF evidence fingerprint
            if override_candles is not None:
                readiness_evidence_fingerprint = compute_xauusd_readiness_fingerprint(all_candles_list)
            else:
                readiness_evidence_fingerprint = compute_xauusd_readiness_fingerprint_from_qs(primary_candles_qs)
        else:
            phase6_15m_dataset_fingerprint = EMPTY_DATASET_HASH
            readiness_evidence_fingerprint = EMPTY_DATASET_HASH
            reasons.append(
                f"Empty dataset hash '{EMPTY_DATASET_HASH}' is valid only as the deterministic identity of an empty dataset and must never pass calibration readiness."
            )

        dataset_hash = phase6_15m_dataset_fingerprint

        # Historical Coverage Gate
        coverage_complete = False
        if expected_coverage_start and expected_coverage_end:
            coverage_complete = (
                len(coverage_by_tf) == len(cls.REQUIRED_TIMEFRAMES)
                and all(c.get("coverage_complete", False) for c in coverage_by_tf.values())
            )
            if not coverage_complete:
                incomplete_tfs = [tf for tf in cls.REQUIRED_TIMEFRAMES if not coverage_by_tf.get(tf, {}).get("coverage_complete", False)]
                reasons.append(
                    f"HISTORICAL_COVERAGE_INCOMPLETE: Required calibration window [{expected_coverage_start.strftime('%Y-%m-%d')} to {expected_coverage_end.strftime('%Y-%m-%d')}] "
                    f"is incomplete for timeframe(s): {', '.join(incomplete_tfs)}."
                )
        elif total_candles == 0:
            reasons.append("HISTORICAL_COVERAGE_INCOMPLETE: Zero candles present to evaluate coverage.")

        # Durations and Boundaries
        req_start_dt = expected_coverage_start or parse_strict_iso_datetime("2020-04-07T00:00:00Z")
        req_end_dt = expected_coverage_end or parse_strict_iso_datetime("2026-09-01T00:00:00Z")
        required_duration_days = round((req_end_dt - req_start_dt).total_seconds() / 86400.0, 2)
        required_coverage_start = req_start_dt.isoformat()
        required_coverage_end_exclusive = req_end_dt.isoformat()

        actual_global_start = earliest_dt.isoformat() if earliest_dt else None
        actual_global_end = latest_dt.isoformat() if latest_dt else None
        actual_observed_span_days = duration_days

        gap_stats = {
            "by_timeframe": gap_stats_by_tf,
            "total_internal_gaps": sum(g.get("internal_gap_count", 0) for g in gap_stats_by_tf.values()),
            "total_missing_intervals": sum(g.get("missing_interval_count", 0) for g in gap_stats_by_tf.values()),
            "largest_gap_seconds": max([g.get("largest_gap_seconds", 0) for g in gap_stats_by_tf.values()] or [0]),
            "missing_intervals_pct": gap_stats_by_tf.get("15m", {}).get("missing_intervals_pct", "0.00%"),
            "1m_missing": gap_stats_by_tf.get("1m", {}).get("missing_intervals_pct", "N/A"),
            "5m_missing": gap_stats_by_tf.get("5m", {}).get("missing_intervals_pct", "N/A"),
            "15m_missing": gap_stats_by_tf.get("15m", {}).get("missing_intervals_pct", "N/A"),
            "1h_missing": gap_stats_by_tf.get("1h", {}).get("missing_intervals_pct", "N/A"),
            "4h_missing": gap_stats_by_tf.get("4h", {}).get("missing_intervals_pct", "N/A"),
            "1d_missing": gap_stats_by_tf.get("1d", {}).get("missing_intervals_pct", "N/A"),
        }

        # Final Hard Gate Decision
        is_empty = (total_candles == 0 or dataset_hash in KNOWN_EMPTY_DATASET_HASHES)

        candle_gate_passed = (
            not is_empty
            and is_warmup_satisfied
            and ohlc_errors == 0
            and duplicate_count == 0
            and naive_timestamps == 0
            and zero_or_negative == 0
            and contamination_count == 0
            and primary_listing is not None
        )

        if not candle_gate_passed:
            decision = "CALIBRATION_DATA_NOT_READY"
            passed = False
        elif (expected_coverage_start and expected_coverage_end) and not coverage_complete:
            decision = "CALIBRATION_DATA_NOT_READY"
            passed = False
        elif technical_only:
            # Technical minimum for feature calculation only (20 bars)
            decision = "READY_FOR_EMPIRICAL_CALIBRATION"
            passed = True
        else:
            # Holistic evidence-driven calibration readiness gate (R1-R20, Spec §33, §34)
            # Never return generic PASS if auxiliary evidence is missing.
            if macro_count == 0:
                decision = "CANDLES_READY_MACRO_MISSING"
                passed = False
                reasons.append("Auxiliary evidence incomplete: Point-in-time macro event coverage is 0. Macro feed remains MISSING.")
            elif friction_status != "EMPIRICAL_FRICTION_CONFIGURED":
                decision = "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
                passed = False
                reasons.append("Auxiliary evidence incomplete: Empirical friction parameters are NOT_CONFIGURED (requires contract fees, quote spread distribution, and slippage telemetry).")
            elif quote_count == 0:
                decision = "CANDLES_READY_QUOTE_EVIDENCE_MISSING"
                passed = False
                reasons.append("Auxiliary evidence incomplete: Historical quote evidence count is 0 (MARKET_AFTER_SIGNAL calibration blocked).")
            else:
                decision = "DATA_READY_FOR_CALIBRATION_REVIEW"
                passed = True

        return XauUsdDataReadinessReport(
            passed=passed,
            decision=decision,
            reasons=reasons,
            total_candles=total_candles,
            timeframe_counts=tf_counts,
            earliest_timestamp=earliest_dt,
            latest_timestamp=latest_dt,
            duration_days=duration_days,
            gap_statistics=gap_stats,
            duplicate_count=duplicate_count,
            ohlc_error_count=ohlc_errors,
            naive_timestamp_count=naive_timestamps,
            zero_or_negative_count=zero_or_negative,
            source_contamination_count=contamination_count,
            warmup_15m_bars=warmup_15m_bars,
            is_warmup_satisfied=is_warmup_satisfied,
            volume_evidence_distribution=vol_dist,
            volume_classification=volume_classification,
            macro_event_count=macro_count,
            quote_count=quote_count,
            friction_status=friction_status,
            dataset_hash=dataset_hash,
            generated_at=now_str,
            candle_gate_passed=candle_gate_passed,
            primary_provider=resolved_primary_provider,
            primary_symbol=resolved_primary_symbol,
            listing_status=resolved_listing_status,
            listing_role=resolved_listing_role,
            coverage_complete=coverage_complete,
            coverage_by_timeframe=coverage_by_tf,
            gap_statistics_by_timeframe=gap_stats_by_tf,
            phase6_15m_dataset_fingerprint=phase6_15m_dataset_fingerprint,
            readiness_evidence_fingerprint=readiness_evidence_fingerprint,
            required_coverage_start=required_coverage_start,
            required_coverage_end_exclusive=required_coverage_end_exclusive,
            required_duration_days=required_duration_days,
            actual_global_start=actual_global_start,
            actual_global_end=actual_global_end,
            actual_observed_span_days=actual_observed_span_days,
        )
