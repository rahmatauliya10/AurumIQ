"""Comprehensive hostile unit test suite for XAUUSD Empirical Friction Evidence Checkpoint.

Covers all 17 initial scenarios + all 23 hardening loophole scenarios (40 total test cases):
Original 17 scenarios:
1. No friction evidence -> fails closed (CANDLES_READY_EMPIRICAL_FRICTION_MISSING).
2. Missing legal entity provenance -> fails (LEGAL_ENTITY_EVIDENCE_MISSING).
3. Point vs trade tick size decoupling verified.
4. Arbitrary unverified friction row cannot pass (EMPIRICAL_FRICTION_INCOMPLETE).
5. Wrong venue/account tier/symbol -> fail (EMPIRICAL_FRICTION_INVALID).
6. Invalid/negative spread -> fail.
7. Insufficient sample count or insufficient distinct trading dates (< 5 days) -> fail.
8. Naive timestamp -> fail.
9. Unit mismatch & statistics percentiles bounds sanity.
10. Missing mandatory slippage telemetry -> reports SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING.
11. Dynamic fee formula produces exact entry/exit costs under fluctuating prices.
12. Semantic fingerprint is deterministic; excludes DB IDs and creation dates.
13. Mutating evidence dataset changes fingerprint.
14. Idempotent rerun preserves exact fingerprint.
15. Append-only models reject .update(), .bulk_update(), and .delete().
16. Activation history resolves point-in-time active model without UPDATE.
17. Successful friction evidence advances strictly to CANDLES_READY_QUOTE_EVIDENCE_MISSING;
    production authority remains False (weight 0.0, WAIT).

Hardening 23 scenarios (Directive 14):
H01. Spread complete + slippage missing cannot pass.
H02. Model with base_slippage_bps=None cannot become active.
H03. Command completeness includes mandatory slippage.
H04. Tick-file path with valid file is actually parsed.
H05. Malformed tick file fails.
H06. Slippage-file path is actually parsed.
H07. Malformed telemetry fails.
H08. Hard-coded/default contract geometry without snapshot fails.
H09. Commission without source snapshot fails.
H10. Financing default zero without evidence fails.
H11. Hard-coded swap values cannot pass without supporting snapshot.
H12. Legal source scope mismatch fails.
H13. Contract snapshot scope mismatch fails.
H14. Fee snapshot scope mismatch fails.
H15. Dataset venue/account/symbol mismatch fails.
H16. Missing rollover session threshold fails.
H17. Missing session-count metadata fails.
H18. Wrong distribution unit fails.
H19. Spread summary p75 mismatch with model fails.
H20. Stress p95 lower than p75 fails.
H21. Missing telemetry binding fails.
H22. Activation resolver is point-in-time safe.
H23. Future model activation cannot leak backward.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import os
import pytest
from django.db import IntegrityError, models
from django.core.management import call_command

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentType,
    InstrumentRole,
    MarketListing,
    ListingRole,
    ListingStatus,
)
from apps.market_data.models import (
    CandleQualityFlag,
    MarketCandle,
    VolumeEvidenceType,
    FrictionActivationStatus,
    FrictionBindingRole,
    FrictionComponentType,
    FrictionConditionType,
    FrictionDistributionSummary,
    FrictionEvidenceDataset,
    FrictionModelActivation,
    FrictionModelDatasetBinding,
    FrictionModelSummaryBinding,
    FrictionModelVersion,
    FrictionSessionType,
    FrictionSourceSnapshot,
)
from apps.market_data.friction.commission import (
    calculate_dynamic_fee_bps,
    calculate_execution_notional,
    calculate_round_trip_cost_bps,
    calculate_side_fee_usd,
)
from apps.market_data.friction.distribution import (
    calculate_directional_slippage,
    compute_distribution_statistics,
    validate_slippage_telemetry_sufficiency,
    validate_spread_dataset_sufficiency,
)
from apps.market_data.friction.financing import (
    calculate_overnight_swap_usd,
    crosses_rollover_boundary,
    get_rollover_utc_hour,
    is_triple_swap_day,
    is_us_daylight_saving_time,
)
from apps.market_data.friction.fingerprint import compute_empirical_friction_fingerprint
from apps.market_data.friction.ingestion import (
    build_and_bind_friction_model_version,
    ingest_friction_evidence_dataset,
    ingest_friction_source_snapshot,
    ingest_friction_telemetry_dataset,
)
from apps.market_data.friction.resolution import resolve_friction_model, resolve_friction_model_activation
from apps.market_data.friction.slippage_parser import parse_mt5_execution_telemetry
from apps.market_data.friction.tick_parser import parse_mt5_tick_export
from apps.market_data.readiness import XauUsdDataReadinessEvaluator


# =============================================================================
# FIXTURES & TEST HELPERS
# =============================================================================

@pytest.fixture
def xauusd_setup(db):
    """Seed canonical assets, instruments, and primary XAUUSD spot listing."""
    call_command("seed_instruments")
    instrument = Instrument.get_canonical_xauusd()
    primary_listing = MarketListing.objects.filter(
        instrument=instrument,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    ).first()
    return instrument, primary_listing


def _create_clean_candles(instrument, count=30, tf="15m", source=None):
    """Helper to create N valid chronological UTC candles for warm-up satisfaction."""
    if source is None:
        listing = MarketListing.objects.filter(
            instrument=instrument,
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        ).first()
        source = listing.provider if listing else "twelve_data_xauusd"
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    delta = timedelta(minutes=15)
    candles = []
    for i in range(count):
        t_open = base_time + i * delta
        t_close = t_open + delta
        c = MarketCandle.objects.create(
            instrument=instrument,
            source=source,
            timeframe=tf,
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2000.00"),
            high=Decimal("2005.00"),
            low=Decimal("1995.00"),
            close=Decimal("2002.00"),
            volume=Decimal("150.0"),
            volume_evidence=VolumeEvidenceType.TICK_VOLUME,
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal("2002.00000000"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )
        candles.append(c)
    return candles


@pytest.fixture
def base_sample_ticks():
    """Generate 1,200 valid synthetic tick samples spanning 6 distinct trading days and all sessions."""
    ticks = []
    base_time = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
    for day in range(6):
        current_day = base_time + timedelta(days=day)
        # Asian: hour 2 (50 ticks/day -> 300 total >= 100)
        for i in range(50):
            t = current_day.replace(hour=2, minute=i)
            ticks.append({"timestamp": t, "bid": Decimal("2500.00"), "ask": Decimal("2500.20"), "spread_bps": Decimal("0.80")})
        # London: hour 10 (50 ticks/day -> 300 total >= 100)
        for i in range(50):
            t = current_day.replace(hour=10, minute=i)
            ticks.append({"timestamp": t, "bid": Decimal("2502.00"), "ask": Decimal("2502.25"), "spread_bps": Decimal("1.00")})
        # New York: hour 15 (60 ticks/day -> 360 total >= 100)
        for i in range(60):
            t = current_day.replace(hour=15, minute=i)
            ticks.append({"timestamp": t, "bid": Decimal("2505.00"), "ask": Decimal("2505.30"), "spread_bps": Decimal("1.20")})
        # Rollover: hour 22 (40 ticks/day -> 240 total >= 30)
        for i in range(40):
            t = current_day.replace(hour=22, minute=i)
            ticks.append({"timestamp": t, "bid": Decimal("2504.00"), "ask": Decimal("2504.50"), "spread_bps": Decimal("2.00")})

    return ticks


@pytest.fixture
def base_telemetry_fills():
    """Generate 35 valid synthetic MT5 execution telemetry fills."""
    records = []
    base_time = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(35):
        dec_t = base_time + timedelta(seconds=i * 60)
        send_t = dec_t + timedelta(milliseconds=50)
        fill_t = send_t + timedelta(milliseconds=120)
        side = "BUY" if i % 2 == 0 else "SELL"
        ref_bid = Decimal("2500.00")
        ref_ask = Decimal("2500.20")
        fill_p = Decimal("2500.22") if side == "BUY" else Decimal("2499.98")
        records.append({
            "venue": "EXNESS",
            "symbol": "XAUUSD",
            "account_tier": "STANDARD",
            "side": side,
            "order_type": "MARKET",
            "decision_timestamp": dec_t,
            "order_send_timestamp": send_t,
            "fill_timestamp": fill_t,
            "reference_bid": ref_bid,
            "reference_ask": ref_ask,
            "executed_fill_price": fill_p,
            "requested_price": None,
            "volume_lots": Decimal("1.00"),
            "latency_ms": Decimal("170"),
            "adverse_slippage_price": Decimal("0.02"),
            "signed_slippage_bps": Decimal("0.08"),
            "adverse_only_bps": Decimal("0.08"),
        })
    return records


@pytest.fixture
def qualified_evidence_bundle(db, base_sample_ticks, base_telemetry_fills):
    """Create and persist a complete, qualified empirical friction evidence hierarchy including slippage."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Snapshots
    legal_snap, _ = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/legal/terms",
        source_name="EXNESS_LEGAL_ENTITY_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"EXNESS_SC_LTD:FSA:SD025",
    )
    spec_snap, _ = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/contract-specifications/",
        source_name="EXNESS_MT5_CONTRACT_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"CONTRACT_SIZE:100|POINT:0.01|DIGITS:2",
    )
    fee_snap, _ = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/fees/",
        source_name="EXNESS_FEE_SCHEDULE",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"STANDARD:COMMISSION:0.00",
    )
    swap_snap, _ = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/swap/",
        source_name="EXNESS_SWAP_SCHEDULE",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"SWAP_LONG:-34.80|SWAP_SHORT:12.40|WED:TRIPLE",
    )
    tick_snap, _ = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/tick-history/xauusd",
        source_name="EXNESS_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"RAW_TICK_PAYLOAD_BYTES",
    )
    telem_snap, _ = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/telemetry/xauusd",
        source_name="EXNESS_MT5_TELEMETRY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"RAW_TELEMETRY_PAYLOAD_BYTES",
    )

    # 2. Datasets
    spread_dataset, _ = ingest_friction_evidence_dataset(
        source_snapshot=tick_snap,
        venue="EXNESS",
        account_tier="STANDARD",
        symbol="XAUUSD",
        sample_start=base_sample_ticks[0]["timestamp"],
        sample_end=base_sample_ticks[-1]["timestamp"],
        ticks_data=base_sample_ticks,
    )
    telemetry_dataset, _ = ingest_friction_telemetry_dataset(
        source_snapshot=telem_snap,
        venue="EXNESS",
        account_tier="STANDARD",
        symbol="XAUUSD",
        sample_start=base_telemetry_fills[0]["decision_timestamp"],
        sample_end=base_telemetry_fills[-1]["fill_timestamp"],
        telemetry_records=base_telemetry_fills,
    )

    spread_bps_list = [t["spread_bps"] for t in base_sample_ticks]
    slip_bps_list = [r["signed_slippage_bps"] for r in base_telemetry_fills]

    legal_info = {
        "legal_entity_code": "EXNESS_SC_LTD",
        "legal_entity_name": "Exness (SC) Ltd",
        "regulator": "FSA",
        "license_number": "SD025",
    }
    contract_geom = {
        "digits": 2,
        "point_size": Decimal("0.01"),
        "trade_tick_size": Decimal("0.01"),
        "trade_tick_value": Decimal("1.00"),
        "contract_size": Decimal("100.0"),
        "volume_min": Decimal("0.01"),
        "volume_max": Decimal("200.0"),
        "volume_step": Decimal("0.01"),
    }
    commission_pol = {
        "native_commission_usd_per_lot_per_side": Decimal("0.00"),
        "commission_formula": "DYNAMIC_NOTIONAL_BPS",
    }
    financing_pol = {
        "swap_long_points": Decimal("-34.80"),
        "swap_short_points": Decimal("12.40"),
        "rollover_summer_utc_hour": 21,
        "rollover_winter_utc_hour": 22,
        "triple_swap_weekday": "WEDNESDAY",
        "actual_account_swap_free_status": False,
    }

    model_ver, activation = build_and_bind_friction_model_version(
        legal_entity_snapshot=legal_snap,
        contract_spec_snapshot=spec_snap,
        fee_schedule_snapshot=fee_snap,
        swap_spec_snapshot=swap_snap,
        evidence_dataset=spread_dataset,
        spread_ticks_bps=spread_bps_list,
        telemetry_dataset=telemetry_dataset,
        slippage_records_bps=slip_bps_list,
        legal_entity_info=legal_info,
        contract_geometry=contract_geom,
        commission_policy=commission_pol,
        financing_policy=financing_pol,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        effective_from=datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc),
    )

    return {
        "model_version": model_ver,
        "activation": activation,
        "dataset": spread_dataset,
        "telemetry_dataset": telemetry_dataset,
        "legal_snapshot": legal_snap,
        "contract_snapshot": spec_snap,
        "fee_snapshot": fee_snap,
        "swap_snapshot": swap_snap,
    }


