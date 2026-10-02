"""Per-job structured events and composable event sinks.

There is intentionally no module-level logger or mutable handler registry.
Each job owns an emitter and one or more sinks, which keeps concurrent runs
isolated and makes their event streams straightforward to test.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum, IntEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, TextIO, cast, runtime_checkable

from .errors import EventSinkError


def utc_now() -> datetime:
    """Return an aware UTC timestamp.

    Kept as a function (instead of a constant/default expression) so every
    event receives its own timestamp and tests can inject explicit values.
    """

    return datetime.now(UTC)


class EventLevel(IntEnum):
    """Severity levels aligned with Python's conventional logging values."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @classmethod
    def coerce(cls, value: EventLevel | str | int) -> EventLevel:
        """Normalize a level name or numeric value."""

        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized == "WARN":
                normalized = "WARNING"
            try:
                return cls[normalized]
            except KeyError as exc:
                raise ValueError(f"unknown event level: {value!r}") from exc
        try:
            return cls(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown event level: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable event emitted by one mastering job."""

    code: str
    message: str
    level: EventLevel = EventLevel.INFO
    timestamp: datetime = field(default_factory=utc_now)
    job_id: str | None = None
    stage: str | None = None
    sequence: int | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("event code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("event message must be a non-empty string")
        if self.job_id is not None and (
            not isinstance(self.job_id, str) or not self.job_id.strip()
        ):
            raise ValueError("event job_id must be a non-empty string or None")
        if self.stage is not None and (not isinstance(self.stage, str) or not self.stage.strip()):
            raise ValueError("event stage must be a non-empty string or None")
        if self.sequence is not None and (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ValueError("event sequence must be a non-negative integer or None")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("event timestamp must be a datetime")
        timestamp = self.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        else:
            timestamp = timestamp.astimezone(UTC)
        if not isinstance(self.context, Mapping):
            raise TypeError("event context must be a mapping")

        object.__setattr__(self, "level", EventLevel.coerce(self.level))
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "context", _freeze_mapping(self.context))

    @classmethod
    def create(
        cls,
        code: str,
        message: str,
        *,
        level: EventLevel | str | int = EventLevel.INFO,
        job_id: str | None = None,
        stage: str | None = None,
        sequence: int | None = None,
        timestamp: datetime | None = None,
        context: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> Event:
        """Create an event while merging named structured context fields."""

        merged = dict(context or {})
        merged.update(fields)
        return cls(
            code=code,
            message=message,
            level=EventLevel.coerce(level),
            timestamp=timestamp or utc_now(),
            job_id=job_id,
            stage=stage,
            sequence=sequence,
            context=merged,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        """Reconstruct an event written by :meth:`to_dict`."""

        raw_timestamp = data.get("timestamp")
        if not isinstance(raw_timestamp, str):
            raise ValueError("event timestamp must be an ISO-8601 string")
        timestamp = _parse_utc_timestamp(raw_timestamp)
        raw_context = data.get("context", {})
        if not isinstance(raw_context, Mapping):
            raise ValueError("event context must be a JSON object")
        return cls(
            code=str(data.get("code", "")),
            message=str(data.get("message", "")),
            level=EventLevel.coerce(data.get("level", EventLevel.INFO.name)),
            timestamp=timestamp,
            job_id=_optional_string(data.get("job_id"), "job_id"),
            stage=_optional_string(data.get("stage"), "stage"),
            sequence=_optional_int(data.get("sequence"), "sequence"),
            context=raw_context,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "timestamp": _format_utc_timestamp(self.timestamp),
            "level": self.level.name.lower(),
            "code": self.code,
            "message": self.message,
            "job_id": self.job_id,
            "stage": self.stage,
            "sequence": self.sequence,
            "context": _thaw(self.context),
        }


def new_event(
    code: str,
    message: str,
    *,
    level: EventLevel | str | int = EventLevel.INFO,
    job_id: str | None = None,
    stage: str | None = None,
    sequence: int | None = None,
    timestamp: datetime | None = None,
    context: Mapping[str, Any] | None = None,
    **fields: Any,
) -> Event:
    """Functional spelling of :meth:`Event.create`."""

    return Event.create(
        code,
        message,
        level=level,
        job_id=job_id,
        stage=stage,
        sequence=sequence,
        timestamp=timestamp,
        context=context,
        **fields,
    )


@runtime_checkable
class EventSink(Protocol):
    """Destination for a structured event."""

    def emit(self, event: Event) -> None:
        """Accept one event."""


class NullEventSink:
    """Discard events explicitly, useful for quiet programmatic runs."""

    def emit(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")


class ConsoleEventSink:
    """Write compact human-readable or JSON events to a text stream."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        min_level: EventLevel | str | int = EventLevel.INFO,
        json_lines: bool = False,
        flush: bool = True,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.min_level = EventLevel.coerce(min_level)
        self.json_lines = bool(json_lines)
        self.flush = bool(flush)
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")
        if event.level < self.min_level:
            return
        line = _json_dumps(event.to_dict()) if self.json_lines else self._format_human(event)
        with self._lock:
            self.stream.write(line + "\n")
            if self.flush:
                self.stream.flush()

    @staticmethod
    def _format_human(event: Event) -> str:
        labels = [
            _format_utc_timestamp(event.timestamp),
            event.level.name,
            event.code,
        ]
        if event.job_id is not None:
            labels.append(f"job={event.job_id}")
        if event.stage is not None:
            labels.append(f"stage={event.stage}")
        if event.sequence is not None:
            labels.append(f"seq={event.sequence}")
        suffix = ""
        if event.context:
            suffix = " " + _json_dumps(_thaw(event.context))
        return f"[{' | '.join(labels)}] {event.message}{suffix}"


class JsonlEventSink:
    """Append events to a UTF-8 JSON Lines file.

    The file is opened lazily on the first event.  Call :meth:`close`, or use
    the sink as a context manager, to release the handle deterministically.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        append: bool = True,
        exclusive: bool = False,
        flush: bool = True,
        sync_on_close: bool = False,
        create_parents: bool = True,
    ) -> None:
        if append and exclusive:
            raise ValueError("append and exclusive cannot both be enabled")
        self.path = Path(path)
        self.append = bool(append)
        self.exclusive = bool(exclusive)
        self.flush = bool(flush)
        self.sync_on_close = bool(sync_on_close)
        self.create_parents = bool(create_parents)
        self._handle: TextIO | None = None
        self._has_opened = False
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")
        line = _json_dumps(event.to_dict())
        with self._lock:
            handle = self._open()
            handle.write(line + "\n")
            if self.flush:
                handle.flush()

    def close(self) -> None:
        """Flush and close the underlying file if it was opened."""

        with self._lock:
            if self._handle is not None:
                handle = self._handle
                try:
                    handle.flush()
                    if self.sync_on_close:
                        os.fsync(handle.fileno())
                finally:
                    handle.close()
                    self._handle = None

    def __enter__(self) -> JsonlEventSink:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _open(self) -> TextIO:
        if self._handle is not None:
            return self._handle
        if self.create_parents:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._has_opened:
            mode = "a"
        elif self.exclusive:
            mode = "x"
        else:
            mode = "a" if self.append else "w"
        self._handle = cast(
            TextIO,
            self.path.open(mode, encoding="utf-8", newline="\n"),
        )
        self._has_opened = True
        return self._handle


# Both spellings are exported: ``Jsonl`` follows normal Python class casing,
# while ``JSONL`` is easy to discover when searching for the file format.
JSONLEventSink = JsonlEventSink


class MemoryEventSink:
    """Retain events in memory for tests, API responses, and live UIs."""

    def __init__(self, *, max_events: int | None = None) -> None:
        if max_events is not None and (
            not isinstance(max_events, int) or isinstance(max_events, bool) or max_events <= 0
        ):
            raise ValueError("max_events must be a positive integer or None")
        self._events: deque[Event] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[Event, ...]:
        """Return an immutable snapshot of retained events."""

        with self._lock:
            return tuple(self._events)

    def clear(self) -> None:
        """Remove all retained events."""

        with self._lock:
            self._events.clear()


@dataclass(frozen=True, slots=True)
class SinkFailure:
    """Diagnostic captured when a member of a composite sink fails."""

    sink_type: str
    exception_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class CompositeEventSink:
    """Fan out each event to multiple independently owned sinks.

    By default a failed diagnostic destination does not abort audio
    processing; failures remain available via :attr:`failures`.  Set
    ``strict=True`` when losing any event must fail the job.
    """

    def __init__(
        self,
        *sinks: EventSink | Iterable[EventSink],
        strict: bool = False,
    ) -> None:
        if len(sinks) == 1 and not isinstance(sinks[0], EventSink):
            candidate = sinks[0]
            try:
                normalized = tuple(candidate)
            except TypeError as exc:
                raise TypeError("sinks must implement EventSink") from exc
        else:
            normalized = tuple(cast(EventSink, sink) for sink in sinks)
        for sink in normalized:
            if not isinstance(sink, EventSink):
                raise TypeError(f"sink does not implement EventSink: {sink!r}")
        self.sinks: tuple[EventSink, ...] = normalized
        self.strict = bool(strict)
        self._failures: list[SinkFailure] = []
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        failures: list[SinkFailure] = []
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:  # A logging failure must remain diagnosable.
                failures.append(
                    SinkFailure(
                        sink_type=type(sink).__name__,
                        exception_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
        if failures:
            with self._lock:
                self._failures.extend(failures)
            if self.strict:
                raise EventSinkError(
                    f"{len(failures)} event sink(s) failed",
                    details={
                        "event_code": event.code,
                        "failures": [failure.to_dict() for failure in failures],
                    },
                )

    @property
    def failures(self) -> tuple[SinkFailure, ...]:
        """Return an immutable snapshot of all observed sink failures."""

        with self._lock:
            return tuple(self._failures)

    def close(self) -> None:
        """Close member sinks that provide a ``close`` method."""

        failures: list[SinkFailure] = []
        for sink in reversed(self.sinks):
            close = getattr(sink, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception as exc:
                failures.append(
                    SinkFailure(
                        sink_type=type(sink).__name__,
                        exception_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
        if failures:
            with self._lock:
                self._failures.extend(failures)
            if self.strict:
                raise EventSinkError(
                    f"{len(failures)} event sink(s) failed while closing",
                    details={"failures": [failure.to_dict() for failure in failures]},
                )

    def __enter__(self) -> CompositeEventSink:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class JobEventEmitter:
    """Create sequenced events bound to one job and deliver them to a sink."""

    def __init__(
        self,
        job_id: str,
        sink: EventSink,
        *,
        initial_sequence: int = 0,
    ) -> None:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if not isinstance(sink, EventSink):
            raise TypeError("sink must implement EventSink")
        if (
            not isinstance(initial_sequence, int)
            or isinstance(initial_sequence, bool)
            or initial_sequence < 0
        ):
            raise ValueError("initial_sequence must be a non-negative integer")
        self.job_id = job_id
        self.sink = sink
        self._next_sequence = initial_sequence
        self._lock = threading.Lock()

    @classmethod
    def from_sinks(
        cls,
        job_id: str,
        *sinks: EventSink,
        strict: bool = False,
    ) -> JobEventEmitter:
        """Build an emitter and its composite sink in one call."""

        return cls(job_id, CompositeEventSink(*sinks, strict=strict))

    def emit(
        self,
        code: str,
        message: str,
        *,
        level: EventLevel | str | int = EventLevel.INFO,
        stage: str | None = None,
        context: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> Event:
        """Create, sequence, deliver, and return one event."""

        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            event = Event.create(
                code,
                message,
                level=level,
                job_id=self.job_id,
                stage=stage,
                sequence=sequence,
                context=context,
                **fields,
            )
            # Delivery remains inside the lock so concurrent producers cannot
            # write sequence 1 before sequence 0 to an otherwise ordered sink.
            self.sink.emit(event)
        return event

    def debug(self, code: str, message: str, **fields: Any) -> Event:
        return self.emit(code, message, level=EventLevel.DEBUG, **fields)

    def info(self, code: str, message: str, **fields: Any) -> Event:
        return self.emit(code, message, level=EventLevel.INFO, **fields)

    def warning(self, code: str, message: str, **fields: Any) -> Event:
        return self.emit(code, message, level=EventLevel.WARNING, **fields)

    def error(self, code: str, message: str, **fields: Any) -> Event:
        return self.emit(code, message, level=EventLevel.ERROR, **fields)


def _format_utc_timestamp(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    normalized = _normalize_json_value(value)
    if isinstance(normalized, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in normalized.items()})
    if isinstance(normalized, list):
        return tuple(_freeze(item) for item in normalized)
    return normalized


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in sorted(value, key=repr)]
    return value


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _normalize_json_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return _format_utc_timestamp(timestamp)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_normalize_json_value(item) for item in sorted(value, key=repr)]
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize_json_value(asdict(value))
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    return repr(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"event {name} must be a string or null")
    return value


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"event {name} must be an integer or null")
    return value


__all__ = [
    "CompositeEventSink",
    "ConsoleEventSink",
    "Event",
    "EventLevel",
    "EventSink",
    "JSONLEventSink",
    "JobEventEmitter",
    "JsonlEventSink",
    "MemoryEventSink",
    "NullEventSink",
    "SinkFailure",
    "new_event",
    "utc_now",
]
