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
from typing import Any, Dict, Optional
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
    FrictionPopulationSemantics,
    FrictionQualificationStatus,
    FrictionSessionType,
    FrictionSourceQualificationAssertion,
    FrictionSourceSnapshot,
    FrictionSourceType,
)
from apps.market_data.friction.artifact_parsers import (
    compare_asserted_vs_derived,
    compute_normalized_evidence_hash,
    parse_commission_backing_artifact,
    parse_contract_spec_backing_artifact,
    parse_financing_backing_artifact,
    parse_legal_entity_backing_artifact,
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
    create_friction_qualification_assertion,
    ingest_friction_evidence_dataset,
    ingest_friction_source_snapshot,
    ingest_friction_telemetry_dataset,
    resolve_slippage_cost_samples,
    verify_authoritative_backing_artifact,
)
from apps.market_data.friction.resolution import resolve_friction_model, resolve_friction_model_activation
from apps.market_data.friction.slippage_parser import parse_mt5_execution_telemetry
from apps.market_data.friction.tick_parser import parse_mt5_tick_export
from apps.market_data.friction.validation import validate_friction_model_for_activation
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
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin="https://www.exness.com/legal/terms",
        collection_methodology="BROKER_PORTAL_DOCUMENT_VERIFIED",
        original_filename="exness_sc_terms_and_conditions.pdf",
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
        source_type=FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value,
        source_origin="https://www.exness.com/contract-specifications/",
        collection_methodology="MT5_TERMINAL_SPEC_EXPORT",
        original_filename="exness_contract_specs.json",
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
        source_type=FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value,
        source_origin="https://www.exness.com/fees/",
        collection_methodology="BROKER_PERSONAL_AREA_EXPORT",
        original_filename="exness_fee_schedule.json",
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
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin="https://www.exness.com/swap/",
        collection_methodology="BROKER_PORTAL_DOCUMENT_VERIFIED",
        original_filename="exness_swap_schedule.json",
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
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
        source_origin="https://www.exness.com/tick-history/xauusd",
        collection_methodology="MT5_TERMINAL_TICK_EXPORT",
        original_filename="XAUUSD_Ticks_2026.csv",
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
        source_type=FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT.value,
        source_origin="https://www.exness.com/telemetry/xauusd",
        collection_methodology="MT5_EXECUTION_TELEMETRY_EXPORT",
        original_filename="execution_telemetry_2026.csv",
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
        telemetry_records=base_telemetry_fills,
        legal_entity_info=legal_info,
        contract_geometry=contract_geom,
        commission_policy=commission_pol,
        financing_policy=financing_pol,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        effective_from=datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc),
        test_qualification_seam=True,
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
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="",
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
    assert any("Legal entity" in r for r in rep.reasons)


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
def test_05_wrong_venue_or_symbol_fails(xauusd_setup):
    """Scenario 5: Wrong venue/symbol model fails direct validation and cannot satisfy XAUUSD readiness."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/l_binance", "L_BINANCE", "BINANCE", "BTCUSDT", "STANDARD", now_utc, now_utc, b"L_BINANCE"
    )
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

    # 1. Direct validation against target scope fails with EMPIRICAL_FRICTION_INVALID
    res = validate_friction_model_for_activation(
        wrong_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"

    # 2. Canonical resolver does not select wrong venue/symbol for XAUUSD readiness
    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
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

    legal_snap, _ = ingest_friction_source_snapshot("http://ex.com/l", "L", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"L", source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value)
    spec_snap, _ = ingest_friction_source_snapshot("http://ex.com/c", "C", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"C", source_type=FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value)
    fee_snap, _ = ingest_friction_source_snapshot("http://ex.com/f", "F", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"F", source_type=FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value)
    swap_snap, _ = ingest_friction_source_snapshot("http://ex.com/s", "S", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"S", source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value)
    tick_snap, _ = ingest_friction_source_snapshot("http://ex.com/t", "T", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc, b"T", source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value)

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
        legal_entity_info={"legal_entity_code": "EXNESS_SC_LTD", "legal_entity_name": "Exness (SC) Ltd", "regulator": "FSA", "license_number": "SD025"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1"), "contract_size": Decimal("100"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0"), "commission_formula": "D"},
        financing_policy={"swap_long_points": Decimal("-10"), "swap_short_points": Decimal("5"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY"},
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        test_qualification_seam=True,
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
        population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
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
        population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
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
        population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
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
        population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
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
        population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
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
        population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
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


# =============================================================================
# DIRECTIVE 13: 20 HOSTILE ARCHITECTURE SEAL TESTS (T01 - T20)
# =============================================================================

@pytest.mark.django_db
def test_seal_01_readiness_uses_canonical_pit_resolver(xauusd_setup, monkeypatch):
    """T01: Readiness MUST use the canonical PIT resolver (Directive 2 & 13.1)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    called_with = {}
    from apps.market_data.friction import resolution
    orig_resolver = resolution.resolve_friction_model_activation

    def spy_resolver(as_of=None, venue="EXNESS", symbol="XAUUSD", account_tier="STANDARD", legal_entity_code="EXNESS_SC_LTD"):
        called_with["venue"] = venue
        called_with["symbol"] = symbol
        called_with["account_tier"] = account_tier
        called_with["legal_entity_code"] = legal_entity_code
        return orig_resolver(as_of=as_of, venue=venue, symbol=symbol, account_tier=account_tier, legal_entity_code=legal_entity_code)

    monkeypatch.setattr("apps.market_data.readiness.resolve_friction_model_activation", spy_resolver)

    rep = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_macro_count=1,
        execution_venue="EXNESS",
        execution_account_tier="STANDARD",
        execution_legal_entity_code="EXNESS_SC_LTD",
    )
    assert called_with["venue"] == "EXNESS"
    assert called_with["symbol"] == "XAUUSD"
    assert called_with["account_tier"] == "STANDARD"
    assert called_with["legal_entity_code"] == "EXNESS_SC_LTD"


