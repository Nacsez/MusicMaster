"""Typed, versioned configuration for Music Mastering Tools.

The schema intentionally represents both the upstream Matchering 2.0.6
capabilities and planned project capabilities.  Runtime adapters must reject a
requested option they cannot honor; silently downgrading a mastering job would
make the run manifest misleading and is therefore forbidden.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, TypeVar, cast

SCHEMA_VERSION = 1


class StringEnum(StrEnum):
    """Enum that serializes naturally as a JSON string."""

    def __str__(self) -> str:
        return str(self.value)


class EngineKind(StringEnum):
    """Processing implementation selected for a job."""

    UPSTREAM = "upstream-matchering-2.0.6"
    NATIVE = "music-mastering-tools-native"


class OutputMode(StringEnum):
    """Post-matching output branch."""

    LIMITED = "limited"
    NORMALIZED = "normalized-no-limiter"
    RAW_FLOAT = "raw-float-no-limiter"


class LoudnessMetric(StringEnum):
    """Level-analysis method.

    ``MATCHERING_RMS`` is implemented by the upstream adapter. ``EBU_R128`` is
    reserved for the native engine milestone and is rejected until implemented.
    """

    MATCHERING_RMS = "matchering-loud-section-rms"
    EBU_R128 = "ebu-r128-lufs"


class LimiterKind(StringEnum):
    HYRAX = "hyrax"
    EXTERNAL = "external"
    NONE = "none"


class PeakMode(StringEnum):
    SAMPLE_PEAK = "sample-peak"
    TRUE_PEAK = "true-peak"


class DitherMode(StringEnum):
    NONE = "none"
    TPDF = "tpdf"
    NOISE_SHAPED = "noise-shaped"


class PolicyAction(StringEnum):
    ERROR = "error"
    WARN = "warn"
    ALLOW = "allow"


class MetadataPolicy(StringEnum):
    DROP = "drop"
    COPY_SAFE = "copy-safe"


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    """One reference and its independent level/frequency blend weights."""

    path: str
    level_weight: float = 1.0
    frequency_weight: float = 1.0
    label: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.path, "reference.path")
        _optional_string(self.label, "reference.label")
        _finite_non_negative(self.level_weight, "reference.level_weight")
        _finite_non_negative(self.frequency_weight, "reference.frequency_weight")


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """One requested deliverable."""

    path: str
    subtype: str = "PCM_24"
    mode: OutputMode = OutputMode.LIMITED
    dither: DitherMode = DitherMode.NONE
    label: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.path, "output.path")
        _non_empty(self.subtype, "output.subtype")
        _optional_string(self.label, "output.label")
        object.__setattr__(self, "mode", _enum(self.mode, OutputMode, "output.mode"))
        object.__setattr__(self, "dither", _enum(self.dither, DitherMode, "output.dither"))


@dataclass(frozen=True, slots=True)
class AudioConfig:
    """File normalization and basic processing limits."""

    internal_sample_rate: int = 44_100
    max_length_seconds: float = 15 * 60
    allow_identical_target_and_reference: bool = False
    temp_directory: str | None = None
    metadata_policy: MetadataPolicy = MetadataPolicy.DROP

    def __post_init__(self) -> None:
        _positive_int(self.internal_sample_rate, "audio.internal_sample_rate")
        _finite_positive(self.max_length_seconds, "audio.max_length_seconds")
        _boolean(
            self.allow_identical_target_and_reference,
            "audio.allow_identical_target_and_reference",
        )
        _optional_non_empty(self.temp_directory, "audio.temp_directory")
        object.__setattr__(
            self,
            "metadata_policy",
            _enum(self.metadata_policy, MetadataPolicy, "audio.metadata_policy"),
        )


@dataclass(frozen=True, slots=True)
class MatchingConfig:
    """Level and spectral matching controls."""

    loudness_metric: LoudnessMetric = LoudnessMetric.MATCHERING_RMS
    amount: float = 1.0
    max_piece_seconds: float = 15.0
    fft_size: int = 4096
    lin_log_oversampling: int = 4
    rms_correction_steps: int = 4
    min_value: float = 1e-6
    lowess_fraction: float = 0.0375
    lowess_iterations: int = 0
    lowess_delta: float = 0.001
    # ``None`` preserves the exact Matchering 2.0.6 transfer curve. A finite
    # ceiling is a planned native-engine safety feature and must not be
    # silently claimed by the compatibility adapter.
    max_eq_gain_db: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "loudness_metric",
            _enum(
                self.loudness_metric,
                LoudnessMetric,
                "matching.loudness_metric",
            ),
        )
        _finite_between(self.amount, 0.0, 1.0, "matching.amount")
        _finite_positive(self.max_piece_seconds, "matching.max_piece_seconds")
        if (
            not isinstance(self.fft_size, int)
            or isinstance(self.fft_size, bool)
            or self.fft_size < 8
        ):
            raise ValueError("matching.fft_size must be an integer of at least 8")
        if self.fft_size & (self.fft_size - 1):
            raise ValueError("matching.fft_size must be a power of two")
        _positive_int(self.lin_log_oversampling, "matching.lin_log_oversampling")
        _non_negative_int(self.rms_correction_steps, "matching.rms_correction_steps")
        _finite_positive(self.min_value, "matching.min_value")
        _finite_positive(self.lowess_fraction, "matching.lowess_fraction")
        if self.lowess_fraction > 1:
            raise ValueError("matching.lowess_fraction must not exceed 1")
        _non_negative_int(self.lowess_iterations, "matching.lowess_iterations")
        _finite_non_negative(self.lowess_delta, "matching.lowess_delta")
        if self.max_eq_gain_db is not None:
            _finite_positive(self.max_eq_gain_db, "matching.max_eq_gain_db")


@dataclass(frozen=True, slots=True)
class LimiterConfig:
    """Final limiter and peak policy."""

    kind: LimiterKind = LimiterKind.HYRAX
    peak_mode: PeakMode = PeakMode.SAMPLE_PEAK
    threshold_linear: float = (2**15 - 61) / 2**15
    true_peak_oversampling: int = 4
    attack_ms: float = 1.0
    hold_ms: float = 1.0
    release_ms: float = 3000.0
    attack_filter_coefficient: float = -2.0
    hold_filter_order: int = 1
    hold_filter_coefficient: float = 7.0
    release_filter_order: int = 1
    release_filter_coefficient: float = 800.0
    external_command: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _enum(self.kind, LimiterKind, "limiter.kind"),
        )
        object.__setattr__(
            self,
            "peak_mode",
            _enum(self.peak_mode, PeakMode, "limiter.peak_mode"),
        )
        _finite_between(
            self.threshold_linear,
            0.0,
            1.0,
            "limiter.threshold_linear",
            inclusive_low=False,
            inclusive_high=False,
        )
        _positive_int(self.true_peak_oversampling, "limiter.true_peak_oversampling")
        _finite_positive(self.attack_ms, "limiter.attack_ms")
        _finite_positive(self.hold_ms, "limiter.hold_ms")
        _finite_positive(self.release_ms, "limiter.release_ms")
        _finite(
            self.attack_filter_coefficient,
            "limiter.attack_filter_coefficient",
        )
        _positive_int(self.hold_filter_order, "limiter.hold_filter_order")
        _finite(
            self.hold_filter_coefficient,
            "limiter.hold_filter_coefficient",
        )
        _positive_int(self.release_filter_order, "limiter.release_filter_order")
        _finite(
            self.release_filter_coefficient,
            "limiter.release_filter_coefficient",
        )
        _string_tuple(self.external_command, "limiter.external_command")
        if self.kind is LimiterKind.EXTERNAL and not self.external_command:
            raise ValueError("limiter.external_command is required when limiter.kind is 'external'")


@dataclass(frozen=True, slots=True)
class PreviewConfig:
    """Optional aligned A/B preview outputs."""

    enabled: bool = False
    target_path: str | None = None
    result_path: str | None = None
    subtype: str = "PCM_16"
    duration_seconds: float = 30.0
    analysis_step_seconds: float = 5.0
    fade_seconds: float = 1.0
    fade_coefficient: float = 8.0

    def __post_init__(self) -> None:
        _boolean(self.enabled, "preview.enabled")
        _optional_non_empty(self.target_path, "preview.target_path")
        _optional_non_empty(self.result_path, "preview.result_path")
        _non_empty(self.subtype, "preview.subtype")
        if self.enabled and not (self.target_path or self.result_path):
            raise ValueError(
                "preview.target_path or preview.result_path is required when previews are enabled"
            )
        _finite_positive(self.duration_seconds, "preview.duration_seconds")
        _finite_positive(self.analysis_step_seconds, "preview.analysis_step_seconds")
        _finite_positive(self.fade_seconds, "preview.fade_seconds")
        checked_fade_coefficient = _finite(
            self.fade_coefficient,
            "preview.fade_coefficient",
        )
        if self.duration_seconds <= self.analysis_step_seconds:
            raise ValueError(
                "preview.duration_seconds must be greater than preview.analysis_step_seconds"
            )
        if checked_fade_coefficient < 2:
            raise ValueError("preview.fade_coefficient must be at least 2")


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Thresholds used by preflight diagnostics and upstream warnings."""

    silence_peak: float = 1e-12
    near_silence_peak: float = 1e-4
    clipping_peak: float = 1.0
    clipping_samples_threshold: int = 8
    limited_samples_threshold: int = 128

    def __post_init__(self) -> None:
        _finite_non_negative(self.silence_peak, "detection.silence_peak")
        _finite_positive(self.near_silence_peak, "detection.near_silence_peak")
        if self.near_silence_peak <= self.silence_peak:
            raise ValueError(
                "detection.near_silence_peak must be greater than detection.silence_peak"
            )
        _finite_positive(self.clipping_peak, "detection.clipping_peak")
        _non_negative_int(
            self.clipping_samples_threshold,
            "detection.clipping_samples_threshold",
        )
        _positive_int(
            self.limited_samples_threshold,
            "detection.limited_samples_threshold",
        )
        if self.limited_samples_threshold <= self.clipping_samples_threshold:
            raise ValueError(
                "detection.limited_samples_threshold must exceed clipping_samples_threshold"
            )


