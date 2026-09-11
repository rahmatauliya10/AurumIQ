"""
Unit and integration tests for Phase 8: Live Paper Observation (XAUUSD Dual-Side BUY + SELL).

Validates:
  1. Unfinished decision candle cannot generate final observation.
  2. Signal at timestamp T cannot read candles after T (Zero lookahead).
  3. 1m/5m data cannot independently create a strategic signal.
  4. BUY side outcome logic (TP upward, SL downward).
  5. SELL side outcome logic (TP downward, SL upward).
  6. Identical input produces identical decision (Deterministic reproducibility fingerprint).
  7. Active friction model is resolved point-in-time.
  8. Wrong venue/tier/legal entity fails closed.
  9. Live order execution is impossible from Phase 8 runner.
  10. Replay does not mutate original observation records.
  11. Live-vs-replay parity reports BUY, SELL, and COMBINED separately.
  12. 14-day status cannot become COMPLETE early.
  13. Live Phase 8 observation consumes existing SignalRecord.
  14. Existing LiveRiskPlanRecord remains authoritative for risk geometry.
  15. Phase 8 does not create duplicate strategic SignalRecord for the same analysis fingerprint.
  16. Monetary paper PnL cannot be calculated without explicit paper lot size.
  17. Expected weekend/market closure does not mark INTERRUPTED.
  18. Missed eligible market-open evaluation does mark operational failure.
  19. Structural AST scan proves Phase 8 package has zero broker order execution dependencies.
"""
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.live_monitor.models import (
    LiveRiskPlanRecord,
    PaperObservationRecord,
    Phase8InterruptionRecord,
    Phase8OperationalState,
)
from apps.live_monitor.paper_service import Phase8PaperService
from apps.market_data.friction.resolution import resolve_friction_model
from apps.market_data.models import (
    FrictionActivationStatus,
    FrictionModelActivation,
    FrictionModelVersion,
    FrictionSourceSnapshot,
    FrictionSourceType,
    MarketCandle,
)
from apps.signals.models import SignalRecord
from engine.backtest.xauusd_types import XauUsdCostConfig
from engine.core.types import (
    CandleData,
    IntrabarPolicy,
    RiskSide,
    SideRiskPlanSnapshot,
    SignalState,
    UserDecision,
)
from engine.paper.continuity import (
    OBSERVATION_WINDOW_CALENDAR_DAYS,
    Phase8ContinuityTracker,
    is_expected_market_closure,
)
from engine.paper.fingerprint import compute_observation_fingerprint
from engine.paper.guards import (
    FORBIDDEN_EXECUTION_SYMBOLS,
    PAPER_ONLY,
    REAL_ORDER_EXECUTION,
    assert_paper_execution_safety,
)
from engine.paper.observer import Phase8PaperObserver
from engine.paper.parity import Phase8ParityAuditor
from engine.paper.types import (
    InterruptionCategory,
    PaperOutcomeType,
    ParityDimension,
    Phase8Status,
)


from django.core.management import call_command


@pytest.fixture
def xauusd_instrument(db):
    call_command("seed_instruments")
    return Instrument.get_canonical_xauusd()



@pytest.fixture
def active_empirical_friction(db):
    now = datetime.now(timezone.utc)
    snap, _ = FrictionSourceSnapshot.objects.get_or_create(
        snapshot_id="SNAP_TEST_P8_LEGAL",
        defaults={
            "source_url": "https://example.com/source",
            "source_name": "Official Source",
            "source_type": FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
            "venue": "EXNESS",
            "symbol": "XAUUSD",
            "account_tier": "STANDARD_CENT",
            "retrieved_at": now,
            "known_at": now,
            "raw_payload_bytes_sha256": "a" * 64,
            "metadata": {},
        },
    )
    model_ver, _ = FrictionModelVersion.objects.get_or_create(
        model_version_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        defaults={
            "venue": "EXNESS",
            "account_tier": "STANDARD_CENT",
            "symbol": "XAUUSD",
            "legal_entity_code": "EXNESS_SC_LTD",
            "legal_entity_name": "Exness (SC) Ltd",
            "regulator": "FSA_SEYCHELLES",
            "license_number": "SD025",
            "legal_entity_source_snapshot": snap,
            "contract_size": Decimal("1.00"),
            "digits": 3,
            "point_size": Decimal("0.001"),
            "trade_tick_size": Decimal("0.001"),
            "trade_tick_value": Decimal("0.1"),
            "volume_min": Decimal("0.01"),
            "volume_max": Decimal("200.00"),
            "volume_step": Decimal("0.01"),
            "base_spread_bps": Decimal("0.8338"),
            "stress_spread_bps": Decimal("1.1779"),
            "base_slippage_bps": Decimal("0.0000"),
            "stress_slippage_bps": Decimal("0.0000"),
            "native_commission_usd_per_lot_per_side": Decimal("0.0000"),
            "commission_formula": "ZERO_COMMISSION",
            "swap_long_points": Decimal("-698.50"),
            "swap_short_points": Decimal("0.00"),
            "empirical_friction_evidence_fingerprint": "c" * 64,
        },
    )
    activation, _ = FrictionModelActivation.objects.get_or_create(
        activation_id="P8_TEST_ACTIVATION_1",
        defaults={
            "friction_model_version": model_ver,
            "activation_status": FrictionActivationStatus.ACTIVE,
            "effective_from": now - timedelta(days=30),
            "known_at": now - timedelta(days=30),
            "source_or_reason": "Phase 8 qualification",
        },
    )
    return model_ver



