"""Application services for the private local graphical portal.

This module deliberately contains no HTTP or browser code.  It gives the
desktop-facing adapter a typed, testable boundary over the same catalog,
configuration, validation, service, event, and manifest APIs used by the CLI.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
import threading
import traceback
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from .audio_probe import DefaultAudioProbe
from .catalog import AudioFacts as CatalogAudioFacts
from .catalog import (
    CatalogConflictError,
    CatalogError,
    CatalogFormatError,
    CatalogRole,
    CatalogSelection,
    CatalogStore,
    LocationRecord,
    LocationState,
    ReferenceSetRecord,
    RunArtifactRecord,
    RunRecord,
    TrackRecord,
)
from .catalog_integration import (
    finalize_catalog_run,
    recover_catalog_run,
    selection_with_weights,
)
from .config import (
    AudioConfig,
    DetectionConfig,
    EdgeCasePolicy,
    EngineKind,
    JobConfig,
    LimiterConfig,
    MatchingConfig,
    OutputMode,
    OutputSpec,
    PreviewConfig,
)
from .doctor import run_doctor
from .engine import create_engine
from .errors import MusicMasteringError
from .events import Event, EventSink
from .manifest import RunManifest, fingerprint_file
from .service import MasteringService
from .validation import validate_job
from .workbench import WorkspaceLayout

MAX_PORTAL_REFERENCES = 32
MAX_PORTAL_TARGETS = 32
MAX_OPERATION_EVENTS = 4_000
MAX_OPERATION_HISTORY = 50
PORTAL_PREFERENCES_KIND = "music-mastering-tools/portal-preferences"
PORTAL_PREFERENCES_SCHEMA_VERSION = 1
PORTAL_PREFERENCES_FILENAME = "portal-preferences.json"
PORTAL_DIALOG_LOCATIONS_FILENAME = "dialog-locations.json"
MASTER_DISCARD_DOCUMENT_KIND = "music-mastering-tools/master-version-discard"
MASTER_RESTORE_DOCUMENT_KIND = "music-mastering-tools/master-version-restore"
MASTER_LIBRARY_SCHEMA_VERSION = 1
MAX_DISPLAY_LABEL_CHARACTERS = 200
MAX_EXPORT_ITEMS = 512


class PortalBusyError(RuntimeError):
    """A serialized background operation is already running."""


@dataclass(frozen=True, slots=True)
class PreparedJob:
    """One immutable job plus its planned or materialized private paths."""

    job: JobConfig
    selection: CatalogSelection
    configuration_path: Path
    selection_path: Path
    manifest_path: Path
    event_log_path: Path
    output_root: Path
    output_directory: Path
    materialized: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job.execution.job_id,
            "target_id": self.selection.target.track_id,
            "configuration_path": str(self.configuration_path),
            "selection_path": str(self.selection_path),
            "manifest_path": str(self.manifest_path),
            "event_log_path": str(self.event_log_path),
            "output_root": str(self.output_root),
            "output_directory": str(self.output_directory),
            "selection_id": self.selection.selection_id,
            "engine": self.job.execution.engine.value,
            "materialized": self.materialized,
            "outputs": [item.path for item in self.job.outputs],
            "preview_paths": [
                path
                for path in (
                    self.job.preview.target_path,
                    self.job.preview.result_path,
                )
                if path is not None
            ],
        }


@dataclass(slots=True)
class OperationRecord:
    """Thread-safe state for one serialized long-running portal operation."""

    operation_id: str
    kind: str
    state: str = "queued"
    stage: str | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    started_at: str | None = None
    finished_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "state": self.state,
            "stage": self.stage,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "events": list(self.events),
            "result": _json_ready(self.result),
            "error": _json_ready(self.error),
        }


class OperationEventSink(EventSink):
    """Append immutable service events to one portal operation."""

    def __init__(self, manager: PortalOperationManager, operation_id: str) -> None:
        self._manager = manager
        self._operation_id = operation_id

    def emit(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")
        self._manager.append_event(self._operation_id, event)


OperationWorker = Callable[[OperationEventSink], Mapping[str, Any]]


class PortalOperationManager:
    """Run at most one non-thread-safe mastering operation in the background."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mmt-portal-operation",
        )
        self._lock = threading.RLock()
        self._records: dict[str, OperationRecord] = {}
        self._order: list[str] = []
        self._active_id: str | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            if self._active_id is None:
                return False
            record = self._records[self._active_id]
            return record.state in {"queued", "running"}

    def start(self, kind: str, worker: OperationWorker) -> OperationRecord:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("operation kind must be a non-empty string")
        with self._lock:
            if self.active:
                raise PortalBusyError(
                    "another mastering operation is already running; wait for it to finish"
                )
            operation_id = f"op_{uuid.uuid4().hex}"
            record = OperationRecord(operation_id=operation_id, kind=kind.strip())
            self._records[operation_id] = record
            self._order.append(operation_id)
            self._active_id = operation_id
            while len(self._order) > MAX_OPERATION_HISTORY:
                expired = self._order.pop(0)
                if expired != self._active_id:
                    self._records.pop(expired, None)
            self._executor.submit(self._run, operation_id, worker)
            return record

    def current(self) -> OperationRecord | None:
        with self._lock:
            if self._active_id is not None:
                return self._copy_record(self._records[self._active_id])
            if not self._order:
                return None
            return self._copy_record(self._records[self._order[-1]])

    def get(self, operation_id: str) -> OperationRecord:
        with self._lock:
            try:
                return self._copy_record(self._records[operation_id])
            except KeyError as exc:
                raise KeyError(f"unknown operation: {operation_id}") from exc

    def list(self) -> tuple[OperationRecord, ...]:
        with self._lock:
            return tuple(
                self._copy_record(self._records[operation_id])
                for operation_id in reversed(self._order)
            )

    def append_event(self, operation_id: str, event: Event) -> None:
        value = event.to_dict()
        with self._lock:
            record = self._records[operation_id]
            record.events.append(value)
            if len(record.events) > MAX_OPERATION_EVENTS:
                del record.events[: len(record.events) - MAX_OPERATION_EVENTS]
            if event.stage is not None:
                record.stage = event.stage

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _run(self, operation_id: str, worker: OperationWorker) -> None:
        with self._lock:
            record = self._records[operation_id]
            record.state = "running"
            record.started_at = _utc_now()
        sink = OperationEventSink(self, operation_id)
        try:
            result = dict(worker(sink))
        except Exception as exc:  # noqa: BLE001 - boundary records every failure
            self._logger.exception(
                "Portal operation %s (%s) failed",
                operation_id,
                record.kind,
            )
            error = _exception_payload(exc)
            error["traceback"] = traceback.format_exc()
            with self._lock:
                record.state = "failed"
                record.error = error
                record.finished_at = _utc_now()
        else:
            with self._lock:
                record.state = "succeeded"
                record.result = _json_ready(result)
                record.finished_at = _utc_now()
        finally:
            with self._lock:
                if self._active_id == operation_id:
                    self._active_id = None

    @staticmethod
    def _copy_record(record: OperationRecord) -> OperationRecord:
        return OperationRecord(
            operation_id=record.operation_id,
            kind=record.kind,
            state=record.state,
            stage=record.stage,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            events=[dict(item) for item in record.events],
            result=(dict(record.result) if record.result is not None else None),
            error=(dict(record.error) if record.error is not None else None),
        )