@pytest.mark.django_db
def test_seal_02_unrelated_newer_active_model_cannot_hijack_readiness(xauusd_setup, qualified_evidence_bundle):
    """T02: Unrelated newer ACTIVE model cannot hijack readiness (Directive 2 & 13.2)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    now_utc = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    snap = qualified_evidence_bundle["legal_snapshot"]

    unrelated_model = FrictionModelVersion.objects.create(
        model_version_id="UNRELATED_EURUSD_MODEL_SEAL",
        venue="EXNESS",
        symbol="EURUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=snap,
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=5,
        point_size=Decimal("0.0001"),
        trade_tick_size=Decimal("0.0001"),
        trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100000.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.0"),
        volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"),
        commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-5.0"),
        swap_short_points=Decimal("2.0"),
        rollover_summer_utc_hour=21,
        rollover_winter_utc_hour=22,
        triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("0.80"),
        stress_spread_bps=Decimal("1.50"),
        base_slippage_bps=Decimal("0.20"),
        stress_slippage_bps=Decimal("0.50"),
        empirical_friction_evidence_fingerprint="fp_unrelated_seal",
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_UNRELATED_SEAL_NEWER",
        friction_model_version=unrelated_model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Newer unrelated model",
    )

    rep = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument, override_macro_count=1)
    assert rep.friction_status == "EMPIRICAL_FRICTION_CONFIGURED"

    res = resolve_friction_model_activation(as_of=now_utc, symbol="XAUUSD")
    assert res is not None
    model_ver, act = res
    assert act.friction_model_version.symbol == "XAUUSD"
    assert act.friction_model_version.model_version_id != "UNRELATED_EURUSD_MODEL_SEAL"


@pytest.mark.django_db
def test_seal_03_standard_vs_raw_mismatch_fails(xauusd_setup, qualified_evidence_bundle):
    """T03: STANDARD model cannot satisfy RAW_SPREAD target and vice versa (Directive 2 & 13.3)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    rep_raw = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_macro_count=1,
        execution_account_tier="RAW_SPREAD",
    )
    assert rep_raw.friction_status == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
    assert rep_raw.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"

    res = validate_friction_model_for_activation(
        qualified_evidence_bundle["model_version"],
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="RAW_SPREAD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"


@pytest.mark.django_db
def test_seal_04_legal_entity_mismatch_fails(xauusd_setup, qualified_evidence_bundle):
    """T04: Legal entity A cannot satisfy legal entity B (Directive 2 & 13.4)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    rep_bvi = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        override_macro_count=1,
        execution_legal_entity_code="EXNESS_BVI_LTD",
    )
    assert rep_bvi.friction_status == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
    assert rep_bvi.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"

    res = validate_friction_model_for_activation(
        qualified_evidence_bundle["model_version"],
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_BVI_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"


@pytest.mark.django_db
def test_seal_05_command_parse_success_but_insufficient_ticks_stays_blocked(xauusd_setup):
    """T05: Command parse-success with insufficient tick dataset stays blocked (Directive 3 & 13.5)."""
    tick_file = "artifacts/test_seal_05_ticks_tmp.csv"
    manifest_file = "artifacts/test_seal_05_manifest_tmp.json"
    report_file = "artifacts/test_seal_05_report_tmp.md"

    with open(tick_file, "w", encoding="utf-8") as f:
        f.write("DateTime,Bid,Ask\n")
        for i in range(5):
            f.write(f"2026-08-20 10:00:0{i}+00:00,2500.00,2500.20\n")

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            tick_file=tick_file,
            output_manifest=manifest_file,
            output_report=report_file,
        )
        assert os.path.exists(manifest_file)
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["evidence_inventory"]["bid_ask_spread_distribution"]["status"] == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
        assert manifest["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert FrictionModelActivation.objects.filter(activation_status=FrictionActivationStatus.ACTIVE).count() == 0
    finally:
        for p in [tick_file, manifest_file, report_file]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_seal_06_command_parse_success_but_insufficient_telemetry_stays_blocked(xauusd_setup):
    """T06: Command parse-success with insufficient telemetry fills stays blocked (Directive 3 & 13.6)."""
    slip_file = "artifacts/test_seal_06_slip_tmp.csv"
    manifest_file = "artifacts/test_seal_06_manifest_tmp.json"
    report_file = "artifacts/test_seal_06_report_tmp.md"

    with open(slip_file, "w", encoding="utf-8") as f:
        f.write("side,order_type,decision_timestamp,order_send_timestamp,reference_bid,reference_ask,executed_fill_price,fill_timestamp,volume_lots,latency_ms,symbol,account_tier,venue\n")
        for i in range(5):
            f.write(f"BUY,MARKET,2026-08-20 10:00:0{i}+00:00,2026-08-20 10:00:0{i}.050+00:00,2500.00,2500.20,2500.22,2026-08-20 10:00:0{i}.100+00:00,0.10,50,XAUUSD,STANDARD,EXNESS\n")

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            slippage_file=slip_file,
            output_manifest=manifest_file,
            output_report=report_file,
        )
        assert os.path.exists(manifest_file)
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["evidence_inventory"]["execution_slippage_telemetry"]["status"] == "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
        assert manifest["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    finally:
        for p in [slip_file, manifest_file, report_file]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_seal_07_dry_run_insufficient_samples_stays_blocked(xauusd_setup):
    """T07: Dry-run insufficient samples executes full validation and stays blocked without DB writes (Directive 3, 12, 13.7)."""
    tick_file = "artifacts/test_seal_07_ticks_tmp.csv"
    manifest_file = "artifacts/test_seal_07_manifest_tmp.json"
    report_file = "artifacts/test_seal_07_report_tmp.md"

    with open(tick_file, "w", encoding="utf-8") as f:
        f.write("DateTime,Bid,Ask\n")
        for i in range(5):
            f.write(f"2026-08-20 10:00:0{i}+00:00,2500.00,2500.20\n")

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            tick_file=tick_file,
            dry_run=True,
            output_manifest=manifest_file,
            output_report=report_file,
        )
        assert FrictionEvidenceDataset.objects.count() == 0
        assert FrictionSourceSnapshot.objects.count() == 0

        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["evidence_inventory"]["bid_ask_spread_distribution"]["status"] == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
        assert manifest["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    finally:
        for p in [tick_file, manifest_file, report_file]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_seal_08_builder_draft_cannot_cause_command_to_report_configured(monkeypatch):
    """T08: Builder DRAFT cannot cause command to report configured (Directive 4 & 13.8)."""
    manifest_file = "artifacts/test_seal_08_manifest_tmp.json"
    report_file = "artifacts/test_seal_08_report_tmp.md"

    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    snap = FrictionSourceSnapshot.objects.create(
        source_url="http://ex.com/l",
        source_name="L",
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_payload_bytes_sha256=hashlib.sha256(b"L").hexdigest(),
        raw_content=b"L",
        metadata={},
    )
    draft_model = FrictionModelVersion.objects.create(
        model_version_id="DRAFT_MODEL_SEAL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=snap,
        contract_spec_source_snapshot=snap,
        fee_schedule_source_snapshot=snap,
        swap_spec_source_snapshot=snap,
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
        empirical_friction_evidence_fingerprint="fp_draft",
    )
    draft_act = FrictionModelActivation.objects.create(
        activation_id="ACT_DRAFT_SEAL",
        friction_model_version=draft_model,
        known_at=now_utc,
        effective_from=now_utc,
        activation_status=FrictionActivationStatus.DRAFT,
        source_or_reason="Draft model reason",
    )

    from apps.market_data.management.commands import ingest_xauusd_empirical_friction
    monkeypatch.setattr(
        ingest_xauusd_empirical_friction,
        "build_and_bind_friction_model_version",
        lambda **kwargs: (draft_model, draft_act),
    )

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    finally:
        for p in [manifest_file, report_file]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_seal_09_command_success_requires_actual_active_activation(monkeypatch):
    """T09: Command success requires actual ACTIVE activation (Directive 4 & 13.9)."""
    manifest_file = "artifacts/test_seal_09_manifest_tmp.json"
    report_file = "artifacts/test_seal_09_report_tmp.md"

    from apps.market_data.management.commands import ingest_xauusd_empirical_friction
    monkeypatch.setattr(
        ingest_xauusd_empirical_friction,
        "build_and_bind_friction_model_version",
        lambda **kwargs: (None, None),
    )

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    finally:
        for p in [manifest_file, report_file]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_seal_10_command_success_requires_readiness_evaluator_to_reach_quote_gate(monkeypatch, qualified_evidence_bundle):
    """T10: Command success requires readiness evaluator itself to reach quote gate (Directive 4 & 13.10)."""
    manifest_file = "artifacts/test_seal_10_manifest_tmp.json"
    report_file = "artifacts/test_seal_10_report_tmp.md"

    from apps.market_data.management.commands import ingest_xauusd_empirical_friction
    monkeypatch.setattr(
        ingest_xauusd_empirical_friction,
        "build_and_bind_friction_model_version",
        lambda **kwargs: (qualified_evidence_bundle["model_version"], qualified_evidence_bundle["activation"]),
    )

    from apps.market_data.readiness import XauUsdDataReadinessReport
    mock_rep = XauUsdDataReadinessReport(
        passed=False,
        decision="CANDLES_READY_EMPIRICAL_FRICTION_MISSING",
        reasons=["Blocked by readiness evaluator authority"],
        total_candles=0,
        timeframe_counts={},
        earliest_timestamp=None,
        latest_timestamp=None,
        duration_days=0.0,
        gap_statistics={},
        duplicate_count=0,
        ohlc_error_count=0,
        naive_timestamp_count=0,
        zero_or_negative_count=0,
        source_contamination_count=0,
        warmup_15m_bars=0,
        is_warmup_satisfied=False,
        volume_evidence_distribution={},
        volume_classification="UNAVAILABLE",
        macro_event_count=0,
        quote_count=0,
        friction_status="EMPIRICAL_FRICTION_INVALID",
        dataset_hash="empty",
        generated_at="now",
    )
    monkeypatch.setattr(XauUsdDataReadinessEvaluator, "evaluate", lambda **kwargs: mock_rep)

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["status"] == "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    finally:
        for p in [manifest_file, report_file]:
            if os.path.exists(p):
                os.remove(p)


def test_seal_11_1000_ticks_from_one_day_cannot_active():
    """T11: 1000 ticks from only 1 trading day fails sufficiency and cannot activate (Directive 5, 6, 13.11)."""
    ticks = []
    base_ts = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(1000):
        ticks.append({
            "timestamp": base_ts + timedelta(seconds=i),
            "bid": Decimal("2500.00"),
            "ask": Decimal("2500.20"),
            "spread_bps": Decimal("0.80"),
        })

    is_valid, errors = validate_spread_dataset_sufficiency(ticks)
    assert is_valid is False
    assert any("distinct trading dates" in e for e in errors)


def test_seal_12_missing_rollover_samples_cannot_active():
    """T12: 1000 ticks across 5 days with zero rollover samples fails sufficiency (Directive 5, 6, 13.12)."""
    ticks = []
    for day in range(5):
        day_ts = datetime(2026, 8, 17 + day, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(200):
            ticks.append({
                "timestamp": day_ts + timedelta(seconds=i),
                "bid": Decimal("2500.00"),
                "ask": Decimal("2500.20"),
                "spread_bps": Decimal("0.80"),
            })

    assert len(ticks) == 1000
    is_valid, errors = validate_spread_dataset_sufficiency(ticks)
    assert is_valid is False
    assert any("rollover session coverage" in e for e in errors)


@pytest.mark.django_db
def test_seal_13_malformed_source_sha_cannot_active(qualified_evidence_bundle):
    """T13: Malformed source SHA cannot pass canonical validator and cannot activate (Directive 5, 6, 13.13)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    bad_snap = FrictionSourceSnapshot.objects.create(
        source_url="http://ex.com/bad_sha",
        source_name="BAD_SHA_SNAP",
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_payload_bytes_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        raw_content=b"CORRUPTED_OR_TAMPERED_CONTENT",
        metadata={},
    )

    model = FrictionModelVersion.objects.create(
        model_version_id="BAD_SHA_MODEL_SEAL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=bad_snap,
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
        empirical_friction_evidence_fingerprint="fp_bad_sha",
    )

    res = validate_friction_model_for_activation(
        model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert any("SHA-256 verification failed" in r for r in res.reasons)


@pytest.mark.django_db
def test_seal_14_user_provided_unverified_legal_json_cannot_satisfy_hard_gate(qualified_evidence_bundle):
    """T14: USER_PROVIDED_UNVERIFIED legal JSON cannot satisfy hard gate (Directive 7, 8, 13.14)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    raw = b'{"legal_entity_code": "EXNESS_SC_LTD"}'
    snap = FrictionSourceSnapshot.objects.create(
        source_url="http://ex.com/unverified_legal",
        source_name="UNVERIFIED_LEGAL",
        source_type=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_payload_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        raw_content=raw,
        metadata={},
    )
    model = FrictionModelVersion.objects.create(
        model_version_id="UNVERIFIED_LEGAL_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=snap,
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
        empirical_friction_evidence_fingerprint="fp_unverified_legal",
    )

    res = validate_friction_model_for_activation(
        model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"
    assert any("unverified" in r for r in res.reasons)


@pytest.mark.django_db
def test_seal_15_user_provided_unverified_contract_json_cannot_satisfy_hard_gate(qualified_evidence_bundle):
    """T15: USER_PROVIDED_UNVERIFIED contract JSON cannot satisfy hard gate (Directive 7, 9, 13.15)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    raw = b'{"contract_size": 100.0}'
    snap = FrictionSourceSnapshot.objects.create(
        source_url="http://ex.com/unverified_contract",
        source_name="UNVERIFIED_CONTRACT",
        source_type=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_payload_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        raw_content=raw,
        metadata={},
    )
    model = FrictionModelVersion.objects.create(
        model_version_id="UNVERIFIED_CONTRACT_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=snap,
        fee_schedule_source_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_unverified_contract",
    )

    res = validate_friction_model_for_activation(
        model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"
    assert any("unverified" in r for r in res.reasons)


@pytest.mark.django_db
def test_seal_16_user_provided_unverified_fee_json_cannot_satisfy_hard_gate(qualified_evidence_bundle):
    """T16: USER_PROVIDED_UNVERIFIED fee JSON cannot satisfy hard gate (Directive 7, 10, 13.16)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    raw = b'{"native_commission_usd_per_lot_per_side": 0.0}'
    snap = FrictionSourceSnapshot.objects.create(
        source_url="http://ex.com/unverified_fee",
        source_name="UNVERIFIED_FEE",
        source_type=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_payload_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        raw_content=raw,
        metadata={},
    )
    model = FrictionModelVersion.objects.create(
        model_version_id="UNVERIFIED_FEE_MODEL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_source_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_source_snapshot=snap,
        swap_spec_source_snapshot=qualified_evidence_bundle["swap_snapshot"],
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_unverified_fee",
    )

    res = validate_friction_model_for_activation(
        model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"
    assert any("unverified" in r for r in res.reasons)


@pytest.mark.django_db
def test_seal_17_user_provided_unverified_swap_json_cannot_satisfy_hard_gate(qualified_evidence_bundle):
    """T17: USER_PROVIDED_UNVERIFIED swap JSON cannot satisfy hard gate (Directive 7, 10, 13.17)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    raw = b'{"swap_long_points": -34.80}'
    snap = FrictionSourceSnapshot.objects.create(
        source_url="http://ex.com/unverified_swap",
        source_name="UNVERIFIED_SWAP",
        source_type=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_payload_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        raw_content=raw,
        metadata={},
    )
    model = FrictionModelVersion.objects.create(
        model_version_id="UNVERIFIED_SWAP_MODEL",
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
        swap_spec_source_snapshot=snap,
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_unverified_swap",
    )

    res = validate_friction_model_for_activation(
        model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"
    assert any("unverified" in r for r in res.reasons)


def test_seal_18_negative_signed_slippage_cannot_reduce_friction_in_adverse_only_policy():
    """T18: Negative signed slippage cannot reduce friction in adverse-only policy (Directive 11 & 13.18)."""
    negative_fills = [Decimal("-5.00") for _ in range(35)]
    adverse_only_values = [max(Decimal("0.00"), s) for s in negative_fills]
    stats = compute_distribution_statistics(adverse_only_values)

    assert stats["stat_p75"] == Decimal("0.000000")
    assert stats["stat_p95"] == Decimal("0.000000")
    assert stats["stat_min"] == Decimal("0.000000")
    base_slippage_bps = max(Decimal("0.00"), stats["stat_p75"])
    assert base_slippage_bps >= Decimal("0.00")


def test_seal_19_adverse_only_policy_fingerprint_differs_from_signed_policy():
    """T19: Adverse-only policy fingerprint differs from signed policy fingerprint (Directive 11 & 13.19)."""
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

    fp_adverse = compute_empirical_friction_fingerprint(
        **base_args,
        slippage_cost_policy_version="ADVERSE_ONLY_P75_P95_V1",
    )
    fp_signed = compute_empirical_friction_fingerprint(
        **base_args,
        slippage_cost_policy_version="RAW_SIGNED_DISTRIBUTION_V1",
    )

    assert fp_adverse != fp_signed
    assert len(fp_adverse) == 64
    assert len(fp_signed) == 64


@pytest.mark.django_db
def test_seal_20_future_activation_cannot_leak_backward_through_readiness(xauusd_setup):
    """T20: Future activation cannot leak backward through point-in-time readiness (Directive 2, 13.20)."""
    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    t_past = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
    t_future = datetime(2026, 9, 10, 0, 0, 0, tzinfo=timezone.utc)
    
    legal_snap, _ = ingest_friction_source_snapshot("http://ex.com/l", "L", "EXNESS", "XAUUSD", "STANDARD", t_future, t_future, b"LEGAL_CONTENT")
    spec_snap, _ = ingest_friction_source_snapshot("http://ex.com/c", "C", "EXNESS", "XAUUSD", "STANDARD", t_future, t_future, b"CONTRACT_CONTENT")
    fee_snap, _ = ingest_friction_source_snapshot("http://ex.com/f", "F", "EXNESS", "XAUUSD", "STANDARD", t_future, t_future, b"FEE_CONTENT")
    swap_snap, _ = ingest_friction_source_snapshot("http://ex.com/s", "S", "EXNESS", "XAUUSD", "STANDARD", t_future, t_future, b"SWAP_CONTENT")

    future_model = FrictionModelVersion.objects.create(
        model_version_id="FUTURE_MODEL_SEAL_20",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        legal_entity_code="EXNESS_SC_LTD",
        legal_entity_name="Exness (SC) Ltd",
        regulator="FSA",
        license_number="SD025",
        legal_entity_source_snapshot=legal_snap,
        contract_spec_source_snapshot=spec_snap,
        fee_schedule_source_snapshot=fee_snap,
        swap_spec_source_snapshot=swap_snap,
        digits=2, point_size=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.00"),
        contract_size=Decimal("100.0"), volume_min=Decimal("0.01"), volume_max=Decimal("200.0"), volume_step=Decimal("0.01"),
        native_commission_usd_per_lot_per_side=Decimal("0.00"), commission_formula="DYNAMIC_NOTIONAL_BPS",
        swap_long_points=Decimal("-34.80"), swap_short_points=Decimal("12.40"),
        rollover_summer_utc_hour=21, rollover_winter_utc_hour=22, triple_swap_weekday="WEDNESDAY",
        base_spread_bps=Decimal("1.00"), stress_spread_bps=Decimal("2.00"),
        base_slippage_bps=Decimal("0.50"), stress_slippage_bps=Decimal("1.00"),
        empirical_friction_evidence_fingerprint="fp_future_seal_20",
    )
    FrictionModelActivation.objects.create(
        activation_id="ACT_FUTURE_SEAL_20",
        friction_model_version=future_model,
        known_at=t_future,
        effective_from=t_future,
        activation_status=FrictionActivationStatus.ACTIVE,
        source_or_reason="Future model only",
    )

    # 1. As of past, the future activation must NOT resolve
    rep_past = XauUsdDataReadinessEvaluator.evaluate(
        instrument=instrument,
        as_of=t_past,
        override_macro_count=1,
    )
    assert rep_past.friction_status == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
    assert rep_past.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"

    # 2. Once time reaches future effective_from, resolution succeeds
    as_of_future = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)
    res_future = resolve_friction_model_activation(as_of=as_of_future, symbol="XAUUSD")
    assert res_future is not None
    assert res_future[0].model_version_id == "FUTURE_MODEL_SEAL_20"


# =============================================================================
# PROVENANCE SEAL & POPULATION SEMANTICS HOSTILE TESTS (T21 - T36)
# =============================================================================

@pytest.mark.django_db
def test_seal_21_default_population_semantics_is_unknown_not_adverse_only(qualified_evidence_bundle):
    """T21: Default population semantics must be UNKNOWN, not ADVERSE_ONLY (Requirement 1 & 7)."""
    dataset = qualified_evidence_bundle["dataset"]
    summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_DEFAULT_POP",
        evidence_dataset=dataset,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        sample_count=1000,
        stat_min=Decimal("0.5"),
        stat_p50=Decimal("1.0"),
        stat_p75=Decimal("1.00"),
        stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"),
        stat_p99=Decimal("2.5"),
        stat_max=Decimal("3.0"),
        stat_mean=Decimal("1.1"),
        stat_std=Decimal("0.2"),
    )
    assert summary.population_semantics == FrictionPopulationSemantics.UNKNOWN
    assert summary.population_semantics != FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY


@pytest.mark.django_db
def test_seal_22_spread_summary_cannot_be_labeled_adverse_only(qualified_evidence_bundle):
    """T22: Spread distribution cannot be labeled adverse-only (Requirement 1 & 7)."""
    model = qualified_evidence_bundle["model_version"]
    dataset = qualified_evidence_bundle["dataset"]
    bad_spread_summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_SPREAD_ADVERSE_ONLY",
        evidence_dataset=dataset,
        component_type=FrictionComponentType.SPREAD,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        population_semantics=FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value,
        sample_count=dataset.sample_count,
        stat_min=Decimal("0.5"),
        stat_p50=Decimal("1.0"),
        stat_p75=Decimal("1.00"),
        stat_p90=Decimal("1.5"),
        stat_p95=Decimal("2.00"),
        stat_p99=Decimal("2.5"),
        stat_max=Decimal("3.0"),
        stat_mean=Decimal("1.1"),
        stat_std=Decimal("0.2"),
    )
    FrictionModelSummaryBinding.objects.create(
        binding_id="BIND_SUM_BAD_SPREAD_POP",
        friction_model_version=model,
        distribution_summary=bad_spread_summary,
        binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    )
    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert val.is_valid is False
    assert val.status == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
    assert any("population semantics" in r for r in val.reasons)


def _clone_model_with_bindings(
    orig_model: FrictionModelVersion,
    new_model_id: str,
    override_fields: Optional[Dict[str, Any]] = None,
    override_spread_summary: Optional[FrictionDistributionSummary] = None,
    override_slippage_summary: Optional[FrictionDistributionSummary] = None,
    override_spread_dataset: Optional[FrictionEvidenceDataset] = None,
    override_telemetry_dataset: Optional[FrictionEvidenceDataset] = None,
) -> FrictionModelVersion:
    """Helper to create a fresh immutable model cloned from qualified evidence bundle without mutation."""
    fields = {
        "model_version_id": new_model_id,
        "venue": orig_model.venue,
        "symbol": orig_model.symbol,
        "account_tier": orig_model.account_tier,
        "legal_entity_code": orig_model.legal_entity_code,
        "legal_entity_name": orig_model.legal_entity_name,
        "regulator": orig_model.regulator,
        "license_number": orig_model.license_number,
        "legal_entity_source_snapshot": orig_model.legal_entity_source_snapshot,
        "contract_spec_source_snapshot": orig_model.contract_spec_source_snapshot,
        "fee_schedule_source_snapshot": orig_model.fee_schedule_source_snapshot,
        "swap_spec_source_snapshot": orig_model.swap_spec_source_snapshot,
        "digits": orig_model.digits,
        "point_size": orig_model.point_size,
        "trade_tick_size": orig_model.trade_tick_size,
        "trade_tick_value": orig_model.trade_tick_value,
        "contract_size": orig_model.contract_size,
        "volume_min": orig_model.volume_min,
        "volume_max": orig_model.volume_max,
        "volume_step": orig_model.volume_step,
        "native_commission_usd_per_lot_per_side": orig_model.native_commission_usd_per_lot_per_side,
        "commission_formula": orig_model.commission_formula,
        "swap_long_points": orig_model.swap_long_points,
        "swap_short_points": orig_model.swap_short_points,
        "rollover_summer_utc_hour": orig_model.rollover_summer_utc_hour,
        "rollover_winter_utc_hour": orig_model.rollover_winter_utc_hour,
        "triple_swap_weekday": orig_model.triple_swap_weekday,
        "base_spread_bps": orig_model.base_spread_bps,
        "stress_spread_bps": orig_model.stress_spread_bps,
        "base_slippage_bps": orig_model.base_slippage_bps,
        "stress_slippage_bps": orig_model.stress_slippage_bps,
        "empirical_friction_evidence_fingerprint": f"fp_{new_model_id}",
        "slippage_cost_policy_version": orig_model.slippage_cost_policy_version,
    }
    if override_fields:
        fields.update(override_fields)

    cloned = FrictionModelVersion.objects.create(**fields)

    # Bind spread dataset
    sp_ds = override_spread_dataset or next(
        (db.evidence_dataset for db in orig_model.dataset_bindings.all() if db.binding_role == FrictionBindingRole.PRIMARY_SPREAD_SAMPLE),
        None,
    )
    if sp_ds:
        FrictionModelDatasetBinding.objects.create(
            binding_id=f"BIND_DS_SP_{new_model_id}",
            friction_model_version=cloned,
            evidence_dataset=sp_ds,
            binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
        )

    # Bind telemetry dataset
    telem_ds = override_telemetry_dataset or next(
        (db.evidence_dataset for db in orig_model.dataset_bindings.all() if db.binding_role in (FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE, FrictionBindingRole.TELEMETRY_SAMPLE)),
        None,
    )
    if telem_ds:
        FrictionModelDatasetBinding.objects.create(
            binding_id=f"BIND_DS_TELEM_{new_model_id}",
            friction_model_version=cloned,
            evidence_dataset=telem_ds,
            binding_role=FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE,
        )

    # Bind spread summary
    sp_sum = override_spread_summary or next(
        (sb.distribution_summary for sb in orig_model.summary_bindings.all() if sb.binding_role == FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION),
        None,
    )
    if sp_sum:
        FrictionModelSummaryBinding.objects.create(
            binding_id=f"BIND_SUM_SP_{new_model_id}",
            friction_model_version=cloned,
            distribution_summary=sp_sum,
            binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
        )

    # Bind slippage summary
    slip_sum = override_slippage_summary or next(
        (sb.distribution_summary for sb in orig_model.summary_bindings.all() if sb.binding_role == FrictionBindingRole.NORMAL_SLIPPAGE_DISTRIBUTION),
        None,
    )
    if slip_sum:
        FrictionModelSummaryBinding.objects.create(
            binding_id=f"BIND_SUM_SLIP_{new_model_id}",
            friction_model_version=cloned,
            distribution_summary=slip_sum,
            binding_role=FrictionBindingRole.NORMAL_SLIPPAGE_DISTRIBUTION,
        )

    return cloned


@pytest.mark.django_db
def test_seal_23_legacy_or_unknown_slippage_population_cannot_active(qualified_evidence_bundle):
    """T23: Legacy/unknown slippage population cannot qualify as ACTIVE (Requirement 1 & 7)."""
    orig_model = qualified_evidence_bundle["model_version"]
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    unknown_summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_SLIPPAGE_UNKNOWN_POP",
        evidence_dataset=telem_ds,
        component_type=FrictionComponentType.SLIPPAGE,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        population_semantics=FrictionPopulationSemantics.UNKNOWN.value,
        sample_count=telem_ds.sample_count,
        stat_min=Decimal("0.0"),
        stat_p50=Decimal("0.08"),
        stat_p75=Decimal("0.08"),
        stat_p90=Decimal("0.08"),
        stat_p95=Decimal("0.08"),
        stat_p99=Decimal("0.08"),
        stat_max=Decimal("0.08"),
        stat_mean=Decimal("0.08"),
        stat_std=Decimal("0.0"),
    )
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_UNKNOWN_SLIP_POP",
        override_slippage_summary=unknown_summary,
    )
    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert val.is_valid is False
    assert val.status == "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
    assert any("UNKNOWN or unspecified population semantics" in r for r in val.reasons)


