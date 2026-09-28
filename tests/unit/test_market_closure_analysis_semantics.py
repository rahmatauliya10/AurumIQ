"""
Unit tests for Stage 2 prospective market-closure analysis semantics,
candle-interval legitimacy, live-path idempotency, and audit dry-run separation.

Covers Cases A through G:
  CASE A: Friday 20:45 -> 21:00 legitimate final candle processed shortly after 21:00
          => analysis persists ONCE
  CASE B: Saturday fully-closed 15m interval
          => no trading SignalRecord, no Phase3A trading snapshot, no risk plan, no candidate alert
  CASE C: Sunday 20:45 -> 21:00 fully closed
          => suppressed / rejected
  CASE D: Sunday 21:00 -> 21:15 valid first reopen candle
          => analysis persists ONCE
  CASE E: Same valid primary candle invoked twice in LIVE mode
          => second invocation returns idempotent/no-op; no duplicate SignalRecord, RiskPlan, or Alert
  CASE F: Same valid primary candle evaluated in AUDIT/DRY_RUN
          => scores are returned; persisted row counts unchanged
  CASE G: Market CLOSED + audit of old Friday candle
          => computes in DRY_RUN; creates zero live side effects
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from unittest.mock import patch

from apps.alerts.models import AlertEvent
from apps.analysis.models import CycleSnapshotRecord
from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    ListingRole,
    ListingStatus,
    MarketListing,
    ProviderHealthSnapshot,
)
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
from apps.live_monitor.types import CandleClosedEvent
from apps.market_data.market_hours import (
    is_expected_market_closure,
    is_expected_market_interval_closed,
)
from apps.market_data.models import DataQualitySnapshot, MacroScheduleVintage, MarketCandle
from apps.signals.models import SignalRecord
from engine.cycles.engine import RobustTimeCycleEngine


@pytest.fixture
def xauusd_setup(db):
    """Set up complete canonical XAUUSD infrastructure."""
    base, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold Spot", "asset_type": AssetType.COMMODITY})
    quote, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=base,
        quote_asset=quote,
        instrument_type=InstrumentType.SPOT,
        defaults={"role": InstrumentRole.EXECUTION, "is_active": True},
    )
    inst.is_active = True
    inst.save()

    primary_listing, _ = MarketListing.objects.get_or_create(
        instrument=inst,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        defaults={"provider": "twelve_data", "provider_symbol": "XAU/USD", "status": ListingStatus.ACTIVE},
    )

    t_health = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
    ProviderHealthSnapshot.objects.get_or_create(
        listing=primary_listing,
        checked_at=t_health,
        defaults={"status": "HEALTHY"},
    )

    DataQualitySnapshot.objects.get_or_create(
        instrument=inst,
        timeframe="15m",
        timestamp=t_health,
        defaults={"is_stale": False, "hard_fail": False},
    )

    from apps.market_data.models import MacroEventIdentity
    ident, _ = MacroEventIdentity.objects.get_or_create(
        identity_id="US_NFP",
        defaults={
            "name": "US Nonfarm Payrolls",
            "event_family": "NFP",
            "country": "US",
            "reporting_agency": "BLS",
        },
    )
    MacroScheduleVintage.objects.get_or_create(
        vintage_id="sched_nfp_test_v0",
        event=ident,
        scheduled_at=datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc),
        defaults={"known_at": datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)},
    )

    # Populate 35 historical 15m candles preceding Friday 20:45 UTC
    t_start = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    current_p = Decimal("2650.00")
    for i in range(35):
        c_open = t_start + timedelta(minutes=15 * i)
        c_close = c_open + timedelta(minutes=15)
        MarketCandle.objects.get_or_create(
            instrument=inst,
            timeframe="15m",
            timestamp_open=c_open,
            timestamp_close=c_close,
            defaults={
                "open": current_p,
                "high": current_p + Decimal("2.00"),
                "low": current_p - Decimal("1.00"),
                "close": current_p + Decimal("0.50"),
                "volume": Decimal("500"),
                "is_closed": True,
                "source": "twelve_data",
            },
        )
        current_p += Decimal("0.50")

    return inst


def _build_closed_event(
    t_open: datetime,
    t_close: datetime,
    open_p: Decimal = Decimal("2660.00"),
    close_p: Decimal = Decimal("2662.00"),
) -> CandleClosedEvent:
    from apps.live_monitor.adapter import PublicMarketDataAdapter
    return PublicMarketDataAdapter.create_xauusd_candle_closed_event(
        instrument="XAUUSD",
        timeframe="15m",
        timestamp_open=t_open,
        timestamp_close=t_close,
        open_price=open_p,
        high_price=open_p + Decimal("3.00"),
        low_price=open_p - Decimal("1.50"),
        close_price=close_p,
        volume=Decimal("1200"),
        source="twelve_data",
        is_closed=True,
    )


@pytest.mark.django_db
class TestMarketClosureAnalysisSemantics:
    """Targeted acceptance test matrix for Cases A through G."""

    def test_case_a_friday_final_candle_processed_after_closure(self, xauusd_setup):
        """
        CASE A: Friday 20:45 -> 21:00 legitimate final candle processed shortly after 21:00
        => analysis persists ONCE
        => actionable candidate alert publication is suppressed if evaluated during closure
        """
        t_open = datetime(2026, 9, 25, 20, 45, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        assert not is_expected_market_interval_closed(t_open, t_close)

        # Simulate execution at 21:03 UTC (shortly after 21:00 closure boundary)
        now_utc = datetime(2026, 9, 25, 21, 3, tzinfo=timezone.utc)
        assert is_expected_market_closure(now_utc)

        sig_count_before = SignalRecord.objects.count()
        risk_count_before = LiveRiskPlanRecord.objects.count()

        with patch("apps.alerts.services.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            mock_dt.fromisoformat = datetime.fromisoformat
            sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="test-stage2-sha",
                dry_run=False,
            )

        assert sig_rec is not None
        assert sig_rec.timestamp == t_close
        assert SignalRecord.objects.count() == sig_count_before + 1

        # Actionable candidate alerts must be suppressed during market closure
        # (no BUY_WINDOW_CANDIDATE / SELL_WINDOW_CANDIDATE emitted while market closed)
        candidate_alerts = AlertEvent.objects.filter(
            event_type__in=["BUY_WINDOW_CANDIDATE", "SELL_WINDOW_CANDIDATE", "READY_LONG", "READY_SHORT"]
        )
        assert candidate_alerts.count() == 0

    def test_case_b_saturday_fully_closed_interval_rejected(self, xauusd_setup):
        """
        CASE B: Saturday fully-closed 15m interval
        => rejected before creating SignalRecord
        => no trading SignalRecord
        => no Phase3A trading snapshot
        => no risk plan
        => no candidate alert
        """
        t_open = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 26, 12, 15, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        assert is_expected_market_interval_closed(t_open, t_close)

        sig_count_before = SignalRecord.objects.count()
        risk_count_before = LiveRiskPlanRecord.objects.count()
        snap_count_before = CycleSnapshotRecord.objects.count()
        alert_count_before = AlertEvent.objects.count()

        with pytest.raises(ValueError, match="fully inside governed market closure"):
            XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="test-stage2-sha",
                dry_run=False,
            )

        assert SignalRecord.objects.count() == sig_count_before
        assert LiveRiskPlanRecord.objects.count() == risk_count_before
        assert CycleSnapshotRecord.objects.count() == snap_count_before
        assert AlertEvent.objects.count() == alert_count_before

        # Also verify RobustTimeCycleEngine directly rejects closed intervals for XAUUSD
        engine = RobustTimeCycleEngine.for_xauusd()
        with pytest.raises(ValueError, match="XAUUSD Phase 3A rejected: candle interval.*fully inside"):
            engine.analyze(
                latest_candle=event,
                structure=None,
                timeframe="15m",
                instrument="XAUUSD",
            )

    def test_case_c_sunday_pre_open_fully_closed_interval(self, xauusd_setup):
        """
        CASE C: Sunday 20:45 -> 21:00 fully closed
        => suppressed / rejected
        """
        t_open = datetime(2026, 9, 27, 20, 45, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        assert is_expected_market_interval_closed(t_open, t_close)

        with pytest.raises(ValueError, match="fully inside governed market closure"):
            XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="test-stage2-sha",
                dry_run=False,
            )

    def test_case_d_sunday_reopen_candle_persists(self, xauusd_setup):
        """
        CASE D: Sunday 21:00 -> 21:15 valid first reopen candle
        => analysis persists ONCE
        """
        t_open = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 27, 21, 15, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        assert not is_expected_market_interval_closed(t_open, t_close)

        sig_count_before = SignalRecord.objects.count()

        sig_rec, risk_rec, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test-stage2-sha",
            dry_run=False,
        )

        assert sig_rec is not None
        assert sig_rec.timestamp == t_close
        assert SignalRecord.objects.count() == sig_count_before + 1

    def test_case_e_live_idempotency_invoked_twice(self, xauusd_setup):
        """
        CASE E: Same valid primary candle invoked twice in LIVE mode
        => second invocation returns idempotent / no-op
        => no duplicate SignalRecord
        => no duplicate RiskPlan
        => no duplicate AlertEvent
        """
        t_open = datetime(2026, 9, 25, 20, 45, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        # First LIVE invocation
        sig1, risk1, state1 = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test-stage2-sha",
            dry_run=False,
        )
        sig_count_after_first = SignalRecord.objects.count()
        risk_count_after_first = LiveRiskPlanRecord.objects.count()
        alert_count_after_first = AlertEvent.objects.count()

        # Second LIVE invocation of exact same primary candle
        sig2, risk2, state2 = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test-stage2-sha",
            dry_run=False,
        )

        # Invariant checks:
        assert sig2.id == sig1.id
        assert sig2.analysis_fingerprint == sig1.analysis_fingerprint
        assert SignalRecord.objects.count() == sig_count_after_first
        assert LiveRiskPlanRecord.objects.count() == risk_count_after_first
        assert AlertEvent.objects.count() == alert_count_after_first

    def test_case_f_audit_dry_run_evaluates_without_persisting(self, xauusd_setup):
        """
        CASE F: Same valid primary candle evaluated in AUDIT/DRY_RUN
        => scores and calculations are returned
        => persisted row counts unchanged
        """
        t_open = datetime(2026, 9, 25, 20, 45, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        sig_count_before = SignalRecord.objects.count()
        risk_count_before = LiveRiskPlanRecord.objects.count()
        snap_count_before = CycleSnapshotRecord.objects.count()
        alert_count_before = AlertEvent.objects.count()

        from apps.backtests.tasks import resolve_xauusd_research_profiles
        sig_prof, risk_prof = resolve_xauusd_research_profiles(
            calibration_artifact_id="xauusd_calibrated_profile_champion"
        )

        sig_dry, risk_dry, state_dry = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test-stage2-sha",
            signal_profile=sig_prof,
            risk_profile=risk_prof,
            dry_run=True,
        )

        # Verify scores and calculation structures are fully populated
        assert sig_dry is not None
        assert sig_dry.long_direction_score is not None
        assert sig_dry.short_direction_score is not None
        assert sig_dry.long_timing_score is not None
        assert sig_dry.short_timing_score is not None
        assert sig_dry.analysis_fingerprint != ""
        assert sig_dry.id is None  # In-memory only!

        if risk_dry is not None:
            assert risk_dry.id is None  # In-memory only!

        # Persisted row counts MUST be completely unchanged
        assert SignalRecord.objects.count() == sig_count_before
        assert LiveRiskPlanRecord.objects.count() == risk_count_before
        assert CycleSnapshotRecord.objects.count() == snap_count_before
        assert AlertEvent.objects.count() == alert_count_before

    def test_case_g_market_closed_audit_creates_zero_live_side_effects(self, xauusd_setup):
        """
        CASE G: Market CLOSED + audit of old Friday candle
        => computes in DRY_RUN
        => creates zero live side effects
        """
        t_open = datetime(2026, 9, 25, 20, 45, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
        event = _build_closed_event(t_open, t_close)

        # Audit executed on Sunday afternoon during market closure
        sunday_closure = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
        assert is_expected_market_closure(sunday_closure)

        sig_count_before = SignalRecord.objects.count()
        risk_count_before = LiveRiskPlanRecord.objects.count()
        snap_count_before = CycleSnapshotRecord.objects.count()
        alert_count_before = AlertEvent.objects.count()

        with patch("apps.alerts.services.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_closure
            mock_dt.fromisoformat = datetime.fromisoformat
            sig_dry, risk_dry, state_dry = XauUsdLiveDecisionPipelineService.process_closed_candle(
                event=event,
                code_revision="test-stage2-sha",
                dry_run=True,
            )

        assert sig_dry is not None
        assert sig_dry.id is None
        assert SignalRecord.objects.count() == sig_count_before
        assert LiveRiskPlanRecord.objects.count() == risk_count_before
        assert CycleSnapshotRecord.objects.count() == snap_count_before
        assert AlertEvent.objects.count() == alert_count_before
