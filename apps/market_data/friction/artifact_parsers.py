"""Authoritative component-specific backing artifact parsers for XAUUSD empirical friction.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance:
- A backing artifact format alone (%PDF, <html>, len > 32) CANNOT qualify.
- Component-specific parsers independently extract normalized fields FROM raw artifact bytes.
- Contradictory user metadata is rejected (EMPIRICAL_FRICTION_INVALID).
- Missing required fields fail closed.
- Computes canonical normalized evidence hash and records parser identity.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple


def parse_optional_evidence_bool(value: Any) -> Optional[bool]:
    """Strictly parse an optional evidence boolean without silent coercion or bool('false') pitfalls.

    Accepted True values:
        True, "true", "TRUE", "True", 1, "1", "yes", "YES", "Yes", "t", "y"
    Accepted False values:
        False, "false", "FALSE", "False", 0, "0", "no", "NO", "No", "f", "n"
    Missing / None values:
        None, "" (empty or whitespace string)
    Unknown tokens / types:
        Raises ValueError (fail closed)
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        raise ValueError(f"STRICT_BOOLEAN_ERROR: Invalid boolean numeric value: {value}")

    val_str = str(value).strip().lower()
    if val_str == "":
        return None
    if val_str in ("true", "1", "yes", "t", "y"):
        return True
    if val_str in ("false", "0", "no", "f", "n"):
        return False
    raise ValueError(f"STRICT_BOOLEAN_ERROR: Cannot parse '{value}' as evidence boolean (must be true/false/1/0/yes/no or None).")


KNOWN_ACCOUNT_TIERS = {"STANDARD", "STANDARD_CENT", "RAW_SPREAD"}


def normalize_account_tier(raw_tier: Optional[str]) -> Optional[str]:
    """Normalize broker account tier string to canonical tier representation.

    Guarantees exact normalized tier semantics and prevents substring confusion:
    - STANDARD != STANDARD_CENT
    - STANDARD_CENT != RAW_SPREAD
    - STANDARD_CENT != PRO
    - STANDARD_CENT != ZERO
    """
    if not raw_tier:
        return None
    cleaned = re.sub(r"[_\-\s]+", "_", str(raw_tier).strip()).upper()
    if cleaned in ("STANDARD_CENT", "STANDARDCENT"):
        return "STANDARD_CENT"
    if cleaned == "STANDARD":
        return "STANDARD"
    if cleaned in ("RAW_SPREAD", "RAWSPREAD"):
        return "RAW_SPREAD"
    if cleaned in ("ZERO", "PRO"):
        return cleaned
    return cleaned


def validate_account_tier(raw_tier: Optional[str]) -> str:
    """Validate and normalize account tier, raising ValueError if unsupported."""
    norm = normalize_account_tier(raw_tier)
    if not norm or norm not in KNOWN_ACCOUNT_TIERS:
        raise ValueError(f"Unsupported or unverified account tier: '{raw_tier}' (supported: {sorted(list(KNOWN_ACCOUNT_TIERS))})")
    return norm



def _matches_expected_symbol(
    candidate: str,
    expected_symbol: str,
    expected_broker_symbol: Optional[str] = None,
    expected_account_tier: Optional[str] = None,
) -> bool:
    """Check if candidate symbol matches expected_symbol with exact broker symbol binding.

    Hardening Rules (Pre-Phase-8 Calibration Hardening Governance):
    1. STANDARD_CENT tier:
       Requires explicit expected_broker_symbol (no implicit inference).
       Candidate must match exact expected broker symbol.
       Hostile lookalikes and standard symbols ('GOLDc', 'GOLD', 'XAUUSDm', 'XAUUSD') are strictly rejected.
    2. STANDARD tier:
       If expected_broker_symbol is provided (e.g. 'XAUUSDm'), candidate must match it exactly.
       Cent symbols ('XAUUSDc', 'GOLDc') are strictly rejected under all circumstances.
       If expected_broker_symbol is None, canonical 'XAUUSD', 'XAU/USD', 'GOLD' are accepted,
       while suffix variants like 'XAUUSDm' require explicit expected_broker_symbol.
    3. Explicit expected_broker_symbol:
       If expected_broker_symbol is provided, candidate must match it exactly.
    """
    c = str(candidate).replace("/", "").strip().upper()
    e = str(expected_symbol).replace("/", "").strip().upper()
    norm_tier = normalize_account_tier(expected_account_tier)

    # 1. STANDARD_CENT tier: Strict exact broker symbol match requiring explicit expected_broker_symbol
    if norm_tier == "STANDARD_CENT":
        if not expected_broker_symbol or not str(expected_broker_symbol).strip():
            raise ValueError(
                "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
            )
        target_broker = str(expected_broker_symbol).replace("/", "").strip().upper()
        # Standard analytical and broker symbols (XAUUSD, XAUUSDm, GOLD, GOLDc, EURUSDc, BTCUSDc, USOILc) are strictly rejected.
        if c in ("XAUUSD", "XAUUSDM", "GOLD", "GOLDC", "EURUSDC", "BTCUSDC", "USOILC"):
            if target_broker != c:
                return False
        # Standard symbols cannot qualify cent tier even if passed as expected_broker_symbol
        if target_broker in ("XAUUSD", "XAUUSDM", "GOLD"):
            return False
        return c == target_broker

    # 2. Explicit broker symbol provided for other tiers
    target_broker = (
        str(expected_broker_symbol).replace("/", "").strip().upper()
        if expected_broker_symbol
        else None
    )
    if target_broker:
        # Cent symbols can NEVER qualify non-cent tiers (e.g. STANDARD or RAW_SPREAD)
        if norm_tier in ("STANDARD", "RAW_SPREAD") and c in ("XAUUSDC", "GOLDC"):
            return False
        return c == target_broker

    # Cent symbols and suffix variants are strictly rejected for STANDARD tier when no broker symbol is specified
    if c in ("XAUUSDC", "GOLDC", "XAUUSDM"):
        return False

    # Standard account: exact canonical match or canonical alias (XAUUSD / GOLD)
    if c == e:
        return True
    if e == "XAUUSD" and c in ("GOLD", "XAUUSD"):
        return True

    return False


