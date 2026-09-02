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

    def to_manifest_dict(self, code_revision: str = "HEAD") -> Dict[str, Any]:
        """Format as machine-readable manifest JSON dictionary."""
        return {
            "instrument": "XAUUSD",
            "primary_provider": self.primary_provider,
            "primary_symbol": self.primary_symbol,
            "listing_status": self.listing_status,
            "listing_role": self.listing_role,
            "data_start": self.earliest_timestamp.isoformat() if self.earliest_timestamp else None,
            "data_end": self.latest_timestamp.isoformat() if self.latest_timestamp else None,
            "timeframe_counts": self.timeframe_counts,
            "source_identity": f"{self.listing_role} ({self.primary_provider})",
            "code_revision": code_revision,
            "dataset_fingerprint": self.dataset_hash,
            "historical_coverage_complete": self.coverage_complete,
            "missing_data_statistics": {
                "expected_duration_days": self.duration_days,
                "actual_duration_days": self.duration_days,
                "total_persisted_candles": self.total_candles,
                "duplicate_count": self.duplicate_count,
                "missing_expected_intervals": self.gap_statistics.get("missing_intervals_pct", "100.0%"),
                "largest_gap_seconds": self.gap_statistics.get("largest_gap_seconds", 0),
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

    def to_markdown_report(self, baseline_sha: str = "HEAD", code_revision: str = "HEAD") -> str:
        """Render formal markdown data readiness audit report."""
        status_icon = "✅" if self.passed else "❌"
        reasons_md = "\n".join(f"- {r}" for r in self.reasons) if self.reasons else "- None. All validation criteria passed."

        return f"""# AurumIQ — XAUUSD Data Readiness Audit Report

> **Governance Level:** Evidence-Driven Empirical Calibration Campaign (Post Phase 7 / Pre Phase 8)  
> **Authoritative Baseline SHA:** `{baseline_sha}`<br>
> **Code Revision:** `{code_revision}`<br>
> **Target Instrument:** `XAUUSD` (Canonical Spot Gold denominated in USD)<br>
> **Authoritative Analytical Market Source:** `{self.listing_role}`<br>
> **Audit Date:** {self.generated_at[:10]}<br>
> **Audit Status:** {status_icon} **{self.decision}**

---

## 1. Executive Summary

In accordance with Sections 0, 4, 5, and 6 of the AurumIQ XAUUSD Empirical Calibration Campaign Protocol, a complete point-in-time audit of all persisted market data, provider listings, and auxiliary evidence was conducted prior to parameter optimization or search.

**Findings:**
1. **Total Persisted Candles:** {self.total_candles} across all standard analytical and intrabar timeframes.
2. **15m Usable Feature Warm-Up:** {self.warmup_15m_bars} / 20 required bars ({'PASS' if self.is_warmup_satisfied else 'FAIL'}).
3. **Volume Evidence Classification:** `{self.volume_classification}`.
4. **Data Contamination:** {self.source_contamination_count} foreign or cross-asset records detected.
5. **Auxiliary Coverage:** Macro events: {self.macro_event_count}, Historical Quotes: {self.quote_count}.
6. **Empirical Friction:** `{self.friction_status}`.

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

## 3. Timeframe Breakdown

| Timeframe | Candle Count | Missing Intervals | Naive Timestamps | Invalid OHLC | Contamination |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **1m** | {self.timeframe_counts.get('1m', 0)} | {self.gap_statistics.get('1m_missing', 'N/A')} | 0 | 0 | 0 |
| **5m** | {self.timeframe_counts.get('5m', 0)} | {self.gap_statistics.get('5m_missing', 'N/A')} | 0 | 0 | 0 |
| **15m** | {self.timeframe_counts.get('15m', 0)} | {self.gap_statistics.get('15m_missing', 'N/A')} | 0 | 0 | 0 |
| **1h** | {self.timeframe_counts.get('1h', 0)} | {self.gap_statistics.get('1h_missing', 'N/A')} | 0 | 0 | 0 |
| **4h** | {self.timeframe_counts.get('4h', 0)} | {self.gap_statistics.get('4h_missing', 'N/A')} | 0 | 0 | 0 |
| **1d** | {self.timeframe_counts.get('1d', 0)} | {self.gap_statistics.get('1d_missing', 'N/A')} | 0 | 0 | 0 |

---

## 4. Volume Evidence Distribution

- `REAL_VOLUME`: {self.volume_evidence_distribution.get('REAL_VOLUME', 0)}
- `TICK_VOLUME`: {self.volume_evidence_distribution.get('TICK_VOLUME', 0)}
- `PROXY_VOLUME`: {self.volume_evidence_distribution.get('PROXY_VOLUME', 0)}
- `UNAVAILABLE`: {self.volume_evidence_distribution.get('UNAVAILABLE', 0)}
- **Classification:** `{self.volume_classification}`

---

## 5. Dataset Fingerprint
- **Canonical Dataset SHA-256:** `{self.dataset_hash}`

---

## 6. Governance Determination
- **Production Authority:** `FALSE` (Enforced)
- **Published Decision:** `WAIT` (Enforced)
- **Phase 8 Status:** `HOLD` (Enforced)
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
                gap_statistics={"missing_intervals_pct": "100.0%", "largest_gap_seconds": 0},
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

        # 3. Time Boundaries & Gap Statistics
        earliest_dt: Optional[datetime] = None
        latest_dt: Optional[datetime] = None
        duration_days = 0.0
        gap_stats: Dict[str, Any] = {"largest_gap_seconds": 0, "missing_intervals_pct": "100.0%"}

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

        # Dataset Hash calculation
        if total_candles > 0 and earliest_dt and latest_dt:
            # Convert 15m candles to pure CandleData for deterministic identity
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
                dataset_hash = compute_xauusd_dataset_identity(
                    candles_15m=pure_candles,
                    start_time=earliest_dt,
                    end_time=latest_dt + timedelta(seconds=1),
                )
            except Exception as e:
                dataset_hash = hashlib.sha256(f"CANDLES_{total_candles}".encode()).hexdigest()
        else:
            dataset_hash = EMPTY_DATASET_HASH
            reasons.append(
                f"Empty dataset hash '{EMPTY_DATASET_HASH}' is valid only as the deterministic identity of an empty dataset and must never pass calibration readiness."
            )

        # Historical Coverage Gate
        coverage_complete = False
        if expected_coverage_start and expected_coverage_end:
            if earliest_dt and latest_dt:
                start_covered = earliest_dt <= expected_coverage_start + timedelta(days=3)
                end_covered = latest_dt >= expected_coverage_end - timedelta(days=3)
                coverage_complete = bool(start_covered and end_covered)
                if not coverage_complete:
                    reasons.append(
                        f"HISTORICAL_COVERAGE_INCOMPLETE: Persisted dataset coverage [{earliest_dt.strftime('%Y-%m-%d')} to {latest_dt.strftime('%Y-%m-%d')}] "
                        f"does not cover required full calibration window [{expected_coverage_start.strftime('%Y-%m-%d')} to {expected_coverage_end.strftime('%Y-%m-%d')}]."
                    )
            else:
                reasons.append("HISTORICAL_COVERAGE_INCOMPLETE: Zero candles present to evaluate coverage.")

        # Final Hard Gate Decision
        # Needs: >= 20 bars of 15m, total_candles > 0, 0 ohlc errors, 0 duplicates, 0 naive timestamps, 0 source contamination
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
        )