def make_candle(
    ts_open: datetime,
    ts_close: datetime,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    is_closed: bool = True,
) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_close,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=Decimal("100.0"),
        is_closed=is_closed,
        quote_rate=Decimal("1.0"),
        close_usd=Decimal(close_p),
        source_id="TEST",
    )


# ---------------------------------------------------------------------------
# 1. Unfinished decision candle cannot generate final observation
# ---------------------------------------------------------------------------
def test_unfinished_decision_candle_cannot_generate_final_observation():
    observer = Phase8PaperObserver()
    with pytest.raises(ValueError, match="INTRABAR_RESOLUTION_ONLY|cannot independently create"):
        observer.validate_decision_timeframe("1m")

    # In service boundary: unclosed event raises ValueError
    from apps.live_monitor.services import CandleClosedEvent, XauUsdLiveDecisionPipelineService
    unclosed_event = CandleClosedEvent(
        event_id="evt_test_unclosed",
        instrument="XAUUSD",
        timeframe="15m",
        timestamp_open=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 9, 11, 10, 15, tzinfo=timezone.utc),
        open=Decimal("2500.0"),
        high=Decimal("2510.0"),
        low=Decimal("2495.0"),
        close=Decimal("2505.0"),
        volume=Decimal("50.0"),
        is_closed=False,
    )
    with pytest.raises(ValueError, match="Unclosed candle"):
        XauUsdLiveDecisionPipelineService.process_closed_candle(
            event=unclosed_event,
            code_revision="test_rev",
        )


# ---------------------------------------------------------------------------
# 2. Signal at timestamp T cannot read candles after T (Zero lookahead)
# ---------------------------------------------------------------------------
def test_signal_at_t_cannot_read_candles_after_t():
    from engine.signals.engine import XauUsdSignalEngine

    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    c1 = make_candle(t0 - timedelta(minutes=30), t0 - timedelta(minutes=15), "2500", "2505", "2498", "2502")
    c2 = make_candle(t0 - timedelta(minutes=15), t0, "2502", "2510", "2500", "2508")
    future_c = make_candle(t0, t0 + timedelta(minutes=15), "2508", "2550", "2505", "2540")

    all_candles = [c1, c2, future_c]
    pit_candles, unclosed = XauUsdSignalEngine.filter_pit_candles(all_candles, as_of=t0)

    assert len(pit_candles) == 2
    assert future_c not in pit_candles
    assert all(c.timestamp_close <= t0 for c in pit_candles)


# ---------------------------------------------------------------------------
# 3. 1m/5m data cannot independently create a strategic signal
# ---------------------------------------------------------------------------
def test_1m_5m_data_cannot_independently_create_strategic_signal():
    observer = Phase8PaperObserver()
    for tf in ["1m", "5m"]:
        with pytest.raises(ValueError, match="INTRABAR_RESOLUTION_ONLY"):
            observer.validate_decision_timeframe(tf)

    # Eligible strategic timeframes succeed
    for tf in ["15m", "1h", "4h", "1d"]:
        observer.validate_decision_timeframe(tf)


