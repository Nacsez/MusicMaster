"""Black-box command contracts for the private catalog CLI."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from music_mastering_tools.catalog import CatalogRole, CatalogStore
from music_mastering_tools.cli import EXIT_SUCCESS, EXIT_USAGE_OR_CONFIG, build_parser, main

from .helpers import write_tone


def _invoke(arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _catalog_arguments(database: Path, *arguments: str) -> list[str]:
    return ["catalog", "--database", str(database), *arguments]


class CatalogCliTests(unittest.TestCase):
    def test_parser_exposes_catalog_tree_and_init_empty_list_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "catalog.sqlite3"
            parsed = build_parser().parse_args(_catalog_arguments(database, "list", "--json"))
            self.assertEqual(parsed.command, "catalog")
            self.assertEqual(parsed.catalog_command, "list")
            self.assertTrue(parsed.json)

            exit_code, output, errors = _invoke(_catalog_arguments(database, "init"))
            self.assertEqual(exit_code, EXIT_SUCCESS)
            self.assertEqual(errors, "")
            initialized = json.loads(output)
            self.assertTrue(initialized["catalog_id"].startswith("cat_"))
            self.assertEqual(initialized["database"], str(database.resolve()))
            self.assertEqual(initialized["schema_version"], 1)

            exit_code, output, errors = _invoke(_catalog_arguments(database, "list"))
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(output, "Catalog contains no matching tracks.\n")

            exit_code, output, errors = _invoke(_catalog_arguments(database, "list", "--json"))
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(output), {"count": 0, "items": []})

    def test_track_add_show_verify_relink_archive_restore_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "catalog.sqlite3"
            target_path = write_tone(root / "target.wav", frequency=330)
            reference_path = write_tone(root / "reference.wav", frequency=660)

            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "add",
                    str(target_path),
                    "--role",
                    "target",
                    "--label",
                    "Target",
                    "--json",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            target = json.loads(output)["items"][0]
            self.assertEqual(target["roles"], ["target"])
            self.assertEqual(target["label"], "Target")
            self.assertEqual(target["audio_facts"]["sample_rate"], 44_100)

            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "add",
                    str(reference_path),
                    "--role",
                    "reference",
                    "--json",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            reference = json.loads(output)["items"][0]
            self.assertEqual(reference["roles"], ["reference"])

            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "show", target["track_id"])
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertIn("Target", output)
            self.assertIn("available:", output)

            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "show",
                    target["track_id"],
                    "--json",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(output)["track_id"], target["track_id"])

            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "list",
                    "--role",
                    "reference",
                    "--json",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            listed = json.loads(output)
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["items"][0]["track_id"], reference["track_id"])

            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "verify",
                    target["track_id"],
                    "--json",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(output)["items"][0]["state"], "available")

            replacement = root / "target-moved.wav"
            replacement.write_bytes(target_path.read_bytes())
            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "relink",
                    target["track_id"],
                    str(replacement),
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(output)["path"], str(replacement.resolve()))

            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "archive", target["track_id"])
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertTrue(json.loads(output)["archived"])
            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "restore", target["track_id"])
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertFalse(json.loads(output)["archived"])

            exit_code, _, errors = _invoke(
                _catalog_arguments(
                    database,
                    "add",
                    str(target_path),
                    str(reference_path),
                    "--role",
                    "both",
                    "--label",
                    "Invalid shared label",
                )
            )
            self.assertEqual(exit_code, EXIT_USAGE_OR_CONFIG)
            self.assertIn("--label can be used only", errors)

    def test_reference_sets_selection_export_and_both_import_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "catalog.sqlite3"
            target_path = write_tone(root / "target.wav", frequency=330)
            reference_a_path = write_tone(root / "reference-a.wav", frequency=550)
            reference_b_path = write_tone(root / "reference-b.wav", frequency=770)

            def add(path: Path, role: str) -> str:
                exit_code, output, errors = _invoke(
                    _catalog_arguments(
                        database,
                        "add",
                        str(path),
                        "--role",
                        role,
                        "--json",
                    )
                )
                self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
                return str(json.loads(output)["items"][0]["track_id"])

            target_id = add(target_path, "target")
            reference_a_id = add(reference_a_path, "reference")
            reference_b_id = add(reference_b_path, "reference")

            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "set", "create", "Two References")
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            set_id = output.split(maxsplit=1)[0]
            self.assertTrue(set_id.startswith("refset_"))

            for track_id, level, frequency in (
                (reference_a_id, "0.75", "0.25"),
                (reference_b_id, "0.25", "0.75"),
            ):
                exit_code, output, errors = _invoke(
                    _catalog_arguments(
                        database,
                        "set",
                        "add",
                        set_id,
                        track_id,
                        "--level-weight",
                        level,
                        "--frequency-weight",
                        frequency,
                    )
                )
                self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
                self.assertIn("reference(s)", output)

            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "set", "list", "--json")
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(json.loads(output)["count"], 1)
            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "set", "show", set_id, "--json")
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(len(json.loads(output)["members"]), 2)

            selection_path = root / "selection.json"
            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "selection",
                    "--target",
                    target_id,
                    "--reference-set",
                    set_id,
                    "--output",
                    str(selection_path),
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertIn("Selection sel_sha256_", output)
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            self.assertEqual(len(selection["references"]), 2)
            self.assertEqual(selection["references"][0]["level_weight"], 0.75)

            imported_database = root / "imported-selection.sqlite3"
            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    imported_database,
                    "import",
                    str(selection_path),
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertEqual(
                json.loads(output)["selection_id"],
                selection["selection_id"],
            )

            catalog_export = root / "catalog.json"
            exit_code, output, errors = _invoke(
                _catalog_arguments(database, "export", str(catalog_export))
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertIn("Catalog exported:", output)
            imported_catalog_database = root / "imported-catalog.sqlite3"
            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    imported_catalog_database,
                    "import",
                    str(catalog_export),
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertIn("catalog_id", json.loads(output))

            exit_code, _, errors = _invoke(
                _catalog_arguments(database, "export", str(catalog_export))
            )
            self.assertEqual(exit_code, EXIT_USAGE_OR_CONFIG)
            self.assertIn("FileExistsError", errors)
            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "export",
                    str(catalog_export),
                    "--force",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertIn("Catalog exported:", output)

            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "set",
                    "remove",
                    set_id,
                    reference_a_id,
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            self.assertIn("(1 reference(s))", output)

    def test_runs_artifacts_and_malformed_import_have_stable_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "catalog.sqlite3"
            artifact_path = root / "master.wav"
            artifact_path.write_bytes(b"master")
            with CatalogStore(database) as catalog:
                catalog.record_run("run-1", "completed")
                catalog.index_artifact(
                    "run-1",
                    artifact_path,
                    role=CatalogRole.GENERATED_OUTPUT,
                    media_type="audio/wav",
                    metadata={"mode": "limited"},
                )

            exit_code, output, errors = _invoke(_catalog_arguments(database, "runs", "--json"))
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            runs = json.loads(output)
            self.assertEqual(runs["items"][0]["run_id"], "run-1")
            exit_code, output, errors = _invoke(
                _catalog_arguments(
                    database,
                    "artifacts",
                    "run-1",
                    "--json",
                )
            )
            self.assertEqual((exit_code, errors), (EXIT_SUCCESS, ""))
            artifacts = json.loads(output)
            self.assertEqual(artifacts["items"][0]["metadata"]["mode"], "limited")

            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            exit_code, _, errors = _invoke(_catalog_arguments(database, "import", str(malformed)))
            self.assertEqual(exit_code, EXIT_USAGE_OR_CONFIG)
            self.assertIn("invalid JSON document", errors)


if __name__ == "__main__":
    unittest.main()