@dataclass(frozen=True, slots=True)
class EdgeCasePolicy:
    """Explicit actions for known hazardous or lossy conditions."""

    silence: PolicyAction = PolicyAction.ERROR
    near_silence: PolicyAction = PolicyAction.ERROR
    non_finite_audio: PolicyAction = PolicyAction.ERROR
    mono_input: PolicyAction = PolicyAction.WARN
    more_than_two_channels: PolicyAction = PolicyAction.ERROR
    lossy_input: PolicyAction = PolicyAction.WARN
    sample_rate_conversion: PolicyAction = PolicyAction.WARN
    existing_output: PolicyAction = PolicyAction.ERROR
    create_output_directories: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "silence",
            "near_silence",
            "non_finite_audio",
            "mono_input",
            "more_than_two_channels",
            "lossy_input",
            "sample_rate_conversion",
            "existing_output",
        ):
            object.__setattr__(
                self,
                field_name,
                _enum(
                    getattr(self, field_name),
                    PolicyAction,
                    f"edge_cases.{field_name}",
                ),
            )
        _boolean(
            self.create_output_directories,
            "edge_cases.create_output_directories",
        )


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Run isolation, selected engine, and audit artifact locations."""

    engine: EngineKind = EngineKind.UPSTREAM
    job_id: str | None = None
    manifest_path: str | None = None
    event_log_path: str | None = None
    max_workers: int = 1
    dry_run: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "engine",
            _enum(self.engine, EngineKind, "execution.engine"),
        )
        _positive_int(self.max_workers, "execution.max_workers")
        if self.job_id is not None:
            _non_empty(self.job_id, "execution.job_id")
        _optional_non_empty(self.manifest_path, "execution.manifest_path")
        _optional_non_empty(self.event_log_path, "execution.event_log_path")
        _boolean(self.dry_run, "execution.dry_run")


@dataclass(frozen=True, slots=True)
class JobConfig:
    """Complete, serializable mastering job contract."""

    target: str
    references: tuple[ReferenceSpec, ...]
    outputs: tuple[OutputSpec, ...]
    schema_version: int = SCHEMA_VERSION
    audio: AudioConfig = field(default_factory=AudioConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    limiter: LimiterConfig = field(default_factory=LimiterConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    edge_cases: EdgeCasePolicy = field(default_factory=EdgeCasePolicy)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    notes: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.target, "target")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ValueError("schema_version must be an integer")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {self.schema_version}; expected {SCHEMA_VERSION}"
            )
        if not isinstance(self.references, tuple) or not all(
            isinstance(item, ReferenceSpec) for item in self.references
        ):
            raise ValueError("references must be a tuple of ReferenceSpec values")
        if not isinstance(self.outputs, tuple) or not all(
            isinstance(item, OutputSpec) for item in self.outputs
        ):
            raise ValueError("outputs must be a tuple of OutputSpec values")
        for field_name, value, expected_type in (
            ("audio", self.audio, AudioConfig),
            ("matching", self.matching, MatchingConfig),
            ("limiter", self.limiter, LimiterConfig),
            ("preview", self.preview, PreviewConfig),
            ("detection", self.detection, DetectionConfig),
            ("edge_cases", self.edge_cases, EdgeCasePolicy),
            ("execution", self.execution, ExecutionConfig),
        ):
            if not isinstance(value, expected_type):
                raise ValueError(f"{field_name} must be a {expected_type.__name__} value")
        _optional_string(self.notes, "notes")
        if not self.references:
            raise ValueError("at least one reference is required")
        if not self.outputs:
            raise ValueError("at least one output is required")
        if not any(item.level_weight > 0 for item in self.references):
            raise ValueError("at least one reference level_weight must be greater than zero")
        if not any(item.frequency_weight > 0 for item in self.references):
            raise ValueError("at least one reference frequency_weight must be greater than zero")
        if self.matching.max_piece_seconds >= self.audio.max_length_seconds:
            raise ValueError(
                "matching.max_piece_seconds must be less than audio.max_length_seconds"
            )
        if self.limiter.kind is LimiterKind.HYRAX:
            attack_samples = int(self.audio.internal_sample_rate * self.limiter.attack_ms * 1e-3)
            hold_samples = int(self.audio.internal_sample_rate * self.limiter.hold_ms * 1e-3)
            nyquist = self.audio.internal_sample_rate / 2
            release_cutoff = self.limiter.release_filter_coefficient / self.limiter.release_ms
            if attack_samples < 1:
                raise ValueError(
                    "limiter.attack_ms must produce at least one sample at "
                    "audio.internal_sample_rate"
                )
            if hold_samples < 3:
                raise ValueError(
                    "limiter.hold_ms must produce at least three samples at "
                    "audio.internal_sample_rate"
                )
            if self.limiter.attack_filter_coefficient >= 0:
                raise ValueError(
                    "limiter.attack_filter_coefficient must be negative for the Hyrax envelope"
                )
            if not 0 < self.limiter.hold_filter_coefficient < nyquist:
                raise ValueError(
                    "limiter.hold_filter_coefficient must be between zero and "
                    "the internal Nyquist frequency"
                )
            if not 0 < release_cutoff < nyquist:
                raise ValueError(
                    "limiter.release_filter_coefficient / limiter.release_ms "
                    "must be between zero and the internal Nyquist frequency"
                )

    @property
    def normalized_level_weights(self) -> tuple[float, ...]:
        return _normalized_weights(tuple(item.level_weight for item in self.references))

    @property
    def normalized_frequency_weights(self) -> tuple[float, ...]:
        return _normalized_weights(tuple(item.frequency_weight for item in self.references))

    def resolved(self, base_directory: str | Path) -> JobConfig:
        """Return a copy whose relative paths are anchored to ``base_directory``."""

        base = Path(base_directory).resolve()

        def resolve(value: str | None) -> str | None:
            if value is None:
                return None
            path = Path(value)
            return str(path if path.is_absolute() else (base / path).resolve())

        return replace(
            self,
            target=resolve(self.target) or self.target,
            references=tuple(
                replace(reference, path=resolve(reference.path) or reference.path)
                for reference in self.references
            ),
            outputs=tuple(
                replace(output, path=resolve(output.path) or output.path) for output in self.outputs
            ),
            audio=replace(
                self.audio,
                temp_directory=resolve(self.audio.temp_directory),
            ),
            preview=replace(
                self.preview,
                target_path=resolve(self.preview.target_path),
                result_path=resolve(self.preview.result_path),
            ),
            execution=replace(
                self.execution,
                manifest_path=resolve(self.execution.manifest_path),
                event_log_path=resolve(self.execution.event_log_path),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_ready(asdict(self)))

    def to_json(self, *, indent: int = 2) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=indent,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> JobConfig:
        _reject_unknown_keys(
            data,
            {
                "schema_version",
                "target",
                "references",
                "outputs",
                "audio",
                "matching",
                "limiter",
                "preview",
                "detection",
                "edge_cases",
                "execution",
                "notes",
            },
            "job",
        )
        raw_schema_version = data.get("schema_version", SCHEMA_VERSION)
        if not isinstance(raw_schema_version, int) or isinstance(raw_schema_version, bool):
            raise ValueError("schema_version must be an integer")
        raw_target = data.get("target", "")
        if not isinstance(raw_target, str):
            raise ValueError("target must be a string")
        references = tuple(
            _dataclass_from_mapping(ReferenceSpec, item, f"references[{index}]")
            for index, item in enumerate(_sequence(data.get("references"), "references"))
        )
        outputs = tuple(
            _dataclass_from_mapping(OutputSpec, item, f"outputs[{index}]")
            for index, item in enumerate(_sequence(data.get("outputs"), "outputs"))
        )
        return cls(
            schema_version=raw_schema_version,
            target=raw_target,
            references=references,
            outputs=outputs,
            audio=_nested(AudioConfig, data, "audio"),
            matching=_nested(MatchingConfig, data, "matching"),
            limiter=_nested(LimiterConfig, data, "limiter"),
            preview=_nested(PreviewConfig, data, "preview"),
            detection=_nested(DetectionConfig, data, "detection"),
            edge_cases=_nested(EdgeCasePolicy, data, "edge_cases"),
            execution=_nested(ExecutionConfig, data, "execution"),
            notes=_optional_string(data.get("notes"), "notes"),
        )


def load_job_config(path: str | Path, *, resolve_paths: bool = True) -> JobConfig:
    """Load a job from JSON and optionally resolve paths relative to that file."""

    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(
            handle,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    if not isinstance(raw, Mapping):
        raise ValueError("job configuration root must be a JSON object")
    job = JobConfig.from_dict(raw)
    return job.resolved(config_path.parent) if resolve_paths else job


def save_job_config(job: JobConfig, path: str | Path) -> None:
    """Write a normalized job configuration."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(job.to_json(), encoding="utf-8")


