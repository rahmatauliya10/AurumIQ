"""Volume indicator calculations (Volume Ratio, Volume Z-Score, Volume Semantics)."""
from decimal import Decimal
from typing import Optional, Sequence
import math

from engine.core.types import CandleData, VolumeEvidenceType, VolumeFeatureResult


def calculate_volume_features(candles: Sequence[CandleData], period: int = 20) -> VolumeFeatureResult:
    """
    Extract volume features with strict semantic evidence validation (XAU-P2-01).
    Guarantees:
      - UNAVAILABLE volume produces is_usable=False and ratio=None, zscore=None.
      - Any non-positive (<= 0), missing, or UNAVAILABLE bar anywhere in the rolling window
        disqualifies the entire window as UNAVAILABLE.
      - Unknown/invalid runtime evidence labels (e.g. "FABRICATED_VOLUME") return is_usable=False
        with reason="INVALID_VOLUME_EVIDENCE".
      - Mixing incompatible volume types across the rolling window produces is_usable=False
        with reason="MIXED_VOLUME_SEMANTICS".
      - PROXY_VOLUME, TICK_VOLUME, REAL_VOLUME are explicitly labeled and verified.
      - Zero volume fabrication or silent assumption.
    """
    if not candles:
        return VolumeFeatureResult(
            evidence_type=VolumeEvidenceType.UNAVAILABLE,
            is_usable=False,
            ratio=None,
            zscore=None,
            reason="EMPTY_CANDLES",
        )

    # 1. Check sufficient lookback
    if len(candles) < period or period <= 0:
        curr_cand = candles[-1]
        ev = curr_cand.volume_evidence if isinstance(curr_cand.volume_evidence, VolumeEvidenceType) else VolumeEvidenceType.UNAVAILABLE
        return VolumeFeatureResult(
            evidence_type=ev,
            is_usable=False,
            ratio=None,
            zscore=None,
            reason="INSUFFICIENT_LOOKBACK",
        )

    window = candles[-period:]
    current_candle = window[-1]

    # 2. Strict Enum Validation across entire window
    for c in window:
        if c.volume_evidence not in (
            VolumeEvidenceType.REAL_VOLUME,
            VolumeEvidenceType.TICK_VOLUME,
            VolumeEvidenceType.PROXY_VOLUME,
            VolumeEvidenceType.UNAVAILABLE,
        ) or not isinstance(c.volume_evidence, VolumeEvidenceType):
            return VolumeFeatureResult(
                evidence_type=VolumeEvidenceType.UNAVAILABLE,
                is_usable=False,
                ratio=None,
                zscore=None,
                reason="INVALID_VOLUME_EVIDENCE",
            )

    # 3. Validate entire window for non-positive or unavailable volume
    for c in window:
        if (
            c.volume_evidence == VolumeEvidenceType.UNAVAILABLE
            or c.volume is None
            or c.volume <= Decimal("0")
        ):
            return VolumeFeatureResult(
                evidence_type=VolumeEvidenceType.UNAVAILABLE,
                is_usable=False,
                ratio=None,
                zscore=None,
                reason="UNAVAILABLE_VOLUME",
            )

    # 4. Check for mixed volume semantics across the rolling window
    unique_evidence_types = {c.volume_evidence for c in window}
    if len(unique_evidence_types) > 1:
        return VolumeFeatureResult(
            evidence_type=current_candle.volume_evidence,
            is_usable=False,
            ratio=None,
            zscore=None,
            reason="MIXED_VOLUME_SEMANTICS",
        )

    # 5. Valid homogeneous volume window calculation
    curr_evidence = current_candle.volume_evidence
    volumes = [c.volume for c in window]
    ratio = calculate_volume_ratio(volumes, period)
    zscore = calculate_volume_zscore(volumes, period)

    return VolumeFeatureResult(
        evidence_type=curr_evidence,
        is_usable=True,
        ratio=ratio,
        zscore=zscore,
        reason="VALID",
    )



def calculate_volume_ratio(volumes: Sequence[Decimal], period: int = 20) -> Optional[float]:
    """
    Calculate current bar volume ratio relative to its period-SMA.
    Formula: Volume_t / SMA(Volume, period)
    """
    if len(volumes) < period or period <= 0:
        return None

    window = volumes[-period:]
    mean_vol = sum(window) / Decimal(str(period))

    if mean_vol <= 0:
        return 1.0

    current_vol = volumes[-1]
    ratio = current_vol / mean_vol
    return float(round(ratio, 2))


def calculate_volume_zscore(volumes: Sequence[Decimal], period: int = 20) -> Optional[float]:
    """
    Calculate Volume Z-Score relative to its recent window distribution:
    Formula: (Volume_t - Mean) / StdDev
    """
    if len(volumes) < period or period <= 0:
        return None

    window = [float(v) for v in volumes[-period:]]
    mean = sum(window) / float(period)
    variance = sum((x - mean) ** 2 for x in window) / float(period)
    std_dev = math.sqrt(variance)

    if std_dev == 0.0:
        return 0.0

    current_vol = float(volumes[-1])
    zscore = (current_vol - mean) / std_dev
    return float(round(zscore, 2))