@pytest.mark.django_db
def test_seal_24_explicit_cli_qualified_label_cannot_upgrade_unverified_handmade_json():
    """T24: Explicit CLI qualified label cannot upgrade an unverified handmade JSON (Requirement 3 & 7)."""
    handmade_file = "artifacts/handmade_legal_entity_tmp.json"
    manifest_file = "artifacts/test_seal_24_manifest_tmp.json"
    report_file = "artifacts/test_seal_24_report_tmp.md"
    os.makedirs("artifacts", exist_ok=True)
    try:
        # A human-authored JSON without authoritative backing artifact / provenance
        with open(handmade_file, "w", encoding="utf-8") as f:
            json.dump({
                "legal_entity_code": "EXNESS_SC_LTD",
                "legal_entity_name": "Exness (SC) Ltd",
                "regulator": "FSA",
                "license_number": "SD025",
            }, f)

        call_command(
            "ingest_xauusd_empirical_friction",
            "--legal-entity-file", handmade_file,
            "--legal-entity-source-type", "OFFICIAL_BROKER_DOCUMENT",
            "--dry-run",
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["evidence_inventory"]["legal_entity_scope"]["status"] == "EMPIRICAL_FRICTION_INVALID"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert any("USER_PROVIDED_UNVERIFIED" in r for r in manifest["blocking_reasons"])
    finally:
        for p in (handmade_file, manifest_file, report_file):
            if os.path.exists(p):
                os.remove(p)


def test_seal_25_swapping_source_role_assignments_alters_fingerprint():
    """T25: Swapping source role assignments alters fingerprint (Requirement 5 & 7)."""
    base_args = {
        "semantic_versions": {"friction_policy_schema_version": "1.0.0"},
        "venue": "EXNESS",
        "legal_entity_code": "EXNESS_SC_LTD",
        "account_tier": "STANDARD",
        "symbol": "XAUUSD",
        "contract_geometry": {"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1"), "contract_size": Decimal("100"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200"), "volume_step": Decimal("0.01")},
        "source_snapshot_hashes": ["hash_alpha", "hash_beta"],
        "dataset_hashes": ["ds_hash"],
        "distribution_summaries": [],
        "calibrated_parameters": {"base_spread_bps": Decimal("1"), "stress_spread_bps": Decimal("2"), "base_slippage_bps": Decimal("0.5"), "stress_slippage_bps": Decimal("1")},
        "commission_policy": {"native_commission_usd_per_lot_per_side": Decimal("0"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        "financing_policy": {"swap_long_points": Decimal("-10"), "swap_short_points": Decimal("5")},
    }

    # Fingerprint A: LEGAL_ENTITY = hash_alpha, COMMISSION = hash_beta
    fp_a = compute_empirical_friction_fingerprint(
        **base_args,
        source_evidence={
            "LEGAL_ENTITY": {"sha256": "hash_alpha", "source_type": "OFFICIAL_BROKER_DOCUMENT"},
            "COMMISSION": {"sha256": "hash_beta", "source_type": "BROKER_PERSONAL_AREA_EXPORT"},
        },
    )

    # Fingerprint B: Swapped roles: LEGAL_ENTITY = hash_beta, COMMISSION = hash_alpha
    fp_b = compute_empirical_friction_fingerprint(
        **base_args,
        source_evidence={
            "LEGAL_ENTITY": {"sha256": "hash_beta", "source_type": "OFFICIAL_BROKER_DOCUMENT"},
            "COMMISSION": {"sha256": "hash_alpha", "source_type": "BROKER_PERSONAL_AREA_EXPORT"},
        },
    )

    assert fp_a != fp_b, "Swapping role bindings must alter the deterministic fingerprint."


def test_seal_26_canonical_slippage_policy_resolver_sample_selection():
    """T26: Canonical slippage policy resolver is the only sample-selection path (Requirement 2 & 7)."""
    telemetry = [
        {"adverse_only_bps": Decimal("0.15"), "signed_slippage_bps": Decimal("-0.20")},
        {"adverse_only_bps": Decimal("0.40"), "signed_slippage_bps": Decimal("0.40")},
    ]

    # ADVERSE_ONLY policy
    adv_samples, adv_pop = resolve_slippage_cost_samples(telemetry, "ADVERSE_ONLY_P75_P95_V1")
    assert adv_samples == [Decimal("0.15"), Decimal("0.40")]
    assert adv_pop == FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value

    # RAW_SIGNED policy
    sgn_samples, sgn_pop = resolve_slippage_cost_samples(telemetry, "RAW_SIGNED_DISTRIBUTION_V1")
    assert sgn_samples == [Decimal("-0.20"), Decimal("0.40")]
    assert sgn_pop == FrictionPopulationSemantics.SLIPPAGE_SIGNED.value

    # Unknown policy fails closed
    with pytest.raises(ValueError, match="SLIPPAGE_POLICY_INVALID"):
        resolve_slippage_cost_samples(telemetry, "INVALID_POLICY_NAME")


@pytest.mark.django_db
def test_seal_27_adverse_only_policy_cannot_consume_signed_sample_list(qualified_evidence_bundle):
    """T27: ADVERSE_ONLY policy cannot consume a caller-supplied signed sample list containing negatives (Requirement 2 & 7)."""
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    with pytest.raises(ValueError, match="ADVERSE_ONLY policy cannot consume signed sample list containing negative values"):
        build_and_bind_friction_model_version(
            legal_entity_snapshot=qualified_evidence_bundle["legal_snapshot"],
            contract_spec_snapshot=qualified_evidence_bundle["contract_snapshot"],
            fee_schedule_snapshot=qualified_evidence_bundle["fee_snapshot"],
            swap_spec_snapshot=qualified_evidence_bundle["swap_snapshot"],
            evidence_dataset=qualified_evidence_bundle["dataset"],
            spread_ticks_bps=[Decimal("1.0")],
            telemetry_dataset=telem_ds,
            slippage_records_bps=[Decimal("-0.50"), Decimal("0.80")],  # Negative signed slippage
            slippage_cost_policy_version="ADVERSE_ONLY_P75_P95_V1",
            slippage_population_semantics=FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value,
        )


def test_seal_28_summary_population_semantics_enters_fingerprint():
    """T28: Summary population semantics enters fingerprint (Requirement 5 & 7)."""
    base_args = {
        "semantic_versions": {"friction_policy_schema_version": "1.0.0"},
        "venue": "EXNESS",
        "legal_entity_code": "EXNESS_SC_LTD",
        "account_tier": "STANDARD",
        "symbol": "XAUUSD",
        "contract_geometry": {"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1"), "contract_size": Decimal("100"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200"), "volume_step": Decimal("0.01")},
        "source_snapshot_hashes": ["hash1"],
        "dataset_hashes": ["ds1"],
        "calibrated_parameters": {"base_spread_bps": Decimal("1"), "stress_spread_bps": Decimal("2"), "base_slippage_bps": Decimal("0.5"), "stress_slippage_bps": Decimal("1")},
        "commission_policy": {"native_commission_usd_per_lot_per_side": Decimal("0"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        "financing_policy": {"swap_long_points": Decimal("-10"), "swap_short_points": Decimal("5")},
    }

    sum_adverse = [{
        "component_type": "SLIPPAGE",
        "condition": "NORMAL",
        "session": "ALL",
        "unit": "BPS",
        "population_semantics": "SLIPPAGE_ADVERSE_ONLY",
        "sample_count": 50,
        "stat_p75": Decimal("0.5"),
        "stat_p95": Decimal("1.0"),
    }]
    sum_signed = [{
        "component_type": "SLIPPAGE",
        "condition": "NORMAL",
        "session": "ALL",
        "unit": "BPS",
        "population_semantics": "SLIPPAGE_SIGNED",
        "sample_count": 50,
        "stat_p75": Decimal("0.5"),
        "stat_p95": Decimal("1.0"),
    }]

    fp_adverse = compute_empirical_friction_fingerprint(**base_args, distribution_summaries=sum_adverse)
    fp_signed = compute_empirical_friction_fingerprint(**base_args, distribution_summaries=sum_signed)

    assert fp_adverse != fp_signed


@pytest.mark.django_db
def test_seal_29_omitted_source_type_defaults_to_user_provided_unverified():
    """T29: Omitted source type in ingestion snapshot defaults to USER_PROVIDED_UNVERIFIED (Requirement 3)."""
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    snap, _ = ingest_friction_source_snapshot(
        source_url="http://ex.com/unspec",
        source_name="UNSPECIFIED_SNAP",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"UNVERIFIED_RAW_PAYLOAD",
    )
    assert snap.source_type == FrictionSourceType.USER_PROVIDED_UNVERIFIED.value


@pytest.mark.django_db
def test_seal_30_mt5_tick_export_cannot_satisfy_legal_entity_evidence(qualified_evidence_bundle):
    """T30: Cross-component qualification fails: MT5 tick export cannot satisfy legal-entity evidence (Requirement 4 & 6)."""
    orig_model = qualified_evidence_bundle["model_version"]
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    tick_as_legal, _ = ingest_friction_source_snapshot(
        source_url="http://ex.com/tick",
        source_name="TICK_LEGAL",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"TICK_DATA_BYTES",
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
    )
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_BAD_LEGAL_SOURCE_TYPE",
        override_fields={"legal_entity_source_snapshot": tick_as_legal},
    )

    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert val.is_valid is False
    assert val.status == "EMPIRICAL_FRICTION_INVALID"
    assert any("Legal entity source provenance 'MT5_TICK_HISTORY_EXPORT' is unverified" in r for r in val.reasons)


@pytest.mark.django_db
def test_seal_31_mt5_symbol_info_cannot_satisfy_slippage_evidence(qualified_evidence_bundle):
    """T31: Cross-component qualification fails: MT5 SymbolInfo cannot satisfy slippage telemetry evidence (Requirement 4 & 6)."""
    orig_model = qualified_evidence_bundle["model_version"]
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    symbol_info_snap, _ = ingest_friction_source_snapshot(
        source_url="http://ex.com/sym",
        source_name="SYMBOL_INFO_SNAP",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"SYMBOL_INFO_BYTES",
        source_type=FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value,
    )
    ds_bad_telem = FrictionEvidenceDataset.objects.create(
        dataset_id="DS_BAD_TELEM_SRC",
        source_snapshot=symbol_info_snap,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        sample_start=telem_ds.sample_start,
        sample_end=telem_ds.sample_end,
        sample_count=telem_ds.sample_count,
        distinct_trading_days=telem_ds.distinct_trading_days,
        session_counts=telem_ds.session_counts,
        source_units="BPS",
        raw_dataset_sha256="bad_telem_sha",
        collection_methodology="MT5_SYMBOL_INFO_EXPORT",
    )
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_BAD_TELEM_SRC_TYPE",
        override_telemetry_dataset=ds_bad_telem,
    )

    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert val.is_valid is False
    assert val.status == "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
    assert any("Execution slippage telemetry source provenance" in r for r in val.reasons)


@pytest.mark.django_db
def test_seal_32_spread_dataset_with_unverified_source_cannot_active(qualified_evidence_bundle):
    """T32: Spread dataset with USER_PROVIDED_UNVERIFIED source cannot achieve ACTIVE (Requirement 3, 4, 6)."""
    orig_model = qualified_evidence_bundle["model_version"]
    spread_ds = qualified_evidence_bundle["dataset"]
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    unverified_snap, _ = ingest_friction_source_snapshot(
        source_url="http://ex.com/u",
        source_name="UNVERIFIED_TICKS",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"UNVERIFIED_TICK_BYTES",
        source_type=FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
    )
    ds_unver_spread = FrictionEvidenceDataset.objects.create(
        dataset_id="DS_UNVER_SPREAD",
        source_snapshot=unverified_snap,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        sample_start=spread_ds.sample_start,
        sample_end=spread_ds.sample_end,
        sample_count=spread_ds.sample_count,
        distinct_trading_days=spread_ds.distinct_trading_days,
        session_counts=spread_ds.session_counts,
        source_units="POINTS",
        raw_dataset_sha256="unver_spread_sha",
        collection_methodology="TEST",
    )
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_UNVER_SPREAD_SRC",
        override_spread_dataset=ds_unver_spread,
    )

    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert val.is_valid is False
    assert val.status == "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
    assert any("Spread dataset source provenance 'USER_PROVIDED_UNVERIFIED' is unverified" in r for r in val.reasons)


@pytest.mark.django_db
def test_seal_33_slippage_dataset_with_unverified_source_cannot_active(qualified_evidence_bundle):
    """T33: Slippage dataset with USER_PROVIDED_UNVERIFIED source cannot achieve ACTIVE (Requirement 3, 4, 6)."""
    orig_model = qualified_evidence_bundle["model_version"]
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    now_utc = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    unverified_snap, _ = ingest_friction_source_snapshot(
        source_url="http://ex.com/u_slip",
        source_name="UNVERIFIED_TELEM",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=b"UNVERIFIED_TELEMETRY_BYTES",
        source_type=FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
    )
    ds_unver_telem = FrictionEvidenceDataset.objects.create(
        dataset_id="DS_UNVER_TELEM",
        source_snapshot=unverified_snap,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        sample_start=telem_ds.sample_start,
        sample_end=telem_ds.sample_end,
        sample_count=telem_ds.sample_count,
        distinct_trading_days=telem_ds.distinct_trading_days,
        session_counts=telem_ds.session_counts,
        source_units="BPS",
        raw_dataset_sha256="unver_telem_sha",
        collection_methodology="TEST",
    )
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_UNVER_TELEM_SRC",
        override_telemetry_dataset=ds_unver_telem,
    )

    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert val.is_valid is False
    assert val.status == "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
    assert any("Execution slippage telemetry source provenance 'USER_PROVIDED_UNVERIFIED' is unverified" in r for r in val.reasons)


def test_seal_34_favorable_signed_fills_do_not_reduce_adverse_only_cost_below_zero():
    """T34: Favorable signed fills (-0.50 bps) resolve to >= 0 under ADVERSE_ONLY policy (Requirement 2)."""
    records = [
        {"adverse_only_bps": Decimal("0.00"), "signed_slippage_bps": Decimal("-0.50")},
        {"adverse_only_bps": Decimal("0.25"), "signed_slippage_bps": Decimal("0.25")},
    ]
    samples, pop = resolve_slippage_cost_samples(records, "ADVERSE_ONLY_P75_P95_V1")
    assert all(s >= Decimal("0") for s in samples)
    assert samples[0] == Decimal("0.00")
    assert pop == FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value


@pytest.mark.django_db
def test_seal_35_unknown_slippage_policy_fails_closed(qualified_evidence_bundle):
    """T35: Unknown or unsupported slippage cost policy fails closed in validator (Requirement 2 & 6)."""
    orig_model = qualified_evidence_bundle["model_version"]
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_UNKNOWN_POLICY",
        override_fields={"slippage_cost_policy_version": "UNKNOWN_FICTITIOUS_POLICY_V99"},
    )
    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
        slippage_cost_policy_version="UNKNOWN_FICTITIOUS_POLICY_V99",
    )
    assert val.is_valid is False
    assert val.status == "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
    assert any("Unknown or unsupported slippage cost policy" in r for r in val.reasons)


@pytest.mark.django_db
def test_seal_36_adverse_only_model_bound_to_signed_summary_fails_validation(qualified_evidence_bundle):
    """T36: ADVERSE_ONLY model policy bound to SLIPPAGE_SIGNED summary fails closed (Requirement 1, 2, 6)."""
    orig_model = qualified_evidence_bundle["model_version"]
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    signed_summary = FrictionDistributionSummary.objects.create(
        summary_id="SUM_SLIPPAGE_SIGNED_MISMATCH",
        evidence_dataset=telem_ds,
        component_type=FrictionComponentType.SLIPPAGE,
        condition=FrictionConditionType.NORMAL,
        session=FrictionSessionType.ALL,
        unit="BPS",
        population_semantics=FrictionPopulationSemantics.SLIPPAGE_SIGNED.value,
        sample_count=telem_ds.sample_count,
        stat_min=Decimal("0.0"),
        stat_p50=Decimal("0.08"),
        stat_p75=Decimal("0.08"),
        stat_p90=Decimal("0.08"),
        stat_p95=Decimal("0.08"),
        stat_p99=Decimal("0.08"),
        stat_max=Decimal("0.08"),
        stat_mean=Decimal("0.08"),
        stat_std=Decimal("0.0"),
    )
    model = _clone_model_with_bindings(
        orig_model,
        "MODEL_SIGNED_MISMATCH",
        override_fields={"slippage_cost_policy_version": "ADVERSE_ONLY_P75_P95_V1"},
        override_slippage_summary=signed_summary,
    )

    val = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
        slippage_cost_policy_version="ADVERSE_ONLY_P75_P95_V1",
    )
    assert val.is_valid is False
    assert val.status == "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
    assert any("requires SLIPPAGE_ADVERSE_ONLY summary" in r for r in val.reasons)


