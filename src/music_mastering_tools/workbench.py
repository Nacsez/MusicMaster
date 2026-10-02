"""Interactive private terminal workbench.

The controller deliberately depends on the same catalog, configuration, and
service APIs as non-interactive callers. A later graphical adapter can reuse
those boundaries without creating a second mastering behavior.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .audio_probe import AudioProbeUnavailable, DefaultAudioProbe
from .catalog import AudioFacts as CatalogAudioFacts
from .catalog import (
    CatalogError,
    CatalogRole,
    CatalogSelection,
    CatalogStore,
    LocationState,
    ReferenceSetRecord,
    TrackRecord,
)
from .catalog_integration import finalize_catalog_run, selection_with_weights
from .config import (
    AudioConfig,
    EngineKind,
    ExecutionConfig,
    JobConfig,
    OutputMode,
    OutputSpec,
    PreviewConfig,
    ReferenceSpec,
    save_job_config,
)
from .doctor import run_doctor
from .errors import MusicMasteringError
from .events import ConsoleEventSink, EventLevel
from .service import MasteringService
from .validation import validate_job

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """All mutable private state created by the interactive adapter."""

    root: Path
    catalog_database: Path
    jobs: Path
    runs: Path
    outputs: Path
    temporary: Path
    exports: Path
    logs: Path

    @classmethod
    def create(cls, root: str | Path) -> WorkspaceLayout:
        workspace = Path(root).resolve()
        if workspace.exists() and not workspace.is_dir():
            raise ValueError(f"workbench path is not a directory: {workspace}")
        layout = cls(
            root=workspace,
            catalog_database=workspace / "catalog" / "catalog.sqlite3",
            jobs=workspace / "jobs",
            runs=workspace / "runs",
            outputs=workspace / "outputs",
            temporary=workspace / "tmp",
            exports=workspace / "exports",
            logs=workspace / "logs",
        )
        for directory in (
            layout.root,
            layout.catalog_database.parent,
            layout.jobs,
            layout.runs,
            layout.outputs,
            layout.temporary,
            layout.exports,
            layout.logs,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return layout


class Workbench:
    """Menu-oriented operator workflow with injectable terminal I/O."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        input_function: InputFunction = input,
        output_function: OutputFunction = print,
        service: MasteringService | None = None,
    ) -> None:
        self.layout = WorkspaceLayout.create(workspace)
        self.input = input_function
        self.output = output_function
        self.service = service or MasteringService()

    def run(self) -> int:
        try:
            with CatalogStore(self.layout.catalog_database) as catalog:
                self._welcome(catalog)
                while True:
                    self._line()
                    self.output("1. Create and run a mastering job")
                    self.output("2. Manage the track and artifact catalog")
                    self.output("3. Show recent runs and artifacts")
                    self.output("4. Run diagnostics")
                    self.output("5. Help")
                    self.output("0. Exit")
                    choice = self._ask("Choose: ").casefold()
                    if choice == "0":
                        self.output("Workbench closed. Private files remain in:")
                        self.output(f"  {self.layout.root}")
                        return 0
                    if choice == "1":
                        self._create_job(catalog)
                    elif choice == "2":
                        self._catalog_menu(catalog)
                    elif choice == "3":
                        self._show_runs(catalog)
                    elif choice == "4":
                        self.output(run_doctor().render_text())
                    elif choice == "5":
                        self._help()
                    else:
                        self.output("Enter one of the displayed menu numbers.")
        except (EOFError, KeyboardInterrupt):
            self.output("")
            self.output("Workbench cancelled safely; no audio file was deleted.")
            return 0

    def _welcome(self, catalog: CatalogStore) -> None:
        self._line("=")
        self.output("Music Mastering Tools — Private Workbench")
        self._line("=")
        self.output(f"Workspace: {self.layout.root}")
        self.output(f"Catalog:   {catalog.path}")
        self.output(
            f"Catalog contains {len(catalog.list_tracks(include_archived=True))} track(s), "
            f"{len(catalog.list_reference_sets())} reference set(s), and "
            f"{len(catalog.list_runs())} run(s)."
        )
        self.output("No catalog action copies or deletes source audio.")

    def _catalog_menu(self, catalog: CatalogStore) -> None:
        while True:
            self._line()
            self.output("Catalog")
            self.output("1. List all tracks")
            self.output("2. Add target/input tracks")
            self.output("3. Add reference tracks")
            self.output("4. Add tracks usable as either target or reference")
            self.output("5. Verify known locations")
            self.output("6. List named weighted reference sets")
            self.output("7. Export the complete catalog")
            self.output("8. Import a catalog or track-selection list")
            self.output("9. Archive a catalog track")
            self.output("0. Back")
            choice = self._ask("Choose: ").casefold()
            if choice == "0":
                return
            try:
                if choice == "1":
                    self._list_tracks(catalog)
                elif choice in {"2", "3", "4"}:
                    roles = {
                        "2": (CatalogRole.TARGET,),
                        "3": (CatalogRole.REFERENCE,),
                        "4": (CatalogRole.TARGET, CatalogRole.REFERENCE),
                    }[choice]
                    self._add_tracks(catalog, roles)
                elif choice == "5":
                    self._verify_catalog(catalog)
                elif choice == "6":
                    self._list_reference_sets(catalog)
                elif choice == "7":
                    self._export_catalog(catalog)
                elif choice == "8":
                    self._import_catalog(catalog)
                elif choice == "9":
                    self._archive_track(catalog)
                else:
                    self.output("Enter one of the displayed menu numbers.")
            except (CatalogError, OSError, ValueError) as exc:
                self.output(f"Catalog operation failed: {exc}")

    def _add_tracks(
        self,
        catalog: CatalogStore,
        roles: tuple[CatalogRole, ...],
    ) -> None:
        self.output("Paste or drag an audio file path. Leave blank when finished.")
        while True:
            raw = self._ask("Audio path: ")
            if not raw.strip():
                return
            path = Path(_clean_dropped_path(raw)).expanduser().resolve()
            label = self._ask("Optional catalog label: ").strip() or None
            try:
                track = catalog.add_track(
                    path,
                    roles=roles,
                    label=label,
                    audio_facts=_probe_catalog_audio(path),
                )
            except (AudioProbeUnavailable, CatalogError, OSError, ValueError) as exc:
                self.output(f"Could not add '{path}': {exc}")
                continue
            self.output(
                f"Added {track.track_id} as {', '.join(role.value for role in track.roles)}."
            )

    def _list_tracks(
        self,
        catalog: CatalogStore,
        *,
        role: CatalogRole | None = None,
    ) -> tuple[TrackRecord, ...]:
        tracks = catalog.list_tracks(role=role, include_archived=True)
        if not tracks:
            self.output("No matching tracks are cataloged.")
            return tracks
        for index, track in enumerate(tracks, start=1):
            locations = catalog.list_locations(track.track_id)
            available = next(
                (item.path for item in locations if item.state is LocationState.AVAILABLE),
                "(no available location)",
            )
            facts = (
                ""
                if track.audio_facts is None
                else (
                    f" {track.audio_facts.sample_rate} Hz/"
                    f"{track.audio_facts.channels} ch/"
                    f"{track.audio_facts.duration_seconds:.1f} s"
                )
            )
            label = track.label or Path(available).stem
            flags = " archived" if track.archived else ""
            self.output(
                f"{index:>3}. {label}{facts}{flags}\n     {track.track_id}\n     {available}"
            )
        return tracks

    def _verify_catalog(self, catalog: CatalogStore) -> None:
        locations = catalog.verify(include_archived=True)
        counts: dict[str, int] = {}
        for location in locations:
            counts[location.state.value] = counts.get(location.state.value, 0) + 1
        rendered = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        self.output(f"Verified {len(locations)} location(s): {rendered or 'none'}.")

    def _list_reference_sets(self, catalog: CatalogStore) -> tuple[ReferenceSetRecord, ...]:
        reference_sets = catalog.list_reference_sets()
        if not reference_sets:
            self.output("No named reference sets exist yet.")
            return reference_sets
        for index, reference_set in enumerate(reference_sets, start=1):
            self.output(
                f"{index:>3}. {reference_set.name} "
                f"({len(reference_set.members)} reference(s))\n"
                f"     {reference_set.set_id}"
            )
            for member in reference_set.members:
                self.output(
                    f"       {member.ordinal}: {member.track_id} "
                    f"level={member.level_weight:g}, "
                    f"frequency={member.frequency_weight:g}"
                )
        return reference_sets

    def _export_catalog(self, catalog: CatalogStore) -> None:
        default = self.layout.exports / "catalog.json"
        raw = self._ask(f"Export path [{default}]: ").strip()
        destination = Path(_clean_dropped_path(raw)).resolve() if raw else default
        if destination.exists() and not self._yes("Replace the existing export? [y/N]: "):
            self.output("Export cancelled.")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(catalog.export_json(), encoding="utf-8", newline="\n")
        self.output(f"Reimportable catalog exported: {destination}")

    def _import_catalog(self, catalog: CatalogStore) -> None:
        raw = self._ask("Catalog or selection JSON path: ")
        source = Path(_clean_dropped_path(raw)).resolve()
        document = source.read_text(encoding="utf-8")
        try:
            selection = CatalogSelection.from_json(document)
        except (CatalogError, ValueError):
            catalog.import_json(document)
            self.output(f"Catalog imported from {source}")
        else:
            catalog.import_selection(document)
            self.output(f"Selection {selection.selection_id} and its tracks were imported.")

    def _archive_track(self, catalog: CatalogStore) -> None:
        tracks = self._list_tracks(catalog)
        track = self._choose_track(tracks, "Track number to archive (blank cancels): ")
        if track is None:
            return
        if not self._yes(
            "Archive only the catalog record? Source audio will not be deleted [y/N]: "
        ):
            self.output("Archive cancelled.")
            return
        catalog.archive_track(track.track_id)
        self.output(f"Archived {track.track_id}; historical run links were retained.")

    def _create_job(self, catalog: CatalogStore) -> None:
        targets = self._list_tracks(catalog, role=CatalogRole.TARGET)
        target = self._choose_track(targets, "Target number (blank cancels): ")
        if target is None:
            return
        references = self._list_tracks(catalog, role=CatalogRole.REFERENCE)
        if not references:
            self.output("Add at least one reference track in the Catalog menu first.")
            return

        reference_sets = catalog.list_reference_sets()
        if reference_sets:
            self.output(
                "Enter comma-separated reference track numbers, or "
                "S followed by a named-set number (for example S1)."
            )
        else:
            self.output("Enter one or more comma-separated reference track numbers.")
        raw_selection = self._ask("References (blank cancels): ").strip()
        if not raw_selection:
            return
        try:
            if raw_selection.casefold().startswith("s"):
                set_index = int(raw_selection[1:]) - 1
                if set_index < 0 or set_index >= len(reference_sets):
                    raise IndexError("named reference-set number is outside the displayed list")
                selected_set = reference_sets[set_index]
                selection = catalog.build_selection(
                    target.track_id,
                    reference_set_id=selected_set.set_id,
                )
            else:
                indices = _parse_indices(raw_selection, len(references))
                selection = catalog.build_selection(
                    target.track_id,
                    reference_ids=[references[index].track_id for index in indices],
                )
        except (CatalogError, IndexError, ValueError) as exc:
            self.output(f"Invalid reference selection: {exc}")
            return

        if len(selection.references) > 1 and self._yes(
            "Customize independent level/frequency weights? [y/N]: "
        ):
            weight_values: list[tuple[float, float]] = []
            for reference in selection.references:
                name = reference.track.label or Path(reference.track.path).stem
                level = self._positive_or_zero(f"Level weight for {name} [1]: ", 1.0)
                frequency = self._positive_or_zero(
                    f"Frequency weight for {name} [1]: ",
                    1.0,
                )
                weight_values.append((level, frequency))
            try:
                selection = selection_with_weights(selection, weight_values)
            except ValueError as exc:
                self.output(f"Invalid weight set: {exc}")
                return

        self._show_selection(selection)
        if len(selection.references) > 1 and self._yes(
            "Save this weighted reference set for reuse? [y/N]: "
        ):
            name = self._ask("Reference-set name: ").strip()
            if name:
                try:
                    saved_set = catalog.create_reference_set(name)
                    for reference in selection.references:
                        catalog.set_reference_member(
                            saved_set.set_id,
                            reference.track.track_id,
                            ordinal=reference.ordinal,
                            level_weight=reference.level_weight,
                            frequency_weight=reference.frequency_weight,
                        )
                    self.output(f"Saved reference set: {saved_set.set_id}")
                except CatalogError as exc:
                    self.output(f"Reference set was not saved: {exc}")

        try:
            job, config_path, selection_path = self._materialize_job(selection)
        except (OSError, ValueError) as exc:
            self.output(f"Could not create job files: {exc}")
            return

        report = validate_job(job)
        self.output(
            f"Validation {'PASS' if report.ok else 'FAIL'}: "
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)."
        )
        for issue in report.issues:
            self.output(f"  {issue.severity.value.upper()} {issue.code}: {issue.message}")
        if not report.ok:
            self.output(f"Saved job for correction: {config_path}")
            return

        if self._yes("Run the recommended no-audio dry run now? [Y/n]: ", default=True):
            if not self._execute_job(
                catalog,
                selection,
                replace(
                    job,
                    execution=replace(
                        job.execution,
                        job_id=f"{job.execution.job_id}-dry",
                    ),
                ),
                config_path,
                selection_path,
                dry_run=True,
            ):
                self.output("Real rendering remains blocked until the dry-run issue is fixed.")
                return

        if self._ask("Type RUN to render the mastered audio, or press Enter to stop: ") != "RUN":
            self.output(f"Job saved without rendering: {config_path}")
            return
        self._execute_job(
            catalog,
            selection,
            job,
            config_path,
            selection_path,
            dry_run=False,
        )

    def _materialize_job(
        self,
        selection: CatalogSelection,
    ) -> tuple[JobConfig, Path, Path]:
        run_id = _new_run_id()
        output_directory = self.layout.outputs / run_id
        run_directory = self.layout.runs / run_id
        output_directory.mkdir(parents=True, exist_ok=False)
        run_directory.mkdir(parents=True, exist_ok=False)

        outputs = [
            OutputSpec(
                str(output_directory / "mastered-limited-24.wav"),
                subtype="PCM_24",
                mode=OutputMode.LIMITED,
                label="limited distribution master",
            )
        ]
        if self._yes("Also create normalized/no-limiter PCM24 output? [y/N]: "):
            outputs.append(
                OutputSpec(
                    str(output_directory / "mastered-normalized-24.wav"),
                    subtype="PCM_24",
                    mode=OutputMode.NORMALIZED,
                    label="normalized without limiter",
                )
            )
        if self._yes("Also create raw FLOAT DAW output? [y/N]: "):
            outputs.append(
                OutputSpec(
                    str(output_directory / "mastered-raw-float.wav"),
                    subtype="FLOAT",
                    mode=OutputMode.RAW_FLOAT,
                    label="raw floating-point DAW handoff",
                )
            )
        preview_enabled = self._yes("Create paired target/result previews? [y/N]: ")
        preview = PreviewConfig(
            enabled=preview_enabled,
            target_path=(str(output_directory / "preview-target.wav") if preview_enabled else None),
            result_path=(str(output_directory / "preview-result.wav") if preview_enabled else None),
        )
        engine = EngineKind.UPSTREAM if len(selection.references) == 1 else EngineKind.NATIVE
        references = tuple(
            ReferenceSpec(
                path=item.track.path,
                level_weight=(1.0 if len(selection.references) == 1 else item.level_weight),
                frequency_weight=(1.0 if len(selection.references) == 1 else item.frequency_weight),
                label=item.track.label,
            )
            for item in selection.references
        )
        job = JobConfig(
            target=selection.target.path,
            references=references,
            outputs=tuple(outputs),
            audio=AudioConfig(
                temp_directory=str(self.layout.temporary / run_id),
            ),
            preview=preview,
            execution=ExecutionConfig(
                engine=engine,
                job_id=run_id,
                manifest_path=str(run_directory / "manifest.json"),
                event_log_path=str(run_directory / "events.jsonl"),
            ),
            notes=(
                f"Created by the private workbench from catalog selection {selection.selection_id}."
            ),
        )
        config_path = self.layout.jobs / f"{run_id}.json"
        selection_path = self.layout.jobs / f"{run_id}.selection.json"
        save_job_config(job, config_path)
        selection_path.write_text(selection.to_json(), encoding="utf-8", newline="\n")
        self.output(f"Saved job:      {config_path}")
        self.output(f"Saved selection: {selection_path}")
        return job, config_path, selection_path

    def _execute_job(
        self,
        catalog: CatalogStore,
        selection: CatalogSelection,
        job: JobConfig,
        config_path: Path,
        selection_path: Path,
        *,
        dry_run: bool,
    ) -> bool:
        run_id = job.execution.job_id or uuid.uuid4().hex
        run_directory = self.layout.runs / run_id
        manifest_path = run_directory / "manifest.json"
        event_path = run_directory / "events.jsonl"
        run_directory.mkdir(parents=True, exist_ok=True)
        self.output(f"{'Dry run' if dry_run else 'Render'} started: {run_id}")
        succeeded = False
        try:
            self.service.run(
                job,
                manifest_path=manifest_path,
                event_log_path=event_path,
                configuration_path=config_path,
                sink=ConsoleEventSink(min_level=EventLevel.INFO),
                dry_run=dry_run,
                command=("mmt", "workbench", "run", str(config_path)),
            )
            succeeded = True
        except MusicMasteringError as exc:
            self.output(f"Run failed: {exc}")
        finally:
            if manifest_path.is_file():
                try:
                    finalize_catalog_run(
                        catalog,
                        selection,
                        manifest_path,
                        configuration_path=config_path,
                        selection_path=selection_path,
                    )
                except (CatalogError, MusicMasteringError, OSError, ValueError) as exc:
                    self.output(f"Catalog finalization failed: {exc}")
                    succeeded = False
        self.output(f"Manifest: {manifest_path}")
        self.output(f"Event log: {event_path}")
        if succeeded:
            self.output(
                "Dry run passed." if dry_run else f"Mastering completed: {job.outputs[0].path}"
            )
        return succeeded

    def _show_selection(self, selection: CatalogSelection) -> None:
        self.output(f"Target: {selection.target.label or selection.target.path}")
        profile_kind = (
            "exact Matchering compatibility"
            if len(selection.references) == 1
            else "weighted profile"
        )
        self.output(f"References: {len(selection.references)} ({profile_kind})")
        for reference, level, frequency in zip(
            selection.references,
            selection.normalized_level_weights,
            selection.normalized_frequency_weights,
            strict=True,
        ):
            self.output(
                f"  {reference.ordinal + 1}. "
                f"{reference.track.label or Path(reference.track.path).stem}: "
                f"level={reference.level_weight:g} ({level:.3f}), "
                f"frequency={reference.frequency_weight:g} ({frequency:.3f})"
            )
        self.output(f"Selection ID: {selection.selection_id}")

    def _show_runs(self, catalog: CatalogStore) -> None:
        runs = catalog.list_runs()
        if not runs:
            self.output("No catalog-backed runs have been recorded.")
            return
        for run in reversed(runs[-20:]):
            artifacts = catalog.list_run_artifacts(run.run_id)
            self.output(
                f"{run.run_id} [{run.status}] {len(artifacts)} artifact(s)\n"
                f"  selection: {run.selection_id or '(not catalog-bound)'}\n"
                f"  manifest: {run.manifest_path or '(none)'}"
            )
            for artifact in artifacts:
                self.output(f"    {artifact.role.value}[{artifact.ordinal}]: {artifact.path}")

    def _help(self) -> None:
        self.output(
            "Add targets and references to the private catalog, then create a job. "
            "A single reference uses the exact Matchering 2.0.6 path. Two or more "
            "references use the deterministic weighted loudness/Mid-Side profile "
            "engine. Every accepted run writes a configuration, reimportable "
            "selection, manifest, event log, hashes, and catalog artifact index."
        )
        self.output(
            "Source audio is never copied or deleted. Archive hides a catalog "
            "entry while retaining run history. Exported catalog/selection JSON "
            "contains private paths and hashes; handle it as private data."
        )

    def _choose_track(
        self,
        tracks: Sequence[TrackRecord],
        prompt: str,
    ) -> TrackRecord | None:
        if not tracks:
            return None
        raw = self._ask(prompt).strip()
        if not raw:
            return None
        try:
            index = int(raw) - 1
            return tracks[index] if index >= 0 else None
        except (IndexError, ValueError):
            self.output("That track number is not available.")
            return None

    def _positive_or_zero(self, prompt: str, default: float) -> float:
        while True:
            raw = self._ask(prompt).strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError:
                self.output("Enter a finite non-negative number.")
                continue
            if value < 0 or value != value or value in {float("inf"), float("-inf")}:
                self.output("Enter a finite non-negative number.")
                continue
            return value

    def _yes(self, prompt: str, *, default: bool = False) -> bool:
        raw = self._ask(prompt).strip().casefold()
        if not raw:
            return default
        return raw in {"y", "yes"}

    def _ask(self, prompt: str) -> str:
        return self.input(prompt)

    def _line(self, character: str = "-") -> None:
        self.output(character * 72)


def run_workbench(
    workspace: str | Path,
    *,
    input_function: InputFunction = input,
    output_function: OutputFunction = print,
) -> int:
    """Launch the interactive adapter."""

    return Workbench(
        workspace,
        input_function=input_function,
        output_function=output_function,
    ).run()


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


def _clean_dropped_path(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _parse_indices(value: str, length: int) -> tuple[int, ...]:
    rendered = [item.strip() for item in value.split(",") if item.strip()]
    if not rendered:
        raise ValueError("select at least one reference")
    indices = tuple(int(item) - 1 for item in rendered)
    if any(index < 0 or index >= length for index in indices):
        raise ValueError("a reference number is outside the displayed list")
    if len(indices) != len(set(indices)):
        raise ValueError("a reference number was selected more than once")
    return indices


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:10]}"


__all__ = ["Workbench", "WorkspaceLayout", "run_workbench"]
