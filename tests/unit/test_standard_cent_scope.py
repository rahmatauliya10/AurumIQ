"""Hostile regression test suite for Standard Cent execution scope extension.

Verifies all governance directives for EXNESS / STANDARD_CENT scope:
1. STANDARD still works.
2. RAW_SPREAD still works.
3. STANDARD_CENT is accepted as a distinct tier.
4. STANDARD evidence cannot qualify STANDARD_CENT.
5. STANDARD_CENT evidence cannot qualify STANDARD.
6. Substring "STANDARD" cannot satisfy STANDARD_CENT (anti-substring collision).
7. Hostile broker symbol rejection: GOLDc, GOLD, XAUUSD, XAUUSDm, EURUSDc, BTCUSDc rejected for Standard Cent.
8. Authoritative expected broker symbol for Exness Standard Cent is strictly XAUUSDc.
9. Canonical XAUUSD and broker XAUUSDc remain distinct; analytical feed is unchanged.
10. Account-number/private fields are not required and not persisted.
11. Cent-lot safety: Standard Cent does not inherit Standard lot geometry; missing geometry fails closed.
12. USC currency semantics: 1 USD = 100 USC denomination conversion does NOT substitute for geometry.
13. Readiness remains strictly closed: CANDLES_READY_EMPIRICAL_FRICTION_MISSING, passed=False, weight=0.0, WAIT.
14. Raw artifact bytes enforcement: All parsers reject non-bytes inputs with TypeError.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import io
import json
import os
import tempfile
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.instruments.models import (
    Instrument,
    MarketListing,
    ListingRole,
    ListingStatus,
)
from apps.market_data.models import (
    MarketCandle,
    FrictionSourceSnapshot,
    FrictionEvidenceDataset,
    FrictionModelVersion,
    FrictionSourceType,
    FrictionVerificationMethod,
    FrictionAttestationStatus,
    FrictionSourceProvenanceAttestation,
)
from apps.market_data.friction.artifact_parsers import (
    KNOWN_ACCOUNT_TIERS,
    normalize_account_tier,
    validate_account_tier,
    _matches_expected_symbol,
    parse_legal_entity_backing_artifact,
    parse_contract_spec_backing_artifact,
    parse_commission_backing_artifact,
    parse_financing_backing_artifact,
)
from apps.market_data.friction.commission import (
    ACCOUNT_CURRENCY_USD,
    ACCOUNT_CURRENCY_USC,
    CENT_RATIO_USC_PER_USD,
    get_account_currency_for_tier,
    convert_currency,
)
from apps.market_data.friction.tick_parser import parse_mt5_tick_export
from apps.market_data.friction.slippage_parser import parse_mt5_execution_telemetry
from apps.market_data.friction.ingestion import verify_authoritative_backing_artifact
from apps.market_data.friction.validation import validate_source_qualification_assertion
from apps.market_data.readiness import XauUsdDataReadinessEvaluator


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
    for i in range(count):
        t_open = base_time + i * delta
        t_close = t_open + delta
        MarketCandle.objects.create(
            instrument=instrument,
            source=source,
            timeframe=tf,
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2000.00"),
            high=Decimal("2005.00"),
            low=Decimal("1995.00"),
            close=Decimal("2001.00"),
            volume=Decimal("100"),
        )


# =============================================================================
# 1. TIER ENUMERATION & NORMALIZATION TESTS (Directives 1, 2, 3, 5)
# =============================================================================

class TestAccountTierEnumerationAndNormalization:
    """Proves STANDARD, RAW_SPREAD, and STANDARD_CENT are supported with exact normalization."""

    def test_directive_1_standard_in_known_tiers(self):
        assert "STANDARD" in KNOWN_ACCOUNT_TIERS

    def test_directive_2_raw_spread_in_known_tiers(self):
        assert "RAW_SPREAD" in KNOWN_ACCOUNT_TIERS

    def test_directive_3_standard_cent_in_known_tiers(self):
        assert "STANDARD_CENT" in KNOWN_ACCOUNT_TIERS

    @pytest.mark.parametrize(
        "raw_input,expected_tier",
        [
            ("STANDARD", "STANDARD"),
            ("standard", "STANDARD"),
            ("Standard", "STANDARD"),
            ("RAW_SPREAD", "RAW_SPREAD"),
            ("raw_spread", "RAW_SPREAD"),
            ("RAW-SPREAD", "RAW_SPREAD"),
            ("RAW SPREAD", "RAW_SPREAD"),
            ("STANDARD_CENT", "STANDARD_CENT"),
            ("standard_cent", "STANDARD_CENT"),
            ("Standard_Cent", "STANDARD_CENT"),
            ("STANDARD-CENT", "STANDARD_CENT"),
            ("STANDARD CENT", "STANDARD_CENT"),
            ("standard cent", "STANDARD_CENT"),
        ],
    )
    def test_normalize_account_tier_valid_inputs(self, raw_input, expected_tier):
        assert normalize_account_tier(raw_input) == expected_tier
        assert validate_account_tier(raw_input) == expected_tier

    @pytest.mark.parametrize(
        "invalid_tier",
        [
            "PRO",
            "ZERO",
            "CENT",
            "STANDARD_PRO",
            "STANDARD_ZERO",
            "VIP",
            "DEMO",
            "",
            None,
        ],
    )
    def test_validate_account_tier_invalid_inputs_rejected(self, invalid_tier):
        with pytest.raises(ValueError, match="Unsupported or unverified account tier"):
            validate_account_tier(invalid_tier)

    def test_substring_confusion_tier_isolation(self):
        """Directive 6: 'STANDARD' is a substring of 'STANDARD_CENT', but they must never match."""
        tier_std = normalize_account_tier("STANDARD")
        tier_cent = normalize_account_tier("STANDARD_CENT")
        assert tier_std != tier_cent
        assert tier_std == "STANDARD"
        assert tier_cent == "STANDARD_CENT"


# =============================================================================
# 2. ACCOUNT CURRENCY SEMANTICS TESTS (USC vs USD)
# =============================================================================

class TestAccountCurrencySemantics:
    """Proves cent-account currency (USC) handling decoupled from market quote currency (USD)."""

    def test_currency_mapping_per_tier(self):
        assert get_account_currency_for_tier("STANDARD") == ACCOUNT_CURRENCY_USD
        assert get_account_currency_for_tier("RAW_SPREAD") == ACCOUNT_CURRENCY_USD
        assert get_account_currency_for_tier("STANDARD_CENT") == ACCOUNT_CURRENCY_USC

    def test_convert_currency_usd_to_usc(self):
        amount_usd = Decimal("1.50")
        amount_usc = convert_currency(amount_usd, ACCOUNT_CURRENCY_USD, ACCOUNT_CURRENCY_USC)
        assert amount_usc == Decimal("150.00")
        assert CENT_RATIO_USC_PER_USD == Decimal("100")

    def test_convert_currency_usc_to_usd(self):
        amount_usc = Decimal("150.00")
        amount_usd = convert_currency(amount_usc, ACCOUNT_CURRENCY_USC, ACCOUNT_CURRENCY_USD)
        assert amount_usd == Decimal("1.5000")

    def test_convert_currency_identity(self):
        assert convert_currency(Decimal("42.50"), "USD", "USD") == Decimal("42.50")
        assert convert_currency(Decimal("4250"), "USC", "USC") == Decimal("4250")

    def test_convert_currency_unknown_fails_closed(self):
        with pytest.raises(ValueError, match="Unsupported.*conversion"):
            convert_currency(Decimal("100"), "EUR", "USD")

        with pytest.raises(ValueError, match="Unsupported.*conversion"):
            convert_currency(Decimal("100"), "USD", "JPY")


# =============================================================================
# 3. EXACT BROKER SYMBOL HARDENING & HOSTILE REJECTION TESTS
# =============================================================================

class TestExactBrokerSymbolHardening:
    """Directive: Exact Exness broker symbol hardening for STANDARD_CENT (reject GOLDc)."""

    def test_exness_standard_cent_exact_symbol_accepted(self):
        """Authoritative Exness broker symbol XAUUSDc is strictly accepted for STANDARD_CENT."""
        assert _matches_expected_symbol("XAUUSDc", "XAUUSD", expected_account_tier="STANDARD_CENT") is True
        assert _matches_expected_symbol("XAUUSDC", "XAUUSD", expected_account_tier="STANDARD_CENT") is True
        assert _matches_expected_symbol("XAUUSDc", "XAUUSD", expected_broker_symbol="XAUUSDc", expected_account_tier="STANDARD_CENT") is True

    @pytest.mark.parametrize(
        "hostile_symbol",
        [
            "GOLDc",
            "GOLDC",
            "GOLD",
            "gold",
            "XAUUSD",
            "xauusd",
            "XAU/USD",
            "XAUUSDm",
            "XAUUSDM",
            "EURUSDc",
            "BTCUSDc",
            "GBPUSD",
            "USOILc",
        ],
    )
    def test_hostile_lookalikes_rejected_for_standard_cent(self, hostile_symbol):
        """FUZZY_GOLDC_ACCEPTED = false: GOLDc, GOLD, XAUUSD, XAUUSDm, and other lookalikes are REJECTED."""
        assert _matches_expected_symbol(
            hostile_symbol,
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        ) is False

    def test_standard_account_symbol_matching(self):
        """STANDARD tier accepts canonical XAUUSD, XAU/USD, GOLD, but strictly rejects cent symbols."""
        assert _matches_expected_symbol("XAUUSD", "XAUUSD", expected_account_tier="STANDARD") is True
        assert _matches_expected_symbol("XAU/USD", "XAUUSD", expected_account_tier="STANDARD") is True
        assert _matches_expected_symbol("GOLD", "XAUUSD", expected_account_tier="STANDARD") is True

        # Cent and suffix variants are strictly rejected for STANDARD tier
        assert _matches_expected_symbol("XAUUSDc", "XAUUSD", expected_account_tier="STANDARD") is False
        assert _matches_expected_symbol("GOLDc", "XAUUSD", expected_account_tier="STANDARD") is False
        assert _matches_expected_symbol("XAUUSDm", "XAUUSD", expected_account_tier="STANDARD") is False

    def test_custom_expected_broker_symbol_supported_when_explicit(self):
        """If a future authoritative broker symbol is explicitly bound, it matches exactly."""
        assert _matches_expected_symbol(
            "XAUUSD_CENT",
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSD_CENT",
            expected_account_tier="STANDARD_CENT",
        ) is True
        assert _matches_expected_symbol(
            "XAUUSDc",
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSD_CENT",
            expected_account_tier="STANDARD_CENT",
        ) is False


# =============================================================================
# 4. RAW ARTIFACT BYTES ENFORCEMENT & TYPE VALIDATION
# =============================================================================

class TestRawArtifactBytesEnforcement:
    """Directive 3: Review broad parser changes — enforce raw artifact bytes requirements."""

    @pytest.mark.parametrize("bad_input", ["string_content", {"dict": "value"}, 12345, [1, 2, 3]])
    def test_legal_entity_parser_rejects_non_bytes(self, bad_input):
        with pytest.raises(TypeError, match="Expected raw artifact bytes"):
            parse_legal_entity_backing_artifact(bad_input)

    @pytest.mark.parametrize("bad_input", ["string_content", {"dict": "value"}, 12345, [1, 2, 3]])
    def test_contract_spec_parser_rejects_non_bytes(self, bad_input):
        with pytest.raises(TypeError, match="Expected raw artifact bytes"):
            parse_contract_spec_backing_artifact(bad_input)

    @pytest.mark.parametrize("bad_input", ["string_content", {"dict": "value"}, 12345, [1, 2, 3]])
    def test_commission_parser_rejects_non_bytes(self, bad_input):
        with pytest.raises(TypeError, match="Expected raw artifact bytes"):
            parse_commission_backing_artifact(bad_input)

    @pytest.mark.parametrize("bad_input", ["string_content", {"dict": "value"}, 12345, [1, 2, 3]])
    def test_financing_parser_rejects_non_bytes(self, bad_input):
        with pytest.raises(TypeError, match="Expected raw artifact bytes"):
            parse_financing_backing_artifact(bad_input)

    @pytest.mark.parametrize("bad_input", ["string_content", {"dict": "value"}, 12345, [1, 2, 3]])
    def test_tick_parser_rejects_non_bytes(self, bad_input):
        with pytest.raises(TypeError, match="Expected raw artifact bytes"):
            parse_mt5_tick_export(bad_input)

    @pytest.mark.parametrize("bad_input", ["string_content", {"dict": "value"}, 12345, [1, 2, 3]])
    def test_telemetry_parser_rejects_non_bytes(self, bad_input):
        with pytest.raises(TypeError, match="Expected raw artifact bytes"):
            parse_mt5_execution_telemetry(bad_input)


# =============================================================================
# 5. ARTIFACT PARSERS TIER & SYMBOL ISOLATION (Directives 4, 5, 6, 10)
# =============================================================================

class TestArtifactParsersTierIsolation:
    """Proves parsers enforce strict tier and broker symbol isolation without substring bleed."""

    def test_contract_spec_standard_cent_accepted(self):
        artifact = {
            "symbol": "XAUUSDc",
            "account_tier": "STANDARD_CENT",
            "digits": 2,
            "point_size": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "contract_size": 100,
            "volume_min": 0.01,
            "volume_max": 200.0,
            "volume_step": 0.01,
        }
        raw_bytes = json.dumps(artifact).encode("utf-8")
        res = parse_contract_spec_backing_artifact(
            raw_bytes,
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )
        assert res["contract_size"] == Decimal("100")
        assert res["account_tier"] == "STANDARD_CENT"
        assert res["broker_symbol"] == "XAUUSDc"

    def test_contract_spec_rejects_goldc_for_standard_cent(self):
        """Hostile test: contract spec with GOLDc must be rejected for EXNESS STANDARD_CENT."""
        artifact = {
            "symbol": "GOLDc",
            "account_tier": "STANDARD_CENT",
            "digits": 2,
            "point_size": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "contract_size": 100,
            "volume_min": 0.01,
            "volume_max": 200.0,
            "volume_step": 0.01,
        }
        raw_bytes = json.dumps(artifact).encode("utf-8")
        with pytest.raises(ValueError, match="CONTRACT_SPEC_EVIDENCE_MISSING"):
            parse_contract_spec_backing_artifact(
                raw_bytes,
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_contract_spec_rejects_canonical_xauusd_for_standard_cent(self):
        """Canonical XAUUSD cannot silently satisfy broker XAUUSDc for STANDARD_CENT."""
        artifact = {
            "symbol": "XAUUSD",
            "account_tier": "STANDARD_CENT",
            "digits": 2,
            "point_size": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "contract_size": 100,
            "volume_min": 0.01,
            "volume_max": 200.0,
            "volume_step": 0.01,
        }
        raw_bytes = json.dumps(artifact).encode("utf-8")
        with pytest.raises(ValueError, match="CONTRACT_SPEC_EVIDENCE_MISSING"):
            parse_contract_spec_backing_artifact(
                raw_bytes,
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_directive_4_standard_evidence_cannot_qualify_standard_cent(self):
        """STANDARD artifact cannot qualify STANDARD_CENT."""
        artifact = {
            "symbol": "XAUUSD",
            "account_tier": "STANDARD",
            "digits": 2,
            "point_size": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "contract_size": 100,
            "volume_min": 0.01,
            "volume_max": 200.0,
            "volume_step": 0.01,
        }
        raw_bytes = json.dumps(artifact).encode("utf-8")
        with pytest.raises(ValueError, match="(account tier mismatch|symbol.*does not match|incompatible)"):
            parse_contract_spec_backing_artifact(
                raw_bytes,
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_directive_5_standard_cent_evidence_cannot_qualify_standard(self):
        """STANDARD_CENT artifact cannot qualify STANDARD."""
        artifact = {
            "symbol": "XAUUSDc",
            "account_tier": "STANDARD_CENT",
            "digits": 2,
            "point_size": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "contract_size": 100,
            "volume_min": 0.01,
            "volume_max": 200.0,
            "volume_step": 0.01,
        }
        raw_bytes = json.dumps(artifact).encode("utf-8")
        with pytest.raises(ValueError, match="(account tier mismatch|symbol.*does not match|incompatible)"):
            parse_contract_spec_backing_artifact(
                raw_bytes,
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSD",
                expected_account_tier="STANDARD",
            )

    def test_directive_6_substring_standard_fails_for_standard_cent(self):
        """Directive 6: Text mentioning 'STANDARD' alone must NOT satisfy STANDARD_CENT."""
        raw_text = (
            "Exness Contract Specifications\n"
            "Instrument: XAUUSDc\n"
            "Account Type: STANDARD\n"  # Only STANDARD, not STANDARD_CENT
            "Digits: 2\n"
            "Point: 0.01\n"
            "Tick Size: 0.01\n"
            "Tick Value: 0.01\n"
            "Contract Size: 100\n"
            "Minimum Volume: 0.01\n"
            "Maximum Volume: 200.0\n"
            "Volume Step: 0.01\n"
        )
        with pytest.raises(ValueError, match="account tier mismatch"):
            parse_contract_spec_backing_artifact(
                raw_text.encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_contract_spec_raw_text_standard_cent_accepted(self):
        raw_text = (
            "Exness Contract Specifications\n"
            "Instrument: XAUUSDc\n"
            "Account Type: Standard Cent\n"
            "Digits: 2\n"
            "Point: 0.01\n"
            "Tick Size: 0.01\n"
            "Tick Value: 0.01\n"
            "Contract Size: 100\n"
            "Minimum Volume: 0.01\n"
            "Maximum Volume: 200.0\n"
            "Volume Step: 0.01\n"
        )
        res = parse_contract_spec_backing_artifact(
            raw_text.encode("utf-8"),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )
        assert res["contract_size"] == Decimal("100")
        assert res["account_tier"] == "STANDARD_CENT"

    def test_directive_10_missing_standard_cent_geometry_fails_closed(self):
        """Directive 10: Missing geometry fields fail closed; no fallback or inheritance from Standard."""
        incomplete_artifact = {
            "symbol": "XAUUSDc",
            "account_tier": "STANDARD_CENT",
            # missing contract_size, volume_min, volume_max, volume_step
            "digits": 2,
            "point_size": 0.01,
        }
        with pytest.raises(ValueError, match="CONTRACT_SPEC_EVIDENCE_MISSING"):
            parse_contract_spec_backing_artifact(
                json.dumps(incomplete_artifact).encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_standard_cent_does_not_inherit_standard_geometry(self):
        """Cent-Lot Safety: Standard Cent does NOT inherit Standard lot geometry."""
        standard_artifact = {
            "symbol": "XAUUSD",
            "account_tier": "STANDARD",
            "digits": 2,
            "point_size": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "contract_size": 100,
            "volume_min": 0.01,
            "volume_max": 200.0,
            "volume_step": 0.01,
        }
        # Attempting to parse Standard geometry for Standard Cent must fail closed
        with pytest.raises(ValueError, match="(account tier mismatch|symbol.*does not match|incompatible)"):
            parse_contract_spec_backing_artifact(
                json.dumps(standard_artifact).encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_commission_parser_standard_cent_zero_commission(self):
        """Exness Standard Cent has 0.0 commission per lot."""
        artifact = {
            "symbol": "XAUUSDc",
            "account_tier": "STANDARD_CENT",
            "commission_per_lot_usd": 0.0,
        }
        res = parse_commission_backing_artifact(
            json.dumps(artifact).encode("utf-8"),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )
        assert res["native_commission_usd_per_lot_per_side"] == Decimal("0.0")
        assert res["account_tier"] == "STANDARD_CENT"

    def test_commission_parser_rejects_goldc_for_standard_cent(self):
        """Commission artifact with GOLDc must be rejected for STANDARD_CENT."""
        artifact = {
            "symbol": "GOLDc",
            "account_tier": "STANDARD_CENT",
            "commission_per_lot_usd": 0.0,
        }
        with pytest.raises(ValueError, match="COMMISSION_PARSER_ERROR"):
            parse_commission_backing_artifact(
                json.dumps(artifact).encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_commission_parser_tier_mismatch_rejected(self):
        artifact = {
            "symbol": "XAUUSD",
            "account_tier": "STANDARD",
            "commission_per_lot_usd": 0.0,
        }
        with pytest.raises(ValueError, match="account tier mismatch"):
            parse_commission_backing_artifact(
                json.dumps(artifact).encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_financing_parser_standard_cent_accepted(self):
        artifact = {
            "symbol": "XAUUSDc",
            "account_tier": "STANDARD_CENT",
            "swap_long": -15.5,
            "swap_short": 8.2,
            "rollover_summer_utc_hour": 21,
            "rollover_winter_utc_hour": 22,
            "triple_swap_weekday": "WEDNESDAY",
        }
        res = parse_financing_backing_artifact(
            json.dumps(artifact).encode("utf-8"),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )
        assert res["swap_long_points"] == Decimal("-15.5")
        assert res["swap_short_points"] == Decimal("8.2")

    def test_financing_parser_rejects_goldc_for_standard_cent(self):
        """Financing artifact with GOLDc must be rejected for STANDARD_CENT."""
        artifact = {
            "symbol": "GOLDc",
            "account_tier": "STANDARD_CENT",
            "swap_long": -15.5,
            "swap_short": 8.2,
            "rollover_summer_utc_hour": 21,
            "rollover_winter_utc_hour": 22,
            "triple_swap_weekday": "WEDNESDAY",
        }
        with pytest.raises(ValueError, match="FINANCING_EVIDENCE_MISSING"):
            parse_financing_backing_artifact(
                json.dumps(artifact).encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_financing_parser_tier_mismatch_rejected(self):
        artifact = {
            "symbol": "XAUUSD",
            "account_tier": "STANDARD",
            "swap_long": -15.5,
            "swap_short": 8.2,
            "rollover_summer_utc_hour": 21,
            "rollover_winter_utc_hour": 22,
            "triple_swap_weekday": "WEDNESDAY",
        }
        with pytest.raises(ValueError, match="(account tier mismatch|symbol.*does not match|incompatible)"):
            parse_financing_backing_artifact(
                json.dumps(artifact).encode("utf-8"),
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )


# =============================================================================
# 6. TICK & TELEMETRY PARSER ISOLATION TESTS (Directive 7)
# =============================================================================

class TestTickAndTelemetryParserIsolation:
    """Directive 7: Tick and slippage parsers validate broker_symbol and account_tier correctly."""

    def test_tick_parser_rejects_wrong_symbol_for_standard_cent(self):
        """Tick file with symbol EURUSDc instead of XAUUSDc must be rejected for Standard Cent."""
        csv_content = (
            "<DATE>\t<TIME>\t<BID>\t<ASK>\t<SYMBOL>\n"
            "2026.01.05\t00:00:01.123\t2000.10\t2000.35\tEURUSDc\n"
        )
        with pytest.raises(ValueError, match="Symbol mismatch"):
            parse_mt5_tick_export(
                csv_content.encode("utf-8"),
                expected_symbol="XAUUSD",
                server_tz=timezone.utc,
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_tick_parser_rejects_goldc_for_standard_cent(self):
        """Tick file with symbol GOLDc must be rejected for Standard Cent."""
        csv_content = (
            "<DATE>\t<TIME>\t<BID>\t<ASK>\t<SYMBOL>\n"
            "2026.01.05\t00:00:01.123\t2000.10\t2000.35\tGOLDc\n"
        )
        with pytest.raises(ValueError, match="Symbol mismatch"):
            parse_mt5_tick_export(
                csv_content.encode("utf-8"),
                expected_symbol="XAUUSD",
                server_tz=timezone.utc,
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    def test_telemetry_parser_validates_standard_cent(self):
        header = "order_type,side,decision_timestamp,order_send_timestamp,reference_bid,reference_ask,executed_fill_price,fill_timestamp,volume_lots,latency_ms,symbol,account_tier,venue\n"
        row = "BUY,BUY,2026-01-05 10:00:00Z,2026-01-05 10:00:00.010Z,2000.10,2000.35,2000.36,2026-01-05 10:00:00.050Z,1.00,40,XAUUSDc,STANDARD_CENT,EXNESS\n"
        csv_content = (header + row).encode("utf-8")
        res, summary = parse_mt5_execution_telemetry(
            csv_content,
            expected_venue="EXNESS",
            expected_symbol="XAUUSD",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )
        assert summary["sample_count"] == 1
        assert summary["symbol"] == "XAUUSD"
        assert summary["broker_symbol"] == "XAUUSDc"
        assert res[0]["symbol"] == "XAUUSDc"
        assert summary["account_tier"] == "STANDARD_CENT"

    def test_telemetry_parser_rejects_cross_tier(self):
        header = "order_type,side,decision_timestamp,order_send_timestamp,reference_bid,reference_ask,executed_fill_price,fill_timestamp,volume_lots,latency_ms,symbol,account_tier,venue\n"
        row = "BUY,BUY,2026-01-05 10:00:00Z,2026-01-05 10:00:00.010Z,2000.10,2000.35,2000.36,2026-01-05 10:00:00.050Z,1.00,40,XAUUSD,STANDARD,EXNESS\n"
        csv_content = (header + row).encode("utf-8")
        with pytest.raises(ValueError, match="(account tier mismatch|symbol mismatch|incompatible)"):
            parse_mt5_execution_telemetry(
                csv_content,
                expected_venue="EXNESS",
                expected_symbol="XAUUSD",
                expected_account_tier="STANDARD_CENT",
                expected_broker_symbol="XAUUSDc",
            )

    def test_telemetry_parser_rejects_goldc_for_standard_cent(self):
        header = "order_type,side,decision_timestamp,order_send_timestamp,reference_bid,reference_ask,executed_fill_price,fill_timestamp,volume_lots,latency_ms,symbol,account_tier,venue\n"
        row = "BUY,BUY,2026-01-05 10:00:00Z,2026-01-05 10:00:00.010Z,2000.10,2000.35,2000.36,2026-01-05 10:00:00.050Z,1.00,40,GOLDc,STANDARD_CENT,EXNESS\n"
        csv_content = (header + row).encode("utf-8")
        with pytest.raises(ValueError, match="Telemetry symbol mismatch"):
            parse_mt5_execution_telemetry(
                csv_content,
                expected_venue="EXNESS",
                expected_symbol="XAUUSD",
                expected_account_tier="STANDARD_CENT",
                expected_broker_symbol="XAUUSDc",
            )


# =============================================================================
# 7. MANAGEMENT COMMAND CLI & MANIFEST TESTS (Directives 3, 8)
# =============================================================================

class TestManagementCommandStandardCent:
    """Proves ingest_xauusd_empirical_friction CLI supports STANDARD_CENT."""

    def test_cli_accepts_standard_cent_argument(self, db):
        """Running dry-run with --account-tier STANDARD_CENT succeeds and records scope."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
            manifest_path = tf_m.name
            report_path = tf_r.name

        try:
            out = io.StringIO()
            call_command(
                "ingest_xauusd_empirical_friction",
                "--account-tier", "STANDARD_CENT",
                "--broker-symbol", "XAUUSDc",
                "--output-manifest", manifest_path,
                "--output-report", report_path,
                "--dry-run",
                stdout=out,
            )
            output = out.getvalue()
            assert "STANDARD_CENT" in output
            assert "Saved manifest" in output

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            assert manifest["account_tier"] == "STANDARD_CENT"
            assert manifest["canonical_symbol"] == "XAUUSD"
            assert manifest["broker_symbol"] == "XAUUSDc"
            assert manifest["account_currency"] == "USC"
        finally:
            if os.path.exists(manifest_path):
                os.remove(manifest_path)
            if os.path.exists(report_path):
                os.remove(report_path)

    def test_cli_rejects_unsupported_tier(self, db):
        with pytest.raises(CommandError, match="invalid choice"):
            call_command(
                "ingest_xauusd_empirical_friction",
                "--account-tier", "PRO",
            )

    def test_cli_rejects_zero_tier(self, db):
        with pytest.raises(CommandError, match="invalid choice"):
            call_command(
                "ingest_xauusd_empirical_friction",
                "--account-tier", "ZERO",
            )