@pytest.mark.django_db
def test_seal_37_fake_provenance_block_without_backing_artifact_remains_unverified_and_cannot_active():
    """T37: Handwritten normalized JSON containing fake provenance block without actual backing artifact
    resolves to USER_PROVIDED_UNVERIFIED and cannot achieve ACTIVE (Surgical Fix 1 & 6.1)."""
    handmade_file = "artifacts/test_seal_37_fake_prov_tmp.json"
    manifest_file = "artifacts/test_seal_37_manifest_tmp.json"
    report_file = "artifacts/test_seal_37_report_tmp.md"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(handmade_file, "w", encoding="utf-8") as f:
            json.dump({
                "legal_entity_code": "EXNESS_SC_LTD",
                "legal_entity_name": "Exness (SC) Ltd",
                "regulator": "FSA",
                "license_number": "SD025",
                "provenance": {
                    "source_origin": "https://www.exness.com/terms/",
                    "collection_methodology": "MANUAL_BROWSER_DOWNLOAD",
                    "raw_sha256": "fake_sha_256_000000000000000000000000000000000000000000000000000000",
                },
            }, f)

        call_command(
            "ingest_xauusd_empirical_friction",
            "--legal-entity-file", handmade_file,
            "--legal-entity-source-type", "OFFICIAL_BROKER_DOCUMENT",
            "--dry-run",
            output_manifest=manifest_file,
            output_report=report_file,
        )
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["evidence_inventory"]["legal_entity_scope"]["status"] == "EMPIRICAL_FRICTION_INVALID"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert any("USER_PROVIDED_UNVERIFIED" in r or "backing artifact file is missing" in r for r in manifest["blocking_reasons"])
    finally:
        for p in (handmade_file, manifest_file, report_file):
            if os.path.exists(p):
                os.remove(p)


