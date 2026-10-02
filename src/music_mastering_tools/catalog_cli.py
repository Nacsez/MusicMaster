"""Command-line adapter for the private content-addressed catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .audio_probe import AudioProbeUnavailable, DefaultAudioProbe
from .catalog import (
    AudioFacts,
    CatalogRole,
    CatalogStore,
    LocationRecord,
    ReferenceSetRecord,
    RunArtifactRecord,
    RunRecord,
    TrackRecord,
    default_catalog_path,
)
from .catalog_integration import recover_catalog_run


def add_catalog_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``mmt catalog`` command tree."""

    catalog = commands.add_parser(
        "catalog",
        help="manage the private track, reference-set, run, and artifact catalog",
    )
    catalog.add_argument(
        "--database",
        type=Path,
        help="catalog database (default: .mmt/catalog/catalog.sqlite3)",
    )
    actions = catalog.add_subparsers(dest="catalog_command", required=True)

    actions.add_parser("init", help="create or inspect the catalog database")

    add = actions.add_parser("add", help="fingerprint and add one or more audio tracks")
    add.add_argument("paths", nargs="+", type=Path)
    add.add_argument(
        "--role",
        choices=("target", "reference", "both"),
        required=True,
    )
    add.add_argument("--label", help="label (valid only when adding one path)")
    add.add_argument("--json", action="store_true")

    list_tracks = actions.add_parser("list", help="list cataloged tracks")
    list_tracks.add_argument(
        "--role",
        choices=(CatalogRole.TARGET.value, CatalogRole.REFERENCE.value),
    )
    list_tracks.add_argument("--include-archived", action="store_true")
    list_tracks.add_argument("--json", action="store_true")

    show = actions.add_parser("show", help="show one track and every known location")
    show.add_argument("track_id")
    show.add_argument("--json", action="store_true")

    verify = actions.add_parser("verify", help="rehash known locations and report their state")
    verify.add_argument("track_id", nargs="?")
    verify.add_argument("--include-archived", action="store_true")
    verify.add_argument("--json", action="store_true")

    relink = actions.add_parser(
        "relink",
        help="add a replacement location only when its bytes match the selected track",
    )
    relink.add_argument("track_id")
    relink.add_argument("path", type=Path)

    archive = actions.add_parser("archive", help="archive a track without deleting audio")
    archive.add_argument("track_id")
    restore = actions.add_parser("restore", help="restore an archived track")
    restore.add_argument("track_id")

    export = actions.add_parser("export", help="export a strict, reimportable catalog document")
    export.add_argument("path", type=Path)
    export.add_argument("--force", action="store_true")

    import_ = actions.add_parser(
        "import",
        help="import a full catalog document or a reimportable track selection",
    )
    import_.add_argument("path", type=Path)

    sets = actions.add_parser("set", help="manage named weighted reference sets")
    set_actions = sets.add_subparsers(dest="catalog_set_command", required=True)
    set_create = set_actions.add_parser("create", help="create an empty named reference set")
    set_create.add_argument("name")
    set_list = set_actions.add_parser("list", help="list reference sets")
    set_list.add_argument("--json", action="store_true")
    set_show = set_actions.add_parser("show", help="show one reference set")
    set_show.add_argument("set_id")
    set_show.add_argument("--json", action="store_true")
    set_add = set_actions.add_parser("add", help="add or update one weighted set member")
    set_add.add_argument("set_id")
    set_add.add_argument("track_id")
    set_add.add_argument("--level-weight", type=float, default=1.0)
    set_add.add_argument("--frequency-weight", type=float, default=1.0)
    set_add.add_argument("--ordinal", type=int)
    set_remove = set_actions.add_parser("remove", help="remove one member from a set")
    set_remove.add_argument("set_id")
    set_remove.add_argument("track_id")

    selection = actions.add_parser(
        "selection",
        help="export a target/reference selection for later re-import",
    )
    selection.add_argument("--target", required=True, dest="target_id")
    selected = selection.add_mutually_exclusive_group(required=True)
    selected.add_argument("--reference", action="append", dest="reference_ids")
    selected.add_argument("--reference-set", dest="reference_set_id")
    selection.add_argument("--output", required=True, type=Path)
    selection.add_argument("--force", action="store_true")

    recover = actions.add_parser(
        "recover-run",
        help="idempotently finalize and index a preserved run manifest",
    )
    recover.add_argument("manifest", type=Path)
    recover.add_argument(
        "--selection",
        type=Path,
        help="selection JSON (auto-detected from an augmented CLI/workbench manifest)",
    )
    recover.add_argument(
        "--configuration",
        type=Path,
        help="job configuration (auto-detected when already listed by the manifest)",
    )

    runs = actions.add_parser("runs", help="list indexed mastering runs")
    runs.add_argument("--json", action="store_true")
    artifacts = actions.add_parser("artifacts", help="list every artifact indexed for a run")
    artifacts.add_argument("run_id")
    artifacts.add_argument("--json", action="store_true")


