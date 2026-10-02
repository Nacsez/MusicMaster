"""Reproducible run manifests, artifact fingerprints, and atomic persistence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .errors import ErrorCode, ManifestError, MusicMasteringError

MANIFEST_SCHEMA_VERSION = 1
DEFAULT_HASH_CHUNK_SIZE = 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    """Lifecycle states persisted in a run manifest."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # A readable alias for integrations that use "succeeded".
    SUCCEEDED = "completed"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """A content fingerprint and the file facts used to calculate it."""

    path: str
    size_bytes: int
    sha256: str
    modified_ns: int | None = None
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("fingerprint path must be a non-empty string")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ValueError("fingerprint size_bytes must be a non-negative integer")
        if self.modified_ns is not None and (
            not isinstance(self.modified_ns, int)
            or isinstance(self.modified_ns, bool)
            or self.modified_ns < 0
        ):
            raise ValueError("fingerprint modified_ns must be non-negative or None")
        if self.algorithm != "sha256":
            raise ValueError("only the sha256 fingerprint algorithm is supported")
        normalized = self.sha256.casefold()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("fingerprint sha256 must be 64 hexadecimal characters")
        object.__setattr__(self, "sha256", normalized)

    @property
    def digest(self) -> str:
        """Algorithm-neutral spelling of :attr:`sha256`."""

        return self.sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "algorithm": self.algorithm,
            "sha256": self.sha256,
            "modified_ns": self.modified_ns,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FileFingerprint:
        digest = data.get("sha256", data.get("digest"))
        return cls(
            path=_required_string(data.get("path"), "fingerprint.path"),
            size_bytes=_required_int(data.get("size_bytes"), "fingerprint.size_bytes", minimum=0),
            sha256=_required_string(digest, "fingerprint.sha256"),
            modified_ns=_optional_int(
                data.get("modified_ns"), "fingerprint.modified_ns", minimum=0
            ),
            algorithm=str(data.get("algorithm", "sha256")),
        )


