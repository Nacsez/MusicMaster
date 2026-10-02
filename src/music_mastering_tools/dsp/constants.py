"""Dependency-free identifiers for the native weighted DSP implementation."""

MAX_WEIGHTED_REFERENCES = 32
PROFILE_ALGORITHM_VERSION = "mmt-weighted-reference-profile-v1"
LEVEL_COMBINATION_RULE = "weighted-geometric-mean-linear-rms"
SPECTRAL_COMBINATION_RULE = "weighted-geometric-mean-linear-magnitude"

__all__ = [
    "LEVEL_COMBINATION_RULE",
    "MAX_WEIGHTED_REFERENCES",
    "PROFILE_ALGORITHM_VERSION",
    "SPECTRAL_COMBINATION_RULE",
]