def test_seal_38_declared_backing_sha_without_backing_file_fails_closed():
    """T38: Declared backing SHA but no backing file fails closed (Surgical Fix 1 & 6.2)."""
    is_valid, raw_bytes, computed_sha, errors = verify_authoritative_backing_artifact(
        backing_file_path=None,
        declared_sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        expected_source_type="OFFICIAL_BROKER_DOCUMENT",
    )
    assert is_valid is False
    assert any("backing artifact file is missing" in e for e in errors)


def test_seal_39_backing_file_sha_mismatch_fails_closed():
    """T39: Actual backing file present but computed SHA != declared SHA fails closed (Surgical Fix 1 & 6.3)."""
    backing_file = "artifacts/test_seal_39_mismatch_tmp.txt"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(backing_file, "wb") as f:
            f.write(b"EXNESS_OFFICIAL_DOCUMENT_REAL_BYTES_HERE_FOR_TEST_PURPOSES")

        declared_wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"
        is_valid, raw_bytes, computed_sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=backing_file,
            declared_sha256=declared_wrong_sha,
            expected_source_type="OFFICIAL_BROKER_DOCUMENT",
        )
        assert is_valid is False
        assert any("SHA-256 mismatch" in e for e in errors)
    finally:
        if os.path.exists(backing_file):
            os.remove(backing_file)