# ---------------------------------------------------------------------------
# 4. BUY side outcome logic is correct (TP upward, SL downward)
# ---------------------------------------------------------------------------
def test_buy_side_outcome_logic(xauusd_instrument):
    observer = Phase8PaperObserver()
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

    sig = SignalRecord(
        analysis_fingerprint="sig_buy_1",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="test_code",
    )
    risk = LiveRiskPlanRecord(
        source_signal_fingerprint="sig_buy_1",
        signal_timestamp=t0,
        instrument="XAUUSD",
        risk_side="LONG",
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action="BUY",
        entry_min=Decimal("2500.0"),
        entry_mid=Decimal("2502.0"),
        entry_max=Decimal("2504.0"),
        stop_final=Decimal("2490.0"),  # SL below entry
        tp1=Decimal("2520.0"),         # TP above entry
    )

    # Future candle touching TP1 (high >= 2520)
    future_c = [
        make_candle(t0, t0 + timedelta(minutes=15), "2504", "2525", "2502", "2522"),
    ]

    obs = observer.observe_production_setup(
        signal_record=sig,
        risk_record=risk,
        future_candles_15m=future_c,
    )

    assert obs.side == "BUY"
    assert obs.outcome == PaperOutcomeType.TP1_FIRST.value
    assert obs.paper_entry_price_basis is not None
    assert obs.paper_exit_price == Decimal("2520.0")
    assert obs.gross_r > Decimal("0")


# ---------------------------------------------------------------------------
# 5. SELL side outcome logic is correct (TP downward, SL upward)
# ---------------------------------------------------------------------------
def test_sell_side_outcome_logic(xauusd_instrument):
    observer = Phase8PaperObserver()
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

    sig = SignalRecord(
        analysis_fingerprint="sig_sell_1",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="SELL_WINDOW",
        user_decision="SELL",
        code_revision="test_code",
    )
    risk = LiveRiskPlanRecord(
        source_signal_fingerprint="sig_sell_1",
        signal_timestamp=t0,
        instrument="XAUUSD",
        risk_side="SHORT",
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action="SELL",
        entry_min=Decimal("2500.0"),
        entry_mid=Decimal("2502.0"),
        entry_max=Decimal("2504.0"),
        stop_final=Decimal("2515.0"),  # SL above entry for SHORT
        tp1=Decimal("2485.0"),         # TP below entry for SHORT
    )

    # Future candle touching TP1 (low <= 2485)
    future_c = [
        make_candle(t0, t0 + timedelta(minutes=15), "2502", "2504", "2480", "2482"),
    ]

    obs = observer.observe_production_setup(
        signal_record=sig,
        risk_record=risk,
        future_candles_15m=future_c,
    )

    assert obs.side == "SELL"
    assert obs.outcome == PaperOutcomeType.TP1_FIRST.value
    assert obs.paper_exit_price == Decimal("2485.0")
    assert obs.gross_r > Decimal("0")


# ---------------------------------------------------------------------------
# 6. Identical input produces identical decision (Deterministic fingerprint)
# ---------------------------------------------------------------------------
def test_identical_input_produces_identical_decision():
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    fp1 = compute_observation_fingerprint(
        code_revision="rev123",
        engine_version="4.0.0",
        config_version="cfg-2026-v1",
        market_data_hash="hash_abc",
        friction_model_version_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        decision_timestamp=t0,
        decision_timeframe="15m",
        side="BUY",
        source_signal_fingerprint="sig_123",
    )
    fp2 = compute_observation_fingerprint(
        code_revision="rev123",
        engine_version="4.0.0",
        config_version="cfg-2026-v1",
        market_data_hash="hash_abc",
        friction_model_version_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        decision_timestamp=t0,
        decision_timeframe="15m",
        side="BUY",
        source_signal_fingerprint="sig_123",
    )
    assert fp1 == fp2
    assert len(fp1) == 64

    # Different side produces distinct fingerprint
    fp_sell = compute_observation_fingerprint(
        code_revision="rev123",
        engine_version="4.0.0",
        config_version="cfg-2026-v1",
        market_data_hash="hash_abc",
        friction_model_version_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        decision_timestamp=t0,
        decision_timeframe="15m",
        side="SELL",
        source_signal_fingerprint="sig_123",
    )
    assert fp1 != fp_sell


# ---------------------------------------------------------------------------
# 7. Active friction model resolved point-in-time
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_active_friction_model_resolved_point_in_time(active_empirical_friction):
    now_utc = datetime.now(timezone.utc)
    model = resolve_friction_model(
        as_of=now_utc,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        legal_entity_code="EXNESS_SC_LTD",
    )
    assert model is not None
    assert model.model_version_id == "EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1"
    assert model.base_spread_bps == Decimal("0.8338")
    assert model.base_slippage_bps == Decimal("0.0000")


