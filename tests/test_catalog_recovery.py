"""Recovery contracts for indexing a completed run without rerendering it."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from music_mastering_tools import recover_catalog_run
from music_mastering_tools.catalog import CatalogRole, CatalogStore
from music_mastering_tools.catalog_integration import register_job_selection
from music_mastering_tools.cli import EXIT_SUCCESS, main
from music_mastering_tools.config import JobConfig, OutputSpec, ReferenceSpec
from music_mastering_tools.manifest import ArtifactManifest, RunManifest

from .helpers import write_tone


def _invoke(arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class CatalogRecoveryTests(unittest.TestCase):
    def test_recover_run_is_idempotent_and_auto_detects_owned_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=330)
            reference = write_tone(root / "reference.wav", frequency=660)
            output = write_tone(root / "master.wav", frequency=440)
            configuration = root / "job.json"
            configuration.write_text('{"schema_version": 1}\n', encoding="utf-8")
            selection_path = root / "selection.json"
            manifest_path = root / "manifest.json"
            job = JobConfig(
                target=str(target),
                references=(ReferenceSpec(str(reference)),),
                outputs=(OutputSpec(str(output)),),
            )

            with CatalogStore(root / "selection-source.sqlite3") as source:
                selection = register_job_selection(source, job)
            selection_path.write_text(selection.to_json(), encoding="utf-8")

            manifest = RunManifest.create(run_id="recover-completed-run")
            manifest.add_input(
                ArtifactManifest.from_file(
                    target,
                    role="target",
                    media_type="audio/wav",
                )
            )
            manifest.add_input(
                ArtifactManifest.from_file(
                    reference,
                    role="reference[0]",
                    media_type="audio/wav",
                )
            )
            manifest.add_output(
                ArtifactManifest.from_file(
                    output,
                    role="mastered-output",
                    media_type="audio/wav",
                )
            )
            manifest.mark_completed()
            manifest.save(manifest_path)

            first_database = root / "first-catalog.sqlite3"
            explicit_arguments = [
                "catalog",
                "--database",
                str(first_database),
                "recover-run",
                str(manifest_path),
                "--selection",
                str(selection_path),
                "--configuration",
                str(configuration),
            ]
            exit_code, stdout, stderr = _invoke(explicit_arguments)
            self.assertEqual((exit_code, stderr), (EXIT_SUCCESS, ""))
            recovered = json.loads(stdout)
            self.assertEqual(recovered["run_id"], "recover-completed-run")
            self.assertEqual(recovered["status"], "completed")
            self.assertEqual(recovered["artifact_count"], 6)

            exit_code, stdout, stderr = _invoke(explicit_arguments)
            self.assertEqual((exit_code, stderr), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(stdout)["artifact_count"], 6)

            second_database = root / "second-catalog.sqlite3"
            exit_code, stdout, stderr = _invoke(
                [
                    "catalog",
                    "--database",
                    str(second_database),
                    "recover-run",
                    str(manifest_path),
                ]
            )
            self.assertEqual((exit_code, stderr), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(stdout)["artifact_count"], 6)

            with CatalogStore(second_database) as catalog:
                self.assertEqual(len(catalog.list_runs()), 1)
                self.assertEqual(
                    len(catalog.list_tracks(role=CatalogRole.TARGET)),
                    1,
                )
                self.assertEqual(
                    len(catalog.list_tracks(role=CatalogRole.REFERENCE)),
                    1,
                )
                self.assertEqual(
                    len(catalog.list_tracks(role=CatalogRole.GENERATED_OUTPUT)),
                    1,
                )
                self.assertEqual(
                    len(
                        catalog.list_run_artifacts(
                            "recover-completed-run",
                            role=CatalogRole.AUDIT,
                        )
                    ),
                    3,
                )

            third_database = root / "third-catalog.sqlite3"
            with CatalogStore(third_database) as catalog:
                recovered = recover_catalog_run(catalog, manifest_path)
                self.assertEqual(
                    recovered.manifest.run_id,
                    "recover-completed-run",
                )
                self.assertEqual(
                    recovered.selection.selection_id,
                    selection.selection_id,
                )
                self.assertEqual(recovered.manifest_path, manifest_path.resolve())
                self.assertEqual(
                    len(catalog.list_run_artifacts(recovered.manifest.run_id)),
                    6,
                )

    def test_public_recovery_uses_embedded_selection_and_rejects_ambiguous_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=330)
            reference = write_tone(root / "reference.wav", frequency=660)
            output = write_tone(root / "master.wav", frequency=440)
            selection_path = root / "selection.json"
            job = JobConfig(
                target=str(target),
                references=(ReferenceSpec(str(reference)),),
                outputs=(OutputSpec(str(output)),),
            )
            with CatalogStore(root / "selection-source.sqlite3") as source:
                selection = register_job_selection(source, job)
            selection_path.write_text(selection.to_json(), encoding="utf-8")

            manifest_path = root / "embedded-manifest.json"
            manifest = RunManifest.create(run_id="embedded-selection-run")
            manifest.add_input(
                ArtifactManifest.from_file(target, role="target", media_type="audio/wav")
            )
            manifest.add_input(
                ArtifactManifest.from_file(
                    reference,
                    role="reference[0]",
                    media_type="audio/wav",
                )
            )
            manifest.add_output(
                ArtifactManifest.from_file(
                    output,
                    role="mastered-output",
                    media_type="audio/wav",
                )
            )
            manifest.extensions["catalog"] = {"selection": selection.to_dict()}
            manifest.mark_completed()
            manifest.save(manifest_path)

            with CatalogStore(root / "embedded-catalog.sqlite3") as catalog:
                recovered = recover_catalog_run(catalog, manifest_path)
                self.assertEqual(
                    recovered.manifest.run_id,
                    "embedded-selection-run",
                )
                self.assertEqual(len(catalog.list_tracks()), 3)

            ambiguous_path = root / "ambiguous-manifest.json"
            ambiguous = RunManifest.load(manifest_path)
            ambiguous.extensions.clear()
            ambiguous.add_input(
                ArtifactManifest.from_file(
                    selection_path,
                    role="catalog-selection",
                    media_type="application/json",
                )
            )
            duplicate_selection = root / "duplicate-selection.json"
            duplicate_selection.write_text(selection.to_json(), encoding="utf-8")
            ambiguous.add_input(
                ArtifactManifest.from_file(
                    duplicate_selection,
                    role="catalog-selection",
                    media_type="application/json",
                )
            )
            ambiguous.save(ambiguous_path)

            with CatalogStore(root / "ambiguous-catalog.sqlite3") as catalog:
                with self.assertRaisesRegex(ValueError, "more than one"):
                    recover_catalog_run(catalog, ambiguous_path)
                self.assertEqual(catalog.list_tracks(), ())
                self.assertEqual(catalog.list_runs(), ())


if __name__ == "__main__":
    unittest.main()
