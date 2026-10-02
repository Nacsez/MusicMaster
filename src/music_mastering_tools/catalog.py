"""Private, content-addressed catalog for mastering tracks and run artifacts.

The catalog deliberately stores *references* to local files instead of copying
audio.  Track identity is derived from file bytes, while locations are separate
records that can become missing or stale without changing historical identity.

SQLite is used through the Python standard library.  Every mutating operation
is transactional, schema versions are explicit, and JSON exchange rejects
duplicate keys, unknown fields, and non-finite values.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from .manifest import RunManifest, fingerprint_file

CATALOG_SCHEMA_VERSION = 1
CATALOG_DOCUMENT_KIND = "music-mastering-tools/catalog"
SELECTION_DOCUMENT_KIND = "music-mastering-tools/track-selection"
JSON_SCHEMA_VERSION = 1
DEFAULT_BUSY_TIMEOUT_MS = 5_000

_TRACK_PREFIX = "trk_sha256_"
_LOCATION_PREFIX = "loc_sha256_"
_ARTIFACT_PREFIX = "art_sha256_"
_SELECTION_PREFIX = "sel_sha256_"
_REFERENCE_SET_PREFIX = "refset_"
_CATALOG_PREFIX = "cat_"


class CatalogError(RuntimeError):
    """Base exception for catalog persistence and domain failures."""


class CatalogConflictError(CatalogError):
    """Existing catalog state conflicts with a requested mutation."""


class CatalogFormatError(CatalogError, ValueError):
    """A catalog database or exchange document violates its schema."""


class CatalogRole(StrEnum):
    """Stable roles understood by the local catalog."""

    TARGET = "target"
    REFERENCE = "reference"
    GENERATED_OUTPUT = "generated-output"
    AUDIT = "audit"


class LocationState(StrEnum):
    """Observed availability of a known filesystem location."""

    AVAILABLE = "available"
    MISSING = "missing"
    CHANGED = "changed"
    UNREADABLE = "unreadable"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class AudioFacts:
    """Optional decoded audio metadata associated with content."""

    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    format: str | None = None
    subtype: str | None = None

    def __post_init__(self) -> None:
        _positive_int(self.sample_rate, "audio_facts.sample_rate")
        _positive_int(self.channels, "audio_facts.channels")
        _non_negative_int(self.frames, "audio_facts.frames")
        _finite_non_negative(self.duration_seconds, "audio_facts.duration_seconds")
        _optional_non_empty(self.format, "audio_facts.format")
        _optional_non_empty(self.subtype, "audio_facts.subtype")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "duration_seconds": self.duration_seconds,
            "format": self.format,
            "subtype": self.subtype,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AudioFacts:
        _check_fields(
            value,
            {
                "sample_rate",
                "channels",
                "frames",
                "duration_seconds",
                "format",
                "subtype",
            },
            {"sample_rate", "channels", "frames", "duration_seconds"},
            "audio_facts",
        )
        return cls(
            sample_rate=_required_int(value.get("sample_rate"), "audio_facts.sample_rate"),
            channels=_required_int(value.get("channels"), "audio_facts.channels"),
            frames=_required_int(value.get("frames"), "audio_facts.frames"),
            duration_seconds=_required_float(
                value.get("duration_seconds"),
                "audio_facts.duration_seconds",
            ),
            format=_optional_string(value.get("format"), "audio_facts.format"),
            subtype=_optional_string(value.get("subtype"), "audio_facts.subtype"),
        )


@dataclass(frozen=True, slots=True)
class TrackRecord:
    """One immutable content identity and its catalog annotations."""

    track_id: str
    sha256: str
    size_bytes: int
    roles: tuple[CatalogRole, ...]
    label: str | None
    audio_facts: AudioFacts | None
    archived: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LocationRecord:
    """One path at which cataloged content has been observed."""

    location_id: str
    track_id: str
    path: str
    state: LocationState
    modified_ns: int | None
    last_verified_at: str | None


@dataclass(frozen=True, slots=True)
class ReferenceSetMember:
    """One independently weighted reference in a named set."""

    track_id: str
    ordinal: int
    level_weight: float
    frequency_weight: float

    def __post_init__(self) -> None:
        _validate_track_id(self.track_id)
        _non_negative_int(self.ordinal, "reference_member.ordinal")
        _finite_non_negative(self.level_weight, "reference_member.level_weight")
        _finite_non_negative(
            self.frequency_weight,
            "reference_member.frequency_weight",
        )
        if self.level_weight == 0 and self.frequency_weight == 0:
            raise ValueError("a reference member must contribute at least one positive weight")


@dataclass(frozen=True, slots=True)
class ReferenceSetRecord:
    """A named, reusable weighted reference collection."""

    set_id: str
    name: str
    members: tuple[ReferenceSetMember, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SelectedTrack:
    """Portable target or reference identity and its current path."""

    track_id: str
    sha256: str
    size_bytes: int
    path: str
    label: str | None = None

    def __post_init__(self) -> None:
        _validate_track_id(self.track_id, expected_sha256=self.sha256)
        _validate_sha256(self.sha256, "selected_track.sha256")
        _non_negative_int(self.size_bytes, "selected_track.size_bytes")
        _non_empty(self.path, "selected_track.path")
        _optional_non_empty(self.label, "selected_track.label")

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "path": self.path,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], where: str) -> SelectedTrack:
        _check_fields(
            value,
            {"track_id", "sha256", "size_bytes", "path", "label"},
            {"track_id", "sha256", "size_bytes", "path"},
            where,
        )
        return cls(
            track_id=_required_string(value.get("track_id"), f"{where}.track_id"),
            sha256=_required_string(value.get("sha256"), f"{where}.sha256"),
            size_bytes=_required_int(value.get("size_bytes"), f"{where}.size_bytes"),
            path=_required_string(value.get("path"), f"{where}.path"),
            label=_optional_string(value.get("label"), f"{where}.label"),
        )


@dataclass(frozen=True, slots=True)
class SelectedReference:
    """Portable reference identity plus its requested blend weights."""

    track: SelectedTrack
    ordinal: int
    level_weight: float
    frequency_weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.track, SelectedTrack):
            raise TypeError("selected_reference.track must be a SelectedTrack")
        _non_negative_int(self.ordinal, "selected_reference.ordinal")
        _finite_non_negative(
            self.level_weight,
            "selected_reference.level_weight",
        )
        _finite_non_negative(
            self.frequency_weight,
            "selected_reference.frequency_weight",
        )
        if self.level_weight == 0 and self.frequency_weight == 0:
            raise ValueError("a selected reference must contribute at least one positive weight")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.track.to_dict(),
            "ordinal": self.ordinal,
            "level_weight": self.level_weight,
            "frequency_weight": self.frequency_weight,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], where: str) -> SelectedReference:
        _check_fields(
            value,
            {
                "track_id",
                "sha256",
                "size_bytes",
                "path",
                "label",
                "ordinal",
                "level_weight",
                "frequency_weight",
            },
            {
                "track_id",
                "sha256",
                "size_bytes",
                "path",
                "ordinal",
                "level_weight",
                "frequency_weight",
            },
            where,
        )
        track_fields = {
            key: value[key]
            for key in ("track_id", "sha256", "size_bytes", "path", "label")
            if key in value
        }
        return cls(
            track=SelectedTrack.from_dict(track_fields, where),
            ordinal=_required_int(value.get("ordinal"), f"{where}.ordinal"),
            level_weight=_required_float(
                value.get("level_weight"),
                f"{where}.level_weight",
            ),
            frequency_weight=_required_float(
                value.get("frequency_weight"),
                f"{where}.frequency_weight",
            ),
        )


@dataclass(frozen=True, slots=True)
class CatalogSelection:
    """Immutable, portable target and weighted-reference selection.

    A target identity may also appear once as a reference so the job-level
    ``allow_identical_target_and_reference`` diagnostic policy remains
    representable. Normal validation rejects that request unless the explicit
    override is enabled. Duplicate identities within the reference list remain
    ambiguous and are rejected.
    """

    target: SelectedTrack
    references: tuple[SelectedReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.target, SelectedTrack):
            raise TypeError("selection.target must be a SelectedTrack")
        if not isinstance(self.references, tuple) or not all(
            isinstance(item, SelectedReference) for item in self.references
        ):
            raise TypeError("selection.references must be a tuple of SelectedReference values")
        if not self.references:
            raise ValueError("selection requires at least one reference")
        ordinals = tuple(item.ordinal for item in self.references)
        if ordinals != tuple(range(len(self.references))):
            raise ValueError(
                "selection reference ordinals must be contiguous and ordered from zero"
            )
        track_ids = tuple(item.track.track_id for item in self.references)
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("selection cannot contain duplicate reference content")
        if sum(item.level_weight for item in self.references) <= 0:
            raise ValueError("selection requires a positive total level weight")
        if sum(item.frequency_weight for item in self.references) <= 0:
            raise ValueError("selection requires a positive total frequency weight")

    @property
    def selection_id(self) -> str:
        """Deterministic semantic identity, independent of labels and paths."""

        references = sorted(
            (
                {
                    "track_id": item.track.track_id,
                    "level_weight": item.level_weight,
                    "frequency_weight": item.frequency_weight,
                }
                for item in self.references
            ),
            key=lambda item: cast(str, item["track_id"]),
        )
        payload = {
            "target_track_id": self.target.track_id,
            "references": references,
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return _SELECTION_PREFIX + digest

    @property
    def normalized_level_weights(self) -> tuple[float, ...]:
        total = sum(item.level_weight for item in self.references)
        return tuple(item.level_weight / total for item in self.references)

    @property
    def normalized_frequency_weights(self) -> tuple[float, ...]:
        total = sum(item.frequency_weight for item in self.references)
        return tuple(item.frequency_weight / total for item in self.references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": SELECTION_DOCUMENT_KIND,
            "schema_version": JSON_SCHEMA_VERSION,
            "selection_id": self.selection_id,
            "target": self.target.to_dict(),
            "references": [item.to_dict() for item in self.references],
        }

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
    def from_json(cls, document: str | bytes) -> CatalogSelection:
        value = _load_json_object(document)
        _check_fields(
            value,
            {"kind", "schema_version", "selection_id", "target", "references"},
            {"kind", "schema_version", "selection_id", "target", "references"},
            "selection",
        )
        if value["kind"] != SELECTION_DOCUMENT_KIND:
            raise CatalogFormatError(f"unsupported selection kind: {value['kind']!r}")
        if value["schema_version"] != JSON_SCHEMA_VERSION:
            raise CatalogFormatError(
                f"unsupported selection schema_version: {value['schema_version']!r}"
            )
        raw_target = _required_object(value.get("target"), "selection.target")
        raw_references = _required_list(value.get("references"), "selection.references")
        selection = cls(
            target=SelectedTrack.from_dict(raw_target, "selection.target"),
            references=tuple(
                SelectedReference.from_dict(
                    _required_object(item, f"selection.references[{index}]"),
                    f"selection.references[{index}]",
                )
                for index, item in enumerate(raw_references)
            ),
        )
        requested_id = _required_string(value.get("selection_id"), "selection.selection_id")
        if requested_id != selection.selection_id:
            raise CatalogFormatError(
                "selection_id does not match the target, references, and weights"
            )
        return selection


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Catalog index record for one mastering run."""

    run_id: str
    status: str
    selection_id: str | None
    manifest_path: str | None
    manifest_sha256: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class RunArtifactRecord:
    """One content-addressed artifact used or produced by a run."""

    run_id: str
    role: CatalogRole
    ordinal: int
    artifact_id: str
    track_id: str | None
    path: str
    sha256: str
    size_bytes: int
    media_type: str | None
    state: LocationState
    metadata: Mapping[str, Any]