class PortalApplication:
    """GUI-independent application layer used by the local portal."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        service_factory: Callable[[], MasteringService] = MasteringService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.layout = WorkspaceLayout.create(workspace)
        self._service_factory = service_factory
        self.logger = logger or logging.getLogger(__name__)
        # Initialize/migrate the SQLite catalog before the threaded HTTP server
        # can issue its first parallel dashboard requests. Allowing several
        # first-use connections to race schema metadata creation can expose a
        # transient, partially initialized catalog to one request.
        with CatalogStore(self.layout.catalog_database) as catalog:
            self.logger.debug(
                "Portal catalog ready id=%s revision=%s path=%s",
                catalog.catalog_id,
                catalog.revision,
                catalog.path,
            )
        self.preferences_path = self.layout.root / PORTAL_PREFERENCES_FILENAME
        self._preferences_lock = threading.RLock()
        self.operations = PortalOperationManager(logger=self.logger)

    def close(self, *, wait: bool = True) -> None:
        self.operations.shutdown(wait=wait)

    def bootstrap(self) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            all_tracks = catalog.list_tracks(include_archived=True)
            active_tracks = tuple(track for track in all_tracks if not track.archived)
            runs = catalog.list_runs()
            return {
                "workspace": _workspace_payload(self.layout),
                "catalog": {
                    "catalog_id": catalog.catalog_id,
                    "database": str(catalog.path),
                    "revision": catalog.revision,
                    "active_track_count": len(active_tracks),
                    "track_count": len(all_tracks),
                    "reference_set_count": len(catalog.list_reference_sets()),
                    "run_count": len(runs),
                },
                "recent_runs": [_run_payload(run) for run in reversed(runs[-5:])],
                "privacy": {
                    "local_only": True,
                    "source_audio_copied": False,
                    "source_audio_deleted": False,
                    "public_release_enabled": False,
                },
                "limitations": _limitations(),
                "preferences": self.get_preferences(),
            }

    def job_defaults(self) -> dict[str, Any]:
        return {
            "audio": _json_ready(asdict(AudioConfig())),
            "matching": _json_ready(asdict(MatchingConfig())),
            "limiter": _json_ready(asdict(LimiterConfig())),
            "preview": _json_ready(asdict(PreviewConfig())),
            "detection": _json_ready(asdict(DetectionConfig())),
            "edge_cases": _json_ready(asdict(EdgeCasePolicy())),
            "outputs": {
                "limited": True,
                "normalized": False,
                "raw": False,
                "limited_subtype": "PCM_24",
                "normalized_subtype": "PCM_24",
                "raw_subtype": "FLOAT",
            },
            "notes": "",
            "limits": {
                "maximum_targets": MAX_PORTAL_TARGETS,
                "maximum_references": MAX_PORTAL_REFERENCES,
                "max_workers": 1,
            },
            "preferences": self.get_preferences(),
            "unsupported": _limitations(),
        }

    def get_preferences(self) -> dict[str, Any]:
        """Return the strict, versioned portal preferences document.

        The preferences file itself always lives under the private workspace.
        Its selected output directory may point elsewhere because choosing an
        operator-owned delivery folder is the purpose of this setting.
        """

        with self._preferences_lock:
            preferences_path = self._resolved_preferences_path()
            if not preferences_path.exists():
                return _default_preferences(self.layout)
            try:
                raw = json.loads(
                    preferences_path.read_text(encoding="utf-8"),
                    object_pairs_hook=_unique_preferences_object,
                    parse_constant=_reject_json_constant,
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"portal preferences are not valid JSON: {preferences_path}"
                ) from exc
            return _parse_preferences(raw)

    def update_preferences(self, preferences: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically replace the complete portal preferences document."""

        parsed = _parse_preferences(preferences)
        document = (
            json.dumps(
                parsed,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        with self._preferences_lock:
            _write_text_file(
                self._resolved_preferences_path(),
                document,
                overwrite=True,
            )
        return dict(parsed)

    def _resolved_preferences_path(self) -> Path:
        preferences_path = self.preferences_path.resolve()
        if not preferences_path.is_relative_to(self.layout.root):
            raise PermissionError("portal preferences path escapes the private workspace")
        return preferences_path

    def dialog_initial_directory(
        self,
        mode: str,
        purpose: str,
        initial_directory: str | None = None,
    ) -> Path:
        """Resolve picker context, then remembered location, then workspace defaults.

        Picker history is separate from the strict preferences schema so older
        preference documents and clients keep working without migration.
        """

        group = _dialog_location_group(mode, purpose)
        if initial_directory is not None:
            path = Path(_string(initial_directory, "initial_directory")).expanduser().resolve()
            self.logger.debug("Picker using current location group=%s path=%s", group, path)
            return path
        with self._preferences_lock:
            remembered = self._read_dialog_locations().get(group)
        if remembered is not None:
            path = Path(remembered)
            if path.is_dir():
                self.logger.debug("Picker using remembered location group=%s path=%s", group, path)
                return path
            self.logger.info("Picker remembered location unavailable group=%s path=%s", group, path)
        defaults = {
            "audio": self.layout.root,
            "folder": Path(self.get_preferences()["default_output_directory"]),
            "catalog": self.layout.exports,
            "selection": self.layout.exports,
            "job-configuration": self.layout.jobs,
            "run-manifest": self.layout.runs,
        }
        return defaults[group]

    def remember_dialog_location(
        self,
        mode: str,
        purpose: str,
        paths: Sequence[str],
    ) -> None:
        """Persist successful picks without changing history on cancellation."""

        group = _dialog_location_group(mode, purpose)
        if not paths:
            return
        selected = Path(paths[0]).expanduser().resolve()
        directory = selected if mode == "choose-folder" else selected.parent
        with self._preferences_lock:
            locations = self._read_dialog_locations()
            locations[group] = str(directory)
            document = {"schema_version": 1, "locations": locations}
            try:
                _write_text_file(
                    self._resolved_dialog_locations_path(),
                    json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    overwrite=True,
                )
            except OSError:
                # Choosing a usable path should still succeed if optional
                # history persistence fails; retain the diagnostic in logs.
                self.logger.warning(
                    "Could not persist picker location group=%s path=%s",
                    group,
                    directory,
                    exc_info=True,
                )
                return
        self.logger.debug("Picker remembered location group=%s path=%s", group, directory)

    def _resolved_dialog_locations_path(self) -> Path:
        path = (self.layout.root / PORTAL_DIALOG_LOCATIONS_FILENAME).resolve()
        if not path.is_relative_to(self.layout.root):
            raise PermissionError("dialog locations path escapes the private workspace")
        return path

    def _read_dialog_locations(self) -> dict[str, str]:
        path = self._resolved_dialog_locations_path()
        if not path.exists():
            return {}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(document, dict)
                or set(document) != {"schema_version", "locations"}
                or type(document["schema_version"]) is not int
                or document["schema_version"] != 1
                or not isinstance(document["locations"], dict)
            ):
                raise ValueError("invalid dialog locations document")
            locations: dict[str, str] = {}
            for group, value in document["locations"].items():
                _dialog_location_group(
                    "choose-folder"
                    if group == "folder"
                    else "open-audio"
                    if group == "audio"
                    else "open-json",
                    group,
                )
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or any(ord(character) < 32 for character in value)
                    or not Path(value).is_absolute()
                ):
                    raise ValueError("dialog location must be an absolute path")
                locations[group] = value
            return locations
        except (OSError, ValueError, TypeError):
            self.logger.warning("Ignoring unreadable picker history path=%s", path, exc_info=True)
            return {}

    def capabilities(self) -> dict[str, Any]:
        return {
            kind.value: create_engine(kind).capabilities.to_dict()
            for kind in (EngineKind.UPSTREAM, EngineKind.NATIVE)
        }

    def diagnostics(self) -> dict[str, Any]:
        return run_doctor().to_dict()

    def list_logs(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for path in sorted(
            self.layout.logs.glob("*.log"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        ):
            facts = path.stat()
            values.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": facts.st_size,
                    "modified_ns": facts.st_mtime_ns,
                }
            )
        return values

    def read_log(self, name: str, *, maximum_bytes: int = 262_144) -> dict[str, Any]:
        if Path(name).name != name or not name.casefold().endswith(".log"):
            raise ValueError("log name must be a plain .log file name")
        if maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be greater than zero")
        path = (self.layout.logs / name).resolve()
        if not path.is_relative_to(self.layout.logs):
            raise ValueError("log path escapes the private log directory")
        size_bytes = path.stat().st_size
        truncated = size_bytes > maximum_bytes
        with path.open("rb") as handle:
            if truncated:
                handle.seek(-maximum_bytes, os.SEEK_END)
            selected = handle.read(maximum_bytes)
        return {
            "name": name,
            "path": str(path),
            "truncated": truncated,
            "text": selected.decode("utf-8", errors="replace"),
        }

    def assert_revealable_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        if path == self.layout.root or path.is_relative_to(self.layout.root):
            return path
        output_directory = Path(self.get_preferences()["default_output_directory"]).resolve()
        if path == output_directory or path.is_relative_to(output_directory):
            return path
        with CatalogStore(self.layout.catalog_database) as catalog:
            for track in catalog.list_tracks(include_archived=True):
                if any(
                    Path(location.path).resolve() == path
                    for location in catalog.list_locations(track.track_id)
                ):
                    return path
            for run in catalog.list_runs():
                if run.manifest_path is not None and Path(run.manifest_path).resolve() == path:
                    return path
                if any(
                    Path(artifact.path).resolve() == path
                    for artifact in catalog.list_run_artifacts(run.run_id)
                ):
                    return path
        raise PermissionError("the requested path is not owned by the workspace or catalog")

    def list_tracks(
        self,
        *,
        role: str | None = None,
        include_archived: bool = True,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_role = CatalogRole(role) if role else None
        normalized_query = (query or "").strip().casefold()
        with CatalogStore(self.layout.catalog_database) as catalog:
            values: list[dict[str, Any]] = []
            for track in catalog.list_tracks(
                role=selected_role,
                include_archived=include_archived,
            ):
                payload = _track_payload(catalog, track)
                if (
                    normalized_query
                    and normalized_query
                    not in json.dumps(
                        payload,
                        sort_keys=True,
                    ).casefold()
                ):
                    continue
                values.append(payload)
            return values

    def get_track(self, track_id: str) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            return _track_payload(catalog, catalog.get_track(track_id))

    def update_track_label(self, track_id: str, label: object) -> dict[str, Any]:
        """Update catalog presentation metadata without renaming local audio."""

        selected_label = _validated_display_label(label)
        with CatalogStore(self.layout.catalog_database) as catalog:
            before = catalog.get_track(track_id)
            updated = catalog.update_track_label(track_id, selected_label)
            self.logger.info(
                "Updated catalog display label track_id=%s old_label=%r new_label=%r; "
                "audio locations were not changed",
                track_id,
                before.label,
                updated.label,
            )
            return _track_payload(catalog, updated)

    def master_library(
        self,
        *,
        include_discarded: bool = False,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Return source rows with completed master versions nested beneath them."""

        with CatalogStore(self.layout.catalog_database) as catalog:
            sources = tuple(
                track
                for track in catalog.list_tracks(
                    role=CatalogRole.TARGET,
                    include_archived=include_archived,
                )
                if CatalogRole.GENERATED_OUTPUT not in track.roles
            )
            runs_by_source: dict[str, list[RunRecord]] = {source.track_id: [] for source in sources}
            for run in reversed(catalog.list_runs()):
                if run.status != "completed":
                    continue
                selection = catalog.get_run_selection(run.run_id)
                if selection is None or selection.target.track_id not in runs_by_source:
                    continue
                if _mastered_output_artifacts(catalog, run):
                    runs_by_source[selection.target.track_id].append(run)

            payloads: list[dict[str, Any]] = []
            active_version_count = 0
            discarded_version_count = 0
            mastered_source_count = 0
            for source in sources:
                source_payload = _track_payload(catalog, source)
                all_versions = [
                    _master_version_payload(catalog, source, run)
                    for run in runs_by_source[source.track_id]
                ]
                active_versions = [
                    version for version in all_versions if version["library_status"] == "available"
                ]
                discarded_versions = [
                    version for version in all_versions if version["library_status"] == "discarded"
                ]
                unavailable_versions = [
                    version
                    for version in all_versions
                    if version["library_status"] == "unavailable"
                ]
                active_version_count += len(active_versions)
                discarded_version_count += len(discarded_versions)
                if active_versions:
                    status = "mastered"
                    mastered_source_count += 1
                elif discarded_versions and not unavailable_versions:
                    status = "discarded-only"
                elif unavailable_versions:
                    status = "unavailable"
                else:
                    status = "unmastered"

                visible_versions = (
                    all_versions
                    if include_discarded
                    else [
                        version
                        for version in all_versions
                        if version["library_status"] != "discarded"
                    ]
                )
                automatic_ab: dict[str, Any] | None = None
                if len(active_versions) == 1:
                    preferred = active_versions[0]["preferred_audition"]
                    if isinstance(preferred, Mapping):
                        automatic_ab = {
                            "a": {
                                "kind": "source",
                                "track_id": source.track_id,
                                "label": _source_display_name(source_payload),
                                "path": source_payload["preferred_path"],
                            },
                            "b": dict(preferred),
                        }
                payloads.append(
                    {
                        "source": source_payload,
                        "status": status,
                        "active_version_count": len(active_versions),
                        "discarded_version_count": len(discarded_versions),
                        "requires_version_choice": len(active_versions) > 1,
                        "automatic_ab": automatic_ab,
                        "versions": visible_versions,
                    }
                )

            payloads.sort(
                key=lambda item: (
                    _source_display_name(cast(Mapping[str, Any], item["source"])).casefold(),
                    cast(Mapping[str, Any], item["source"])["track_id"],
                )
            )
            return {
                "sources": payloads,
                "summary": {
                    "source_count": len(payloads),
                    "mastered_source_count": mastered_source_count,
                    "active_version_count": active_version_count,
                    "discarded_version_count": discarded_version_count,
                },
            }

    def export_master_versions(
        self,
        destination_directory: object,
        suffix: object,
        items: object,
    ) -> dict[str, Any]:
        """Copy selected preferred or explicit master deliverables as a batch."""

        destination = _absolute_directory_path(
            destination_directory,
            "master export.destination_directory",
        )
        if destination.exists() and not destination.is_dir():
            raise NotADirectoryError(f"master export destination is not a directory: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        if not isinstance(suffix, str):
            raise TypeError("master export.suffix must be a string")
        sanitized_suffix = _sanitize_filename_component(suffix, maximum=48, fallback="")
        if not isinstance(items, list):
            raise TypeError("master export.items must be a JSON array")
        if not items:
            raise ValueError("master export.items must contain at least one selection")
        if len(items) > MAX_EXPORT_ITEMS:
            raise ValueError(
                f"master export.items cannot contain more than {MAX_EXPORT_ITEMS} selections"
            )

        results: list[dict[str, Any]] = []
        self.logger.info(
            "Starting master batch export destination=%s item_count=%s "
            "requested_suffix=%r sanitized_suffix=%r",
            destination,
            len(items),
            suffix,
            sanitized_suffix,
        )
        with CatalogStore(self.layout.catalog_database) as catalog:
            for index, raw_item in enumerate(items):
                result: dict[str, Any] = {"index": index, "ok": False}
                try:
                    item = _mapping(raw_item, f"master export.items[{index}]")
                    _check_exact_fields(
                        item,
                        {"source_track_id", "version_id", "artifact_ordinal"},
                        {"source_track_id", "version_id"},
                        f"master export.items[{index}]",
                    )
                    source_track_id = _string(
                        item.get("source_track_id"),
                        f"master export.items[{index}].source_track_id",
                    )
                    version_id = _string(
                        item.get("version_id"),
                        f"master export.items[{index}].version_id",
                    )
                    ordinal = _optional_non_negative_integer(
                        item.get("artifact_ordinal"),
                        f"master export.items[{index}].artifact_ordinal",
                    )
                    result.update(
                        {
                            "source_track_id": source_track_id,
                            "version_id": version_id,
                            "artifact_ordinal": ordinal,
                        }
                    )
                    source = catalog.get_track(source_track_id)
                    run, candidates = _master_version(
                        catalog,
                        source_track_id,
                        version_id,
                    )
                    candidate = (
                        _preferred_master_artifact(candidates)
                        if ordinal is None
                        else next(
                            (artifact for artifact in candidates if artifact.ordinal == ordinal),
                            None,
                        )
                    )
                    if candidate is None:
                        raise ValueError(
                            f"version {run.run_id!r} has no selected master deliverable"
                        )
                    if candidate.state is not LocationState.AVAILABLE:
                        raise CatalogConflictError(
                            "the selected master deliverable is not available for export"
                        )
                    source_path = Path(candidate.path).resolve()
                    observed = _assert_artifact_bytes(candidate, source_path)
                    source_payload = _track_payload(catalog, source)
                    base_name = _sanitize_filename_component(
                        _source_display_name(source_payload),
                        maximum=120,
                        fallback=source.track_id[-12:],
                    )
                    extension = source_path.suffix if source_path.suffix else ".audio"
                    export_path = destination / f"{base_name}{sanitized_suffix}{extension}"
                    _copy_file_exclusive(source_path, export_path)
                    copied = _assert_fingerprint(
                        export_path,
                        sha256=observed.sha256,
                        size_bytes=observed.size_bytes,
                    )
                    result.update(
                        {
                            "ok": True,
                            "artifact_ordinal": candidate.ordinal,
                            "source_path": str(source_path),
                            "destination_path": str(export_path),
                            "sha256": copied.sha256,
                            "size_bytes": copied.size_bytes,
                        }
                    )
                    self.logger.info(
                        "Exported master source_track_id=%s version_id=%s ordinal=%s "
                        "source=%s destination=%s sha256=%s",
                        source_track_id,
                        version_id,
                        candidate.ordinal,
                        source_path,
                        export_path,
                        copied.sha256,
                    )
                except Exception as exc:  # noqa: BLE001 - per-item batch report
                    self.logger.exception(
                        "Master batch export item failed index=%s selection=%r",
                        index,
                        raw_item,
                    )
                    result["error"] = _exception_payload(exc)
                results.append(result)
        return {
            "destination_directory": str(destination),
            "suffix": suffix,
            "sanitized_suffix": sanitized_suffix,
            "results": results,
            "success_count": sum(bool(item["ok"]) for item in results),
            "failure_count": sum(not bool(item["ok"]) for item in results),
        }

    def discard_master_version(
        self,
        source_track_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        """Quarantine one completed portal master version without deleting evidence."""

        transaction_id = f"discard-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
        quarantine_root = (
            self.layout.root
            / "trash"
            / "master-versions"
            / _sanitize_filename_component(version_id, maximum=80, fallback=uuid.uuid4().hex)
            / transaction_id
        ).resolve()
        if not quarantine_root.is_relative_to(self.layout.root):
            raise PermissionError("master quarantine directory escapes the private workspace")

        moved: list[tuple[Path, Path]] = []
        tombstone_path = quarantine_root / "tombstone.json"
        self.logger.info(
            "Beginning recoverable master-version discard source_track_id=%s "
            "version_id=%s quarantine=%s",
            source_track_id,
            version_id,
            quarantine_root,
        )
        with CatalogStore(self.layout.catalog_database) as catalog:
            source = catalog.get_track(source_track_id)
            run, candidates = _master_version(catalog, source_track_id, version_id)
            _assert_portal_owned_master_version(
                catalog,
                self.layout,
                source,
                run,
                candidates,
            )
            if any(item.state is not LocationState.AVAILABLE for item in candidates):
                raise CatalogConflictError(
                    "master version is not wholly available; refresh the library before discarding"
                )
            source_paths = _catalog_source_paths(catalog)
            other_active_paths = _other_active_master_paths(catalog, run.run_id)
            planned: list[dict[str, Any]] = []
            seen_paths: set[Path] = set()
            for artifact in candidates:
                source_path = Path(artifact.path).resolve()
                if source_path in seen_paths:
                    raise CatalogConflictError(
                        "master version indexes one physical output more than once"
                    )
                seen_paths.add(source_path)
                if source_path in source_paths:
                    raise PermissionError(
                        "refusing to quarantine a path cataloged as target/reference source audio"
                    )
                if source_path in other_active_paths:
                    raise CatalogConflictError(
                        "refusing to quarantine an output path shared by another active version"
                    )
                observed = _assert_artifact_bytes(artifact, source_path)
                quarantine_name = _sanitize_filename_component(
                    source_path.name,
                    maximum=160,
                    fallback="master",
                )
                quarantine_path = quarantine_root / (f"{artifact.ordinal:03d}-{quarantine_name}")
                planned.append(
                    {
                        "ordinal": artifact.ordinal,
                        "artifact_id": artifact.artifact_id,
                        "track_id": artifact.track_id,
                        "original_path": str(source_path),
                        "quarantine_path": str(quarantine_path),
                        "sha256": observed.sha256,
                        "size_bytes": observed.size_bytes,
                        "media_type": artifact.media_type,
                        "mode": artifact.metadata.get("mode"),
                    }
                )

            preserved = [
                _artifact_payload(item)
                for item in catalog.list_run_artifacts(run.run_id)
                if item not in candidates
            ]
            quarantine_root.mkdir(parents=True, exist_ok=False)
            try:
                for entry in planned:
                    original = Path(cast(str, entry["original_path"]))
                    quarantined = Path(cast(str, entry["quarantine_path"]))
                    _move_file_exclusive(original, quarantined)
                    moved.append((original, quarantined))
                    self.logger.info(
                        "Quarantined master artifact version_id=%s ordinal=%s "
                        "original=%s quarantine=%s sha256=%s",
                        version_id,
                        entry["ordinal"],
                        original,
                        quarantined,
                        entry["sha256"],
                    )

                discarded_at = _utc_now()
                tombstone = {
                    "kind": MASTER_DISCARD_DOCUMENT_KIND,
                    "schema_version": MASTER_LIBRARY_SCHEMA_VERSION,
                    "transaction_id": transaction_id,
                    "source_track_id": source_track_id,
                    "version_id": version_id,
                    "discarded_at": discarded_at,
                    "catalog_id": catalog.catalog_id,
                    "catalog_database": str(catalog.path),
                    "manifest_path": run.manifest_path,
                    "moved": planned,
                    "preserved_artifact_count": len(preserved),
                    "recovery": {
                        "endpoint": "/api/master-library/restore",
                        "payload": {
                            "source_track_id": source_track_id,
                            "version_id": version_id,
                        },
                    },
                }
                _write_text_file(
                    tombstone_path,
                    json.dumps(
                        tombstone,
                        indent=2,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n",
                    overwrite=False,
                )
                with catalog.transaction():
                    for artifact, entry in zip(candidates, planned, strict=True):
                        catalog.transition_run_artifact(
                            run.run_id,
                            role=CatalogRole.GENERATED_OUTPUT,
                            ordinal=artifact.ordinal,
                            expected_path=cast(str, entry["original_path"]),
                            expected_state=LocationState.AVAILABLE,
                            new_path=cast(str, entry["quarantine_path"]),
                            new_state=LocationState.ARCHIVED,
                        )
                        if artifact.track_id is None:
                            raise CatalogFormatError(
                                "master deliverable lost its linked generated track"
                            )
                        catalog.transition_location_state(
                            artifact.track_id,
                            cast(str, entry["original_path"]),
                            expected_state=LocationState.AVAILABLE,
                            new_state=LocationState.ARCHIVED,
                        )
                    catalog.index_artifact(
                        run.run_id,
                        tombstone_path,
                        role=CatalogRole.AUDIT,
                        media_type="application/json",
                        metadata={
                            "manifest_role": "master-version-discard-tombstone",
                            "transaction_id": transaction_id,
                            "source_track_id": source_track_id,
                            "recoverable": True,
                        },
                    )
            except Exception:
                rollback_failures = _rollback_moves(moved)
                if not rollback_failures:
                    tombstone_path.unlink(missing_ok=True)
                    _remove_empty_directory_tree(quarantine_root, self.layout.root / "trash")
                else:
                    self.logger.critical(
                        "Master-version discard rollback was incomplete version_id=%s "
                        "failures=%s tombstone=%s",
                        version_id,
                        rollback_failures,
                        tombstone_path,
                    )
                raise

        self.logger.info(
            "Master version discarded recoverably source_track_id=%s version_id=%s "
            "artifact_count=%s tombstone=%s",
            source_track_id,
            version_id,
            len(planned),
            tombstone_path,
        )
        return {
            "source_track_id": source_track_id,
            "version_id": version_id,
            "status": "discarded",
            "discarded_at": discarded_at,
            "tombstone_path": str(tombstone_path),
            "quarantine_directory": str(quarantine_root),
            "moved": planned,
            "preserved": preserved,
            "recovery": {
                "endpoint": "/api/master-library/restore",
                "payload": {
                    "source_track_id": source_track_id,
                    "version_id": version_id,
                },
            },
        }

    def restore_master_version(
        self,
        source_track_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        """Restore a quarantined master version to its exact original paths."""

        restored_moves: list[tuple[Path, Path]] = []
        self.logger.info(
            "Beginning master-version restore source_track_id=%s version_id=%s",
            source_track_id,
            version_id,
        )
        with CatalogStore(self.layout.catalog_database) as catalog:
            catalog.get_track(source_track_id)
            run, candidates = _master_version(catalog, source_track_id, version_id)
            if any(item.state is not LocationState.ARCHIVED for item in candidates):
                raise CatalogConflictError(
                    "master version is not wholly discarded; refresh the library before restoring"
                )
            tombstone_path, tombstone = _matching_discard_tombstone(
                catalog,
                source_track_id,
                run.run_id,
                candidates,
            )
            raw_entries = tombstone.get("moved")
            if not isinstance(raw_entries, list):
                raise CatalogFormatError("master discard tombstone has no moved artifact list")
            entries_by_ordinal: dict[int, Mapping[str, Any]] = {}
            for index, raw_entry in enumerate(raw_entries):
                entry = _mapping(raw_entry, f"discard tombstone.moved[{index}]")
                ordinal = _required_non_negative_integer(
                    entry.get("ordinal"),
                    f"discard tombstone.moved[{index}].ordinal",
                )
                if ordinal in entries_by_ordinal:
                    raise CatalogFormatError("master discard tombstone repeats an artifact ordinal")
                entries_by_ordinal[ordinal] = entry

            planned: list[dict[str, Any]] = []
            for artifact in candidates:
                matched_entry = entries_by_ordinal.get(artifact.ordinal)
                if matched_entry is None:
                    raise CatalogFormatError(
                        "master discard tombstone does not cover every deliverable"
                    )
                quarantine_path = Path(
                    _string(
                        matched_entry.get("quarantine_path"),
                        "discard tombstone quarantine_path",
                    )
                ).resolve()
                original_path = Path(
                    _string(
                        matched_entry.get("original_path"),
                        "discard tombstone original_path",
                    )
                ).resolve()
                if Path(artifact.path).resolve() != quarantine_path:
                    raise CatalogConflictError(
                        "quarantined artifact path no longer matches its tombstone"
                    )
                if original_path.exists():
                    raise FileExistsError(
                        f"restore destination is occupied; nothing was overwritten: {original_path}"
                    )
                _assert_artifact_bytes(artifact, quarantine_path)
                planned.append(
                    {
                        "ordinal": artifact.ordinal,
                        "artifact_id": artifact.artifact_id,
                        "track_id": artifact.track_id,
                        "quarantine_path": str(quarantine_path),
                        "original_path": str(original_path),
                        "sha256": artifact.sha256,
                        "size_bytes": artifact.size_bytes,
                    }
                )

            restore_id = f"restore-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
            audit_path = tombstone_path.parent / f"{restore_id}.json"
            try:
                for entry in planned:
                    quarantined = Path(cast(str, entry["quarantine_path"]))
                    original = Path(cast(str, entry["original_path"]))
                    original.parent.mkdir(parents=True, exist_ok=True)
                    _move_file_exclusive(quarantined, original)
                    restored_moves.append((quarantined, original))
                    self.logger.info(
                        "Restored master artifact version_id=%s ordinal=%s "
                        "quarantine=%s original=%s sha256=%s",
                        version_id,
                        entry["ordinal"],
                        quarantined,
                        original,
                        entry["sha256"],
                    )

                restored_at = _utc_now()
                audit = {
                    "kind": MASTER_RESTORE_DOCUMENT_KIND,
                    "schema_version": MASTER_LIBRARY_SCHEMA_VERSION,
                    "restore_id": restore_id,
                    "source_track_id": source_track_id,
                    "version_id": version_id,
                    "restored_at": restored_at,
                    "discard_tombstone_path": str(tombstone_path),
                    "moved": planned,
                }
                _write_text_file(
                    audit_path,
                    json.dumps(
                        audit,
                        indent=2,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n",
                    overwrite=False,
                )
                with catalog.transaction():
                    for artifact, entry in zip(candidates, planned, strict=True):
                        catalog.transition_run_artifact(
                            run.run_id,
                            role=CatalogRole.GENERATED_OUTPUT,
                            ordinal=artifact.ordinal,
                            expected_path=cast(str, entry["quarantine_path"]),
                            expected_state=LocationState.ARCHIVED,
                            new_path=cast(str, entry["original_path"]),
                            new_state=LocationState.AVAILABLE,
                        )
                        if artifact.track_id is None:
                            raise CatalogFormatError(
                                "master deliverable lost its linked generated track"
                            )
                        catalog.transition_location_state(
                            artifact.track_id,
                            cast(str, entry["original_path"]),
                            expected_state=LocationState.ARCHIVED,
                            new_state=LocationState.AVAILABLE,
                        )
                    catalog.index_artifact(
                        run.run_id,
                        audit_path,
                        role=CatalogRole.AUDIT,
                        media_type="application/json",
                        metadata={
                            "manifest_role": "master-version-restore-audit",
                            "restore_id": restore_id,
                            "source_track_id": source_track_id,
                            "discard_tombstone_path": str(tombstone_path),
                        },
                    )
            except Exception:
                rollback_failures = _rollback_moves(restored_moves)
                if not rollback_failures:
                    audit_path.unlink(missing_ok=True)
                else:
                    self.logger.critical(
                        "Master-version restore rollback was incomplete version_id=%s "
                        "failures=%s audit=%s",
                        version_id,
                        rollback_failures,
                        audit_path,
                    )
                raise

        self.logger.info(
            "Master version restored source_track_id=%s version_id=%s artifact_count=%s audit=%s",
            source_track_id,
            version_id,
            len(planned),
            audit_path,
        )
        return {
            "source_track_id": source_track_id,
            "version_id": version_id,
            "status": "available",
            "restored_at": restored_at,
            "tombstone_path": str(tombstone_path),
            "audit_path": str(audit_path),
            "moved": planned,
            "recovery": None,
        }

    def add_tracks(
        self,
        paths: Sequence[str],
        *,
        roles: Sequence[str],
        labels: Mapping[str, str | None] | None = None,
    ) -> dict[str, Any]:
        if not paths:
            raise ValueError("select at least one audio file")
        selected_roles = tuple(CatalogRole(role) for role in roles)
        if not selected_roles:
            raise ValueError("select at least one catalog role")
        if any(role not in {CatalogRole.TARGET, CatalogRole.REFERENCE} for role in selected_roles):
            raise ValueError("audio can be added only as a target or reference")
        label_by_path = dict(labels or {})
        results: list[dict[str, Any]] = []
        with CatalogStore(self.layout.catalog_database) as catalog:
            for raw_path in paths:
                path = Path(raw_path).expanduser().resolve()
                try:
                    track = catalog.add_track(
                        path,
                        roles=selected_roles,
                        label=label_by_path.get(raw_path),
                        audio_facts=_probe_catalog_audio(path),
                    )
                except Exception as exc:  # noqa: BLE001 - per-file batch report
                    self.logger.exception("Could not add catalog track %s", path)
                    results.append(
                        {
                            "path": str(path),
                            "ok": False,
                            "error": _exception_payload(exc),
                        }
                    )
                else:
                    results.append(
                        {
                            "path": str(path),
                            "ok": True,
                            "track": _track_payload(catalog, track),
                        }
                    )
        return {
            "results": results,
            "success_count": sum(bool(item["ok"]) for item in results),
            "failure_count": sum(not bool(item["ok"]) for item in results),
        }

    def verify_tracks(self, track_id: str | None = None) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            locations = catalog.verify(
                track_id=track_id,
                include_archived=True,
            )
        counts: dict[str, int] = {}
        for location in locations:
            counts[location.state.value] = counts.get(location.state.value, 0) + 1
        return {
            "count": len(locations),
            "states": counts,
            "locations": [_location_payload(item) for item in locations],
        }

    def relink_track(
        self,
        track_id: str,
        path: str,
        *,
        archive_location_id: str | None = None,
    ) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            location = catalog.relink(
                track_id,
                Path(path).expanduser().resolve(),
                archive_location_id=archive_location_id,
            )
            return {
                "location": _location_payload(location),
                "track": _track_payload(catalog, catalog.get_track(track_id)),
            }

    def set_track_archived(self, track_id: str, *, archived: bool) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            track = catalog.archive_track(track_id, archived=archived)
            return _track_payload(catalog, track)

    def list_reference_sets(self) -> list[dict[str, Any]]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            return [_reference_set_payload(catalog, item) for item in catalog.list_reference_sets()]

    def create_reference_set(self, name: str) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            return _reference_set_payload(
                catalog,
                catalog.create_reference_set(name),
            )

    def rename_reference_set(self, set_id: str, name: str) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            return _reference_set_payload(
                catalog,
                catalog.rename_reference_set(set_id, name),
            )

    def delete_reference_set(self, set_id: str) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            deleted = catalog.delete_reference_set(set_id)
            return _reference_set_payload(catalog, deleted)

    def replace_reference_set_members(
        self,
        set_id: str,
        members: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if len(members) > MAX_PORTAL_REFERENCES:
            raise ValueError(
                f"a reference set cannot contain more than {MAX_PORTAL_REFERENCES} members"
            )
        parsed: list[tuple[str, float, float]] = []
        for index, member in enumerate(members):
            value = _mapping(member, f"members[{index}]")
            parsed.append(
                (
                    _string(value.get("track_id"), f"members[{index}].track_id"),
                    _non_negative_number(
                        value.get("level_weight"),
                        f"members[{index}].level_weight",
                    ),
                    _non_negative_number(
                        value.get("frequency_weight"),
                        f"members[{index}].frequency_weight",
                    ),
                )
            )
        with CatalogStore(self.layout.catalog_database) as catalog:
            record = catalog.replace_reference_set_members(set_id, parsed)
            return _reference_set_payload(catalog, record)

    def export_catalog(self, destination: str, *, overwrite: bool = False) -> dict[str, Any]:
        path = Path(destination).expanduser().resolve()
        if path.exists() and not overwrite:
            raise FileExistsError(f"export already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with CatalogStore(self.layout.catalog_database) as catalog:
            document = catalog.export_json()
            catalog_id = catalog.catalog_id
            revision = catalog.revision
        _write_text_file(path, document, overwrite=overwrite)
        return {
            "path": str(path),
            "catalog_id": catalog_id,
            "revision": revision,
        }

    def import_catalog(self, source: str) -> dict[str, Any]:
        path = Path(source).expanduser().resolve()
        document = path.read_text(encoding="utf-8")
        raw = json.loads(document)
        if not isinstance(raw, Mapping):
            raise ValueError("import document must be a JSON object")
        with CatalogStore(self.layout.catalog_database) as catalog:
            if raw.get("kind") == "music-mastering-tools/track-selection":
                selection = catalog.import_selection(document)
                kind = "selection"
                selection_id: str | None = selection.selection_id
                selection_payload: dict[str, Any] | None = selection.to_dict()
            else:
                catalog.import_json(document)
                kind = "catalog"
                selection_id = None
                selection_payload = None
            return {
                "path": str(path),
                "kind": kind,
                "selection_id": selection_id,
                "selection": selection_payload,
                "catalog_id": catalog.catalog_id,
                "revision": catalog.revision,
            }

    def export_selection(
        self,
        request: Mapping[str, Any],
        destination: str,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        path = Path(destination).expanduser().resolve()
        if path.exists() and not overwrite:
            raise FileExistsError(f"selection export already exists: {path}")
        target_ids = _target_ids_from_request(request)
        if len(target_ids) != 1:
            raise ValueError(
                "portable selection files contain exactly one target; export each "
                "batch target as its own selection, or export the catalog to retain "
                "all target and reference tracks"
            )
        with CatalogStore(self.layout.catalog_database) as catalog:
            selection = _selection_from_request(catalog, request)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_file(
            path,
            selection.to_json(),
            overwrite=overwrite,
        )
        return {
            "path": str(path),
            "selection_id": selection.selection_id,
            "reference_count": len(selection.references),
        }

    def list_runs(self) -> list[dict[str, Any]]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            return [
                {
                    **_run_payload(run),
                    "artifact_count": len(catalog.list_run_artifacts(run.run_id)),
                }
                for run in reversed(catalog.list_runs())
            ]

    def list_run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            return [_artifact_payload(item) for item in catalog.list_run_artifacts(run_id)]

    def load_run_manifest(self, run_id: str) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            run = catalog.get_run(run_id)
        if run.manifest_path is None:
            raise FileNotFoundError(f"run {run_id!r} has no manifest path")
        return RunManifest.load(run.manifest_path).to_dict()

    def recover_run(
        self,
        manifest_path: str,
        *,
        selection_path: str | None = None,
        configuration_path: str | None = None,
    ) -> dict[str, Any]:
        with CatalogStore(self.layout.catalog_database) as catalog:
            recovered = recover_catalog_run(
                catalog,
                manifest_path,
                selection_path=selection_path,
                configuration_path=configuration_path,
            )
            return {
                "run_id": recovered.manifest.run_id,
                "status": recovered.manifest.status.value,
                "selection_id": recovered.selection.selection_id,
                "manifest_path": str(recovered.manifest_path),
                "artifact_count": len(catalog.list_run_artifacts(recovered.manifest.run_id)),
            }

    def validate_job_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = _mapping(request, "job")
        target_ids = _target_ids_from_request(payload)
        if len(target_ids) == 1:
            prepared = self._prepare_target_job(
                payload,
                target_ids[0],
                purpose="validate",
            )
            report = validate_job(prepared.job)
            return {
                "prepared": prepared.to_dict(),
                "report": report.to_dict(),
                "job": prepared.job.to_dict(),
                "selection": prepared.selection.to_dict(),
            }

        targets: list[dict[str, Any]] = []
        for index, target_id in enumerate(target_ids):
            try:
                prepared = self._prepare_target_job(
                    payload,
                    target_id,
                    purpose="validate",
                )
                report = validate_job(prepared.job)
            except Exception as exc:  # noqa: BLE001 - complete per-target report
                targets.append(
                    {
                        "index": index,
                        "target_id": target_id,
                        "state": "invalid",
                        "ok": False,
                        "error": _exception_payload(exc),
                    }
                )
                continue
            targets.append(
                {
                    "index": index,
                    "target_id": target_id,
                    "state": "valid" if report.ok else "invalid",
                    "ok": report.ok,
                    "prepared": prepared.to_dict(),
                    "report": report.to_dict(),
                    "job": prepared.job.to_dict(),
                    "selection": prepared.selection.to_dict(),
                }
            )
        valid_count = sum(bool(item["ok"]) for item in targets)
        return {
            "kind": "batch-validation",
            "batch": {
                "target_count": len(target_ids),
                "valid_count": valid_count,
                "invalid_count": len(target_ids) - valid_count,
                "ok": valid_count == len(target_ids),
            },
            "targets": targets,
        }

    def start_job(
        self,
        request: Mapping[str, Any],
        *,
        dry_run: bool,
    ) -> OperationRecord:
        frozen_request = _json_ready(dict(request))
        if not isinstance(frozen_request, dict):
            raise TypeError("job request must be a JSON object")
        kind = "dry-run" if dry_run else "render"
        return self.operations.start(
            kind,
            lambda sink: self._run_job(
                cast(dict[str, Any], frozen_request),
                sink,
                dry_run=dry_run,
            ),
        )

    def prepare_job(
        self,
        request: Mapping[str, Any],
        *,
        purpose: str,
    ) -> PreparedJob:
        if purpose not in {"validate", "dry-run", "render"}:
            raise ValueError("purpose must be validate, dry-run, or render")
        payload = _mapping(request, "job")
        target_ids = _target_ids_from_request(payload)
        if len(target_ids) != 1:
            raise ValueError(
                "prepare_job accepts exactly one target; use validate_job_request "
                "or start_job for a target batch"
            )
        return self._prepare_target_job(payload, target_ids[0], purpose=purpose)

    def _prepare_target_job(
        self,
        request: Mapping[str, Any],
        target_id: str,
        *,
        purpose: str,
    ) -> PreparedJob:
        materialize = purpose != "validate"
        run_id = _new_run_id(purpose)
        run_directory = self.layout.runs / run_id

        with CatalogStore(self.layout.catalog_database) as catalog:
            selection = _selection_for_target(catalog, request, target_id)

        output_root = self._output_root_from_request(request)
        target_slug = _target_slug(selection)
        output_directory = output_root / f"{target_slug}-master-{run_id}"
        outputs = _outputs_from_request(request, output_directory, target_slug=target_slug)
        settings = _mapping(request.get("settings", {}), "job.settings")
        audio = {
            **cast(dict[str, Any], _json_ready(asdict(AudioConfig()))),
            **dict(_mapping(settings.get("audio", {}), "job.settings.audio")),
            "temp_directory": str(self.layout.temporary / run_id),
        }
        matching = {
            **cast(dict[str, Any], _json_ready(asdict(MatchingConfig()))),
            **dict(_mapping(settings.get("matching", {}), "job.settings.matching")),
        }
        limiter = {
            **cast(dict[str, Any], _json_ready(asdict(LimiterConfig()))),
            **dict(_mapping(settings.get("limiter", {}), "job.settings.limiter")),
        }
        detection = {
            **cast(dict[str, Any], _json_ready(asdict(DetectionConfig()))),
            **dict(_mapping(settings.get("detection", {}), "job.settings.detection")),
        }
        edge_cases = {
            **cast(dict[str, Any], _json_ready(asdict(EdgeCasePolicy()))),
            **dict(_mapping(settings.get("edge_cases", {}), "job.settings.edge_cases")),
        }
        if edge_cases["existing_output"] != "error":
            raise ValueError(
                "job.settings.edge_cases.existing_output must be 'error'; "
                "portal jobs never overwrite an existing output"
            )
        preview = _preview_from_request(
            request,
            output_directory,
            target_slug=target_slug,
        )
        engine = EngineKind.UPSTREAM if len(selection.references) == 1 else EngineKind.NATIVE
        references = [
            {
                "path": item.track.path,
                "level_weight": (1.0 if len(selection.references) == 1 else item.level_weight),
                "frequency_weight": (
                    1.0 if len(selection.references) == 1 else item.frequency_weight
                ),
                "label": item.track.label,
            }
            for item in selection.references
        ]
        manifest_path = run_directory / "manifest.json"
        event_log_path = run_directory / "events.jsonl"
        job = JobConfig.from_dict(
            {
                "schema_version": 1,
                "target": selection.target.path,
                "references": references,
                "outputs": [_json_ready(asdict(item)) for item in outputs],
                "audio": audio,
                "matching": matching,
                "limiter": limiter,
                "preview": preview,
                "detection": detection,
                "edge_cases": edge_cases,
                "execution": {
                    "engine": engine.value,
                    "job_id": run_id,
                    "manifest_path": str(manifest_path),
                    "event_log_path": str(event_log_path),
                    "max_workers": 1,
                    "dry_run": purpose == "dry-run",
                },
                "notes": _optional_string(request.get("notes"), "job.notes"),
            }
        )
        configuration_path = self.layout.jobs / f"{run_id}.json"
        selection_path = self.layout.jobs / f"{run_id}.selection.json"
        prepared = PreparedJob(
            job=job,
            selection=selection,
            configuration_path=configuration_path,
            selection_path=selection_path,
            manifest_path=manifest_path,
            event_log_path=event_log_path,
            output_root=output_root,
            output_directory=output_directory,
            materialized=materialize,
        )
        if materialize:
            output_directory_created = False
            run_directory_created = False
            configuration_created = False
            selection_created = False
            try:
                output_directory.mkdir(parents=True, exist_ok=False)
                output_directory_created = True
                run_directory.mkdir(parents=True, exist_ok=False)
                run_directory_created = True
                _write_text_file(
                    configuration_path,
                    job.to_json(),
                    overwrite=False,
                )
                configuration_created = True
                _write_text_file(
                    selection_path,
                    selection.to_json(),
                    overwrite=False,
                )
                selection_created = True
            except Exception:
                for cleanup_error in _cleanup_materialization_failure(
                    prepared,
                    output_directory_created=output_directory_created,
                    run_directory_created=run_directory_created,
                    configuration_created=configuration_created,
                    selection_created=selection_created,
                ):
                    self.logger.warning(
                        "Could not fully remove an uncommitted portal scaffold: %s",
                        cleanup_error,
                    )
                raise
        return prepared

    def _output_root_from_request(self, request: Mapping[str, Any]) -> Path:
        requested = request.get("output_directory")
        if requested is None:
            requested = self.get_preferences()["default_output_directory"]
        return _absolute_directory_path(requested, "job.output_directory")

    def _run_job(
        self,
        request: Mapping[str, Any],
        sink: EventSink,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        payload = _mapping(request, "job")
        target_ids = _target_ids_from_request(payload)
        purpose = "dry-run" if dry_run else "render"
        if len(target_ids) == 1:
            prepared = self._prepare_target_job(
                payload,
                target_ids[0],
                purpose=purpose,
            )
            return self._execute_prepared_job(prepared, sink, dry_run=dry_run)

        targets: list[dict[str, Any]] = []
        for index, target_id in enumerate(target_ids):
            sink.emit(
                Event.create(
                    "MMT-I-PORTAL-BATCH-TARGET-START",
                    f"Starting batch target {index + 1} of {len(target_ids)}.",
                    stage="batch",
                    target_id=target_id,
                    target_index=index,
                    target_count=len(target_ids),
                )
            )
            batch_prepared: PreparedJob | None = None
            try:
                batch_prepared = self._prepare_target_job(
                    payload,
                    target_id,
                    purpose=purpose,
                )
                result = self._execute_prepared_job(
                    batch_prepared,
                    sink,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - independent targets continue
                self.logger.exception(
                    "Batch %s target %s failed; continuing with remaining targets",
                    purpose,
                    target_id,
                )
                item: dict[str, Any] = {
                    "index": index,
                    "target_id": target_id,
                    "state": "failed",
                    "error": _exception_payload(exc),
                }
                if batch_prepared is not None:
                    item["run_id"] = batch_prepared.job.execution.job_id
                    item["prepared"] = batch_prepared.to_dict()
                    if batch_prepared.manifest_path.is_file():
                        try:
                            item["manifest"] = RunManifest.load(
                                batch_prepared.manifest_path
                            ).to_dict()
                        except Exception as manifest_exc:  # noqa: BLE001 - summary aid
                            item["manifest_error"] = _exception_payload(manifest_exc)
                targets.append(item)
                sink.emit(
                    Event.create(
                        "MMT-E-PORTAL-BATCH-TARGET-FAILED",
                        f"Batch target {index + 1} failed; processing will continue.",
                        level="error",
                        stage="batch",
                        target_id=target_id,
                        target_index=index,
                        error=_exception_payload(exc),
                    )
                )
                continue
            targets.append(
                {
                    "index": index,
                    "target_id": target_id,
                    "state": "succeeded",
                    "run_id": batch_prepared.job.execution.job_id,
                    **result,
                }
            )
            sink.emit(
                Event.create(
                    "MMT-I-PORTAL-BATCH-TARGET-COMPLETE",
                    f"Completed batch target {index + 1} of {len(target_ids)}.",
                    stage="batch",
                    target_id=target_id,
                    target_index=index,
                    target_count=len(target_ids),
                )
            )

        success_count = sum(item["state"] == "succeeded" for item in targets)
        failure_count = len(targets) - success_count
        status = (
            "succeeded"
            if failure_count == 0
            else "failed"
            if success_count == 0
            else "partial_failure"
        )
        return {
            "kind": f"batch-{purpose}",
            "batch": {
                "status": status,
                "target_count": len(targets),
                "success_count": success_count,
                "failure_count": failure_count,
                "failure_policy": "continue-independent-targets",
                "failure_policy_rationale": (
                    "Each target has an independent run and output set, so one "
                    "failure does not prevent usable masters for the remaining targets."
                ),
            },
            "targets": targets,
        }

    def _execute_prepared_job(
        self,
        prepared: PreparedJob,
        sink: EventSink,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        report = validate_job(prepared.job)

        service_failure: Exception | None = None
        outcome = None
        try:
            outcome = self._service_factory().run(
                prepared.job,
                manifest_path=prepared.manifest_path,
                event_log_path=prepared.event_log_path,
                configuration_path=prepared.configuration_path,
                sink=sink,
                dry_run=dry_run,
                command=(
                    "mmt",
                    "portal",
                    "dry-run" if dry_run else "render",
                    str(prepared.configuration_path),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - finalize retained evidence
            service_failure = exc

        finalization_failure: Exception | None = None
        if prepared.manifest_path.is_file():
            try:
                with CatalogStore(self.layout.catalog_database) as catalog:
                    finalize_catalog_run(
                        catalog,
                        prepared.selection,
                        prepared.manifest_path,
                        configuration_path=prepared.configuration_path,
                        selection_path=prepared.selection_path,
                    )
            except Exception as exc:  # noqa: BLE001 - preserve both causes
                finalization_failure = exc
        else:
            for cleanup_error in _cleanup_uncommitted_prepared_job(prepared):
                self.logger.warning(
                    "Could not fully remove an uncommitted portal scaffold: %s",
                    cleanup_error,
                )

        if service_failure is not None:
            if finalization_failure is not None:
                raise CatalogError(
                    "mastering failed and its preserved manifest could not be indexed: "
                    f"{finalization_failure}"
                ) from service_failure
            raise service_failure
        if finalization_failure is not None:
            raise CatalogError(
                "mastering completed, but catalog finalization failed; use Recover Run: "
                f"{finalization_failure}"
            ) from finalization_failure
        if outcome is None or not prepared.manifest_path.is_file():
            raise RuntimeError("mastering returned without an authoritative manifest")

        return {
            "kind": "dry-run" if dry_run else "render",
            "prepared": prepared.to_dict(),
            "validation": report.to_dict(),
            "manifest": RunManifest.load(prepared.manifest_path).to_dict(),
        }


def _write_text_file(
    path: Path,
    content: str,
    *,
    overwrite: bool,
) -> None:
    """Durably publish text, atomically replacing an approved destination."""

    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        temporary_handle = os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        )
        descriptor = -1
        with temporary_handle as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _cleanup_uncommitted_prepared_job(prepared: PreparedJob) -> tuple[str, ...]:
    """Remove only known, run-scoped files when no manifest could be created."""

    errors: list[str] = []
    files = {
        prepared.configuration_path,
        prepared.selection_path,
        prepared.manifest_path,
        prepared.event_log_path,
        *(Path(item.path) for item in prepared.job.outputs),
        *(
            Path(path)
            for path in (
                prepared.job.preview.target_path,
                prepared.job.preview.result_path,
            )
            if path is not None
        ),
    }
    for path in files:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    directories = {
        prepared.manifest_path.parent,
        *(Path(item.path).parent for item in prepared.job.outputs),
        Path(prepared.job.audio.temp_directory)
        if prepared.job.audio.temp_directory is not None
        else prepared.manifest_path.parent,
    }
    for directory in directories:
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"{directory}: {exc}")
    return tuple(errors)


def _cleanup_materialization_failure(
    prepared: PreparedJob,
    *,
    output_directory_created: bool,
    run_directory_created: bool,
    configuration_created: bool,
    selection_created: bool,
) -> tuple[str, ...]:
    """Remove only paths proven to have been created by this preparation call."""

    errors: list[str] = []
    files = (
        (prepared.selection_path, selection_created),
        (prepared.configuration_path, configuration_created),
    )
    for path, owned in files:
        if not owned:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    directories = (
        (prepared.manifest_path.parent, run_directory_created),
        (prepared.output_directory, output_directory_created),
    )
    for directory, owned in directories:
        if not owned:
            continue
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"{directory}: {exc}")
    return tuple(errors)


def _selection_from_request(
    catalog: CatalogStore,
    request: Mapping[str, Any],
) -> CatalogSelection:
    target_ids = _target_ids_from_request(request)
    if len(target_ids) != 1:
        raise ValueError("a portable selection requires exactly one target")
    return _selection_for_target(catalog, request, target_ids[0])


def _selection_for_target(
    catalog: CatalogStore,
    request: Mapping[str, Any],
    target_id: str,
) -> CatalogSelection:
    raw_references = request.get("references")
    if (
        not isinstance(raw_references, Sequence)
        or isinstance(raw_references, (str, bytes))
        or not raw_references
    ):
        raise ValueError("job.references must contain at least one reference")
    if len(raw_references) > MAX_PORTAL_REFERENCES:
        raise ValueError(f"job.references cannot contain more than {MAX_PORTAL_REFERENCES} entries")
    reference_ids: list[str] = []
    weights: list[tuple[float, float]] = []
    for index, raw_reference in enumerate(raw_references):
        reference = _mapping(raw_reference, f"job.references[{index}]")
        reference_ids.append(
            _string(
                reference.get("track_id"),
                f"job.references[{index}].track_id",
            )
        )
        weights.append(
            (
                _non_negative_number(
                    reference.get("level_weight", 1.0),
                    f"job.references[{index}].level_weight",
                ),
                _non_negative_number(
                    reference.get("frequency_weight", 1.0),
                    f"job.references[{index}].frequency_weight",
                ),
            )
        )
    selection = catalog.build_selection(
        target_id,
        reference_ids=reference_ids,
    )
    if len(reference_ids) == 1:
        return selection
    return selection_with_weights(selection, weights)


def _target_ids_from_request(request: Mapping[str, Any]) -> tuple[str, ...]:
    raw_target_ids = request.get("target_ids")
    if raw_target_ids is None:
        return (_string(request.get("target_id"), "job.target_id"),)
    if not isinstance(raw_target_ids, Sequence) or isinstance(
        raw_target_ids,
        (str, bytes),
    ):
        raise ValueError("job.target_ids must be a JSON array")
    if not raw_target_ids:
        raise ValueError("job.target_ids must contain at least one target")
    if len(raw_target_ids) > MAX_PORTAL_TARGETS:
        raise ValueError(f"job.target_ids cannot contain more than {MAX_PORTAL_TARGETS} entries")
    target_ids = tuple(
        _string(value, f"job.target_ids[{index}]") for index, value in enumerate(raw_target_ids)
    )
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("job.target_ids cannot contain duplicate target content")
    legacy_target = request.get("target_id")
    if legacy_target is not None and (_string(legacy_target, "job.target_id") != target_ids[0]):
        raise ValueError(
            "when target_id and target_ids are both supplied, target_id must "
            "match the first target_ids entry"
        )
    return target_ids


def _outputs_from_request(
    request: Mapping[str, Any],
    output_directory: Path,
    *,
    target_slug: str,
) -> tuple[OutputSpec, ...]:
    raw = _mapping(request.get("outputs", {}), "job.outputs")
    outputs: list[OutputSpec] = []
    if _boolean(raw.get("limited", True), "job.outputs.limited"):
        outputs.append(
            OutputSpec(
                str(output_directory / f"{target_slug}-mastered-limited.wav"),
                subtype=_string(
                    raw.get("limited_subtype", "PCM_24"),
                    "job.outputs.limited_subtype",
                ),
                mode=OutputMode.LIMITED,
                label="limited distribution master",
            )
        )
    if _boolean(raw.get("normalized", False), "job.outputs.normalized"):
        outputs.append(
            OutputSpec(
                str(output_directory / f"{target_slug}-mastered-normalized.wav"),
                subtype=_string(
                    raw.get("normalized_subtype", "PCM_24"),
                    "job.outputs.normalized_subtype",
                ),
                mode=OutputMode.NORMALIZED,
                label="normalized master without limiter",
            )
        )
    if _boolean(raw.get("raw", False), "job.outputs.raw"):
        outputs.append(
            OutputSpec(
                str(output_directory / f"{target_slug}-mastered-raw.wav"),
                subtype=_string(
                    raw.get("raw_subtype", "FLOAT"),
                    "job.outputs.raw_subtype",
                ),
                mode=OutputMode.RAW_FLOAT,
                label="raw floating-point DAW handoff",
            )
        )
    if not outputs:
        raise ValueError("select at least one output")
    return tuple(outputs)


def _preview_from_request(
    request: Mapping[str, Any],
    output_directory: Path,
    *,
    target_slug: str,
) -> dict[str, Any]:
    defaults = cast(dict[str, Any], _json_ready(asdict(PreviewConfig())))
    raw = dict(_mapping(request.get("preview", {}), "job.preview"))
    enabled = _boolean(raw.get("enabled", defaults["enabled"]), "job.preview.enabled")
    value = {**defaults, **raw, "enabled": enabled}
    value["target_path"] = (
        str(output_directory / f"{target_slug}-preview-target.wav") if enabled else None
    )
    value["result_path"] = (
        str(output_directory / f"{target_slug}-preview-result.wav") if enabled else None
    )
    return value


def _target_slug(selection: CatalogSelection) -> str:
    raw = selection.target.label or Path(selection.target.path).stem
    rendered = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in raw.strip()
    )
    while "--" in rendered:
        rendered = rendered.replace("--", "-")
    rendered = rendered.strip(" .-_")[:40].rstrip(" .-_")
    if not rendered:
        rendered = "track"
    reserved = {
        "aux",
        "clock$",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if rendered.casefold() in reserved:
        rendered = f"track-{rendered}"
    return rendered


def _absolute_directory_path(value: object, name: str) -> Path:
    raw = _string(value, name)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{name} points to a file, not a directory: {resolved}")
    return resolved


def _dialog_location_group(mode: str, purpose: str) -> str:
    """Keep persisted histories limited to the supported native picker purposes."""

    if mode in {"open-audio", "open-audio-many"} and purpose == "audio":
        return purpose
    if mode == "choose-folder" and purpose == "folder":
        return purpose
    if mode in {"open-json", "save-json"} and purpose in {
        "catalog",
        "selection",
        "job-configuration",
        "run-manifest",
    }:
        return purpose
    raise ValueError(f"unsupported dialog mode/purpose combination: {mode!r}, {purpose!r}")


def _default_preferences(layout: WorkspaceLayout) -> dict[str, Any]:
    return {
        "kind": PORTAL_PREFERENCES_KIND,
        "schema_version": PORTAL_PREFERENCES_SCHEMA_VERSION,
        "default_output_directory": str(layout.outputs),
    }


def _parse_preferences(value: object) -> dict[str, Any]:
    preferences = _mapping(value, "portal preferences")
    if not all(isinstance(key, str) for key in preferences):
        raise ValueError("portal preferences field names must be strings")
    expected = {
        "kind",
        "schema_version",
        "default_output_directory",
    }
    actual = set(preferences)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected fields: {', '.join(unexpected)}")
        raise ValueError(f"portal preferences fields are invalid ({'; '.join(details)})")
    kind = _string(preferences.get("kind"), "portal preferences.kind")
    if kind != PORTAL_PREFERENCES_KIND:
        raise ValueError(f"unsupported portal preferences kind: {kind!r}")
    schema_version = preferences.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != PORTAL_PREFERENCES_SCHEMA_VERSION
    ):
        raise ValueError(f"unsupported portal preferences schema_version: {schema_version!r}")
    output_directory = _absolute_directory_path(
        preferences.get("default_output_directory"),
        "portal preferences.default_output_directory",
    )
    return {
        "kind": PORTAL_PREFERENCES_KIND,
        "schema_version": PORTAL_PREFERENCES_SCHEMA_VERSION,
        "default_output_directory": str(output_directory),
    }


def _unique_preferences_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate portal preferences JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _probe_catalog_audio(path: Path) -> CatalogAudioFacts:
    facts = DefaultAudioProbe().probe(path)
    return CatalogAudioFacts(
        sample_rate=facts.sample_rate,
        channels=facts.channels,
        frames=facts.frames,
        duration_seconds=facts.duration_seconds,
        format=facts.format,
        subtype=facts.subtype,
    )


def _workspace_payload(layout: WorkspaceLayout) -> dict[str, str]:
    return {
        "root": str(layout.root),
        "preferences": str(layout.root / PORTAL_PREFERENCES_FILENAME),
        "catalog_database": str(layout.catalog_database),
        "jobs": str(layout.jobs),
        "runs": str(layout.runs),
        "outputs": str(layout.outputs),
        "temporary": str(layout.temporary),
        "exports": str(layout.exports),
        "logs": str(layout.logs),
    }


def _track_payload(catalog: CatalogStore, track: TrackRecord) -> dict[str, Any]:
    locations = catalog.list_locations(track.track_id)
    preferred = next(
        (item.path for item in locations if item.state is LocationState.AVAILABLE),
        None,
    )
    return {
        "track_id": track.track_id,
        "sha256": track.sha256,
        "size_bytes": track.size_bytes,
        "roles": [role.value for role in track.roles],
        "label": track.label,
        "audio_facts": (track.audio_facts.to_dict() if track.audio_facts is not None else None),
        "archived": track.archived,
        "created_at": track.created_at,
        "updated_at": track.updated_at,
        "preferred_path": preferred,
        "locations": [_location_payload(item) for item in locations],
    }


def _location_payload(location: LocationRecord) -> dict[str, Any]:
    return {
        "location_id": location.location_id,
        "track_id": location.track_id,
        "path": location.path,
        "state": location.state.value,
        "modified_ns": location.modified_ns,
        "last_verified_at": location.last_verified_at,
    }


def _reference_set_payload(
    catalog: CatalogStore,
    reference_set: ReferenceSetRecord,
) -> dict[str, Any]:
    total_level = math.fsum(item.level_weight for item in reference_set.members)
    total_frequency = math.fsum(item.frequency_weight for item in reference_set.members)
    members: list[dict[str, Any]] = []
    for member in reference_set.members:
        track = catalog.get_track(member.track_id)
        locations = catalog.list_locations(member.track_id)
        path = next(
            (item.path for item in locations if item.state is LocationState.AVAILABLE),
            None,
        )
        members.append(
            {
                "track_id": member.track_id,
                "ordinal": member.ordinal,
                "level_weight": member.level_weight,
                "frequency_weight": member.frequency_weight,
                "normalized_level_weight": (
                    member.level_weight / total_level if total_level > 0 else 0.0
                ),
                "normalized_frequency_weight": (
                    member.frequency_weight / total_frequency if total_frequency > 0 else 0.0
                ),
                "label": track.label,
                "path": path,
                "archived": track.archived,
            }
        )
    return {
        "set_id": reference_set.set_id,
        "name": reference_set.name,
        "members": members,
        "created_at": reference_set.created_at,
        "updated_at": reference_set.updated_at,
    }


def _run_payload(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "selection_id": run.selection_id,
        "manifest_path": run.manifest_path,
        "manifest_sha256": run.manifest_sha256,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _artifact_payload(artifact: RunArtifactRecord) -> dict[str, Any]:
    return {
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
        "metadata": _json_ready(artifact.metadata),
    }


def _mastered_output_artifacts(
    catalog: CatalogStore,
    run: RunRecord,
) -> tuple[RunArtifactRecord, ...]:
    """Return only true mastered audio deliverables for a completed run."""

    if run.status != "completed":
        return ()
    values: list[RunArtifactRecord] = []
    for artifact in catalog.list_run_artifacts(run.run_id):
        if (
            artifact.role is not CatalogRole.GENERATED_OUTPUT
            or artifact.metadata.get("manifest_role") != "mastered-output"
            or artifact.media_type is None
            or not artifact.media_type.startswith("audio/")
            or artifact.track_id is None
        ):
            continue
        generated = catalog.get_track(artifact.track_id)
        if CatalogRole.GENERATED_OUTPUT not in generated.roles:
            continue
        values.append(artifact)
    return tuple(sorted(values, key=lambda item: item.ordinal))


def _master_version(
    catalog: CatalogStore,
    source_track_id: str,
    version_id: str,
) -> tuple[RunRecord, tuple[RunArtifactRecord, ...]]:
    source = catalog.get_track(source_track_id)
    if CatalogRole.TARGET not in source.roles or CatalogRole.GENERATED_OUTPUT in source.roles:
        raise ValueError("source_track_id must identify an original target track")
    run = catalog.get_run(version_id)
    if run.status != "completed":
        raise CatalogConflictError("only completed runs are mastered versions")
    selection = catalog.get_run_selection(run.run_id)
    if selection is None or selection.target.track_id != source_track_id:
        raise PermissionError(
            "the requested master version is not owned by the requested source track"
        )
    candidates = _mastered_output_artifacts(catalog, run)
    if not candidates:
        raise CatalogError("completed run has no linked mastered audio deliverables")
    return run, candidates


def _master_version_payload(
    catalog: CatalogStore,
    source: TrackRecord,
    run: RunRecord,
) -> dict[str, Any]:
    candidates = _mastered_output_artifacts(catalog, run)
    preferred = _preferred_master_artifact(candidates)
    if preferred is not None:
        library_status = "available"
    elif all(item.state is LocationState.ARCHIVED for item in candidates):
        library_status = "discarded"
    else:
        library_status = "unavailable"

    deliverables: list[dict[str, Any]] = []
    for artifact in candidates:
        path = Path(artifact.path)
        metadata = _json_ready(artifact.metadata)
        if not isinstance(metadata, Mapping):
            raise CatalogFormatError("master artifact metadata lost its object shape")
        mode = metadata.get("mode")
        engine_mode = mode if isinstance(mode, str) else None
        mode_family = _master_mode_family(engine_mode)
        label = metadata.get("label")
        deliverables.append(
            {
                "run_id": run.run_id,
                "ordinal": artifact.ordinal,
                "artifact_id": artifact.artifact_id,
                "track_id": artifact.track_id,
                "path": artifact.path,
                "media_type": artifact.media_type,
                "state": artifact.state.value,
                "manifest_role": "mastered-output",
                "mode": mode_family,
                "engine_mode": engine_mode,
                "label": (
                    label
                    if isinstance(label, str) and label.strip()
                    else str(engine_mode).replace("-", " ").title()
                    if engine_mode
                    else f"Master output {artifact.ordinal + 1}"
                ),
                "playable": (
                    artifact.state is LocationState.AVAILABLE
                    and artifact.track_id is not None
                    and path.is_file()
                ),
                "preferred": (preferred is not None and artifact.ordinal == preferred.ordinal),
            }
        )
    preferred_payload = next(
        (item for item in deliverables if item["preferred"]),
        None,
    )
    source_name = source.label
    if source_name is None:
        try:
            source_name = Path(catalog.preferred_location(source.track_id).path).stem
        except CatalogError:
            source_name = source.track_id[-12:]
    recovery: dict[str, Any] | None = None
    can_restore = False
    if library_status == "discarded":
        try:
            tombstone_path, _ = _matching_discard_tombstone(
                catalog,
                source.track_id,
                run.run_id,
                candidates,
            )
        except (CatalogError, CatalogFormatError, FileNotFoundError, ValueError):
            tombstone_path = None
        if tombstone_path is not None and all(Path(item.path).is_file() for item in candidates):
            can_restore = True
            recovery = {
                "endpoint": "/api/master-library/restore",
                "payload": {
                    "source_track_id": source.track_id,
                    "version_id": run.run_id,
                },
                "tombstone_path": str(tombstone_path),
            }
    return {
        "version_id": run.run_id,
        "run_id": run.run_id,
        "display_label": f"{source_name} · {run.created_at[:10]} · {run.run_id[-8:]}",
        "library_status": library_status,
        "run_status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "deliverables": deliverables,
        "preferred_audition": preferred_payload,
        "can_discard": (
            library_status == "available" and all(Path(item.path).is_file() for item in candidates)
        ),
        "can_restore": can_restore,
        "recovery": recovery,
    }


def _preferred_master_artifact(
    artifacts: Sequence[RunArtifactRecord],
) -> RunArtifactRecord | None:
    available = [
        artifact
        for artifact in artifacts
        if artifact.state is LocationState.AVAILABLE and Path(artifact.path).is_file()
    ]
    if not available:
        return None
    priority = {
        "limited": 0,
        "normalized": 1,
        "raw": 2,
    }
    return min(
        available,
        key=lambda artifact: (
            priority.get(
                _master_mode_family(
                    artifact.metadata.get("mode")
                    if isinstance(artifact.metadata.get("mode"), str)
                    else None
                ),
                99,
            ),
            artifact.ordinal,
        ),
    )


def _master_mode_family(value: str | None) -> str:
    normalized = (value or "").casefold()
    for family in ("limited", "normalized", "raw"):
        if normalized == family or normalized.startswith(f"{family}-"):
            return family
    return normalized or "other"


def _assert_portal_owned_master_version(
    catalog: CatalogStore,
    layout: WorkspaceLayout,
    source: TrackRecord,
    run: RunRecord,
    artifacts: Sequence[RunArtifactRecord],
) -> None:
    if run.status != "completed" or run.manifest_path is None:
        raise PermissionError("only completed, manifest-backed portal runs can be discarded")
    manifest_path = Path(run.manifest_path).resolve()
    if not manifest_path.is_relative_to(layout.runs) or not manifest_path.is_file():
        raise PermissionError("run manifest is not owned by the private portal runs directory")
    manifest = RunManifest.load(manifest_path)
    if manifest.run_id != run.run_id or manifest.status.value != "completed":
        raise CatalogConflictError("run manifest identity/status no longer matches the catalog")
    extension = manifest.extensions.get("catalog")
    if not isinstance(extension, Mapping) or extension.get("catalog_id") != catalog.catalog_id:
        raise PermissionError("run manifest is not owned by the active private catalog")
    selection = catalog.get_run_selection(run.run_id)
    if selection is None or selection.target.track_id != source.track_id:
        raise PermissionError("run selection does not belong to the requested source track")
    job_configs = [
        item
        for item in catalog.list_run_artifacts(run.run_id)
        if item.metadata.get("manifest_role") == "job-config"
        and Path(item.path).resolve().is_relative_to(layout.jobs)
    ]
    if len(job_configs) != 1:
        raise PermissionError("run lacks exactly one portal-owned job configuration")

    manifest_outputs = {
        (
            Path(item.path).resolve(),
            item.fingerprint.sha256 if item.fingerprint is not None else None,
            item.fingerprint.size_bytes if item.fingerprint is not None else None,
        )
        for item in manifest.outputs
        if item.role == "mastered-output"
        and item.media_type is not None
        and item.media_type.startswith("audio/")
    }
    for artifact in artifacts:
        if artifact.track_id is None:
            raise PermissionError("master output is not linked to generated catalog content")
        generated = catalog.get_track(artifact.track_id)
        if CatalogRole.GENERATED_OUTPUT not in generated.roles:
            raise PermissionError("master output track lacks the generated-output role")
        identity = (
            Path(artifact.path).resolve(),
            artifact.sha256,
            artifact.size_bytes,
        )
        if identity not in manifest_outputs:
            raise CatalogConflictError(
                "master output no longer matches the authoritative run manifest"
            )


def _catalog_source_paths(catalog: CatalogStore) -> set[Path]:
    paths: set[Path] = set()
    for track in catalog.list_tracks(include_archived=True):
        if not {CatalogRole.TARGET, CatalogRole.REFERENCE}.intersection(track.roles):
            continue
        paths.update(
            Path(location.path).resolve() for location in catalog.list_locations(track.track_id)
        )
    return paths


def _other_active_master_paths(catalog: CatalogStore, selected_run_id: str) -> set[Path]:
    paths: set[Path] = set()
    for run in catalog.list_runs():
        if run.run_id == selected_run_id or run.status != "completed":
            continue
        paths.update(
            Path(artifact.path).resolve()
            for artifact in _mastered_output_artifacts(catalog, run)
            if artifact.state is LocationState.AVAILABLE
        )
    return paths


def _matching_discard_tombstone(
    catalog: CatalogStore,
    source_track_id: str,
    version_id: str,
    candidates: Sequence[RunArtifactRecord],
) -> tuple[Path, Mapping[str, Any]]:
    expected = {(item.ordinal, str(Path(item.path).resolve())) for item in candidates}
    errors: list[str] = []
    tombstones = [
        artifact
        for artifact in reversed(catalog.list_run_artifacts(version_id))
        if artifact.role is CatalogRole.AUDIT
        and artifact.state is LocationState.AVAILABLE
        and artifact.metadata.get("manifest_role") == "master-version-discard-tombstone"
    ]
    for artifact in tombstones:
        path = Path(artifact.path).resolve()
        try:
            _assert_artifact_bytes(artifact, path)
            raw = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            value = _mapping(raw, "master discard tombstone")
            if (
                value.get("kind") != MASTER_DISCARD_DOCUMENT_KIND
                or value.get("schema_version") != MASTER_LIBRARY_SCHEMA_VERSION
                or value.get("source_track_id") != source_track_id
                or value.get("version_id") != version_id
            ):
                continue
            raw_moved = value.get("moved")
            if not isinstance(raw_moved, list):
                raise CatalogFormatError("discard tombstone moved field is not an array")
            observed: set[tuple[int, str]] = set()
            for index, raw_entry in enumerate(raw_moved):
                entry = _mapping(raw_entry, f"discard tombstone.moved[{index}]")
                observed.add(
                    (
                        _required_non_negative_integer(
                            entry.get("ordinal"),
                            f"discard tombstone.moved[{index}].ordinal",
                        ),
                        str(
                            Path(
                                _string(
                                    entry.get("quarantine_path"),
                                    f"discard tombstone.moved[{index}].quarantine_path",
                                )
                            ).resolve()
                        ),
                    )
                )
            if observed == expected:
                return path, value
        except (OSError, TypeError, ValueError, CatalogError) as exc:
            errors.append(f"{path}: {exc}")
    detail = f" ({'; '.join(errors)})" if errors else ""
    raise CatalogError("no valid discard tombstone matches the quarantined master version" + detail)


def _assert_artifact_bytes(
    artifact: RunArtifactRecord,
    path: Path,
) -> Any:
    return _assert_fingerprint(
        path,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
    )


def _assert_fingerprint(
    path: Path,
    *,
    sha256: str,
    size_bytes: int,
) -> Any:
    observed = fingerprint_file(path)
    if observed.sha256 != sha256 or observed.size_bytes != size_bytes:
        raise CatalogConflictError(f"artifact bytes changed since cataloging: {path}")
    return observed


def _source_display_name(source: Mapping[str, Any]) -> str:
    label = source.get("label")
    if isinstance(label, str) and label.strip():
        return label
    path = source.get("preferred_path")
    if isinstance(path, str) and path:
        return Path(path).stem
    track_id = source.get("track_id")
    return str(track_id)[-12:] if track_id is not None else "untitled"


def _validated_display_label(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("label must be a string or null")
    if value != value.strip():
        raise ValueError("label cannot begin or end with whitespace")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized:
        raise ValueError("label cannot be empty; use null to clear it")
    if len(normalized) > MAX_DISPLAY_LABEL_CHARACTERS:
        raise ValueError(f"label cannot exceed {MAX_DISPLAY_LABEL_CHARACTERS} characters")
    if any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in normalized
    ):
        raise ValueError("label cannot contain control or line-separator characters")
    return normalized


def _sanitize_filename_component(
    value: str,
    *,
    maximum: int,
    fallback: str,
) -> str:
    if maximum <= 0:
        raise ValueError("filename component maximum must be positive")
    normalized = unicodedata.normalize("NFC", value)
    forbidden = '<>:"/\\|?*'
    rendered = "".join(
        "-"
        if character in forbidden or unicodedata.category(character).startswith("C")
        else character
        for character in normalized
    )
    rendered = " ".join(rendered.split()).strip(" .")
    rendered = rendered[:maximum].rstrip(" .")
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if rendered.split(".", 1)[0].casefold() in reserved:
        rendered = f"_{rendered}"
    return rendered or fallback


def _copy_file_exclusive(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"export already exists; nothing was overwritten: {destination}")
    created = False
    try:
        with destination.open("xb") as writer:
            created = True
            with source.open("rb") as reader:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    except FileExistsError as exc:
        raise FileExistsError(
            f"export already exists; nothing was overwritten: {destination}"
        ) from exc
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _move_file_exclusive(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"master artifact is not a file: {source}")
    if destination.exists():
        raise FileExistsError(f"destination already exists; nothing was overwritten: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(source, destination)
        return
    except OSError:
        pass
    try:
        _copy_file_exclusive(source, destination)
        source.unlink()
    except Exception:
        if source.exists():
            destination.unlink(missing_ok=True)
        raise


def _rollback_moves(moves: Sequence[tuple[Path, Path]]) -> list[str]:
    failures: list[str] = []
    for original, moved_to in reversed(moves):
        try:
            if original.exists():
                raise FileExistsError(f"rollback destination is occupied: {original}")
            _move_file_exclusive(moved_to, original)
        except Exception as exc:  # noqa: BLE001 - retain every rollback failure
            failures.append(f"{moved_to} -> {original}: {exc}")
    return failures


def _remove_empty_directory_tree(path: Path, boundary: Path) -> None:
    selected = path.resolve()
    stop = boundary.resolve()
    if not selected.is_relative_to(stop):
        raise PermissionError("cleanup path escapes the selected trash boundary")
    while selected != stop:
        try:
            selected.rmdir()
        except OSError:
            break
        selected = selected.parent


def _check_exact_fields(
    value: Mapping[str, Any],
    allowed: set[str],
    required: set[str],
    name: str,
) -> None:
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        details: list[str] = []
        if unknown:
            details.append(f"unknown fields: {', '.join(sorted(unknown))}")
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        raise ValueError(f"{name} fields are invalid ({'; '.join(details)})")


def _optional_non_negative_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _required_non_negative_integer(value, name)


def _required_non_negative_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _limitations() -> list[dict[str, str]]:
    return [
        {
            "control": "match amount",
            "status": "represented-not-runnable",
            "detail": "Current engines require a full amount of 1.0.",
        },
        {
            "control": "EBU R128 loudness",
            "status": "represented-not-runnable",
            "detail": "Current engines use Matchering RMS.",
        },
        {
            "control": "true peak and oversampling",
            "status": "represented-not-runnable",
            "detail": "Current limiter support is sample-peak Hyrax.",
        },
        {
            "control": "dither and metadata copying",
            "status": "represented-not-runnable",
            "detail": "Current outputs require no dither and metadata drop.",
        },
        {
            "control": "external limiter",
            "status": "represented-not-runnable",
            "detail": "External command execution is disabled.",
        },
        {
            "control": "parallel jobs and cancellation",
            "status": "planned",
            "detail": (
                "The portal serializes operations and batch targets; it does not "
                "claim parallel processing or cancellation."
            ),
        },
    ]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return cast(Mapping[str, Any], value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _non_negative_number(value: object, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def _json_ready(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


def _exception_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MusicMasteringError):
        return exc.to_dict()
    return {
        "exception_type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
        "code": None,
        "details": {},
        "retryable": False,
    }


def _new_run_id(purpose: str) -> str:
    normalized = "".join(
        character for character in purpose.casefold() if character.isalnum() or character == "-"
    ).strip("-")
    if not normalized:
        normalized = "job"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{normalized}-{uuid.uuid4().hex[:10]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "MAX_PORTAL_REFERENCES",
    "MAX_PORTAL_TARGETS",
    "OperationEventSink",
    "OperationRecord",
    "PORTAL_PREFERENCES_KIND",
    "PORTAL_PREFERENCES_SCHEMA_VERSION",
    "PortalApplication",
    "PortalBusyError",
    "PortalOperationManager",
    "PreparedJob",
]