# ---------------------------------------------------------------------------
# 8. Wrong venue/tier/legal entity fails closed
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_wrong_venue_tier_legal_entity_fails_closed(active_empirical_friction):
    now_utc = datetime.now(timezone.utc)
    # Wrong venue
    assert resolve_friction_model(as_of=now_utc, venue="UNKNOWN", symbol="XAUUSD", account_tier="STANDARD_CENT", legal_entity_code="EXNESS_SC_LTD") is None
    # Wrong tier
    assert resolve_friction_model(as_of=now_utc, venue="EXNESS", symbol="XAUUSD", account_tier="STANDARD_PRO", legal_entity_code="EXNESS_SC_LTD") is None
    # Wrong legal entity
    assert resolve_friction_model(as_of=now_utc, venue="EXNESS", symbol="XAUUSD", account_tier="STANDARD_CENT", legal_entity_code="WRONG_ENTITY") is None


# ---------------------------------------------------------------------------
# 9. Live order execution is impossible from Phase 8 runner
# ---------------------------------------------------------------------------
def test_live_order_execution_impossible_from_phase8_runner():
    assert PAPER_ONLY is True
    assert REAL_ORDER_EXECUTION == "disabled"

    # assert_paper_execution_safety succeeds on paper action
    assert_paper_execution_safety("paper_observation")

    # Raises RuntimeError on attempted broker / send action
    with pytest.raises(RuntimeError, match="CRITICAL SAFETY VIOLATION"):
        assert_paper_execution_safety("submit_live_order")


# ---------------------------------------------------------------------------
# 10. Replay does not mutate the original observation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_replay_does_not_mutate_original_observation(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_replay_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        long_direction_score=85.0,
        code_revision="rev_test",
    )
    risk = LiveRiskPlanRecord.objects.create(
        source_signal_fingerprint="sig_replay_test",
        signal_timestamp=t0,
        instrument="XAUUSD",
        risk_side="LONG",
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action="BUY",
    )

    obs = Phase8PaperService.process_production_signal_observation(sig, risk)
    initial_outcome = obs.outcome
    initial_score = obs.signal_score

    # Replay audit run
    auditor = Phase8ParityAuditor()
    comp = auditor.compare_item(
        observation_id=obs.observation_id,
        side=obs.side,
        decision_timestamp=obs.decision_timestamp,
        live_decision=obs.signal_decision,
        replay_decision="BUY",
        live_score=obs.signal_score,
        replay_score=85.0,
        live_entry_eligible=True,
        replay_entry_eligible=True,
        live_friction_model_id=obs.friction_model_version_id,
        replay_friction_model_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        live_outcome=obs.outcome,
        replay_outcome=initial_outcome,
    )
    report = auditor.audit_parity([comp])

    # Refresh from database and verify 100% unmutated
    obs_refreshed = PaperObservationRecord.objects.get(observation_id=obs.observation_id)
    assert obs_refreshed.outcome == initial_outcome
    assert obs_refreshed.signal_score == initial_score
    assert report.overall_status == "PASS"


# ---------------------------------------------------------------------------
# 11. Live-vs-replay parity reports BUY, SELL, COMBINED separately
# ---------------------------------------------------------------------------
def test_live_vs_replay_parity_reports_buy_sell_combined_separately():
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    auditor = Phase8ParityAuditor()

    buy_comp = auditor.compare_item(
        observation_id="obs_b1",
        side="BUY",
        decision_timestamp=t0,
        live_decision="BUY",
        replay_decision="BUY",
        live_score=80.0,
        replay_score=80.0,
        live_entry_eligible=True,
        replay_entry_eligible=True,
        live_friction_model_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        replay_friction_model_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        live_outcome="TP1_FIRST",
        replay_outcome="TP1_FIRST",
    )
    sell_comp = auditor.compare_item(
        observation_id="obs_s1",
        side="SELL",
        decision_timestamp=t0,
        live_decision="SELL",
        replay_decision="SELL",
        live_score=75.0,
        replay_score=75.0,
        live_entry_eligible=True,
        replay_entry_eligible=True,
        live_friction_model_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        replay_friction_model_id="EXNESS_XAUUSD_STANDARD_CENT_EMPIRICAL_V1",
        live_outcome="SL_FIRST",
        replay_outcome="SL_FIRST",
    )

    report = auditor.audit_parity([buy_comp, sell_comp])

    assert report.buy_parity.dimension == ParityDimension.BUY
    assert report.buy_parity.total_evaluated == 1
    assert report.buy_parity.is_parity_passed is True

    assert report.sell_parity.dimension == ParityDimension.SELL
    assert report.sell_parity.total_evaluated == 1
    assert report.sell_parity.is_parity_passed is True

    assert report.combined_parity.dimension == ParityDimension.COMBINED
    assert report.combined_parity.total_evaluated == 2
    assert report.combined_parity.is_parity_passed is True
    assert report.overall_status == "PASS"