# =============================================================================
# 8. READINESS EVALUATOR TESTS FOR STANDARD_CENT (Directive 11)
# =============================================================================

class TestReadinessEvaluatorStandardCent:
    """Directive 11: Readiness remains strictly FAIL-CLOSED (WAIT) with STANDARD_CENT."""

    def test_readiness_fails_closed_with_missing_empirical_evidence(self, xauusd_setup):
        instrument, primary_listing = xauusd_setup
        _create_clean_candles(instrument, count=30)

        report = XauUsdDataReadinessEvaluator.evaluate(
            execution_venue="EXNESS",
            execution_account_tier="STANDARD_CENT",
            execution_legal_entity_code="EXNESS_SC_REVISED",
            override_macro_count=100,
        )

        assert report.passed is False
        assert report.decision == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert report.friction_status == "EMPIRICAL_FRICTION_NOT_CONFIGURED"
        assert any("No active FrictionModelActivation" in r or "Empirical friction" in r for r in report.reasons)

        # Confirm markdown report preserves production authority FALSE and WAIT
        md = report.to_markdown_report()
        assert "Production Authority:** `FALSE`" in md
        assert "Published Decision:** `WAIT`" in md

    @pytest.mark.django_db
    def test_readiness_evaluator_rejects_invalid_account_tier(self):
        with pytest.raises(ValueError, match="Unsupported or unverified account tier"):
            XauUsdDataReadinessEvaluator.evaluate(
                execution_venue="EXNESS",
                execution_account_tier="VIP_TIER",
            )


# =============================================================================
# 9. PRIVACY & NON-PERSISTENCE OF SENSITIVE CREDENTIALS (Directive 9)
# =============================================================================

class TestPrivacyAndNonPersistenceOfSensitiveData:
    """Directive 9: Private credentials and account identifiers are not required and not persisted."""

    def test_attestation_does_not_contain_account_number_or_credentials(self, db):
        """Attestation model only stores cryptographic hash and non-sensitive audit metadata."""
        fields = [f.name for f in FrictionSourceProvenanceAttestation._meta.get_fields()]
        assert "account_login" not in fields
        assert "account_password" not in fields
        assert "personal_name" not in fields
        assert "balance" not in fields
        assert "equity" not in fields

    def test_dataset_does_not_contain_account_number_or_credentials(self, db):
        fields = [f.name for f in FrictionEvidenceDataset._meta.get_fields()]
        assert "account_login" not in fields
        assert "account_password" not in fields
        assert "personal_name" not in fields
        assert "balance" not in fields
        assert "equity" not in fields