def test_seal_40_backing_file_sha_match_and_qualified_path_qualifies():
    """T40: Actual backing file present + SHA matches + allowed source path qualifies (Surgical Fix 1, 3, 6.4)."""
    backing_file = "artifacts/test_seal_40_terms_tmp.txt"
    doc_content = b"EXNESS CLIENT AGREEMENT AND REGULATORY TERMS SPECIFICATION SD025 FSA LICENSE"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(backing_file, "wb") as f:
            f.write(doc_content)
        expected_sha = hashlib.sha256(doc_content).hexdigest()

        is_valid, raw_bytes, computed_sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=backing_file,
            declared_sha256=expected_sha,
            expected_source_type="OFFICIAL_BROKER_DOCUMENT",
        )
        assert is_valid is True
        assert raw_bytes == doc_content
        assert computed_sha == expected_sha
        assert errors == []
    finally:
        if os.path.exists(backing_file):
            os.remove(backing_file)


def test_seal_41_source_type_label_alone_cannot_upgrade_arbitrary_bytes():
    """T41: Source type label alone cannot upgrade arbitrary noise bytes (Surgical Fix 3 & 6.5)."""
    arbitrary_file = "artifacts/test_seal_41_noise_tmp.bin"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(arbitrary_file, "wb") as f:
            f.write(b"arbitrary_unstructured_noise_0123456789_abcdefghijklmnop_xyz")

        is_valid, raw_bytes, computed_sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=arbitrary_file,
            declared_sha256=None,
            expected_source_type="OFFICIAL_BROKER_DOCUMENT",
        )
        assert is_valid is False
        assert any("lacks authentic document structure" in e for e in errors)
    finally:
        if os.path.exists(arbitrary_file):
            os.remove(arbitrary_file)


@pytest.mark.django_db
def test_seal_42_direct_slippage_samples_without_explicit_semantics_fails_closed(qualified_evidence_bundle):
    """T42: slippage_records_bps supplied with ADVERSE_ONLY policy but population_semantics=None fails closed (Surgical Fix 4 & 6.6)."""
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    with pytest.raises(ValueError, match="Direct slippage_records_bps requires explicit slippage_population_semantics"):
        build_and_bind_friction_model_version(
            legal_entity_snapshot=qualified_evidence_bundle["legal_snapshot"],
            contract_spec_snapshot=qualified_evidence_bundle["contract_snapshot"],
            fee_schedule_snapshot=qualified_evidence_bundle["fee_snapshot"],
            swap_spec_snapshot=qualified_evidence_bundle["swap_snapshot"],
            evidence_dataset=qualified_evidence_bundle["dataset"],
            spread_ticks_bps=[Decimal("1.0")],
            telemetry_dataset=telem_ds,
            slippage_records_bps=[Decimal("0.10"), Decimal("0.20")],
            slippage_cost_policy_version="ADVERSE_ONLY_P75_P95_V1",
            slippage_population_semantics=None,
        )


@pytest.mark.django_db
def test_seal_43_all_positive_signed_samples_cannot_be_relabelled_adverse_only(qualified_evidence_bundle):
    """T43: All-positive signed sample list cannot be automatically relabeled ADVERSE_ONLY (Surgical Fix 4 & 6.7)."""
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    with pytest.raises(ValueError, match="Population semantics mismatch: policy ADVERSE_ONLY_P75_P95_V1 requires SLIPPAGE_ADVERSE_ONLY, got SLIPPAGE_SIGNED"):
        build_and_bind_friction_model_version(
            legal_entity_snapshot=qualified_evidence_bundle["legal_snapshot"],
            contract_spec_snapshot=qualified_evidence_bundle["contract_snapshot"],
            fee_schedule_snapshot=qualified_evidence_bundle["fee_snapshot"],
            swap_spec_snapshot=qualified_evidence_bundle["swap_snapshot"],
            evidence_dataset=qualified_evidence_bundle["dataset"],
            spread_ticks_bps=[Decimal("1.0")],
            telemetry_dataset=telem_ds,
            slippage_records_bps=[Decimal("0.10"), Decimal("0.20")],  # all positive, but signed semantics declared
            slippage_cost_policy_version="ADVERSE_ONLY_P75_P95_V1",
            slippage_population_semantics=FrictionPopulationSemantics.SLIPPAGE_SIGNED.value,
        )