# ---------------------------------------------------------------------------
# 12. 14-day status cannot become COMPLETE early
# ---------------------------------------------------------------------------
def test_14_day_status_cannot_become_complete_early():
    start_time = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    tracker = Phase8ContinuityTracker(window_start=start_time)

    # Day 1: 1 hour after start
    status_day1 = tracker.evaluate_gate_status(start_time + timedelta(hours=1))
    assert status_day1 == Phase8Status.OBSERVING

    # Day 7: cannot be COMPLETE
    status_day7 = tracker.evaluate_gate_status(start_time + timedelta(days=7))
    assert status_day7 == Phase8Status.OBSERVING

    # Day 13: cannot be COMPLETE
    status_day13 = tracker.evaluate_gate_status(start_time + timedelta(days=13, hours=23))
    assert status_day13 == Phase8Status.OBSERVING

    # Day 14+ with 0 failures can become COMPLETE
    status_day14 = tracker.evaluate_gate_status(start_time + timedelta(days=14, minutes=1))
    assert status_day14 == Phase8Status.COMPLETE


# ---------------------------------------------------------------------------
# 13. Live Phase 8 observation consumes existing SignalRecord
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_live_phase8_observation_consumes_existing_signal_record(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_consumed_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        long_direction_score=88.5,
        code_revision="rev_consume",
    )

    obs = Phase8PaperService.process_production_signal_observation(sig)
    assert obs.source_signal_record == sig
    assert obs.source_signal_fingerprint == "sig_consumed_test"
    assert obs.signal_score == 88.5


# ---------------------------------------------------------------------------
# 14. Existing LiveRiskPlanRecord remains authoritative for risk geometry
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_existing_liveriskplanrecord_remains_authoritative(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_risk_auth_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_risk",
    )
    risk = LiveRiskPlanRecord.objects.create(
        source_signal_fingerprint="sig_risk_auth_test",
        signal_timestamp=t0,
        instrument="XAUUSD",
        risk_side="LONG",
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action="BUY",
        entry_min=Decimal("2510.0"),
        entry_max=Decimal("2514.0"),
        stop_final=Decimal("2498.0"),
        tp1=Decimal("2535.0"),
    )

    obs = Phase8PaperService.process_production_signal_observation(sig, risk)
    assert obs.source_risk_plan_record == risk
    assert obs.source_risk_plan_fingerprint == risk.risk_plan_fingerprint


# ---------------------------------------------------------------------------
# 15. Phase 8 does not create duplicate SignalRecord
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_phase8_does_not_create_duplicate_signal_record(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_no_duplicate_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_nodup",
    )
    count_before = SignalRecord.objects.count()

    Phase8PaperService.process_production_signal_observation(sig)
    count_after = SignalRecord.objects.count()

    assert count_before == count_after, "Phase 8 observation must NOT create a duplicate SignalRecord"


