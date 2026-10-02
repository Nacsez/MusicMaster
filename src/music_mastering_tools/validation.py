"""Static and audio-aware preflight validation."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .audio_probe import AudioFacts, AudioProbe, AudioProbeUnavailable, DefaultAudioProbe
from .config import (
    EngineKind,
    JobConfig,
    LimiterKind,
    OutputMode,
    PolicyAction,
)
from .errors import CapabilityError


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One stable, actionable preflight finding."""

    code: str
    severity: ValidationSeverity
    message: str
    path: str | None = None
    remediation: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.path is not None:
            value["path"] = self.path
        if self.remediation is not None:
            value["remediation"] = self.remediation
        if self.details:
            value["details"] = self.details
        return value


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    audio_facts: tuple[AudioFacts, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
            "audio_facts": [
                {
                    "path": facts.path,
                    "backend": facts.backend,
                    "format": facts.format,
                    "subtype": facts.subtype,
                    "sample_rate": facts.sample_rate,
                    "channels": facts.channels,
                    "frames": facts.frames,
                    "duration_seconds": facts.duration_seconds,
                    "peak": facts.peak,
                    "rms": facts.rms,
                    "clipping_samples": facts.clipping_samples,
                    "non_finite_samples": facts.non_finite_samples,
                }
                for facts in self.audio_facts
            ],
        }


def validate_job(
    job: JobConfig,
    *,
    inspect_audio: bool = True,
    require_inputs: bool = True,
    probe: AudioProbe | None = None,
) -> ValidationReport:
    """Validate paths, engine-independent semantics, and input audio facts."""

    issues: list[ValidationIssue] = []
    facts: list[AudioFacts] = []
    input_paths = [Path(job.target), *(Path(item.path) for item in job.references)]
    output_paths = [
        *(Path(item.path) for item in job.outputs),
        *(Path(value) for value in (job.preview.target_path, job.preview.result_path) if value),
    ]

    _validate_input_paths(input_paths, require_inputs, issues)
    _validate_path_collisions(input_paths, output_paths, job, issues)
    _validate_output_semantics(job, issues)
    _validate_runtime_semantics(job, issues)
    _validate_engine_capabilities(job, issues)

    if (
        job.execution.engine in {EngineKind.UPSTREAM, EngineKind.NATIVE}
        and job.audio.internal_sample_rate != 44_100
    ):
        issues.append(
            ValidationIssue(
                "MMT-W-CUSTOM-SAMPLE-RATE",
                ValidationSeverity.WARNING,
                "The Matchering-parity DSP explicitly labels non-44.1 kHz processing as untested.",
                remediation=(
                    "Keep 44.1 kHz for the compatibility/weighted baseline or add "
                    "a dedicated characterization matrix before accepting this "
                    "configuration."
                ),
                details={"sample_rate": job.audio.internal_sample_rate},
            )
        )

    if inspect_audio and all(path.is_file() for path in input_paths):
        active_probe = probe or DefaultAudioProbe(clipping_peak=job.detection.clipping_peak)
        for index, path in enumerate(input_paths):
            role = "target" if index == 0 else f"reference[{index - 1}]"
            try:
                measured = active_probe.probe(path)
            except AudioProbeUnavailable as exc:
                issues.append(
                    ValidationIssue(
                        "MMT-E-AUDIO-PROBE",
                        ValidationSeverity.ERROR,
                        f"Could not inspect {role}: {exc}",
                        path=str(path),
                        remediation=(
                            "Install the baseline audio dependencies or supply a PCM WAV input."
                        ),
                    )
                )
                continue
            facts.append(measured)
            _validate_audio_facts(measured, role, job, issues)

    return ValidationReport(tuple(issues), tuple(facts))


def _validate_input_paths(
    paths: Iterable[Path],
    require_inputs: bool,
    issues: list[ValidationIssue],
) -> None:
    for path in paths:
        if not path.exists():
            severity = ValidationSeverity.ERROR if require_inputs else ValidationSeverity.INFO
            issues.append(
                ValidationIssue(
                    "MMT-E-INPUT-MISSING" if require_inputs else "MMT-I-INPUT-NOT-CHECKED",
                    severity,
                    "Input file does not exist."
                    if require_inputs
                    else "Input file is absent; existence was not required for this validation.",
                    path=str(path),
                )
            )
        elif not path.is_file():
            issues.append(
                ValidationIssue(
                    "MMT-E-INPUT-NOT-FILE",
                    ValidationSeverity.ERROR,
                    "Input path is not a regular file.",
                    path=str(path),
                )
            )


