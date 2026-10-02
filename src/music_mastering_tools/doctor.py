"""Non-throwing environment diagnostics for optional mastering capabilities.

Only standard-library modules are imported when this module is loaded.
Third-party packages are discovered and, when requested, imported inside
``run_doctor`` so a missing or broken optional dependency becomes a report
entry rather than an application startup failure.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from functools import partial
from pathlib import Path
from typing import Any


class DoctorStatus(StrEnum):
    """Outcome of one diagnostic check."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """Description of one lazily inspected Python dependency."""

    module: str
    distribution: str | None = None
    purpose: str = ""
    required: bool = False
    expected_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.module, str) or not self.module.strip():
            raise ValueError("dependency module must be a non-empty string")
        if self.distribution is not None and (
            not isinstance(self.distribution, str) or not self.distribution.strip()
        ):
            raise ValueError("dependency distribution must be a non-empty string or None")
        if self.expected_version is not None and (
            not isinstance(self.expected_version, str) or not self.expected_version.strip()
        ):
            raise ValueError("dependency expected_version must be a non-empty string or None")

    @property
    def distribution_name(self) -> str:
        return self.distribution or self.module


DEFAULT_OPTIONAL_DEPENDENCIES: tuple[DependencySpec, ...] = (
    DependencySpec(
        "matchering",
        purpose="upstream Matchering 2.0.6 processing engine",
        expected_version="2.0.6",
    ),
    DependencySpec("numpy", purpose="array operations and numerical DSP"),
    DependencySpec("scipy", purpose="signal processing and convolution"),
    DependencySpec("statsmodels", purpose="LOWESS spectral smoothing"),
    DependencySpec("resampy", purpose="sample-rate conversion"),
    DependencySpec(
        "soundfile",
        distribution="soundfile",
        purpose="audio file decoding and encoding through libsndfile",
    ),
)


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One environment check with enough detail for automated diagnosis."""

    name: str
    status: DoctorStatus
    summary: str
    required: bool = False
    version: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    remediation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("doctor check name must be a non-empty string")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("doctor check summary must be a non-empty string")
        object.__setattr__(
            self,
            "status",
            self.status if isinstance(self.status, DoctorStatus) else DoctorStatus(self.status),
        )
        if not isinstance(self.details, Mapping):
            raise TypeError("doctor check details must be a mapping")
        object.__setattr__(self, "details", dict(self.details))

    @property
    def ok(self) -> bool:
        """Whether this check does not prevent required functionality."""

        return self.status is not DoctorStatus.FAIL

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "required": self.required,
            "version": self.version,
            "details": _json_ready(self.details),
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Complete point-in-time environment diagnostic report."""

    checks: tuple[DoctorCheck, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.generated_at, datetime):
            raise TypeError("doctor report generated_at must be a datetime")
        generated_at = self.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        else:
            generated_at = generated_at.astimezone(UTC)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "checks", tuple(self.checks))

    @property
    def healthy(self) -> bool:
        """Whether every required check passed."""

        return all(check.status is not DoctorStatus.FAIL for check in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(check.status is DoctorStatus.WARNING for check in self.checks)

    @property
    def exit_code(self) -> int:
        """Conventional process exit code for a doctor CLI command."""

        return 0 if self.healthy else 1

    def get(self, name: str) -> DoctorCheck | None:
        """Return a named check, if present."""

        return next((check for check in self.checks if check.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "healthy": self.healthy,
            "has_warnings": self.has_warnings,
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self, *, indent: int = 2) -> str:
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

    def render_text(self) -> str:
        """Render a concise human-readable diagnostic summary."""

        lines = []
        for check in self.checks:
            version = f" ({check.version})" if check.version else ""
            lines.append(f"[{check.status.value.upper():7}] {check.name}{version}: {check.summary}")
            if check.remediation and check.status is not DoctorStatus.PASS:
                lines.append(f"          Remedy: {check.remediation}")
        result = "healthy" if self.healthy else "action required"
        lines.append(f"Overall: {result}")
        return "\n".join(lines)


def run_doctor(
    *,
    minimum_python: tuple[int, int] = (3, 11),
    ffmpeg_binary: str | os.PathLike[str] = "ffmpeg",
    dependencies: Iterable[object] | None = None,
    include_optional_dependencies: bool = True,
    probe_imports: bool = True,
    command_timeout_seconds: float = 5.0,
) -> DoctorReport:
    """Inspect the runtime without propagating environmental failures.

    Parameters are injectable so tests and downstream applications can add
    engine-specific dependencies.  Missing default dependencies and FFmpeg are
    warnings because the standard-library launchpad remains usable without
    them; callers can pass a ``DependencySpec(required=True)`` for capabilities
    selected by a concrete job.
    """

    if (
        not isinstance(minimum_python, tuple)
        or len(minimum_python) != 2
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in minimum_python
        )
    ):
        raise ValueError("minimum_python must be a (major, minor) integer tuple")
    if (
        not isinstance(command_timeout_seconds, (int, float))
        or isinstance(command_timeout_seconds, bool)
        or command_timeout_seconds <= 0
    ):
        raise ValueError("command_timeout_seconds must be positive")

    checks: list[DoctorCheck] = [
        _safe_check(
            "python",
            required=True,
            operation=lambda: _check_python(minimum_python),
        ),
        _safe_check(
            "ffmpeg",
            required=False,
            operation=lambda: _check_ffmpeg(ffmpeg_binary, float(command_timeout_seconds)),
        ),
    ]

    selected: list[object] = []
    # Caller-supplied requirements come first so a required engine dependency
    # overrides its optional default check during de-duplication.
    if dependencies is not None:
        selected.extend(dependencies)
    if include_optional_dependencies:
        selected.extend(DEFAULT_OPTIONAL_DEPENDENCIES)

    seen: set[tuple[str, str]] = set()
    for dependency in selected:
        if not isinstance(dependency, DependencySpec):
            checks.append(
                DoctorCheck(
                    name="python-dependency",
                    status=DoctorStatus.WARNING,
                    summary=(
                        "ignored an invalid dependency specification of type "
                        f"{type(dependency).__name__}"
                    ),
                    details={"value": repr(dependency)},
                    remediation="Pass DependencySpec instances to run_doctor().",
                )
            )
            continue
        identity = (dependency.module, dependency.distribution_name)
        if identity in seen:
            continue
        seen.add(identity)
        checks.append(
            _safe_check(
                f"python:{dependency.module}",
                required=dependency.required,
                operation=partial(_check_dependency, dependency, probe_imports=probe_imports),
            )
        )

    return DoctorReport(tuple(checks))


def doctor(**kwargs: Any) -> DoctorReport:
    """Short functional alias for :func:`run_doctor`."""

    return run_doctor(**kwargs)


class Doctor:
    """Reusable doctor configuration for applications and tests."""

    def __init__(
        self,
        *,
        minimum_python: tuple[int, int] = (3, 11),
        ffmpeg_binary: str | os.PathLike[str] = "ffmpeg",
        dependencies: Sequence[DependencySpec] = (),
        include_optional_dependencies: bool = True,
        probe_imports: bool = True,
        command_timeout_seconds: float = 5.0,
    ) -> None:
        self.minimum_python = minimum_python
        self.ffmpeg_binary = ffmpeg_binary
        self.dependencies = tuple(dependencies)
        self.include_optional_dependencies = include_optional_dependencies
        self.probe_imports = probe_imports
        self.command_timeout_seconds = command_timeout_seconds

    def run(self) -> DoctorReport:
        """Execute the configured checks."""

        return run_doctor(
            minimum_python=self.minimum_python,
            ffmpeg_binary=self.ffmpeg_binary,
            dependencies=self.dependencies,
            include_optional_dependencies=self.include_optional_dependencies,
            probe_imports=self.probe_imports,
            command_timeout_seconds=self.command_timeout_seconds,
        )


def _safe_check(
    name: str,
    *,
    required: bool,
    operation: Callable[[], DoctorCheck],
) -> DoctorCheck:
    try:
        return operation()
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        return DoctorCheck(
            name=name,
            status=DoctorStatus.FAIL if required else DoctorStatus.WARNING,
            summary=f"diagnostic raised {type(exc).__name__}: {exc}",
            required=required,
            details={"exception_type": type(exc).__name__},
            remediation="Inspect the environment and rerun the doctor command.",
        )


def _check_python(minimum: tuple[int, int]) -> DoctorCheck:
    running = (sys.version_info.major, sys.version_info.minor)
    passes = running >= minimum
    minimum_text = ".".join(str(part) for part in minimum)
    version = platform.python_version()
    return DoctorCheck(
        name="python",
        status=DoctorStatus.PASS if passes else DoctorStatus.FAIL,
        summary=(
            f"Python {version} satisfies the >= {minimum_text} requirement"
            if passes
            else f"Python {version} is older than required {minimum_text}"
        ),
        required=True,
        version=version,
        details={
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
            "minimum_version": minimum_text,
        },
        remediation=(
            None
            if passes
            else f"Install Python {minimum_text} or newer and recreate the environment."
        ),
    )


def _check_ffmpeg(
    binary: str | os.PathLike[str],
    timeout_seconds: float,
) -> DoctorCheck:
    requested = os.fspath(binary)
    located = shutil.which(requested)
    if located is None:
        candidate = Path(requested)
        if candidate.is_file():
            located = str(candidate.resolve())
    if located is None:
        return DoctorCheck(
            name="ffmpeg",
            status=DoctorStatus.WARNING,
            summary="FFmpeg executable was not found",
            required=False,
            details={"requested": requested},
            remediation=(
                "Install FFmpeg or configure ffmpeg_binary with its executable path; "
                "WAV/SoundFile workflows can still operate without it."
            ),
        )

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [located, "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DoctorCheck(
            name="ffmpeg",
            status=DoctorStatus.WARNING,
            summary=f"FFmpeg was found but could not be executed: {exc}",
            required=False,
            details={"path": located, "exception_type": type(exc).__name__},
            remediation="Verify the executable and its native runtime dependencies.",
        )

    output_lines = completed.stdout.splitlines()
    banner = output_lines[0].strip() if output_lines else ""
    version = _ffmpeg_version_from_banner(banner)
    if completed.returncode != 0:
        return DoctorCheck(
            name="ffmpeg",
            status=DoctorStatus.WARNING,
            summary=f"FFmpeg version probe exited with code {completed.returncode}",
            required=False,
            version=version,
            details={
                "path": located,
                "returncode": completed.returncode,
                "banner": banner,
            },
            remediation="Run `ffmpeg -version` directly and inspect its output.",
        )
    return DoctorCheck(
        name="ffmpeg",
        status=DoctorStatus.PASS,
        summary="FFmpeg executable is available",
        required=False,
        version=version,
        details={"path": located, "banner": banner},
    )


def _check_dependency(
    dependency: DependencySpec,
    *,
    probe_imports: bool,
) -> DoctorCheck:
    missing_status = DoctorStatus.FAIL if dependency.required else DoctorStatus.WARNING
    try:
        spec = importlib.util.find_spec(dependency.module)
    except (ImportError, AttributeError, ValueError) as exc:
        return DoctorCheck(
            name=f"python:{dependency.module}",
            status=missing_status,
            summary=f"dependency discovery failed: {exc}",
            required=dependency.required,
            details={
                "module": dependency.module,
                "distribution": dependency.distribution_name,
                "purpose": dependency.purpose,
                "exception_type": type(exc).__name__,
            },
            remediation=_dependency_remediation(dependency),
        )

    if spec is None:
        return DoctorCheck(
            name=f"python:{dependency.module}",
            status=missing_status,
            summary="Python dependency is not installed",
            required=dependency.required,
            details={
                "module": dependency.module,
                "distribution": dependency.distribution_name,
                "purpose": dependency.purpose,
            },
            remediation=_dependency_remediation(dependency),
        )

    metadata_version: str | None
    try:
        metadata_version = importlib.metadata.version(dependency.distribution_name)
    except importlib.metadata.PackageNotFoundError:
        metadata_version = None
    except Exception:
        metadata_version = None

    module_version: str | None = None
    native_version: str | None = None
    if probe_imports:
        try:
            loaded = importlib.import_module(dependency.module)
            raw_module_version = getattr(loaded, "__version__", None)
            if raw_module_version is not None:
                module_version = str(raw_module_version)
            if dependency.module == "soundfile":
                raw_native_version = getattr(loaded, "__libsndfile_version__", None)
                if raw_native_version is not None:
                    native_version = str(raw_native_version)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            return DoctorCheck(
                name=f"python:{dependency.module}",
                status=missing_status,
                summary=(
                    f"module was discovered but failed to import: {type(exc).__name__}: {exc}"
                ),
                required=dependency.required,
                version=metadata_version,
                details={
                    "module": dependency.module,
                    "distribution": dependency.distribution_name,
                    "purpose": dependency.purpose,
                    "origin": getattr(spec, "origin", None),
                    "exception_type": type(exc).__name__,
                },
                remediation=_dependency_remediation(dependency),
            )

    version = metadata_version or module_version
    details: dict[str, Any] = {
        "module": dependency.module,
        "distribution": dependency.distribution_name,
        "purpose": dependency.purpose,
        "origin": getattr(spec, "origin", None),
        "import_probed": probe_imports,
        "metadata_version": metadata_version,
        "module_version": module_version,
        "expected_version": dependency.expected_version,
    }
    if native_version is not None:
        details["libsndfile_version"] = native_version

    if version is None:
        return DoctorCheck(
            name=f"python:{dependency.module}",
            status=DoctorStatus.WARNING,
            summary="dependency is available but its version could not be determined",
            required=dependency.required,
            details=details,
            remediation=(
                "Install the dependency as a versioned distribution for reproducible runs."
            ),
        )
    if dependency.expected_version is not None and version != dependency.expected_version:
        return DoctorCheck(
            name=f"python:{dependency.module}",
            status=(DoctorStatus.FAIL if dependency.required else DoctorStatus.WARNING),
            summary=(
                f"dependency version {version} does not match the required "
                f"compatibility baseline {dependency.expected_version}"
            ),
            required=dependency.required,
            version=version,
            details=details,
            remediation=(
                f"Install {dependency.distribution_name}=="
                f"{dependency.expected_version} in the project environment."
            ),
        )
    return DoctorCheck(
        name=f"python:{dependency.module}",
        status=DoctorStatus.PASS,
        summary="Python dependency is available",
        required=dependency.required,
        version=version,
        details=details,
    )


def _dependency_remediation(dependency: DependencySpec) -> str:
    qualifier = "required" if dependency.required else "optional"
    return (
        f"Install the {qualifier} '{dependency.distribution_name}' distribution "
        f"to enable {dependency.purpose or dependency.module}."
    )


def _ffmpeg_version_from_banner(banner: str) -> str | None:
    parts = banner.split()
    if len(parts) >= 3 and parts[0].casefold() == "ffmpeg" and parts[1] == "version":
        return parts[2]
    return None


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return repr(value)


__all__ = [
    "DEFAULT_OPTIONAL_DEPENDENCIES",
    "DependencySpec",
    "Doctor",
    "DoctorCheck",
    "DoctorReport",
    "DoctorStatus",
    "doctor",
    "run_doctor",
]