T = TypeVar("T")


def _nested(type_: type[T], data: Mapping[str, Any], key: str) -> T:
    value = data.get(key, {})
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return _dataclass_from_mapping(type_, value, key)


def _dataclass_from_mapping(type_: type[T], value: Any, name: str) -> T:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    fields = type_.__dataclass_fields__  # type: ignore[attr-defined]
    _reject_unknown_keys(value, set(fields), name)
    converted = dict(value)
    enum_fields: dict[str, type[StringEnum]] = {
        "engine": EngineKind,
        "mode": OutputMode,
        "dither": DitherMode,
        "loudness_metric": LoudnessMetric,
        "kind": LimiterKind,
        "peak_mode": PeakMode,
        "metadata_policy": MetadataPolicy,
        "silence": PolicyAction,
        "near_silence": PolicyAction,
        "non_finite_audio": PolicyAction,
        "mono_input": PolicyAction,
        "more_than_two_channels": PolicyAction,
        "lossy_input": PolicyAction,
        "sample_rate_conversion": PolicyAction,
        "existing_output": PolicyAction,
    }
    for field_name, enum_type in enum_fields.items():
        if field_name in converted:
            try:
                converted[field_name] = enum_type(converted[field_name])
            except (TypeError, ValueError) as exc:
                allowed = ", ".join(member.value for member in enum_type)
                raise ValueError(f"{name}.{field_name} must be one of: {allowed}") from exc
    if "external_command" in converted:
        converted["external_command"] = tuple(
            _sequence(converted["external_command"], f"{name}.external_command")
        )
    try:
        return type_(**converted)
    except TypeError as exc:
        raise ValueError(f"invalid {name}: {exc}") from exc


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a JSON array")
    return cast(Sequence[Any], value)


