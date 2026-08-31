"""
Phase 3B Experimental Spectral & Cycle Research Artifact Architecture.

Provides immutable research artifact and audit provenance contracts for Phase 3B.
Point-in-time safe, pure Python, zero Django imports, zero subprocess/network calls.
"""
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Optional

from engine.core.types import (
    AcfResult,
    CycleReliabilityResult,
    FftResult,
    HilbertResult,
    WaveletResult,
)
from engine.cycles.experimental.profile import (
    ResearchCalibrationStatus,
    _deep_freeze,
)


@dataclass(frozen=True)
class Cycle3BResearchProvenance:
    """Audit provenance tracking for empirical spectral research artifacts."""
    instrument: str
    provider: str
    timeframe: str
    data_start: datetime
    data_end: datetime
    as_of: datetime
    raw_observations: int
    effective_n: float
    code_revision: str
    data_fingerprint: str
    algorithm_version: str = "3.1.0-3B"
    generated_at: Optional[datetime] = None
    pit_safe: bool = False

    def __post_init__(self):
        """Strict validation of chronological causality, observation boundaries, and explicit auditability."""
        if self.data_start > self.data_end:
            raise ValueError(
                f"Provenance data_start ({self.data_start}) cannot be after data_end ({self.data_end})."
            )
        if self.data_end > self.as_of:
            raise ValueError(
                f"Provenance data_end ({self.data_end}) cannot be after as_of ({self.as_of})."
            )
        if self.raw_observations < 0:
            raise ValueError("raw_observations cannot be negative.")
        if self.effective_n < 0.0:
            raise ValueError("effective_n cannot be negative.")
        if not self.code_revision or not self.code_revision.strip():
            raise ValueError("code_revision must be explicitly provided.")
        if not self.data_fingerprint or not self.data_fingerprint.strip():
            raise ValueError("data_fingerprint must be explicitly provided.")


@dataclass(frozen=True)
class Cycle3BResearchArtifact:
    """
    Immutable consolidated spectral research artifact for Phase 3B.
    Captures descriptive spectral results and candidate reliability metrics
    with defensive recursive immutability.
    """
    provenance: Cycle3BResearchProvenance
    acf_result: Optional[AcfResult] = None
    fft_result: Optional[FftResult] = None
    wavelet_result: Optional[WaveletResult] = None
    hilbert_result: Optional[HilbertResult] = None
    reliability_candidate: Optional[CycleReliabilityResult] = None
    algorithm_config: Mapping[str, Any] = field(default_factory=dict)
    status: ResearchCalibrationStatus = ResearchCalibrationStatus.CANDIDATE_NOT_FROZEN

    def __post_init__(self):
        """Enforce strict recursive deep immutability across all dictionary fields."""
        if self.algorithm_config is not None:
            object.__setattr__(
                self,
                "algorithm_config",
                _deep_freeze(dict(self.algorithm_config)),
            )