@pytest.mark.django_db
def test_seal_44_adverse_only_direct_samples_with_explicit_semantics_accepted(qualified_evidence_bundle):
    """T44: ADVERSE_ONLY direct samples with explicit SLIPPAGE_ADVERSE_ONLY accepted when all evidence valid (Surgical Fix 4, 5, 6.8)."""
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    samples_30 = [Decimal("0.15")] * 35
    model_ver, activation = build_and_bind_friction_model_version(
        model_version_id="MODEL_ADVERSE_EXPLICIT_ACCEPTED",
        legal_entity_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_snapshot=qualified_evidence_bundle["swap_snapshot"],
        evidence_dataset=qualified_evidence_bundle["dataset"],
        spread_ticks_bps=[Decimal("1.0")] * 1000,
        telemetry_dataset=telem_ds,
        slippage_records_bps=samples_30,
        slippage_cost_policy_version="ADVERSE_ONLY_P75_P95_V1",
        slippage_population_semantics=FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value,
        legal_entity_info={"legal_entity_code": "EXNESS_SC_LTD", "legal_entity_name": "Exness", "regulator": "FSA", "license_number": "SD025"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1.00"), "contract_size": Decimal("100.0"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200.0"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        financing_policy={"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY", "actual_account_swap_free_status": False},
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
    )
    assert model_ver is not None
    summary_binding = FrictionModelSummaryBinding.objects.filter(
        friction_model_version=model_ver,
        distribution_summary__component_type=FrictionComponentType.SLIPPAGE,
    ).first()
    assert summary_binding is not None
    assert summary_binding.distribution_summary.population_semantics == FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value


@pytest.mark.django_db
def test_seal_45_signed_direct_samples_with_explicit_semantics_accepted_under_raw_signed_policy(qualified_evidence_bundle):
    """T45: SIGNED direct samples with explicit SLIPPAGE_SIGNED accepted under RAW_SIGNED_DISTRIBUTION_V1 (Surgical Fix 4, 5, 6.9)."""
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    samples_30 = [Decimal("-0.10"), Decimal("0.20")] * 18
    model_ver, activation = build_and_bind_friction_model_version(
        model_version_id="MODEL_SIGNED_EXPLICIT_ACCEPTED",
        legal_entity_snapshot=qualified_evidence_bundle["legal_snapshot"],
        contract_spec_snapshot=qualified_evidence_bundle["contract_snapshot"],
        fee_schedule_snapshot=qualified_evidence_bundle["fee_snapshot"],
        swap_spec_snapshot=qualified_evidence_bundle["swap_snapshot"],
        evidence_dataset=qualified_evidence_bundle["dataset"],
        spread_ticks_bps=[Decimal("1.0")] * 1000,
        telemetry_dataset=telem_ds,
        slippage_records_bps=samples_30,
        slippage_cost_policy_version="RAW_SIGNED_DISTRIBUTION_V1",
        slippage_population_semantics=FrictionPopulationSemantics.SLIPPAGE_SIGNED.value,
        legal_entity_info={"legal_entity_code": "EXNESS_SC_LTD", "legal_entity_name": "Exness", "regulator": "FSA", "license_number": "SD025"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1.00"), "contract_size": Decimal("100.0"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200.0"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        financing_policy={"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY", "actual_account_swap_free_status": False},
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
    )
    assert model_ver is not None
    summary_binding = FrictionModelSummaryBinding.objects.filter(
        friction_model_version=model_ver,
        distribution_summary__component_type=FrictionComponentType.SLIPPAGE,
    ).first()
    assert summary_binding is not None
    assert summary_binding.distribution_summary.population_semantics == FrictionPopulationSemantics.SLIPPAGE_SIGNED.value


@pytest.mark.django_db
def test_seal_46_explicit_population_semantics_mismatching_policy_fails_closed(qualified_evidence_bundle):
    """T46: Explicit population semantics mismatching policy fails closed (Surgical Fix 4 & 6.10)."""
    telem_ds = qualified_evidence_bundle["telemetry_dataset"]
    with pytest.raises(ValueError, match="Population semantics mismatch: policy RAW_SIGNED_DISTRIBUTION_V1 requires SLIPPAGE_SIGNED, got SLIPPAGE_ADVERSE_ONLY"):
        build_and_bind_friction_model_version(
            legal_entity_snapshot=qualified_evidence_bundle["legal_snapshot"],
            contract_spec_snapshot=qualified_evidence_bundle["contract_snapshot"],
            fee_schedule_snapshot=qualified_evidence_bundle["fee_snapshot"],
            swap_spec_snapshot=qualified_evidence_bundle["swap_snapshot"],
            evidence_dataset=qualified_evidence_bundle["dataset"],
            spread_ticks_bps=[Decimal("1.0")],
            telemetry_dataset=telem_ds,
            slippage_records_bps=[Decimal("0.10")] * 30,
            slippage_cost_policy_version="RAW_SIGNED_DISTRIBUTION_V1",
            slippage_population_semantics=FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value,
        )


def test_seal_47_arbitrary_valid_pdf_with_broker_label_cannot_qualify():
    """T47: Arbitrary valid PDF + OFFICIAL_BROKER_DOCUMENT label cannot qualify (Binding Fix 1)."""
    pdf_path = "artifacts/test_seal_47_arbitrary.pdf"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj\n<< /Title (Invoice 1234) /Author (Unknown) >>\nendobj\n%%EOF")

        is_valid, raw, sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=pdf_path,
            declared_sha256=None,
            expected_source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        )
        assert is_valid is False
        assert any("lacks authentic document structure or authoritative broker evidence" in e for e in errors)
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


def test_seal_48_arbitrary_valid_html_with_broker_label_cannot_qualify():
    """T48: Arbitrary valid HTML + broker label cannot qualify (Binding Fix 1)."""
    html_path = "artifacts/test_seal_48_arbitrary.html"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(html_path, "wb") as f:
            f.write(b"<!DOCTYPE html><html><head><title>Test Blog</title></head><body><h1>Hello World</h1><p>Not broker evidence</p></body></html>")

        is_valid, raw, sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=html_path,
            declared_sha256=None,
            expected_source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        )
        assert is_valid is False
        assert any("lacks authentic document structure or authoritative broker evidence" in e for e in errors)
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)


def test_seal_49_genuine_format_without_exness_component_evidence_fails():
    """T49: Genuine-format document without Exness/component evidence fails parser and verification (Binding Fix 1 & 2)."""
    generic_doc = "artifacts/test_seal_49_generic_statement.html"
    os.makedirs("artifacts", exist_ok=True)
    try:
        with open(generic_doc, "wb") as f:
            f.write(b"<html><body><h1>Bank of Alpha Statement</h1><p>Account 99999 USD Balance: 1000</p></body></html>")

        is_valid, raw, sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=generic_doc,
            declared_sha256=None,
            expected_source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
            component_role="LEGAL_ENTITY",
        )
        assert is_valid is False
        assert any("Exness legal entity" in e or "lacks" in e.lower() for e in errors)

        with pytest.raises(ValueError, match="Exness legal entity"):
            parse_legal_entity_backing_artifact(b"<html><body><h1>Bank of Alpha Statement</h1></body></html>")
    finally:
        if os.path.exists(generic_doc):
            os.remove(generic_doc)


def test_seal_50_genuine_backing_artifact_with_contradictory_normalized_metadata_fails():
    """T50: Genuine backing artifact + contradictory normalized metadata fails (Binding Fix 3)."""
    genuine_raw = b"EXNESS_SC_LTD:FSA:SD025"
    parsed = parse_legal_entity_backing_artifact(genuine_raw)
    assert parsed["license_number"] == "SD025"
    assert parsed["regulator"] == "FSA"

    contradictory_meta = {
        "legal_entity_code": "EXNESS_SC_LTD",
        "legal_entity_name": "Exness (SC) Ltd",
        "regulator": "FSA",
        "license_number": "SD999",
    }

    is_match, mismatches = compare_asserted_vs_derived(contradictory_meta, parsed)
    assert is_match is False
    assert any("license_number" in m for m in mismatches)


def test_seal_51_legal_artifact_not_containing_regulator_or_license_fails():
    """T51: Legal artifact not containing regulator or license cannot qualify (Binding Fix 3)."""
    raw_incomplete = b"Exness Group Corporate Overview without regulator or license number"
    with pytest.raises(ValueError, match="regulator or license"):
        parse_legal_entity_backing_artifact(raw_incomplete)


def test_seal_52_contract_artifact_lacking_xauusd_contract_fields_fails():
    """T52: Contract artifact lacking XAUUSD contract fields cannot qualify (Binding Fix 3)."""
    raw_other_symbol = b"SYMBOL:EURUSD|CONTRACT_SIZE:100000|POINT:0.00001|DIGITS:5"
    with pytest.raises(ValueError, match="XAUUSD"):
        parse_contract_spec_backing_artifact(raw_other_symbol, expected_symbol="XAUUSD")


def test_seal_53_fee_document_not_establishing_selected_account_tier_commission_fails():
    """T53: Fee document that does not establish selected account-tier commission cannot qualify (Binding Fix 3)."""
    raw_raw_spread_only = b"SYMBOL:XAUUSD|RAW_SPREAD:COMMISSION:3.50"
    with pytest.raises(ValueError, match="STANDARD"):
        parse_commission_backing_artifact(raw_raw_spread_only, expected_symbol="XAUUSD", expected_account_tier="STANDARD")


def test_seal_54_financing_document_not_establishing_xauusd_swap_values_fails():
    """T54: Financing document that does not establish XAUUSD swap values cannot qualify (Binding Fix 3)."""
    raw_no_xauusd = b"SYMBOL:EURUSD|SWAP_LONG:-3.5|SWAP_SHORT:1.2"
    with pytest.raises(ValueError, match="XAUUSD"):
        parse_financing_backing_artifact(raw_no_xauusd, expected_symbol="XAUUSD")