class CatalogStore:
    """Transactional SQLite catalog.

    The connection opens at construction time for convenient direct use and is
    closed by :meth:`close` or context-manager exit.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        _positive_int(busy_timeout_ms, "busy_timeout_ms")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection: sqlite3.Connection | None = sqlite3.connect(
                self.path,
                timeout=busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._transaction_depth = 0
            self._initialize_schema()
        except CatalogError:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            self._connection = None
            raise
        except (OSError, sqlite3.Error) as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            self._connection = None
            raise CatalogError(f"could not open catalog '{self.path}': {exc}") from exc

    def __enter__(self) -> CatalogStore:
        self._require_connection()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    @property
    def catalog_id(self) -> str:
        return self._meta("catalog_id")

    @property
    def revision(self) -> int:
        try:
            return int(self._meta("revision"))
        except ValueError as exc:
            raise CatalogFormatError("catalog revision is not an integer") from exc

    def close(self) -> None:
        connection = self._connection
        if connection is None:
            return
        if self._transaction_depth:
            connection.rollback()
            self._transaction_depth = 0
        connection.close()
        self._connection = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Open a write transaction, using savepoints when nested."""

        connection = self._require_connection()
        depth = self._transaction_depth
        savepoint = f"catalog_savepoint_{depth}"
        try:
            if depth == 0:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(f"SAVEPOINT {savepoint}")
            self._transaction_depth += 1
            yield
        except BaseException:
            self._transaction_depth -= 1
            if depth == 0:
                connection.rollback()
            else:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            self._transaction_depth -= 1
            if depth == 0:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    def add_track(
        self,
        path: str | os.PathLike[str],
        *,
        roles: Sequence[CatalogRole | str],
        label: str | None = None,
        audio_facts: AudioFacts | None = None,
    ) -> TrackRecord:
        """Fingerprint and catalog a local file without copying it."""

        selected_roles = _roles(roles)
        _optional_non_empty(label, "label")
        if audio_facts is not None and not isinstance(audio_facts, AudioFacts):
            raise TypeError("audio_facts must be an AudioFacts value or None")
        fingerprint = fingerprint_file(path)
        track_id = _track_id(fingerprint.sha256)
        canonical_path = fingerprint.path
        normalized_path = _normalized_path(canonical_path)
        location_id = _location_id(normalized_path)
        now = _utc_now()

        connection = self._require_connection()
        existing_location = connection.execute(
            "SELECT track_id FROM locations WHERE normalized_path = ?",
            (normalized_path,),
        ).fetchone()
        if existing_location is not None:
            existing_track_id = _row_string(existing_location, "track_id")
            if existing_track_id != track_id:
                with self.transaction():
                    connection.execute(
                        """
                        UPDATE locations
                        SET state = ?, last_verified_at = ?
                        WHERE normalized_path = ?
                        """,
                        (LocationState.CHANGED.value, now, normalized_path),
                    )
                    self._bump_revision()
                raise CatalogConflictError(
                    "a cataloged path now contains different bytes; verify or archive "
                    "the old location before registering the new content"
                )

        with self.transaction():
            existing_track = connection.execute(
                "SELECT * FROM tracks WHERE track_id = ?",
                (track_id,),
            ).fetchone()
            if existing_track is None:
                connection.execute(
                    """
                    INSERT INTO tracks (
                        track_id, sha256, size_bytes, label,
                        sample_rate, channels, frames, duration_seconds,
                        audio_format, audio_subtype, archived, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        track_id,
                        fingerprint.sha256,
                        fingerprint.size_bytes,
                        label,
                        audio_facts.sample_rate if audio_facts else None,
                        audio_facts.channels if audio_facts else None,
                        audio_facts.frames if audio_facts else None,
                        audio_facts.duration_seconds if audio_facts else None,
                        audio_facts.format if audio_facts else None,
                        audio_facts.subtype if audio_facts else None,
                        now,
                        now,
                    ),
                )
            else:
                existing_facts = _audio_facts_from_row(existing_track)
                if (
                    audio_facts is not None
                    and existing_facts is not None
                    and audio_facts != existing_facts
                ):
                    raise CatalogConflictError(
                        "audio facts conflict with the existing content record"
                    )
                existing_label = _row_optional_string(existing_track, "label")
                if label is not None and existing_label not in {None, label}:
                    raise CatalogConflictError("label conflicts with the existing content record")
                connection.execute(
                    """
                    UPDATE tracks
                    SET label = COALESCE(label, ?),
                        sample_rate = COALESCE(sample_rate, ?),
                        channels = COALESCE(channels, ?),
                        frames = COALESCE(frames, ?),
                        duration_seconds = COALESCE(duration_seconds, ?),
                        audio_format = COALESCE(audio_format, ?),
                        audio_subtype = COALESCE(audio_subtype, ?),
                        updated_at = ?
                    WHERE track_id = ?
                    """,
                    (
                        label,
                        audio_facts.sample_rate if audio_facts else None,
                        audio_facts.channels if audio_facts else None,
                        audio_facts.frames if audio_facts else None,
                        audio_facts.duration_seconds if audio_facts else None,
                        audio_facts.format if audio_facts else None,
                        audio_facts.subtype if audio_facts else None,
                        now,
                        track_id,
                    ),
                )
            connection.executemany(
                "INSERT OR IGNORE INTO track_roles (track_id, role) VALUES (?, ?)",
                ((track_id, role.value) for role in selected_roles),
            )
            connection.execute(
                """
                INSERT INTO locations (
                    location_id, track_id, path, normalized_path, state,
                    modified_ns, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    state = excluded.state,
                    modified_ns = excluded.modified_ns,
                    last_verified_at = excluded.last_verified_at
                """,
                (
                    location_id,
                    track_id,
                    canonical_path,
                    normalized_path,
                    LocationState.AVAILABLE.value,
                    fingerprint.modified_ns,
                    now,
                ),
            )
            self._bump_revision()
        return self.get_track(track_id)

    def get_track(self, track_id: str) -> TrackRecord:
        _validate_track_id(track_id)
        row = (
            self._require_connection()
            .execute(
                "SELECT * FROM tracks WHERE track_id = ?",
                (track_id,),
            )
            .fetchone()
        )
        if row is None:
            raise CatalogError(f"unknown track: {track_id}")
        return self._track_from_row(row)

    def update_track_label(self, track_id: str, label: str | None) -> TrackRecord:
        """Replace a track's display label without touching any audio path.

        Label policy belongs to the calling application because different
        adapters may impose different presentation limits.  The catalog still
        rejects blank non-null values so callers cannot persist ambiguous
        whitespace-only labels.
        """

        self.get_track(track_id)
        _optional_non_empty(label, "label")
        now = _utc_now()
        with self.transaction():
            self._require_connection().execute(
                """
                UPDATE tracks
                SET label = ?, updated_at = ?
                WHERE track_id = ?
                """,
                (label, now, track_id),
            )
            self._bump_revision()
        return self.get_track(track_id)

    def list_tracks(
        self,
        *,
        role: CatalogRole | str | None = None,
        include_archived: bool = False,
    ) -> tuple[TrackRecord, ...]:
        parameters: list[object] = []
        clauses: list[str] = []
        join = ""
        if role is not None:
            selected_role = CatalogRole(role)
            join = " JOIN track_roles AS selected_role ON selected_role.track_id = tracks.track_id"
            clauses.append("selected_role.role = ?")
            parameters.append(selected_role.value)
        if not include_archived:
            clauses.append("tracks.archived = 0")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._require_connection().execute(
            f"SELECT tracks.* FROM tracks{join}{where} ORDER BY tracks.track_id",
            parameters,
        )
        return tuple(self._track_from_row(row) for row in rows)

    def list_locations(self, track_id: str) -> tuple[LocationRecord, ...]:
        self.get_track(track_id)
        rows = self._require_connection().execute(
            "SELECT * FROM locations WHERE track_id = ? ORDER BY path",
            (track_id,),
        )
        return tuple(self._location_from_row(row) for row in rows)

    def preferred_location(self, track_id: str) -> LocationRecord:
        """Return the stable first currently present available path for a track."""

        self.get_track(track_id)
        rows = tuple(
            self._require_connection().execute(
                """
            SELECT * FROM locations
            WHERE track_id = ? AND state = ?
            ORDER BY path
            """,
                (track_id, LocationState.AVAILABLE.value),
            )
        )
        for row in rows:
            location = self._location_from_row(row)
            if Path(location.path).is_file():
                return location
        raise CatalogConflictError(
            f"track has no verified, currently present available location: {track_id}"
        )

    def verify(
        self,
        track_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> tuple[LocationRecord, ...]:
        """Refresh availability without changing any content identity."""

        if track_id is not None:
            self.get_track(track_id)
        clauses: list[str] = []
        parameters: list[object] = []
        if track_id is not None:
            clauses.append("locations.track_id = ?")
            parameters.append(track_id)
        if not include_archived:
            clauses.append("tracks.archived = 0")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = tuple(
            self._require_connection().execute(
                f"""
                SELECT locations.*
                FROM locations
                JOIN tracks ON tracks.track_id = locations.track_id
                {where}
                ORDER BY locations.location_id
                """,
                parameters,
            )
        )
        selected_location_ids = tuple(_row_string(row, "location_id") for row in rows)
        observed: list[tuple[str, LocationState, int | None, str]] = []
        for row in rows:
            path = _row_string(row, "path")
            location_id = _row_string(row, "location_id")
            current_state = LocationState(_row_string(row, "state"))
            if current_state in {LocationState.ARCHIVED, LocationState.DELETED}:
                # These are intentional lifecycle states, not stale
                # availability observations. In particular, recoverably
                # quarantined master locations must remain restorable even
                # after an operator runs Verify All.
                continue
            expected_track = self.get_track(_row_string(row, "track_id"))
            candidate = Path(path)
            now = _utc_now()
            if not candidate.is_file():
                observed.append((location_id, LocationState.MISSING, None, now))
                continue
            try:
                fingerprint = fingerprint_file(candidate)
            except Exception:
                observed.append((location_id, LocationState.UNREADABLE, None, now))
                continue
            state = (
                LocationState.AVAILABLE
                if fingerprint.sha256 == expected_track.sha256
                and fingerprint.size_bytes == expected_track.size_bytes
                else LocationState.CHANGED
            )
            observed.append((location_id, state, fingerprint.modified_ns, now))

        if observed:
            with self.transaction():
                self._require_connection().executemany(
                    """
                    UPDATE locations
                    SET state = ?, modified_ns = ?, last_verified_at = ?
                    WHERE location_id = ?
                    """,
                    (
                        (state.value, modified_ns, verified_at, location_id)
                        for location_id, state, modified_ns, verified_at in observed
                    ),
                )
                self._bump_revision()
        refreshed: list[LocationRecord] = []
        for location_id in selected_location_ids:
            row = (
                self._require_connection()
                .execute(
                    "SELECT * FROM locations WHERE location_id = ?",
                    (location_id,),
                )
                .fetchone()
            )
            if row is None:
                raise CatalogFormatError(f"verified location disappeared: {location_id}")
            refreshed.append(self._location_from_row(row))
        return tuple(refreshed)

    def relink(
        self,
        track_id: str,
        new_path: str | os.PathLike[str],
        *,
        archive_location_id: str | None = None,
    ) -> LocationRecord:
        """Attach a new path only when it contains the expected bytes."""

        track = self.get_track(track_id)
        fingerprint = fingerprint_file(new_path)
        if fingerprint.sha256 != track.sha256 or fingerprint.size_bytes != track.size_bytes:
            raise CatalogConflictError("relink path does not contain the requested track content")
        normalized_path = _normalized_path(fingerprint.path)
        location_id = _location_id(normalized_path)
        now = _utc_now()
        connection = self._require_connection()
        collision = connection.execute(
            "SELECT track_id FROM locations WHERE normalized_path = ?",
            (normalized_path,),
        ).fetchone()
        if collision is not None and _row_string(collision, "track_id") != track_id:
            raise CatalogConflictError("relink path belongs to a different catalog track")
        if archive_location_id is not None:
            old = connection.execute(
                "SELECT track_id FROM locations WHERE location_id = ?",
                (archive_location_id,),
            ).fetchone()
            if old is None or _row_string(old, "track_id") != track_id:
                raise CatalogError("archive_location_id does not belong to the requested track")

        with self.transaction():
            connection.execute(
                """
                INSERT INTO locations (
                    location_id, track_id, path, normalized_path, state,
                    modified_ns, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    state = excluded.state,
                    modified_ns = excluded.modified_ns,
                    last_verified_at = excluded.last_verified_at
                """,
                (
                    location_id,
                    track_id,
                    fingerprint.path,
                    normalized_path,
                    LocationState.AVAILABLE.value,
                    fingerprint.modified_ns,
                    now,
                ),
            )
            if archive_location_id is not None and archive_location_id != location_id:
                connection.execute(
                    "UPDATE locations SET state = ? WHERE location_id = ?",
                    (LocationState.ARCHIVED.value, archive_location_id),
                )
            self._bump_revision()
        row = connection.execute(
            "SELECT * FROM locations WHERE location_id = ?",
            (location_id,),
        ).fetchone()
        if row is None:
            raise CatalogFormatError("relinked location was not persisted")
        return self._location_from_row(row)

    def archive_track(self, track_id: str, *, archived: bool = True) -> TrackRecord:
        self.get_track(track_id)
        now = _utc_now()
        with self.transaction():
            connection = self._require_connection()
            connection.execute(
                "UPDATE tracks SET archived = ?, updated_at = ? WHERE track_id = ?",
                (int(archived), now, track_id),
            )
            connection.execute(
                "UPDATE locations SET state = ? WHERE track_id = ?",
                (
                    (LocationState.ARCHIVED.value if archived else LocationState.MISSING.value),
                    track_id,
                ),
            )
            self._bump_revision()
        if not archived:
            self.verify(track_id)
        return self.get_track(track_id)

    def create_reference_set(self, name: str) -> ReferenceSetRecord:
        normalized_name = _non_empty(name, "reference_set.name").strip()
        name_key = unicodedata.normalize("NFC", normalized_name).casefold()
        set_id = (
            _REFERENCE_SET_PREFIX
            + hashlib.sha256(f"{self.catalog_id}\0{name_key}".encode()).hexdigest()
        )
        now = _utc_now()
        try:
            with self.transaction():
                self._require_connection().execute(
                    """
                    INSERT INTO reference_sets (
                        set_id, name, name_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (set_id, normalized_name, name_key, now, now),
                )
                self._bump_revision()
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(
                f"a reference set named {normalized_name!r} already exists"
            ) from exc
        return self.get_reference_set(set_id)

    def rename_reference_set(self, set_id: str, name: str) -> ReferenceSetRecord:
        """Rename a reference set without changing its stable catalog identity."""

        self.get_reference_set(set_id)
        normalized_name = _non_empty(name, "reference_set.name").strip()
        name_key = unicodedata.normalize("NFC", normalized_name).casefold()
        try:
            with self.transaction():
                cursor = self._require_connection().execute(
                    """
                    UPDATE reference_sets
                    SET name = ?, name_key = ?, updated_at = ?
                    WHERE set_id = ?
                    """,
                    (normalized_name, name_key, _utc_now(), set_id),
                )
                if cursor.rowcount != 1:
                    raise CatalogFormatError("reference set disappeared while being renamed")
                self._bump_revision()
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(
                f"a reference set named {normalized_name!r} already exists"
            ) from exc
        return self.get_reference_set(set_id)

    def delete_reference_set(self, set_id: str) -> ReferenceSetRecord:
        """Delete a named set and its memberships without deleting any tracks."""

        with self.transaction():
            deleted = self.get_reference_set(set_id)
            cursor = self._require_connection().execute(
                "DELETE FROM reference_sets WHERE set_id = ?",
                (set_id,),
            )
            if cursor.rowcount != 1:
                raise CatalogFormatError("reference set disappeared while being deleted")
            self._bump_revision()
        return deleted

    def replace_reference_set_members(
        self,
        set_id: str,
        members: Sequence[ReferenceSetMember | tuple[str, float, float]],
    ) -> ReferenceSetRecord:
        """Atomically replace and reorder every member of a reference set.

        Ordered ``(track_id, level_weight, frequency_weight)`` triples derive
        their ordinals from sequence order. Explicit ``ReferenceSetMember``
        values must assign contiguous ordinals. Empty input clears the set.
        """

        selected_members = _reference_set_members(members)
        track_ids = tuple(member.track_id for member in selected_members)
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("reference set members cannot contain duplicate tracks")
        ordinals = sorted(member.ordinal for member in selected_members)
        if ordinals != list(range(len(selected_members))):
            raise ValueError("reference set member ordinals must be contiguous from zero")

        with self.transaction():
            self.get_reference_set(set_id)
            for member in selected_members:
                track = self.get_track(member.track_id)
                if track.archived:
                    raise CatalogConflictError(
                        "an archived track cannot be added to a reference set"
                    )
                if CatalogRole.REFERENCE not in track.roles:
                    raise CatalogConflictError("track is not cataloged with the reference role")
            connection = self._require_connection()
            connection.execute(
                "DELETE FROM reference_set_members WHERE set_id = ?",
                (set_id,),
            )
            connection.executemany(
                """
                INSERT INTO reference_set_members (
                    set_id, track_id, ordinal, level_weight, frequency_weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        set_id,
                        member.track_id,
                        member.ordinal,
                        member.level_weight,
                        member.frequency_weight,
                    )
                    for member in sorted(
                        selected_members,
                        key=lambda item: item.ordinal,
                    )
                ),
            )
            connection.execute(
                "UPDATE reference_sets SET updated_at = ? WHERE set_id = ?",
                (_utc_now(), set_id),
            )
            self._bump_revision()
        return self.get_reference_set(set_id)

    def set_reference_member(
        self,
        set_id: str,
        track_id: str,
        *,
        level_weight: float = 1.0,
        frequency_weight: float = 1.0,
        ordinal: int | None = None,
    ) -> ReferenceSetRecord:
        reference_set = self.get_reference_set(set_id)
        track = self.get_track(track_id)
        if track.archived:
            raise CatalogConflictError("an archived track cannot be added to a reference set")
        if CatalogRole.REFERENCE not in track.roles:
            raise CatalogConflictError("track is not cataloged with the reference role")
        member = ReferenceSetMember(
            track_id=track_id,
            ordinal=0 if ordinal is None else ordinal,
            level_weight=level_weight,
            frequency_weight=frequency_weight,
        )
        connection = self._require_connection()
        existing = connection.execute(
            """
            SELECT ordinal FROM reference_set_members
            WHERE set_id = ? AND track_id = ?
            """,
            (set_id, track_id),
        ).fetchone()
        if ordinal is None:
            if existing is not None:
                selected_ordinal = _row_int(existing, "ordinal")
            else:
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ordinal
                    FROM reference_set_members WHERE set_id = ?
                    """,
                    (set_id,),
                ).fetchone()
                if row is None:
                    raise CatalogFormatError("could not allocate a reference ordinal")
                selected_ordinal = _row_int(row, "next_ordinal")
        else:
            selected_ordinal = ordinal
        ReferenceSetMember(
            track_id=track_id,
            ordinal=selected_ordinal,
            level_weight=level_weight,
            frequency_weight=frequency_weight,
        )
        try:
            with self.transaction():
                connection.execute(
                    """
                    INSERT INTO reference_set_members (
                        set_id, track_id, ordinal, level_weight, frequency_weight
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(set_id, track_id) DO UPDATE SET
                        ordinal = excluded.ordinal,
                        level_weight = excluded.level_weight,
                        frequency_weight = excluded.frequency_weight
                    """,
                    (
                        set_id,
                        track_id,
                        selected_ordinal,
                        member.level_weight,
                        member.frequency_weight,
                    ),
                )
                connection.execute(
                    "UPDATE reference_sets SET updated_at = ? WHERE set_id = ?",
                    (_utc_now(), reference_set.set_id),
                )
                self._bump_revision()
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(
                f"reference ordinal {selected_ordinal} is already in use"
            ) from exc
        return self.get_reference_set(set_id)

    def remove_reference_member(self, set_id: str, track_id: str) -> ReferenceSetRecord:
        self.get_reference_set(set_id)
        with self.transaction():
            connection = self._require_connection()
            cursor = connection.execute(
                "DELETE FROM reference_set_members WHERE set_id = ? AND track_id = ?",
                (set_id, track_id),
            )
            if cursor.rowcount == 0:
                raise CatalogError("track is not a member of the reference set")
            rows = tuple(
                connection.execute(
                    """
                    SELECT track_id FROM reference_set_members
                    WHERE set_id = ? ORDER BY ordinal, track_id
                    """,
                    (set_id,),
                )
            )
            connection.executemany(
                """
                UPDATE reference_set_members SET ordinal = ?
                WHERE set_id = ? AND track_id = ?
                """,
                (
                    (ordinal, set_id, _row_string(row, "track_id"))
                    for ordinal, row in enumerate(rows)
                ),
            )
            connection.execute(
                "UPDATE reference_sets SET updated_at = ? WHERE set_id = ?",
                (_utc_now(), set_id),
            )
            self._bump_revision()
        return self.get_reference_set(set_id)

    def get_reference_set(self, set_id: str) -> ReferenceSetRecord:
        row = (
            self._require_connection()
            .execute(
                "SELECT * FROM reference_sets WHERE set_id = ?",
                (set_id,),
            )
            .fetchone()
        )
        if row is None:
            raise CatalogError(f"unknown reference set: {set_id}")
        members = tuple(
            ReferenceSetMember(
                track_id=_row_string(member, "track_id"),
                ordinal=_row_int(member, "ordinal"),
                level_weight=_row_float(member, "level_weight"),
                frequency_weight=_row_float(member, "frequency_weight"),
            )
            for member in self._require_connection().execute(
                """
                SELECT * FROM reference_set_members
                WHERE set_id = ? ORDER BY ordinal, track_id
                """,
                (set_id,),
            )
        )
        return ReferenceSetRecord(
            set_id=_row_string(row, "set_id"),
            name=_row_string(row, "name"),
            members=members,
            created_at=_row_string(row, "created_at"),
            updated_at=_row_string(row, "updated_at"),
        )

    def list_reference_sets(self) -> tuple[ReferenceSetRecord, ...]:
        rows = self._require_connection().execute(
            "SELECT set_id FROM reference_sets ORDER BY name_key"
        )
        return tuple(self.get_reference_set(_row_string(row, "set_id")) for row in rows)

    def build_selection(
        self,
        target_id: str,
        *,
        reference_ids: Sequence[str] | None = None,
        reference_set_id: str | None = None,
    ) -> CatalogSelection:
        """Resolve catalog identities to one immutable portable selection."""

        if (reference_ids is None) == (reference_set_id is None):
            raise ValueError("provide exactly one of reference_ids or reference_set_id")
        target = self.get_track(target_id)
        if target.archived or CatalogRole.TARGET not in target.roles:
            raise CatalogConflictError("target must be an active track with the target role")
        selected_target = self._selected_track(target)

        members: Sequence[ReferenceSetMember]
        if reference_set_id is not None:
            members = self.get_reference_set(reference_set_id).members
        else:
            if reference_ids is None:
                raise AssertionError("reference_ids cannot be None here")
            members = tuple(
                ReferenceSetMember(
                    track_id=track_id,
                    ordinal=ordinal,
                    level_weight=1.0,
                    frequency_weight=1.0,
                )
                for ordinal, track_id in enumerate(reference_ids)
            )
        references: list[SelectedReference] = []
        for ordinal, member in enumerate(members):
            track = self.get_track(member.track_id)
            if track.archived or CatalogRole.REFERENCE not in track.roles:
                raise CatalogConflictError(
                    "every reference must be active and cataloged with the reference role"
                )
            references.append(
                SelectedReference(
                    track=self._selected_track(track),
                    ordinal=ordinal,
                    level_weight=member.level_weight,
                    frequency_weight=member.frequency_weight,
                )
            )
        return CatalogSelection(target=selected_target, references=tuple(references))

    def import_selection(self, document: str | bytes) -> CatalogSelection:
        """Strictly parse, verify, and atomically register a portable selection."""

        selection = CatalogSelection.from_json(document)
        entries: tuple[tuple[SelectedTrack, CatalogRole], ...] = (
            (selection.target, CatalogRole.TARGET),
            *tuple((item.track, CatalogRole.REFERENCE) for item in selection.references),
        )
        observed: list[tuple[SelectedTrack, CatalogRole, int | None]] = []
        for entry, role in entries:
            fingerprint = fingerprint_file(entry.path)
            if (
                fingerprint.sha256 != entry.sha256
                or fingerprint.size_bytes != entry.size_bytes
                or entry.track_id != _track_id(fingerprint.sha256)
            ):
                raise CatalogConflictError(
                    f"selection path does not match declared content: {entry.path}"
                )
            observed.append((entry, role, fingerprint.modified_ns))

        with self.transaction():
            for entry, role, modified_ns in observed:
                self._insert_imported_track(
                    entry,
                    roles=(role,),
                    modified_ns=modified_ns,
                    state=LocationState.AVAILABLE,
                )
            self._bump_revision()
        return selection

    def record_run(
        self,
        run_id: str,
        status: str,
        *,
        selection: CatalogSelection | None = None,
        manifest_path: str | os.PathLike[str] | None = None,
        manifest_sha256: str | None = None,
    ) -> RunRecord:
        _non_empty(run_id, "run_id")
        _non_empty(status, "status")
        if selection is not None and not isinstance(selection, CatalogSelection):
            raise TypeError("selection must be a CatalogSelection or None")
        if manifest_sha256 is not None:
            _validate_sha256(manifest_sha256, "manifest_sha256")
        resolved_manifest = None if manifest_path is None else str(Path(manifest_path).resolve())
        selection_id = selection.selection_id if selection is not None else None
        selection_json = selection.to_json(indent=0) if selection is not None else None
        now = _utc_now()
        connection = self._require_connection()
        existing = connection.execute(
            "SELECT selection_id FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            existing_selection = _row_optional_string(existing, "selection_id")
            if (
                existing_selection is not None
                and selection_id is not None
                and existing_selection != selection_id
            ):
                raise CatalogConflictError("a run's catalog selection is immutable")
        with self.transaction():
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, status, selection_id, selection_json,
                    manifest_path, manifest_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    selection_id = COALESCE(runs.selection_id, excluded.selection_id),
                    selection_json = COALESCE(runs.selection_json, excluded.selection_json),
                    manifest_path = excluded.manifest_path,
                    manifest_sha256 = excluded.manifest_sha256,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    status,
                    selection_id,
                    selection_json,
                    resolved_manifest,
                    manifest_sha256,
                    now,
                    now,
                ),
            )
            self._bump_revision()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        row = (
            self._require_connection()
            .execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            )
            .fetchone()
        )
        if row is None:
            raise CatalogError(f"unknown run: {run_id}")
        return RunRecord(
            run_id=_row_string(row, "run_id"),
            status=_row_string(row, "status"),
            selection_id=_row_optional_string(row, "selection_id"),
            manifest_path=_row_optional_string(row, "manifest_path"),
            manifest_sha256=_row_optional_string(row, "manifest_sha256"),
            created_at=_row_string(row, "created_at"),
            updated_at=_row_string(row, "updated_at"),
        )

    def list_runs(self) -> tuple[RunRecord, ...]:
        rows = self._require_connection().execute(
            "SELECT run_id FROM runs ORDER BY created_at, run_id"
        )
        return tuple(self.get_run(_row_string(row, "run_id")) for row in rows)

    def get_run_selection(self, run_id: str) -> CatalogSelection | None:
        """Return the immutable target/reference selection recorded for a run."""

        document = self._run_selection_json(run_id)
        return CatalogSelection.from_json(document) if document is not None else None

    def index_artifact(
        self,
        run_id: str,
        path: str | os.PathLike[str],
        *,
        role: CatalogRole | str,
        ordinal: int | None = None,
        track_id: str | None = None,
        media_type: str | None = None,
        state: LocationState | str = LocationState.AVAILABLE,
        metadata: Mapping[str, Any] | None = None,
    ) -> RunArtifactRecord:
        self.get_run(run_id)
        selected_role = CatalogRole(role)
        selected_state = LocationState(state)
        _optional_non_empty(media_type, "media_type")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping or None")
        metadata_value = _copy_json_value(metadata or {}, "metadata")
        if not isinstance(metadata_value, dict):
            raise AssertionError("catalog artifact metadata normalization lost its mapping")
        if selected_state is not LocationState.AVAILABLE:
            raise ValueError("new run artifacts must be available when indexed")
        fingerprint = fingerprint_file(path)
        artifact_id = _artifact_id(fingerprint.sha256)
        if track_id is not None:
            track = self.get_track(track_id)
            if track.sha256 != fingerprint.sha256:
                raise CatalogConflictError("artifact bytes do not match the linked track")
        connection = self._require_connection()
        if ordinal is None:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ordinal
                FROM run_artifacts WHERE run_id = ? AND role = ?
                """,
                (run_id, selected_role.value),
            ).fetchone()
            if row is None:
                raise CatalogFormatError("could not allocate an artifact ordinal")
            selected_ordinal = _row_int(row, "next_ordinal")
        else:
            _non_negative_int(ordinal, "ordinal")
            selected_ordinal = ordinal
        try:
            with self.transaction():
                connection.execute(
                    """
                    INSERT INTO run_artifacts (
                        run_id, role, ordinal, artifact_id, track_id, path,
                        sha256, size_bytes, media_type, state, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        selected_role.value,
                        selected_ordinal,
                        artifact_id,
                        track_id,
                        fingerprint.path,
                        fingerprint.sha256,
                        fingerprint.size_bytes,
                        media_type,
                        selected_state.value,
                        _canonical_json(metadata_value),
                    ),
                )
                self._bump_revision()
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(
                f"artifact role/ordinal already indexed: {selected_role.value}[{selected_ordinal}]"
            ) from exc
        row = connection.execute(
            """
            SELECT * FROM run_artifacts
            WHERE run_id = ? AND role = ? AND ordinal = ?
            """,
            (run_id, selected_role.value, selected_ordinal),
        ).fetchone()
        if row is None:
            raise CatalogFormatError("indexed artifact was not persisted")
        return self._artifact_from_row(row)

    def list_run_artifacts(
        self,
        run_id: str,
        *,
        role: CatalogRole | str | None = None,
    ) -> tuple[RunArtifactRecord, ...]:
        self.get_run(run_id)
        parameters: list[object] = [run_id]
        clause = ""
        if role is not None:
            selected_role = CatalogRole(role)
            clause = " AND role = ?"
            parameters.append(selected_role.value)
        rows = self._require_connection().execute(
            f"""
            SELECT * FROM run_artifacts
            WHERE run_id = ?{clause}
            ORDER BY role, ordinal
            """,
            parameters,
        )
        return tuple(self._artifact_from_row(row) for row in rows)

    def transition_run_artifact(
        self,
        run_id: str,
        *,
        role: CatalogRole | str,
        ordinal: int,
        expected_path: str | os.PathLike[str],
        expected_state: LocationState | str,
        new_path: str | os.PathLike[str],
        new_state: LocationState | str,
    ) -> RunArtifactRecord:
        """Move the catalog pointer for an already fingerprinted run artifact.

        The destination bytes must exist and still match the indexed digest.
        The expected path/state pair makes recovery and quarantine transitions
        compare-and-swap operations instead of blind updates.
        """

        self.get_run(run_id)
        selected_role = CatalogRole(role)
        selected_expected_state = LocationState(expected_state)
        selected_new_state = LocationState(new_state)
        _non_negative_int(ordinal, "ordinal")
        expected = str(Path(expected_path).resolve())
        destination = Path(new_path).resolve()
        observed = fingerprint_file(destination)
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT * FROM run_artifacts
            WHERE run_id = ? AND role = ? AND ordinal = ?
            """,
            (run_id, selected_role.value, ordinal),
        ).fetchone()
        if row is None:
            raise CatalogError(f"unknown run artifact: {run_id}/{selected_role.value}/{ordinal}")
        artifact = self._artifact_from_row(row)
        if (
            str(Path(artifact.path).resolve()) != expected
            or artifact.state is not selected_expected_state
        ):
            raise CatalogConflictError(
                "run artifact path/state changed before the requested transition"
            )
        if artifact.sha256 != observed.sha256 or artifact.size_bytes != observed.size_bytes:
            raise CatalogConflictError(
                "run artifact destination bytes do not match the indexed fingerprint"
            )
        with self.transaction():
            cursor = connection.execute(
                """
                UPDATE run_artifacts
                SET path = ?, state = ?
                WHERE run_id = ? AND role = ? AND ordinal = ?
                  AND path = ? AND state = ?
                """,
                (
                    str(destination),
                    selected_new_state.value,
                    run_id,
                    selected_role.value,
                    ordinal,
                    artifact.path,
                    selected_expected_state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise CatalogConflictError(
                    "run artifact changed concurrently during the requested transition"
                )
            self._bump_revision()
        refreshed = connection.execute(
            """
            SELECT * FROM run_artifacts
            WHERE run_id = ? AND role = ? AND ordinal = ?
            """,
            (run_id, selected_role.value, ordinal),
        ).fetchone()
        if refreshed is None:
            raise CatalogFormatError("transitioned run artifact disappeared")
        return self._artifact_from_row(refreshed)

    def transition_location_state(
        self,
        track_id: str,
        path: str | os.PathLike[str],
        *,
        expected_state: LocationState | str,
        new_state: LocationState | str,
    ) -> LocationRecord:
        """Compare-and-swap one known track location's availability state."""

        self.get_track(track_id)
        expected = LocationState(expected_state)
        selected = LocationState(new_state)
        normalized = _normalized_path(path)
        now = _utc_now()
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT * FROM locations
            WHERE track_id = ? AND normalized_path = ?
            """,
            (track_id, normalized),
        ).fetchone()
        if row is None:
            raise CatalogError(f"unknown location for track {track_id}: {Path(path).resolve()}")
        location = self._location_from_row(row)
        if location.state is not expected:
            raise CatalogConflictError(
                "track location state changed before the requested transition"
            )
        if selected is LocationState.AVAILABLE:
            observed = fingerprint_file(path)
            track = self.get_track(track_id)
            if observed.sha256 != track.sha256 or observed.size_bytes != track.size_bytes:
                raise CatalogConflictError(
                    "restored track location bytes do not match the catalog identity"
                )
        with self.transaction():
            cursor = connection.execute(
                """
                UPDATE locations
                SET state = ?, last_verified_at = ?
                WHERE track_id = ? AND normalized_path = ? AND state = ?
                """,
                (
                    selected.value,
                    now,
                    track_id,
                    normalized,
                    expected.value,
                ),
            )
            if cursor.rowcount != 1:
                raise CatalogConflictError(
                    "track location changed concurrently during the requested transition"
                )
            self._bump_revision()
        updated = connection.execute(
            """
            SELECT * FROM locations
            WHERE track_id = ? AND normalized_path = ?
            """,
            (track_id, normalized),
        ).fetchone()
        if updated is None:
            raise CatalogFormatError("transitioned track location disappeared")
        return self._location_from_row(updated)

    def ingest_manifest(
        self,
        manifest_path: str | os.PathLike[str],
        *,
        selection: CatalogSelection | None = None,
    ) -> RunRecord:
        """Index a committed run manifest and every fingerprinted artifact it names.

        Files are reopened and fingerprinted by :meth:`index_artifact`; a stale
        or tampered manifest therefore cannot silently seed the catalog.
        Re-ingesting the same run replaces its artifact index atomically.
        """

        manifest_file = Path(manifest_path).resolve()
        manifest = RunManifest.load(manifest_file)
        manifest_fingerprint = fingerprint_file(manifest_file)
        artifacts = (*manifest.inputs, *manifest.outputs)
        for artifact in artifacts:
            if artifact.fingerprint is None:
                raise CatalogConflictError(f"manifest artifact has no fingerprint: {artifact.role}")
            observed = fingerprint_file(artifact.path)
            if (
                observed.sha256 != artifact.fingerprint.sha256
                or observed.size_bytes != artifact.fingerprint.size_bytes
            ):
                raise CatalogConflictError(
                    f"manifest artifact changed before catalog ingestion: {artifact.path}"
                )

        with self.transaction():
            run = self.record_run(
                manifest.run_id,
                manifest.status.value,
                selection=selection,
                manifest_path=manifest_file,
                manifest_sha256=manifest_fingerprint.sha256,
            )
            self._require_connection().execute(
                "DELETE FROM run_artifacts WHERE run_id = ?",
                (manifest.run_id,),
            )
            for artifact in artifacts:
                fingerprint = artifact.fingerprint
                if fingerprint is None:
                    raise AssertionError("fingerprints were validated before the transaction")
                role = _catalog_role_for_manifest_artifact(artifact.role)
                track_id = self._track_id_for_sha256(fingerprint.sha256)
                self.index_artifact(
                    manifest.run_id,
                    artifact.path,
                    role=role,
                    track_id=track_id,
                    media_type=artifact.media_type,
                    metadata={
                        "manifest_role": artifact.role,
                        **dict(artifact.metadata),
                    },
                )
            self.index_artifact(
                manifest.run_id,
                manifest_file,
                role=CatalogRole.AUDIT,
                media_type="application/json",
                metadata={"manifest_role": "run-manifest"},
            )
        return run

    def export_json(self, *, indent: int = 2) -> str:
        """Export the complete catalog as strict, versioned JSON."""

        tracks = [
            {
                "track_id": track.track_id,
                "sha256": track.sha256,
                "size_bytes": track.size_bytes,
                "roles": [role.value for role in track.roles],
                "label": track.label,
                "audio_facts": (
                    track.audio_facts.to_dict() if track.audio_facts is not None else None
                ),
                "archived": track.archived,
                "created_at": track.created_at,
                "updated_at": track.updated_at,
            }
            for track in self.list_tracks(include_archived=True)
        ]
        locations = [
            {
                "location_id": location.location_id,
                "track_id": location.track_id,
                "path": location.path,
                "state": location.state.value,
                "modified_ns": location.modified_ns,
                "last_verified_at": location.last_verified_at,
            }
            for track in self.list_tracks(include_archived=True)
            for location in self.list_locations(track.track_id)
        ]
        reference_sets = [
            {
                "set_id": item.set_id,
                "name": item.name,
                "members": [
                    {
                        "track_id": member.track_id,
                        "ordinal": member.ordinal,
                        "level_weight": member.level_weight,
                        "frequency_weight": member.frequency_weight,
                    }
                    for member in item.members
                ],
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in self.list_reference_sets()
        ]
        runs = [
            {
                "run_id": run.run_id,
                "status": run.status,
                "selection_id": run.selection_id,
                "selection_json": self._run_selection_json(run.run_id),
                "manifest_path": run.manifest_path,
                "manifest_sha256": run.manifest_sha256,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
            }
            for run in self.list_runs()
        ]
        artifacts = [
            {
                "run_id": artifact.run_id,
                "role": artifact.role.value,
                "ordinal": artifact.ordinal,
                "artifact_id": artifact.artifact_id,
                "track_id": artifact.track_id,
                "path": artifact.path,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "media_type": artifact.media_type,
                "state": artifact.state.value,
                "metadata": dict(artifact.metadata),
            }
            for run in self.list_runs()
            for artifact in self.list_run_artifacts(run.run_id)
        ]
        document = {
            "kind": CATALOG_DOCUMENT_KIND,
            "schema_version": JSON_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "revision": self.revision,
            "tracks": tracks,
            "locations": locations,
            "reference_sets": reference_sets,
            "runs": runs,
            "artifacts": artifacts,
        }
        return (
            json.dumps(
                document,
                indent=indent,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )

    def import_json(self, document: str | bytes) -> None:
        """Strictly merge a complete catalog export in one transaction."""

        value = _load_json_object(document)
        _check_fields(
            value,
            {
                "kind",
                "schema_version",
                "catalog_id",
                "revision",
                "tracks",
                "locations",
                "reference_sets",
                "runs",
                "artifacts",
            },
            {
                "kind",
                "schema_version",
                "catalog_id",
                "revision",
                "tracks",
                "locations",
                "reference_sets",
                "runs",
                "artifacts",
            },
            "catalog",
        )
        if value["kind"] != CATALOG_DOCUMENT_KIND:
            raise CatalogFormatError(f"unsupported catalog kind: {value['kind']!r}")
        if value["schema_version"] != JSON_SCHEMA_VERSION:
            raise CatalogFormatError(
                f"unsupported catalog schema_version: {value['schema_version']!r}"
            )
        _non_empty(_required_string(value["catalog_id"], "catalog.catalog_id"), "catalog_id")
        _non_negative_int(
            _required_int(value["revision"], "catalog.revision"),
            "catalog.revision",
        )
        tracks = _required_list(value["tracks"], "catalog.tracks")
        locations = _required_list(value["locations"], "catalog.locations")
        reference_sets = _required_list(
            value["reference_sets"],
            "catalog.reference_sets",
        )
        runs = _required_list(value["runs"], "catalog.runs")
        artifacts = _required_list(value["artifacts"], "catalog.artifacts")

        parsed_tracks = tuple(
            self._parse_export_track(
                _required_object(item, f"catalog.tracks[{index}]"),
                f"catalog.tracks[{index}]",
            )
            for index, item in enumerate(tracks)
        )
        parsed_locations = tuple(
            self._parse_export_location(
                _required_object(item, f"catalog.locations[{index}]"),
                f"catalog.locations[{index}]",
            )
            for index, item in enumerate(locations)
        )
        parsed_sets = tuple(
            self._parse_export_set(
                _required_object(item, f"catalog.reference_sets[{index}]"),
                f"catalog.reference_sets[{index}]",
            )
            for index, item in enumerate(reference_sets)
        )
        parsed_runs = tuple(
            self._parse_export_run(
                _required_object(item, f"catalog.runs[{index}]"),
                f"catalog.runs[{index}]",
            )
            for index, item in enumerate(runs)
        )
        parsed_artifacts = tuple(
            self._parse_export_artifact(
                _required_object(item, f"catalog.artifacts[{index}]"),
                f"catalog.artifacts[{index}]",
            )
            for index, item in enumerate(artifacts)
        )
        self._validate_import_references(
            parsed_tracks,
            parsed_locations,
            parsed_sets,
            parsed_runs,
            parsed_artifacts,
        )
        try:
            with self.transaction():
                self._merge_import(
                    parsed_tracks,
                    parsed_locations,
                    parsed_sets,
                    parsed_runs,
                    parsed_artifacts,
                )
                self._bump_revision()
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(f"catalog import conflicts with local state: {exc}") from exc

    def _initialize_schema(self) -> None:
        connection = self._require_connection()
        version_row = connection.execute("PRAGMA user_version").fetchone()
        if version_row is None:
            raise CatalogFormatError("could not read catalog schema version")
        version = cast(int, version_row[0])
        if version > CATALOG_SCHEMA_VERSION:
            raise CatalogFormatError(
                f"catalog schema {version} is newer than supported schema {CATALOG_SCHEMA_VERSION}"
            )
        if version == 0:
            existing = tuple(
                connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                )
            )
            if existing:
                raise CatalogFormatError(
                    "unversioned database already contains tables; refusing an unsafe migration"
                )
            catalog_id = _CATALOG_PREFIX + uuid.uuid4().hex
            connection.executescript(_SCHEMA_V1)
            connection.executemany(
                "INSERT INTO catalog_meta (key, value) VALUES (?, ?)",
                (
                    ("catalog_id", catalog_id),
                    ("revision", "0"),
                ),
            )
        elif version == 1:
            for key in ("catalog_id", "revision"):
                self._meta(key)

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CatalogError("catalog is closed")
        return self._connection

    def _meta(self, key: str) -> str:
        row = (
            self._require_connection()
            .execute(
                "SELECT value FROM catalog_meta WHERE key = ?",
                (key,),
            )
            .fetchone()
        )
        if row is None:
            raise CatalogFormatError(f"catalog metadata is missing {key!r}")
        return _row_string(row, "value")

    def _bump_revision(self) -> None:
        cursor = self._require_connection().execute(
            """
            UPDATE catalog_meta
            SET value = CAST(value AS INTEGER) + 1
            WHERE key = 'revision'
            """
        )
        if cursor.rowcount != 1:
            raise CatalogFormatError("catalog revision metadata could not be updated")

    def _track_from_row(self, row: sqlite3.Row) -> TrackRecord:
        track_id = _row_string(row, "track_id")
        roles = tuple(
            CatalogRole(_row_string(role, "role"))
            for role in self._require_connection().execute(
                "SELECT role FROM track_roles WHERE track_id = ? ORDER BY role",
                (track_id,),
            )
        )
        return TrackRecord(
            track_id=track_id,
            sha256=_row_string(row, "sha256"),
            size_bytes=_row_int(row, "size_bytes"),
            roles=roles,
            label=_row_optional_string(row, "label"),
            audio_facts=_audio_facts_from_row(row),
            archived=bool(_row_int(row, "archived")),
            created_at=_row_string(row, "created_at"),
            updated_at=_row_string(row, "updated_at"),
        )

    @staticmethod
    def _location_from_row(row: sqlite3.Row) -> LocationRecord:
        return LocationRecord(
            location_id=_row_string(row, "location_id"),
            track_id=_row_string(row, "track_id"),
            path=_row_string(row, "path"),
            state=LocationState(_row_string(row, "state")),
            modified_ns=_row_optional_int(row, "modified_ns"),
            last_verified_at=_row_optional_string(row, "last_verified_at"),
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> RunArtifactRecord:
        raw_metadata = _load_json_object(_row_string(row, "metadata_json"))
        return RunArtifactRecord(
            run_id=_row_string(row, "run_id"),
            role=CatalogRole(_row_string(row, "role")),
            ordinal=_row_int(row, "ordinal"),
            artifact_id=_row_string(row, "artifact_id"),
            track_id=_row_optional_string(row, "track_id"),
            path=_row_string(row, "path"),
            sha256=_row_string(row, "sha256"),
            size_bytes=_row_int(row, "size_bytes"),
            media_type=_row_optional_string(row, "media_type"),
            state=LocationState(_row_string(row, "state")),
            metadata=MappingProxyType(dict(raw_metadata)),
        )

    def _selected_track(self, track: TrackRecord) -> SelectedTrack:
        location = self.preferred_location(track.track_id)
        return SelectedTrack(
            track_id=track.track_id,
            sha256=track.sha256,
            size_bytes=track.size_bytes,
            path=location.path,
            label=track.label,
        )

    def _track_id_for_sha256(self, sha256: str) -> str | None:
        row = (
            self._require_connection()
            .execute(
                "SELECT track_id FROM tracks WHERE sha256 = ?",
                (sha256,),
            )
            .fetchone()
        )
        return None if row is None else _row_string(row, "track_id")

    def _insert_imported_track(
        self,
        entry: SelectedTrack,
        *,
        roles: Sequence[CatalogRole],
        modified_ns: int | None,
        state: LocationState,
    ) -> None:
        connection = self._require_connection()
        now = _utc_now()
        normalized_path = _normalized_path(entry.path)
        location_id = _location_id(normalized_path)
        path_collision = connection.execute(
            "SELECT track_id FROM locations WHERE normalized_path = ?",
            (normalized_path,),
        ).fetchone()
        if path_collision is not None and _row_string(path_collision, "track_id") != entry.track_id:
            raise CatalogConflictError(f"imported path belongs to different content: {entry.path}")
        connection.execute(
            """
            INSERT INTO tracks (
                track_id, sha256, size_bytes, label, archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                label = COALESCE(tracks.label, excluded.label),
                updated_at = excluded.updated_at
            """,
            (
                entry.track_id,
                entry.sha256,
                entry.size_bytes,
                entry.label,
                now,
                now,
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO track_roles (track_id, role) VALUES (?, ?)",
            ((entry.track_id, role.value) for role in roles),
        )
        connection.execute(
            """
            INSERT INTO locations (
                location_id, track_id, path, normalized_path, state,
                modified_ns, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(location_id) DO UPDATE SET
                state = excluded.state,
                modified_ns = excluded.modified_ns,
                last_verified_at = excluded.last_verified_at
            """,
            (
                location_id,
                entry.track_id,
                str(Path(entry.path).resolve()),
                normalized_path,
                state.value,
                modified_ns,
                now,
            ),
        )

    def _run_selection_json(self, run_id: str) -> str | None:
        row = (
            self._require_connection()
            .execute(
                "SELECT selection_json FROM runs WHERE run_id = ?",
                (run_id,),
            )
            .fetchone()
        )
        if row is None:
            raise CatalogError(f"unknown run: {run_id}")
        return _row_optional_string(row, "selection_json")

    @staticmethod
    def _parse_export_track(
        value: Mapping[str, Any],
        where: str,
    ) -> tuple[TrackRecord, tuple[CatalogRole, ...]]:
        _check_fields(
            value,
            {
                "track_id",
                "sha256",
                "size_bytes",
                "roles",
                "label",
                "audio_facts",
                "archived",
                "created_at",
                "updated_at",
            },
            {
                "track_id",
                "sha256",
                "size_bytes",
                "roles",
                "label",
                "audio_facts",
                "archived",
                "created_at",
                "updated_at",
            },
            where,
        )
        sha256 = _required_string(value["sha256"], f"{where}.sha256")
        track_id = _required_string(value["track_id"], f"{where}.track_id")
        _validate_track_id(track_id, expected_sha256=sha256)
        raw_roles = _required_list(value["roles"], f"{where}.roles")
        roles = _roles(
            tuple(
                _required_string(item, f"{where}.roles[{index}]")
                for index, item in enumerate(raw_roles)
            )
        )
        raw_audio = value["audio_facts"]
        audio_facts = (
            None
            if raw_audio is None
            else AudioFacts.from_dict(_required_object(raw_audio, f"{where}.audio_facts"))
        )
        track = TrackRecord(
            track_id=track_id,
            sha256=sha256,
            size_bytes=_required_int(value["size_bytes"], f"{where}.size_bytes"),
            roles=roles,
            label=_optional_string(value["label"], f"{where}.label"),
            audio_facts=audio_facts,
            archived=_required_bool(value["archived"], f"{where}.archived"),
            created_at=_required_string(value["created_at"], f"{where}.created_at"),
            updated_at=_required_string(value["updated_at"], f"{where}.updated_at"),
        )
        _validate_timestamp(track.created_at, f"{where}.created_at")
        _validate_timestamp(track.updated_at, f"{where}.updated_at")
        return track, roles

    @staticmethod
    def _parse_export_location(
        value: Mapping[str, Any],
        where: str,
    ) -> LocationRecord:
        _check_fields(
            value,
            {
                "location_id",
                "track_id",
                "path",
                "state",
                "modified_ns",
                "last_verified_at",
            },
            {"location_id", "track_id", "path", "state", "modified_ns", "last_verified_at"},
            where,
        )
        path = _required_string(value["path"], f"{where}.path")
        if not Path(path).is_absolute():
            raise CatalogFormatError(f"{where}.path must be absolute")
        normalized = _normalized_path(path)
        location_id = _required_string(value["location_id"], f"{where}.location_id")
        if location_id != _location_id(normalized):
            raise CatalogFormatError(f"{where}.location_id does not match its path")
        last_verified = _optional_string(
            value["last_verified_at"],
            f"{where}.last_verified_at",
        )
        if last_verified is not None:
            _validate_timestamp(last_verified, f"{where}.last_verified_at")
        modified_ns = _optional_int(value["modified_ns"], f"{where}.modified_ns")
        if modified_ns is not None:
            _non_negative_int(modified_ns, f"{where}.modified_ns")
        return LocationRecord(
            location_id=location_id,
            track_id=_required_string(value["track_id"], f"{where}.track_id"),
            path=str(Path(path).resolve()),
            state=LocationState(_required_string(value["state"], f"{where}.state")),
            modified_ns=modified_ns,
            last_verified_at=last_verified,
        )

    @staticmethod
    def _parse_export_set(
        value: Mapping[str, Any],
        where: str,
    ) -> ReferenceSetRecord:
        _check_fields(
            value,
            {"set_id", "name", "members", "created_at", "updated_at"},
            {"set_id", "name", "members", "created_at", "updated_at"},
            where,
        )
        members_raw = _required_list(value["members"], f"{where}.members")
        members: list[ReferenceSetMember] = []
        for index, raw_member in enumerate(members_raw):
            member_where = f"{where}.members[{index}]"
            member = _required_object(raw_member, member_where)
            _check_fields(
                member,
                {"track_id", "ordinal", "level_weight", "frequency_weight"},
                {"track_id", "ordinal", "level_weight", "frequency_weight"},
                member_where,
            )
            members.append(
                ReferenceSetMember(
                    track_id=_required_string(
                        member["track_id"],
                        f"{member_where}.track_id",
                    ),
                    ordinal=_required_int(member["ordinal"], f"{member_where}.ordinal"),
                    level_weight=_required_float(
                        member["level_weight"],
                        f"{member_where}.level_weight",
                    ),
                    frequency_weight=_required_float(
                        member["frequency_weight"],
                        f"{member_where}.frequency_weight",
                    ),
                )
            )
        if tuple(item.ordinal for item in members) != tuple(range(len(members))):
            raise CatalogFormatError(f"{where}.member ordinals must be contiguous")
        if len({item.track_id for item in members}) != len(members):
            raise CatalogFormatError(f"{where} contains duplicate track members")
        created_at = _required_string(value["created_at"], f"{where}.created_at")
        updated_at = _required_string(value["updated_at"], f"{where}.updated_at")
        _validate_timestamp(created_at, f"{where}.created_at")
        _validate_timestamp(updated_at, f"{where}.updated_at")
        set_id = _required_string(value["set_id"], f"{where}.set_id")
        if not set_id.startswith(_REFERENCE_SET_PREFIX):
            raise CatalogFormatError(f"{where}.set_id has an unsupported form")
        return ReferenceSetRecord(
            set_id=set_id,
            name=_required_string(value["name"], f"{where}.name"),
            members=tuple(members),
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _parse_export_run(value: Mapping[str, Any], where: str) -> tuple[RunRecord, str | None]:
        _check_fields(
            value,
            {
                "run_id",
                "status",
                "selection_id",
                "selection_json",
                "manifest_path",
                "manifest_sha256",
                "created_at",
                "updated_at",
            },
            {
                "run_id",
                "status",
                "selection_id",
                "selection_json",
                "manifest_path",
                "manifest_sha256",
                "created_at",
                "updated_at",
            },
            where,
        )
        selection_json = _optional_string(
            value["selection_json"],
            f"{where}.selection_json",
        )
        selection_id = _optional_string(value["selection_id"], f"{where}.selection_id")
        if (selection_json is None) != (selection_id is None):
            raise CatalogFormatError(
                f"{where}.selection_json and selection_id must both be present or absent"
            )
        if selection_json is not None:
            parsed_selection = CatalogSelection.from_json(selection_json)
            if parsed_selection.selection_id != selection_id:
                raise CatalogFormatError(f"{where}.selection_id does not match selection_json")
        manifest_sha256 = _optional_string(
            value["manifest_sha256"],
            f"{where}.manifest_sha256",
        )
        if manifest_sha256 is not None:
            _validate_sha256(manifest_sha256, f"{where}.manifest_sha256")
        created_at = _required_string(value["created_at"], f"{where}.created_at")
        updated_at = _required_string(value["updated_at"], f"{where}.updated_at")
        _validate_timestamp(created_at, f"{where}.created_at")
        _validate_timestamp(updated_at, f"{where}.updated_at")
        return (
            RunRecord(
                run_id=_required_string(value["run_id"], f"{where}.run_id"),
                status=_required_string(value["status"], f"{where}.status"),
                selection_id=selection_id,
                manifest_path=_optional_string(
                    value["manifest_path"],
                    f"{where}.manifest_path",
                ),
                manifest_sha256=manifest_sha256,
                created_at=created_at,
                updated_at=updated_at,
            ),
            selection_json,
        )

    @staticmethod
    def _parse_export_artifact(
        value: Mapping[str, Any],
        where: str,
    ) -> RunArtifactRecord:
        _check_fields(
            value,
            {
                "run_id",
                "role",
                "ordinal",
                "artifact_id",
                "track_id",
                "path",
                "sha256",
                "size_bytes",
                "media_type",
                "state",
                "metadata",
            },
            {
                "run_id",
                "role",
                "ordinal",
                "artifact_id",
                "track_id",
                "path",
                "sha256",
                "size_bytes",
                "media_type",
                "state",
                "metadata",
            },
            where,
        )
        sha256 = _required_string(value["sha256"], f"{where}.sha256")
        _validate_sha256(sha256, f"{where}.sha256")
        artifact_id = _required_string(value["artifact_id"], f"{where}.artifact_id")
        if artifact_id != _artifact_id(sha256):
            raise CatalogFormatError(f"{where}.artifact_id does not match sha256")
        metadata = _required_object(value["metadata"], f"{where}.metadata")
        _validate_json_value(metadata, f"{where}.metadata")
        track_id = _optional_string(value["track_id"], f"{where}.track_id")
        if track_id is not None:
            _validate_track_id(track_id)
        return RunArtifactRecord(
            run_id=_required_string(value["run_id"], f"{where}.run_id"),
            role=CatalogRole(_required_string(value["role"], f"{where}.role")),
            ordinal=_required_int(value["ordinal"], f"{where}.ordinal"),
            artifact_id=artifact_id,
            track_id=track_id,
            path=_required_string(value["path"], f"{where}.path"),
            sha256=sha256,
            size_bytes=_required_int(value["size_bytes"], f"{where}.size_bytes"),
            media_type=_optional_string(value["media_type"], f"{where}.media_type"),
            state=LocationState(_required_string(value["state"], f"{where}.state")),
            metadata=MappingProxyType(dict(metadata)),
        )

    @staticmethod
    def _validate_import_references(
        tracks: Sequence[tuple[TrackRecord, tuple[CatalogRole, ...]]],
        locations: Sequence[LocationRecord],
        reference_sets: Sequence[ReferenceSetRecord],
        runs: Sequence[tuple[RunRecord, str | None]],
        artifacts: Sequence[RunArtifactRecord],
    ) -> None:
        track_ids = {track.track_id for track, _ in tracks}
        run_ids = {run.run_id for run, _ in runs}
        if len(track_ids) != len(tracks):
            raise CatalogFormatError("catalog export contains duplicate tracks")
        if len(run_ids) != len(runs):
            raise CatalogFormatError("catalog export contains duplicate runs")
        location_ids = {item.location_id for item in locations}
        if len(location_ids) != len(locations):
            raise CatalogFormatError("catalog export contains duplicate locations")
        for location in locations:
            if location.track_id not in track_ids:
                raise CatalogFormatError("location references an absent track")
        set_ids = {item.set_id for item in reference_sets}
        if len(set_ids) != len(reference_sets):
            raise CatalogFormatError("catalog export contains duplicate reference sets")
        for reference_set in reference_sets:
            for member in reference_set.members:
                if member.track_id not in track_ids:
                    raise CatalogFormatError("reference set references an absent track")
        artifact_keys = {(item.run_id, item.role, item.ordinal) for item in artifacts}
        if len(artifact_keys) != len(artifacts):
            raise CatalogFormatError("catalog export contains duplicate run artifact positions")
        for artifact in artifacts:
            if artifact.run_id not in run_ids:
                raise CatalogFormatError("artifact references an absent run")
            if artifact.track_id is not None and artifact.track_id not in track_ids:
                raise CatalogFormatError("artifact references an absent track")

    def _merge_import(
        self,
        tracks: Sequence[tuple[TrackRecord, tuple[CatalogRole, ...]]],
        locations: Sequence[LocationRecord],
        reference_sets: Sequence[ReferenceSetRecord],
        runs: Sequence[tuple[RunRecord, str | None]],
        artifacts: Sequence[RunArtifactRecord],
    ) -> None:
        connection = self._require_connection()
        for track, roles in tracks:
            facts = track.audio_facts
            connection.execute(
                """
                INSERT INTO tracks (
                    track_id, sha256, size_bytes, label,
                    sample_rate, channels, frames, duration_seconds,
                    audio_format, audio_subtype, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id) DO NOTHING
                """,
                (
                    track.track_id,
                    track.sha256,
                    track.size_bytes,
                    track.label,
                    facts.sample_rate if facts else None,
                    facts.channels if facts else None,
                    facts.frames if facts else None,
                    facts.duration_seconds if facts else None,
                    facts.format if facts else None,
                    facts.subtype if facts else None,
                    int(track.archived),
                    track.created_at,
                    track.updated_at,
                ),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO track_roles (track_id, role) VALUES (?, ?)",
                ((track.track_id, role.value) for role in roles),
            )
        for location in locations:
            normalized_path = _normalized_path(location.path)
            connection.execute(
                """
                INSERT INTO locations (
                    location_id, track_id, path, normalized_path, state,
                    modified_ns, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id) DO NOTHING
                """,
                (
                    location.location_id,
                    location.track_id,
                    location.path,
                    normalized_path,
                    location.state.value,
                    location.modified_ns,
                    location.last_verified_at,
                ),
            )
        for reference_set in reference_sets:
            name_key = unicodedata.normalize("NFC", reference_set.name).casefold()
            connection.execute(
                """
                INSERT INTO reference_sets (
                    set_id, name, name_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(set_id) DO NOTHING
                """,
                (
                    reference_set.set_id,
                    reference_set.name,
                    name_key,
                    reference_set.created_at,
                    reference_set.updated_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO reference_set_members (
                    set_id, track_id, ordinal, level_weight, frequency_weight
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(set_id, track_id) DO NOTHING
                """,
                (
                    (
                        reference_set.set_id,
                        member.track_id,
                        member.ordinal,
                        member.level_weight,
                        member.frequency_weight,
                    )
                    for member in reference_set.members
                ),
            )
        for run, selection_json in runs:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, status, selection_id, selection_json,
                    manifest_path, manifest_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    run.run_id,
                    run.status,
                    run.selection_id,
                    selection_json,
                    run.manifest_path,
                    run.manifest_sha256,
                    run.created_at,
                    run.updated_at,
                ),
            )
        connection.executemany(
            """
            INSERT INTO run_artifacts (
                run_id, role, ordinal, artifact_id, track_id, path,
                sha256, size_bytes, media_type, state, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, role, ordinal) DO NOTHING
            """,
            (
                (
                    artifact.run_id,
                    artifact.role.value,
                    artifact.ordinal,
                    artifact.artifact_id,
                    artifact.track_id,
                    artifact.path,
                    artifact.sha256,
                    artifact.size_bytes,
                    artifact.media_type,
                    artifact.state.value,
                    _canonical_json(dict(artifact.metadata)),
                )
                for artifact in artifacts
            ),
        )