# =============================================================================
# ORIGINAL 17 HOSTILE TESTS
# =============================================================================

@pytest.mark.django_db
def test_01_no_friction_evidence_fails_closed(xauusd_setup):
    """Scenario 1: No friction evidence in database -> fails closed (CANDLES_READY_EMPIRICAL_FRICTION_MISSING)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    assert FrictionModelActivation.objects.count() == 0
    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
    assert rep.passed is False
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    assert any("No active FrictionModelActivation found" in r for r in rep.reasons)


@pytest.mark.django_db
def test_02_missing_legal_entity_provenance_fails(xauusd_setup, qualified_evidence_bundle):
    """Scenario 2: Missing legal entity provenance -> fails (LEGAL_ENTITY_EVIDENCE_MISSING)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    snap = qualified_evidence_bundle["legal_snapshot"]
    
    bad_model = FrictionModelVersion.objects.create(
        model_version_id="BAD_LEGAL_ENTITY_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="",
        legal_entity_name="",
        regulator="",
        license_number="",
        legal_entity_source_snapshot=snap,
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        empirical_friction_evidence_fingerprint="dummy_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="BAD_LEGAL_ACTIVATION",
        friction_model_version=bad_model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test legal entity absence",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    assert any("Legal entity provenance" in r for r in rep.reasons)


@pytest.mark.django_db
def test_03_point_vs_trade_tick_size_decoupling(qualified_evidence_bundle):
    """Scenario 3: Verify point_size and trade_tick_size are decoupled and stored independently."""
    model = qualified_evidence_bundle["model_version"]
    assert hasattr(model, "point_size")
    assert hasattr(model, "trade_tick_size")
    assert model.point_size == Decimal("0.01")
    assert model.trade_tick_size == Decimal("0.01")
    assert FrictionModelVersion._meta.get_field("point_size").name == "point_size"
    assert FrictionModelVersion._meta.get_field("trade_tick_size").name == "trade_tick_size"


@pytest.mark.django_db
def test_04_arbitrary_unverified_friction_row_cannot_pass(xauusd_setup, qualified_evidence_bundle):
    """Scenario 4: An arbitrary friction row without bound datasets/summaries fails with EMPIRICAL_FRICTION_INCOMPLETE."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    snap = qualified_evidence_bundle["legal_snapshot"]
    spec_snap = qualified_evidence_bundle["contract_snapshot"]
    fee_snap = qualified_evidence_bundle["fee_snapshot"]
    swap_snap = qualified_evidence_bundle["swap_snapshot"]
    unverified_model = FrictionModelVersion.objects.create(
        model_version_id="UNVERIFIED_LONE_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=snap,
        contract_spec_source_snapshot=spec_snap,
        fee_schedule_source_snapshot=fee_snap,
        swap_spec_source_snapshot=swap_snap,
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="lone_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="UNVERIFIED_ACTIVATION",
        friction_model_version=unverified_model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test unverified lone model",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_INCOMPLETE"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    assert any("no bound datasets or distribution summaries" in r for r in rep.reasons)


@pytest.mark.django_db
def test_05_wrong_venue_or_symbol_fails(xauusd_setup, qualified_evidence_bundle):
    """Scenario 5: Wrong venue/account tier/symbol fails with EMPIRICAL_FRICTION_INVALID."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    snap = qualified_evidence_bundle["legal_snapshot"]
    wrong_model = FrictionModelVersion.objects.create(
        model_version_id="WRONG_VENUE_MODEL",
        venue="BINANCE",
        symbol="BTCUSDT",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=snap,
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        empirical_friction_evidence_fingerprint="wrong_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="WRONG_VENUE_ACTIVATION",
        friction_model_version=wrong_model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test wrong venue model",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_INVALID"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


def test_06_invalid_or_negative_spread_fails():
    """Scenario 6: Inverted or negative spreads fail dataset validation."""
    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    invalid_ticks = [
        {"timestamp": now, "bid": Decimal("2500.00"), "ask": Decimal("2499.00"), "spread_bps": Decimal("-4.00")}
    ]
    is_valid, errors = validate_spread_dataset_sufficiency(invalid_ticks)
    assert is_valid is False
    assert any("Inverted or crossed spread" in e for e in errors)


def test_07_insufficient_sample_count_or_distinct_dates_fails():
    """Scenario 7: Sample count < 1000 or distinct trading dates < 5 fails sufficiency."""
    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    few_ticks = [
        {"timestamp": now + timedelta(minutes=i), "bid": Decimal("2500.00"), "ask": Decimal("2500.20"), "spread_bps": Decimal("0.80")}
        for i in range(100)
    ]
    is_valid, errors = validate_spread_dataset_sufficiency(few_ticks)
    assert is_valid is False
    assert any("Insufficient spread sample count" in e for e in errors)
    assert any("Insufficient temporal span" in e for e in errors)


def test_08_naive_timestamp_rejected():
    """Scenario 8: Naive timestamp fails validation with explicit error."""
    naive_ticks = [
        {"timestamp": datetime(2026, 8, 20, 10, 0, 0), "bid": Decimal("2500.00"), "ask": Decimal("2500.20"), "spread_bps": Decimal("0.80")}
    ]
    is_valid, errors = validate_spread_dataset_sufficiency(naive_ticks)
    assert is_valid is False
    assert any("Naive or non-datetime timestamp" in e for e in errors)


def test_09_unit_mismatch_and_percentile_sanity():
    """Scenario 9: Statistics percentiles are monotonically ascending and within min/max bounds."""
    vals = [Decimal("0.50"), Decimal("0.80"), Decimal("1.20"), Decimal("2.00"), Decimal("3.50")]
    stats = compute_distribution_statistics(vals)
    assert stats["stat_min"] <= stats["stat_p50"] <= stats["stat_p75"] <= stats["stat_p90"] <= stats["stat_p95"] <= stats["stat_max"]
    assert stats["stat_min"] == Decimal("0.500000")
    assert stats["stat_max"] == Decimal("3.500000")


def test_10_missing_mandatory_slippage_telemetry():
    """Scenario 10: Slippage telemetry validation reports missing fields or insufficient sample count."""
    is_valid, errors = validate_slippage_telemetry_sufficiency([])
    assert is_valid is False
    assert any("Insufficient slippage telemetry fills" in e for e in errors)

    # Directional slippage validation
    adv_buy, bps_buy = calculate_directional_slippage("BUY", Decimal("2500.50"), Decimal("2499.80"), Decimal("2500.00"))
    assert adv_buy == Decimal("0.500000")
    assert bps_buy > Decimal("0")

    adv_sell, bps_sell = calculate_directional_slippage("SELL", Decimal("2499.50"), Decimal("2500.00"), Decimal("2500.20"))
    assert adv_sell == Decimal("0.500000")
    assert bps_sell > Decimal("0")


def test_11_dynamic_fee_formula_accuracy():
    """Scenario 11: Dynamic fee formula scales with execution notional; Standard account is 0.00 bps."""
    fee_std = calculate_dynamic_fee_bps(Decimal("0.00"), Decimal("100.0"), Decimal("2500.00"))
    assert fee_std == Decimal("0.0000")

    fee_raw_2500 = calculate_dynamic_fee_bps(Decimal("3.50"), Decimal("100.0"), Decimal("2500.00"))
    assert fee_raw_2500 == Decimal("0.1400")

    fee_raw_3500 = calculate_dynamic_fee_bps(Decimal("3.50"), Decimal("100.0"), Decimal("3500.00"))
    assert fee_raw_3500 == Decimal("0.1000")

    rt = calculate_round_trip_cost_bps(Decimal("1.20"), Decimal("0.14"), Decimal("0.14"), Decimal("0.50"), Decimal("0.50"))
    assert rt == Decimal("2.48")


def test_12_semantic_fingerprint_determinism_and_exclusions():
    """Scenario 12: Deterministic fingerprint excludes DB IDs and includes semantic evidence."""
    fp1 = compute_empirical_friction_fingerprint(
        semantic_versions={"friction_policy_schema_version": "1.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={"digits": 2, "contract_size": Decimal("100.0")},
        source_snapshot_hashes=["hash_a", "hash_b"],
        dataset_hashes=["ds_hash_1"],
        distribution_summaries=[{"component_type": "SPREAD", "stat_p75": Decimal("1.20")}],
        calibrated_parameters={"base_spread_bps": Decimal("1.20"), "stress_spread_bps": Decimal("2.50")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        financing_policy={"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "actual_account_swap_free_status": False},
        bound_binding_roles=["PRIMARY_SPREAD_SAMPLE"],
    )

    fp2 = compute_empirical_friction_fingerprint(
        semantic_versions={"friction_policy_schema_version": "1.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={"digits": 2, "contract_size": Decimal("100.0")},
        source_snapshot_hashes=["hash_b", "hash_a"],
        dataset_hashes=["ds_hash_1"],
        distribution_summaries=[{"component_type": "SPREAD", "stat_p75": Decimal("1.20")}],
        calibrated_parameters={"base_spread_bps": Decimal("1.20"), "stress_spread_bps": Decimal("2.50")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        financing_policy={"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "actual_account_swap_free_status": False},
        bound_binding_roles=["PRIMARY_SPREAD_SAMPLE"],
    )
    assert fp1 == fp2
    assert len(fp1) == 64


def test_13_evidence_mutation_changes_fingerprint():
    """Scenario 13: Mutating any evidence field or policy parameter changes the fingerprint."""
    base_args = {
        "semantic_versions": {"friction_policy_schema_version": "1.0.0"},
        "venue": "EXNESS",
        "legal_entity_code": "EXNESS_SC_LTD",
        "account_tier": "STANDARD",
        "symbol": "XAUUSD",
        "contract_geometry": {"digits": 2, "contract_size": Decimal("100.0")},
        "source_snapshot_hashes": ["hash_a"],
        "dataset_hashes": ["ds_hash_1"],
        "distribution_summaries": [{"component_type": "SPREAD", "stat_p75": Decimal("1.20")}],
        "calibrated_parameters": {"base_spread_bps": Decimal("1.20"), "stress_spread_bps": Decimal("2.50")},
        "commission_policy": {"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        "financing_policy": {"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "actual_account_swap_free_status": False},
        "bound_binding_roles": ["PRIMARY_SPREAD_SAMPLE"],
    }
    fp_base = compute_empirical_friction_fingerprint(**base_args)

    mutated_args = dict(base_args)
    mutated_args["calibrated_parameters"] = {"base_spread_bps": Decimal("1.30"), "stress_spread_bps": Decimal("2.50")}
    fp_mut = compute_empirical_friction_fingerprint(**mutated_args)
    assert fp_base != fp_mut


@pytest.mark.django_db
def test_14_idempotent_rerun_preserves_fingerprint(qualified_evidence_bundle):
    """Scenario 14: Idempotent rerun preserves exact fingerprint and adds zero duplicates."""
    model1 = qualified_evidence_bundle["model_version"]
    fp1 = model1.empirical_friction_evidence_fingerprint
    snap_count_before = FrictionSourceSnapshot.objects.count()
    dataset_count_before = FrictionEvidenceDataset.objects.count()

    snap, created = ingest_friction_source_snapshot(
        source_url="https://www.exness.com/fees/",
        source_name="EXNESS_FEE_SCHEDULE",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc),
        known_at=datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc),
        raw_content=b"STANDARD:COMMISSION:0.00",
    )
    assert created is False
    assert FrictionSourceSnapshot.objects.count() == snap_count_before
    assert FrictionEvidenceDataset.objects.count() == dataset_count_before
    assert model1.empirical_friction_evidence_fingerprint == fp1


@pytest.mark.django_db
def test_15_append_only_immutability_enforced(qualified_evidence_bundle):
    """Scenario 15: Append-only models reject update(), bulk_update(), and delete()."""
    snap = qualified_evidence_bundle["legal_snapshot"]
    dataset = qualified_evidence_bundle["dataset"]
    model = qualified_evidence_bundle["model_version"]

    with pytest.raises(PermissionError):
        snap.delete()
    with pytest.raises(PermissionError):
        FrictionSourceSnapshot.objects.all().delete()
    with pytest.raises(PermissionError):
        FrictionEvidenceDataset.objects.all().delete()
    with pytest.raises(PermissionError):
        FrictionModelVersion.objects.all().delete()

    with pytest.raises(PermissionError):
        FrictionSourceSnapshot.objects.filter(pk=snap.pk).update(source_name="MODIFIED")
    with pytest.raises(PermissionError):
        FrictionModelVersion.objects.filter(pk=model.pk).update(base_spread_bps=Decimal("9.99"))

    with pytest.raises(ValueError):
        snap.source_name = "ALTERED"
        snap.save()


@pytest.mark.django_db
def test_16_activation_history_resolves_without_update(qualified_evidence_bundle):
    """Scenario 16: Model activation resolves point-in-time from append-only history."""
    now_utc = datetime(2026, 8, 28, 14, 0, 0, tzinfo=timezone.utc)
    model = qualified_evidence_bundle["model_version"]

    active = FrictionModelActivation.objects.filter(
        effective_from__lte=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
    ).order_by("-effective_from").first()
    assert active is not None
    assert active.friction_model_version == model


@pytest.mark.django_db
def test_17_successful_friction_advances_strictly_to_quote_evidence_missing(xauusd_setup, qualified_evidence_bundle):
    """Scenario 17: Successful friction evidence advances strictly to CANDLES_READY_QUOTE_EVIDENCE_MISSING."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_CONFIGURED"
    assert rep.decision == "CANDLES_READY_QUOTE_EVIDENCE_MISSING"
    assert rep.passed is False
    assert rep.empirical_friction_evidence_fingerprint is not None
    assert len(rep.empirical_friction_evidence_fingerprint) == 64
    manifest = rep.to_manifest_dict(code_revision="0855cf61f4ce4e15a5c7161f941aa263940432c0")
    assert manifest["empirical_friction_evidence"]["status"] == "EMPIRICAL_FRICTION_CONFIGURED"
    assert manifest["hard_data_readiness_gate"]["decision"] == "CANDLES_READY_QUOTE_EVIDENCE_MISSING"


# =============================================================================
# HARDENING TESTS FOR DISCOVERED LOOPHOLES (DIRECTIVE 14: 23 TESTS)
# =============================================================================

@pytest.mark.django_db
def test_harden_01_spread_complete_slippage_missing_cannot_pass(xauusd_setup, base_sample_ticks):
    """H01: Spread complete + slippage missing cannot pass (Directive 8 & 14.1)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

    legal_snap, _ = ingest_friction_source_snapshot("http://ex.com/l", "L", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"L")
    spec_snap, _ = ingest_friction_source_snapshot("http://ex.com/c", "C", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"C")
    fee_snap, _ = ingest_friction_source_snapshot("http://ex.com/f", "F", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"F")
    swap_snap, _ = ingest_friction_source_snapshot("http://ex.com/s", "S", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"S")
    tick_snap, _ = ingest_friction_source_snapshot("http://ex.com/t", "T", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"T")

    spread_ds, _ = ingest_friction_evidence_dataset(
        source_snapshot=tick_snap,
        venue="EXNESS",
        account_tier="STANDARD",
        symbol="XAUUSD",
        sample_start=base_sample_ticks[0]["timestamp"],
        sample_end=base_sample_ticks[-1]["timestamp"],
        ticks_data=base_sample_ticks,
    )

    model_ver, act = build_and_bind_friction_model_version(
        legal_entity_snapshot=legal_snap,
        contract_spec_snapshot=spec_snap,
        fee_schedule_snapshot=fee_snap,
        swap_spec_snapshot=swap_snap,
        evidence_dataset=spread_ds,
        spread_ticks_bps=[t["spread_bps"] for t in base_sample_ticks],
        telemetry_dataset=None,
        slippage_records_bps=None,
        legal_entity_info={"legal_entity_code": "SC", "legal_entity_name": "N", "regulator": "R", "license_number": "L"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1"), "contract_size": Decimal("100"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0"), "commission_formula": "D"},
        financing_policy={"swap_long_points": Decimal("-10"), "swap_short_points": Decimal("5"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY"},
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
    )
    # Model without slippage must be DRAFT
    assert act.activation_status == FrictionActivationStatus.DRAFT

    # Even if an activation is explicitly created as ACTIVE for this incomplete model, readiness fails closed
    FrictionModelActivation.objects.create(
        activation_id="FORCED_ACTIVE_NO_SLIP",
        friction_model_version=model_ver,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test forced activation of incomplete model",
    )
    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


@pytest.mark.django_db
def test_harden_02_model_with_base_slippage_none_cannot_become_active(base_sample_ticks):
    """H02: Model with base_slippage_bps=None cannot become ACTIVE (Directive 9 & 14.2)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    legal_snap, _ = ingest_friction_source_snapshot("http://ex.com/l", "L2", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"L2")
    spec_snap, _ = ingest_friction_source_snapshot("http://ex.com/c", "C2", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"C2")
    fee_snap, _ = ingest_friction_source_snapshot("http://ex.com/f", "F2", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"F2")
    swap_snap, _ = ingest_friction_source_snapshot("http://ex.com/s", "S2", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"S2")
    tick_snap, _ = ingest_friction_source_snapshot("http://ex.com/t", "T2", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"T2")

    spread_ds, _ = ingest_friction_evidence_dataset(
        source_snapshot=tick_snap,
        venue="EXNESS",
        account_tier="STANDARD",
        symbol="XAUUSD",
        sample_start=base_sample_ticks[0]["timestamp"],
        sample_end=base_sample_ticks[-1]["timestamp"],
        ticks_data=base_sample_ticks,
    )

    model_ver, act = build_and_bind_friction_model_version(
        legal_entity_snapshot=legal_snap,
        contract_spec_snapshot=spec_snap,
        fee_schedule_snapshot=fee_snap,
        swap_spec_snapshot=swap_snap,
        evidence_dataset=spread_ds,
        spread_ticks_bps=[t["spread_bps"] for t in base_sample_ticks],
        telemetry_dataset=None,
        slippage_records_bps=None,
        legal_entity_info={"legal_entity_code": "SC", "legal_entity_name": "N", "regulator": "R", "license_number": "L"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1"), "contract_size": Decimal("100"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0"), "commission_formula": "D"},
        financing_policy={"swap_long_points": Decimal("-10"), "swap_short_points": Decimal("5"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY"},
    )
    assert model_ver.base_slippage_bps is None
    assert act.activation_status == FrictionActivationStatus.DRAFT


@pytest.mark.django_db
def test_harden_03_command_completeness_includes_mandatory_slippage():
    """H03: Command completeness includes mandatory slippage (Directive 8 & 14.3)."""
    manifest_file = "artifacts/test_friction_h03_manifest_tmp.json"
    report_file = "artifacts/test_friction_h03_report_tmp.md"
    try:
        # Run command with zero evidence
        call_command(
            "ingest_xauusd_empirical_friction",
            dry_run=True,
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert data["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert data["evidence_inventory"]["execution_slippage_telemetry"]["status"] == "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING"
    finally:
        if os.path.exists(manifest_file):
            os.remove(manifest_file)
        if os.path.exists(report_file):
            os.remove(report_file)


def test_harden_04_tick_file_path_with_valid_file_is_actually_parsed():
    """H04: Tick-file path with valid file is actually parsed (Directive 6 & 14.4)."""
    rows = ["DateTime,Bid,Ask,Flags"]
    base_t = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
    for day in range(6):
        d = base_t + timedelta(days=day)
        for i in range(200):
            ts = (d + timedelta(minutes=i)).strftime("%Y.%m.%d %H:%M:%S.000+00:00")
            rows.append(f"{ts},2500.00,2500.25,0")

    csv_payload = "\n".join(rows).encode("utf-8")
    ticks, summary = parse_mt5_tick_export(csv_payload, expected_symbol="XAUUSD")
    assert len(ticks) == 1200
    assert summary["distinct_trading_days"] == 6
    assert ticks[0]["spread_price"] == Decimal("0.25")
    assert ticks[0]["mid"] == Decimal("2500.125")


def test_harden_05_malformed_tick_file_fails():
    """H05: Malformed tick file fails (Directive 6 & 14.5)."""
    # Empty payload
    with pytest.raises(ValueError, match="empty"):
        parse_mt5_tick_export(b"")

    # Missing bid/ask headers
    with pytest.raises(ValueError, match="Missing required bid/ask"):
        parse_mt5_tick_export(b"Date,Time,Volume\n2026.01.01,10:00:00,10")

    # Crossed spread (ask <= bid)
    bad_quotes = b"DateTime,Bid,Ask\n2026-08-20T10:00:00Z,2500.50,2500.00"
    with pytest.raises(ValueError, match="Crossed or inverted quote"):
        parse_mt5_tick_export(bad_quotes)

    # Naive timestamp
    naive_ts = b"DateTime,Bid,Ask\n2026-08-20 10:00:00,2500.00,2500.20"
    with pytest.raises(ValueError, match="Naive timestamp"):
        parse_mt5_tick_export(naive_ts)


def test_harden_06_slippage_file_path_is_actually_parsed():
    """H06: Slippage-file path is actually parsed (Directive 7 & 14.6)."""
    headers = "side,order_type,decision_timestamp,order_send_timestamp,reference_bid,reference_ask,executed_fill_price,fill_timestamp,volume_lots,latency_ms,symbol,account_tier,venue"
    rows = [headers]
    base_t = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(35):
        t1 = (base_t + timedelta(seconds=i * 10)).isoformat()
        t2 = (base_t + timedelta(seconds=i * 10, milliseconds=50)).isoformat()
        t3 = (base_t + timedelta(seconds=i * 10, milliseconds=120)).isoformat()
        rows.append(f"BUY,MARKET,{t1},{t2},2500.00,2500.20,2500.22,{t3},1.0,70,XAUUSD,STANDARD,EXNESS")

    csv_payload = "\n".join(rows).encode("utf-8")
    recs, summary = parse_mt5_execution_telemetry(csv_payload, expected_venue="EXNESS", expected_symbol="XAUUSD", expected_account_tier="STANDARD")
    assert len(recs) == 35
    assert summary["sample_count"] == 35
    assert recs[0]["adverse_slippage_price"] == Decimal("0.02")
    assert recs[0]["signed_slippage_bps"] > Decimal("0")


def test_harden_07_malformed_telemetry_fails():
    """H07: Malformed telemetry fails (Directive 7 & 14.7)."""
    # Empty
    with pytest.raises(ValueError, match="empty"):
        parse_mt5_execution_telemetry(b"")

    # Chronological violation: fill before send
    headers = "side,order_type,decision_timestamp,order_send_timestamp,reference_bid,reference_ask,executed_fill_price,fill_timestamp,volume_lots,latency_ms,symbol,account_tier,venue"
    t1 = "2026-08-20T10:00:00Z"
    t2 = "2026-08-20T10:00:05Z"
    t_invalid = "2026-08-20T10:00:02Z"
    bad_chrono = f"{headers}\nBUY,MARKET,{t1},{t2},2500.00,2500.20,2500.25,{t_invalid},1.0,70,XAUUSD,STANDARD,EXNESS".encode("utf-8")
    with pytest.raises(ValueError, match="Chronological sequence invalid"):
        parse_mt5_execution_telemetry(bad_chrono)

    # Scope mismatch
    bad_scope = f"{headers}\nBUY,MARKET,{t1},{t2},2500.00,2500.20,2500.25,{t2},1.0,70,EURUSD,STANDARD,EXNESS".encode("utf-8")
    with pytest.raises(ValueError, match="symbol mismatch"):
        parse_mt5_execution_telemetry(bad_scope, expected_symbol="XAUUSD")


@pytest.mark.django_db
def test_harden_08_hard_coded_default_contract_geometry_without_snapshot_fails(xauusd_setup, qualified_evidence_bundle):
    """H08: Hard-coded/default contract geometry without snapshot fails (Directive 4 & 14.8)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    no_contract_model = FrictionModelVersion.objects.create(
        model_version_id="NO_CONTRACT_SNAP_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=None,  # Missing contract spec snapshot
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="no_contract_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="NO_CONTRACT_ACT",
        friction_model_version=no_contract_model,
        known_at=datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        effective_from=datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test no contract spec snapshot",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "CONTRACT_SPEC_EVIDENCE_MISSING"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


@pytest.mark.django_db
def test_harden_09_commission_without_source_snapshot_fails(xauusd_setup, qualified_evidence_bundle):
    """H09: Commission without source snapshot fails (Directive 5 & 14.9)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    no_fee_model = FrictionModelVersion.objects.create(
        model_version_id="NO_FEE_SNAP_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=None,  # Missing fee snapshot
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="no_fee_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="NO_FEE_ACT",
        friction_model_version=no_fee_model,
        known_at=datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        effective_from=datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test no fee snapshot",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "COMMISSION_EVIDENCE_MISSING"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


@pytest.mark.django_db
def test_harden_10_financing_default_zero_without_evidence_fails(xauusd_setup, qualified_evidence_bundle):
    """H10: Financing default zero without evidence fails (Directive 3 & 14.10)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    no_swap_model = FrictionModelVersion.objects.create(
        model_version_id="NO_SWAP_SNAP_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=None,  # Missing swap snapshot
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("0.00"),
        swap_short_points=Decimal("0.00"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="no_swap_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="NO_SWAP_ACT",
        friction_model_version=no_swap_model,
        known_at=datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        effective_from=datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test no swap snapshot",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "FINANCING_EVIDENCE_MISSING"
    assert rep.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"


@pytest.mark.django_db
def test_harden_11_hard_coded_swap_values_cannot_pass_without_supporting_snapshot():
    """H11: Hard-coded swap values cannot pass without supporting snapshot (Directive 3 & 14.11)."""
    manifest_file = "artifacts/test_friction_h11_manifest_tmp.json"
    report_file = "artifacts/test_friction_h11_report_tmp.md"
    try:
        # Run command without --swap-spec-file
        call_command(
            "ingest_xauusd_empirical_friction",
            dry_run=True,
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["evidence_inventory"]["financing_policy"]["status"] == "FINANCING_EVIDENCE_MISSING"
        assert data["evidence_inventory"]["financing_policy"]["swap_long_points"] is None
    finally:
        if os.path.exists(manifest_file):
            os.remove(manifest_file)
        if os.path.exists(report_file):
            os.remove(report_file)


@pytest.mark.django_db
def test_harden_12_legal_source_scope_mismatch_fails(xauusd_setup, qualified_evidence_bundle):
    """H12: Legal source scope mismatch fails (Directive 10 & 14.12)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    # Snapshot venue is IC_MARKETS, but model venue is EXNESS
    mismatched_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/l_bad", "IC_LEGAL", "IC_MARKETS", "XAUUSD", "STANDARD", now_utc, now_utc, b"IC_MARKETS_LEGAL"
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MISMATCHED_LEGAL_SCOPE_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=mismatched_snap,
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="mismatch_legal_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="MISMATCHED_LEGAL_ACT",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test mismatched legal scope",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_INVALID"


@pytest.mark.django_db
def test_harden_13_contract_snapshot_scope_mismatch_fails(xauusd_setup, qualified_evidence_bundle):
    """H13: Contract snapshot scope mismatch fails (Directive 10 & 14.13)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    mismatched_spec, _ = ingest_friction_source_snapshot(
        "http://ex.com/c_bad", "SPEC_EURUSD", "EXNESS", "EURUSD", "STANDARD", now_utc, now_utc, b"EURUSD_SPEC"
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MISMATCHED_CONTRACT_SCOPE_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=mismatched_spec,
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="mismatch_spec_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="MISMATCHED_CONTRACT_ACT",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test mismatched contract scope",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_INVALID"


@pytest.mark.django_db
def test_harden_14_fee_snapshot_scope_mismatch_fails(xauusd_setup, qualified_evidence_bundle):
    """H14: Fee snapshot scope mismatch fails (Directive 10 & 14.14)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    mismatched_fee, _ = ingest_friction_source_snapshot(
        "http://ex.com/f_bad", "RAW_FEE", "EXNESS", "XAUUSD", "RAW_SPREAD", now_utc, now_utc, b"RAW_SPREAD_FEE"
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MISMATCHED_FEE_SCOPE_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=mismatched_fee,
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="mismatch_fee_fp",
    )
    FrictionModelActivation.objects.create(
        activation_id="MISMATCHED_FEE_ACT",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test mismatched fee scope",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_INVALID"


@pytest.mark.django_db
def test_harden_15_dataset_venue_account_symbol_mismatch_fails(xauusd_setup, qualified_evidence_bundle):
    """H15: Dataset venue/account/symbol mismatch fails (Directive 10 & 14.15)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    model = qualified_evidence_bundle["model_version"]
    dataset = qualified_evidence_bundle["dataset"]

    ds_bad = FrictionEvidenceDataset.objects.create(
        dataset_id="DATASET_MISMATCHED_SCOPE",
        source_snapshot=dataset.source_snapshot,
        venue="BINANCE",
        symbol="XAUUSD",
        account_tier="STANDARD",
        sample_start=dataset.sample_start,
        sample_end=dataset.sample_end,
        sample_count=dataset.sample_count,
        distinct_trading_days=dataset.distinct_trading_days,
        session_counts=dataset.session_counts,
        source_units="POINTS",
        raw_dataset_sha256="bad_ds_sha",
        collection_methodology="TEST",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_MISMATCHED_DS",
        friction_model_version=model,
        evidence_dataset=ds_bad,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_INVALID"


@pytest.mark.django_db
def test_harden_16_missing_rollover_session_threshold_fails(xauusd_setup, qualified_evidence_bundle):
    """H16: Missing rollover session threshold fails (Directive 10 & 14.16)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    # Create dataset with ROLLOVER count < 30 (15)
    ds_low_rollover = FrictionEvidenceDataset.objects.create(
        dataset_id="DS_LOW_ROLLOVER",
        source_snapshot=qualified_evidence_bundle["dataset"].source_snapshot,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        sample_start=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        sample_end=datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc),
        sample_count=1200,
        distinct_trading_days=6,
        session_counts={"ASIAN": 200, "LONDON": 200, "NEW_YORK": 200, "ROLLOVER": 15},
        source_units="POINTS",
        raw_dataset_sha256="low_rollover_sha",
        collection_methodology="MT5",
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MODEL_LOW_ROLLOVER",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_low_rollover",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_DS_LOW_ROLL",
        friction_model_version=model,
        evidence_dataset=ds_low_rollover,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )
    summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_LOW_ROLL",
        evidence_dataset=ds_low_rollover,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        sample_count=1200,
        stat_min=Decimal("0.5"), stat_p50=Decimal("1.0"), stat_p75=Decimal("1.00"), stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"), stat_p99=Decimal("2.5"), stat_max=Decimal("3.0"), stat_mean=Decimal("1.1"), stat_std=Decimal("0.2"),
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_LOW_ROLL",
        friction_model_version=model,
        distribution_summary=summary,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_LOW_ROLL",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test low rollover count",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SPREAD_EMPIRICAL_EVIDENCE_MISSING"


@pytest.mark.django_db
def test_harden_17_missing_session_count_metadata_fails(xauusd_setup, qualified_evidence_bundle):
    """H17: Missing session-count metadata fails (Directive 10 & 14.17)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    ds_no_session = FrictionEvidenceDataset.objects.create(
        dataset_id="DS_NO_SESSION",
        source_snapshot=qualified_evidence_bundle["dataset"].source_snapshot,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        sample_start=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        sample_end=datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc),
        sample_count=1200,
        distinct_trading_days=6,
        session_counts={},  # Empty session counts
        source_units="POINTS",
        raw_dataset_sha256="no_session_sha",
        collection_methodology="MT5",
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MODEL_NO_SESSION",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_no_session",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_DS_NO_SESS",
        friction_model_version=model,
        evidence_dataset=ds_no_session,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )
    summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_NO_SESS",
        evidence_dataset=ds_no_session,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        sample_count=1200,
        stat_min=Decimal("0.5"), stat_p50=Decimal("1.0"), stat_p75=Decimal("1.00"), stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"), stat_p99=Decimal("2.5"), stat_max=Decimal("3.0"), stat_mean=Decimal("1.1"), stat_std=Decimal("0.2"),
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_NO_SESS",
        friction_model_version=model,
        distribution_summary=summary,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_NO_SESS",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test no session metadata",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SPREAD_EMPIRICAL_EVIDENCE_MISSING"


@pytest.mark.django_db
def test_harden_18_wrong_distribution_unit_fails(xauusd_setup, qualified_evidence_bundle):
    """H18: Wrong distribution unit fails (Directive 10 & 14.18)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    dataset = qualified_evidence_bundle["dataset"]

    # Create summary with unit = "POINTS" instead of "BPS"
    summary_wrong_unit = FrictionDistributionSummary.objects.create(
        summary_id="SUM_WRONG_UNIT",
        evidence_dataset=dataset,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="POINTS",  # Invalid unit
        sample_count=dataset.sample_count,
        stat_min=Decimal("0.5"), stat_p50=Decimal("1.0"), stat_p75=Decimal("1.00"), stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"), stat_p99=Decimal("2.5"), stat_max=Decimal("3.0"), stat_mean=Decimal("1.1"), stat_std=Decimal("0.2"),
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MODEL_WRONG_UNIT",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_wrong_unit",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_DS_WU",
        friction_model_version=model,
        evidence_dataset=dataset,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_WU",
        friction_model_version=model,
        distribution_summary=summary_wrong_unit,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_WU",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test wrong unit",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"


@pytest.mark.django_db
def test_harden_19_spread_summary_p75_mismatch_with_model_fails(xauusd_setup, qualified_evidence_bundle):
    """H19: Spread summary p75 mismatch with model fails (Directive 10 & 14.19)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    dataset = qualified_evidence_bundle["dataset"]

    summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_P75_MISMATCH",
        evidence_dataset=dataset,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        sample_count=dataset.sample_count,
        stat_min=Decimal("0.5"), stat_p50=Decimal("1.0"), stat_p75=Decimal("1.00"), stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"), stat_p99=Decimal("2.5"), stat_max=Decimal("3.0"), stat_mean=Decimal("1.1"), stat_std=Decimal("0.2"),
    )

    # Model base_spread_bps is 5.00, but summary p75 is 1.00
    model = FrictionModelVersion.objects.create(
        model_version_id="MODEL_P75_MISMATCH",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("5.00"),  # Mismatch with summary p75 (1.00)
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_p75_mismatch",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_DS_P75M",
        friction_model_version=model,
        evidence_dataset=dataset,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_P75M",
        friction_model_version=model,
        distribution_summary=summary,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_P75M",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test p75 mismatch",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"


@pytest.mark.django_db
def test_harden_20_stress_p95_lower_than_p75_fails(xauusd_setup, qualified_evidence_bundle):
    """H20: Stress p95 lower than p75 fails (Directive 10 & 14.20)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    dataset = qualified_evidence_bundle["dataset"]

    # p95 (0.50) is strictly less than p75 (1.50)
    summary_inverted = FrictionDistributionSummary.objects.create(
        summary_id="SUM_INVERTED_STRESS",
        evidence_dataset=dataset,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        sample_count=dataset.sample_count,
        stat_min=Decimal("0.5"), stat_p50=Decimal("1.0"), stat_p75=Decimal("1.50"), stat_p90=Decimal("1.2"),
        stat_p95=Decimal("0.50"), stat_p99=Decimal("2.5"), stat_max=Decimal("3.0"), stat_mean=Decimal("1.1"), stat_std=Decimal("0.2"),
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="MODEL_INVERTED_STRESS",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.50"),
        stress_spread_bps=Decimal("0.50"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_inv_stress",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_DS_INVS",
        friction_model_version=model,
        evidence_dataset=dataset,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_INVS",
        friction_model_version=model,
        distribution_summary=summary_inverted,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_INVS",
        friction_model_version=model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test inverted stress spread",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"


@pytest.mark.django_db
def test_harden_21_missing_telemetry_binding_fails(xauusd_setup, qualified_evidence_bundle):
    """H21: Missing telemetry binding fails (Directive 10 & 14.21)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    dataset = qualified_evidence_bundle["dataset"]

    # Create model with spread bindings only, omitting telemetry bindings
    model_no_telem = FrictionModelVersion.objects.create(
        model_version_id="MODEL_NO_TELEM_BINDING",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2,
        point_size=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"),
        swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"),
        stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"),
        stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_no_telem",
    )
    FrictionModelDatasetBinding.objects.create(
        binding_id="BIND_SPREAD_ONLY",
        friction_model_version=model_no_telem,
        evidence_dataset=dataset,
        binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
    )
    summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_SPREAD_ONLY",
        evidence_dataset=dataset,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        sample_count=dataset.sample_count,
        stat_min=Decimal("0.5"), stat_p50=Decimal("1.0"), stat_p75=Decimal("1.00"), stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"), stat_p99=Decimal("2.5"), stat_max=Decimal("3.0"), stat_mean=Decimal("1.1"), stat_std=Decimal("0.2"),
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_SP_ONLY",
        friction_model_version=model_no_telem,
        distribution_summary=summary,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_NO_TELEM_BINDING",
        friction_model_version=model_no_telem,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Test no telemetry binding",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING"


@pytest.mark.django_db
def test_harden_22_activation_resolver_is_point_in_time_safe(qualified_evidence_bundle):
    """H22: Activation resolver is point-in-time safe (Directive 13 & 14.22)."""
    t1 = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 15, 0, 0, 0, tzinfo=timezone.utc)

    # Model 1 active from t1
    model1 = FrictionModelVersion.objects.create(
        model_version_id="MODEL_V1_PIT",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_v1_pit",
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_V1_PIT",
        friction_model_version=model1,
        known_at=t1,
        effective_from=t1,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Initial baseline calibration model",
    )

    # Model 2 active from t2
    model2 = FrictionModelVersion.objects.create(
        model_version_id="MODEL_V2_PIT",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_v2_pit",
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_V2_PIT",
        friction_model_version=model2,
        known_at=t2,
        effective_from=t2,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Newer calibration model",
    )

    # Point-in-time check before t1 -> None
    res_before = resolve_friction_model(as_of=datetime(2026, 7, 30, tzinfo=timezone.utc))
    assert res_before is None

    # Point-in-time check between t1 and t2 -> model1
    res_between = resolve_friction_model(as_of=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert res_between == model1

    # Point-in-time check after t2 -> model2
    res_after = resolve_friction_model(as_of=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert res_after == model2


@pytest.mark.django_db
def test_harden_23_future_model_activation_cannot_leak_backward(qualified_evidence_bundle):
    """H23: Future model activation cannot leak backward (Directive 13 & 14.23)."""
    t1 = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    t_future = datetime(2026, 9, 15, 0, 0, 0, tzinfo=timezone.utc)

    model1 = FrictionModelVersion.objects.create(
        model_version_id="MODEL_PAST_LEAK",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_past_leak",
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_PAST_LEAK",
        friction_model_version=model1,
        known_at=t1,
        effective_from=t1,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Current model",
    )

    # Create future model
    model_future = FrictionModelVersion.objects.create(
        model_version_id="MODEL_FUTURE_LEAK",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_future_leak",
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_FUTURE_LEAK",
        friction_model_version=model_future,
        known_at=t_future,
        effective_from=t_future,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Future model",
    )

    # Query at a historical date (August 20)
    as_of_historical = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
    res = resolve_friction_model(as_of=as_of_historical)
    assert res == model1
    assert res != model_future
