"""Faithful adapter for the pinned Matchering 2.0.6 compatibility engine.

Matchering uses process-global log handlers. Runs are therefore serialized and
the handler state is reset after every invocation. Multi-process scheduling can
be added above this boundary without misrepresenting the engine as thread-safe.
"""

from __future__ import annotations

import importlib
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType

from ..config import (
    DitherMode,
    EngineKind,
    JobConfig,
    LimiterKind,
    LoudnessMetric,
    MetadataPolicy,
    OutputMode,
    OutputSpec,
    PeakMode,
)
from ..errors import CapabilityError, DependencyError, ProcessingError
from .base import (
    EngineCapabilities,
    EngineLogHandler,
    EngineLogRecord,
    EngineRunResult,
    requested_artifact_paths,
)

PINNED_MATCHERING_VERSION = "2.0.6"
_RUN_LOCK = threading.RLock()
_CODE_PATTERN = re.compile(r"^\s*(\d{4})\s*:\s*(.*)$", re.DOTALL)


class UpstreamMatcheringEngine:
    """Run jobs using the public Matchering 2.0.6 Python API."""

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine_id=EngineKind.UPSTREAM.value,
            engine_version=PINNED_MATCHERING_VERSION,
            implementation_status="verified-runnable",
            runnable=True,
            maximum_references=1,
            independent_reference_weights=False,
            partial_matching_amount=False,
            ebu_r128_loudness=False,
            spectral_gain_ceiling=False,
            sample_peak_limiter=True,
            true_peak_limiter=False,
            external_limiter=False,
            dither=False,
            previews=True,
            thread_safe=False,
        )

    def validate_capabilities(self, job: JobConfig) -> None:
        """Reject every option that Matchering cannot faithfully honor."""

        unsupported: list[dict[str, object]] = []

        if job.execution.engine is not EngineKind.UPSTREAM:
            unsupported.append(_unsupported("execution.engine", job.execution.engine.value))
        if len(job.references) != 1:
            unsupported.append(_unsupported("references", len(job.references), expected=1))
        if job.references and (
            job.references[0].level_weight != 1.0 or job.references[0].frequency_weight != 1.0
        ):
            unsupported.append(
                _unsupported(
                    "references[0].weights",
                    {
                        "level_weight": job.references[0].level_weight,
                        "frequency_weight": job.references[0].frequency_weight,
                    },
                    expected={"level_weight": 1.0, "frequency_weight": 1.0},
                )
            )
        if job.matching.amount != 1.0:
            unsupported.append(_unsupported("matching.amount", job.matching.amount, expected=1.0))
        if job.matching.loudness_metric is not LoudnessMetric.MATCHERING_RMS:
            unsupported.append(
                _unsupported(
                    "matching.loudness_metric",
                    job.matching.loudness_metric.value,
                    expected=LoudnessMetric.MATCHERING_RMS.value,
                )
            )
        if job.matching.max_eq_gain_db is not None:
            unsupported.append(
                _unsupported("matching.max_eq_gain_db", job.matching.max_eq_gain_db, expected=None)
            )
        minimum_piece_seconds = job.matching.fft_size / job.audio.internal_sample_rate
        if job.matching.max_piece_seconds <= minimum_piece_seconds:
            unsupported.append(
                _unsupported(
                    "matching.max_piece_seconds",
                    job.matching.max_piece_seconds,
                    expected=f"> {minimum_piece_seconds}",
                )
            )
        if job.matching.min_value >= 0.1:
            unsupported.append(
                _unsupported(
                    "matching.min_value",
                    job.matching.min_value,
                    expected="< 0.1",
                )
            )
        if job.limiter.threshold_linear <= job.matching.min_value:
            unsupported.append(
                _unsupported(
                    "limiter.threshold_linear",
                    job.limiter.threshold_linear,
                    expected=f"> {job.matching.min_value}",
                )
            )
        if job.audio.metadata_policy is not MetadataPolicy.DROP:
            unsupported.append(
                _unsupported(
                    "audio.metadata_policy",
                    job.audio.metadata_policy.value,
                    expected=MetadataPolicy.DROP.value,
                )
            )
        if job.limiter.kind is LimiterKind.EXTERNAL:
            unsupported.append(_unsupported("limiter.kind", job.limiter.kind.value))
        if job.limiter.peak_mode is not PeakMode.SAMPLE_PEAK:
            unsupported.append(
                _unsupported(
                    "limiter.peak_mode",
                    job.limiter.peak_mode.value,
                    expected=PeakMode.SAMPLE_PEAK.value,
                )
            )
        limited_requested = any(output.mode is OutputMode.LIMITED for output in job.outputs)
        if limited_requested and job.limiter.kind is LimiterKind.NONE:
            unsupported.append(
                _unsupported(
                    "limiter.kind",
                    job.limiter.kind.value,
                    expected=LimiterKind.HYRAX.value,
                )
            )
        if limited_requested and job.limiter.kind is LimiterKind.HYRAX:
            attack_samples = int(job.audio.internal_sample_rate * job.limiter.attack_ms * 1e-3)
            hold_samples = int(job.audio.internal_sample_rate * job.limiter.hold_ms * 1e-3)
            nyquist = job.audio.internal_sample_rate / 2
            release_cutoff = job.limiter.release_filter_coefficient / job.limiter.release_ms
            for field, requested, expected in (
                ("limiter.attack_ms", attack_samples, "at least 1 sample"),
                ("limiter.hold_ms", hold_samples, "at least 3 samples"),
            ):
                minimum = 1 if field.endswith("attack_ms") else 3
                if requested < minimum:
                    unsupported.append(_unsupported(field, requested, expected=expected))
            if job.limiter.attack_filter_coefficient >= 0:
                unsupported.append(
                    _unsupported(
                        "limiter.attack_filter_coefficient",
                        job.limiter.attack_filter_coefficient,
                        expected="< 0",
                    )
                )
            if not 0 < job.limiter.hold_filter_coefficient < nyquist:
                unsupported.append(
                    _unsupported(
                        "limiter.hold_filter_coefficient",
                        job.limiter.hold_filter_coefficient,
                        expected=f"between 0 and {nyquist} Hz",
                    )
                )
            if not 0 < release_cutoff < nyquist:
                unsupported.append(
                    _unsupported(
                        "limiter.release_filter_coefficient/release_ms",
                        release_cutoff,
                        expected=f"between 0 and {nyquist} Hz",
                    )
                )
        if job.preview.duration_seconds <= 5:
            unsupported.append(
                _unsupported(
                    "preview.duration_seconds",
                    job.preview.duration_seconds,
                    expected="> 5",
                )
            )
        if job.preview.analysis_step_seconds <= 1:
            unsupported.append(
                _unsupported(
                    "preview.analysis_step_seconds",
                    job.preview.analysis_step_seconds,
                    expected="> 1",
                )
            )
        if job.preview.enabled:
            if not float(job.preview.fade_coefficient).is_integer():
                unsupported.append(
                    _unsupported(
                        "preview.fade_coefficient",
                        job.preview.fade_coefficient,
                        expected="an integer",
                    )
                )
            for field, seconds in (
                ("preview.duration_seconds", job.preview.duration_seconds),
                (
                    "preview.analysis_step_seconds",
                    job.preview.analysis_step_seconds,
                ),
                ("preview.fade_seconds", job.preview.fade_seconds),
            ):
                samples = seconds * job.audio.internal_sample_rate
                if not float(samples).is_integer():
                    unsupported.append(
                        _unsupported(
                            field,
                            seconds,
                            expected=("a duration that maps to an integer sample count"),
                        )
                    )
        if job.execution.max_workers != 1:
            unsupported.append(
                _unsupported("execution.max_workers", job.execution.max_workers, expected=1)
            )
        for index, output in enumerate(job.outputs):
            if output.dither is not DitherMode.NONE:
                unsupported.append(
                    _unsupported(
                        f"outputs[{index}].dither",
                        output.dither.value,
                        expected=DitherMode.NONE.value,
                    )
                )
            if output.mode is OutputMode.RAW_FLOAT and output.subtype.casefold() not in {
                "float",
                "double",
            }:
                unsupported.append(
                    _unsupported(
                        f"outputs[{index}].subtype",
                        output.subtype,
                        expected="FLOAT or DOUBLE for raw-float-no-limiter",
                    )
                )

        if unsupported:
            constraints = "; ".join(
                (
                    f"{item['field']}={item['requested']!r}"
                    + (
                        f" (expected {item['supported_value']})"
                        if "supported_value" in item
                        else ""
                    )
                )
                for item in unsupported
            )
            raise CapabilityError(
                f"Matchering {PINNED_MATCHERING_VERSION} cannot honor: {constraints}",
                details={
                    "engine": EngineKind.UPSTREAM.value,
                    "unsupported": unsupported,
                },
            )

    def run(
        self,
        job: JobConfig,
        *,
        log_handler: EngineLogHandler | None = None,
    ) -> EngineRunResult:
        self.validate_capabilities(job)
        matchering = _load_matchering()
        defaults = importlib.import_module("matchering.defaults")
        _verify_version(matchering)

        started_at = datetime.now(UTC)
        started = time.monotonic()
        with _RUN_LOCK:
            try:
                _configure_logging(matchering, log_handler)
                matchering.process(
                    target=job.target,
                    reference=job.references[0].path,
                    results=[_result(matchering, output) for output in job.outputs],
                    config=_config(matchering, defaults, job),
                    preview_target=_preview_result(
                        matchering,
                        job.preview.target_path if job.preview.enabled else None,
                        job.preview.subtype,
                    ),
                    preview_result=_preview_result(
                        matchering,
                        job.preview.result_path if job.preview.enabled else None,
                        job.preview.subtype,
                    ),
                )
            except (CapabilityError, DependencyError):
                raise
            except Exception as exc:
                raise ProcessingError(
                    f"Matchering processing failed: {exc}",
                    details={
                        "engine": EngineKind.UPSTREAM.value,
                        "engine_version": PINNED_MATCHERING_VERSION,
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            finally:
                # The upstream package exposes no getter or restoration token.
                # Resetting to no-op handlers avoids leaking this run's sink.
                try:
                    matchering.log()
                except Exception:
                    pass

        return EngineRunResult.completed(
            engine_id=EngineKind.UPSTREAM.value,
            engine_version=PINNED_MATCHERING_VERSION,
            output_paths=requested_artifact_paths(job),
            started_at=started_at,
            duration_seconds=time.monotonic() - started,
        )


def _load_matchering() -> ModuleType:
    try:
        return importlib.import_module("matchering")
    except (ImportError, OSError) as exc:
        raise DependencyError(
            "Matchering 2.0.6 and its native audio dependencies are not available.",
            details={
                "dependency": "matchering",
                "required_version": PINNED_MATCHERING_VERSION,
                "original_error": str(exc),
            },
        ) from exc


def _verify_version(matchering: ModuleType) -> None:
    actual = getattr(matchering, "__version__", None)
    if actual != PINNED_MATCHERING_VERSION:
        raise DependencyError(
            "The installed Matchering version does not match the compatibility baseline.",
            details={
                "dependency": "matchering",
                "required_version": PINNED_MATCHERING_VERSION,
                "actual_version": actual,
            },
        )


def _config(matchering: ModuleType, defaults: ModuleType, job: JobConfig) -> object:
    limiter = defaults.LimiterConfig(
        attack=job.limiter.attack_ms,
        hold=job.limiter.hold_ms,
        release=job.limiter.release_ms,
        attack_filter_coefficient=job.limiter.attack_filter_coefficient,
        hold_filter_order=job.limiter.hold_filter_order,
        hold_filter_coefficient=job.limiter.hold_filter_coefficient,
        release_filter_order=job.limiter.release_filter_order,
        release_filter_coefficient=job.limiter.release_filter_coefficient,
    )
    configured = matchering.Config(
        internal_sample_rate=job.audio.internal_sample_rate,
        max_length=job.audio.max_length_seconds,
        max_piece_size=job.matching.max_piece_seconds,
        threshold=job.limiter.threshold_linear,
        min_value=job.matching.min_value,
        fft_size=job.matching.fft_size,
        lin_log_oversampling=job.matching.lin_log_oversampling,
        rms_correction_steps=job.matching.rms_correction_steps,
        clipping_samples_threshold=job.detection.clipping_samples_threshold,
        limited_samples_threshold=job.detection.limited_samples_threshold,
        allow_equality=job.audio.allow_identical_target_and_reference,
        lowess_frac=job.matching.lowess_fraction,
        lowess_it=job.matching.lowess_iterations,
        lowess_delta=job.matching.lowess_delta,
        preview_size=job.preview.duration_seconds,
        preview_analysis_step=job.preview.analysis_step_seconds,
        preview_fade_size=job.preview.fade_seconds,
        preview_fade_coefficient=job.preview.fade_coefficient,
        temp_folder=job.audio.temp_directory,
        limiter=limiter,
    )
    # Matchering multiplies float seconds by the sample rate but does not cast
    # the result. NumPy later rejects those mathematically integral floats as
    # shape and linspace lengths whenever a preview window is shorter than the
    # track. Preflight proves exact integrality; the adapter makes that intended
    # unit conversion explicit at the compatibility boundary.
    configured.preview_size = int(job.preview.duration_seconds * job.audio.internal_sample_rate)
    configured.preview_analysis_step = int(
        job.preview.analysis_step_seconds * job.audio.internal_sample_rate
    )
    configured.preview_fade_size = int(job.preview.fade_seconds * job.audio.internal_sample_rate)
    configured.preview_fade_coefficient = int(job.preview.fade_coefficient)
    return configured


def _result(matchering: ModuleType, output: OutputSpec) -> object:
    mode = output.mode
    return matchering.Result(
        output.path,
        output.subtype,
        use_limiter=mode is OutputMode.LIMITED,
        normalize=mode is OutputMode.NORMALIZED,
    )


def _preview_result(matchering: ModuleType, path: str | None, subtype: str) -> object | None:
    return None if path is None else matchering.Result(path, subtype)


def _configure_logging(matchering: ModuleType, handler: EngineLogHandler | None) -> None:
    if handler is None:
        matchering.log()
        return

    def forward(level: str) -> Callable[[object], None]:
        def emit(message: object) -> None:
            rendered = str(message)
            match = _CODE_PATTERN.match(rendered)
            code = int(match.group(1)) if match else None
            text = match.group(2) if match else rendered
            handler(EngineLogRecord(level=level, message=text, code=code))

        return emit

    matchering.log(
        warning_handler=forward("warning"),
        info_handler=forward("info"),
        debug_handler=forward("debug"),
        show_codes=True,
    )


def _unsupported(
    field: str, requested: object, *, expected: object | None = None
) -> dict[str, object]:
    value: dict[str, object] = {"field": field, "requested": requested}
    if expected is not None or field == "matching.max_eq_gain_db":
        value["supported_value"] = expected
    return value


__all__ = ["PINNED_MATCHERING_VERSION", "UpstreamMatcheringEngine"]
