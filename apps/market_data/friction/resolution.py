"""Point-in-time deterministic friction model resolver.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance (Directive 13):
- Resolves effective active friction model version for an exact target execution tuple:
    (venue, symbol, account_tier, legal_entity_code)
- Queries append-only FrictionModelActivation records point-in-time:
    * known_at <= as_of
    * effective_from <= as_of
    * effective_to is None or effective_to > as_of
    * activation_status == ACTIVE
- Guarantees:
    * Before new activation known/effective -> previous model resolved.
    * After new activation -> newer model resolved.
    * Future activations never leak backward into historical evaluations.
    * Old activation and evidence records remain strictly immutable.
"""
from datetime import datetime, timezone
from typing import Optional, Tuple
from django.db import models

from apps.market_data.models import (
    FrictionActivationStatus,
    FrictionModelActivation,
    FrictionModelVersion,
)


def resolve_friction_model(
    as_of: datetime,
    venue: Optional[str] = None,
    symbol: Optional[str] = None,
    account_tier: Optional[str] = None,
    legal_entity_code: Optional[str] = None,
) -> Optional[FrictionModelVersion]:
    """Resolve active FrictionModelVersion at point-in-time as_of for exact target scope."""
    res = resolve_friction_model_activation(
        as_of=as_of,
        venue=venue,
        symbol=symbol,
        account_tier=account_tier,
        legal_entity_code=legal_entity_code,
    )
    if res is not None:
        return res[0]
    return None


def resolve_friction_model_activation(
    as_of: datetime,
    venue: Optional[str] = None,
    symbol: Optional[str] = None,
    account_tier: Optional[str] = None,
    legal_entity_code: Optional[str] = None,
) -> Optional[Tuple[FrictionModelVersion, FrictionModelActivation]]:
    """Resolve (model_version, activation) active at point-in-time as_of for exact target scope."""
    if as_of.tzinfo is None:
        raise ValueError(f"resolve_friction_model: Naive as_of timestamp '{as_of}' rejected. UTC required.")

    from django.conf import settings

    target_venue = (venue or getattr(settings, "XAUUSD_EXECUTION_VENUE", "EXNESS")).upper()
    target_symbol = (symbol or "XAUUSD").upper()
    target_account_tier = (account_tier or getattr(settings, "XAUUSD_EXECUTION_ACCOUNT_TIER", "STANDARD")).upper()
    target_legal_entity = legal_entity_code or getattr(settings, "XAUUSD_EXECUTION_LEGAL_ENTITY_CODE", None)

    # Fail closed if legal_entity_code or account_tier is not established
    if not target_legal_entity or not target_account_tier:
        return None

    utc_as_of = as_of.astimezone(timezone.utc)

    qs = FrictionModelActivation.objects.select_related(
        "friction_model_version",
        "friction_model_version__legal_entity_source_snapshot",
        "friction_model_version__contract_spec_source_snapshot",
        "friction_model_version__fee_schedule_source_snapshot",
        "friction_model_version__swap_spec_source_snapshot",
    ).filter(
        activation_status=FrictionActivationStatus.ACTIVE,
        known_at__lte=utc_as_of,
        effective_from__lte=utc_as_of,
        friction_model_version__venue=target_venue,
        friction_model_version__symbol=target_symbol,
        friction_model_version__account_tier=target_account_tier,
        friction_model_version__legal_entity_code=target_legal_entity.upper(),
    ).filter(
        models.Q(effective_to__isnull=True) | models.Q(effective_to__gt=utc_as_of)
    )

    # Newer activations supersede earlier ones without mutating old records
    activation = qs.order_by("-effective_from", "-known_at").first()
    if activation:
        return activation.friction_model_version, activation
    return None