def compute_normalized_evidence_hash(derived_data: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of normalized derived evidence dictionary."""
    filtered = {
        k: v for k, v in derived_data.items()
        if not str(k).startswith("_") and k not in ("parser_name", "parser_version", "normalized_evidence_hash", "provenance")
    }
    serialized = json.dumps(filtered, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def compare_asserted_vs_derived(asserted_data: Dict[str, Any], derived_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Compare user-asserted metadata against authoritative derived evidence.

    Returns:
        (is_consistent, mismatches_list)
    """
    mismatches: List[str] = []
    ignored_keys = {
        "provenance", "backing_artifact", "raw_backing_sha256", "raw_sha256",
        "parser_name", "parser_version", "normalized_evidence_hash",
    }
    for key, asserted_val in asserted_data.items():
        if asserted_val is None or key in ignored_keys:
            continue
        if key not in derived_data:
            continue
        derived_val = derived_data[key]
        if derived_val is None:
            continue

        # 1. Compare booleans first (CRITICAL: bool is a subclass of int in Python)
        if isinstance(derived_val, bool):
            try:
                parsed_bool = parse_optional_evidence_bool(asserted_val)
                if parsed_bool != derived_val:
                    mismatches.append(
                        f"Asserted {key} '{asserted_val}' contradicts authoritative parsed backing value '{derived_val}'."
                    )
            except ValueError:
                mismatches.append(
                    f"Asserted {key} '{asserted_val}' contradicts authoritative parsed backing value '{derived_val}'."
                )
        # 2. Compare decimals / numbers
        elif isinstance(derived_val, (Decimal, int, float)) or (
            isinstance(asserted_val, (int, float, str)) and re.match(r"^-?\d+(\.\d+)?$", str(asserted_val).strip())
            and re.match(r"^-?\d+(\.\d+)?$", str(derived_val).strip())
        ):
            try:
                if Decimal(str(asserted_val)) != Decimal(str(derived_val)):
                    mismatches.append(
                        f"Asserted {key} '{asserted_val}' contradicts authoritative parsed backing value '{derived_val}'."
                    )
            except (InvalidOperation, ValueError):
                if str(asserted_val).strip().lower() != str(derived_val).strip().lower():
                    mismatches.append(
                        f"Asserted {key} '{asserted_val}' contradicts authoritative parsed backing value '{derived_val}'."
                    )
        else:
            # String comparison (case-insensitive for codes, exact for names)
            a_str = str(asserted_val).strip()
            d_str = str(derived_val).strip()
            if a_str.upper() != d_str.upper():
                mismatches.append(
                    f"Asserted {key} '{asserted_val}' contradicts authoritative parsed backing value '{derived_val}'."
                )

    return len(mismatches) == 0, mismatches


# =============================================================================
# =============================================================================
# 1. LEGAL ENTITY AUTHORITATIVE PARSER
# =============================================================================

def parse_legal_entity_backing_artifact(
    raw_content: bytes,
    parser_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Extract and validate authoritative legal entity provenance from raw artifact bytes.

    Returns:
        derived_normalized_dict containing legal_entity_name, legal_entity_code,
        regulator, license_number, parser_name, parser_version, normalized_evidence_hash.
    Raises:
        ValueError if artifact fails to establish legal entity identity, regulator, or license number.
    """
    parser_name = "parse_legal_entity_backing_artifact"
    if not isinstance(raw_content, (bytes, bytearray)):
        raise TypeError(f"LEGAL_ENTITY_PARSER_ERROR: Expected raw artifact bytes, got {type(raw_content).__name__}.")

    if not raw_content or len(raw_content.strip()) < 10:
        raise ValueError(
            "LEGAL_ENTITY_PARSER_ERROR: Backing artifact is empty or lacks authentic Exness legal entity evidence; missing regulator or license number."
        )

    text = raw_content.decode("utf-8", errors="ignore").strip()

    # Generic format-only rejection (PDF or HTML without actual legal entity / regulator evidence)
    has_format_header = raw_content.startswith(b"%PDF") or b"<html" in raw_content[:512].lower()

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            le = data.get("legal_entity") if isinstance(data.get("legal_entity"), dict) else {}
            name = str(data.get("legal_entity_name") or le.get("legal_entity_name") or data.get("entity_name") or data.get("name") or "").strip()
            code = str(data.get("legal_entity_code") or le.get("legal_entity_code") or data.get("code") or "").strip()
            reg = str(data.get("regulator") or le.get("regulator") or data.get("regulatory_authority") or "").strip()
            lic = str(data.get("license_number") or le.get("license_number") or data.get("license") or data.get("licence_number") or "").strip()
            if not (reg and lic):
                raise ValueError(
                    "LEGAL_ENTITY_PARSER_ERROR: Backing artifact lacks authentic legal entity evidence; missing regulator or license number."
                )
            if not name and not code:
                raise ValueError(
                    "LEGAL_ENTITY_EVIDENCE_MISSING: Backing artifact lacks explicit legal entity identity (name or code)."
                )
            # Deterministic mapping backed by explicit mapping policy when one is missing
            if name and not code:
                if "SC" in name.upper() or "SEYCHELLES" in name.upper():
                    code = "EXNESS_SC_LTD"
                elif "CY" in name.upper() or "CYPRUS" in name.upper():
                    code = "EXNESS_CY_LTD"
                elif "UK" in name.upper():
                    code = "EXNESS_UK_LTD"
                else:
                    code = re.sub(r"[^A-Za-z0-9_]+", "_", name.upper()).strip("_")
            elif code and not name:
                if code == "EXNESS_SC_LTD":
                    name = "Exness (SC) Ltd"
                elif code == "EXNESS_CY_LTD":
                    name = "Exness (Cy) Ltd"
                elif code == "EXNESS_UK_LTD":
                    name = "Exness (UK) Ltd"
                else:
                    name = code.replace("_", " ").title()

            derived = {
                "legal_entity_name": name,
                "legal_entity_code": code,
                "regulator": reg,
                "license_number": lic,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived
        except json.JSONDecodeError:
            pass

    # Try delimited / colon format e.g. "EXNESS_SC_LTD:FSA:SD025" or key=value
    parts = [p.strip() for p in text.split(":") if p.strip()]
    if len(parts) >= 3 and not has_format_header:
        code, reg, lic = parts[0], parts[1], parts[2]
        if code in ("EXNESS_SC_LTD", "EXNESS_CY_LTD", "EXNESS_UK_LTD") or "EXNESS" in code.upper():
            name = "Exness (SC) Ltd" if "SC" in code else f"Exness ({code})"
            derived = {
                "legal_entity_name": name,
                "legal_entity_code": code,
                "regulator": reg,
                "license_number": lic,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived

    # Key-value / pipe format e.g. legal_entity_name=...|regulator=...
    if "=" in text:
        kv = {}
        for token in re.split(r"[|\n;]", text):
            if "=" in token:
                k, v = token.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        name = kv.get("legal_entity_name") or kv.get("name")
        code = kv.get("legal_entity_code") or kv.get("code")
        reg = kv.get("regulator")
        lic = kv.get("license_number") or kv.get("license")
        if reg and lic:
            if not name and not code:
                raise ValueError(
                    "LEGAL_ENTITY_EVIDENCE_MISSING: Backing artifact lacks explicit legal entity identity (name or code)."
                )
            if name and not code:
                code = "EXNESS_SC_LTD" if "SC" in name.upper() else re.sub(r"[^A-Za-z0-9_]+", "_", name.upper()).strip("_")
            elif code and not name:
                name = "Exness (SC) Ltd" if "SC" in code else code.replace("_", " ").title()
            derived = {
                "legal_entity_name": name,
                "legal_entity_code": code,
                "regulator": reg,
                "license_number": lic,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived

    # Text / Document regex extraction
    reg_match = re.search(r"\b(FSA|CySEC|FCA|FSCA|CBCS|CMA)\b", text, re.IGNORECASE)
    lic_match = re.search(r"(?:license|licence|no\.?|number)[:\s#=]+([A-Za-z0-9/_-]+)", text, re.IGNORECASE) or re.search(r"\b(SD\d{3}|[0-9]{3}/[0-9]{2})\b", text)
    name_match = re.search(r"(Exness\s*(?:\([A-Za-z0-9\s]+\))?\s*(?:Ltd|Limited|B\.V\.)?)", text, re.IGNORECASE)

    if reg_match and lic_match:
        if not name_match:
            raise ValueError(
                "LEGAL_ENTITY_EVIDENCE_MISSING: Backing artifact lacks authentic Exness legal entity identity; regulator/license without explicit entity identity cannot qualify."
            )
        reg = reg_match.group(1).upper()
        lic = lic_match.group(1).strip()
        name = name_match.group(1).strip()
        code = "EXNESS_SC_LTD" if "SC" in name.upper() else re.sub(r"[^A-Za-z0-9_]+", "_", name.upper()).strip("_")
        derived = {
            "legal_entity_name": name,
            "legal_entity_code": code,
            "regulator": reg,
            "license_number": lic,
            "parser_name": parser_name,
            "parser_version": parser_version,
        }
        derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
        return derived

    raise ValueError(
        "LEGAL_ENTITY_PARSER_ERROR: Backing artifact lacks authentic Exness legal entity evidence; missing regulator or license number."
    )


# =============================================================================
# 2. CONTRACT SPECIFICATION AUTHORITATIVE PARSER
# =============================================================================

def parse_contract_spec_backing_artifact(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    expected_broker_symbol: Optional[str] = None,
    expected_account_tier: Optional[str] = None,
    parser_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Extract and validate authoritative contract geometry from raw MT5 / broker export bytes.

    CRITICAL (Directive §1):
    Removes all contract-spec silent defaults. Every mandatory geometry field:
    digits, point_size, trade_tick_size, trade_tick_value, contract_size,
    volume_min, volume_max, volume_step MUST be explicitly established.
    Missing any field raises ValueError("CONTRACT_SPEC_EVIDENCE_MISSING: ...").

    MANDATORY INSTRUMENT APPLICABILITY:
    The authoritative raw artifact must explicitly establish applicability to expected_symbol
    (e.g., XAUUSD, XAU/USD, GOLD) or expected_broker_symbol (e.g. XAUUSDc).
    An otherwise complete geometry artifact with no instrument identity or with an
    incompatible symbol fails closed (CONTRACT_SPEC_EVIDENCE_MISSING).
    """
    parser_name = "parse_contract_spec_backing_artifact"
    if not isinstance(raw_content, (bytes, bytearray)):
        raise TypeError(f"CONTRACT_SPEC_PARSER_ERROR: Expected raw artifact bytes, got {type(raw_content).__name__}.")

    if not raw_content or len(raw_content.strip()) < 10:
        raise ValueError("CONTRACT_SPEC_PARSER_ERROR: Backing artifact is empty or insufficient.")

    text = raw_content.decode("utf-8", errors="ignore").strip()
    norm_tier = normalize_account_tier(expected_account_tier)
    if norm_tier == "STANDARD_CENT":
        if not expected_broker_symbol or not str(expected_broker_symbol).strip():
            raise ValueError(
                "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
            )
    target_broker_sym = str(expected_broker_symbol).strip() if expected_broker_symbol else None

    required_geometry_keys = {
        "digits": ["digits"],
        "point_size": ["point_size", "point"],
        "trade_tick_size": ["trade_tick_size", "tick_size"],
        "trade_tick_value": ["trade_tick_value", "tick_value"],
        "contract_size": ["contract_size"],
        "volume_min": ["volume_min", "vol_min"],
        "volume_max": ["volume_max", "vol_max"],
        "volume_step": ["volume_step", "vol_step"],
    }

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            spec = None
            explicit_symbol_found = False
            detected_key = None

            # If target_broker_sym is explicitly provided, look only for target_broker_sym or matches via _matches_expected_symbol
            if target_broker_sym:
                if target_broker_sym in data and isinstance(data[target_broker_sym], dict):
                    spec = data[target_broker_sym]
                    explicit_symbol_found = True
                    detected_key = target_broker_sym
            elif norm_tier != "STANDARD_CENT":
                if expected_symbol in data and isinstance(data[expected_symbol], dict):
                    spec = data[expected_symbol]
                    explicit_symbol_found = True
                    detected_key = expected_symbol
                elif f"{expected_symbol[:3]}/{expected_symbol[3:]}" in data and isinstance(data[f"{expected_symbol[:3]}/{expected_symbol[3:]}"], dict):
                    spec = data[f"{expected_symbol[:3]}/{expected_symbol[3:]}"]
                    explicit_symbol_found = True
                    detected_key = f"{expected_symbol[:3]}/{expected_symbol[3:]}"
                elif expected_symbol == "XAUUSD" and "GOLD" in data and isinstance(data["GOLD"], dict):
                    spec = data["GOLD"]
                    explicit_symbol_found = True
                    detected_key = "GOLD"

            if not explicit_symbol_found:
                for k, v in data.items():
                    if isinstance(v, dict) and _matches_expected_symbol(k, expected_symbol, target_broker_sym, expected_account_tier):
                        spec = v
                        explicit_symbol_found = True
                        detected_key = k
                        break

            if explicit_symbol_found:
                det_clean = str(detected_key).replace("/", "").upper()
                if norm_tier == "STANDARD":
                    if det_clean in ("XAUUSDC", "GOLDC"):
                        raise ValueError(
                            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains cent symbol '{detected_key}' which is incompatible with STANDARD account tier."
                        )
                    if not target_broker_sym and det_clean in ("XAUUSDM",):
                        raise ValueError(
                            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains suffix symbol '{detected_key}' which requires explicit broker symbol."
                        )
                    if target_broker_sym and det_clean != str(target_broker_sym).replace("/", "").upper():
                        raise ValueError(
                            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{detected_key}' does not match expected broker symbol '{target_broker_sym}'."
                        )
                elif norm_tier == "STANDARD_CENT":
                    if det_clean in ("XAUUSD", "XAUUSDM", "GOLD", "GOLDC") and det_clean != str(target_broker_sym).replace("/", "").upper():
                        raise ValueError(
                            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{detected_key}' does not match expected broker symbol '{target_broker_sym}'."
                        )

            if not explicit_symbol_found:
                other_sym_keys = [
                    k for k in data.keys()
                    if isinstance(data[k], dict) and re.match(r"^[A-Z0-9/_-]{4,10}$", k.upper())
                    and not _matches_expected_symbol(k, expected_symbol, target_broker_sym, expected_account_tier)
                ]
                if other_sym_keys:
                    raise ValueError(
                        f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{other_sym_keys[0]}' does not match expected '{target_broker_sym or expected_symbol}'."
                    )

                # If data itself is the specification dictionary
                if "symbol" in data and data["symbol"]:
                    sym_val = str(data["symbol"])
                    if _matches_expected_symbol(sym_val, expected_symbol, target_broker_sym, expected_account_tier):
                        det_clean = sym_val.replace("/", "").upper()
                        if norm_tier == "STANDARD":
                            if det_clean in ("XAUUSDC", "GOLDC"):
                                raise ValueError(
                                    f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains cent symbol '{sym_val}' which is incompatible with STANDARD account tier."
                                )
                            if not target_broker_sym and det_clean in ("XAUUSDM",):
                                raise ValueError(
                                    f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains suffix symbol '{sym_val}' which requires explicit broker symbol."
                                )
                            if target_broker_sym and det_clean != str(target_broker_sym).replace("/", "").upper():
                                raise ValueError(
                                    f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{sym_val}' does not match expected broker symbol '{target_broker_sym}'."
                                )
                        elif norm_tier == "STANDARD_CENT":
                            if det_clean in ("XAUUSD", "XAUUSDM", "GOLD", "GOLDC") and det_clean != str(target_broker_sym).replace("/", "").upper():
                                raise ValueError(
                                    f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{sym_val}' does not match expected broker symbol '{target_broker_sym}'."
                                )
                        spec = data
                        explicit_symbol_found = True
                    else:
                        raise ValueError(
                            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{data['symbol']}' does not match expected '{target_broker_sym or expected_symbol}'."
                        )
                elif "symbols" in data or "instruments" in data:
                    sym_list = [str(s).upper() for s in (data.get("symbols") or data.get("instruments") or [])]
                    if any(_matches_expected_symbol(s, expected_symbol, target_broker_sym, expected_account_tier) for s in sym_list):
                        if norm_tier == "STANDARD" and any(s.replace("/", "") in ("XAUUSDC", "GOLDC") for s in sym_list):
                            raise ValueError(
                                "CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains cent symbol which is incompatible with STANDARD account tier."
                            )
                        spec = data
                        explicit_symbol_found = True
                    else:
                        raise ValueError(
                            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact does not establish applicability for '{expected_symbol}'."
                        )

            if not explicit_symbol_found:
                raise ValueError(
                    f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact lacks instrument identity or applicability for '{expected_symbol}'."
                )
            if isinstance(spec, dict):
                spec_tier = spec.get("account_tier") or data.get("account_tier") or spec.get("account_type") or data.get("account_type") or spec.get("tier") or data.get("tier")
                if spec_tier and normalize_account_tier(spec_tier) != norm_tier:
                    raise ValueError(f"account tier mismatch: artifact specifies '{spec_tier}' but expected '{expected_account_tier}'.")

                extracted = {}
                missing = []
                for field_name, aliases in required_geometry_keys.items():
                    val = None
                    for a in aliases:
                        if a in spec and spec[a] is not None:
                            val = spec[a]
                            break
                    if val is None:
                        missing.append(field_name)
                    else:
                        extracted[field_name] = val

                if missing:
                    raise ValueError(
                        f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact lacks mandatory geometry fields: {', '.join(missing)}."
                    )

                derived = {
                    "symbol": expected_symbol,
                    "digits": int(extracted["digits"]),
                    "point_size": Decimal(str(extracted["point_size"])),
                    "trade_tick_size": Decimal(str(extracted["trade_tick_size"])),
                    "trade_tick_value": Decimal(str(extracted["trade_tick_value"])),
                    "contract_size": Decimal(str(extracted["contract_size"])),
                    "volume_min": Decimal(str(extracted["volume_min"])),
                    "volume_max": Decimal(str(extracted["volume_max"])),
                    "volume_step": Decimal(str(extracted["volume_step"])),
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                }
                if spec_tier:
                    derived["account_tier"] = normalize_account_tier(spec_tier)
                observed_broker_sym = None
                if isinstance(spec, dict) and (spec.get("symbol") or spec.get("broker_symbol")):
                    observed_broker_sym = str(spec.get("symbol") or spec.get("broker_symbol"))
                elif "symbol" in data and data["symbol"]:
                    observed_broker_sym = str(data["symbol"])
                elif "broker_symbol" in data and data["broker_symbol"]:
                    observed_broker_sym = str(data["broker_symbol"])
                if observed_broker_sym:
                    derived["broker_symbol"] = observed_broker_sym
                derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                return derived
        except json.JSONDecodeError:
            pass

    # Non-JSON: Check for explicit symbol tag or presence in text
    sym_tag = re.search(r"\bSYMBOL[:\s=]+([A-Za-z0-9/_-]+)", text, re.IGNORECASE)
    if sym_tag:
        sym = sym_tag.group(1).replace("/", "").upper()
        if not _matches_expected_symbol(sym, expected_symbol, target_broker_sym, expected_account_tier):
            raise ValueError(
                f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbol '{sym}' does not match expected '{target_broker_sym or expected_symbol}'."
            )
        if norm_tier == "STANDARD" and sym in ("XAUUSDC", "GOLDC"):
            raise ValueError(
                f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains cent symbol '{sym}' which is incompatible with STANDARD account tier."
            )
        if not target_broker_sym and norm_tier == "STANDARD" and sym in ("XAUUSDM",):
            raise ValueError(
                f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains suffix symbol '{sym}' which requires explicit broker symbol."
            )
    else:
        ignore_words = {
            "DIGITS", "VOLUME", "SPREAD", "MARGIN", "SYMBOL", "POINT", "POINTS",
            "STATUS", "TRADE", "TICK", "CONTRACT", "POLICY", "SIZE", "STEP", "VALUE",
            "MINIMUM", "MAXIMUM", "ACCOUNT", "TYPE", "TIER",
        }
        sym_mentions = [
            s.replace("/", "").upper() for s in re.findall(r"\b([A-Z0-9]{4,8}|[A-Z]{3}/[A-Z]{3}c?)\b", text)
            if s.upper() not in ignore_words and s.replace("/", "").upper() not in ignore_words
        ]
        if norm_tier == "STANDARD_CENT":
            has_expected = bool(
                target_broker_sym and re.search(rf"\b{re.escape(target_broker_sym)}\b", text, re.IGNORECASE)
            )
        else:
            if target_broker_sym:
                has_expected = bool(re.search(rf"\b{re.escape(target_broker_sym)}\b", text, re.IGNORECASE))
            else:
                has_expected = bool(
                    re.search(rf"\b{re.escape(expected_symbol)}\b", text, re.IGNORECASE)
                    or (expected_symbol.upper() == "XAUUSD" and re.search(r"\b(XAU/USD|GOLD)\b", text, re.IGNORECASE))
                )
        if norm_tier == "STANDARD":
            if any(s in ("XAUUSDC", "GOLDC") for s in sym_mentions):
                raise ValueError(
                    "CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains cent symbol for STANDARD account tier."
                )
            if not target_broker_sym and any(s in ("XAUUSDM",) for s in sym_mentions):
                raise ValueError(
                    "CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact contains suffix symbol for STANDARD account tier."
                )
        if sym_mentions and not has_expected:
            raise ValueError(
                f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact symbols '{sym_mentions[:3]}' do not match expected '{target_broker_sym or expected_symbol}'."
            )
        if not has_expected and not sym_mentions:
            raise ValueError(
                f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact lacks instrument identity or applicability for '{target_broker_sym or expected_symbol}'."
            )

    # Try pipe / colon delimited format e.g. "SYMBOL:XAUUSD|CONTRACT_SIZE:100|POINT:0.01|DIGITS:2"
    if "|" in text or ":" in text:
        kv = {}
        for token in re.split(r"[|\n;]", text):
            if ":" in token:
                k, v = token.split(":", 1)
                kv[k.strip().upper()] = v.strip()
            elif "=" in token:
                k, v = token.split("=", 1)
                kv[k.strip().upper()] = v.strip()

        tier_val = (
            kv.get("ACCOUNT_TYPE")
            or kv.get("ACCOUNT TYPE")
            or kv.get("ACCOUNT_TIER")
            or kv.get("ACCOUNT TIER")
            or kv.get("TIER")
        )
        if tier_val and normalize_account_tier(tier_val) != norm_tier:
            raise ValueError(f"account tier mismatch: artifact specifies tier '{tier_val}' but expected '{expected_account_tier}'.")

        required_kv_map = {
            "digits": ["DIGITS"],
            "point_size": ["POINT_SIZE", "POINT", "POINT SIZE"],
            "trade_tick_size": ["TRADE_TICK_SIZE", "TICK_SIZE", "TRADE TICK SIZE", "TICK SIZE"],
            "trade_tick_value": ["TRADE_TICK_VALUE", "TICK_VALUE", "TRADE TICK VALUE", "TICK VALUE"],
            "contract_size": ["CONTRACT_SIZE", "CONTRACT", "CONTRACT SIZE"],
            "volume_min": ["VOLUME_MIN", "VOL_MIN", "MINIMUM VOLUME", "MIN VOLUME", "VOLUME MIN"],
            "volume_max": ["VOLUME_MAX", "VOL_MAX", "MAXIMUM VOLUME", "MAX VOLUME", "VOLUME MAX"],
            "volume_step": ["VOLUME_STEP", "VOL_STEP", "VOLUME STEP", "VOL STEP"],
        }
        extracted_kv = {}
        missing_kv = []
        for field_name, aliases in required_kv_map.items():
            val = None
            for a in aliases:
                if a in kv and kv[a] is not None and kv[a] != "":
                    val = kv[a]
                    break
            if val is None:
                missing_kv.append(field_name)
            else:
                extracted_kv[field_name] = val

        if not missing_kv:
            derived = {
                "symbol": expected_symbol,
                "digits": int(extracted_kv["digits"]),
                "point_size": Decimal(extracted_kv["point_size"]),
                "trade_tick_size": Decimal(extracted_kv["trade_tick_size"]),
                "trade_tick_value": Decimal(extracted_kv["trade_tick_value"]),
                "contract_size": Decimal(extracted_kv["contract_size"]),
                "volume_min": Decimal(extracted_kv["volume_min"]),
                "volume_max": Decimal(extracted_kv["volume_max"]),
                "volume_step": Decimal(extracted_kv["volume_step"]),
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            if tier_val:
                derived["account_tier"] = normalize_account_tier(tier_val)
            if "SYMBOL" in kv and kv["SYMBOL"]:
                derived["broker_symbol"] = str(kv["SYMBOL"])
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived

    # MT5 SymbolInfo text format
    digits_m = re.search(r"digits[:\s=]+(\d+)", text, re.IGNORECASE)
    point_m = re.search(r"point(?:_size)?[:\s=]+([\d.]+)", text, re.IGNORECASE)
    tick_size_m = re.search(r"(?:trade_)?tick_size[:\s=]+([\d.]+)", text, re.IGNORECASE)
    tick_val_m = re.search(r"(?:trade_)?tick_value[:\s=]+([\d.]+)", text, re.IGNORECASE)
    c_size_m = re.search(r"contract(?:_size)?[:\s=]+([\d.]+)", text, re.IGNORECASE)
    v_min_m = re.search(r"volume_min[:\s=]+([\d.]+)", text, re.IGNORECASE)
    v_max_m = re.search(r"volume_max[:\s=]+([\d.]+)", text, re.IGNORECASE)
    v_step_m = re.search(r"volume_step[:\s=]+([\d.]+)", text, re.IGNORECASE)

    missing_regex = []
    if not digits_m:
        missing_regex.append("digits")
    if not point_m:
        missing_regex.append("point_size")
    if not tick_size_m:
        missing_regex.append("trade_tick_size")
    if not tick_val_m:
        missing_regex.append("trade_tick_value")
    if not c_size_m:
        missing_regex.append("contract_size")
    if not v_min_m:
        missing_regex.append("volume_min")
    if not v_max_m:
        missing_regex.append("volume_max")
    if not v_step_m:
        missing_regex.append("volume_step")

    if missing_regex:
        raise ValueError(
            f"CONTRACT_SPEC_EVIDENCE_MISSING: Backing artifact lacks mandatory geometry fields: {', '.join(missing_regex)}."
        )

    derived = {
        "symbol": expected_symbol,
        "digits": int(digits_m.group(1)),
        "point_size": Decimal(point_m.group(1)),
        "trade_tick_size": Decimal(tick_size_m.group(1)),
        "trade_tick_value": Decimal(tick_val_m.group(1)),
        "contract_size": Decimal(c_size_m.group(1)),
        "volume_min": Decimal(v_min_m.group(1)),
        "volume_max": Decimal(v_max_m.group(1)),
        "volume_step": Decimal(v_step_m.group(1)),
        "parser_name": parser_name,
        "parser_version": parser_version,
    }
    if sym_tag:
        derived["broker_symbol"] = sym_tag.group(1).strip()
    derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
    return derived


# =============================================================================
# 3. COMMISSION AUTHORITATIVE PARSER
# =============================================================================

def parse_commission_backing_artifact(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    expected_account_tier: str = "STANDARD",
    expected_broker_symbol: Optional[str] = None,
    parser_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Extract and validate authoritative commission schedule from raw broker export bytes.

    CRITICAL (Directive §4):
    Commission evidence must establish account_tier, commission rate/rule, and
    symbol/instrument applicability. Does NOT inject symbol solely from expected_symbol
    when artifact contains no applicability evidence.
    """
    parser_name = "parse_commission_backing_artifact"
    if not isinstance(raw_content, (bytes, bytearray)):
        raise TypeError(f"COMMISSION_PARSER_ERROR: Expected raw artifact bytes, got {type(raw_content).__name__}.")

    if not raw_content or len(raw_content.strip()) < 5:
        raise ValueError("COMMISSION_PARSER_ERROR: Backing artifact is empty or insufficient.")

    text = raw_content.decode("utf-8", errors="ignore").strip()
    norm_tier = normalize_account_tier(expected_account_tier) or "STANDARD"
    if norm_tier == "STANDARD_CENT":
        if not expected_broker_symbol or not str(expected_broker_symbol).strip():
            raise ValueError(
                "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
            )
    target_broker_sym = str(expected_broker_symbol).strip() if expected_broker_symbol else None

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            # Check tier specific section or top level
            tier_data = None
            if "tiers" in data and isinstance(data["tiers"], dict):
                tier_data = data["tiers"].get(expected_account_tier) or data["tiers"].get(norm_tier)
                if not tier_data:
                    for k, v in data["tiers"].items():
                        if normalize_account_tier(k) == norm_tier:
                            tier_data = v
                            break
            elif data.get("account_tier"):
                data_tier = normalize_account_tier(data.get("account_tier"))
                if data_tier != norm_tier:
                    raise ValueError(f"account tier mismatch: Fee document specifies '{data.get('account_tier')}' but expected '{expected_account_tier}'.")
                tier_data = data

            if tier_data is not None and isinstance(tier_data, dict):
                fee_val = None
                for k in ("native_commission_usd_per_lot_per_side", "commission", "commission_per_lot_usd", "fee"):
                    if k in tier_data and tier_data[k] is not None:
                        fee_val = tier_data[k]
                        break
                formula = tier_data.get("commission_formula") or "DYNAMIC_NOTIONAL_BPS"
                if fee_val is not None:
                    # Check applicability in tier_data or top-level data
                    is_instrument_specific = False
                    is_global_scope = False

                    tier_sym = str(tier_data.get("symbol") or "").upper()
                    tier_syms = [str(s).upper() for s in (tier_data.get("symbols") or tier_data.get("instruments") or [])]
                    top_sym = str(data.get("symbol") or "").upper()
                    top_syms = [str(s).upper() for s in (data.get("symbols") or data.get("instruments") or [])]
                    all_syms = set([tier_sym, top_sym] + tier_syms + top_syms)

                    if norm_tier == "STANDARD_CENT":
                        valid_symbols = {target_broker_sym.upper()} if target_broker_sym else {"XAUUSDC"}
                    else:
                        valid_symbols = {
                            expected_symbol.upper(),
                            f"{expected_symbol[:3]}/{expected_symbol[3:]}".upper(),
                            "GOLD",
                        }
                        if target_broker_sym:
                            valid_symbols.add(target_broker_sym.upper())

                    if any(s in all_syms for s in valid_symbols):
                        is_instrument_specific = True

                    scope_decl = str(tier_data.get("scope") or data.get("scope") or tier_data.get("applicability") or data.get("applicability") or "").upper()
                    if scope_decl in ("GLOBAL", "ALL", "ALL_SYMBOLS", "ALL_INSTRUMENTS", "ACCOUNT_TIER_WIDE"):
                        is_global_scope = True
                    elif tier_data.get("all_instruments") is True or data.get("all_instruments") is True or tier_data.get("all_symbols") is True or data.get("all_symbols") is True:
                        is_global_scope = True

                    if not is_instrument_specific and not is_global_scope:
                        raise ValueError(
                            f"COMMISSION_PARSER_ERROR: Backing artifact for tier '{expected_account_tier}' lacks symbol applicability for '{expected_symbol}' or explicit global tier scope."
                        )

                    derived = {
                        "account_tier": norm_tier,
                        "symbol": expected_symbol if is_instrument_specific else "ALL",
                        "applicability_scope": "INSTRUMENT" if is_instrument_specific else "GLOBAL",
                        "native_commission_usd_per_lot_per_side": Decimal(str(fee_val)),
                        "commission_formula": formula,
                        "parser_name": parser_name,
                        "parser_version": parser_version,
                    }
                    derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                    return derived
            elif "tiers" in data:
                normalized_avail = {normalize_account_tier(k) for k in data["tiers"].keys()}
                if norm_tier not in normalized_avail:
                    raise ValueError(
                        f"COMMISSION_PARSER_ERROR: Fee document does not establish commission for requested account tier '{expected_account_tier}'."
                    )
        except json.JSONDecodeError:
            pass

    # Try pipe / colon format e.g. "STANDARD:COMMISSION:0.00:SCOPE:GLOBAL" or "STANDARD_CENT:COMMISSION:0.00:XAUUSD"
    if ":" in text or "|" in text:
        parts = [p.strip() for p in re.split(r"[:|]", text) if p.strip()]
        tiers_in_text = [normalize_account_tier(p) for p in parts if normalize_account_tier(p) in KNOWN_ACCOUNT_TIERS]
        if tiers_in_text and norm_tier not in tiers_in_text:
            raise ValueError(
                f"COMMISSION_PARSER_ERROR: Fee document does not establish commission for requested account tier '{expected_account_tier}' (found: {tiers_in_text})."
            )

        if norm_tier == "STANDARD_CENT":
            if not target_broker_sym:
                raise ValueError(
                    "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
                )
            target_sym = str(target_broker_sym).replace("/", "").strip().upper()
            has_sym = any(p.upper() == target_sym for p in parts)
        else:
            has_sym = any(
                p.upper() in (expected_symbol.upper(), f"{expected_symbol[:3]}/{expected_symbol[3:]}".upper(), "GOLD")
                for p in parts
            )
        has_global = any(p.upper() in ("GLOBAL", "ALL", "ALL_SYMBOLS", "ALL_INSTRUMENTS", "ACCOUNT_WIDE") for p in parts)

        if "COMMISSION" in [p.upper() for p in parts]:
            idx = [p.upper() for p in parts].index("COMMISSION")
            if idx + 1 < len(parts):
                fee_str = parts[idx + 1]
                if not (has_sym or has_global):
                    raise ValueError(
                        f"COMMISSION_PARSER_ERROR: Backing artifact lacks applicability evidence for '{expected_symbol}' or explicit global tier scope."
                    )
                derived = {
                    "account_tier": norm_tier,
                    "symbol": expected_symbol if has_sym else "ALL",
                    "applicability_scope": "INSTRUMENT" if has_sym else "GLOBAL",
                    "native_commission_usd_per_lot_per_side": Decimal(fee_str),
                    "commission_formula": "DYNAMIC_NOTIONAL_BPS",
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                }
                derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                return derived

    # Regex search for tier and commission with applicability requirement
    if norm_tier == "STANDARD_CENT":
        tier_re = re.search(r"\b(?:STANDARD[_\-\s]+CENT|CENT)\b.*?commission[:\s=]+([\d.]+)", text, re.IGNORECASE)
    elif norm_tier == "STANDARD":
        tier_re = re.search(r"\bSTANDARD(?!\s*[-_]?\s*CENT)\b.*?commission[:\s=]+([\d.]+)", text, re.IGNORECASE)
    else:
        tier_re = re.search(rf"\b{re.escape(expected_account_tier)}\b.*?commission[:\s=]+([\d.]+)", text, re.IGNORECASE)

    if tier_re:
        fee_val = Decimal(tier_re.group(1))
        if norm_tier == "STANDARD_CENT":
            if not target_broker_sym:
                raise ValueError(
                    "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
                )
            target_sym = str(target_broker_sym).strip()
            has_sym = bool(re.search(rf"\b{re.escape(target_sym)}\b", text, re.IGNORECASE))
        else:
            has_sym = bool(
                re.search(rf"\b{re.escape(expected_symbol)}\b", text, re.IGNORECASE)
                or re.search(r"\b(XAU/USD|GOLD)\b", text, re.IGNORECASE)
            )
        has_global = bool(re.search(r"\b(ALL\s+SYMBOLS|ALL\s+INSTRUMENTS|GLOBAL\s+TIER|GLOBAL|ACCOUNT_WIDE)\b", text, re.IGNORECASE))
        if not (has_sym or has_global):
            raise ValueError(
                f"COMMISSION_PARSER_ERROR: Backing artifact lacks applicability evidence for '{expected_symbol}' or explicit global tier scope."
            )
        derived = {
            "account_tier": norm_tier,
            "symbol": expected_symbol if has_sym else "ALL",
            "applicability_scope": "INSTRUMENT" if has_sym else "GLOBAL",
            "native_commission_usd_per_lot_per_side": fee_val,
            "commission_formula": "DYNAMIC_NOTIONAL_BPS",
            "parser_name": parser_name,
            "parser_version": parser_version,
        }
        derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
        return derived

    # Check if other tiers exist but not requested
    detected_tiers = set()
    if re.search(r"\b(?:STANDARD[_\-\s]+CENT|CENT)\b", text, re.IGNORECASE):
        detected_tiers.add("STANDARD_CENT")
    if re.search(r"\bSTANDARD(?!\s*[-_]?\s*CENT)\b", text, re.IGNORECASE):
        detected_tiers.add("STANDARD")
    if re.search(r"\bRAW[_\-\s]*SPREAD\b", text, re.IGNORECASE):
        detected_tiers.add("RAW_SPREAD")
    if re.search(r"\bPRO\b", text, re.IGNORECASE):
        detected_tiers.add("PRO")
    if re.search(r"\bZERO\b", text, re.IGNORECASE):
        detected_tiers.add("ZERO")

    if detected_tiers and norm_tier not in detected_tiers:
        raise ValueError(
            f"COMMISSION_PARSER_ERROR: Fee document does not establish commission for requested account tier '{expected_account_tier}' (found: {sorted(detected_tiers)})."
        )

    raise ValueError(
        f"COMMISSION_PARSER_ERROR: Backing artifact does not establish commission rule or rate for account tier '{expected_account_tier}'."
    )


# =============================================================================
# 4. FINANCING / SWAP AUTHORITATIVE PARSER
# =============================================================================

def parse_financing_backing_artifact(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    parser_version: str = "1.0.0",
    expected_broker_symbol: Optional[str] = None,
    expected_account_tier: str = "STANDARD",
) -> Dict[str, Any]:
    """Extract and validate authoritative swap/rollover policy from raw broker export bytes.

    CRITICAL (Directive §2):
    Removes all financing silent defaults. Must explicitly establish swap rate evidence,
    rollover policy evidence (summer/winter utc hours), and triple-swap rule evidence.
    Unknown actual swap-free status remains None (UNKNOWN), NOT False.

    MANDATORY INSTRUMENT APPLICABILITY:
    Swap values are instrument-specific. The artifact must explicitly establish that
    swap_long/swap_short, rollover, and triple-swap rule apply to expected_symbol (e.g. XAUUSD).
    Generic swap values without instrument applicability fail closed (FINANCING_EVIDENCE_MISSING).
    """
    parser_name = "parse_financing_backing_artifact"
    if not isinstance(raw_content, (bytes, bytearray)):
        raise TypeError(f"FINANCING_PARSER_ERROR: Expected raw artifact bytes, got {type(raw_content).__name__}.")

    if not raw_content or len(raw_content.strip()) < 10:
        raise ValueError("FINANCING_PARSER_ERROR: Backing artifact is empty or insufficient.")

    norm_tier = normalize_account_tier(expected_account_tier)
    if norm_tier == "STANDARD_CENT":
        if not expected_broker_symbol or not str(expected_broker_symbol).strip():
            raise ValueError(
                "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
            )
    target_broker_sym = str(expected_broker_symbol).strip() if expected_broker_symbol else None
    text = raw_content.decode("utf-8", errors="ignore").strip()

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            spec = None
            explicit_symbol_found = False

            # Check if expected_symbol or broker symbol is a key in JSON data
            for k in data.keys():
                if isinstance(data[k], dict) and _matches_expected_symbol(
                    k, expected_symbol, expected_broker_symbol=expected_broker_symbol, expected_account_tier=norm_tier
                ):
                    spec = data[k]
                    explicit_symbol_found = True
                    break

            if not explicit_symbol_found:
                other_sym_keys = [
                    k for k in data.keys()
                    if isinstance(data[k], dict) and re.match(r"^[A-Za-z0-9_/-]{3,10}$", k.strip())
                ]
                if other_sym_keys:
                    raise ValueError(
                        f"FINANCING_EVIDENCE_MISSING: Financing backing artifact symbol '{other_sym_keys[0]}' does not match expected '{expected_symbol}'."
                    )

                if "symbol" in data and data["symbol"]:
                    if _matches_expected_symbol(
                        data["symbol"], expected_symbol, expected_broker_symbol=expected_broker_symbol, expected_account_tier=norm_tier
                    ):
                        spec = data
                        explicit_symbol_found = True
                    else:
                        raise ValueError(
                            f"FINANCING_EVIDENCE_MISSING: Financing backing artifact symbol '{data['symbol']}' does not match expected '{expected_symbol}'."
                        )
                elif "symbols" in data or "instruments" in data:
                    sym_list = [str(s).upper() for s in (data.get("symbols") or data.get("instruments") or [])]
                    if any(_matches_expected_symbol(
                        s, expected_symbol, expected_broker_symbol=expected_broker_symbol, expected_account_tier=norm_tier
                    ) for s in sym_list):
                        spec = data
                        explicit_symbol_found = True
                    else:
                        raise ValueError(
                            f"FINANCING_EVIDENCE_MISSING: Financing backing artifact does not establish swap values for '{expected_symbol}'."
                        )

            if not explicit_symbol_found:
                raise ValueError(
                    f"FINANCING_EVIDENCE_MISSING: Financing backing artifact lacks instrument applicability for '{expected_symbol}'."
                )

            if isinstance(spec, dict):
                spec_tier = spec.get("account_tier") or data.get("account_tier")
                if spec_tier and normalize_account_tier(spec_tier) != norm_tier:
                    raise ValueError(f"account tier mismatch: artifact specifies '{spec_tier}' but expected '{expected_account_tier}'.")

                s_long_val = spec.get("swap_long_points") if spec.get("swap_long_points") is not None else spec.get("swap_long")
                s_short_val = spec.get("swap_short_points") if spec.get("swap_short_points") is not None else spec.get("swap_short")
                if s_long_val is None or s_short_val is None:
                    raise ValueError(
                        f"FINANCING_PARSER_ERROR: Financing backing artifact lacks swap rate evidence (swap_long / swap_short) for '{expected_symbol}'."
                    )

                r_sum_val = spec.get("rollover_summer_utc_hour")
                r_win_val = spec.get("rollover_winter_utc_hour")
                if r_sum_val is None or r_win_val is None:
                    raise ValueError(
                        "FINANCING_PARSER_ERROR: Financing backing artifact lacks rollover policy evidence (rollover_summer_utc_hour / rollover_winter_utc_hour)."
                    )

                triple_val = spec.get("triple_swap_weekday") or spec.get("triple_swap_day")
                if not triple_val:
                    raise ValueError(
                        "FINANCING_PARSER_ERROR: Financing backing artifact lacks triple-swap rule evidence (triple_swap_weekday)."
                    )

                swap_free_status = None
                if "actual_account_swap_free_status" in spec and spec["actual_account_swap_free_status"] is not None:
                    swap_free_status = parse_optional_evidence_bool(spec["actual_account_swap_free_status"])
                elif "swap_free" in spec and spec["swap_free"] is not None:
                    swap_free_status = parse_optional_evidence_bool(spec["swap_free"])

                swap_free_avail = None
                if "swap_free_available_for_account_type" in spec and spec["swap_free_available_for_account_type"] is not None:
                    swap_free_avail = parse_optional_evidence_bool(spec["swap_free_available_for_account_type"])

                derived = {
                    "symbol": expected_symbol,
                    "swap_long_points": Decimal(str(s_long_val)),
                    "swap_short_points": Decimal(str(s_short_val)),
                    "rollover_summer_utc_hour": int(r_sum_val),
                    "rollover_winter_utc_hour": int(r_win_val),
                    "triple_swap_weekday": str(triple_val).upper(),
                    "actual_account_swap_free_status": swap_free_status,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                }
                if swap_free_avail is not None:
                    derived["swap_free_available_for_account_type"] = swap_free_avail
                derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                return derived
        except json.JSONDecodeError:
            pass

    # Non-JSON: Check for explicit symbol tag or presence in text
    sym_tag = re.search(r"\bSYMBOL[:\s=]+([A-Za-z0-9/_-]+)", text, re.IGNORECASE)
    if sym_tag:
        sym = sym_tag.group(1).replace("/", "").upper()
        if not _matches_expected_symbol(sym, expected_symbol, expected_broker_symbol=target_broker_sym, expected_account_tier=norm_tier):
            raise ValueError(
                f"FINANCING_EVIDENCE_MISSING: Financing backing artifact symbol '{sym}' does not match expected '{target_broker_sym or expected_symbol}'."
            )
    else:
        ignore_words = {"DIGITS", "VOLUME", "SPREAD", "MARGIN", "SYMBOL", "POINTS", "STATUS", "TRIPLE", "POLICY", "SWAP", "LONG", "SHORT", "HOURS", "WEDNESDAY", "SUMMER", "WINTER", "ROLLOVER"}
        sym_mentions = [
            s.replace("/", "").upper() for s in re.findall(r"\b([A-Za-z0-9]{4,8}|[A-Za-z]{3}/[A-Za-z]{3,4})\b", text)
            if s.upper() not in ignore_words and s.replace("/", "").upper() not in ignore_words
        ]
        has_expected = any(
            _matches_expected_symbol(s, expected_symbol, expected_broker_symbol=target_broker_sym, expected_account_tier=norm_tier)
            for s in sym_mentions
        )
        if not has_expected:
            if norm_tier == "STANDARD_CENT":
                if not target_broker_sym:
                    raise ValueError(
                        "BROKER_SYMBOL_SCOPE_MISSING: STANDARD_CENT execution scope requires explicit expected_broker_symbol."
                    )
                target_sym = str(target_broker_sym).strip()
                has_expected = bool(re.search(rf"\b{re.escape(target_sym)}\b", text, re.IGNORECASE))
            else:
                has_expected = bool(
                    re.search(rf"\b{re.escape(expected_symbol)}\b", text, re.IGNORECASE)
                    or (expected_symbol.upper() == "XAUUSD" and re.search(r"\b(XAU/USD|GOLD)\b", text, re.IGNORECASE))
                )
        if sym_mentions and not has_expected:
            raise ValueError(
                f"FINANCING_EVIDENCE_MISSING: Financing backing artifact symbol '{sym_mentions[0]}' does not match expected '{target_broker_sym or expected_symbol}'."
            )
        if not has_expected and not sym_mentions:
            raise ValueError(
                f"FINANCING_EVIDENCE_MISSING: Financing backing artifact lacks instrument applicability for '{target_broker_sym or expected_symbol}'."
            )

    # Try pipe format e.g. "SYMBOL:XAUUSD|SWAP_LONG:-34.80|SWAP_SHORT:12.40|ROLLOVER_SUMMER:21|ROLLOVER_WINTER:22|TRIPLE:WEDNESDAY"
    if "|" in text or ":" in text:
        kv = {}
        for token in re.split(r"[|\n;]", text):
            if ":" in token:
                k, v = token.split(":", 1)
                kv[k.strip().upper()] = v.strip()
            elif "=" in token:
                k, v = token.split("=", 1)
                kv[k.strip().upper()] = v.strip()

        if ("SWAP_LONG" in kv or "SWAP_LONG_POINTS" in kv) and ("SWAP_SHORT" in kv or "SWAP_SHORT_POINTS" in kv):
            s_long_raw = kv.get("SWAP_LONG") or kv.get("SWAP_LONG_POINTS")
            s_short_raw = kv.get("SWAP_SHORT") or kv.get("SWAP_SHORT_POINTS")

            r_sum_raw = kv.get("ROLLOVER_SUMMER_UTC_HOUR") or kv.get("ROLLOVER_SUMMER")
            r_win_raw = kv.get("ROLLOVER_WINTER_UTC_HOUR") or kv.get("ROLLOVER_WINTER")
            if not (r_sum_raw and r_win_raw):
                raise ValueError(
                    "FINANCING_PARSER_ERROR: Financing backing artifact lacks rollover policy evidence (rollover summer/winter hours)."
                )

            triple_raw = None
            for k in ("TRIPLE_SWAP_WEEKDAY", "TRIPLE_SWAP", "TRIPLE", "WED"):
                if k in kv:
                    triple_raw = "WEDNESDAY" if "WED" in str(kv[k]).upper() else str(kv[k]).upper()
                    break
            if not triple_raw:
                raise ValueError(
                    "FINANCING_PARSER_ERROR: Financing backing artifact lacks triple-swap rule evidence."
                )

            swap_free_status = None
            if "ACTUAL_ACCOUNT_SWAP_FREE_STATUS" in kv:
                swap_free_status = parse_optional_evidence_bool(kv["ACTUAL_ACCOUNT_SWAP_FREE_STATUS"])
            elif "SWAP_FREE" in kv:
                swap_free_status = parse_optional_evidence_bool(kv["SWAP_FREE"])

            swap_free_avail = None
            if "SWAP_FREE_AVAILABLE_FOR_ACCOUNT_TYPE" in kv:
                swap_free_avail = parse_optional_evidence_bool(kv["SWAP_FREE_AVAILABLE_FOR_ACCOUNT_TYPE"])

            derived = {
                "symbol": expected_symbol,
                "swap_long_points": Decimal(s_long_raw),
                "swap_short_points": Decimal(s_short_raw),
                "rollover_summer_utc_hour": int(r_sum_raw),
                "rollover_winter_utc_hour": int(r_win_raw),
                "triple_swap_weekday": triple_raw,
                "actual_account_swap_free_status": swap_free_status,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            if swap_free_avail is not None:
                derived["swap_free_available_for_account_type"] = swap_free_avail
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived

    # Regex search: must explicitly match all required fields
    long_m = re.search(r"swap_long(?:_points)?[:\s=]+(-?[\d.]+)", text, re.IGNORECASE)
    short_m = re.search(r"swap_short(?:_points)?[:\s=]+(-?[\d.]+)", text, re.IGNORECASE)
    r_sum_m = re.search(r"rollover_summer(?:_utc_hour)?[:\s=]+(\d+)", text, re.IGNORECASE)
    r_win_m = re.search(r"rollover_winter(?:_utc_hour)?[:\s=]+(\d+)", text, re.IGNORECASE)
    triple_m = re.search(r"triple(?:_swap)?(?:_weekday)?[:\s=]+([A-Za-z]+)", text, re.IGNORECASE)

    if long_m and short_m:
        if not (r_sum_m and r_win_m):
            raise ValueError(
                "FINANCING_PARSER_ERROR: Financing backing artifact lacks rollover policy evidence (rollover summer/winter hours)."
            )
        if not triple_m:
            raise ValueError(
                "FINANCING_PARSER_ERROR: Financing backing artifact lacks triple-swap rule evidence."
            )

        swap_free_status = None
        sf_m = re.search(r"(?:actual_account_)?swap_free(?:_status)?[:\s=]+([A-Za-z0-9]+)", text, re.IGNORECASE)
        if sf_m:
            swap_free_status = parse_optional_evidence_bool(sf_m.group(1))

        swap_free_avail = None
        sf_avail_m = re.search(r"swap_free_available(?:_for_account_type)?[:\s=]+([A-Za-z0-9]+)", text, re.IGNORECASE)
        if sf_avail_m:
            swap_free_avail = parse_optional_evidence_bool(sf_avail_m.group(1))

        derived = {
            "symbol": expected_symbol,
            "swap_long_points": Decimal(long_m.group(1)),
            "swap_short_points": Decimal(short_m.group(1)),
            "rollover_summer_utc_hour": int(r_sum_m.group(1)),
            "rollover_winter_utc_hour": int(r_win_m.group(1)),
            "triple_swap_weekday": triple_m.group(1).upper(),
            "actual_account_swap_free_status": swap_free_status,
            "parser_name": parser_name,
            "parser_version": parser_version,
        }
        if swap_free_avail is not None:
            derived["swap_free_available_for_account_type"] = swap_free_avail
        derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
        return derived

    raise ValueError(
        f"FINANCING_PARSER_ERROR: Backing artifact does not establish swap values for '{expected_symbol}'."
    )

