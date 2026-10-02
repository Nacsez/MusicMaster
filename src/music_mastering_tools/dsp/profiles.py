"""Deterministic, independently weighted multi-reference profile blending.

The blend operates on analysis products, never on unrelated waveforms or on
already-limited renders. Loud-section RMS and magnitude spectra are positive
amplitude quantities, so their weighted geometric mean is equivalent to an
arithmetic mean in amplitude decibels.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..config import JobConfig
from ..manifest import fingerprint_file
from .constants import (
    LEVEL_COMBINATION_RULE,
    MAX_WEIGHTED_REFERENCES,
    PROFILE_ALGORITHM_VERSION,
    SPECTRAL_COMBINATION_RULE,
)

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class RequestedReference:
    """One selected reference occurrence before duplicate coalescing."""

    index: int
    path: str
    label: str | None
    content_sha256: str
    requested_level_weight: float
    requested_frequency_weight: float
    normalized_level_weight: float
    normalized_frequency_weight: float

    def to_details(self) -> dict[str, object]:
        return {
            "index": self.index,
            "path": self.path,
            "label": self.label,
            "content_sha256": self.content_sha256,
            "requested_level_weight": self.requested_level_weight,
            "requested_frequency_weight": self.requested_frequency_weight,
            "normalized_level_weight": self.normalized_level_weight,
            "normalized_frequency_weight": self.normalized_frequency_weight,
            "effective_for_level": self.normalized_level_weight > 0,
            "effective_for_frequency": self.normalized_frequency_weight > 0,
            "effective": (self.normalized_level_weight > 0 or self.normalized_frequency_weight > 0),
        }


@dataclass(frozen=True, slots=True)
class ReferenceGroup:
    """Content-identical references coalesced into one analysis operation."""

    content_sha256: str
    path: str
    source_indices: tuple[int, ...]
    level_weight: float
    frequency_weight: float

    @property
    def effective(self) -> bool:
        return self.level_weight > 0 or self.frequency_weight > 0

    def to_details(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "source_indices": list(self.source_indices),
            "level_weight": self.level_weight,
            "frequency_weight": self.frequency_weight,
            "effective_for_level": self.level_weight > 0,
            "effective_for_frequency": self.frequency_weight > 0,
            "effective": self.effective,
        }


@dataclass(frozen=True, slots=True)
class ReferenceInventory:
    """Selected occurrences plus deterministically sorted content groups."""

    requested: tuple[RequestedReference, ...]
    groups: tuple[ReferenceGroup, ...]

    @property
    def duplicate_group_count(self) -> int:
        return sum(len(group.source_indices) > 1 for group in self.groups)

    def group_for_index(self, index: int) -> ReferenceGroup:
        for group in self.groups:
            if index in group.source_indices:
                return group
        raise KeyError(index)

    def to_details(self) -> dict[str, object]:
        requested: list[dict[str, object]] = []
        for item in self.requested:
            details = item.to_details()
            details["effective_group_sha256"] = self.group_for_index(item.index).content_sha256
            requested.append(details)
        return {
            "requested": requested,
            "effective_groups": [group.to_details() for group in self.groups],
            "duplicate_group_count": self.duplicate_group_count,
        }


@dataclass(frozen=True, slots=True)
class ReferenceProfile:
    """Analysis products for one unique reference-content group."""

    group: ReferenceGroup
    match_rms: float
    final_amplitude_coefficient: float
    mid_spectrum: FloatArray | None
    side_spectrum: FloatArray | None

    def __post_init__(self) -> None:
        _finite_positive(self.match_rms, "reference profile match_rms")
        _finite_positive(
            self.final_amplitude_coefficient,
            "reference profile final_amplitude_coefficient",
        )
        if self.group.frequency_weight > 0:
            if self.mid_spectrum is None or self.side_spectrum is None:
                raise ValueError(
                    "frequency-weighted reference profiles require Mid and Side spectra"
                )
        object.__setattr__(self, "mid_spectrum", _immutable_spectrum(self.mid_spectrum))
        object.__setattr__(
            self,
            "side_spectrum",
            _immutable_spectrum(self.side_spectrum),
        )

    @property
    def profile_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(PROFILE_ALGORITHM_VERSION.encode("utf-8"))
        digest.update(bytes.fromhex(self.group.content_sha256))
        digest.update(
            struct.pack(
                ">dd",
                self.match_rms,
                self.final_amplitude_coefficient,
            )
        )
        for spectrum in (self.mid_spectrum, self.side_spectrum):
            if spectrum is None:
                digest.update(b"\x00")
            else:
                digest.update(b"\x01")
                digest.update(_canonical_spectrum_bytes(spectrum))
        return digest.hexdigest()

    def to_details(self) -> dict[str, object]:
        return {
            **self.group.to_details(),
            "profile_sha256": self.profile_sha256,
            "match_rms": self.match_rms,
            "final_amplitude_coefficient": self.final_amplitude_coefficient,
            "mid_spectrum_sha256": _spectrum_sha256(self.mid_spectrum),
            "side_spectrum_sha256": _spectrum_sha256(self.side_spectrum),
        }


@dataclass(frozen=True, slots=True)
class BlendedReferenceProfile:
    """Combined profile consumed by the target matching plan."""

    match_rms: float
    final_amplitude_coefficient: float
    mid_spectrum: FloatArray
    side_spectrum: FloatArray
    direct_level_profile: bool
    direct_frequency_profile: bool

    def __post_init__(self) -> None:
        _finite_positive(self.match_rms, "blended match_rms")
        _finite_positive(
            self.final_amplitude_coefficient,
            "blended final_amplitude_coefficient",
        )
        object.__setattr__(
            self,
            "mid_spectrum",
            _immutable_spectrum_required(self.mid_spectrum),
        )
        object.__setattr__(
            self,
            "side_spectrum",
            _immutable_spectrum_required(self.side_spectrum),
        )

    def to_details(self, *, floor: float) -> dict[str, object]:
        return {
            "algorithm_version": PROFILE_ALGORITHM_VERSION,
            "weight_normalization": "independent-stable-sum-per-dimension",
            "level_combination": LEVEL_COMBINATION_RULE,
            "frequency_combination": SPECTRAL_COMBINATION_RULE,
            "log_floor_linear": floor,
            "match_rms": self.match_rms,
            "final_amplitude_coefficient": self.final_amplitude_coefficient,
            "mid_spectrum_sha256": _spectrum_sha256(self.mid_spectrum),
            "side_spectrum_sha256": _spectrum_sha256(self.side_spectrum),
            "direct_level_profile": self.direct_level_profile,
            "direct_frequency_profile": self.direct_frequency_profile,
        }


def build_reference_inventory(job: JobConfig) -> ReferenceInventory:
    """Fingerprint and coalesce selected references by immutable content."""

    level_weights = job.normalized_level_weights
    frequency_weights = job.normalized_frequency_weights
    requested: list[RequestedReference] = []
    indices_by_digest: dict[str, list[int]] = {}

    for index, reference in enumerate(job.references):
        digest = fingerprint_file(reference.path).sha256
        requested.append(
            RequestedReference(
                index=index,
                path=reference.path,
                label=reference.label,
                content_sha256=digest,
                requested_level_weight=float(reference.level_weight),
                requested_frequency_weight=float(reference.frequency_weight),
                normalized_level_weight=level_weights[index],
                normalized_frequency_weight=frequency_weights[index],
            )
        )
        indices_by_digest.setdefault(digest, []).append(index)

    groups: list[ReferenceGroup] = []
    for digest in sorted(indices_by_digest):
        indices = tuple(indices_by_digest[digest])
        groups.append(
            ReferenceGroup(
                content_sha256=digest,
                path=job.references[indices[0]].path,
                source_indices=indices,
                level_weight=math.fsum(level_weights[index] for index in indices),
                frequency_weight=math.fsum(frequency_weights[index] for index in indices),
            )
        )
    return ReferenceInventory(tuple(requested), tuple(groups))


def blend_reference_profiles(
    profiles: tuple[ReferenceProfile, ...],
    *,
    floor: float,
) -> BlendedReferenceProfile:
    """Combine profiles in deterministic content-hash order."""

    _finite_positive(floor, "profile blend floor")
    ordered = tuple(sorted(profiles, key=lambda profile: profile.group.content_sha256))
    level_profiles = tuple(profile for profile in ordered if profile.group.level_weight > 0)
    frequency_profiles = tuple(profile for profile in ordered if profile.group.frequency_weight > 0)
    if not level_profiles:
        raise ValueError("at least one effective level profile is required")
    if not frequency_profiles:
        raise ValueError("at least one effective frequency profile is required")

    direct_level = len(level_profiles) == 1
    if direct_level:
        match_rms = level_profiles[0].match_rms
        final_coefficient = level_profiles[0].final_amplitude_coefficient
    else:
        match_rms = _weighted_geometric_scalar(
            tuple((profile.match_rms, profile.group.level_weight) for profile in level_profiles),
            floor=floor,
        )
        final_coefficient = _weighted_geometric_scalar(
            tuple(
                (
                    profile.final_amplitude_coefficient,
                    profile.group.level_weight,
                )
                for profile in level_profiles
            ),
            floor=floor,
        )

    direct_frequency = len(frequency_profiles) == 1
    if direct_frequency:
        mid_spectrum = _required_profile_spectrum(
            frequency_profiles[0].mid_spectrum,
            "Mid",
        )
        side_spectrum = _required_profile_spectrum(
            frequency_profiles[0].side_spectrum,
            "Side",
        )
    else:
        mid_spectrum = _weighted_geometric_spectrum(
            tuple(
                (
                    _required_profile_spectrum(profile.mid_spectrum, "Mid"),
                    profile.group.frequency_weight,
                )
                for profile in frequency_profiles
            ),
            floor=floor,
        )
        side_spectrum = _weighted_geometric_spectrum(
            tuple(
                (
                    _required_profile_spectrum(profile.side_spectrum, "Side"),
                    profile.group.frequency_weight,
                )
                for profile in frequency_profiles
            ),
            floor=floor,
        )

    return BlendedReferenceProfile(
        match_rms=match_rms,
        final_amplitude_coefficient=final_coefficient,
        mid_spectrum=mid_spectrum,
        side_spectrum=side_spectrum,
        direct_level_profile=direct_level,
        direct_frequency_profile=direct_frequency,
    )


def _weighted_geometric_scalar(
    values: tuple[tuple[float, float], ...],
    *,
    floor: float,
) -> float:
    return math.exp(math.fsum(weight * math.log(max(floor, value)) for value, weight in values))


def _weighted_geometric_spectrum(
    values: tuple[tuple[FloatArray, float], ...],
    *,
    floor: float,
) -> FloatArray:
    shape = values[0][0].shape
    if any(spectrum.shape != shape for spectrum, _ in values):
        raise ValueError("reference spectra must have identical shapes")
    logarithmic = np.zeros(shape, dtype=np.float64)
    for spectrum, weight in values:
        logarithmic += weight * np.log(np.maximum(floor, spectrum))
    blended = np.exp(logarithmic)
    if not np.all(np.isfinite(blended)):
        raise ValueError("weighted reference spectrum is non-finite")
    return blended


def _required_profile_spectrum(
    spectrum: FloatArray | None,
    channel_name: str,
) -> FloatArray:
    if spectrum is None:
        raise ValueError(f"{channel_name} spectrum is absent from an effective profile")
    return spectrum


def _immutable_spectrum(value: FloatArray | None) -> FloatArray | None:
    if value is None:
        return None
    return _immutable_spectrum_required(value)


def _immutable_spectrum_required(value: FloatArray) -> FloatArray:
    spectrum = np.array(value, dtype=np.float64, copy=True, order="C")
    if spectrum.ndim != 1 or not spectrum.size:
        raise ValueError("reference spectra must be non-empty one-dimensional arrays")
    if np.any(spectrum < 0) or not np.all(np.isfinite(spectrum)):
        raise ValueError("reference spectra must be finite and non-negative")
    spectrum.setflags(write=False)
    return spectrum


def _finite_positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


def _canonical_spectrum_bytes(spectrum: FloatArray) -> bytes:
    canonical = np.asarray(spectrum, dtype="<f8", order="C")
    return struct.pack(">Q", canonical.size) + canonical.tobytes(order="C")


def _spectrum_sha256(spectrum: FloatArray | None) -> str | None:
    if spectrum is None:
        return None
    return hashlib.sha256(_canonical_spectrum_bytes(spectrum)).hexdigest()


__all__ = [
    "LEVEL_COMBINATION_RULE",
    "MAX_WEIGHTED_REFERENCES",
    "PROFILE_ALGORITHM_VERSION",
    "SPECTRAL_COMBINATION_RULE",
    "BlendedReferenceProfile",
    "ReferenceGroup",
    "ReferenceInventory",
    "ReferenceProfile",
    "RequestedReference",
    "blend_reference_profiles",
    "build_reference_inventory",
]