def default_catalog_path(workspace: str | os.PathLike[str] = ".") -> Path:
    """Return the conventional private catalog location for a workspace."""

    return Path(workspace).resolve() / ".mmt" / "catalog" / "catalog.sqlite3"


def _track_id(sha256: str) -> str:
    _validate_sha256(sha256, "sha256")
    return _TRACK_PREFIX + sha256.casefold()


def _artifact_id(sha256: str) -> str:
    _validate_sha256(sha256, "sha256")
    return _ARTIFACT_PREFIX + sha256.casefold()


def _location_id(normalized_path: str) -> str:
    return _LOCATION_PREFIX + hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()


def _catalog_role_for_manifest_artifact(role: str) -> CatalogRole:
    normalized = role.casefold()
    if normalized == "target":
        return CatalogRole.TARGET
    if normalized.startswith("reference"):
        return CatalogRole.REFERENCE
    if any(
        marker in normalized
        for marker in (
            "manifest",
            "event-log",
            "configuration",
            "config",
            "selection",
            "report",
            "diagnostic",
        )
    ):
        return CatalogRole.AUDIT
    return CatalogRole.GENERATED_OUTPUT


def _normalized_path(path: str | os.PathLike[str]) -> str:
    resolved = str(Path(path).resolve())
    return unicodedata.normalize("NFC", os.path.normcase(resolved))