# ---------------------------------------------------------------------------
# 16. Monetary paper PnL requires explicit position size
# ---------------------------------------------------------------------------
def test_monetary_paper_pnl_requires_explicit_position_size(xauusd_instrument):
    observer = Phase8PaperObserver()
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

    sig = SignalRecord(
        analysis_fingerprint="sig_pnl_size_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="test_code",
    )
    risk = LiveRiskPlanRecord(
        source_signal_fingerprint="sig_pnl_size_test",
        signal_timestamp=t0,
        instrument="XAUUSD",
        risk_side="LONG",
        is_valid_risk_plan=True,
        execution_eligible=True,
        effective_action="BUY",
        entry_min=Decimal("2500.0"),
        entry_max=Decimal("2504.0"),
        stop_final=Decimal("2490.0"),
        tp1=Decimal("2520.0"),
    )
    future_c = [make_candle(t0, t0 + timedelta(minutes=15), "2504", "2525", "2502", "2522")]

    # Case A: Without paper volume lots -> gross_pnl and net_pnl must be None
    obs_no_vol = observer.observe_production_setup(sig, risk, future_c, paper_volume_lots=None)
    assert obs_no_vol.gross_pnl is None
    assert obs_no_vol.net_pnl is None
    assert obs_no_vol.gross_r is not None  # Normalized R remains authoritative

    # Case B: With explicit paper volume lots (e.g. 0.05 lots) -> calculates monetary PnL in USC
    obs_with_vol = observer.observe_production_setup(sig, risk, future_c, paper_volume_lots=Decimal("0.05"), pnl_currency="USC")
    assert obs_with_vol.paper_volume_lots == Decimal("0.05")
    assert obs_with_vol.pnl_currency == "USC"
    assert obs_with_vol.gross_pnl is not None
    assert obs_with_vol.net_pnl is not None


# ---------------------------------------------------------------------------
# 17. Expected weekend/market closure does not mark INTERRUPTED
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_expected_weekend_market_closure_does_not_mark_interrupted():
    # Saturday 12:00 UTC (Market closed)
    saturday_ts = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert is_expected_market_closure(saturday_ts) is True

    rec = Phase8PaperService.record_interruption(
        category=InterruptionCategory.EXPECTED_MARKET_CLOSURE,
        description="Weekend market closure",
        timestamp=saturday_ts,
    )
    assert rec.is_operational_failure is False

    state = Phase8PaperService.get_or_create_operational_state()
    assert state.status != Phase8Status.INTERRUPTED.value
    assert state.unresolved_integrity_failures == 0


# ---------------------------------------------------------------------------
# 18. Missed eligible evaluation does mark operational failure
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_missed_eligible_evaluation_marks_operational_failure():
    # Tuesday 14:00 UTC (Market open)
    tuesday_ts = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)
    assert is_expected_market_closure(tuesday_ts) is False

    rec = Phase8PaperService.record_interruption(
        category=InterruptionCategory.MISSED_ELIGIBLE_INTERVAL,
        description="Missing closed candle evaluation during market hours",
        timestamp=tuesday_ts,
    )
    assert rec.is_operational_failure is True

    state = Phase8PaperService.get_or_create_operational_state()
    assert state.unresolved_integrity_failures > 0
    assert state.status == Phase8Status.INTERRUPTED.value


# ---------------------------------------------------------------------------
# 19. Structural AST scan proves Phase 8 package has zero order execution dependency
# ---------------------------------------------------------------------------
def test_structural_ast_scan_phase8_has_no_order_execution_dependency():
    paper_dir = Path("engine/paper")
    python_files = list(paper_dir.glob("*.py"))
    assert len(python_files) >= 5, "engine/paper must contain Python modules"

    for py_file in python_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            # Check for forbidden function calls
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                assert func_name not in FORBIDDEN_EXECUTION_SYMBOLS, (
                    f"Forbidden execution call '{func_name}' detected in {py_file}!"
                )
            # Check for forbidden imports
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in FORBIDDEN_EXECUTION_SYMBOLS, (
                        f"Forbidden execution import '{alias.name}' detected in {py_file}!"
                    )


# ---------------------------------------------------------------------------
# 20. Same SignalRecord observed twice creates one record & returns DUPLICATE_NOOP
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_same_signal_observed_twice_creates_one_record_and_duplicate_noop(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_dup_test_01",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_dup1",
    )

    # First observation -> creates record, returns NEW status
    res1 = Phase8PaperService.process_production_signal_observation(sig)
    assert res1.status != "DUPLICATE_NOOP"
    assert PaperObservationRecord.objects.filter(source_signal_fingerprint="sig_dup_test_01").count() == 1

    # Second observation of same signal -> idempotent, returns DUPLICATE_NOOP
    res2 = Phase8PaperService.process_production_signal_observation(sig)
    assert res2.status == "DUPLICATE_NOOP"
    assert PaperObservationRecord.objects.filter(source_signal_fingerprint="sig_dup_test_01").count() == 1


