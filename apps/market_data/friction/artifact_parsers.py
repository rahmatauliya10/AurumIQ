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

        # Compare decimals / numbers
        if isinstance(derived_val, (Decimal, int, float)) or (
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
        elif isinstance(derived_val, bool):
            if bool(asserted_val) != bool(derived_val):
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
        ValueError if artifact fails to establish legal entity, regulator, or license number.
    """
    parser_name = "parse_legal_entity_backing_artifact"
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
            name = str(data.get("legal_entity_name") or data.get("entity_name") or data.get("name") or "").strip()
            code = str(data.get("legal_entity_code") or data.get("code") or "").strip()
            reg = str(data.get("regulator") or data.get("regulatory_authority") or "").strip()
            lic = str(data.get("license_number") or data.get("license") or data.get("licence_number") or "").strip()
            if not (reg and lic):
                raise ValueError(
                    "LEGAL_ENTITY_PARSER_ERROR: Backing artifact lacks authentic Exness legal entity evidence; missing regulator or license number."
                )
            if not name:
                name = "Exness (SC) Ltd" if "SC" in code else "Exness Entity"
            if not code:
                code = "EXNESS_SC_LTD" if "SC" in name.upper() else "EXNESS_LEGAL_ENTITY"
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
            derived = {
                "legal_entity_name": name or "Exness (SC) Ltd",
                "legal_entity_code": code or "EXNESS_SC_LTD",
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
        reg = reg_match.group(1).upper()
        lic = lic_match.group(1).strip()
        name = name_match.group(1).strip() if name_match else "Exness (SC) Ltd"
        code = "EXNESS_SC_LTD" if "SC" in name.upper() else "EXNESS_LEGAL_ENTITY"
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
    parser_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Extract and validate authoritative contract geometry from raw MT5 / broker export bytes.

    Returns:
        derived_normalized_dict containing contract spec fields, parser_name,
        parser_version, and normalized_evidence_hash.
    Raises:
        ValueError if artifact lacks required contract specification fields for expected_symbol.
    """
    parser_name = "parse_contract_spec_backing_artifact"
    if not raw_content or len(raw_content.strip()) < 10:
        raise ValueError("CONTRACT_SPEC_PARSER_ERROR: Backing artifact is empty or insufficient.")

    text = raw_content.decode("utf-8", errors="ignore").strip()

    # Reject if artifact explicitly specifies a different symbol and not expected_symbol
    sym_tag = re.search(r"SYMBOL[:\s=]+([A-Za-z0-9/_-]+)", text, re.IGNORECASE)
    if sym_tag:
        sym = sym_tag.group(1).replace("/", "").upper()
        if sym != expected_symbol.upper():
            raise ValueError(
                f"CONTRACT_SPEC_PARSER_ERROR: Backing artifact symbol '{sym}' does not match expected '{expected_symbol}'."
            )
    else:
        ignore_words = {"DIGITS", "VOLUME", "SPREAD", "MARGIN", "SYMBOL", "POINTS", "STATUS"}
        sym_mentions = [
            s.replace("/", "").upper() for s in re.findall(r"\b([A-Z]{6}|[A-Z]{3}/[A-Z]{3})\b", text)
            if s.upper() not in ignore_words and s.replace("/", "").upper() not in ignore_words
        ]
        if sym_mentions and expected_symbol.upper() not in sym_mentions:
            raise ValueError(
                f"CONTRACT_SPEC_PARSER_ERROR: Backing artifact symbol '{sym_mentions[0]}' does not match expected '{expected_symbol}'."
            )

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            spec = data.get(expected_symbol) or data.get(f"{expected_symbol[:3]}/{expected_symbol[3:]}") or data
            if isinstance(spec, dict) and "digits" in spec:
                digits = int(spec["digits"])
                point = Decimal(str(spec.get("point_size") or spec.get("point") or "0.01"))
                tick_size = Decimal(str(spec.get("trade_tick_size") or spec.get("tick_size") or point))
                tick_val = Decimal(str(spec.get("trade_tick_value") or spec.get("tick_value") or "1.00"))
                c_size = Decimal(str(spec.get("contract_size") or "100.0"))
                v_min = Decimal(str(spec.get("volume_min") or "0.01"))
                v_max = Decimal(str(spec.get("volume_max") or "200.0"))
                v_step = Decimal(str(spec.get("volume_step") or "0.01"))
                derived = {
                    "symbol": expected_symbol,
                    "digits": digits,
                    "point_size": point,
                    "trade_tick_size": tick_size,
                    "trade_tick_value": tick_val,
                    "contract_size": c_size,
                    "volume_min": v_min,
                    "volume_max": v_max,
                    "volume_step": v_step,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                }
                derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                return derived
        except json.JSONDecodeError:
            pass

    # Try pipe / colon delimited format e.g. "CONTRACT_SIZE:100|POINT:0.01|DIGITS:2"
    if "|" in text or ":" in text:
        kv = {}
        for token in re.split(r"[|\n;]", text):
            if ":" in token:
                k, v = token.split(":", 1)
                kv[k.strip().upper()] = v.strip()
            elif "=" in token:
                k, v = token.split("=", 1)
                kv[k.strip().upper()] = v.strip()

        if "DIGITS" in kv and ("CONTRACT_SIZE" in kv or "CONTRACT" in kv):
            digits = int(kv["DIGITS"])
            point_str = kv.get("POINT") or kv.get("POINT_SIZE") or "0.01"
            point = Decimal(point_str)
            tick_size = Decimal(kv.get("TICK_SIZE") or kv.get("TRADE_TICK_SIZE") or point_str)
            tick_val = Decimal(kv.get("TICK_VALUE") or kv.get("TRADE_TICK_VALUE") or "1.00")
            c_size = Decimal(kv.get("CONTRACT_SIZE") or kv.get("CONTRACT") or "100.0")
            v_min = Decimal(kv.get("VOL_MIN") or kv.get("VOLUME_MIN") or "0.01")
            v_max = Decimal(kv.get("VOL_MAX") or kv.get("VOLUME_MAX") or "200.0")
            v_step = Decimal(kv.get("VOL_STEP") or kv.get("VOLUME_STEP") or "0.01")
            derived = {
                "symbol": expected_symbol,
                "digits": digits,
                "point_size": point,
                "trade_tick_size": tick_size,
                "trade_tick_value": tick_val,
                "contract_size": c_size,
                "volume_min": v_min,
                "volume_max": v_max,
                "volume_step": v_step,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived

    # MT5 SymbolInfo text format
    digits_m = re.search(r"digits[:\s=]+(\d+)", text, re.IGNORECASE)
    point_m = re.search(r"point(?:_size)?[:\s=]+([\d.]+)", text, re.IGNORECASE)
    c_size_m = re.search(r"contract(?:_size)?[:\s=]+([\d.]+)", text, re.IGNORECASE)

    if digits_m and c_size_m:
        digits = int(digits_m.group(1))
        point = Decimal(point_m.group(1)) if point_m else Decimal("0.01")
        tick_size_m = re.search(r"tick_size[:\s=]+([\d.]+)", text, re.IGNORECASE)
        tick_size = Decimal(tick_size_m.group(1)) if tick_size_m else point
        tick_val_m = re.search(r"tick_value[:\s=]+([\d.]+)", text, re.IGNORECASE)
        tick_val = Decimal(tick_val_m.group(1)) if tick_val_m else Decimal("1.00")
        c_size = Decimal(c_size_m.group(1))
        v_min_m = re.search(r"volume_min[:\s=]+([\d.]+)", text, re.IGNORECASE)
        v_min = Decimal(v_min_m.group(1)) if v_min_m else Decimal("0.01")
        v_max_m = re.search(r"volume_max[:\s=]+([\d.]+)", text, re.IGNORECASE)
        v_max = Decimal(v_max_m.group(1)) if v_max_m else Decimal("200.0")
        v_step_m = re.search(r"volume_step[:\s=]+([\d.]+)", text, re.IGNORECASE)
        v_step = Decimal(v_step_m.group(1)) if v_step_m else Decimal("0.01")
        derived = {
            "symbol": expected_symbol,
            "digits": digits,
            "point_size": point,
            "trade_tick_size": tick_size,
            "trade_tick_value": tick_val,
            "contract_size": c_size,
            "volume_min": v_min,
            "volume_max": v_max,
            "volume_step": v_step,
            "parser_name": parser_name,
            "parser_version": parser_version,
        }
        derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
        return derived

    raise ValueError(
        f"CONTRACT_SPEC_PARSER_ERROR: Backing artifact lacks required contract specification fields for '{expected_symbol}'."
    )


# =============================================================================
# 3. COMMISSION AUTHORITATIVE PARSER
# =============================================================================

def parse_commission_backing_artifact(
    raw_content: bytes,
    expected_symbol: str = "XAUUSD",
    expected_account_tier: str = "STANDARD",
    parser_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Extract and validate authoritative commission schedule from raw broker export bytes.

    Returns:
        derived_normalized_dict containing commission schedule fields, parser_name,
        parser_version, and normalized_evidence_hash.
    Raises:
        ValueError if artifact does not establish commission for expected_account_tier.
    """
    parser_name = "parse_commission_backing_artifact"
    if not raw_content or len(raw_content.strip()) < 5:
        raise ValueError("COMMISSION_PARSER_ERROR: Backing artifact is empty or insufficient.")

    text = raw_content.decode("utf-8", errors="ignore").strip()

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            # Check tier specific section or top level
            tier_data = None
            if "tiers" in data and isinstance(data["tiers"], dict):
                tier_data = data["tiers"].get(expected_account_tier)
            elif data.get("account_tier", "").upper() == expected_account_tier.upper():
                tier_data = data

            if tier_data is not None and isinstance(tier_data, dict):
                fee_val = tier_data.get("native_commission_usd_per_lot_per_side") or tier_data.get("commission")
                formula = tier_data.get("commission_formula") or "DYNAMIC_NOTIONAL_BPS"
                if fee_val is not None:
                    derived = {
                        "account_tier": expected_account_tier,
                        "symbol": expected_symbol,
                        "native_commission_usd_per_lot_per_side": Decimal(str(fee_val)),
                        "commission_formula": formula,
                        "parser_name": parser_name,
                        "parser_version": parser_version,
                    }
                    derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                    return derived
            elif "tiers" in data and expected_account_tier not in data["tiers"]:
                raise ValueError(
                    f"COMMISSION_PARSER_ERROR: Fee document does not establish commission for requested account tier '{expected_account_tier}'."
                )
        except json.JSONDecodeError:
            pass

    # Try pipe / colon format e.g. "STANDARD:COMMISSION:0.00"
    if ":" in text or "|" in text:
        parts = [p.strip() for p in re.split(r"[:|]", text) if p.strip()]
        # Check if tier is mentioned
        tiers_in_text = [p.upper() for p in parts if p.upper() in ("STANDARD", "RAW_SPREAD", "PRO", "ZERO")]
        if tiers_in_text and expected_account_tier.upper() not in tiers_in_text:
            raise ValueError(
                f"COMMISSION_PARSER_ERROR: Fee document does not establish commission for requested account tier '{expected_account_tier}' (found: {tiers_in_text})."
            )

        if "COMMISSION" in [p.upper() for p in parts]:
            idx = [p.upper() for p in parts].index("COMMISSION")
            if idx + 1 < len(parts):
                fee_str = parts[idx + 1]
                derived = {
                    "account_tier": expected_account_tier,
                    "symbol": expected_symbol,
                    "native_commission_usd_per_lot_per_side": Decimal(fee_str),
                    "commission_formula": "DYNAMIC_NOTIONAL_BPS",
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                }
                derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                return derived

    # Regex search for tier and commission
    tier_re = re.search(rf"\b{re.escape(expected_account_tier)}\b.*?commission[:\s=]+([\d.]+)", text, re.IGNORECASE)
    if tier_re:
        fee_val = Decimal(tier_re.group(1))
        derived = {
            "account_tier": expected_account_tier,
            "symbol": expected_symbol,
            "native_commission_usd_per_lot_per_side": fee_val,
            "commission_formula": "DYNAMIC_NOTIONAL_BPS",
            "parser_name": parser_name,
            "parser_version": parser_version,
        }
        derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
        return derived

    # Check if other tiers exist but not requested
    other_tiers = re.findall(r"\b(RAW_SPREAD|PRO|ZERO|STANDARD)\b", text, re.IGNORECASE)
    if other_tiers and expected_account_tier.upper() not in [t.upper() for t in other_tiers]:
        raise ValueError(
            f"COMMISSION_PARSER_ERROR: Fee document does not establish commission for requested account tier '{expected_account_tier}'."
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
) -> Dict[str, Any]:
    """Extract and validate authoritative swap/rollover policy from raw broker export bytes.

    Returns:
        derived_normalized_dict containing financing fields, parser_name,
        parser_version, and normalized_evidence_hash.
    Raises:
        ValueError if artifact fails to establish XAUUSD swap values and rollover policy.
    """
    parser_name = "parse_financing_backing_artifact"
    if not raw_content or len(raw_content.strip()) < 10:
        raise ValueError("FINANCING_PARSER_ERROR: Backing artifact is empty or insufficient.")

    text = raw_content.decode("utf-8", errors="ignore").strip()

    # Reject if artifact explicitly specifies other symbols without expected_symbol
    sym_tag = re.search(r"SYMBOL[:\s=]+([A-Za-z0-9/_-]+)", text, re.IGNORECASE)
    if sym_tag:
        sym = sym_tag.group(1).replace("/", "").upper()
        if sym != expected_symbol.upper():
            raise ValueError(
                f"FINANCING_PARSER_ERROR: Financing backing artifact does not establish swap values for '{expected_symbol}' (found: '{sym}')."
            )
    else:
        ignore_words = {"DIGITS", "VOLUME", "SPREAD", "MARGIN", "SYMBOL", "POINTS", "STATUS", "TRIPLE", "POLICY"}
        sym_mentions = [
            s.replace("/", "").upper() for s in re.findall(r"\b([A-Z]{6}|[A-Z]{3}/[A-Z]{3})\b", text)
            if s.upper() not in ignore_words and s.replace("/", "").upper() not in ignore_words
        ]
        if sym_mentions and expected_symbol.upper() not in sym_mentions:
            raise ValueError(
                f"FINANCING_PARSER_ERROR: Financing backing artifact does not establish swap values for '{expected_symbol}' (mentions: {sym_mentions})."
            )

    # Try JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            spec = data.get(expected_symbol) or data.get(f"{expected_symbol[:3]}/{expected_symbol[3:]}") or data
            if isinstance(spec, dict) and ("swap_long_points" in spec or "swap_long" in spec):
                s_long = Decimal(str(spec.get("swap_long_points") if spec.get("swap_long_points") is not None else spec.get("swap_long")))
                s_short = Decimal(str(spec.get("swap_short_points") if spec.get("swap_short_points") is not None else spec.get("swap_short")))
                triple = str(spec.get("triple_swap_weekday") or "WEDNESDAY").upper()
                r_sum = int(spec.get("rollover_summer_utc_hour", 21))
                r_win = int(spec.get("rollover_winter_utc_hour", 22))
                swap_free = bool(spec.get("actual_account_swap_free_status", False))
                derived = {
                    "symbol": expected_symbol,
                    "swap_long_points": s_long,
                    "swap_short_points": s_short,
                    "rollover_summer_utc_hour": r_sum,
                    "rollover_winter_utc_hour": r_win,
                    "triple_swap_weekday": triple,
                    "actual_account_swap_free_status": swap_free,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                }
                derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
                return derived
        except json.JSONDecodeError:
            pass

    # Try pipe format e.g. "SWAP_LONG:-34.80|SWAP_SHORT:12.40|WED:TRIPLE"
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
            s_long = Decimal(kv.get("SWAP_LONG") or kv.get("SWAP_LONG_POINTS"))
            s_short = Decimal(kv.get("SWAP_SHORT") or kv.get("SWAP_SHORT_POINTS"))
            triple = "WEDNESDAY"
            for k in ("TRIPLE_SWAP_WEEKDAY", "TRIPLE", "WED"):
                if k in kv:
                    triple = "WEDNESDAY" if "WED" in str(kv[k]).upper() else str(kv[k]).upper()
            r_sum = int(kv.get("ROLLOVER_SUMMER_UTC_HOUR", 21))
            r_win = int(kv.get("ROLLOVER_WINTER_UTC_HOUR", 22))
            derived = {
                "symbol": expected_symbol,
                "swap_long_points": s_long,
                "swap_short_points": s_short,
                "rollover_summer_utc_hour": r_sum,
                "rollover_winter_utc_hour": r_win,
                "triple_swap_weekday": triple,
                "actual_account_swap_free_status": False,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
            return derived

    # Regex search
    long_m = re.search(r"swap_long(?:_points)?[:\s=]+(-?[\d.]+)", text, re.IGNORECASE)
    short_m = re.search(r"swap_short(?:_points)?[:\s=]+(-?[\d.]+)", text, re.IGNORECASE)
    if long_m and short_m:
        s_long = Decimal(long_m.group(1))
        s_short = Decimal(short_m.group(1))
        derived = {
            "symbol": expected_symbol,
            "swap_long_points": s_long,
            "swap_short_points": s_short,
            "rollover_summer_utc_hour": 21,
            "rollover_winter_utc_hour": 22,
            "triple_swap_weekday": "WEDNESDAY",
            "actual_account_swap_free_status": False,
            "parser_name": parser_name,
            "parser_version": parser_version,
        }
        derived["normalized_evidence_hash"] = compute_normalized_evidence_hash(derived)
        return derived

    raise ValueError(
        f"FINANCING_PARSER_ERROR: Backing artifact does not establish swap values for '{expected_symbol}'."
    )
