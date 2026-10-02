"""Structured exceptions shared by the mastering toolchain.

The project treats error identifiers as part of its machine-readable contract.
Human-readable messages may improve over time, while :class:`ErrorCode` values
remain stable for event logs, manifests, CLI exit handling, and API clients.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ErrorCode(StrEnum):
    """Stable identifiers for expected failure categories."""

    CONFIG_INVALID = "config_invalid"
    PREFLIGHT_FAILED = "preflight_failed"
    DEPENDENCY_MISSING = "dependency_missing"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    PROCESSING_FAILED = "processing_failed"
    OUTPUT_COLLISION = "output_collision"
    SILENCE_DETECTED = "silence_detected"
    NONFINITE_AUDIO = "nonfinite_audio"

    INPUT_NOT_FOUND = "input_not_found"
    INPUT_UNREADABLE = "input_unreadable"
    FINGERPRINT_FAILED = "fingerprint_failed"
    MANIFEST_INVALID = "manifest_invalid"
    MANIFEST_READ_FAILED = "manifest_read_failed"
    MANIFEST_WRITE_FAILED = "manifest_write_failed"
    EVENT_SINK_FAILED = "event_sink_failed"
    EXTERNAL_COMMAND_FAILED = "external_command_failed"
    INTERNAL_ERROR = "internal_error"

    def __str__(self) -> str:
        return self.value


class MusicMasteringError(Exception):
    """Base exception carrying a stable code and structured context.

    Parameters
    ----------
    code:
        Stable machine-readable failure identifier.
    message:
        Concise human-readable explanation.
    details:
        Optional JSON-oriented values that help diagnose the failure.  A
        defensive copy is stored so callers cannot mutate an exception after
        it has been raised or recorded.
    retryable:
        Whether retrying the same operation may reasonably succeed without
        changing its inputs.
    """

    default_code = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | str | None = None,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("error message must be a non-empty string")
        selected_code = self.default_code if code is None else code
        try:
            self.code = (
                selected_code if isinstance(selected_code, ErrorCode) else ErrorCode(selected_code)
            )
        except ValueError as exc:
            raise ValueError(f"unknown error code: {selected_code!r}") from exc
        self.message = message
        self.details = _freeze_mapping(details or {})
        self.retryable = bool(retryable)
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-oriented representation suitable for a manifest."""

        return {
            "code": self.code.value,
            "message": self.message,
            "details": _thaw(self.details),
            "retryable": self.retryable,
            "exception_type": type(self).__name__,
        }


class ConfigError(MusicMasteringError):
    """A job configuration is invalid or internally inconsistent."""

    default_code = ErrorCode.CONFIG_INVALID


class PreflightError(MusicMasteringError):
    """Inputs or destinations failed validation before processing."""

    default_code = ErrorCode.PREFLIGHT_FAILED


class DependencyError(MusicMasteringError):
    """A required executable, Python package, or native library is absent."""

    default_code = ErrorCode.DEPENDENCY_MISSING


class CapabilityError(MusicMasteringError):
    """The selected engine cannot honor a requested capability."""

    default_code = ErrorCode.CAPABILITY_UNSUPPORTED


class ProcessingError(MusicMasteringError):
    """Audio processing failed after a job passed preflight."""

    default_code = ErrorCode.PROCESSING_FAILED


class OutputCollisionError(PreflightError):
    """An output would overwrite an existing or input artifact."""

    default_code = ErrorCode.OUTPUT_COLLISION


class SilenceError(PreflightError):
    """An input was silent under the configured detection threshold."""

    default_code = ErrorCode.SILENCE_DETECTED


class NonFiniteAudioError(PreflightError):
    """An input or intermediate signal contains NaN or infinite samples."""

    default_code = ErrorCode.NONFINITE_AUDIO


class ManifestError(MusicMasteringError):
    """A run manifest could not be fingerprinted, serialized, or loaded."""

    default_code = ErrorCode.MANIFEST_INVALID


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class EventSinkError(MusicMasteringError):
    """One or more event sinks failed to accept an event."""

    default_code = ErrorCode.EVENT_SINK_FAILED


# A compact alias is convenient in adapters while the descriptive public name
# remains the canonical spelling.
MMTError = MusicMasteringError


__all__ = [
    "CapabilityError",
    "ConfigError",
    "DependencyError",
    "ErrorCode",
    "EventSinkError",
    "MMTError",
    "ManifestError",
    "MusicMasteringError",
    "NonFiniteAudioError",
    "OutputCollisionError",
    "PreflightError",
    "ProcessingError",
    "SilenceError",
]
