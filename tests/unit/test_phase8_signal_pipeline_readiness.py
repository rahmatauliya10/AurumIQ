"""Targeted unit tests for Phase 8 Signal-Capable Runtime Readiness."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from unittest.mock import MagicMock, patch

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentType, ListingRole, ListingStatus, MarketListing, ProviderHealthSnapshot
from apps.live_monitor.adapter import PublicMarketDataAdapter
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord, PaperObservationRecord
from apps.live_monitor.recovery import catch_up_unprocessed_xauusd_candles
from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
from apps.market_data.macro.replay import resolve_macro_events_as_of
from apps.market_data.models import DataQualitySnapshot, MacroEventFamily, MacroEventIdentity, MacroObservationVintage, MacroScheduleVintage, MarketCandle, PublicationStatus, ScheduleProvenanceType, ScheduleStatus, SourceSnapshot
from apps.market_data.tasks import ingest_primary_candles
from apps.signals.models import SignalRecord
from engine.core.types import CandleData, EventImpact, FeedHealthStatus, MacroEvent, MacroEventContext, SignalState, UserDecision
from engine.cycles.engine import RobustTimeCycleEngine
from engine.cycles.events import evaluate_macro_event_risk
from engine.signals.engine import XauUsdSignalEngine
from engine.signals.profile import Phase4CalibrationStatus, Phase4FeedPolicy, Phase4SignalProfile, SideDirectionPolicy, SideGatePolicy, SideTimingPolicy


@pytest.fixture
def xauusd_instruments(db):
    base, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": AssetType.COMMODITY})
    quote, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=base,
        quote_asset=quote,
        defaults={"instrument_type": InstrumentType.SPOT, "role": "PRIMARY_XAUUSD_SPOT", "is_active": True},
    )
    primary_listing, _ = MarketListing.objects.get_or_create(
        instrument=inst,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        defaults={"provider": "twelve_data_xauusd", "provider_symbol": "XAU/USD", "status": ListingStatus.ACTIVE},
    )
    ProviderHealthSnapshot.objects.create(
        listing=primary_listing,
        status="HEALTHY",
        latency_ms=45.0,
        checked_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
    )
    return inst, primary_listing


@pytest.mark.django_db
class TestCandleAlignmentAndFreshness:
    def test_candle_timestamp_open_and_close_order(self, xauusd_instruments):
        inst, _ = xauusd_instruments
        t_open = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 11, 12, 15, 0, tzinfo=timezone.utc)
        candle = MarketCandle.objects.create(
            instrument=inst,
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2498.00"),
            close=Decimal("2502.50"),
            volume=Decimal("150"),
            source="twelve_data_xauusd",
            is_closed=True,
        )
        assert candle.timestamp_open < candle.timestamp_close
        assert (candle.timestamp_close - candle.timestamp_open).total_seconds() == 900.0


@pytest.mark.django_db
class TestDuplicateDispatchAndExplicitCatchUp:
    def test_duplicate_poll_does_not_redispatch(self, xauusd_instruments):
        inst, _ = xauusd_instruments
        t_open = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 11, 12, 15, 0, tzinfo=timezone.utc)
        mc1, created1 = MarketCandle.objects.update_or_create(
            instrument=inst,
            timeframe="15m",
            timestamp_close=t_close,
            source="twelve_data_xauusd",
            defaults={"timestamp_open": t_open, "open": Decimal("2500"), "high": Decimal("2505"), "low": Decimal("2498"), "close": Decimal("2502"), "volume": Decimal("100"), "is_closed": True},
        )
        assert created1 is True

        # Second identical poll
        mc2, created2 = MarketCandle.objects.update_or_create(
            instrument=inst,
            timeframe="15m",
            timestamp_close=t_close,
            source="twelve_data_xauusd",
            defaults={"timestamp_open": t_open, "open": Decimal("2500"), "high": Decimal("2505"), "low": Decimal("2498"), "close": Decimal("2502"), "volume": Decimal("100"), "is_closed": True},
        )
        assert created2 is False

    def test_explicit_catch_up_finds_unprocessed_candles(self, xauusd_instruments):
        inst, _ = xauusd_instruments
        t_open = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 11, 11, 15, 0, tzinfo=timezone.utc)
        MarketCandle.objects.create(
            instrument=inst,
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2500"),
            high=Decimal("2505"),
            low=Decimal("2498"),
            close=Decimal("2502"),
            volume=Decimal("100"),
            source="twelve_data_xauusd",
            is_closed=True,
        )
        with patch("apps.live_monitor.tasks.process_xauusd_closed_candle_task.delay") as mock_delay:
            res = catch_up_unprocessed_xauusd_candles(dry_run=False)
            assert res["recovered_count"] == 1
            assert mock_delay.call_count == 1


@pytest.mark.django_db
class TestSecondaryFeedOptionality:
    def test_secondary_optional_does_not_fail_hard(self, xauusd_instruments):
        from apps.market_data.providers.base import ProviderHealth
        from apps.instruments.models import ProviderHealthStatus
        inst, primary_listing = xauusd_instruments
        now_utc = datetime.now(timezone.utc)

        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.health_check.return_value = ProviderHealth(
            provider_id="twelve_data_xauusd",
            status=ProviderHealthStatus.HEALTHY,
            checked_at=now_utc,
            latency_ms=25.0,
        )
        raw_candle = MagicMock()
        raw_candle.timestamp_open = now_utc - timedelta(minutes=30)
        raw_candle.timestamp_close = now_utc - timedelta(minutes=15)
        raw_candle.open = Decimal("2500")
        raw_candle.high = Decimal("2505")
        raw_candle.low = Decimal("2495")
        raw_candle.close = Decimal("2502")
        raw_candle.volume = Decimal("100")
        raw_candle.is_closed = True
        raw_candle.quote_rate = None
        raw_candle.close_usd = None
        raw_candle.volume_evidence = "UNAVAILABLE"
        mock_provider.fetch_candles.return_value = [raw_candle]

        def fake_get_setting(key, default=None):
            if key == "XAUUSD_SECONDARY_CRITICAL":
                return False
            if key == "XAUUSD_MAX_DIVERGENCE_PCT":
                return Decimal("0.0035")
            return default

        with patch("apps.market_data.tasks.registry.get", return_value=mock_provider), \
             patch("apps.market_data.tasks._get_setting", side_effect=fake_get_setting):
            res = ingest_primary_candles(timeframes=["15m"], is_secondary_critical=None)
            assert res["status"] == "success"
            dq = DataQualitySnapshot.objects.filter(instrument=inst, timeframe="15m").order_by("-timestamp").first()
            assert dq is not None
            assert dq.hard_fail is False
            assert dq.is_stale is False


class TestMacroEvidenceAndBlackoutLogic:
    def test_evaluate_macro_event_risk_blackout_active(self):
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        event_time = datetime(2026, 9, 11, 12, 10, tzinfo=timezone.utc)
        event = MacroEvent(
            event_id="FOMC_TEST",
            name="FOMC Meeting",
            scheduled_at=event_time,
            impact=EventImpact.HIGH,
        )
        ctx = evaluate_macro_event_risk(now, events=[event], blackout_pre_minutes=30, blackout_post_minutes=30, is_feed_healthy=True)
        assert ctx.is_in_blackout is True
        assert ctx.is_feed_healthy is True
        assert ctx.minutes_to_next_event == 10

    def test_evaluate_macro_event_risk_no_blackout_healthy(self):
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        past_event_time = datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)
        event = MacroEvent(
            event_id="CPI_PAST",
            name="US CPI",
            scheduled_at=past_event_time,
            released_at=past_event_time,
            initial_value="2.9%",
            impact=EventImpact.HIGH,
        )
        ctx = evaluate_macro_event_risk(now, events=[event], blackout_pre_minutes=30, blackout_post_minutes=30, is_feed_healthy=True)
        assert ctx.is_in_blackout is False
        assert ctx.is_feed_healthy is True
        assert ctx.point_in_time_value == "2.9%"

    def test_evaluate_macro_event_risk_missing_coverage_fails_closed(self):
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        ctx = evaluate_macro_event_risk(now, events=[], is_feed_healthy=False)
        assert ctx.is_feed_healthy is False
        assert ctx.is_in_blackout is False


@pytest.fixture
def isolated_calibration_dir(tmp_path, monkeypatch):
    """Isolated calibration artifact directory ensuring zero production test contamination."""
    cal_dir = tmp_path / "artifacts" / "calibration"
    cal_dir.mkdir(parents=True, exist_ok=True)
    from django.conf import settings
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    return cal_dir


def make_test_only_artifact_payload(
    schema="aurumiq.phase8.signal_calibration.v1",
    instrument="XAUUSD",
    sig_status="CANDIDATE_NOT_FROZEN",
    risk_status="CANDIDATE_NOT_FROZEN",
    direction_weights=None,
    timing_weights=None,
    gate_thresholds=None,
    with_fingerprint=True,
):
    """Generate isolated TEST_ONLY valid calibration artifact payload."""
    from apps.backtests.tasks import compute_calibration_artifact_fingerprint
    sig_dict = {
        "name": "TEST_ONLY_XAUUSD_PROFILE",
        "target_instrument": "XAUUSD",
        "calibration_status": sig_status,
        "timeframe": "15m",
        "long_direction": direction_weights or {
            "weight_regime": 15.0, "weight_trend_1h": 15.0, "weight_trend_4h": 15.0, "weight_trend_1d": 15.0,
            "weight_structure_bos": 15.0, "weight_pullback": 10.0, "weight_momentum": 10.0, "weight_volume": 5.0,
        },
        "short_direction": direction_weights or {
            "weight_regime": 15.0, "weight_trend_1h": 15.0, "weight_trend_4h": 15.0, "weight_trend_1d": 15.0,
            "weight_structure_bos": 15.0, "weight_pullback": 10.0, "weight_momentum": 10.0, "weight_volume": 5.0,
        },
        "long_timing": timing_weights or {
            "weight_entry_zone": 25.0, "weight_reversal_confirmation_15m": 25.0,
            "weight_momentum_turn_15m_1h": 20.0, "weight_phase3a": 20.0, "weight_volume_response": 10.0,
        },
        "short_timing": timing_weights or {
            "weight_entry_zone": 25.0, "weight_reversal_confirmation_15m": 25.0,
            "weight_momentum_turn_15m_1h": 20.0, "weight_phase3a": 20.0, "weight_volume_response": 10.0,
        },
        "long_gate": gate_thresholds or {
            "threshold_watch_direction": 50.0, "threshold_ready_direction": 60.0,
            "threshold_ready_timing": 60.0, "threshold_window_direction": 70.0, "threshold_window_timing": 70.0,
        },
        "short_gate": gate_thresholds or {
            "threshold_watch_direction": 50.0, "threshold_ready_direction": 60.0,
            "threshold_ready_timing": 60.0, "threshold_window_direction": 70.0, "threshold_window_timing": 70.0,
        },
        "feed_policy": {
            "primary_15m": "CRITICAL", "primary_1h": "OPTIONAL", "primary_4h": "OPTIONAL",
            "primary_1d": "OPTIONAL", "secondary_provider": "OPTIONAL", "macro_blackout": "CRITICAL",
            "volume": "OPTIONAL", "phase3a": "OPTIONAL", "phase3b": "INFORMATIONAL",
            "dxy_yields_futures": "INFORMATIONAL",
        },
        "details": {"test_marker": "TEST_ONLY"},
    }

    risk_dict = {
        "name": "TEST_ONLY_XAUUSD_RISK_PROFILE",
        "target_instrument": "XAUUSD",
        "calibration_status": risk_status,
        "long_risk_policy": {
            "structure_buffer": "1.50", "atr_multiplier": "2.00",
            "max_stop_distance_atr": "4.00", "min_rr_tp1": "1.80", "tp2_atr_multiplier": "2.50",
        },
        "short_risk_policy": {
            "structure_buffer": "1.50", "atr_multiplier": "2.00",
            "max_stop_distance_atr": "4.00", "min_rr_tp1": "1.80", "tp2_atr_multiplier": "2.50",
        },
        "long_execution_policy": {
            "latency_seconds": 1.0, "synthetic_spread_pct": "0.02", "slippage_pct": "0.01",
        },
        "short_execution_policy": {
            "latency_seconds": 1.0, "synthetic_spread_pct": "0.02", "slippage_pct": "0.01",
        },
    }

    data = {
        "schema": schema,
        "artifact_id": "TEST_ONLY_ARTIFACT_001",
        "instrument": instrument,
        "signal_profile": sig_dict,
        "risk_profile": risk_dict,
        "created_at": "2026-09-11T12:00:00Z",
    }
    if with_fingerprint:
        data["artifact_fingerprint"] = compute_calibration_artifact_fingerprint(data)
    return data


class TestArtifactResolverContracts:
    """10 targeted contract tests for fail-closed artifact resolution and security."""

    def test_01_resolver_rejects_absolute_path(self):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="C:/Windows/System32/drivers/etc/hosts")
        assert sig is None
        assert risk is None

    def test_02_resolver_rejects_traversal(self):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="../../etc/passwd")
        assert sig is None
        assert risk is None
        sig2, risk2 = resolve_xauusd_research_profiles(calibration_artifact_id="..\\..\\secret.json")
        assert sig2 is None
        assert risk2 is None

    def test_03_resolver_rejects_unknown_artifact(self):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="non_existent_artifact_xyz")
        assert sig is None
        assert risk is None
        sig2, risk2 = resolve_xauusd_research_profiles()
        assert sig2 is None
        assert risk2 is None

    def test_04_resolver_rejects_invalid_schema(self, isolated_calibration_dir):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        payload = make_test_only_artifact_payload(schema="invalid.non_aurumiq.schema")
        art_path = isolated_calibration_dir / "bad_schema.json"
        art_path.write_text(json.dumps(payload), encoding="utf-8")

        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="bad_schema")
        assert sig is None
        assert risk is None

    def test_05_resolver_rejects_non_xauusd(self, isolated_calibration_dir):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        payload = make_test_only_artifact_payload(instrument="BTCUSD")
        art_path = isolated_calibration_dir / "bad_inst.json"
        art_path.write_text(json.dumps(payload), encoding="utf-8")

        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="bad_inst")
        assert sig is None
        assert risk is None

    def test_06_resolver_rejects_disallowed_calibration_status(self, isolated_calibration_dir):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        for bad_status in ("LEGACY_REFERENCE", "PENDING_PHASE6", "UNCALIBRATED", "PENDING_DATA"):
            payload = make_test_only_artifact_payload(sig_status=bad_status)
            art_path = isolated_calibration_dir / f"bad_status_{bad_status}.json"
            art_path.write_text(json.dumps(payload), encoding="utf-8")

            sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id=f"bad_status_{bad_status}")
            assert sig is None, f"Status {bad_status} must be rejected"
            assert risk is None

    def test_07_resolver_validates_fingerprint_integrity(self, isolated_calibration_dir):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        # 1. Tampered fingerprint fails
        payload_bad = make_test_only_artifact_payload(with_fingerprint=False)
        payload_bad["artifact_fingerprint"] = "0" * 64
        (isolated_calibration_dir / "tampered.json").write_text(json.dumps(payload_bad), encoding="utf-8")

        sig_bad, risk_bad = resolve_xauusd_research_profiles(calibration_artifact_id="tampered")
        assert sig_bad is None
        assert risk_bad is None

        # 2. Valid fingerprint succeeds
        payload_good = make_test_only_artifact_payload(with_fingerprint=True)
        (isolated_calibration_dir / "valid_fp.json").write_text(json.dumps(payload_good), encoding="utf-8")

        sig_good, risk_good = resolve_xauusd_research_profiles(calibration_artifact_id="valid_fp")
        assert sig_good is not None
        assert risk_good is not None
        assert sig_good.calibration_status.value == "CANDIDATE_NOT_FROZEN"

    def test_08_resolver_reconstructs_exact_decimals(self, isolated_calibration_dir):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        payload = make_test_only_artifact_payload(with_fingerprint=True)
        (isolated_calibration_dir / "dec_test.json").write_text(json.dumps(payload), encoding="utf-8")

        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="dec_test")
        assert risk is not None
        assert isinstance(risk.long_risk_policy.structure_buffer, Decimal)
        assert risk.long_risk_policy.structure_buffer == Decimal("1.50")
        assert risk.long_risk_policy.atr_multiplier == Decimal("2.00")
        assert risk.long_risk_policy.max_stop_distance_atr == Decimal("4.00")
        assert risk.long_risk_policy.min_rr_tp1 == Decimal("1.80")
        assert risk.long_risk_policy.tp2_atr_multiplier == Decimal("2.50")
        assert risk.long_execution_policy.latency_seconds == 1.0
        assert risk.long_execution_policy.synthetic_spread_pct == Decimal("0.02")

    def test_09_resolver_rejects_incomplete_or_invalid_weights(self, isolated_calibration_dir):
        from apps.backtests.tasks import resolve_xauusd_research_profiles
        # Direction weights sum to 80.0 instead of 100.0
        bad_weights = {
            "weight_regime": 10.0, "weight_trend_1h": 10.0, "weight_trend_4h": 10.0, "weight_trend_1d": 10.0,
            "weight_structure_bos": 10.0, "weight_pullback": 10.0, "weight_momentum": 10.0, "weight_volume": 10.0,
        }
        payload = make_test_only_artifact_payload(direction_weights=bad_weights, with_fingerprint=True)
        (isolated_calibration_dir / "bad_weights.json").write_text(json.dumps(payload), encoding="utf-8")

        sig, risk = resolve_xauusd_research_profiles(calibration_artifact_id="bad_weights")
        assert sig is None
        assert risk is None

    def test_10_canonical_fingerprint_key_order_stability(self):
        from apps.backtests.tasks import compute_calibration_artifact_fingerprint
        d1 = {"schema": "aurumiq.test.v1", "instrument": "XAUUSD", "value": 42}
        d2 = {"value": 42, "instrument": "XAUUSD", "schema": "aurumiq.test.v1"}
        d3 = {"schema": "aurumiq.test.v1", "instrument": "XAUUSD", "value": 99}

        # Key order invariance
        assert compute_calibration_artifact_fingerprint(d1) == compute_calibration_artifact_fingerprint(d2)
        # Value change sensitivity
        assert compute_calibration_artifact_fingerprint(d1) != compute_calibration_artifact_fingerprint(d3)


class TestCalibrationDualSideAndWalkForwardContracts:
    """4 targeted contract tests for dual-side independence, walk-forward, purge/embargo, and Phase 8 boundary."""

    def test_11_dual_side_policy_independence(self):
        long_dir = SideDirectionPolicy(
            weight_regime=30.0, weight_trend_1h=20.0, weight_trend_4h=10.0, weight_trend_1d=10.0,
            weight_structure_bos=10.0, weight_pullback=10.0, weight_momentum=5.0, weight_volume=5.0,
        )
        short_dir = SideDirectionPolicy(
            weight_regime=10.0, weight_trend_1h=10.0, weight_trend_4h=20.0, weight_trend_1d=20.0,
            weight_structure_bos=20.0, weight_pullback=10.0, weight_momentum=5.0, weight_volume=5.0,
        )
        assert long_dir.is_configured is True
        assert short_dir.is_configured is True
        assert long_dir.weight_regime != short_dir.weight_regime

    def test_12_walk_forward_fold_generation_determinism(self):
        from engine.backtest.xauusd_types import XauUsdWalkForwardConfig
        from engine.backtest.xauusd_walkforward import XauUsdChronologicalFoldGenerator
        start = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        cfg = XauUsdWalkForwardConfig(total_folds=3, train_ratio=0.6, val_ratio=0.2, oos_ratio=0.2, embargo_seconds=3600.0)

        folds1 = XauUsdChronologicalFoldGenerator.generate_folds(start, end, cfg)
        folds2 = XauUsdChronologicalFoldGenerator.generate_folds(start, end, cfg)
        assert len(folds1) == 3
        assert [f.train_start for f in folds1] == [f.train_start for f in folds2]
        assert [f.oos_end for f in folds1] == [f.oos_end for f in folds2]

    def test_13_purge_and_embargo_preservation(self):
        from engine.backtest.purge import PurgeEngine
        from engine.backtest.xauusd_types import XauUsdSimulatedTrade, XauUsdTradeOutcome
        from engine.core.types import SignalSide, SignalState, UserDecision
        p_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        p_end = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

        t_contained = XauUsdSimulatedTrade(
            trade_id="t1", side=SignalSide.LONG, candidate_state=SignalState.BUY_WINDOW,
            candidate_user_decision=UserDecision.BUY, source_signal_fingerprint="s1",
            signal_timestamp=p_start + timedelta(hours=2), risk_plan_fingerprint="r1",
            planned_risk_amount=Decimal("5.00"), outcome=XauUsdTradeOutcome.TP1_FIRST,
            dependency_window=(p_start + timedelta(hours=2), p_start + timedelta(hours=4)),
            dependency_end_timestamp=p_start + timedelta(hours=4),
        )
        t_crossing = XauUsdSimulatedTrade(
            trade_id="t2", side=SignalSide.LONG, candidate_state=SignalState.BUY_WINDOW,
            candidate_user_decision=UserDecision.BUY, source_signal_fingerprint="s2",
            signal_timestamp=p_start + timedelta(hours=10), risk_plan_fingerprint="r2",
            planned_risk_amount=Decimal("5.00"), outcome=XauUsdTradeOutcome.TP1_FIRST,
            dependency_window=(p_start + timedelta(hours=10), p_start + timedelta(hours=14)),
            dependency_end_timestamp=p_start + timedelta(hours=14),
        )

        res = PurgeEngine.filter_partition(trades=[t_contained, t_crossing], partition_start=p_start, partition_end=p_end, purge_overlapping=True)
        assert len(res.eligible_trades) == 1
        assert res.eligible_trades[0].trade_id == "t1"
        assert len(res.purged_trades) == 1
        assert res.purged_trades[0].trade_id == "t2"

    def test_14_calibration_data_strictly_precedes_phase8_observation(self):
        from pathlib import Path
        manifest_path = Path("artifacts/calibration/xauusd_data_manifest.json")
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cal_end_str = manifest.get("data_end")
        cal_end = datetime.fromisoformat(cal_end_str)

        phase8_start = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
        assert cal_end < phase8_start, f"Data leakage: calibration data end {cal_end} is not before Phase 8 start {phase8_start}"


class TestLivePipelineAndAuthorityLockContracts:
    """2 targeted contract tests for uncalibrated WAIT baseline and Layer B authority lock."""

    def _create_candles(self, n=50, start_price=2500.0, trend=1.0):
        t0 = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
        candles = []
        p = Decimal(str(start_price))
        for i in range(n):
            o = p
            h = o + Decimal("3.00")
            l = o - Decimal("2.00")
            c = o + Decimal(str(trend * 0.5))
            candles.append(
                CandleData(
                    timestamp_open=t0 + timedelta(minutes=15 * i),
                    timestamp_close=t0 + timedelta(minutes=15 * (i + 1)),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=Decimal("100"),
                    is_closed=True,
                )
            )
            p = c
        return candles

    def test_15_pure_engine_emits_wait_under_uncalibrated_profile(self):
        from engine.core.types import RuntimeFeedHealth
        candles = self._create_candles(50)
        engine = XauUsdSignalEngine(code_revision="test-rev")
        health = RuntimeFeedHealth(
            primary_15m=FeedHealthStatus.HEALTHY,
            primary_1h=FeedHealthStatus.HEALTHY,
            primary_4h=FeedHealthStatus.HEALTHY,
            primary_1d=FeedHealthStatus.HEALTHY,
            macro_blackout_feed=FeedHealthStatus.HEALTHY,
            phase3a=FeedHealthStatus.HEALTHY,
        )
        snap = engine.analyze(
            closed_candles_15m=candles,
            closed_candles_1h=candles[:30],
            closed_candles_4h=candles[:15],
            closed_candles_1d=candles[:5],
            runtime_health=health,
            as_of=candles[-1].timestamp_close,
        )
        assert snap.state == SignalState.NO_TRADE
        assert snap.user_decision == UserDecision.WAIT
        assert snap.long_direction.total_score is None
        assert snap.short_direction.total_score is None
        assert snap.long_direction.is_direction_ready is False

    def test_16_layer_b_remains_strictly_wait(self, db, xauusd_instruments):
        inst, _ = xauusd_instruments
        t_open = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 9, 11, 12, 15, 0, tzinfo=timezone.utc)
        event = PublicMarketDataAdapter.create_xauusd_candle_closed_event(
            instrument="XAUUSD",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open_price=Decimal("2500"),
            high_price=Decimal("2505"),
            low_price=Decimal("2498"),
            close_price=Decimal("2502"),
            volume=Decimal("100"),
            source="twelve_data_xauusd",
            is_closed=True,
        )
        sig, risk, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=event,
            code_revision="test-rev",
            is_feed_stale=False,
        )
        assert state.published_user_decision == "WAIT"
        assert state.publication_effective_action == "WAIT"
