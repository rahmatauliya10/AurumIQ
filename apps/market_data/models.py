"""Market data storage models: MarketCandle, DataQualitySnapshot, and QuarantineRecord."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from apps.instruments.models import Instrument, MarketListing


class VolumeEvidenceType(models.TextChoices):
    REAL_VOLUME = "REAL_VOLUME", "Real Volume"
    TICK_VOLUME = "TICK_VOLUME", "Tick Volume"
    PROXY_VOLUME = "PROXY_VOLUME", "Proxy Volume"
    UNAVAILABLE = "UNAVAILABLE", "Unavailable / Missing"


class CandleQualityFlag(models.TextChoices):
    OK = "OK", "Good Quality"
    SUSPECT = "SUSPECT", "Suspect Data"
    INTERPOLATED = "INTERPOLATED", "Interpolated Missing Bar"
    QUARANTINED = "QUARANTINED", "Quarantined Source"


class MarketCandle(models.Model):
    """OHLCV candlestick data stored in strict UTC point-in-time sequence."""
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name="candles",
    )
    source = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Origin source exchange or feed ID (e.g. binance, okx, gold_reference)",
    )
    timeframe = models.CharField(
        max_length=8,
        db_index=True,
        help_text="Timeframe interval: 1m, 5m, 15m, 1h, 4h, 1d",
    )
    timestamp_open = models.DateTimeField(db_index=True)
    timestamp_close = models.DateTimeField(db_index=True)
    
    # OHLCV values
    open = models.DecimalField(max_digits=18, decimal_places=8)
    high = models.DecimalField(max_digits=18, decimal_places=8)
    low = models.DecimalField(max_digits=18, decimal_places=8)
    close = models.DecimalField(max_digits=18, decimal_places=8)
    volume = models.DecimalField(max_digits=24, decimal_places=8, default=Decimal("0"))
    volume_evidence = models.CharField(
        max_length=16,
        choices=VolumeEvidenceType.choices,
        default=VolumeEvidenceType.UNAVAILABLE,
        db_index=True,
        help_text="Volume semantics evidence type (REAL_VOLUME, TICK_VOLUME, PROXY_VOLUME, UNAVAILABLE)",
    )
    
    # Normalization & Integrity
    quote_rate = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("1.000000"),
        help_text="USDT/USD conversion rate applied to this candle",
    )
    close_usd = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        null=True,
        blank=True,
        help_text="Normalized USD price (close * quote_rate)",
    )
    is_closed = models.BooleanField(default=True, db_index=True)
    data_quality_flag = models.CharField(
        max_length=16,
        choices=CandleQualityFlag.choices,
        default=CandleQualityFlag.OK,
        db_index=True,
    )
    source_sequence = models.BigIntegerField(null=True, blank=True)
    ingestion_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("instrument", "source", "timeframe", "timestamp_open")
        ordering = ["timestamp_open"]
        verbose_name = "Market Candle"
        verbose_name_plural = "Market Candles"
        indexes = [
            models.Index(fields=["instrument", "timeframe", "timestamp_open"]),
            models.Index(fields=["instrument", "timeframe", "-timestamp_open"]),
            models.Index(fields=["instrument", "source", "timeframe", "timestamp_open"]),
        ]

    def save(self, *args, **kwargs):
        # Direct USD native pricing vs legacy rate normalization
        if self.close is not None:
            # If quote asset is USD, close_usd is direct close (DIRECT_USD identity semantics)
            if hasattr(self, "instrument") and self.instrument and getattr(self.instrument.quote_asset, "code", None) == "USD":
                self.quote_rate = Decimal("1.000000")
                self.close_usd = self.close.quantize(Decimal("0.00000001"))
            elif self.quote_rate is not None:
                self.close_usd = (self.close * self.quote_rate).quantize(Decimal("0.00000001"))
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return (
            f"{self.instrument.symbol} [{self.timeframe}] @ {self.timestamp_open.isoformat()} "
            f"O:{self.open} H:{self.high} L:{self.low} C:{self.close} ({self.source})"
        )


class DataQualitySnapshot(models.Model):
    """Point-in-time assessment of time-series data quality and integrity."""
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name="quality_snapshots",
    )
    timeframe = models.CharField(max_length=8, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    quality_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100.00"),
        help_text="Calculated quality score (0.0 to 100.0)",
    )
    gap_count = models.IntegerField(default=0)
    duplicate_count = models.IntegerField(default=0)
    violation_count = models.IntegerField(default=0)
    is_stale = models.BooleanField(default=False)
    hard_fail = models.BooleanField(
        default=False,
        help_text="If True, hard gate blocks all BUY_WINDOW signals",
    )
    anomalies = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Data Quality Snapshot"
        verbose_name_plural = "Data Quality Snapshots"
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-timestamp"]),
        ]

    def __str__(self) -> str:
        status = "HARD_FAIL" if self.hard_fail else f"{self.quality_score}%"
        return f"{self.instrument.symbol} [{self.timeframe}] @ {self.timestamp.isoformat()} -> {status}"


class QuarantineRecord(models.Model):
    """Audit log for quarantined providers that exceeded outlier tolerances."""
    listing = models.ForeignKey(
        MarketListing,
        on_delete=models.CASCADE,
        related_name="quarantine_records",
    )
    provider = models.CharField(max_length=32, db_index=True)
    symbol = models.CharField(max_length=64)
    quarantined_at = models.DateTimeField(auto_now_add=True, db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    basis_deviation = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Deviation percentage from consensus reference",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-quarantined_at"]
        verbose_name = "Quarantine Record"
        verbose_name_plural = "Quarantine Records"

    def __str__(self) -> str:
        active_str = "ACTIVE" if self.is_active else "RELEASED"
        return f"Quarantine: {self.provider} ({self.symbol}) - {active_str} [{self.reason[:40]}]"


class MacroEventFamily(models.TextChoices):
    US_CPI = "US_CPI", "US Consumer Price Index"
    US_NFP = "US_NFP", "US Nonfarm Payrolls"
    FOMC_RATE = "FOMC_RATE", "Federal Open Market Committee Rate Decision"


class ScheduleStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled"
    RESCHEDULED = "RESCHEDULED", "Rescheduled"
    CANCELLED = "CANCELLED", "Cancelled"


class PublicationStatus(models.TextChoices):
    PUBLISHED = "PUBLISHED", "Published"
    PUBLISHED_LATE_OR_BUNDLED = "PUBLISHED_LATE_OR_BUNDLED", "Published Late or Bundled"
    OFFICIALLY_NOT_PUBLISHED = "OFFICIALLY_NOT_PUBLISHED", "Officially Not Published"
    MISSING_UNEXPLAINED = "MISSING_UNEXPLAINED", "Missing Unexplained"
    INVALID = "INVALID", "Invalid"


class ScheduleProvenanceType(models.TextChoices):
    BLS_PREVIOUS_RELEASE_ANNOUNCEMENT = "BLS_PREVIOUS_RELEASE_ANNOUNCEMENT", "BLS Previous Release Announcement"
    OMB_PFEI_SCHEDULE = "OMB_PFEI_SCHEDULE", "OMB PFEI Schedule"
    OTHER_FIRST_PARTY = "OTHER_FIRST_PARTY", "Other First Party"
    UNKNOWN = "UNKNOWN", "Unknown"



class MacroEventIdentity(models.Model):
    """Canonical registry of macroeconomic event families."""
    identity_id = models.CharField(max_length=64, primary_key=True)
    event_family = models.CharField(max_length=32, choices=MacroEventFamily.choices, db_index=True)
    name = models.CharField(max_length=128)
    country = models.CharField(max_length=8, default="US")
    impact = models.CharField(max_length=16, default="HIGH")
    reporting_agency = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["identity_id"]
        verbose_name = "Macro Event Identity"
        verbose_name_plural = "Macro Event Identities"

    def __str__(self) -> str:
        return f"{self.identity_id} ({self.name})"


class ImmutableQuerySet(models.QuerySet):
    """QuerySet that strictly enforces append-only immutability at the database level."""

    def update(self, **kwargs):
        raise PermissionError(f"{self.model.__name__} is immutable; QuerySet.update() is prohibited.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise PermissionError(f"{self.model.__name__} is immutable; QuerySet.bulk_update() is prohibited.")

    def delete(self):
        raise PermissionError(f"{self.model.__name__} is append-only; QuerySet.delete() is prohibited.")


class ImmutableManager(models.Manager.from_queryset(ImmutableQuerySet)):
    """Manager enforcing append-only immutability for governed evidence tables."""
    pass


class SourceSnapshot(models.Model):
    """Immutable audit snapshot of raw HTTP response payloads."""
    snapshot_id = models.CharField(max_length=64, primary_key=True)
    source_url = models.URLField(max_length=1024)
    source_name = models.CharField(max_length=64, db_index=True)
    first_retrieved_at = models.DateTimeField(db_index=True)
    http_status = models.IntegerField(default=200)
    content_type = models.CharField(max_length=128, blank=True)
    etag = models.CharField(max_length=256, blank=True)
    last_modified_header = models.CharField(max_length=128, blank=True)
    raw_payload_bytes_sha256 = models.CharField(max_length=64, db_index=True)
    raw_content = models.BinaryField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-first_retrieved_at"]
        verbose_name = "Source Snapshot"
        verbose_name_plural = "Source Snapshots"
        indexes = [
            models.Index(fields=["source_name", "first_retrieved_at"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("SourceSnapshot is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and SourceSnapshot.objects.filter(pk=self.pk).exists():
            raise ValueError("SourceSnapshot is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Snapshot {self.snapshot_id[:8]} ({self.source_name} @ {self.first_retrieved_at.isoformat()})"


class MacroScheduleVintage(models.Model):
    """Point-in-time publication of event scheduled release times (append-only)."""
    vintage_id = models.CharField(max_length=64, primary_key=True)
    event = models.ForeignKey(
        MacroEventIdentity,
        on_delete=models.PROTECT,
        related_name="schedules",
    )
    reference_period = models.CharField(max_length=32, db_index=True)
    scheduled_at = models.DateTimeField(db_index=True)
    schedule_status = models.CharField(
        max_length=32,
        choices=ScheduleStatus.choices,
        default=ScheduleStatus.SCHEDULED,
    )
    source_published_at = models.DateTimeField(null=True, blank=True)
    known_at = models.DateTimeField(db_index=True)
    supersedes_vintage = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
    )
    source_snapshot = models.ForeignKey(
        SourceSnapshot,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="schedules",
    )
    provenance_type = models.CharField(
        max_length=48,
        choices=ScheduleProvenanceType.choices,
        default=ScheduleProvenanceType.UNKNOWN,
        db_index=True,
    )
    announcing_release_url = models.URLField(max_length=1024, null=True, blank=True)
    announcing_release_timestamp = models.DateTimeField(null=True, blank=True)
    parser_rule_version = models.CharField(max_length=64, default="BLS_PREVIOUS_RELEASE_V1")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-known_at", "-vintage_id"]
        verbose_name = "Macro Schedule Vintage"
        verbose_name_plural = "Macro Schedule Vintages"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "reference_period", "known_at"],
                name="unique_macro_schedule_vintage",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "reference_period", "known_at"]),
            models.Index(fields=["known_at"]),
            models.Index(fields=["provenance_type"]),
        ]

    def clean(self):
        super().clean()
        if self.schedule_status == ScheduleStatus.CANCELLED and not self.source_snapshot_id:
            raise ValidationError("Cancellation schedule vintage requires authoritative source_snapshot.")
        if self.known_at and self.scheduled_at and self.known_at >= self.scheduled_at and self.schedule_status != ScheduleStatus.CANCELLED:
            raise ValidationError(f"Schedule vintage known_at ({self.known_at}) must be strictly prior to scheduled_at ({self.scheduled_at}).")
        if self.provenance_type in (
            ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
            ScheduleProvenanceType.OMB_PFEI_SCHEDULE,
            ScheduleProvenanceType.OTHER_FIRST_PARTY,
        ) and not self.source_snapshot_id:
            raise ValidationError(f"Provenanced schedule vintage ({self.provenance_type}) requires authoritative source_snapshot.")
        if self.provenance_type == ScheduleProvenanceType.OMB_PFEI_SCHEDULE and not self.source_published_at:
            raise ValidationError("OMB PFEI Schedule provenance requires a defensible source publication date.")

    def delete(self, *args, **kwargs):
        raise PermissionError("MacroScheduleVintage is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and MacroScheduleVintage.objects.filter(pk=self.pk).exists():
            raise ValueError("MacroScheduleVintage is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Sched {self.event_id} [{self.reference_period}] @ {self.scheduled_at.isoformat()} ({self.schedule_status})"


class MacroObservationVintage(models.Model):
    """Point-in-time macroeconomic observation release and revisions (append-only)."""
    vintage_id = models.CharField(max_length=64, primary_key=True)
    event = models.ForeignKey(
        MacroEventIdentity,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    schedule_vintage = models.ForeignKey(
        MacroScheduleVintage,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    reference_period = models.CharField(max_length=32, db_index=True)
    revision_number = models.IntegerField(default=0, db_index=True)
    revises_vintage = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    observation_date = models.DateField(null=True, blank=True)
    vintage_date = models.DateField(null=True, blank=True, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    source_published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    first_retrieved_at = models.DateTimeField(null=True, blank=True)
    known_at = models.DateTimeField(db_index=True)
    raw_value = models.CharField(max_length=64, blank=True)
    level_value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    derived_change_value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit = models.CharField(max_length=32, blank=True)
    source_snapshot = models.ForeignKey(
        SourceSnapshot,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    publication_status = models.CharField(
        max_length=32,
        choices=PublicationStatus.choices,
        default=PublicationStatus.PUBLISHED,
        db_index=True,
    )
    non_publication_reason = models.CharField(
        max_length=128,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-known_at", "-revision_number"]
        verbose_name = "Macro Observation Vintage"
        verbose_name_plural = "Macro Observation Vintages"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "reference_period", "revision_number"],
                name="unique_macro_observation_revision",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "reference_period", "revision_number"]),
            models.Index(fields=["known_at", "source_published_at"]),
        ]

    def clean(self):
        super().clean()
        if self.publication_status == PublicationStatus.OFFICIALLY_NOT_PUBLISHED and not self.source_snapshot_id:
            raise ValidationError("Officially not published observation requires authoritative source_snapshot.")

    def delete(self, *args, **kwargs):
        raise PermissionError("MacroObservationVintage is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and MacroObservationVintage.objects.filter(pk=self.pk).exists():
            raise ValueError("MacroObservationVintage is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Obs {self.event_id} [{self.reference_period}] rev={self.revision_number} val={self.raw_value}"


class MacroScheduleProvenanceAssertion(models.Model):
    """Append-only audit assertion validating the provenance of a macro schedule vintage."""
    assertion_id = models.CharField(max_length=64, primary_key=True)
    schedule_vintage = models.ForeignKey(
        MacroScheduleVintage,
        on_delete=models.PROTECT,
        related_name="provenance_assertions",
    )
    provenance_type = models.CharField(
        max_length=48,
        choices=ScheduleProvenanceType.choices,
        default=ScheduleProvenanceType.UNKNOWN,
        db_index=True,
    )
    source_snapshot = models.ForeignKey(
        SourceSnapshot,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provenance_assertions",
    )
    announcing_release_url = models.URLField(max_length=1024, null=True, blank=True)
    announcing_release_timestamp = models.DateTimeField(null=True, blank=True)
    parser_rule_version = models.CharField(max_length=64, default="PROVENANCE_RULE_V1")
    asserted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    supersedes_assertion = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-asserted_at", "-assertion_id"]
        verbose_name = "Macro Schedule Provenance Assertion"
        verbose_name_plural = "Macro Schedule Provenance Assertions"
        constraints = [
            models.UniqueConstraint(
                fields=["schedule_vintage", "provenance_type", "source_snapshot"],
                name="unique_schedule_provenance_assertion",
            ),
        ]
        indexes = [
            models.Index(fields=["schedule_vintage", "provenance_type"]),
            models.Index(fields=["asserted_at"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("MacroScheduleProvenanceAssertion is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and MacroScheduleProvenanceAssertion.objects.filter(pk=self.pk).exists():
            raise ValueError("MacroScheduleProvenanceAssertion is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Assertion {self.assertion_id[:12]} for {self.schedule_vintage_id} ({self.provenance_type})"


# =============================================================================
# EMPIRICAL FRICTION EVIDENCE MODELS (Pre-Phase-8 Calibration Governance)
# =============================================================================

class FrictionComponentType(models.TextChoices):
    SPREAD = "SPREAD", "Spread"
    SLIPPAGE = "SLIPPAGE", "Slippage"
    COMMISSION = "COMMISSION", "Commission"
    FINANCING = "FINANCING", "Financing / Swap"
    CONTRACT_SPEC = "CONTRACT_SPEC", "Contract Specification"


class FrictionConditionType(models.TextChoices):
    NORMAL = "NORMAL", "Normal Conditions"
    STRESSED = "STRESSED", "Stressed / Volatile Conditions"
    MACRO_BLACKOUT = "MACRO_BLACKOUT", "Macroeconomic Blackout Window"
    ALL = "ALL", "All Conditions"


class FrictionSessionType(models.TextChoices):
    ASIAN = "ASIAN", "Asian Session"
    LONDON = "LONDON", "London Session"
    NEW_YORK = "NEW_YORK", "New York Session"
    ROLLOVER = "ROLLOVER", "Daily Rollover Window"
    ALL = "ALL", "All Sessions Combined"


class FrictionBindingRole(models.TextChoices):
    PRIMARY_SPREAD_SAMPLE = "PRIMARY_SPREAD_SAMPLE", "Primary Spread Sample Dataset"
    STRESS_SPREAD_SAMPLE = "STRESS_SPREAD_SAMPLE", "Stress Spread Sample Dataset"
    TELEMETRY_SAMPLE = "TELEMETRY_SAMPLE", "Execution Telemetry Sample Dataset"
    PRIMARY_TELEMETRY_SAMPLE = "PRIMARY_TELEMETRY_SAMPLE", "Primary Execution Telemetry Sample Dataset"
    NORMAL_SPREAD_DISTRIBUTION = "NORMAL_SPREAD_DISTRIBUTION", "Normal Spread Distribution Summary"
    STRESS_SPREAD_DISTRIBUTION = "STRESS_SPREAD_DISTRIBUTION", "Stress Spread Distribution Summary"
    SLIPPAGE_DISTRIBUTION = "SLIPPAGE_DISTRIBUTION", "Slippage Distribution Summary"
    NORMAL_SLIPPAGE_DISTRIBUTION = "NORMAL_SLIPPAGE_DISTRIBUTION", "Normal Slippage Distribution Summary"
    STRESS_SLIPPAGE_DISTRIBUTION = "STRESS_SLIPPAGE_DISTRIBUTION", "Stress Slippage Distribution Summary"


class FrictionActivationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft Model (Evidence Incomplete / Unsealed)"
    CANDIDATE = "CANDIDATE", "Candidate Model Awaiting Sign-Off"
    ACTIVE = "ACTIVE", "Active Calibration Model"
    SUPERSEDED = "SUPERSEDED", "Superseded by Newer Calibration Model"
    REJECTED = "REJECTED", "Rejected by Governance Evaluation"
    RETIRED = "RETIRED", "Retired"


class FrictionPopulationSemantics(models.TextChoices):
    UNKNOWN = "UNKNOWN", "Unknown / Legacy Unspecified"
    SPREAD_BPS = "SPREAD_BPS", "Spread Distribution (BPS)"
    SLIPPAGE_ADVERSE_ONLY = "SLIPPAGE_ADVERSE_ONLY", "Slippage Adverse-Only Distribution (BPS)"
    SLIPPAGE_SIGNED = "SLIPPAGE_SIGNED", "Slippage Raw Signed Distribution (BPS)"


class FrictionQualificationStatus(models.TextChoices):
    QUALIFIED = "QUALIFIED", "Qualified Broker Evidence"
    UNVERIFIED = "UNVERIFIED", "Unverified / Format Only"
    REJECTED = "REJECTED", "Rejected by Parser Validation"


class FrictionSourceType(models.TextChoices):
    OFFICIAL_BROKER_DOCUMENT = "OFFICIAL_BROKER_DOCUMENT", "Official Broker Document"
    MT5_SYMBOL_INFO_EXPORT = "MT5_SYMBOL_INFO_EXPORT", "MT5 Symbol Info Export"
    MT5_ACCOUNT_EXPORT = "MT5_ACCOUNT_EXPORT", "MT5 Account Export"
    BROKER_PERSONAL_AREA_EXPORT = "BROKER_PERSONAL_AREA_EXPORT", "Broker Personal Area Export"
    ACCOUNT_CLIENT_AGREEMENT = "ACCOUNT_CLIENT_AGREEMENT", "Account Client Agreement"
    MT5_TICK_HISTORY_EXPORT = "MT5_TICK_HISTORY_EXPORT", "MT5 Tick History Export"
    MT5_EXECUTION_TELEMETRY_EXPORT = "MT5_EXECUTION_TELEMETRY_EXPORT", "MT5 Execution Telemetry Export"
    USER_PROVIDED_UNVERIFIED = "USER_PROVIDED_UNVERIFIED", "User Provided Unverified"


QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES = {
    FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value,
    FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value,
    FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
}

QUALIFIED_CONTRACT_SOURCE_TYPES = {
    FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value,
    FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
}

QUALIFIED_COMMISSION_SOURCE_TYPES = {
    FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value,
    FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value,
    FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
}

QUALIFIED_FINANCING_SOURCE_TYPES = {
    FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value,
    FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value,
    FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
}

QUALIFIED_SPREAD_SOURCE_TYPES = {
    FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
}

QUALIFIED_SLIPPAGE_SOURCE_TYPES = {
    FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT.value,
}

QUALIFIED_FRICTION_SOURCE_TYPES = (
    QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES
    | QUALIFIED_CONTRACT_SOURCE_TYPES
    | QUALIFIED_COMMISSION_SOURCE_TYPES
    | QUALIFIED_FINANCING_SOURCE_TYPES
    | QUALIFIED_SPREAD_SOURCE_TYPES
    | QUALIFIED_SLIPPAGE_SOURCE_TYPES
)


class FrictionSourceSnapshot(models.Model):
    """Immutable audit snapshot of raw broker / venue evidence payloads (append-only)."""
    snapshot_id = models.CharField(max_length=64, primary_key=True)
    source_url = models.URLField(max_length=1024)
    source_name = models.CharField(max_length=64, db_index=True)
    source_type = models.CharField(
        max_length=64,
        choices=FrictionSourceType.choices,
        default=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
        db_index=True,
    )
    source_origin = models.CharField(max_length=255, blank=True, default="")
    collection_methodology = models.CharField(max_length=255, blank=True, default="")
    original_filename = models.CharField(max_length=255, blank=True, default="")
    venue = models.CharField(max_length=32, db_index=True)
    symbol = models.CharField(max_length=32, db_index=True)
    account_tier = models.CharField(max_length=32, db_index=True)
    retrieved_at = models.DateTimeField(db_index=True)
    known_at = models.DateTimeField(db_index=True)
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_to = models.DateTimeField(null=True, blank=True)
    http_status = models.IntegerField(default=200)
    raw_payload_bytes_sha256 = models.CharField(max_length=64, db_index=True)
    raw_content = models.BinaryField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-known_at", "-snapshot_id"]
        verbose_name = "Friction Source Snapshot"
        verbose_name_plural = "Friction Source Snapshots"
        indexes = [
            models.Index(fields=["venue", "symbol", "account_tier"]),
            models.Index(fields=["raw_payload_bytes_sha256"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionSourceSnapshot is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionSourceSnapshot.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionSourceSnapshot is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"FrictionSnapshot {self.snapshot_id[:12]} ({self.source_name} @ {self.venue})"


class FrictionSourceQualificationAssertion(models.Model):
    """Immutable audit assertion validating qualification of a friction source snapshot via trusted parser (Directive 6)."""
    assertion_id = models.CharField(max_length=64, primary_key=True)
    source_snapshot = models.ForeignKey(
        FrictionSourceSnapshot,
        on_delete=models.PROTECT,
        related_name="qualification_assertions",
    )
    component_role = models.CharField(max_length=32, db_index=True)
    qualification_status = models.CharField(
        max_length=32,
        choices=FrictionQualificationStatus.choices,
        default=FrictionQualificationStatus.UNVERIFIED,
        db_index=True,
    )
    parser_name = models.CharField(max_length=64)
    parser_version = models.CharField(max_length=32, default="1.0.0")
    raw_artifact_sha256 = models.CharField(max_length=64)
    normalized_evidence_hash = models.CharField(max_length=64)
    asserted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    qualification_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-asserted_at", "-assertion_id"]
        verbose_name = "Friction Source Qualification Assertion"
        verbose_name_plural = "Friction Source Qualification Assertions"
        indexes = [
            models.Index(fields=["component_role", "qualification_status"]),
            models.Index(fields=["asserted_at"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionSourceQualificationAssertion is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionSourceQualificationAssertion.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionSourceQualificationAssertion is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Assertion {self.assertion_id[:12]} ({self.component_role}: {self.qualification_status})"


class FrictionEvidenceDataset(models.Model):
    """Immutable bounded sample dataset used for empirical friction parameter derivation (append-only)."""
    dataset_id = models.CharField(max_length=64, primary_key=True)
    source_snapshot = models.ForeignKey(
        FrictionSourceSnapshot,
        on_delete=models.PROTECT,
        related_name="datasets",
    )
    venue = models.CharField(max_length=32, db_index=True)
    account_tier = models.CharField(max_length=32, db_index=True)
    symbol = models.CharField(max_length=32, db_index=True)
    sample_start = models.DateTimeField(db_index=True)
    sample_end = models.DateTimeField(db_index=True)
    sample_count = models.IntegerField()
    distinct_trading_days = models.IntegerField()
    session_counts = models.JSONField(default=dict, blank=True)
    source_units = models.CharField(max_length=32)
    raw_dataset_sha256 = models.CharField(max_length=64, db_index=True)
    collection_methodology = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-sample_end", "-dataset_id"]
        verbose_name = "Friction Evidence Dataset"
        verbose_name_plural = "Friction Evidence Datasets"
        indexes = [
            models.Index(fields=["venue", "symbol", "account_tier"]),
            models.Index(fields=["raw_dataset_sha256"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionEvidenceDataset is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionEvidenceDataset.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionEvidenceDataset is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Dataset {self.dataset_id[:12]} ({self.symbol} N={self.sample_count})"


class FrictionDistributionSummary(models.Model):
    """Immutable statistical distribution summary derived from a sample dataset (append-only)."""
    summary_id = models.CharField(max_length=64, primary_key=True)
    evidence_dataset = models.ForeignKey(
        FrictionEvidenceDataset,
        on_delete=models.PROTECT,
        related_name="distributions",
    )
    component_type = models.CharField(max_length=32, choices=FrictionComponentType.choices, db_index=True)
    condition = models.CharField(
        max_length=32,
        choices=FrictionConditionType.choices,
    )
    session = models.CharField(
        max_length=32,
        choices=FrictionSessionType.choices,
    )
    unit = models.CharField(max_length=32)
    sample_count = models.IntegerField()
    stat_min = models.DecimalField(max_digits=12, decimal_places=6)
    stat_p50 = models.DecimalField(max_digits=12, decimal_places=6)
    stat_p75 = models.DecimalField(max_digits=12, decimal_places=6)
    stat_p90 = models.DecimalField(max_digits=12, decimal_places=6)
    stat_p95 = models.DecimalField(max_digits=12, decimal_places=6)
    stat_p99 = models.DecimalField(max_digits=12, decimal_places=6)
    stat_max = models.DecimalField(max_digits=12, decimal_places=6)
    stat_mean = models.DecimalField(max_digits=12, decimal_places=6)
    stat_std = models.DecimalField(max_digits=12, decimal_places=6)
    population_semantics = models.CharField(
        max_length=32,
        choices=FrictionPopulationSemantics.choices,
        default=FrictionPopulationSemantics.UNKNOWN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-created_at", "-summary_id"]
        verbose_name = "Friction Distribution Summary"
        verbose_name_plural = "Friction Distribution Summaries"
        indexes = [
            models.Index(fields=["component_type", "condition", "session"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionDistributionSummary is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionDistributionSummary.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionDistributionSummary is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Summary {self.summary_id[:12]} ({self.component_type} p75={self.stat_p75} {self.unit})"


class FrictionModelVersion(models.Model):
    """Immutable bound calibration friction model version (append-only).
    
    CRITICAL: Contains zero silent evidence defaults (Directive 2).
    All parameters must be explicitly populated from verified evidence or set to None (UNKNOWN).
    """
    model_version_id = models.CharField(max_length=64, primary_key=True)
    venue = models.CharField(max_length=32, db_index=True)
    symbol = models.CharField(max_length=32, db_index=True)
    account_tier = models.CharField(max_length=32, db_index=True)

    # Legal Entity Provenance (Directive 1)
    legal_entity_code = models.CharField(max_length=64, db_index=True)
    legal_entity_name = models.CharField(max_length=128)
    regulator = models.CharField(max_length=64)
    license_number = models.CharField(max_length=64)
    legal_entity_source_snapshot = models.ForeignKey(
        FrictionSourceSnapshot,
        on_delete=models.PROTECT,
        related_name="legal_entity_models",
    )
    contract_spec_source_snapshot = models.ForeignKey(
        FrictionSourceSnapshot,
        on_delete=models.PROTECT,
        related_name="contract_spec_models",
        null=True,
        blank=True,
    )
    fee_schedule_source_snapshot = models.ForeignKey(
        FrictionSourceSnapshot,
        on_delete=models.PROTECT,
        related_name="fee_schedule_models",
        null=True,
        blank=True,
    )
    swap_spec_source_snapshot = models.ForeignKey(
        FrictionSourceSnapshot,
        on_delete=models.PROTECT,
        related_name="swap_spec_models",
        null=True,
        blank=True,
    )

    # Contract Geometry & Tick Specifications (Directive 4: point vs tick_size separate, NO DEFAULTS)
    digits = models.IntegerField(null=True, blank=True)
    point_size = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True)
    trade_tick_size = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True)
    trade_tick_value = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    contract_size = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    volume_min = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    volume_max = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    volume_step = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    # Commission Policy (Directives 3, 6, 8, NO DEFAULTS)
    native_commission_usd_per_lot_per_side = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
    )
    commission_formula = models.CharField(max_length=64, null=True, blank=True)

    # Financing / Swap Policy (Directive 11, NO HARD-CODED DEFAULTS)
    swap_long_points = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    swap_short_points = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rollover_summer_utc_hour = models.IntegerField(null=True, blank=True)
    rollover_winter_utc_hour = models.IntegerField(null=True, blank=True)
    triple_swap_weekday = models.CharField(max_length=16, null=True, blank=True)
    swap_free_available_for_account_type = models.BooleanField(null=True, blank=True)
    actual_account_swap_free_status = models.BooleanField(null=True, blank=True)

    # Spread & Slippage Parameters (MANDATORY SLIPPAGE: null until proven, NO DEFAULTS)
    base_spread_bps = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    stress_spread_bps = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    base_slippage_bps = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    stress_slippage_bps = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

    # Semantic Governance & Fingerprint (Directive 7)
    friction_policy_schema_version = models.CharField(max_length=32, default="1.0.0")
    distribution_algorithm_version = models.CharField(max_length=32, default="1.0.0")
    normalization_version = models.CharField(max_length=32, default="1.0.0")
    commission_formula_version = models.CharField(max_length=32, default="1.0.0")
    financing_rule_version = models.CharField(max_length=32, default="1.0.0")
    slippage_cost_policy_version = models.CharField(max_length=32, default="ADVERSE_ONLY_P75_P95_V1")
    parser_version = models.CharField(max_length=32, default="1.0.0")
    empirical_friction_evidence_fingerprint = models.CharField(max_length=64, db_index=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-created_at", "-model_version_id"]
        verbose_name = "Friction Model Version"
        verbose_name_plural = "Friction Model Versions"
        indexes = [
            models.Index(fields=["venue", "symbol", "account_tier"]),
            models.Index(fields=["empirical_friction_evidence_fingerprint"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionModelVersion is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionModelVersion.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionModelVersion is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"FrictionModel {self.model_version_id} ({self.venue} {self.symbol} {self.account_tier})"


class FrictionModelDatasetBinding(models.Model):
    """Immutable explicit link binding an empirical dataset to a sealed model version (Directive 6)."""
    binding_id = models.CharField(max_length=64, primary_key=True)
    friction_model_version = models.ForeignKey(
        FrictionModelVersion,
        on_delete=models.PROTECT,
        related_name="dataset_bindings",
    )
    evidence_dataset = models.ForeignKey(
        FrictionEvidenceDataset,
        on_delete=models.PROTECT,
        related_name="model_bindings",
    )
    binding_role = models.CharField(max_length=32, choices=FrictionBindingRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-created_at", "-binding_id"]
        verbose_name = "Friction Model Dataset Binding"
        verbose_name_plural = "Friction Model Dataset Bindings"
        constraints = [
            models.UniqueConstraint(
                fields=["friction_model_version", "evidence_dataset", "binding_role"],
                name="unique_friction_model_dataset_binding",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionModelDatasetBinding is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionModelDatasetBinding.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionModelDatasetBinding is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Binding {self.binding_id[:12]} ({self.binding_role})"


class FrictionModelSummaryBinding(models.Model):
    """Immutable explicit link binding a distribution summary to a sealed model version (Directive 6)."""
    binding_id = models.CharField(max_length=64, primary_key=True)
    friction_model_version = models.ForeignKey(
        FrictionModelVersion,
        on_delete=models.PROTECT,
        related_name="summary_bindings",
    )
    distribution_summary = models.ForeignKey(
        FrictionDistributionSummary,
        on_delete=models.PROTECT,
        related_name="model_bindings",
    )
    binding_role = models.CharField(max_length=32, choices=FrictionBindingRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-created_at", "-binding_id"]
        verbose_name = "Friction Model Summary Binding"
        verbose_name_plural = "Friction Model Summary Bindings"
        constraints = [
            models.UniqueConstraint(
                fields=["friction_model_version", "distribution_summary", "binding_role"],
                name="unique_friction_model_summary_binding",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionModelSummaryBinding is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionModelSummaryBinding.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionModelSummaryBinding is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"SummaryBinding {self.binding_id[:12]} ({self.binding_role})"


class FrictionModelActivation(models.Model):
    """Append-only activation history for point-in-time friction model resolution (Directive 5)."""
    activation_id = models.CharField(max_length=64, primary_key=True)
    friction_model_version = models.ForeignKey(
        FrictionModelVersion,
        on_delete=models.PROTECT,
        related_name="activations",
    )
    known_at = models.DateTimeField(db_index=True)
    effective_from = models.DateTimeField(db_index=True)
    effective_to = models.DateTimeField(null=True, blank=True)
    activation_status = models.CharField(
        max_length=32,
        choices=FrictionActivationStatus.choices,
        default=FrictionActivationStatus.ACTIVE,
    )
    source_or_reason = models.TextField()
    supersedes_activation = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        ordering = ["-effective_from", "-activation_id"]
        verbose_name = "Friction Model Activation"
        verbose_name_plural = "Friction Model Activations"
        indexes = [
            models.Index(fields=["activation_status", "effective_from"]),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("FrictionModelActivation is append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and FrictionModelActivation.objects.filter(pk=self.pk).exists():
            raise ValueError("FrictionModelActivation is immutable and append-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Activation {self.activation_id[:12]} ({self.friction_model_version_id} -> {self.activation_status})"