def _roles(values: Sequence[CatalogRole | str]) -> tuple[CatalogRole, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise TypeError("roles must be a sequence")
    roles = tuple(sorted({CatalogRole(value) for value in values}, key=lambda item: item.value))
    if not roles:
        raise ValueError("at least one catalog role is required")
    return roles


def _reference_set_members(
    value: object,
) -> tuple[ReferenceSetMember, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(
            "members must contain ReferenceSetMember values or "
            "(track_id, level_weight, frequency_weight) tuples"
        )
    entries = tuple(value)
    if all(isinstance(item, ReferenceSetMember) for item in entries):
        return tuple(
            ReferenceSetMember(
                track_id=item.track_id,
                ordinal=item.ordinal,
                level_weight=item.level_weight,
                frequency_weight=item.frequency_weight,
            )
            for item in entries
            if isinstance(item, ReferenceSetMember)
        )

    members: list[ReferenceSetMember] = []
    for ordinal, item in enumerate(entries):
        if not isinstance(item, tuple) or len(item) != 3:
            raise TypeError(
                "members must contain ReferenceSetMember values or "
                "(track_id, level_weight, frequency_weight) tuples"
            )
        track_id, level_weight, frequency_weight = item
        if not isinstance(track_id, str):
            raise TypeError("reference set member track_id must be a string")
        if not isinstance(level_weight, (int, float)) or isinstance(level_weight, bool):
            raise TypeError("reference set member level_weight must be a number")
        if not isinstance(frequency_weight, (int, float)) or isinstance(
            frequency_weight,
            bool,
        ):
            raise TypeError("reference set member frequency_weight must be a number")
        members.append(
            ReferenceSetMember(
                track_id=track_id,
                ordinal=ordinal,
                level_weight=level_weight,
                frequency_weight=frequency_weight,
            )
        )
    return tuple(members)


def _validate_track_id(value: str, *, expected_sha256: str | None = None) -> None:
    _non_empty(value, "track_id")
    if not value.startswith(_TRACK_PREFIX):
        raise ValueError("track_id must use the trk_sha256_ prefix")
    digest = value.removeprefix(_TRACK_PREFIX)
    _validate_sha256(digest, "track_id digest")
    if expected_sha256 is not None and digest != expected_sha256.casefold():
        raise ValueError("track_id does not match sha256")


def _validate_sha256(value: str, where: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{where} must be a string")
    normalized = value.casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{where} must be 64 hexadecimal characters")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_timestamp(value: str, where: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogFormatError(f"{where} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CatalogFormatError(f"{where} must include a timezone")


def _audio_facts_from_row(row: sqlite3.Row) -> AudioFacts | None:
    sample_rate = _row_optional_int(row, "sample_rate")
    channels = _row_optional_int(row, "channels")
    frames = _row_optional_int(row, "frames")
    duration = _row_optional_float(row, "duration_seconds")
    values = (sample_rate, channels, frames, duration)
    if all(item is None for item in values):
        return None
    if any(item is None for item in values):
        raise CatalogFormatError("catalog contains partial audio facts")
    return AudioFacts(
        sample_rate=cast(int, sample_rate),
        channels=cast(int, channels),
        frames=cast(int, frames),
        duration_seconds=cast(float, duration),
        format=_row_optional_string(row, "audio_format"),
        subtype=_row_optional_string(row, "audio_subtype"),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _load_json_object(document: str | bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            document,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise CatalogFormatError(f"invalid JSON document: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogFormatError("JSON document root must be an object")
    _validate_json_value(value, "document")
    return cast(dict[str, Any], value)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _validate_json_value(value: Any, where: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CatalogFormatError(f"{where} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{where}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CatalogFormatError(f"{where} contains a non-string object key")
            _validate_json_value(item, f"{where}.{key}")
        return
    raise CatalogFormatError(f"{where} contains unsupported JSON value {type(value).__name__}")


def _copy_json_value(value: Any, where: str) -> Any:
    """Return mutable JSON data, accepting tuples from immutable manifests."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CatalogFormatError(f"{where} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return [_copy_json_value(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CatalogFormatError(f"{where} contains a non-string object key")
            copied[key] = _copy_json_value(item, f"{where}.{key}")
        return copied
    raise CatalogFormatError(f"{where} contains unsupported JSON value {type(value).__name__}")


def _check_fields(
    value: Mapping[str, Any],
    allowed: set[str],
    required: set[str],
    where: str,
) -> None:
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise CatalogFormatError(f"{where} contains unknown field(s): {sorted(unknown)}")
    if missing:
        raise CatalogFormatError(f"{where} is missing required field(s): {sorted(missing)}")


def _required_object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogFormatError(f"{where} must be an object")
    return cast(Mapping[str, Any], value)


def _required_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise CatalogFormatError(f"{where} must be an array")
    return value


def _required_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogFormatError(f"{where} must be a non-empty string")
    return value


def _optional_string(value: Any, where: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, where)


def _required_int(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogFormatError(f"{where} must be an integer")
    return value


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, where)


def _required_float(value: Any, where: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CatalogFormatError(f"{where} must be a number")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise CatalogFormatError(f"{where} must be finite")
    return rendered


def _required_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise CatalogFormatError(f"{where} must be a boolean")
    return value


def _non_empty(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty string")
    return value


def _optional_non_empty(value: str | None, where: str) -> None:
    if value is not None:
        _non_empty(value, where)


def _positive_int(value: int, where: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{where} must be a positive integer")


def _non_negative_int(value: int, where: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{where} must be a non-negative integer")


def _finite_non_negative(value: float, where: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{where} must be a finite non-negative number")


def _row_string(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise CatalogFormatError(f"database field {key!r} must be text")
    return value


def _row_optional_string(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise CatalogFormatError(f"database field {key!r} must be text or null")
    return value


def _row_int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogFormatError(f"database field {key!r} must be an integer")
    return value


def _row_optional_int(row: sqlite3.Row, key: str) -> int | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogFormatError(f"database field {key!r} must be an integer or null")
    return value


def _row_float(row: sqlite3.Row, key: str) -> float:
    value = row[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CatalogFormatError(f"database field {key!r} must be numeric")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise CatalogFormatError(f"database field {key!r} must be finite")
    return rendered


def _row_optional_float(row: sqlite3.Row, key: str) -> float | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CatalogFormatError(f"database field {key!r} must be numeric or null")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise CatalogFormatError(f"database field {key!r} must be finite")
    return rendered


_SCHEMA_V1 = """
BEGIN IMMEDIATE;

CREATE TABLE catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE tracks (
    track_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    label TEXT,
    sample_rate INTEGER CHECK (sample_rate > 0),
    channels INTEGER CHECK (channels > 0),
    frames INTEGER CHECK (frames >= 0),
    duration_seconds REAL CHECK (duration_seconds >= 0),
    audio_format TEXT,
    audio_subtype TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE track_roles (
    track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (
        role IN ('target', 'reference', 'generated-output', 'audit')
    ),
    PRIMARY KEY (track_id, role)
) STRICT;

CREATE TABLE locations (
    location_id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    normalized_path TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('available', 'missing', 'changed', 'unreadable', 'archived', 'deleted')
    ),
    modified_ns INTEGER CHECK (modified_ns >= 0),
    last_verified_at TEXT
) STRICT;
CREATE INDEX locations_track_id ON locations(track_id);

CREATE TABLE reference_sets (
    set_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE reference_set_members (
    set_id TEXT NOT NULL REFERENCES reference_sets(set_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    level_weight REAL NOT NULL CHECK (level_weight >= 0),
    frequency_weight REAL NOT NULL CHECK (frequency_weight >= 0),
    PRIMARY KEY (set_id, track_id),
    UNIQUE (set_id, ordinal),
    CHECK (level_weight > 0 OR frequency_weight > 0)
) STRICT;

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    selection_id TEXT,
    selection_json TEXT,
    manifest_path TEXT,
    manifest_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (selection_id IS NULL AND selection_json IS NULL)
        OR (selection_id IS NOT NULL AND selection_json IS NOT NULL)
    )
) STRICT;

CREATE TABLE run_artifacts (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (
        role IN ('target', 'reference', 'generated-output', 'audit')
    ),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    artifact_id TEXT NOT NULL,
    track_id TEXT REFERENCES tracks(track_id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    media_type TEXT,
    state TEXT NOT NULL CHECK (
        state IN ('available', 'missing', 'changed', 'unreadable', 'archived', 'deleted')
    ),
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (run_id, role, ordinal)
) STRICT;

PRAGMA user_version = 1;
COMMIT;
"""


__all__ = [
    "AudioFacts",
    "CATALOG_SCHEMA_VERSION",
    "CatalogConflictError",
    "CatalogError",
    "CatalogFormatError",
    "CatalogRole",
    "CatalogSelection",
    "CatalogStore",
    "LocationRecord",
    "LocationState",
    "ReferenceSetMember",
    "ReferenceSetRecord",
    "RunArtifactRecord",
    "RunRecord",
    "SelectedReference",
    "SelectedTrack",
    "TrackRecord",
    "default_catalog_path",
]
