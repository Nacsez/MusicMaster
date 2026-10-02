"""Focused behavioral coverage for the private terminal workbench."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import music_mastering_tools.workbench as workbench_module
from music_mastering_tools.catalog import (
    AudioFacts as CatalogAudioFacts,
)
from music_mastering_tools.catalog import (
    CatalogError,
    CatalogRole,
    CatalogSelection,
    CatalogStore,
    TrackRecord,
)
from music_mastering_tools.catalog_integration import selection_with_weights
from music_mastering_tools.config import (
    EngineKind,
    ExecutionConfig,
    JobConfig,
    OutputMode,
    OutputSpec,
    ReferenceSpec,
    load_job_config,
)
from music_mastering_tools.errors import ProcessingError
from music_mastering_tools.validation import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from music_mastering_tools.workbench import (
    Workbench,
    WorkspaceLayout,
    _clean_dropped_path,
    _new_run_id,
    _parse_indices,
    run_workbench,
)
from tests.helpers import write_tone


class ScriptedTerminal:
    """Deterministic terminal adapter that also records prompts and output."""

    def __init__(self, *responses: str | BaseException) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.lines: list[str] = []

    def input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError(f"unexpected input prompt: {prompt}")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def output(self, value: str) -> None:
        self.lines.append(str(value))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class WorkbenchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def make_workbench(
        self,
        *responses: str | BaseException,
        name: str = "workspace",
        service: object | None = None,
    ) -> tuple[Workbench, ScriptedTerminal]:
        terminal = ScriptedTerminal(*responses)
        workbench = Workbench(
            self.root / name,
            input_function=terminal.input,
            output_function=terminal.output,
            service=service,  # type: ignore[arg-type]
        )
        return workbench, terminal

    def populate_catalog(
        self,
        catalog: CatalogStore,
        *,
        references: int = 1,
    ) -> tuple[TrackRecord, tuple[TrackRecord, ...]]:
        facts = CatalogAudioFacts(
            sample_rate=44_100,
            channels=2,
            frames=2_048,
            duration_seconds=2_048 / 44_100,
            format="WAV",
            subtype="PCM_16",
        )
        target = catalog.add_track(
            write_tone(
                self.root / f"{catalog.path.stem}-target.wav",
                frequency=233.0,
                frames=2_048,
            ),
            roles=(CatalogRole.TARGET,),
            label="Target Song",
            audio_facts=facts,
        )
        selected_references = tuple(
            catalog.add_track(
                write_tone(
                    self.root / f"{catalog.path.stem}-reference-{index}.wav",
                    frequency=401.0 + index * 79.0,
                    frames=2_048,
                ),
                roles=(CatalogRole.REFERENCE,),
                label=f"Reference {index + 1}",
                audio_facts=facts,
            )
            for index in range(references)
        )
        return target, selected_references

    def build_selection(
        self,
        catalog: CatalogStore,
        *,
        references: int = 1,
    ) -> CatalogSelection:
        target, selected_references = self.populate_catalog(
            catalog,
            references=references,
        )
        return catalog.build_selection(
            target.track_id,
            reference_ids=[reference.track_id for reference in selected_references],
        )

    def job_for_selection(
        self,
        selection: CatalogSelection,
        *,
        run_id: str = "operator-job",
    ) -> JobConfig:
        return JobConfig(
            target=selection.target.path,
            references=tuple(
                ReferenceSpec(
                    reference.track.path,
                    level_weight=reference.level_weight,
                    frequency_weight=reference.frequency_weight,
                )
                for reference in selection.references
            ),
            outputs=(OutputSpec(str(self.root / f"{run_id}-master.wav")),),
            execution=ExecutionConfig(
                engine=(
                    EngineKind.UPSTREAM if len(selection.references) == 1 else EngineKind.NATIVE
                ),
                job_id=run_id,
            ),
        )


class WorkspaceAndInputTests(WorkbenchTestCase):
    def test_workspace_layout_creates_every_private_directory(self) -> None:
        relative = self.root / "parent" / ".." / "private"
        layout = WorkspaceLayout.create(relative)

        self.assertEqual(layout.root, (self.root / "private").resolve())
        self.assertEqual(
            layout.catalog_database,
            layout.root / "catalog" / "catalog.sqlite3",
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
            self.assertTrue(directory.is_dir(), directory)

        occupied = self.root / "occupied"
        occupied.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a directory"):
            WorkspaceLayout.create(occupied)

    def test_path_index_and_identifier_helpers_cover_edge_inputs(self) -> None:
        self.assertEqual(_clean_dropped_path('  "C:\\Music File.wav"  '), "C:\\Music File.wav")
        self.assertEqual(_clean_dropped_path(" 'track.wav' "), "track.wav")
        self.assertEqual(_clean_dropped_path("'mismatch.wav\""), "'mismatch.wav\"")
        self.assertEqual(_parse_indices(" 1, 3 ", 3), (0, 2))

        for value, message in (
            ("", "at least one"),
            ("0", "outside"),
            ("4", "outside"),
            ("1,1", "more than once"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, message):
                    _parse_indices(value, 3)
        with self.assertRaises(ValueError):
            _parse_indices("not-a-number", 3)

        self.assertRegex(
            _new_run_id(),
            r"^\d{8}T\d{6}Z-[0-9a-f]{10}$",
        )

    def test_input_helpers_reprompt_and_handle_defaults(self) -> None:
        workbench, terminal = self.make_workbench(
            "not-a-number",
            "-1",
            "nan",
            "inf",
            "2.5",
            "",
            "",
            "YES",
        )

        self.assertEqual(workbench._positive_or_zero("Weight: ", 1.0), 2.5)
        self.assertEqual(workbench._positive_or_zero("Default: ", 3.0), 3.0)
        self.assertFalse(workbench._yes("No default: "))
        self.assertTrue(workbench._yes("Confirm: "))
        self.assertEqual(
            terminal.text.count("Enter a finite non-negative number."),
            4,
        )

        with CatalogStore(workbench.layout.catalog_database) as catalog:
            target, _ = self.populate_catalog(catalog)
            tracks = (target,)
            for raw in ("", "0", "-1", "99", "bad"):
                chooser, chooser_terminal = self.make_workbench(
                    raw,
                    name=f"chooser-{raw or 'blank'}",
                )
                self.assertIsNone(chooser._choose_track(tracks, "Track: "))
                if raw not in {"", "0", "-1"}:
                    self.assertIn("not available", chooser_terminal.text)
            chooser, _ = self.make_workbench("1", name="chooser-valid")
            self.assertEqual(chooser._choose_track(tracks, "Track: "), target)

    def test_main_menu_dispatches_help_diagnostics_and_exit(self) -> None:
        workbench, terminal = self.make_workbench(
            "1",
            "2",
            "3",
            "4",
            "5",
            "invalid",
            "0",
        )
        report = mock.Mock()
        report.render_text.return_value = "diagnostics rendered"

        with (
            mock.patch.object(workbench, "_create_job") as create_job,
            mock.patch.object(workbench, "_catalog_menu") as catalog_menu,
            mock.patch.object(workbench, "_show_runs") as show_runs,
            mock.patch.object(workbench_module, "run_doctor", return_value=report),
        ):
            result = workbench.run()

        self.assertEqual(result, 0)
        create_job.assert_called_once()
        catalog_menu.assert_called_once()
        show_runs.assert_called_once()
        self.assertIn("diagnostics rendered", terminal.text)
        self.assertIn("single reference uses the exact Matchering", terminal.text)
        self.assertIn("Enter one of the displayed menu numbers.", terminal.text)
        self.assertIn("Workbench closed.", terminal.text)

    def test_eof_keyboard_interrupt_and_run_workbench_wrapper_cancel_safely(self) -> None:
        for index, interruption in enumerate((EOFError(), KeyboardInterrupt())):
            terminal = ScriptedTerminal(interruption)
            result = run_workbench(
                self.root / f"cancel-{index}",
                input_function=terminal.input,
                output_function=terminal.output,
            )
            self.assertEqual(result, 0)
            self.assertIn("cancelled safely", terminal.text)


class CatalogMenuTests(WorkbenchTestCase):
    def test_add_list_verify_reference_sets_and_archive(self) -> None:
        workbench, terminal = self.make_workbench(
            str(self.root / "missing.wav"),
            "Missing",
            f'"{self.root / "added target.wav"}"',
            "Added Target",
            "",
        )
        valid_target = write_tone(
            self.root / "added target.wav",
            frequency=271.0,
            frames=2_048,
        )
        self.assertTrue(valid_target.is_file())

        with CatalogStore(workbench.layout.catalog_database) as catalog:
            workbench._add_tracks(catalog, (CatalogRole.TARGET,))
            tracks = workbench._list_tracks(catalog)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].label, "Added Target")
            self.assertIn(CatalogRole.TARGET, tracks[0].roles)
            self.assertIn("Could not add", terminal.text)
            self.assertIn("44100 Hz/2 ch", terminal.text)

            reference_path = write_tone(
                self.root / "reference.wav",
                frequency=487.0,
                frames=2_048,
            )
            reference = catalog.add_track(
                reference_path,
                roles=(CatalogRole.REFERENCE,),
                label="Reference",
            )
            reference_set = catalog.create_reference_set("Reusable Blend")
            catalog.set_reference_member(
                reference_set.set_id,
                reference.track_id,
                level_weight=2.0,
                frequency_weight=3.0,
            )
            workbench._list_reference_sets(catalog)
            self.assertIn("Reusable Blend", terminal.text)
            self.assertIn("level=2", terminal.text)

            reference_path.unlink()
            workbench._verify_catalog(catalog)
            self.assertIn("missing=1", terminal.text)

            cancel_terminal = ScriptedTerminal("1", "n")
            workbench.input = cancel_terminal.input
            workbench.output = cancel_terminal.output
            workbench._archive_track(catalog)
            self.assertFalse(catalog.get_track(tracks[0].track_id).archived)
            self.assertIn("Archive cancelled.", cancel_terminal.text)

            archive_terminal = ScriptedTerminal("1", "y")
            workbench.input = archive_terminal.input
            workbench.output = archive_terminal.output
            workbench._archive_track(catalog)
            archived = catalog.list_tracks(include_archived=True)[0]
            self.assertTrue(archived.archived)
            self.assertIn("historical run links were retained", archive_terminal.text)
            workbench._list_tracks(catalog)
            self.assertIn("archived", archive_terminal.text)
            self.assertIn("(no available location)", archive_terminal.text)

    def test_export_overwrite_and_import_catalog_and_selection(self) -> None:
        source_workbench, source_terminal = self.make_workbench("", name="source")
        with CatalogStore(source_workbench.layout.catalog_database) as source:
            selection = self.build_selection(source)
            source_workbench._export_catalog(source)
            export_path = source_workbench.layout.exports / "catalog.json"
            self.assertTrue(export_path.is_file())
            self.assertIn("Reimportable catalog exported", source_terminal.text)

            source_terminal.responses.extend(("", "n", "", "y"))
            previous = export_path.read_text(encoding="utf-8")
            source_workbench._export_catalog(source)
            self.assertIn("Export cancelled.", source_terminal.text)
            source_workbench._export_catalog(source)
            self.assertEqual(export_path.read_text(encoding="utf-8"), previous)

            selection_path = self.root / "selection.json"
            selection_path.write_text(selection.to_json(), encoding="utf-8")

        catalog_import, catalog_terminal = self.make_workbench(
            f'"{export_path}"',
            name="catalog-import",
        )
        with CatalogStore(catalog_import.layout.catalog_database) as destination:
            catalog_import._import_catalog(destination)
            self.assertEqual(len(destination.list_tracks()), 2)
            self.assertIn("Catalog imported", catalog_terminal.text)

        selection_import, selection_terminal = self.make_workbench(
            str(selection_path),
            name="selection-import",
        )
        with CatalogStore(selection_import.layout.catalog_database) as destination:
            selection_import._import_catalog(destination)
            self.assertEqual(len(destination.list_tracks()), 2)
            self.assertIn(selection.selection_id, selection_terminal.text)

    def test_catalog_menu_routes_roles_and_reports_operation_errors(self) -> None:
        missing = self.root / "absent-catalog.json"
        workbench, terminal = self.make_workbench(
            "bad",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            str(missing),
            "9",
            "0",
        )
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            with (
                mock.patch.object(workbench, "_list_tracks") as list_tracks,
                mock.patch.object(workbench, "_add_tracks") as add_tracks,
                mock.patch.object(workbench, "_verify_catalog") as verify,
                mock.patch.object(workbench, "_list_reference_sets") as list_sets,
                mock.patch.object(workbench, "_export_catalog") as export,
                mock.patch.object(workbench, "_archive_track") as archive,
            ):
                workbench._catalog_menu(catalog)

        self.assertIn("Enter one of the displayed menu numbers.", terminal.text)
        self.assertIn("Catalog operation failed:", terminal.text)
        list_tracks.assert_called_once_with(catalog)
        self.assertEqual(
            [call.args[1] for call in add_tracks.call_args_list],
            [
                (CatalogRole.TARGET,),
                (CatalogRole.REFERENCE,),
                (CatalogRole.TARGET, CatalogRole.REFERENCE),
            ],
        )
        verify.assert_called_once_with(catalog)
        list_sets.assert_called_once_with(catalog)
        export.assert_called_once_with(catalog)
        archive.assert_called_once_with(catalog)

    def test_empty_catalog_views_and_archive_blank_are_safe(self) -> None:
        workbench, terminal = self.make_workbench("")
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            self.assertEqual(workbench._list_tracks(catalog), ())
            self.assertEqual(workbench._list_reference_sets(catalog), ())
            workbench._verify_catalog(catalog)
            workbench._archive_track(catalog)
        self.assertIn("No matching tracks are cataloged.", terminal.text)
        self.assertIn("No named reference sets exist yet.", terminal.text)
        self.assertIn("Verified 0 location(s): none.", terminal.text)


class JobMaterializationTests(WorkbenchTestCase):
    def test_materialize_single_reference_uses_upstream_and_minimal_outputs(self) -> None:
        workbench, terminal = self.make_workbench("n", "n", "n")
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog)

        with mock.patch.object(workbench_module, "_new_run_id", return_value="single-run"):
            job, config_path, selection_path = workbench._materialize_job(selection)

        self.assertEqual(job.execution.engine, EngineKind.UPSTREAM)
        self.assertEqual(job.execution.job_id, "single-run")
        self.assertEqual(len(job.outputs), 1)
        self.assertEqual(job.outputs[0].mode, OutputMode.LIMITED)
        self.assertFalse(job.preview.enabled)
        self.assertEqual(job.references[0].level_weight, 1.0)
        self.assertEqual(job.references[0].frequency_weight, 1.0)
        self.assertEqual(
            load_job_config(config_path, resolve_paths=False).to_dict(),
            job.to_dict(),
        )
        self.assertEqual(
            CatalogSelection.from_json(selection_path.read_text(encoding="utf-8")),
            selection,
        )
        self.assertIn("Saved job:", terminal.text)

        repeat_terminal = ScriptedTerminal("n", "n", "n")
        workbench.input = repeat_terminal.input
        with (
            mock.patch.object(workbench_module, "_new_run_id", return_value="single-run"),
            self.assertRaises(FileExistsError),
        ):
            workbench._materialize_job(selection)

    def test_materialize_weighted_selection_includes_all_outputs_and_previews(self) -> None:
        workbench, _ = self.make_workbench("y", "yes", "Y")
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog, references=2)
            weighted = selection_with_weights(
                selection,
                ((2.0, 1.0), (1.0, 4.0)),
            )

        with mock.patch.object(workbench_module, "_new_run_id", return_value="weighted-run"):
            job, config_path, selection_path = workbench._materialize_job(weighted)

        self.assertEqual(job.execution.engine, EngineKind.NATIVE)
        self.assertEqual(
            tuple(output.mode for output in job.outputs),
            (
                OutputMode.LIMITED,
                OutputMode.NORMALIZED,
                OutputMode.RAW_FLOAT,
            ),
        )
        self.assertEqual(
            tuple((item.level_weight, item.frequency_weight) for item in job.references),
            ((2.0, 1.0), (1.0, 4.0)),
        )
        self.assertTrue(job.preview.enabled)
        self.assertTrue(config_path.is_file())
        self.assertTrue(selection_path.is_file())
        self.assertTrue((workbench.layout.outputs / "weighted-run").is_dir())
        self.assertTrue((workbench.layout.runs / "weighted-run").is_dir())

    def test_selection_display_distinguishes_exact_and_weighted_profiles(self) -> None:
        workbench, terminal = self.make_workbench()
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            single = self.build_selection(catalog)
        workbench._show_selection(single)
        self.assertIn("exact Matchering compatibility", terminal.text)
        self.assertIn("(1.000)", terminal.text)

        weighted_workbench, weighted_terminal = self.make_workbench(name="weighted-display")
        with CatalogStore(weighted_workbench.layout.catalog_database) as catalog:
            weighted = self.build_selection(catalog, references=2)
            weighted = selection_with_weights(
                weighted,
                ((3.0, 1.0), (1.0, 3.0)),
            )
        weighted_workbench._show_selection(weighted)
        self.assertIn("weighted profile", weighted_terminal.text)
        self.assertIn("(0.750)", weighted_terminal.text)


class CreateJobWorkflowTests(WorkbenchTestCase):
    def test_single_reference_dry_run_then_explicit_render(self) -> None:
        workbench, terminal = self.make_workbench("1", "1", "", "RUN")
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog)
            job = self.job_for_selection(selection)
            config_path = self.root / "job.json"
            selection_path = self.root / "job.selection.json"
            report = ValidationReport(())
            with (
                mock.patch.object(
                    workbench,
                    "_materialize_job",
                    return_value=(job, config_path, selection_path),
                ),
                mock.patch.object(
                    workbench_module,
                    "validate_job",
                    return_value=report,
                ),
                mock.patch.object(
                    workbench,
                    "_execute_job",
                    side_effect=(True, True),
                ) as execute,
            ):
                workbench._create_job(catalog)

        self.assertEqual(execute.call_count, 2)
        dry_call, render_call = execute.call_args_list
        self.assertTrue(dry_call.kwargs["dry_run"])
        self.assertEqual(dry_call.args[2].execution.job_id, "operator-job-dry")
        self.assertFalse(render_call.kwargs["dry_run"])
        self.assertEqual(render_call.args[2], job)
        self.assertIn("Validation PASS", terminal.text)

    def test_dry_run_failure_blocks_render_and_skip_saves_without_rendering(self) -> None:
        for name, responses, dry_result, expected_calls, expected_message in (
            (
                "dry-failure",
                ("1", "1", ""),
                False,
                1,
                "Real rendering remains blocked",
            ),
            (
                "skip-render",
                ("1", "1", "n", ""),
                True,
                0,
                "Job saved without rendering",
            ),
        ):
            with self.subTest(name=name):
                workbench, terminal = self.make_workbench(*responses, name=name)
                with CatalogStore(workbench.layout.catalog_database) as catalog:
                    selection = self.build_selection(catalog)
                    job = self.job_for_selection(selection, run_id=name)
                    with (
                        mock.patch.object(
                            workbench,
                            "_materialize_job",
                            return_value=(
                                job,
                                self.root / f"{name}.json",
                                self.root / f"{name}.selection.json",
                            ),
                        ),
                        mock.patch.object(
                            workbench_module,
                            "validate_job",
                            return_value=ValidationReport(()),
                        ),
                        mock.patch.object(
                            workbench,
                            "_execute_job",
                            return_value=dry_result,
                        ) as execute,
                    ):
                        workbench._create_job(catalog)
                self.assertEqual(execute.call_count, expected_calls)
                self.assertIn(expected_message, terminal.text)

    def test_validation_failure_keeps_job_and_reports_every_issue(self) -> None:
        workbench, terminal = self.make_workbench("1", "1")
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog)
            job = self.job_for_selection(selection)
            report = ValidationReport(
                (
                    ValidationIssue(
                        "MMT-E-TEST",
                        ValidationSeverity.ERROR,
                        "input rejected",
                    ),
                    ValidationIssue(
                        "MMT-W-TEST",
                        ValidationSeverity.WARNING,
                        "input warning",
                    ),
                )
            )
            with (
                mock.patch.object(
                    workbench,
                    "_materialize_job",
                    return_value=(
                        job,
                        self.root / "failed-job.json",
                        self.root / "failed.selection.json",
                    ),
                ),
                mock.patch.object(
                    workbench_module,
                    "validate_job",
                    return_value=report,
                ),
                mock.patch.object(workbench, "_execute_job") as execute,
            ):
                workbench._create_job(catalog)

        execute.assert_not_called()
        self.assertIn("Validation FAIL: 1 error(s), 1 warning(s)", terminal.text)
        self.assertIn("ERROR MMT-E-TEST: input rejected", terminal.text)
        self.assertIn("WARNING MMT-W-TEST: input warning", terminal.text)
        self.assertIn("Saved job for correction", terminal.text)

    def test_multi_reference_custom_weights_can_be_saved_as_named_set(self) -> None:
        workbench, terminal = self.make_workbench(
            "1",
            "1,2",
            "y",
            "2",
            "3",
            "4",
            "5",
            "y",
            "My Weighted Set",
            "n",
            "",
        )
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            base = self.build_selection(catalog, references=2)
            job = self.job_for_selection(base)
            captured: list[CatalogSelection] = []

            def materialize(
                selection: CatalogSelection,
            ) -> tuple[JobConfig, Path, Path]:
                captured.append(selection)
                return (
                    job,
                    self.root / "weighted.json",
                    self.root / "weighted.selection.json",
                )

            with (
                mock.patch.object(workbench, "_materialize_job", side_effect=materialize),
                mock.patch.object(
                    workbench_module,
                    "validate_job",
                    return_value=ValidationReport(()),
                ),
                mock.patch.object(workbench, "_execute_job") as execute,
            ):
                workbench._create_job(catalog)

            reference_sets = catalog.list_reference_sets()

        execute.assert_not_called()
        self.assertEqual(len(captured), 1)
        self.assertEqual(
            tuple((item.level_weight, item.frequency_weight) for item in captured[0].references),
            ((2.0, 3.0), (4.0, 5.0)),
        )
        self.assertEqual(len(reference_sets), 1)
        self.assertEqual(reference_sets[0].name, "My Weighted Set")
        self.assertEqual(
            tuple((item.level_weight, item.frequency_weight) for item in reference_sets[0].members),
            ((2.0, 3.0), (4.0, 5.0)),
        )
        self.assertIn("Saved reference set:", terminal.text)

    def test_named_set_selection_and_duplicate_set_error_are_visible(self) -> None:
        workbench, terminal = self.make_workbench(
            "1",
            "S1",
            "n",
            "y",
            "Existing Set",
        )
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            target, references = self.populate_catalog(catalog, references=2)
            reference_set = catalog.create_reference_set("Existing Set")
            for ordinal, reference in enumerate(references):
                catalog.set_reference_member(
                    reference_set.set_id,
                    reference.track_id,
                    ordinal=ordinal,
                )
            expected = catalog.build_selection(
                target.track_id,
                reference_set_id=reference_set.set_id,
            )
            captured: list[CatalogSelection] = []

            def fail_materialization(selection: CatalogSelection) -> tuple[JobConfig, Path, Path]:
                captured.append(selection)
                raise OSError("disk unavailable")

            with mock.patch.object(
                workbench,
                "_materialize_job",
                side_effect=fail_materialization,
            ):
                workbench._create_job(catalog)

        self.assertEqual(captured, [expected])
        self.assertIn("Reference set was not saved:", terminal.text)
        self.assertIn("Could not create job files: disk unavailable", terminal.text)

    def test_job_selection_cancellation_missing_reference_and_invalid_inputs(self) -> None:
        target_only, target_terminal = self.make_workbench("1", name="target-only")
        with CatalogStore(target_only.layout.catalog_database) as catalog:
            target_path = write_tone(self.root / "only-target.wav", frames=2_048)
            catalog.add_track(target_path, roles=(CatalogRole.TARGET,))
            target_only._create_job(catalog)
        self.assertIn("Add at least one reference", target_terminal.text)

        invalid, invalid_terminal = self.make_workbench("1", "9", name="invalid-reference")
        with CatalogStore(invalid.layout.catalog_database) as catalog:
            self.build_selection(catalog)
            invalid._create_job(catalog)
        self.assertIn("Invalid reference selection:", invalid_terminal.text)

        invalid_set, invalid_set_terminal = self.make_workbench(
            "1",
            "S0",
            name="invalid-set",
        )
        with CatalogStore(invalid_set.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog)
            reference_set = catalog.create_reference_set("Existing")
            catalog.set_reference_member(
                reference_set.set_id,
                selection.references[0].track.track_id,
            )
            invalid_set._create_job(catalog)
        self.assertIn("outside the displayed list", invalid_set_terminal.text)

        cancel_references, _ = self.make_workbench("1", "", name="cancel-references")
        with CatalogStore(cancel_references.layout.catalog_database) as catalog:
            self.build_selection(catalog)
            with mock.patch.object(cancel_references, "_materialize_job") as materialize:
                cancel_references._create_job(catalog)
        materialize.assert_not_called()

        cancel, _ = self.make_workbench("", name="cancel-target")
        with CatalogStore(cancel.layout.catalog_database) as catalog:
            self.build_selection(catalog)
            with mock.patch.object(cancel, "_materialize_job") as materialize:
                cancel._create_job(catalog)
        materialize.assert_not_called()

        bad_weights, bad_terminal = self.make_workbench(
            "1",
            "1,2",
            "y",
            "0",
            "0",
            "0",
            "0",
            name="bad-weights",
        )
        with CatalogStore(bad_weights.layout.catalog_database) as catalog:
            self.build_selection(catalog, references=2)
            with mock.patch.object(bad_weights, "_materialize_job") as materialize:
                bad_weights._create_job(catalog)
        materialize.assert_not_called()
        self.assertIn("Invalid weight set:", bad_terminal.text)

        blank_set_name, blank_name_terminal = self.make_workbench(
            "1",
            "1,2",
            "n",
            "y",
            "",
            name="blank-set-name",
        )
        with CatalogStore(blank_set_name.layout.catalog_database) as catalog:
            self.build_selection(catalog, references=2)
            with mock.patch.object(
                blank_set_name,
                "_materialize_job",
                side_effect=OSError("stop after blank name"),
            ):
                blank_set_name._create_job(catalog)
            self.assertEqual(catalog.list_reference_sets(), ())
        self.assertIn("stop after blank name", blank_name_terminal.text)


class ExecutionAndRunDisplayTests(WorkbenchTestCase):
    def test_execute_job_success_failure_and_catalog_finalization_failure(self) -> None:
        with CatalogStore(self.root / "execution-catalog.sqlite3") as catalog:
            selection = self.build_selection(catalog)

            success_service = mock.Mock()
            success_workbench, success_terminal = self.make_workbench(
                name="execute-success",
                service=success_service,
            )
            success_job = self.job_for_selection(selection, run_id="success")
            succeeded = success_workbench._execute_job(
                catalog,
                selection,
                success_job,
                self.root / "success.json",
                self.root / "success.selection.json",
                dry_run=True,
            )
            self.assertTrue(succeeded)
            self.assertTrue(success_service.run.call_args.kwargs["dry_run"])
            self.assertEqual(
                success_service.run.call_args.kwargs["command"][:3],
                ("mmt", "workbench", "run"),
            )
            self.assertIn("Dry run passed.", success_terminal.text)

            failed_service = mock.Mock()
            failed_service.run.side_effect = ProcessingError("engine exploded")
            failed_workbench, failed_terminal = self.make_workbench(
                name="execute-failure",
                service=failed_service,
            )
            failed = failed_workbench._execute_job(
                catalog,
                selection,
                self.job_for_selection(selection, run_id="failed"),
                self.root / "failed.json",
                self.root / "failed.selection.json",
                dry_run=False,
            )
            self.assertFalse(failed)
            self.assertIn("Run failed:", failed_terminal.text)
            self.assertIn("Manifest:", failed_terminal.text)
            self.assertIn("Event log:", failed_terminal.text)

            finalize_service = mock.Mock()

            def create_manifest(*_args: object, **kwargs: object) -> None:
                Path(kwargs["manifest_path"]).write_text("{}", encoding="utf-8")  # type: ignore[arg-type]

            finalize_service.run.side_effect = create_manifest
            finalize_workbench, finalize_terminal = self.make_workbench(
                name="execute-finalize",
                service=finalize_service,
            )
            finalize_job = self.job_for_selection(selection, run_id="finalize")
            with mock.patch.object(
                workbench_module,
                "finalize_catalog_run",
                side_effect=CatalogError("index unavailable"),
            ) as finalize:
                finalized = finalize_workbench._execute_job(
                    catalog,
                    selection,
                    finalize_job,
                    self.root / "finalize.json",
                    self.root / "finalize.selection.json",
                    dry_run=False,
                )
            self.assertFalse(finalized)
            finalize.assert_called_once()
            self.assertIn("Catalog finalization failed:", finalize_terminal.text)

    def test_successful_manifest_branch_finalizes_and_reports_render_output(self) -> None:
        service = mock.Mock()
        workbench, terminal = self.make_workbench(
            name="manifest-success",
            service=service,
        )
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog)
            job = self.job_for_selection(selection, run_id="manifest-success")

            def create_manifest(*_args: object, **kwargs: object) -> None:
                Path(kwargs["manifest_path"]).write_text("{}", encoding="utf-8")  # type: ignore[arg-type]

            service.run.side_effect = create_manifest
            with mock.patch.object(
                workbench_module,
                "finalize_catalog_run",
                return_value=mock.sentinel.manifest,
            ) as finalize:
                result = workbench._execute_job(
                    catalog,
                    selection,
                    job,
                    self.root / "manifest-success.json",
                    self.root / "manifest-success.selection.json",
                    dry_run=False,
                )

        self.assertTrue(result)
        finalize.assert_called_once()
        self.assertIn(f"Mastering completed: {job.outputs[0].path}", terminal.text)

    def test_show_runs_lists_recent_inventory_and_empty_state(self) -> None:
        empty_workbench, empty_terminal = self.make_workbench(name="empty-runs")
        with CatalogStore(empty_workbench.layout.catalog_database) as catalog:
            empty_workbench._show_runs(catalog)
        self.assertIn("No catalog-backed runs", empty_terminal.text)

        workbench, terminal = self.make_workbench(name="recorded-runs")
        with CatalogStore(workbench.layout.catalog_database) as catalog:
            selection = self.build_selection(catalog)
            manifest = self.root / "recorded-manifest.json"
            manifest.write_text("manifest", encoding="utf-8")
            output = self.root / "recorded-output.wav"
            output.write_bytes(b"rendered")
            catalog.record_run(
                "recorded-run",
                "completed",
                selection=selection,
                manifest_path=manifest,
            )
            catalog.index_artifact(
                "recorded-run",
                output,
                role=CatalogRole.GENERATED_OUTPUT,
            )
            workbench._show_runs(catalog)

        self.assertIn("recorded-run [completed] 1 artifact(s)", terminal.text)
        self.assertIn(selection.selection_id, terminal.text)
        self.assertIn(str(manifest.resolve()), terminal.text)
        self.assertIn("generated-output[0]", terminal.text)
        self.assertIn(str(output.resolve()), terminal.text)

    def test_help_explains_privacy_and_reimportable_artifacts(self) -> None:
        workbench, terminal = self.make_workbench()
        workbench._help()
        self.assertIn("deterministic weighted", terminal.text)
        self.assertIn("reimportable selection", terminal.text)
        self.assertIn("never copied or deleted", terminal.text)
        self.assertIn("private paths and hashes", terminal.text)


if __name__ == "__main__":
    unittest.main()