def run_catalog_command(args: argparse.Namespace) -> int:
    """Execute a parsed catalog action and write stable operator output."""

    database = (
        args.database.resolve() if args.database is not None else default_catalog_path(Path.cwd())
    )
    with CatalogStore(database) as catalog:
        command = args.catalog_command
        if command == "init":
            _write_json(
                {
                    "catalog_id": catalog.catalog_id,
                    "database": str(database),
                    "revision": catalog.revision,
                    "schema_version": 1,
                }
            )
            return 0
        if command == "add":
            if args.label is not None and len(args.paths) != 1:
                raise ValueError("--label can be used only when adding one path")
            roles = _selected_roles(args.role)
            added_tracks = [
                catalog.add_track(
                    path,
                    roles=roles,
                    label=args.label,
                    audio_facts=_probe_audio(path),
                )
                for path in args.paths
            ]
            _write_records(
                [_track_dict(catalog, track) for track in added_tracks],
                json_output=args.json,
                empty_message="No tracks were added.",
            )
            return 0
        if command == "list":
            listed_tracks = catalog.list_tracks(
                role=args.role,
                include_archived=args.include_archived,
            )
            _write_records(
                [_track_dict(catalog, track) for track in listed_tracks],
                json_output=args.json,
                empty_message="Catalog contains no matching tracks.",
            )
            return 0
        if command == "show":
            value = _track_dict(catalog, catalog.get_track(args.track_id))
            if args.json:
                _write_json(value)
            else:
                _write_track_text(value)
            return 0
        if command == "verify":
            locations = catalog.verify(
                args.track_id,
                include_archived=args.include_archived,
            )
            _write_records(
                [_location_dict(item) for item in locations],
                json_output=args.json,
                empty_message="No catalog locations matched.",
            )
            return 0
        if command == "relink":
            location = catalog.relink(args.track_id, args.path)
            _write_json(_location_dict(location))
            return 0
        if command == "archive":
            _write_json(_track_dict(catalog, catalog.archive_track(args.track_id)))
            return 0
        if command == "restore":
            _write_json(
                _track_dict(
                    catalog,
                    catalog.archive_track(args.track_id, archived=False),
                )
            )
            return 0
        if command == "export":
            _write_owned_file(args.path, catalog.export_json(), force=args.force)
            sys.stdout.write(f"Catalog exported: {args.path.resolve()}\n")
            return 0
        if command == "import":
            document = args.path.read_text(encoding="utf-8")
            kind = _document_kind(document)
            if kind == "music-mastering-tools/track-selection":
                imported = catalog.import_selection(document)
                _write_json(imported.to_dict())
            else:
                catalog.import_json(document)
                _write_json(
                    {
                        "catalog_id": catalog.catalog_id,
                        "database": str(database),
                        "revision": catalog.revision,
                    }
                )
            return 0
        if command == "set":
            return _run_set_command(catalog, args)
        if command == "selection":
            selection = catalog.build_selection(
                args.target_id,
                reference_ids=args.reference_ids,
                reference_set_id=args.reference_set_id,
            )
            _write_owned_file(args.output, selection.to_json(), force=args.force)
            sys.stdout.write(
                f"Selection {selection.selection_id} exported: {args.output.resolve()}\n"
            )
            return 0
        if command == "recover-run":
            return _recover_run(catalog, args)
        if command == "runs":
            _write_records(
                [_run_dict(item) for item in catalog.list_runs()],
                json_output=args.json,
                empty_message="No runs are indexed.",
            )
            return 0
        if command == "artifacts":
            _write_records(
                [_artifact_dict(item) for item in catalog.list_run_artifacts(args.run_id)],
                json_output=args.json,
                empty_message="The run has no indexed artifacts.",
            )
            return 0
    raise AssertionError(f"unhandled catalog command: {args.catalog_command}")