# ---------------------------------------------------------------------------
# 21. Duplicate observation does not refresh success timestamp or advance coverage
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_duplicate_does_not_refresh_success_or_advance_coverage(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_dup_cov_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_cov",
    )

    res1 = Phase8PaperService.process_production_signal_observation(sig)
    state = Phase8PaperService.get_or_create_operational_state()
    assert state.total_signals_evaluated == 1
    assert state.eligible_cycles_observed == 1
    assert state.duplicate_noop_cycles == 0

    fixed_eval_time = datetime(2026, 9, 11, 14, 1, tzinfo=timezone.utc)
    state.last_successful_evaluation_timestamp = fixed_eval_time
    state.save()

    # Repeated calls must NOT advance coverage or refresh success timestamp
    for _ in range(5):
        res = Phase8PaperService.process_production_signal_observation(sig)
        assert res.status == "DUPLICATE_NOOP"

    state_after = Phase8PaperService.get_or_create_operational_state()
    assert state_after.total_signals_evaluated == 1, "Duplicate must NOT increment total_signals_evaluated"
    assert state_after.eligible_cycles_observed == 1, "Duplicate must NOT advance eligible_cycles_observed"
    assert state_after.duplicate_noop_cycles == 5, "Duplicate count must reflect no-op cycles"
    assert state_after.last_successful_evaluation_timestamp == fixed_eval_time, "Duplicate must NOT refresh evaluation timestamp"


# ---------------------------------------------------------------------------
# 22. 14 elapsed days with missing eligible cycles != COMPLETE
# ---------------------------------------------------------------------------
def test_14_elapsed_days_with_missing_eligible_cycles_cannot_be_complete():
    start_time = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    tracker = Phase8ContinuityTracker(
        window_start=start_time,
        unresolved_failures=0,
        eligible_cycles_expected=1000,
        eligible_cycles_observed=990,
        eligible_cycles_missing=10,  # 10 missed cycles
    )

    status = tracker.evaluate_gate_status(
        current_time=start_time + timedelta(days=14, minutes=5),
    )
    assert status == Phase8Status.INTERRUPTED
    assert status != Phase8Status.COMPLETE


# ---------------------------------------------------------------------------
# 23. 14 elapsed days with complete eligible coverage and zero failures = COMPLETE
# ---------------------------------------------------------------------------
def test_14_elapsed_days_with_complete_coverage_and_zero_failures_is_complete():
    start_time = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    eval_time = start_time + timedelta(days=14, minutes=5)

    tracker = Phase8ContinuityTracker(
        window_start=start_time,
        unresolved_failures=0,
        eligible_cycles_expected=1000,
        eligible_cycles_observed=1000,
        eligible_cycles_missing=0,
        last_successful_evaluation=eval_time - timedelta(minutes=5),
        max_stale_seconds=3600.0,
    )

    status = tracker.evaluate_gate_status(current_time=eval_time)
    assert status == Phase8Status.COMPLETE


# ---------------------------------------------------------------------------
# 24. Market closure interval is exempt from staleness during 14-day gate check
# ---------------------------------------------------------------------------
def test_market_closure_interval_is_exempt_from_staleness():
    # Start on Friday Sep 11, 2026 at 00:00 UTC
    start_time = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    # Day 15 is Saturday Sep 26, 2026 at 12:00 UTC (Market closed on Saturday)
    saturday_eval_time = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert is_expected_market_closure(saturday_eval_time) is True

    # Last evaluation was Friday before close (e.g. 20:45 UTC) -> more than 15 hours ago
    last_friday_eval = datetime(2026, 9, 25, 20, 45, tzinfo=timezone.utc)

    tracker = Phase8ContinuityTracker(
        window_start=start_time,
        unresolved_failures=0,
        eligible_cycles_expected=900,
        eligible_cycles_observed=900,
        eligible_cycles_missing=0,
        last_successful_evaluation=last_friday_eval,
        max_stale_seconds=3600.0,  # 1 hour policy
    )

    # Saturday is market closure: staleness check must be EXEMPT
    status = tracker.evaluate_gate_status(current_time=saturday_eval_time)
    assert status == Phase8Status.COMPLETE