def fingerprint_file(
    path: str | os.PathLike[str],
    *,
    chunk_size: int = DEFAULT_HASH_CHUNK_SIZE,
) -> FileFingerprint:
    """Calculate a streaming SHA-256 fingerprint without loading a file.

    The file descriptor is statted before and after hashing.  If its size or
    modification timestamp changes during the read, the result is rejected
    rather than recording a potentially mixed-content digest.
    """

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    requested = Path(path)
    try:
        source = requested.resolve(strict=True)
        if not source.is_file():
            raise OSError(f"path is not a regular file: {source}")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            before = os.fstat(handle.fileno())
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = source.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or after.st_size != current.st_size
            or after.st_mtime_ns != current.st_mtime_ns
            or not os.path.samestat(after, current)
        ):
            raise OSError("file changed while its fingerprint was being calculated")
    except (OSError, RuntimeError) as exc:
        raise ManifestError(
            f"could not fingerprint '{requested}': {exc}",
            code=ErrorCode.FINGERPRINT_FAILED,
            details={"path": str(requested)},
        ) from exc

    return FileFingerprint(
        path=str(source),
        size_bytes=after.st_size,
        sha256=digest.hexdigest(),
        modified_ns=after.st_mtime_ns,
    )


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """One input, output, preview, log, or report associated with a run."""

    path: str
    role: str = "artifact"
    name: str | None = None
    fingerprint: FileFingerprint | None = None
    media_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.path, "artifact.path")
        _required_string(self.role, "artifact.role")
        if self.name is not None:
            _required_string(self.name, "artifact.name")
        if self.media_type is not None:
            _required_string(self.media_type, "artifact.media_type")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("artifact.metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_json_mapping(self.metadata))

    @classmethod
    def from_file(
        cls,
        path: str | os.PathLike[str],
        *,
        role: str,
        name: str | None = None,
        media_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        chunk_size: int = DEFAULT_HASH_CHUNK_SIZE,
    ) -> ArtifactManifest:
        """Fingerprint a file and describe it as a run artifact."""

        fingerprint = fingerprint_file(path, chunk_size=chunk_size)
        return cls(
            path=fingerprint.path,
            role=role,
            name=name,
            fingerprint=fingerprint,
            media_type=media_type,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "name": self.name,
            "fingerprint": (self.fingerprint.to_dict() if self.fingerprint is not None else None),
            "media_type": self.media_type,
            "metadata": _json_ready(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactManifest:
        raw_fingerprint = data.get("fingerprint")
        if raw_fingerprint is not None and not isinstance(raw_fingerprint, Mapping):
            raise ValueError("artifact.fingerprint must be a JSON object or null")
        raw_metadata = data.get("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError("artifact.metadata must be a JSON object")
        return cls(
            path=_required_string(data.get("path"), "artifact.path"),
            role=_required_string(data.get("role", "artifact"), "artifact.role"),
            name=_optional_string(data.get("name"), "artifact.name"),
            fingerprint=(
                FileFingerprint.from_dict(raw_fingerprint) if raw_fingerprint is not None else None
            ),
            media_type=_optional_string(data.get("media_type"), "artifact.media_type"),
            metadata=dict(raw_metadata),
        )


@dataclass(frozen=True, slots=True)
class MetricManifest:
    """One measured value with optional units, stage, and annotations."""

    name: str
    value: int | float | str | bool | None
    unit: str | None = None
    stage: str | None = None
    recorded_at: datetime = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.name, "metric.name")
        if not isinstance(self.value, (int, float, str, bool, type(None))):
            raise TypeError("metric.value must be a JSON scalar or null")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("metric.value must be finite; use null when undefined")
        if self.unit is not None:
            _required_string(self.unit, "metric.unit")
        if self.stage is not None:
            _required_string(self.stage, "metric.stage")
        if not isinstance(self.recorded_at, datetime):
            raise TypeError("metric.recorded_at must be a datetime")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metric.metadata must be a mapping")
        object.__setattr__(self, "recorded_at", _as_utc(self.recorded_at))
        object.__setattr__(self, "metadata", _freeze_json_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "stage": self.stage,
            "recorded_at": _format_timestamp(self.recorded_at),
            "metadata": _json_ready(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MetricManifest:
        raw_metadata = data.get("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError("metric.metadata must be a JSON object")
        raw_recorded_at = data.get("recorded_at")
        recorded_at = (
            _parse_timestamp(raw_recorded_at, "metric.recorded_at")
            if raw_recorded_at is not None
            else _utc_now()
        )
        return cls(
            name=_required_string(data.get("name"), "metric.name"),
            value=data.get("value"),
            unit=_optional_string(data.get("unit"), "metric.unit"),
            stage=_optional_string(data.get("stage"), "metric.stage"),
            recorded_at=recorded_at,
            metadata=dict(raw_metadata),
        )


@dataclass(slots=True)
class RunManifest:
    """Living audit record for one mastering job.

    The manifest is intentionally mutable while a job is running.  Persisted
    snapshots are deterministic JSON documents and all mutating helpers update
    ``updated_at``.
    """

    schema_version: int = MANIFEST_SCHEMA_VERSION
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: RunStatus = RunStatus.CREATED
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    tool_name: str = "music-mastering-tools"
    tool_version: str | None = None
    command: tuple[str, ...] = ()
    configuration: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, str | None] = field(default_factory=dict)
    inputs: list[ArtifactManifest] = field(default_factory=list)
    outputs: list[ArtifactManifest] = field(default_factory=list)
    metrics: list[MetricManifest] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    notes: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported manifest schema_version {self.schema_version}; "
                f"expected {MANIFEST_SCHEMA_VERSION}"
            )
        _required_string(self.run_id, "manifest.run_id")
        self.status = self.status if isinstance(self.status, RunStatus) else RunStatus(self.status)
        self.created_at = _as_utc(self.created_at)
        self.updated_at = _as_utc(self.updated_at)
        _required_string(self.tool_name, "manifest.tool_name")
        if self.tool_version is not None:
            _required_string(self.tool_version, "manifest.tool_version")
        self.command = tuple(str(part) for part in self.command)
        self.configuration = _copy_json_mapping(
            self.configuration,
            "manifest.configuration",
        )
        self.environment = _copy_json_mapping(
            self.environment,
            "manifest.environment",
        )
        self.dependencies = {
            str(name): None if version is None else str(version)
            for name, version in self.dependencies.items()
        }
        self.inputs = list(self.inputs)
        self.outputs = list(self.outputs)
        self.metrics = list(self.metrics)
        self.warnings = [str(item) for item in self.warnings]
        self.error = (
            None if self.error is None else _copy_json_mapping(self.error, "manifest.error")
        )
        self.extensions = _copy_json_mapping(
            self.extensions,
            "manifest.extensions",
        )
        if self.notes is not None and not isinstance(self.notes, str):
            raise TypeError("manifest.notes must be a string or None")

    @classmethod
    def create(
        cls,
        *,
        run_id: str | None = None,
        tool_version: str | None = None,
        command: Sequence[str] = (),
        configuration: Mapping[str, Any] | None = None,
        environment: Mapping[str, Any] | None = None,
    ) -> RunManifest:
        """Create a run with a single consistent initial timestamp."""

        now = _utc_now()
        default_environment = {
            "python": platform.python_version(),
            "platform": platform.platform(),
        }
        if environment is not None:
            default_environment.update(environment)
        return cls(
            run_id=run_id or uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
            tool_version=tool_version,
            command=tuple(command),
            configuration=dict(configuration or {}),
            environment=default_environment,
        )

    @property
    def config(self) -> dict[str, Any]:
        """Compatibility shorthand for :attr:`configuration`."""

        return self.configuration

    def touch(self, *, at: datetime | None = None) -> None:
        """Update the manifest modification timestamp."""

        self.updated_at = _as_utc(at or _utc_now())

    def set_status(self, status: RunStatus | str) -> None:
        self.status = status if isinstance(status, RunStatus) else RunStatus(status)
        self.touch()

    def add_input(self, artifact: ArtifactManifest) -> ArtifactManifest:
        if not isinstance(artifact, ArtifactManifest):
            raise TypeError("input must be an ArtifactManifest")
        self.inputs.append(artifact)
        self.touch()
        return artifact

    def add_output(self, artifact: ArtifactManifest) -> ArtifactManifest:
        if not isinstance(artifact, ArtifactManifest):
            raise TypeError("output must be an ArtifactManifest")
        self.outputs.append(artifact)
        self.touch()
        return artifact

    def add_metric(self, metric: MetricManifest) -> MetricManifest:
        if not isinstance(metric, MetricManifest):
            raise TypeError("metric must be a MetricManifest")
        self.metrics.append(metric)
        self.touch()
        return metric

    def add_warning(self, message: str) -> None:
        self.warnings.append(_required_string(message, "warning"))
        self.touch()

    def mark_running(self) -> None:
        self.set_status(RunStatus.RUNNING)

    def mark_completed(self) -> None:
        self.error = None
        self.set_status(RunStatus.COMPLETED)

    def mark_failed(
        self,
        error: MusicMasteringError | BaseException | Mapping[str, Any] | str,
    ) -> None:
        """Record a normalized failure and mark the run failed."""

        if isinstance(error, MusicMasteringError):
            normalized = error.to_dict()
        elif isinstance(error, Mapping):
            normalized = dict(error)
        elif isinstance(error, BaseException):
            normalized = {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": str(error),
                "exception_type": type(error).__name__,
            }
        else:
            normalized = {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": str(error),
            }
        self.error = _copy_json_mapping(normalized, "manifest.error")
        self.set_status(RunStatus.FAILED)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-oriented manifest representation."""

        data = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status.value,
            "created_at": _format_timestamp(self.created_at),
            "updated_at": _format_timestamp(self.updated_at),
            "tool": {
                "name": self.tool_name,
                "version": self.tool_version,
            },
            "command": list(self.command),
            "configuration": _json_ready(self.configuration),
            "environment": _json_ready(self.environment),
            "dependencies": _json_ready(self.dependencies),
            "inputs": [artifact.to_dict() for artifact in self.inputs],
            "outputs": [artifact.to_dict() for artifact in self.outputs],
            "metrics": [metric.to_dict() for metric in self.metrics],
            "warnings": list(self.warnings),
            "error": _json_ready(self.error),
            "notes": self.notes,
        }
        for key, value in self.extensions.items():
            if key not in data:
                data[key] = _json_ready(value)
        return data

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the manifest as deterministic, strict JSON."""

        return (
            json.dumps(
                self.to_dict(),
                indent=indent,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunManifest:
        """Validate and reconstruct a manifest from decoded JSON."""

        schema_version = _required_int(
            data.get("schema_version", MANIFEST_SCHEMA_VERSION),
            "manifest.schema_version",
            minimum=1,
        )
        raw_tool = data.get("tool", {})
        if not isinstance(raw_tool, Mapping):
            raise ValueError("manifest.tool must be a JSON object")
        raw_configuration = _mapping(data.get("configuration", {}), "configuration")
        raw_environment = _mapping(data.get("environment", {}), "environment")
        raw_dependencies = _mapping(data.get("dependencies", {}), "dependencies")
        raw_error = data.get("error")
        if raw_error is not None and not isinstance(raw_error, Mapping):
            raise ValueError("manifest.error must be a JSON object or null")

        known = {
            "schema_version",
            "run_id",
            "status",
            "created_at",
            "updated_at",
            "tool",
            "command",
            "configuration",
            "environment",
            "dependencies",
            "inputs",
            "outputs",
            "metrics",
            "warnings",
            "error",
            "notes",
        }
        return cls(
            schema_version=schema_version,
            run_id=_required_string(data.get("run_id"), "manifest.run_id"),
            status=RunStatus(data.get("status", RunStatus.CREATED.value)),
            created_at=_parse_timestamp(data.get("created_at"), "manifest.created_at"),
            updated_at=_parse_timestamp(data.get("updated_at"), "manifest.updated_at"),
            tool_name=_required_string(
                raw_tool.get("name", "music-mastering-tools"),
                "manifest.tool.name",
            ),
            tool_version=_optional_string(raw_tool.get("version"), "manifest.tool.version"),
            command=tuple(_string_sequence(data.get("command", []), "manifest.command")),
            configuration=dict(raw_configuration),
            environment=dict(raw_environment),
            dependencies={
                str(name): None if version is None else str(version)
                for name, version in raw_dependencies.items()
            },
            inputs=[
                ArtifactManifest.from_dict(item)
                for item in _mapping_sequence(data.get("inputs", []), "manifest.inputs")
            ],
            outputs=[
                ArtifactManifest.from_dict(item)
                for item in _mapping_sequence(data.get("outputs", []), "manifest.outputs")
            ],
            metrics=[
                MetricManifest.from_dict(item)
                for item in _mapping_sequence(data.get("metrics", []), "manifest.metrics")
            ],
            warnings=_string_sequence(data.get("warnings", []), "manifest.warnings"),
            error=None if raw_error is None else dict(raw_error),
            notes=_optional_string(data.get("notes"), "manifest.notes"),
            extensions={key: value for key, value in data.items() if key not in known},
        )

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Atomically persist this manifest."""

        return ManifestStore(path).save(self)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> RunManifest:
        """Load a manifest from disk."""

        return ManifestStore(path).load()


class ManifestStore:
    """Atomic JSON persistence for one manifest path."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        indent: int = 2,
        create_parents: bool = True,
        overwrite: bool = True,
    ) -> None:
        if not isinstance(indent, int) or isinstance(indent, bool) or indent < 0:
            raise ValueError("indent must be a non-negative integer")
        self.path = Path(path)
        self.indent = indent
        self.create_parents = bool(create_parents)
        self.overwrite = bool(overwrite)
        self._owns_destination = False
        self._last_payload_sha256: str | None = None

    def save(self, manifest: RunManifest) -> Path:
        """Write through a same-directory temporary file and ``os.replace``."""

        if not isinstance(manifest, RunManifest):
            raise TypeError("manifest must be a RunManifest")
        destination = self.path
        if not self.overwrite and self._owns_destination:
            self._assert_owned_destination_unchanged()
        temporary: Path | None = None
        reservation_created = False
        payload_digest: str | None = None
        try:
            if self.create_parents:
                destination.parent.mkdir(parents=True, exist_ok=True)
            payload = manifest.to_json(indent=self.indent)
            payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if not self.overwrite and not self._owns_destination:
                descriptor = os.open(
                    destination,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.close(descriptor)
                reservation_created = True
                self._owns_destination = True
            os.replace(temporary, destination)
            temporary = None
            self._last_payload_sha256 = payload_digest
        except (OSError, TypeError, ValueError) as exc:
            raise ManifestError(
                f"could not write manifest '{destination}': {exc}",
                code=ErrorCode.MANIFEST_WRITE_FAILED,
                details={"path": str(destination)},
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if reservation_created and temporary is not None:
                try:
                    if destination.stat().st_size == 0:
                        destination.unlink()
                        self._owns_destination = False
                except OSError:
                    pass
        return destination

    def _assert_owned_destination_unchanged(self) -> None:
        expected = self._last_payload_sha256
        try:
            current = hashlib.sha256(self.path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ManifestError(
                f"owned manifest '{self.path}' is no longer readable: {exc}",
                code=ErrorCode.MANIFEST_WRITE_FAILED,
                details={"path": str(self.path), "reason": "ownership-lost"},
            ) from exc
        if expected is None or current != expected:
            raise ManifestError(
                f"owned manifest '{self.path}' changed outside this run",
                code=ErrorCode.MANIFEST_WRITE_FAILED,
                details={
                    "path": str(self.path),
                    "reason": "concurrent-modification",
                    "expected_sha256": expected,
                    "actual_sha256": current,
                },
            )

    def load(self) -> RunManifest:
        """Read and validate a manifest without leaking decoder exceptions."""

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestError(
                f"could not read manifest '{self.path}': {exc}",
                code=ErrorCode.MANIFEST_READ_FAILED,
                details={"path": str(self.path)},
            ) from exc
        if not isinstance(raw, Mapping):
            raise ManifestError(
                f"manifest '{self.path}' must contain a JSON object",
                code=ErrorCode.MANIFEST_INVALID,
                details={"path": str(self.path)},
            )
        try:
            return RunManifest.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise ManifestError(
                f"manifest '{self.path}' is invalid: {exc}",
                code=ErrorCode.MANIFEST_INVALID,
                details={"path": str(self.path)},
            ) from exc


Artifact = ArtifactManifest
Metric = MetricManifest


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid ISO-8601 timestamp") from exc
    return _as_utc(parsed)


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("manifest values must be finite JSON numbers")
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _format_timestamp(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _copy_json_mapping(
    value: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    copied = _json_ready(value)
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must be a mapping")
    return copied


def _freeze_json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_json(_copy_json_mapping(value, "metadata"))
    if not isinstance(frozen, Mapping):
        raise TypeError("metadata must be a mapping")
    return frozen


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _required_int(value: Any, name: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _optional_int(value: Any, name: str, *, minimum: int) -> int | None:
    if value is None:
        return None
    return _required_int(value, name, minimum=minimum)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"manifest.{name} must be a JSON object")
    return value


def _mapping_sequence(value: Any, name: str) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a JSON array")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{name}[{index}] must be a JSON object")
        result.append(item)
    return result


def _string_sequence(value: Any, name: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a JSON array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{name}[{index}] must be a string")
        result.append(item)
    return result


__all__ = [
    "Artifact",
    "ArtifactManifest",
    "DEFAULT_HASH_CHUNK_SIZE",
    "FileFingerprint",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestStore",
    "Metric",
    "MetricManifest",
    "RunManifest",
    "RunStatus",
    "fingerprint_file",
]