def _validate_path_collisions(
    inputs: list[Path],
    outputs: list[Path],
    job: JobConfig,
    issues: list[ValidationIssue],
) -> None:
    seen_outputs: list[Path] = []
    for output in outputs:
        if any(_same_path_or_file(output, input_path) for input_path in inputs):
            issues.append(
                ValidationIssue(
                    "MMT-E-OUTPUT-OVERWRITES-INPUT",
                    ValidationSeverity.ERROR,
                    "An output path resolves to a target or reference input.",
                    path=str(output),
                    remediation="Choose an output path outside the input files.",
                )
            )
        if any(_same_path_or_file(output, other) for other in seen_outputs):
            issues.append(
                ValidationIssue(
                    "MMT-E-DUPLICATE-OUTPUT",
                    ValidationSeverity.ERROR,
                    "Two requested artifacts resolve to the same path.",
                    path=str(output),
                )
            )
        seen_outputs.append(output)

        if output.exists():
            _append_policy_issue(
                issues,
                job.edge_cases.existing_output,
                "MMT-OUTPUT-EXISTS",
                "The requested output already exists.",
                path=str(output),
                remediation="Select a new path or explicitly change existing_output policy.",
            )

        parent = output.parent
        if not parent.exists() and not job.edge_cases.create_output_directories:
            issues.append(
                ValidationIssue(
                    "MMT-E-OUTPUT-DIRECTORY-MISSING",
                    ValidationSeverity.ERROR,
                    "Output parent directory does not exist and automatic creation is disabled.",
                    path=str(parent),
                )
            )

    if any(_same_path_or_file(inputs[0], reference) for reference in inputs[1:]) and not (
        job.audio.allow_identical_target_and_reference
    ):
        issues.append(
            ValidationIssue(
                "MMT-E-TARGET-REFERENCE-SAME-PATH",
                ValidationSeverity.ERROR,
                "Target and reference resolve to the same path.",
                path=str(inputs[0]),
            )
        )


def _validate_output_semantics(job: JobConfig, issues: list[ValidationIssue]) -> None:
    limited = [item for item in job.outputs if item.mode is OutputMode.LIMITED]
    if limited and job.limiter.kind is LimiterKind.NONE:
        issues.append(
            ValidationIssue(
                "MMT-E-LIMITED-OUTPUT-WITHOUT-LIMITER",
                ValidationSeverity.ERROR,
                "A limited output was requested while limiter.kind is 'none'.",
                remediation=("Select the Hyrax/external limiter or change the output mode."),
            )
        )

    for index, output in enumerate(job.outputs):
        if output.mode is OutputMode.RAW_FLOAT and output.subtype.casefold() not in {
            "float",
            "double",
        }:
            issues.append(
                ValidationIssue(
                    "MMT-E-RAW-FLOAT-INTEGER-SUBTYPE",
                    ValidationSeverity.ERROR,
                    "Raw, potentially out-of-range audio requires a floating-point file subtype.",
                    path=output.path,
                    remediation=(
                        f"Set outputs[{index}].subtype to FLOAT or DOUBLE, "
                        "or choose a normalized/limited output mode."
                    ),
                )
            )

    if job.limiter.kind is LimiterKind.EXTERNAL and job.execution.engine in {
        EngineKind.UPSTREAM,
        EngineKind.NATIVE,
    }:
        issues.append(
            ValidationIssue(
                "MMT-E-EXTERNAL-LIMITER-UNSUPPORTED",
                ValidationSeverity.ERROR,
                "The selected Matchering-parity engine cannot execute an external limiter.",
                remediation=(
                    "Request a raw float output and run the external limiter as a "
                    "separate audited stage."
                ),
            )
        )


