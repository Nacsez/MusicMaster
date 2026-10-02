"""Matchering-parity render pipeline with deterministic reference profiles.

This module is derived from the GPL-3.0-or-later Matchering 2.0.6 processing
stages preserved under ``matchering-master/``. The material difference is that
each unique reference is analyzed independently and the resulting positive
level/spectral measurements are blended before the target is rendered once.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from scipy import interpolate, signal

from ..config import JobConfig
from ..errors import ProcessingError
from .profiles import (
    PROFILE_ALGORITHM_VERSION,
    BlendedReferenceProfile,
    ReferenceInventory,
    ReferenceProfile,
    blend_reference_profiles,
)

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class WeightedProcessOutcome:
    """Structured plan/provenance returned by the native DSP boundary."""

    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class _TargetAnalysis:
    mid: FloatArray
    side: FloatArray
    mid_loudest_pieces: FloatArray
    side_loudest_pieces: FloatArray
    match_rms: float
    divisions: int
    piece_size: int


@dataclass(frozen=True, slots=True)
class _FilterSummary:
    sha256: str
    bins_clamped: int
    maximum_linear_gain: float
    minimum_positive_linear_gain: float

    def to_details(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "bins_clamped": self.bins_clamped,
            "maximum_linear_gain": self.maximum_linear_gain,
            "minimum_positive_linear_gain": self.minimum_positive_linear_gain,
        }


def process_weighted(
    *,
    job: JobConfig,
    inventory: ReferenceInventory,
    config: Any,
    results: list[Any],
    preview_target: Any | None,
    preview_result: Any | None,
) -> WeightedProcessOutcome:
    """Render one target from one or more weighted reference profiles."""

    from matchering.checker import check, check_equality
    from matchering.dsp import (
        amplify,
        channel_count,
        clip,
        normalize,
        size,
    )
    from matchering.limiter import limit
    from matchering.loader import load
    from matchering.log import Code, ModuleError, debug, debug_line, info
    from matchering.preview_creator import create_preview
    from matchering.saver import save
    from matchering.stage_helpers import (
        analyze_levels,
        convolve,
        get_average_rms,
        get_lpis_and_match_rms,
        get_rms_c_and_amplify_pair,
        normalize_reference,
    )
    from matchering.utils import get_temp_folder

    debug(
        "Running Music Mastering Tools weighted reference profile algorithm "
        f"{PROFILE_ALGORITHM_VERSION}"
    )
    debug_line()
    info(Code.INFO_LOADING)
    if not results:
        raise ProcessingError("The native weighted result list is empty.")

    temp_folder = config.temp_folder if config.temp_folder else get_temp_folder(results)
    target, target_sample_rate = load(job.target, "target", temp_folder)
    target, target_sample_rate = check(
        target,
        target_sample_rate,
        config,
        "target",
    )
    _validate_checked_audio(
        target,
        target_sample_rate,
        config,
        channel_count=channel_count,
        size=size,
        module_error=ModuleError,
        validation_code=Code.ERROR_VALIDATION,
        role="target",
    )

    debug_line()
    info(Code.INFO_MATCHING_LEVELS)
    (
        target_mid,
        target_side,
        target_mid_loudest_pieces,
        target_side_loudest_pieces,
        target_match_rms,
        target_divisions,
        target_piece_size,
    ) = analyze_levels(target, "target", config)
    target_analysis = _TargetAnalysis(
        mid=cast(FloatArray, target_mid),
        side=cast(FloatArray, target_side),
        mid_loudest_pieces=cast(FloatArray, target_mid_loudest_pieces),
        side_loudest_pieces=cast(FloatArray, target_side_loudest_pieces),
        match_rms=float(target_match_rms),
        divisions=int(target_divisions),
        piece_size=int(target_piece_size),
    )

    profiles: list[ReferenceProfile] = []
    analysis_durations: dict[str, float] = {}
    for group in inventory.groups:
        if not group.effective:
            continue
        reference_started = monotonic()
        reference, reference_sample_rate = load(group.path, "reference", temp_folder)
        reference, reference_sample_rate = check(
            reference,
            reference_sample_rate,
            config,
            "reference",
        )
        _validate_checked_audio(
            reference,
            reference_sample_rate,
            config,
            channel_count=channel_count,
            size=size,
            module_error=ModuleError,
            validation_code=Code.ERROR_VALIDATION,
            role=f"reference content {group.content_sha256[:12]}",
        )
        if not config.allow_equality:
            check_equality(target, reference)

        reference, final_amplitude_coefficient = normalize_reference(
            reference,
            config,
        )
        (
            _reference_mid,
            _reference_side,
            reference_mid_loudest_pieces,
            reference_side_loudest_pieces,
            reference_match_rms,
            *_reference_divisions,
        ) = analyze_levels(
            reference,
            f"reference-{group.content_sha256[:12]}",
            config,
        )
        mid_spectrum: FloatArray | None = None
        side_spectrum: FloatArray | None = None
        if group.frequency_weight > 0:
            mid_spectrum = _average_fft(
                cast(FloatArray, reference_mid_loudest_pieces),
                int(config.internal_sample_rate),
                int(config.fft_size),
            )
            side_spectrum = _average_fft(
                cast(FloatArray, reference_side_loudest_pieces),
                int(config.internal_sample_rate),
                int(config.fft_size),
            )
        profile = ReferenceProfile(
            group=group,
            match_rms=float(reference_match_rms),
            final_amplitude_coefficient=float(final_amplitude_coefficient),
            mid_spectrum=mid_spectrum,
            side_spectrum=side_spectrum,
        )
        profiles.append(profile)
        analysis_durations[group.content_sha256] = monotonic() - reference_started

    blended = blend_reference_profiles(
        tuple(profiles),
        floor=float(config.min_value),
    )
    level_gain, target_mid, target_side = get_rms_c_and_amplify_pair(
        target_analysis.mid,
        target_analysis.side,
        target_analysis.match_rms,
        blended.match_rms,
        config.min_value,
        "target",
    )
    target_mid_loudest_pieces = amplify(
        target_analysis.mid_loudest_pieces,
        level_gain,
    )
    target_side_loudest_pieces = amplify(
        target_analysis.side_loudest_pieces,
        level_gain,
    )

    debug_line()
    info(Code.INFO_MATCHING_FREQS)
    target_mid_spectrum = _average_fft(
        cast(FloatArray, target_mid_loudest_pieces),
        int(config.internal_sample_rate),
        int(config.fft_size),
    )
    target_side_spectrum = _average_fft(
        cast(FloatArray, target_side_loudest_pieces),
        int(config.internal_sample_rate),
        int(config.fft_size),
    )
    mid_fir, mid_summary = _get_fir_from_spectra(
        target_mid_spectrum,
        blended.mid_spectrum,
        config=config,
        max_eq_gain_db=job.matching.max_eq_gain_db,
    )
    side_fir, side_summary = _get_fir_from_spectra(
        target_side_spectrum,
        blended.side_spectrum,
        config=config,
        max_eq_gain_db=job.matching.max_eq_gain_db,
    )
    result_no_limiter, result_no_limiter_mid = convolve(
        target_mid,
        mid_fir,
        target_side,
        side_fir,
    )
    _require_finite_audio(cast(FloatArray, result_no_limiter), "post-filter result")

    debug_line()
    info(Code.INFO_CORRECTING_LEVELS)
    correction_gains: list[float] = []
    for step in range(1, int(config.rms_correction_steps) + 1):
        debug(f"Applying RMS correction #{step}...")
        result_mid_clipped = clip(result_no_limiter_mid)
        _, clipped_rmses, clipped_average_rms = get_average_rms(
            result_mid_clipped,
            target_analysis.piece_size,
            target_analysis.divisions,
            "result",
        )
        _, result_mid_clipped_match_rms = get_lpis_and_match_rms(
            clipped_rmses,
            clipped_average_rms,
        )
        correction_gain, result_no_limiter_mid, result_no_limiter = get_rms_c_and_amplify_pair(
            result_no_limiter_mid,
            result_no_limiter,
            result_mid_clipped_match_rms,
            blended.match_rms,
            config.min_value,
            "result",
        )
        correction_gains.append(float(correction_gain))
        _require_finite_audio(
            cast(FloatArray, result_no_limiter),
            f"RMS correction step {step}",
        )

    debug_line()
    info(Code.INFO_FINALIZING)
    need_limited = any(result.use_limiter for result in results)
    need_raw = any(not result.use_limiter and not result.normalize for result in results)
    need_normalized = any(not result.use_limiter and result.normalize for result in results)
    normalized_result: FloatArray | None = None
    if need_normalized:
        normalized_result, _normalization_coefficient = normalize(
            result_no_limiter,
            config.threshold,
            config.min_value,
            normalize_clipped=True,
        )
        normalized_result = cast(FloatArray, normalized_result)
        _require_finite_audio(normalized_result, "normalized result")

    limited_result: FloatArray | None = None
    if need_limited:
        limited_result = cast(FloatArray, limit(result_no_limiter, config))
        limited_result = cast(
            FloatArray,
            amplify(limited_result, blended.final_amplitude_coefficient),
        )
        _require_finite_audio(limited_result, "limited result")

    raw_result = cast(FloatArray, result_no_limiter) if need_raw else None
    for required_result in results:
        if required_result.use_limiter:
            selected = limited_result
        elif required_result.normalize:
            selected = normalized_result
        else:
            selected = raw_result
        if selected is None:
            raise ProcessingError(
                "The native weighted engine did not construct a requested output branch."
            )
        save(
            required_result.file,
            selected,
            config.internal_sample_rate,
            required_result.subtype,
        )

    if preview_target or preview_result:
        preview_source = next(
            candidate
            for candidate in (limited_result, raw_result, normalized_result)
            if candidate is not None
        )
        create_preview(
            target,
            preview_source,
            config,
            preview_target,
            preview_result,
        )

    plan_sha256 = _plan_sha256(
        blended,
        level_gain=float(level_gain),
        mid_fir=mid_fir,
        side_fir=side_fir,
        correction_gains=tuple(correction_gains),
    )
    details: dict[str, object] = {
        "algorithm_version": PROFILE_ALGORITHM_VERSION,
        "reference_inventory": inventory.to_details(),
        "reference_profiles": [profile.to_details() for profile in profiles],
        "reference_analysis_seconds": analysis_durations,
        "blend": blended.to_details(floor=float(config.min_value)),
        "plan": {
            "sha256": plan_sha256,
            "target_match_rms": target_analysis.match_rms,
            "initial_level_gain_linear": float(level_gain),
            "rms_correction_gains_linear": correction_gains,
            "mid_filter": mid_summary.to_details(),
            "side_filter": side_summary.to_details(),
            "max_eq_gain_db": job.matching.max_eq_gain_db,
            "internal_sample_rate": int(config.internal_sample_rate),
            "fft_size": int(config.fft_size),
        },
    }
    return WeightedProcessOutcome(details)


def _validate_checked_audio(
    audio: Any,
    sample_rate: int,
    config: Any,
    *,
    channel_count: Any,
    size: Any,
    module_error: type[Exception],
    validation_code: Any,
    role: str,
) -> None:
    if (
        sample_rate != int(config.internal_sample_rate)
        or int(channel_count(audio)) != 2
        or int(size(audio)) <= int(config.fft_size)
    ):
        raise module_error(validation_code)
    array = np.asarray(audio)
    if not np.all(np.isfinite(array)):
        raise ProcessingError(
            f"Checked {role} audio contains non-finite samples.",
            details={"role": role},
        )


def _average_fft(
    loudest_pieces: FloatArray,
    sample_rate: int,
    fft_size: int,
) -> FloatArray:
    """Matchering 2.0.6 linear-magnitude STFT profile."""

    *_, spectra = signal.stft(
        loudest_pieces,
        sample_rate,
        window="boxcar",
        nperseg=fft_size,
        noverlap=0,
        boundary=None,
        padded=False,
    )
    average = np.asarray(np.abs(spectra).mean((0, 2)), dtype=np.float64)
    if not np.all(np.isfinite(average)) or np.any(average < 0):
        raise ProcessingError("Reference analysis produced an invalid spectrum.")
    return average


def _get_fir_from_spectra(
    target_spectrum: FloatArray,
    reference_spectrum: FloatArray,
    *,
    config: Any,
    max_eq_gain_db: float | None,
) -> tuple[FloatArray, _FilterSummary]:
    target_floor = np.maximum(float(config.min_value), target_spectrum)
    matching_fft = reference_spectrum / target_floor
    if not np.all(np.isfinite(matching_fft)) or np.any(matching_fft < 0):
        raise ProcessingError("Spectral matching produced a non-finite transfer curve.")
    matching_fft_filtered = _smooth_exponentially(matching_fft, config)
    matching_fft_filtered[0] = 0.0
    matching_fft_filtered[1] = matching_fft[1]

    bins_clamped = 0
    if max_eq_gain_db is not None:
        maximum = 10.0 ** (max_eq_gain_db / 20.0)
        minimum = 1.0 / maximum
        before = matching_fft_filtered[1:].copy()
        np.clip(
            matching_fft_filtered[1:],
            minimum,
            maximum,
            out=matching_fft_filtered[1:],
        )
        bins_clamped = int(np.count_nonzero(before != matching_fft_filtered[1:]))

    if not np.all(np.isfinite(matching_fft_filtered)):
        raise ProcessingError("Smoothed spectral transfer curve is non-finite.")
    fir = np.asarray(np.fft.irfft(matching_fft_filtered), dtype=np.float64)
    fir = np.asarray(
        np.fft.ifftshift(fir) * signal.windows.hann(len(fir)),
        dtype=np.float64,
    )
    if not np.all(np.isfinite(fir)):
        raise ProcessingError("Planned matching filter is non-finite.")
    positive = matching_fft_filtered[matching_fft_filtered > 0]
    summary = _FilterSummary(
        sha256=_array_sha256(fir),
        bins_clamped=bins_clamped,
        maximum_linear_gain=float(np.max(matching_fft_filtered)),
        minimum_positive_linear_gain=(float(np.min(positive)) if positive.size else 0.0),
    )
    return fir, summary


def _smooth_exponentially(matching_fft: FloatArray, config: Any) -> FloatArray:
    from matchering.dsp import smooth_lowess

    grid_linear = (
        int(config.internal_sample_rate) * 0.5 * np.linspace(0, 1, int(config.fft_size) // 2 + 1)
    )
    grid_logarithmic = (
        int(config.internal_sample_rate)
        * 0.5
        * np.logspace(
            np.log10(4 / int(config.fft_size)),
            0,
            (int(config.fft_size) // 2) * int(config.lin_log_oversampling) + 1,
        )
    )
    to_log = interpolate.interp1d(grid_linear, matching_fft, "cubic")
    matching_fft_log = to_log(grid_logarithmic)
    matching_fft_log_filtered = smooth_lowess(
        matching_fft_log,
        config.lowess_frac,
        config.lowess_it,
        config.lowess_delta,
    )
    to_linear = interpolate.interp1d(
        grid_logarithmic,
        matching_fft_log_filtered,
        "cubic",
        fill_value="extrapolate",
    )
    return np.asarray(to_linear(grid_linear), dtype=np.float64)


def _require_finite_audio(array: FloatArray, stage: str) -> None:
    if not np.all(np.isfinite(array)):
        raise ProcessingError(
            "The native weighted engine produced non-finite audio.",
            details={"stage": stage},
        )


def _array_sha256(array: FloatArray) -> str:
    canonical = np.asarray(array, dtype="<f8", order="C")
    payload = struct.pack(">Q", canonical.size) + canonical.tobytes(order="C")
    return hashlib.sha256(payload).hexdigest()


def _plan_sha256(
    blend: BlendedReferenceProfile,
    *,
    level_gain: float,
    mid_fir: FloatArray,
    side_fir: FloatArray,
    correction_gains: tuple[float, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(PROFILE_ALGORITHM_VERSION.encode("utf-8"))
    digest.update(
        struct.pack(
            ">ddd",
            blend.match_rms,
            blend.final_amplitude_coefficient,
            level_gain,
        )
    )
    digest.update(bytes.fromhex(_array_sha256(mid_fir)))
    digest.update(bytes.fromhex(_array_sha256(side_fir)))
    for gain in correction_gains:
        if not math.isfinite(gain):
            raise ProcessingError("The native weighted plan contains a non-finite gain.")
        digest.update(struct.pack(">d", gain))
    return digest.hexdigest()


__all__ = ["WeightedProcessOutcome", "process_weighted"]