# ---------------------------------------------------------------------------
# 25. Missed market-open interval triggers operational failure in watchdog
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_missed_market_open_interval_triggers_watchdog_failure(xauusd_instrument):
    t0 = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)  # Tuesday (Market Open)
    state = Phase8PaperService.get_or_create_operational_state()
    state.observation_window_start = t0
    state.status = Phase8Status.OBSERVING.value
    state.eligible_cycles_missing = 0
    state.unresolved_integrity_failures = 0
    state.save()

    # Closed 15m candle exists at 10:15 UTC
    c1 = MarketCandle.objects.create(
        instrument=xauusd_instrument,
        source="TWELVE_DATA",
        timeframe="15m",
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal("2500.00"),
        high=Decimal("2505.00"),
        low=Decimal("2498.00"),
        close=Decimal("2503.00"),
        volume=Decimal("100"),
        is_closed=True,
    )

    # Audit at 11:00 UTC (> 30m grace after 10:15 close) with NO PaperObservationRecord
    audit_time = t0 + timedelta(minutes=60)
    audit_res = Phase8PaperService.audit_operational_continuity(now_utc=audit_time, grace_minutes=30)

    assert audit_res["eligible_cycles_missing"] == 1
    assert audit_res["status"] == Phase8Status.INTERRUPTED.value
    assert audit_res["unresolved_integrity_failures"] >= 1


# ---------------------------------------------------------------------------
# 26. Phase 8 Celery task remains paper-only and retry-safe
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_phase8_celery_task_remains_paper_only_and_retry_safe(xauusd_instrument, active_empirical_friction):
    from apps.live_monitor.tasks import process_phase8_paper_observation_task

    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_celery_task_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_celery",
    )

    # Run task first time
    res1 = process_phase8_paper_observation_task(signal_fingerprint="sig_celery_task_test")
    assert res1["status"] == "SUCCESS"
    assert res1["operation_status"] != "DUPLICATE_NOOP"
    assert PaperObservationRecord.objects.filter(source_signal_fingerprint="sig_celery_task_test").count() == 1

    # Simulate Celery retry of same task -> must NOT duplicate or error
    res2 = process_phase8_paper_observation_task(signal_fingerprint="sig_celery_task_test")
    assert res2["status"] == "SUCCESS"
    assert res2["operation_status"] == "DUPLICATE_NOOP"
    assert PaperObservationRecord.objects.filter(source_signal_fingerprint="sig_celery_task_test").count() == 1


# ---------------------------------------------------------------------------
# 27. Explicit paper_volume_lots flows into paper observation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_explicit_paper_volume_lots_flows_into_paper_observation(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_volume_flow_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_vol",
    )

    # Supply explicit volume of 0.25 lots
    res = Phase8PaperService.process_production_signal_observation(
        signal_record=sig,
        paper_volume_lots=Decimal("0.25"),
        pnl_currency="USC",
    )

    assert res.paper_volume_lots == Decimal("0.25")
    assert res.pnl_currency == "USC"


# ---------------------------------------------------------------------------
# 28. Omitted lot size leaves monetary PnL null
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_omitted_lot_size_leaves_monetary_pnl_null(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_pnl_null_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_pnl_null",
    )

    res = Phase8PaperService.process_production_signal_observation(
        signal_record=sig,
        paper_volume_lots=None,
    )

    assert res.paper_volume_lots is None
    assert res.gross_pnl is None
    assert res.net_pnl is None


# ---------------------------------------------------------------------------
# 29. True requested-price slippage remains UNOBSERVABLE
# ---------------------------------------------------------------------------
def test_true_requested_price_slippage_remains_unobservable():
    import json
    from pathlib import Path

    manifest_path = Path("artifacts/calibration/xauusd_standard_cent_empirical_friction_manifest.json")
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    telemetry = data["evidence_inventory"]["execution_slippage_telemetry"]

    # True requested-price slippage is strictly UNOBSERVABLE
    assert telemetry["true_requested_price_slippage"] == "UNOBSERVABLE"
    assert telemetry["execution_gap_vs_reference_quote_mean_points"] == 0.0


# ---------------------------------------------------------------------------
# 30. Step observation cycle forwards paper volume lots
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_step_observation_cycle_forwards_paper_volume_lots(xauusd_instrument, active_empirical_friction):
    t0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    sig = SignalRecord.objects.create(
        analysis_fingerprint="sig_step_vol_test",
        instrument=xauusd_instrument,
        timeframe="15m",
        timestamp=t0,
        state="BUY_WINDOW",
        user_decision="BUY",
        code_revision="rev_step_vol",
    )

    count, status = Phase8PaperService.step_observation_cycle(paper_volume_lots=Decimal("0.50"))
    assert count == 1
    assert status != "DUPLICATE_NOOP"

    obs = PaperObservationRecord.objects.get(source_signal_fingerprint="sig_step_vol_test")
    assert obs.paper_volume_lots == Decimal("0.50")