def _validate_runtime_semantics(job: JobConfig, issues: list[ValidationIssue]) -> None:
    if job.execution.engine is EngineKind.UPSTREAM and job.matching.max_eq_gain_db is not None:
        issues.append(
            ValidationIssue(
                "MMT-E-EQ-GAIN-CEILING-UNSUPPORTED",
                ValidationSeverity.ERROR,
                "Matchering 2.0.6 cannot enforce matching.max_eq_gain_db.",
                remediation=(
                    "Set matching.max_eq_gain_db to null for compatibility runs, "
                    "or use the native engine after its guarded EQ stage is implemented."
                ),
            )
        )

    if job.execution.engine in {EngineKind.UPSTREAM, EngineKind.NATIVE}:
        minimum_seconds = job.matching.fft_size / job.audio.internal_sample_rate
        if job.matching.max_piece_seconds <= minimum_seconds:
            issues.append(
                ValidationIssue(
                    "MMT-E-MATCH-PIECE-TOO-SHORT",
                    ValidationSeverity.ERROR,
                    "matching.max_piece_seconds must exceed one FFT window.",
                    details={"minimum_exclusive_seconds": minimum_seconds},
                )
            )
        if job.matching.min_value >= 0.1:
            issues.append(
                ValidationIssue(
                    "MMT-E-MIN-VALUE-RANGE",
                    ValidationSeverity.ERROR,
                    "Matchering 2.0.6 requires matching.min_value below 0.1.",
                )
            )
        if job.limiter.threshold_linear <= job.matching.min_value:
            issues.append(
                ValidationIssue(
                    "MMT-E-LIMITER-THRESHOLD-RANGE",
                    ValidationSeverity.ERROR,
                    "limiter.threshold_linear must exceed matching.min_value.",
                )
            )
        if job.preview.duration_seconds <= 5:
            issues.append(
                ValidationIssue(
                    "MMT-E-PREVIEW-DURATION-RANGE",
                    ValidationSeverity.ERROR,
                    "Matchering 2.0.6 requires preview.duration_seconds greater than 5.",
                )
            )
        if job.preview.analysis_step_seconds <= 1:
            issues.append(
                ValidationIssue(
                    "MMT-E-PREVIEW-STEP-RANGE",
                    ValidationSeverity.ERROR,
                    "Matchering 2.0.6 requires preview.analysis_step_seconds greater than 1.",
                )
            )

    if (
        job.execution.engine in {EngineKind.UPSTREAM, EngineKind.NATIVE}
        and job.execution.max_workers != 1
    ):
        issues.append(
            ValidationIssue(
                "MMT-E-UPSTREAM-CONCURRENCY",
                ValidationSeverity.ERROR,
                "The Matchering-parity engines require max_workers=1 because "
                "their log handlers are global.",
                remediation="Use one process per job or keep the compatibility worker serialized.",
            )
        )

    if job.preview.enabled:
        if (
            job.execution.engine in {EngineKind.UPSTREAM, EngineKind.NATIVE}
            and not float(job.preview.fade_coefficient).is_integer()
        ):
            issues.append(
                ValidationIssue(
                    "MMT-E-FRACTIONAL-FADE-COEFFICIENT",
                    ValidationSeverity.ERROR,
                    "Matchering preview.fade_coefficient must be an integer.",
                    remediation=(
                        "Choose an integral value so the derived fade sample "
                        "count is a valid NumPy dimension."
                    ),
                )
            )
        for field_name, value in (
            ("duration_seconds", job.preview.duration_seconds),
            ("analysis_step_seconds", job.preview.analysis_step_seconds),
            ("fade_seconds", job.preview.fade_seconds),
        ):
            samples = value * job.audio.internal_sample_rate
            if not float(samples).is_integer():
                issues.append(
                    ValidationIssue(
                        "MMT-E-FRACTIONAL-PREVIEW-SAMPLES",
                        ValidationSeverity.ERROR,
                        (
                            f"preview.{field_name} does not resolve to an integer "
                            "sample count for the selected sample rate."
                        ),
                        remediation=(
                            "Choose a duration that produces an integral sample count; "
                            "this prevents the upstream NumPy stride-shape crash."
                        ),
                        details={"seconds": value, "samples": samples},
                    )
                )


def _validate_engine_capabilities(job: JobConfig, issues: list[ValidationIssue]) -> None:
    # Imported here to keep config-only consumers free from engine imports.
    from .engine import create_engine

    try:
        create_engine(job.execution.engine).validate_capabilities(job)
    except CapabilityError as exc:
        issues.append(
            ValidationIssue(
                "MMT-E-CAPABILITY-UNSUPPORTED",
                ValidationSeverity.ERROR,
                exc.message,
                remediation=(
                    "Select only capabilities reported by the chosen engine, "
                    "or choose a different implemented engine."
                ),
                details=dict(exc.to_dict()["details"]),
            )
        )


