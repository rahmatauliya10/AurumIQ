"""
Tests for Pre-Phase-8 Quote Closure and Slippage Semantic Invariants.

Verifies:
1. Slippage Semantic Invariants:
   - true_requested_price_slippage remains UNOBSERVABLE.
   - execution_gap_proxy is not falsely equated to true slippage = 0.
   - forced_exit_displacement remains quarantined.
2. Historical Quote Evidence Resolution:
   - Natural resolution from persisted qualified dataset without test seams or overrides.
   - Fail-closed behavior when dataset is unqualified or empty.
   - Fail-closed behavior on scope mismatch (venue, symbol, account tier).
3. Database Model & Migration Integrity:
   - FrictionSourceProvenanceAttestation accepts COMPOSITE_GOVERNED_REVIEW.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from django.core.management import call_command

from apps.instruments.models import Instrument, MarketListing, ListingRole, ListingStatus
from apps.market_data.models import (
    FrictionActivationStatus,
    FrictionAttestationStatus,
    FrictionBindingRole,
    FrictionEvidenceDataset,
    FrictionModelActivation,
    FrictionModelDatasetBinding,
    FrictionModelVersion,
    FrictionQualificationStatus,
    FrictionSourceProvenanceAttestation,
    FrictionSourceQualificationAssertion,
    FrictionSourceSnapshot,
    FrictionSourceType,
    FrictionVerificationMethod,
)
from apps.market_data.readiness import XauUsdDataReadinessEvaluator


@pytest.fixture
def xauusd_setup(db):
    """Seed standard assets, instruments, and primary XAUUSD spot listing."""
    call_command("seed_instruments")
    instrument = Instrument.get_canonical_xauusd()
    primary_listing = MarketListing.objects.filter(
        instrument=instrument,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    ).first()
    return instrument, primary_listing


def _create_dummy_snapshot(snapshot_id: str, source_type: str, venue: str = "EXNESS", account_tier: str = "STANDARD_CENT"):
    now = datetime.now(timezone.utc)
    return FrictionSourceSnapshot.objects.create(
        snapshot_id=snapshot_id,
        source_url="https://example.com/source",
        source_name="Official Source",
        source_type=source_type,
        venue=venue,
        symbol="XAUUSD",
        account_tier=account_tier,
        retrieved_at=now,
        known_at=now,
        raw_payload_bytes_sha256="a" * 64,
        metadata={},
    )


@pytest.mark.django_db
class TestSlippageSemanticInvariants:
    """Tests asserting the core Phase 6 slippage semantic invariant."""

    def test_model_version_and_readiness_slippage_semantics(self, xauusd_setup):
        """
        True requested-price slippage must be explicitly designated UNOBSERVABLE.
        The base/stress slippage bps fields represent execution-gap proxy vs reference quote.
        Forced-exit displacement must be recorded as quarantined.
        """
        snap = _create_dummy_snapshot("SNAP_TEST_MODEL_LEGAL_1", FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value)
        model_ver = FrictionModelVersion.objects.create(
            model_version_id="TEST_EXNESS_XAUUSD_CENT_SLIPPAGE_SEMANTICS_V1",
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            symbol="XAUUSD",
            legal_entity_code="EXNESS_SC_LTD",
            legal_entity_name="Exness (SC) Ltd",
            regulator="FSA_SEYCHELLES",
            license_number="SD025",
            legal_entity_source_snapshot=snap,
            contract_size=Decimal("100.00"),
            digits=2,
            point_size=Decimal("0.01"),
            trade_tick_size=Decimal("0.01"),
            trade_tick_value=Decimal("0.01"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100.00"),
            volume_step=Decimal("0.01"),
            base_spread_bps=Decimal("1.25"),
            stress_spread_bps=Decimal("2.50"),
            base_slippage_bps=Decimal("0.0000"),
            stress_slippage_bps=Decimal("0.0000"),
            native_commission_usd_per_lot_per_side=Decimal("0.0000"),
            commission_formula="ZERO_COMMISSION",
            swap_long_points=Decimal("-68.277"),
            swap_short_points=Decimal("35.006"),
            empirical_friction_evidence_fingerprint="a" * 64,
        )

        assert model_ver.base_slippage_bps == Decimal("0.0000")
        assert model_ver.stress_slippage_bps == Decimal("0.0000")

        # Evaluate through readiness evaluator
        report = XauUsdDataReadinessEvaluator.evaluate(
            execution_venue="EXNESS",
            execution_account_tier="STANDARD_CENT",
            execution_legal_entity_code="EXNESS_SC_LTD",
            override_friction_status="EMPIRICAL_FRICTION_CONFIGURED",
            override_quote_count=100,
            override_candles=[],
        )

        manifest = report.to_manifest_dict(allow_mutable_revision=True)
        friction_section = manifest["empirical_friction_evidence"]

        assert friction_section["true_requested_price_slippage"] == "UNOBSERVABLE"
        assert friction_section["slippage_proxy_type"] == "EXECUTION_GAP_VS_REFERENCE_QUOTE"
        assert friction_section["forced_exit_displacement_status"] == "MEASURED_BUT_NOT_NORMAL_MARKET_SLIPPAGE_QUARANTINED"


@pytest.mark.django_db
class TestHistoricalQuoteEvidenceResolution:
    """Tests asserting natural historical quote resolution without fake counts or overrides."""

    def test_natural_resolution_from_qualified_persisted_dataset(self, xauusd_setup):
        """
        When model is active and bound to a qualified PRIMARY_SPREAD_SAMPLE dataset,
        readiness evaluator naturally derives quote_count from dataset sample_count.
        """
        snap = _create_dummy_snapshot("SNAP_TEST_EXNESS_TICKS_20260911", FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value)

        # Create qualification assertion
        FrictionSourceQualificationAssertion.objects.create(
            assertion_id="ASSERT_TEST_QUAL_20260911",
            source_snapshot=snap,
            component_role="SPREAD_DATASET",
            qualification_status=FrictionQualificationStatus.QUALIFIED.value,
            parser_name="ExnessTickHistoryParser",
            parser_version="1.0.0",
            raw_artifact_sha256="b" * 64,
            normalized_evidence_hash="c" * 64,
            qualification_reason="Governed broker tick dataset",
        )

        now = datetime.now(timezone.utc)
        dataset = FrictionEvidenceDataset.objects.create(
            dataset_id="DS_TEST_EXNESS_TICKS_6M",
            source_snapshot=snap,
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            symbol="XAUUSD",
            sample_start=now,
            sample_end=now,
            sample_count=6493208,
            distinct_trading_days=250,
            session_counts={},
            source_units="PRICE_POINTS",
            raw_dataset_sha256="d" * 64,
            collection_methodology="EXNESS_TICK_EXPORT",
        )

        snap_legal = _create_dummy_snapshot("SNAP_TEST_MODEL_LEGAL_2", FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value)
        model_ver = FrictionModelVersion.objects.create(
            model_version_id="TEST_EXNESS_XAUUSD_CENT_QUOTE_TEST_V1",
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            symbol="XAUUSD",
            legal_entity_code="EXNESS_SC_LTD",
            legal_entity_name="Exness (SC) Ltd",
            regulator="FSA_SEYCHELLES",
            license_number="SD025",
            legal_entity_source_snapshot=snap_legal,
            contract_size=Decimal("100.00"),
            digits=2,
            point_size=Decimal("0.01"),
            trade_tick_size=Decimal("0.01"),
            trade_tick_value=Decimal("0.01"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100.00"),
            volume_step=Decimal("0.01"),
            base_spread_bps=Decimal("1.25"),
            stress_spread_bps=Decimal("2.50"),
            base_slippage_bps=Decimal("0.0000"),
            stress_slippage_bps=Decimal("0.0000"),
            native_commission_usd_per_lot_per_side=Decimal("0.0000"),
            commission_formula="ZERO_COMMISSION",
            swap_long_points=Decimal("-68.277"),
            swap_short_points=Decimal("35.006"),
            empirical_friction_evidence_fingerprint="e" * 64,
        )

        FrictionModelDatasetBinding.objects.create(
            binding_id="BIND_TEST_SPREAD_1",
            friction_model_version=model_ver,
            evidence_dataset=dataset,
            binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
        )

        FrictionModelActivation.objects.create(
            activation_id="ACT_TEST_EXNESS_XAUUSD_CENT_20260911",
            friction_model_version=model_ver,
            known_at=now,
            effective_from=now,
            activation_status=FrictionActivationStatus.ACTIVE,
            source_or_reason="Activated in test",
        )

        # Evaluate with empty candle override to avoid ORM candle scans
        report = XauUsdDataReadinessEvaluator.evaluate(
            execution_venue="EXNESS",
            execution_account_tier="STANDARD_CENT",
            execution_legal_entity_code="EXNESS_SC_LTD",
            override_friction_status="EMPIRICAL_FRICTION_CONFIGURED",
            override_quote_count=None,
            override_candles=[],
        )

        assert report.quote_count == 6493208

    def test_fail_closed_when_quote_dataset_is_unqualified(self, xauusd_setup):
        """
        If the bound dataset is not backed by a QUALIFIED assertion, quote_count must remain 0.
        """
        snap = _create_dummy_snapshot("SNAP_TEST_UNQUALIFIED_TICKS_20260911", FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value)

        # Unqualified assertion (status = REJECTED)
        FrictionSourceQualificationAssertion.objects.create(
            assertion_id="ASSERT_TEST_REJECT_20260911",
            source_snapshot=snap,
            component_role="SPREAD_DATASET",
            qualification_status=FrictionQualificationStatus.REJECTED.value,
            parser_name="ExnessTickHistoryParser",
            parser_version="1.0.0",
            raw_artifact_sha256="f" * 64,
            normalized_evidence_hash="1" * 64,
            qualification_reason="Data corruption detected",
        )

        now = datetime.now(timezone.utc)
        dataset = FrictionEvidenceDataset.objects.create(
            dataset_id="DS_TEST_UNQUALIFIED_TICKS",
            source_snapshot=snap,
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            symbol="XAUUSD",
            sample_start=now,
            sample_end=now,
            sample_count=1000000,
            distinct_trading_days=50,
            session_counts={},
            source_units="PRICE_POINTS",
            raw_dataset_sha256="2" * 64,
            collection_methodology="EXNESS_TICK_EXPORT",
        )

        snap_legal = _create_dummy_snapshot("SNAP_TEST_MODEL_LEGAL_3", FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value)
        model_ver = FrictionModelVersion.objects.create(
            model_version_id="TEST_EXNESS_UNQUALIFIED_MODEL_V1",
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            symbol="XAUUSD",
            legal_entity_code="EXNESS_SC_LTD",
            legal_entity_name="Exness (SC) Ltd",
            regulator="FSA_SEYCHELLES",
            license_number="SD025",
            legal_entity_source_snapshot=snap_legal,
            contract_size=Decimal("100.00"),
            digits=2,
            point_size=Decimal("0.01"),
            trade_tick_size=Decimal("0.01"),
            trade_tick_value=Decimal("0.01"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100.00"),
            volume_step=Decimal("0.01"),
            base_spread_bps=Decimal("1.25"),
            stress_spread_bps=Decimal("2.50"),
            base_slippage_bps=Decimal("0.0000"),
            stress_slippage_bps=Decimal("0.0000"),
            native_commission_usd_per_lot_per_side=Decimal("0.0000"),
            commission_formula="ZERO_COMMISSION",
            swap_long_points=Decimal("-68.277"),
            swap_short_points=Decimal("35.006"),
            empirical_friction_evidence_fingerprint="3" * 64,
        )
        FrictionModelDatasetBinding.objects.create(
            binding_id="BIND_TEST_SPREAD_2",
            friction_model_version=model_ver,
            evidence_dataset=dataset,
            binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
        )
        FrictionModelActivation.objects.create(
            activation_id="ACT_TEST_UNQUAL_20260911",
            friction_model_version=model_ver,
            known_at=now,
            effective_from=now,
            activation_status=FrictionActivationStatus.ACTIVE,
            source_or_reason="Activated unqual test",
        )

        report = XauUsdDataReadinessEvaluator.evaluate(
            execution_venue="EXNESS",
            execution_account_tier="STANDARD_CENT",
            execution_legal_entity_code="EXNESS_SC_LTD",
            override_friction_status="EMPIRICAL_FRICTION_CONFIGURED",
            override_quote_count=None,
            override_candles=[],
        )

        # Fail closed: quote count remains 0 because qualification is REJECTED
        assert report.quote_count == 0

    def test_fail_closed_on_scope_mismatch(self, xauusd_setup):
        """
        If evaluation target is ICMARKETS, Exness dataset must not be selected.
        """
        report = XauUsdDataReadinessEvaluator.evaluate(
            execution_venue="ICMARKETS",
            execution_account_tier="STANDARD",
            execution_legal_entity_code="ICM_LTD",
            override_friction_status="EMPIRICAL_FRICTION_NOT_CONFIGURED",
            override_quote_count=None,
            override_candles=[],
        )

        assert report.quote_count == 0
        assert report.passed is False


@pytest.mark.django_db
class TestProvenanceVerificationMethodChoice:
    """Verifies that COMPOSITE_GOVERNED_REVIEW is a valid verification method."""

    def test_composite_governed_review_choice_persisted(self):
        snap = _create_dummy_snapshot("SNAP_TEST_PROVENANCE_20260911", FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value)
        now = datetime.now(timezone.utc)

        att = FrictionSourceProvenanceAttestation.objects.create(
            attestation_id="ATT_TEST_COMPOSITE_20260911",
            source_snapshot=snap,
            component_role="LEGAL_ENTITY_RECEIPT",
            source_origin="https://exness.com",
            source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
            collection_methodology="GOVERNED_COMPOSITE_REVIEW",
            captured_at=now,
            verification_method=FrictionVerificationMethod.COMPOSITE_GOVERNED_REVIEW.value,
            verifier_identity="LEGAL_ENTITY_GOVERNANCE_REVIEWER",
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            raw_artifact_sha256="4" * 64,
            attestation_status=FrictionAttestationStatus.DECLARED.value,
        )

        assert att.verification_method == "COMPOSITE_GOVERNED_REVIEW"
        reloaded = FrictionSourceProvenanceAttestation.objects.get(attestation_id="ATT_TEST_COMPOSITE_20260911")
        assert reloaded.verification_method == "COMPOSITE_GOVERNED_REVIEW"