def _recover_run(catalog: CatalogStore, args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    recovered = recover_catalog_run(
        catalog,
        manifest_path,
        selection_path=args.selection,
        configuration_path=args.configuration,
    )
    finalized = recovered.manifest
    _write_json(
        {
            "artifact_count": len(catalog.list_run_artifacts(finalized.run_id)),
            "catalog_id": catalog.catalog_id,
            "manifest": str(manifest_path),
            "run_id": finalized.run_id,
            "selection_id": recovered.selection.selection_id,
            "status": finalized.status.value,
        }
    )
    return 0


def _run_set_command(catalog: CatalogStore, args: argparse.Namespace) -> int:
    command = args.catalog_set_command
    value: ReferenceSetRecord | None = None
    if command == "create":
        value = catalog.create_reference_set(args.name)
    elif command == "list":
        values = [_set_dict(item) for item in catalog.list_reference_sets()]
        _write_records(
            values,
            json_output=args.json,
            empty_message="No reference sets are defined.",
        )
        return 0
    elif command == "show":
        value = catalog.get_reference_set(args.set_id)
    elif command == "add":
        value = catalog.set_reference_member(
            args.set_id,
            args.track_id,
            level_weight=args.level_weight,
            frequency_weight=args.frequency_weight,
            ordinal=args.ordinal,
        )
    elif command == "remove":
        value = catalog.remove_reference_member(args.set_id, args.track_id)
    else:
        raise AssertionError(f"unhandled catalog set command: {command}")
    rendered = _set_dict(value)
    if getattr(args, "json", False):
        _write_json(rendered)
    else:
        sys.stdout.write(_set_text(rendered))
    return 0


def _probe_audio(path: Path) -> AudioFacts:
    try:
        facts = DefaultAudioProbe().probe(path)
    except AudioProbeUnavailable as exc:
        raise ValueError(f"could not add non-audio or unreadable track '{path}': {exc}") from exc
    return AudioFacts(
        sample_rate=facts.sample_rate,
        channels=facts.channels,
        frames=facts.frames,
        duration_seconds=facts.duration_seconds,
        format=facts.format,
        subtype=facts.subtype,
    )


def _selected_roles(value: str) -> tuple[CatalogRole, ...]:
    if value == "both":
        return (CatalogRole.TARGET, CatalogRole.REFERENCE)
    return (CatalogRole(value),)


def _track_dict(catalog: CatalogStore, track: TrackRecord) -> dict[str, Any]:
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
        "locations": [_location_dict(item) for item in catalog.list_locations(track.track_id)],
    }


def _location_dict(location: LocationRecord) -> dict[str, Any]:
    return {
        "location_id": location.location_id,
        "track_id": location.track_id,
        "path": location.path,
        "state": location.state.value,
        "modified_ns": location.modified_ns,
        "last_verified_at": location.last_verified_at,
    }


def _set_dict(reference_set: ReferenceSetRecord) -> dict[str, Any]:
    return {
        "set_id": reference_set.set_id,
        "name": reference_set.name,
        "created_at": reference_set.created_at,
        "updated_at": reference_set.updated_at,
        "members": [
            {
                "track_id": item.track_id,
                "ordinal": item.ordinal,
                "level_weight": item.level_weight,
                "frequency_weight": item.frequency_weight,
            }
            for item in reference_set.members
        ],
    }


def _run_dict(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "selection_id": run.selection_id,
        "manifest_path": run.manifest_path,
        "manifest_sha256": run.manifest_sha256,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _artifact_dict(artifact: RunArtifactRecord) -> dict[str, Any]:
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
        "metadata": dict(artifact.metadata),
    }


def _write_records(
    values: list[dict[str, Any]],
    *,
    json_output: bool,
    empty_message: str,
) -> None:
    if json_output:
        _write_json({"items": values, "count": len(values)})
        return
    if not values:
        sys.stdout.write(empty_message + "\n")
        return
    for value in values:
        if "track_id" in value and "locations" in value:
            _write_track_text(value)
        elif "set_id" in value:
            sys.stdout.write(_set_text(value))
        else:
            sys.stdout.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def _write_track_text(value: dict[str, Any]) -> None:
    roles = ", ".join(value["roles"])
    label = value["label"] or Path(value["locations"][0]["path"]).stem
    sys.stdout.write(
        f"{value['track_id']} [{roles}] {label}\n"
        f"  sha256: {value['sha256']}\n"
        f"  archived: {str(value['archived']).lower()}\n"
    )
    for location in value["locations"]:
        sys.stdout.write(f"  {location['state']}: {location['path']}\n")


def _set_text(value: dict[str, Any]) -> str:
    lines = [f"{value['set_id']} {value['name']} ({len(value['members'])} reference(s))"]
    lines.extend(
        "  "
        f"{item['ordinal']}: {item['track_id']} "
        f"level={item['level_weight']} frequency={item['frequency_weight']}"
        for item in value["members"]
    )
    return "\n".join(lines) + "\n"


def _write_json(value: Any) -> None:
    sys.stdout.write(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )


def _write_owned_file(path: Path, content: str, *, force: bool) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _document_kind(document: str) -> str | None:
    try:
        value = json.loads(document)
    except json.JSONDecodeError:
        return None
    return value.get("kind") if isinstance(value, dict) else None


__all__ = ["add_catalog_parser", "run_catalog_command"]