def _validate_audio_facts(
    facts: AudioFacts,
    role: str,
    job: JobConfig,
    issues: list[ValidationIssue],
) -> None:
    if facts.non_finite_samples:
        _append_policy_issue(
            issues,
            job.edge_cases.non_finite_audio,
            "MMT-NONFINITE-AUDIO",
            f"{role} contains non-finite samples.",
            path=facts.path,
            remediation="Repair or replace the input before processing.",
            details={"non_finite_samples": facts.non_finite_samples},
        )

    if facts.peak <= job.detection.silence_peak:
        _append_policy_issue(
            issues,
            job.edge_cases.silence,
            "MMT-SILENCE",
            (
                f"{role} is silent at the configured threshold; upstream "
                "Matchering would crash while formatting log10(0)."
            ),
            path=facts.path,
            remediation="Supply audible content or handle silence outside the DSP engine.",
            details={"peak": facts.peak},
        )
    elif facts.peak <= job.detection.near_silence_peak:
        _append_policy_issue(
            issues,
            job.edge_cases.near_silence,
            "MMT-NEAR-SILENCE",
            f"{role} is near silence and may produce unstable spectral ratios.",
            path=facts.path,
            details={"peak": facts.peak},
        )

    if facts.channels == 1:
        _append_policy_issue(
            issues,
            job.edge_cases.mono_input,
            "MMT-MONO-INPUT",
            (
                f"{role} is mono. It can be duplicated to stereo but cannot acquire "
                "genuine Side-channel width."
            ),
            path=facts.path,
        )
    elif facts.channels > 2:
        _append_policy_issue(
            issues,
            job.edge_cases.more_than_two_channels,
            "MMT-MULTICHANNEL-INPUT",
            f"{role} has {facts.channels} channels; the current DSP supports at most two.",
            path=facts.path,
        )

    if facts.duration_seconds > job.audio.max_length_seconds:
        issues.append(
            ValidationIssue(
                "MMT-E-TRACK-TOO-LONG",
                ValidationSeverity.ERROR,
                f"{role} exceeds the configured maximum length.",
                path=facts.path,
                details={
                    "duration_seconds": facts.duration_seconds,
                    "maximum_seconds": job.audio.max_length_seconds,
                },
            )
        )

    minimum_duration_seconds = job.matching.fft_size / job.audio.internal_sample_rate
    if facts.duration_seconds <= minimum_duration_seconds:
        issues.append(
            ValidationIssue(
                "MMT-E-TRACK-TOO-SHORT",
                ValidationSeverity.ERROR,
                (
                    f"{role} is too short to contain more than one FFT window "
                    "after sample-rate normalization."
                ),
                path=facts.path,
                details={
                    "frames": facts.frames,
                    "sample_rate": facts.sample_rate,
                    "duration_seconds": facts.duration_seconds,
                    "minimum_exclusive_seconds": minimum_duration_seconds,
                    "fft_size": job.matching.fft_size,
                    "internal_sample_rate": job.audio.internal_sample_rate,
                },
            )
        )

    if facts.sample_rate != job.audio.internal_sample_rate:
        _append_policy_issue(
            issues,
            job.edge_cases.sample_rate_conversion,
            "MMT-SAMPLE-RATE-CONVERSION",
            (
                f"{role} will be resampled from {facts.sample_rate} Hz to "
                f"{job.audio.internal_sample_rate} Hz."
            ),
            path=facts.path,
        )

    if _has_probably_lossy_extension(Path(facts.path)):
        _append_policy_issue(
            issues,
            job.edge_cases.lossy_input,
            "MMT-LOSSY-INPUT",
            (
                f"{role} uses an extension normally associated with lossy "
                "audio; codec-level confirmation is not yet implemented."
            ),
            path=facts.path,
            remediation="Prefer WAV, FLAC, or AIFF source material.",
        )

    if role == "target" and facts.clipping_samples > (job.detection.clipping_samples_threshold):
        issues.append(
            ValidationIssue(
                "MMT-W-TARGET-CLIPPING",
                ValidationSeverity.WARNING,
                "The target contains repeated full-scale samples.",
                path=facts.path,
                remediation="Prefer an unclipped, unmastered target when available.",
                details={"clipping_samples": facts.clipping_samples},
            )
        )


def _append_policy_issue(
    issues: list[ValidationIssue],
    action: PolicyAction,
    code_suffix: str,
    message: str,
    *,
    path: str | None = None,
    remediation: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    if action is PolicyAction.ALLOW:
        return
    prefix = "MMT-E" if action is PolicyAction.ERROR else "MMT-W"
    issues.append(
        ValidationIssue(
            f"{prefix}-{code_suffix.removeprefix('MMT-')}",
            ValidationSeverity.ERROR
            if action is PolicyAction.ERROR
            else ValidationSeverity.WARNING,
            message,
            path=path,
            remediation=remediation,
            details=details or {},
        )
    )


def _canonical(path: Path) -> str:
    return os.path.normcase(os.fspath(path.resolve(strict=False)))


def _same_path_or_file(first: Path, second: Path) -> bool:
    if _canonical(first) == _canonical(second):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _has_probably_lossy_extension(path: Path) -> bool:
    return path.suffix.casefold() in {
        ".mp3",
        ".mp2",
        ".aac",
        ".opus",
        ".3gp",
    }
