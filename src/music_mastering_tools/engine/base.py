"""Engine contracts for compatibility and future native processing backends."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from ..config import JobConfig


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """Machine-readable statement of what an engine can faithfully execute."""

    engine_id: str
    engine_version: str
    implementation_status: str
    runnable: bool
    maximum_references: int | None
    independent_reference_weights: bool
    partial_matching_amount: bool
    ebu_r128_loudness: bool
    spectral_gain_ceiling: bool
    sample_peak_limiter: bool
    true_peak_limiter: bool
    external_limiter: bool
    dither: bool
    previews: bool
    thread_safe: bool
    planned_capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngineLogRecord:
    """One message emitted from an underlying DSP engine."""

    level: str
    message: str
    code: int | None = None


EngineLogHandler = Callable[[EngineLogRecord], None]


@dataclass(frozen=True, slots=True)
class EngineRunResult:
    """Facts returned after an engine successfully renders a job."""

    engine_id: str
    engine_version: str
    output_paths: tuple[str, ...]
    started_at: str
    finished_at: str
    duration_seconds: float
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Defensively freeze engine provenance returned to the manifest."""

        object.__setattr__(self, "details", _freeze_detail_mapping(self.details))

    @classmethod
    def completed(
        cls,
        *,
        engine_id: str,
        engine_version: str,
        output_paths: tuple[str, ...],
        started_at: datetime,
        duration_seconds: float,
        details: Mapping[str, object] | None = None,
    ) -> EngineRunResult:
        finished_at = datetime.now(UTC)
        return cls(
            engine_id=engine_id,
            engine_version=engine_version,
            output_paths=output_paths,
            started_at=started_at.astimezone(UTC).isoformat(),
            finished_at=finished_at.isoformat(),
            duration_seconds=duration_seconds,
            details=details or {},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "output_paths": list(self.output_paths),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "details": _thaw_detail(self.details),
        }


class MasteringEngine(Protocol):
    """Processing backend used by the orchestration service."""

    @property
    def capabilities(self) -> EngineCapabilities: ...

    def validate_capabilities(self, job: JobConfig) -> None: ...

    def run(
        self,
        job: JobConfig,
        *,
        log_handler: EngineLogHandler | None = None,
    ) -> EngineRunResult: ...


def requested_artifact_paths(job: JobConfig) -> tuple[str, ...]:
    """Return every render path in deterministic job order."""

    paths = [item.path for item in job.outputs]
    if job.preview.enabled:
        paths.extend(
            value
            for value in (job.preview.target_path, job.preview.result_path)
            if value is not None
        )
    return tuple(str(Path(path)) for path in paths)


def _freeze_detail_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("engine result details must be a mapping")
    return MappingProxyType({str(key): _freeze_detail(item) for key, item in value.items()})


def _freeze_detail(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_detail_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_detail(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("engine result details must contain only finite numbers")
        return value
    raise TypeError(
        "engine result details must contain only JSON-oriented values; "
        f"received {type(value).__name__}"
    )


def _thaw_detail(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_detail(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_detail(item) for item in value]
    return value


__all__ = [
    "EngineCapabilities",
    "EngineLogHandler",
    "EngineLogRecord",
    "EngineRunResult",
    "MasteringEngine",
    "requested_artifact_paths",
]