def _reject_unknown_keys(data: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(unknown)}")


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


E = TypeVar("E", bound=StringEnum)


def _enum(value: Any, enum_type: type[E], name: str) -> E:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} must be one of: {allowed}") from exc


def _optional_non_empty(value: Any, name: str) -> str | None:
    checked = _optional_string(value, name)
    if checked is not None:
        _non_empty(checked, name)
    return checked


def _boolean(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


def _string_tuple(value: Any, name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be a tuple of strings")
    for index, item in enumerate(value):
        _non_empty(item, f"{name}[{index}]")


def _non_empty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _finite(value: float, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _finite_positive(value: float, name: str) -> None:
    if _finite(value, name) <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _finite_non_negative(value: float, name: str) -> None:
    if _finite(value, name) < 0:
        raise ValueError(f"{name} must be non-negative")


def _finite_between(
    value: float,
    low: float,
    high: float,
    name: str,
    *,
    inclusive_low: bool = True,
    inclusive_high: bool = True,
) -> None:
    checked = _finite(value, name)
    low_ok = checked >= low if inclusive_low else checked > low
    high_ok = checked <= high if inclusive_high else checked < high
    if not (low_ok and high_ok):
        left = "[" if inclusive_low else "("
        right = "]" if inclusive_high else ")"
        raise ValueError(f"{name} must be in {left}{low}, {high}{right}")


def _positive_int(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _non_negative_int(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _normalized_weights(weights: tuple[float, ...]) -> tuple[float, ...]:
    largest = max(weights)
    scaled = tuple(weight / largest for weight in weights)
    total = math.fsum(scaled)
    return tuple(weight / total for weight in scaled)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")