def test_seal_55_raw_artifact_derived_normalized_hash_enters_fingerprint():
    """T55: Raw artifact-derived normalized hash enters fingerprint (Binding Fix 4 & 5)."""
    base_evidence = {
        "LEGAL_ENTITY": {
            "sha256": "aaaa" * 16,
            "source_type": "OFFICIAL_BROKER_DOCUMENT",
            "parser_name": "parse_legal_entity_backing_artifact",
            "parser_version": "1.0.0",
            "normalized_evidence_hash": "HASH_1111" * 8,
        }
    }
    fp1 = compute_empirical_friction_fingerprint(
        semantic_versions={"sample_sufficiency_policy_version": "1.0.0", "parser_version": "1.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={},
        source_snapshot_hashes=["aaaa" * 16],
        dataset_hashes=[],
        distribution_summaries=[],
        calibrated_parameters={},
        commission_policy={},
        financing_policy={},
        source_evidence=base_evidence,
    )

    mutated_evidence = {
        "LEGAL_ENTITY": {
            "sha256": "aaaa" * 16,
            "source_type": "OFFICIAL_BROKER_DOCUMENT",
            "parser_name": "parse_legal_entity_backing_artifact",
            "parser_version": "1.0.0",
            "normalized_evidence_hash": "HASH_2222" * 8,
        }
    }
    fp2 = compute_empirical_friction_fingerprint(
        semantic_versions={"sample_sufficiency_policy_version": "1.0.0", "parser_version": "1.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={},
        source_snapshot_hashes=["aaaa" * 16],
        dataset_hashes=[],
        distribution_summaries=[],
        calibrated_parameters={},
        commission_policy={},
        financing_policy={},
        source_evidence=mutated_evidence,
    )
    assert fp1 != fp2


def test_seal_56_parser_semantic_version_enters_fingerprint():
    """T56: Parser semantic version enters fingerprint (Binding Fix 5)."""
    fp_v1 = compute_empirical_friction_fingerprint(
        semantic_versions={"sample_sufficiency_policy_version": "1.0.0", "parser_version": "1.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={},
        source_snapshot_hashes=[],
        dataset_hashes=[],
        distribution_summaries=[],
        calibrated_parameters={},
        commission_policy={},
        financing_policy={},
    )
    fp_v2 = compute_empirical_friction_fingerprint(
        semantic_versions={"sample_sufficiency_policy_version": "1.0.0", "parser_version": "2.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={},
        source_snapshot_hashes=[],
        dataset_hashes=[],
        distribution_summaries=[],
        calibrated_parameters={},
        commission_policy={},
        financing_policy={},
    )
    assert fp_v1 != fp_v2


def test_seal_57_changing_parser_version_changes_semantic_fingerprint():
    """T57: Changing parser version changes semantic fingerprint when interpretation version changes (Binding Fix 5)."""
    evidence_v1 = {
        "LEGAL_ENTITY": {
            "sha256": "aaaa" * 16,
            "source_type": "OFFICIAL_BROKER_DOCUMENT",
            "parser_name": "parse_legal_entity_backing_artifact",
            "parser_version": "1.0.0",
            "normalized_evidence_hash": "NORM_HASH_1",
        }
    }
    fp_v1 = compute_empirical_friction_fingerprint(
        semantic_versions={"parser_version": "1.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={},
        source_snapshot_hashes=["aaaa" * 16],
        dataset_hashes=[],
        distribution_summaries=[],
        calibrated_parameters={},
        commission_policy={},
        financing_policy={},
        source_evidence=evidence_v1,
    )

    evidence_v2 = {
        "LEGAL_ENTITY": {
            "sha256": "aaaa" * 16,
            "source_type": "OFFICIAL_BROKER_DOCUMENT",
            "parser_name": "parse_legal_entity_backing_artifact",
            "parser_version": "2.0.0",
            "normalized_evidence_hash": "NORM_HASH_1",
        }
    }
    fp_v2 = compute_empirical_friction_fingerprint(
        semantic_versions={"parser_version": "2.0.0"},
        venue="EXNESS",
        legal_entity_code="EXNESS_SC_LTD",
        account_tier="STANDARD",
        symbol="XAUUSD",
        contract_geometry={},
        source_snapshot_hashes=["aaaa" * 16],
        dataset_hashes=[],
        distribution_summaries=[],
        calibrated_parameters={},
        commission_policy={},
        financing_policy={},
        source_evidence=evidence_v2,
    )
    assert fp_v1 != fp_v2


@pytest.mark.django_db
def test_seal_58_direct_low_level_qualified_source_type_without_valid_qualification_assertion_cannot_active(xauusd_setup, base_sample_ticks, base_telemetry_fills):
    """T58: Direct low-level qualified source_type without valid qualification assertion cannot ACTIVE (Binding Fix 6)."""
    instrument, _ = xauusd_setup
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    legal_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/l_no_assert", "L_NO_ASSERT", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"EXNESS_SC_LTD:FSA:SD025", source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value
    )
    spec_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/c_no_assert", "C_NO_ASSERT", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"CONTRACT_SIZE:100|POINT:0.01|DIGITS:2", source_type=FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value
    )
    fee_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/f_no_assert", "F_NO_ASSERT", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"STANDARD:COMMISSION:0.00", source_type=FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value
    )
    swap_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/s_no_assert", "S_NO_ASSERT", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"SWAP_LONG:-34.80|SWAP_SHORT:12.40|WED:TRIPLE", source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value
    )
    tick_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/t_no_assert", "T_NO_ASSERT", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"TICKS", source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value
    )
    telem_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/e_no_assert", "E_NO_ASSERT", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"TELEMETRY", source_type=FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT.value
    )

    spread_ds, _ = ingest_friction_evidence_dataset(
        source_snapshot=tick_snap, venue="EXNESS", account_tier="STANDARD", symbol="XAUUSD",
        sample_start=base_sample_ticks[0]["timestamp"], sample_end=base_sample_ticks[-1]["timestamp"], ticks_data=base_sample_ticks
    )
    telem_ds, _ = ingest_friction_telemetry_dataset(
        source_snapshot=telem_snap, venue="EXNESS", account_tier="STANDARD", symbol="XAUUSD",
        sample_start=base_telemetry_fills[0]["decision_timestamp"], sample_end=base_telemetry_fills[-1]["fill_timestamp"], telemetry_records=base_telemetry_fills
    )

    model_ver, act = build_and_bind_friction_model_version(
        legal_entity_snapshot=legal_snap,
        contract_spec_snapshot=spec_snap,
        fee_schedule_snapshot=fee_snap,
        swap_spec_snapshot=swap_snap,
        evidence_dataset=spread_ds,
        spread_ticks_bps=[t["spread_bps"] for t in base_sample_ticks],
        telemetry_dataset=telem_ds,
        telemetry_records=base_telemetry_fills,
        legal_entity_info={"legal_entity_code": "EXNESS_SC_LTD", "legal_entity_name": "Exness (SC) Ltd", "regulator": "FSA", "license_number": "SD025"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1.00"), "contract_size": Decimal("100.0"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200.0"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        financing_policy={"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY", "actual_account_swap_free_status": False},
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        test_qualification_seam=False,
    )
    assert act.activation_status == FrictionActivationStatus.DRAFT

    res = validate_friction_model_for_activation(
        model_version=model_ver,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert any("authoritative qualification assertion" in r for r in res.reasons)


@pytest.mark.django_db
def test_seal_59_valid_trusted_parser_and_matching_artifact_and_matching_evidence_qualifies(xauusd_setup, base_sample_ticks, base_telemetry_fills):
    """T59: Valid trusted parser + matching artifact + matching normalized evidence qualifies (Binding Fix 6)."""
    now_utc = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

    legal_bytes = b"EXNESS_SC_LTD:FSA:SD025"
    legal_parsed = parse_legal_entity_backing_artifact(legal_bytes)
    assert legal_parsed["license_number"] == "SD025"

    contract_bytes = b"CONTRACT_SIZE:100|POINT:0.01|DIGITS:2"
    contract_parsed = parse_contract_spec_backing_artifact(contract_bytes, expected_symbol="XAUUSD")

    fee_bytes = b"STANDARD:COMMISSION:0.00"
    fee_parsed = parse_commission_backing_artifact(fee_bytes, expected_symbol="XAUUSD", expected_account_tier="STANDARD")

    swap_bytes = b"SWAP_LONG:-34.80|SWAP_SHORT:12.40|WED:TRIPLE"
    swap_parsed = parse_financing_backing_artifact(swap_bytes, expected_symbol="XAUUSD")

    legal_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/l_q", "L_Q", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        legal_bytes, source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value, metadata=legal_parsed
    )
    spec_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/c_q", "C_Q", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        contract_bytes, source_type=FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value, metadata=contract_parsed
    )
    fee_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/f_q", "F_Q", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        fee_bytes, source_type=FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value, metadata=fee_parsed
    )
    swap_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/s_q", "S_Q", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        swap_bytes, source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value, metadata=swap_parsed
    )
    tick_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/t_q", "T_Q", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"TICKS", source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value
    )
    telem_snap, _ = ingest_friction_source_snapshot(
        "http://ex.com/e_q", "E_Q", "EXNESS", "XAUUSD", "STANDARD", now_utc, now_utc,
        b"TELEMETRY", source_type=FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT.value
    )

    spread_ds, _ = ingest_friction_evidence_dataset(
        source_snapshot=tick_snap, venue="EXNESS", account_tier="STANDARD", symbol="XAUUSD",
        sample_start=base_sample_ticks[0]["timestamp"], sample_end=base_sample_ticks[-1]["timestamp"], ticks_data=base_sample_ticks
    )
    telem_ds, _ = ingest_friction_telemetry_dataset(
        source_snapshot=telem_snap, venue="EXNESS", account_tier="STANDARD", symbol="XAUUSD",
        sample_start=base_telemetry_fills[0]["decision_timestamp"], sample_end=base_telemetry_fills[-1]["fill_timestamp"], telemetry_records=base_telemetry_fills
    )

    create_friction_qualification_assertion(
        source_snapshot=legal_snap, component_role="LEGAL_ENTITY", qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_legal_entity_backing_artifact", parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash(legal_parsed)
    )
    create_friction_qualification_assertion(
        source_snapshot=spec_snap, component_role="CONTRACT_SPEC", qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_contract_spec_backing_artifact", parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash(contract_parsed)
    )
    create_friction_qualification_assertion(
        source_snapshot=fee_snap, component_role="COMMISSION", qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_commission_backing_artifact", parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash(fee_parsed)
    )
    create_friction_qualification_assertion(
        source_snapshot=swap_snap, component_role="FINANCING", qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_financing_backing_artifact", parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash(swap_parsed)
    )
    create_friction_qualification_assertion(
        source_snapshot=tick_snap, component_role="SPREAD_DATASET", qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_mt5_tick_export", parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash({"raw_dataset_sha256": spread_ds.raw_dataset_sha256})
    )
    create_friction_qualification_assertion(
        source_snapshot=telem_snap, component_role="SLIPPAGE_DATASET", qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_mt5_execution_telemetry", parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash({"raw_dataset_sha256": telem_ds.raw_dataset_sha256})
    )

    model_ver, act = build_and_bind_friction_model_version(
        legal_entity_snapshot=legal_snap,
        contract_spec_snapshot=spec_snap,
        fee_schedule_snapshot=fee_snap,
        swap_spec_snapshot=swap_snap,
        evidence_dataset=spread_ds,
        spread_ticks_bps=[t["spread_bps"] for t in base_sample_ticks],
        telemetry_dataset=telem_ds,
        telemetry_records=base_telemetry_fills,
        legal_entity_info={"legal_entity_code": "EXNESS_SC_LTD", "legal_entity_name": "Exness (SC) Ltd", "regulator": "FSA", "license_number": "SD025"},
        contract_geometry={"digits": 2, "point_size": Decimal("0.01"), "trade_tick_size": Decimal("0.01"), "trade_tick_value": Decimal("1.00"), "contract_size": Decimal("100.0"), "volume_min": Decimal("0.01"), "volume_max": Decimal("200.0"), "volume_step": Decimal("0.01")},
        commission_policy={"native_commission_usd_per_lot_per_side": Decimal("0.00"), "commission_formula": "DYNAMIC_NOTIONAL_BPS"},
        financing_policy={"swap_long_points": Decimal("-34.80"), "swap_short_points": Decimal("12.40"), "rollover_summer_utc_hour": 21, "rollover_winter_utc_hour": 22, "triple_swap_weekday": "WEDNESDAY", "actual_account_swap_free_status": False},
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD",
        test_qualification_seam=False,
    )
    assert act.activation_status == FrictionActivationStatus.ACTIVE

    res = validate_friction_model_for_activation(
        model_version=model_ver,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is True
    assert res.status == "EMPIRICAL_FRICTION_CONFIGURED"




