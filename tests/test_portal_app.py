"""Application-layer contracts for the private graphical portal."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from unittest import mock

from music_mastering_tools.catalog import CatalogRole, CatalogStore
from music_mastering_tools.catalog_integration import finalize_catalog_run
from music_mastering_tools.errors import ManifestError
from music_mastering_tools.events import Event
from music_mastering_tools.manifest import ArtifactManifest, RunManifest
from music_mastering_tools.portal_app import (
    MAX_PORTAL_TARGETS,
    PORTAL_PREFERENCES_KIND,
    PORTAL_PREFERENCES_SCHEMA_VERSION,
    OperationEventSink,
    OperationRecord,
    PortalApplication,
    PortalBusyError,
    PortalOperationManager,
)

from .helpers import write_silence, write_tone


def _job_request(
    target_id: str,
    references: list[tuple[str, float, float]],
) -> dict[str, object]:
    return {
        "target_id": target_id,
        "references": [
            {
                "track_id": track_id,
                "level_weight": level_weight,
                "frequency_weight": frequency_weight,
            }
            for track_id, level_weight, frequency_weight in references
        ],
        "outputs": {
            "limited": True,
            "normalized": True,
            "raw": True,
            "limited_subtype": "PCM_24",
            "normalized_subtype": "PCM_24",
            "raw_subtype": "FLOAT",
        },
        "preview": {
            "enabled": False,
            "subtype": "PCM_16",
            "duration_seconds": 30.0,
            "analysis_step_seconds": 5.0,
            "fade_seconds": 1.0,
            "fade_coefficient": 8.0,
        },
        "settings": {
            "audio": {},
            "matching": {},
            "limiter": {},
            "detection": {},
            "edge_cases": {},
        },
        "notes": "Portal regression job",
    }


class PortalOperationManagerTests(unittest.TestCase):
    def test_serializes_work_records_events_and_failures(self) -> None:
        manager = PortalOperationManager()
        entered = threading.Event()
        release = threading.Event()

        def worker(sink: OperationEventSink) -> Mapping[str, Any]:
            sink.emit(
                Event.create(
                    "MMT-I-PORTAL-TEST",
                    "Worker entered.",
                    stage="test-stage",
                )
            )
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return {"answer": 42}

        first = manager.start("test", worker)
        self.assertTrue(entered.wait(timeout=5))
        with self.assertRaises(PortalBusyError):
            manager.start("overlap", lambda sink: {})
        current = manager.current()
        self.assertIsNotNone(current)
        if current is None:
            self.fail("active operation unexpectedly absent")
        self.assertEqual(current.operation_id, first.operation_id)
        self.assertEqual(current.stage, "test-stage")
        self.assertEqual(current.events[0]["code"], "MMT-I-PORTAL-TEST")

        release.set()
        completed = self._wait_for(manager, first.operation_id)
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual(completed.result, {"answer": 42})
        self.assertFalse(manager.active)

        failed = manager.start(
            "failure",
            lambda sink: (_ for _ in ()).throw(ValueError("injected failure")),
        )
        failed_result = self._wait_for(manager, failed.operation_id)
        self.assertEqual(failed_result.state, "failed")
        failure = failed_result.error
        if failure is None:
            self.fail("failed operation did not retain its error")
        self.assertEqual(failure["exception_type"], "ValueError")
        self.assertIn("injected failure", failure["message"])
        self.assertIn("traceback", failure)
        manager.shutdown()

    @staticmethod
    def _wait_for(
        manager: PortalOperationManager,
        operation_id: str,
    ) -> OperationRecord:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            operation = manager.get(operation_id)
            if operation.state not in {"queued", "running"}:
                return operation
            time.sleep(0.01)
        raise AssertionError("operation did not finish")


class PortalApplicationTests(unittest.TestCase):
    def test_master_library_groups_versions_and_automates_single_version_ab(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target_path = write_tone(root / "Original Mix.wav", frequency=330)
            reference_path = write_tone(root / "Reference.wav", frequency=660)
            target = self._add(app, target_path, "target", "Original Mix")
            reference = self._add(app, reference_path, "reference", "Reference")

            first = self._complete_master_version(
                app,
                _job_request(target, [(reference, 1.0, 1.0)]),
                frequency=440,
            )
            library = app.master_library()
            self.assertEqual(library["summary"]["source_count"], 1)
            source = library["sources"][0]
            self.assertEqual(source["source"]["track_id"], target)
            self.assertEqual(source["status"], "mastered")
            self.assertEqual(source["active_version_count"], 1)
            self.assertFalse(source["requires_version_choice"])
            self.assertEqual(source["automatic_ab"]["a"]["track_id"], target)
            self.assertEqual(
                source["automatic_ab"]["b"]["mode"],
                "limited",
                "limited must win the stable limited > normalized > raw preference",
            )
            version = source["versions"][0]
            self.assertEqual(version["version_id"], first)
            self.assertEqual(
                [item["mode"] for item in version["deliverables"]],
                ["limited", "normalized", "raw"],
            )
            self.assertTrue(all(item["playable"] for item in version["deliverables"]))
            self.assertNotIn(
                CatalogRole.GENERATED_OUTPUT.value,
                source["source"]["roles"],
            )

            second = self._complete_master_version(
                app,
                _job_request(target, [(reference, 1.0, 1.0)]),
                frequency=550,
            )
            library = app.master_library()
            source = library["sources"][0]
            self.assertEqual(
                [item["version_id"] for item in source["versions"]],
                [second, first],
            )
            self.assertTrue(source["requires_version_choice"])
            self.assertIsNone(source["automatic_ab"])

            before_path = app.get_track(target)["preferred_path"]
            renamed = app.update_track_label(target, "Renamed Song")
            self.assertEqual(renamed["label"], "Renamed Song")
            self.assertEqual(renamed["preferred_path"], before_path)
            self.assertTrue(target_path.is_file())
            app.update_track_label(target, None)
            self.assertIsNone(app.get_track(target)["label"])
            for invalid in (" padded", "padded ", "", "line\nbreak", "x" * 201):
                with self.subTest(label=repr(invalid)):
                    with self.assertRaises((TypeError, ValueError)):
                        app.update_track_label(target, invalid)
            app.close()

    def test_master_library_falls_back_across_missing_deliverables_truthfully(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_tone(root / "target.wav", frequency=330),
                "target",
                "Target",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )
            version_id = self._complete_master_version(
                app,
                _job_request(target, [(reference, 1.0, 1.0)]),
                frequency=440,
            )
            initial_version = app.master_library()["sources"][0]["versions"][0]
            outputs = {
                deliverable["mode"]: Path(deliverable["path"])
                for deliverable in initial_version["deliverables"]
            }
            normalized_track_id = next(
                deliverable["track_id"]
                for deliverable in initial_version["deliverables"]
                if deliverable["mode"] == "normalized"
            )
            stale_duplicate = root / "000-stale-normalized.wav"
            stale_duplicate.write_bytes(outputs["normalized"].read_bytes())
            with CatalogStore(app.layout.catalog_database) as catalog:
                duplicate = catalog.add_track(
                    stale_duplicate,
                    roles=(CatalogRole.GENERATED_OUTPUT,),
                )
                self.assertEqual(duplicate.track_id, normalized_track_id)
                stale_duplicate.unlink()
                self.assertEqual(
                    Path(catalog.preferred_location(normalized_track_id).path),
                    outputs["normalized"],
                    "media lookup must skip stale duplicate locations",
                )

            outputs["limited"].unlink()
            source = app.master_library()["sources"][0]
            version = source["versions"][0]
            by_mode = {deliverable["mode"]: deliverable for deliverable in version["deliverables"]}
            self.assertEqual(source["status"], "mastered")
            self.assertEqual(source["active_version_count"], 1)
            self.assertEqual(source["automatic_ab"]["b"]["mode"], "normalized")
            self.assertEqual(version["library_status"], "available")
            self.assertEqual(version["preferred_audition"]["mode"], "normalized")
            self.assertFalse(by_mode["limited"]["playable"])
            self.assertFalse(by_mode["limited"]["preferred"])
            self.assertTrue(by_mode["normalized"]["playable"])
            self.assertTrue(by_mode["normalized"]["preferred"])
            self.assertFalse(
                version["can_discard"],
                "discard remains all-or-nothing when one deliverable is missing",
            )

            missing_export = app.export_master_versions(
                str((root / "missing-export").resolve()),
                "-copy",
                [
                    {
                        "source_track_id": target,
                        "version_id": version_id,
                        "artifact_ordinal": by_mode["limited"]["ordinal"],
                    }
                ],
            )
            self.assertEqual(missing_export["success_count"], 0)
            fallback_export = app.export_master_versions(
                str((root / "fallback-export").resolve()),
                "-copy",
                [
                    {
                        "source_track_id": target,
                        "version_id": version_id,
                    }
                ],
            )
            self.assertEqual(fallback_export["success_count"], 1)
            self.assertEqual(
                Path(fallback_export["results"][0]["source_path"]),
                outputs["normalized"],
            )

            outputs["normalized"].unlink()
            outputs["raw"].unlink()
            source = app.master_library()["sources"][0]
            version = source["versions"][0]
            self.assertEqual(source["status"], "unavailable")
            self.assertEqual(source["active_version_count"], 0)
            self.assertIsNone(source["automatic_ab"])
            self.assertEqual(version["library_status"], "unavailable")
            self.assertIsNone(version["preferred_audition"])
            self.assertTrue(
                all(
                    not deliverable["playable"] and not deliverable["preferred"]
                    for deliverable in version["deliverables"]
                )
            )
            unavailable_export = app.export_master_versions(
                str((root / "unavailable-export").resolve()),
                "-copy",
                [
                    {
                        "source_track_id": target,
                        "version_id": version_id,
                    }
                ],
            )
            self.assertEqual(unavailable_export["success_count"], 0)
            with self.assertRaises(ManifestError):
                app.discard_master_version(target, version_id)
            self.assertFalse((app.layout.root / "trash").exists())
            app.close()

    def test_master_batch_export_sanitizes_names_copies_and_never_overwrites(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_tone(root / "original.wav", frequency=330),
                "target",
                "Artist / Song?",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )
            version = self._complete_master_version(
                app,
                _job_request(target, [(reference, 1.0, 1.0)]),
                frequency=440,
            )
            library = app.master_library()
            preferred = library["sources"][0]["versions"][0]["preferred_audition"]
            original_master = Path(preferred["path"])
            original_bytes = original_master.read_bytes()
            destination = root / "delivery"
            selection = {
                "source_track_id": target,
                "version_id": version,
            }

            result = app.export_master_versions(
                str(destination.resolve()),
                " <FINAL>?",
                [selection, selection],
            )
            self.assertEqual(result["sanitized_suffix"], "-FINAL--")
            self.assertEqual(result["success_count"], 1)
            self.assertEqual(result["failure_count"], 1)
            exported = Path(result["results"][0]["destination_path"])
            self.assertEqual(exported.name, "Artist - Song--FINAL--.wav")
            self.assertEqual(exported.read_bytes(), original_bytes)
            self.assertEqual(original_master.read_bytes(), original_bytes)
            self.assertIn("already exists", result["results"][1]["error"]["message"])

            repeat = app.export_master_versions(
                str(destination.resolve()),
                " <FINAL>?",
                [selection],
            )
            self.assertEqual(repeat["success_count"], 0)
            self.assertEqual(exported.read_bytes(), original_bytes)
            app.close()

    def test_discard_and_restore_master_version_preserve_sources_and_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target_path = write_tone(root / "target.wav", frequency=330)
            reference_path = write_tone(root / "reference.wav", frequency=660)
            target = self._add(app, target_path, "target", "Target")
            reference = self._add(app, reference_path, "reference", "Reference")
            version = self._complete_master_version(
                app,
                _job_request(target, [(reference, 1.0, 1.0)]),
                frequency=440,
            )
            before = app.master_library()["sources"][0]["versions"][0]
            original_paths = {
                item["ordinal"]: Path(item["path"]) for item in before["deliverables"]
            }
            manifest_path = Path(app.load_run_manifest(version)["catalog"]["database"]).parent
            run_manifest = next(
                Path(item["path"])
                for item in app.list_run_artifacts(version)
                if item["metadata"]["manifest_role"] == "run-manifest"
            )
            result_preview = next(
                Path(item["path"])
                for item in app.list_run_artifacts(version)
                if item["metadata"]["manifest_role"] == "result-preview"
            )

            discarded = app.discard_master_version(target, version)
            self.assertEqual(discarded["status"], "discarded")
            self.assertTrue(Path(discarded["tombstone_path"]).is_file())
            self.assertTrue(run_manifest.is_file())
            self.assertTrue(result_preview.is_file())
            self.assertIn(
                "result-preview",
                {item["metadata"]["manifest_role"] for item in discarded["preserved"]},
            )
            self.assertTrue(manifest_path.is_dir())
            self.assertTrue(target_path.is_file())
            self.assertTrue(reference_path.is_file())
            self.assertTrue(all(not path.exists() for path in original_paths.values()))
            self.assertTrue(
                all(Path(item["quarantine_path"]).is_file() for item in discarded["moved"])
            )

            hidden = app.master_library()["sources"][0]
            self.assertEqual(hidden["status"], "discarded-only")
            self.assertEqual(hidden["versions"], [])
            visible = app.master_library(include_discarded=True)["sources"][0]
            self.assertEqual(visible["versions"][0]["library_status"], "discarded")
            self.assertTrue(visible["versions"][0]["can_restore"])
            verified = app.verify_tracks()
            self.assertGreaterEqual(verified["states"].get("archived", 0), 3)
            self.assertTrue(
                app.master_library(include_discarded=True)["sources"][0]["versions"][0][
                    "can_restore"
                ],
                "Verify All must preserve intentional quarantine lifecycle states",
            )

            occupied = original_paths[min(original_paths)]
            occupied.write_bytes(b"operator-owned replacement")
            with self.assertRaisesRegex(FileExistsError, "nothing was overwritten"):
                app.restore_master_version(target, version)
            self.assertEqual(occupied.read_bytes(), b"operator-owned replacement")
            self.assertTrue(
                all(Path(item["quarantine_path"]).is_file() for item in discarded["moved"])
            )
            occupied.unlink()
            restored = app.restore_master_version(target, version)
            self.assertEqual(restored["status"], "available")
            self.assertTrue(Path(restored["audit_path"]).is_file())
            self.assertTrue(all(path.is_file() for path in original_paths.values()))
            self.assertTrue(
                all(not Path(item["quarantine_path"]).exists() for item in restored["moved"])
            )
            active = app.master_library()["sources"][0]
            self.assertEqual(active["status"], "mastered")
            self.assertIsNotNone(active["automatic_ab"])
            app.close()

    def test_discard_refuses_output_path_shared_by_another_completed_lineage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_tone(root / "target.wav", frequency=330),
                "target",
                "Target",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )
            version = self._complete_master_version(
                app,
                _job_request(target, [(reference, 1.0, 1.0)]),
                frequency=440,
            )
            with CatalogStore(app.layout.catalog_database) as catalog:
                selected_run = catalog.get_run(version)
                selection = catalog.get_run_selection(version)
                if selection is None:
                    self.fail("fixture run did not persist its selection")
                shared = next(
                    item
                    for item in catalog.list_run_artifacts(version)
                    if item.metadata.get("manifest_role") == "mastered-output"
                )
                catalog.record_run(
                    "shared-completed-lineage",
                    "completed",
                    selection=selection,
                    manifest_path=selected_run.manifest_path,
                    manifest_sha256=selected_run.manifest_sha256,
                )
                catalog.index_artifact(
                    "shared-completed-lineage",
                    shared.path,
                    role=CatalogRole.GENERATED_OUTPUT,
                    track_id=shared.track_id,
                    media_type=shared.media_type,
                    metadata={
                        "manifest_role": "mastered-output",
                        "mode": "limited",
                    },
                )

            with self.assertRaisesRegex(Exception, "shared by another active version"):
                app.discard_master_version(target, version)
            self.assertTrue(Path(shared.path).is_file())
            self.assertFalse((app.layout.root / "trash").exists())
            app.close()

    def test_strict_preferences_persist_atomically_and_feed_job_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            app = PortalApplication(workspace)

            defaults = app.get_preferences()
            self.assertEqual(defaults["kind"], PORTAL_PREFERENCES_KIND)
            self.assertEqual(
                defaults["schema_version"],
                PORTAL_PREFERENCES_SCHEMA_VERSION,
            )
            self.assertEqual(
                defaults["default_output_directory"],
                str(app.layout.outputs),
            )
            self.assertFalse(app.preferences_path.exists())

            delivery = root / "finished masters"
            delivery.mkdir()
            updated_document = {
                "kind": PORTAL_PREFERENCES_KIND,
                "schema_version": PORTAL_PREFERENCES_SCHEMA_VERSION,
                "default_output_directory": str(delivery),
            }
            updated = app.update_preferences(updated_document)
            self.assertEqual(updated, updated_document)
            self.assertEqual(app.bootstrap()["preferences"], updated_document)
            self.assertEqual(app.job_defaults()["preferences"], updated_document)

            other_delivery = root / "do not publish"
            other_delivery.mkdir()
            failed_document = {
                **updated_document,
                "default_output_directory": str(other_delivery),
            }
            with mock.patch(
                "music_mastering_tools.portal_app.os.replace",
                side_effect=OSError("injected preferences publication failure"),
            ):
                with self.assertRaises(OSError):
                    app.update_preferences(failed_document)
            self.assertEqual(app.get_preferences(), updated_document)
            self.assertEqual(
                list(app.preferences_path.parent.glob(f".{app.preferences_path.name}.*.tmp")),
                [],
            )

            with self.assertRaisesRegex(ValueError, "unexpected fields"):
                app.update_preferences({**updated_document, "extra": True})
            with self.assertRaisesRegex(ValueError, "schema_version"):
                app.update_preferences({**updated_document, "schema_version": 2})
            with self.assertRaisesRegex(ValueError, "absolute path"):
                app.update_preferences(
                    {
                        **updated_document,
                        "default_output_directory": "relative-output",
                    }
                )
            app.close()

            reopened = PortalApplication(workspace)
            self.assertEqual(reopened.get_preferences(), updated_document)
            duplicate_document = (
                "{"
                f'"kind": {json.dumps(PORTAL_PREFERENCES_KIND)},'
                '"schema_version": 1,'
                '"schema_version": 1,'
                f'"default_output_directory": {json.dumps(str(delivery))}'
                "}\n"
            )
            reopened.preferences_path.write_text(
                duplicate_document,
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate portal preferences"):
                reopened.get_preferences()
            reopened.close()

    def test_catalog_reference_set_export_import_and_archive_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target_path = write_tone(root / "target.wav", frequency=330)
            reference_a_path = write_tone(root / "reference-a.wav", frequency=550)
            reference_b_path = write_tone(root / "reference-b.wav", frequency=770)

            target = self._add(app, target_path, "target", "Target mix")
            reference_a = self._add(
                app,
                reference_a_path,
                "reference",
                "Reference A",
            )
            reference_b = self._add(
                app,
                reference_b_path,
                "reference",
                "Reference B",
            )

            bootstrap = app.bootstrap()
            self.assertEqual(bootstrap["catalog"]["track_count"], 3)
            self.assertTrue(bootstrap["privacy"]["local_only"])
            listed = app.list_tracks(query="reference a")
            self.assertEqual([item["track_id"] for item in listed], [reference_a])
            detail = app.get_track(target)
            self.assertEqual(detail["label"], "Target mix")
            self.assertEqual(detail["locations"][0]["state"], "available")

            archived = app.set_track_archived(reference_b, archived=True)
            self.assertTrue(archived["archived"])
            restored = app.set_track_archived(reference_b, archived=False)
            self.assertFalse(restored["archived"])
            verification = app.verify_tracks()
            self.assertEqual(verification["states"], {"available": 3})

            reference_set = app.create_reference_set("Initial blend")
            replaced = app.replace_reference_set_members(
                reference_set["set_id"],
                (
                    {
                        "track_id": reference_a,
                        "level_weight": 1.0,
                        "frequency_weight": 3.0,
                    },
                    {
                        "track_id": reference_b,
                        "level_weight": 3.0,
                        "frequency_weight": 1.0,
                    },
                ),
            )
            self.assertEqual(len(replaced["members"]), 2)
            self.assertEqual(
                [item["normalized_level_weight"] for item in replaced["members"]],
                [0.25, 0.75],
            )
            renamed = app.rename_reference_set(
                reference_set["set_id"],
                "Balanced blend",
            )
            self.assertEqual(renamed["name"], "Balanced blend")

            selection_path = root / "selection.json"
            selection_export = app.export_selection(
                _job_request(
                    target,
                    [
                        (reference_a, 1.0, 3.0),
                        (reference_b, 3.0, 1.0),
                    ],
                ),
                str(selection_path),
            )
            self.assertEqual(selection_export["reference_count"], 2)
            self.assertTrue(selection_path.is_file())

            deleted = app.delete_reference_set(reference_set["set_id"])
            self.assertEqual(deleted["name"], "Balanced blend")
            self.assertEqual(app.list_reference_sets(), [])

            export_path = root / "catalog.json"
            exported = app.export_catalog(str(export_path))
            self.assertEqual(exported["path"], str(export_path.resolve()))
            restored_app = PortalApplication(root / "restored-workspace")
            imported = restored_app.import_catalog(str(export_path))
            self.assertEqual(imported["kind"], "catalog")
            self.assertEqual(len(restored_app.list_tracks()), 3)

            selection_app = PortalApplication(root / "selection-workspace")
            imported_selection = selection_app.import_catalog(str(selection_path))
            self.assertEqual(imported_selection["kind"], "selection")
            selection_payload = cast(dict[str, Any], imported_selection["selection"])
            self.assertEqual(selection_payload["target"]["track_id"], target)
            self.assertEqual(
                [
                    (
                        item["track_id"],
                        item["level_weight"],
                        item["frequency_weight"],
                    )
                    for item in selection_payload["references"]
                ],
                [
                    (reference_a, 1.0, 3.0),
                    (reference_b, 3.0, 1.0),
                ],
            )
            self.assertEqual(len(selection_app.list_tracks()), 3)

            export_path.write_text("prior complete export\n", encoding="utf-8")
            with mock.patch(
                "music_mastering_tools.portal_app.os.replace",
                side_effect=OSError("injected publication failure"),
            ):
                with self.assertRaises(OSError):
                    app.export_catalog(str(export_path), overwrite=True)
            self.assertEqual(
                export_path.read_text(encoding="utf-8"),
                "prior complete export\n",
            )
            self.assertEqual(
                list(export_path.parent.glob(f".{export_path.name}.*.tmp")),
                [],
            )
            app.export_catalog(str(export_path), overwrite=True)
            self.assertIn(
                "music-mastering-tools/catalog",
                export_path.read_text(encoding="utf-8"),
            )

            selection_app.close()
            restored_app.close()
            app.close()

    def test_single_and_weighted_job_validation_use_managed_paths_and_engines(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_tone(root / "target.wav", frequency=330),
                "target",
                "Target",
            )
            reference_a = self._add(
                app,
                write_tone(root / "reference-a.wav", frequency=550),
                "reference",
                "Reference A",
            )
            reference_b = self._add(
                app,
                write_tone(root / "reference-b.wav", frequency=770),
                "reference",
                "Reference B",
            )

            single = app.validate_job_request(_job_request(target, [(reference_a, 9.0, 4.0)]))
            self.assertTrue(single["report"]["ok"])
            self.assertEqual(single["prepared"]["engine"], "upstream-matchering-2.0.6")
            self.assertFalse(single["prepared"]["materialized"])
            self.assertEqual(single["job"]["references"][0]["level_weight"], 1.0)
            self.assertEqual(single["job"]["references"][0]["frequency_weight"], 1.0)
            for key in (
                "configuration_path",
                "selection_path",
                "manifest_path",
                "event_log_path",
            ):
                self.assertFalse(Path(single["prepared"][key]).exists())

            target_ids_request = _job_request(
                target,
                [(reference_a, 1.0, 1.0)],
            )
            target_ids_request.pop("target_id")
            target_ids_request["target_ids"] = [target]
            target_ids_single = app.validate_job_request(target_ids_request)
            self.assertEqual(
                target_ids_single["selection"]["target"]["track_id"],
                target,
            )

            weighted = app.validate_job_request(
                _job_request(
                    target,
                    [
                        (reference_a, 1.0, 3.0),
                        (reference_b, 3.0, 1.0),
                    ],
                )
            )
            self.assertTrue(weighted["report"]["ok"])
            self.assertEqual(
                weighted["prepared"]["engine"],
                "music-mastering-tools-native",
            )
            self.assertEqual(
                [item["level_weight"] for item in weighted["selection"]["references"]],
                [1.0, 3.0],
            )
            for path in weighted["prepared"]["outputs"]:
                self.assertTrue(Path(path).is_relative_to(app.layout.outputs))
            self.assertTrue(
                Path(weighted["prepared"]["manifest_path"]).is_relative_to(app.layout.runs)
            )
            self.assertFalse(any(app.layout.jobs.iterdir()))
            self.assertFalse(any(app.layout.runs.iterdir()))
            self.assertFalse(any(app.layout.outputs.iterdir()))
            self.assertEqual(app.job_defaults()["limits"]["maximum_references"], 32)
            self.assertEqual(
                app.job_defaults()["limits"]["maximum_targets"],
                MAX_PORTAL_TARGETS,
            )
            self.assertFalse(
                app.capabilities()["upstream-matchering-2.0.6"]["independent_reference_weights"]
            )
            app.close()

    def test_batch_validation_checks_all_targets_without_materializing_and_export_rejects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target_a = self._add(
                app,
                write_tone(root / "target-a.wav", frequency=330),
                "target",
                "First Mix",
            )
            target_b = self._add(
                app,
                write_tone(root / "target-b.wav", frequency=440),
                "target",
                "Second Mix",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )
            preferred_delivery = root / "preferred delivery"
            app.update_preferences(
                {
                    "kind": PORTAL_PREFERENCES_KIND,
                    "schema_version": PORTAL_PREFERENCES_SCHEMA_VERSION,
                    "default_output_directory": str(preferred_delivery),
                }
            )
            default_result = app.validate_job_request(
                _job_request(target_a, [(reference, 1.0, 1.0)])
            )
            self.assertEqual(
                Path(default_result["prepared"]["output_root"]),
                preferred_delivery.resolve(),
            )
            self.assertFalse(preferred_delivery.exists())

            delivery = root / "new delivery folder"
            request = _job_request(target_a, [(reference, 1.0, 1.0)])
            request["target_ids"] = [target_a, target_b]
            request["output_directory"] = str(delivery)

            result = app.validate_job_request(request)
            self.assertEqual(result["kind"], "batch-validation")
            self.assertEqual(
                result["batch"],
                {
                    "target_count": 2,
                    "valid_count": 2,
                    "invalid_count": 0,
                    "ok": True,
                },
            )
            self.assertEqual(
                [item["target_id"] for item in result["targets"]],
                [target_a, target_b],
            )
            output_directories: list[Path] = []
            for item in result["targets"]:
                self.assertTrue(item["ok"], item.get("report"))
                self.assertFalse(item["prepared"]["materialized"])
                self.assertEqual(
                    Path(item["prepared"]["output_root"]),
                    delivery.resolve(),
                )
                output_directory = Path(item["prepared"]["output_directory"])
                output_directories.append(output_directory)
                self.assertEqual(output_directory.parent, delivery.resolve())
                self.assertIn("-master-", output_directory.name)
                self.assertFalse(output_directory.exists())
                for output in item["prepared"]["outputs"]:
                    self.assertIn("-mastered-", Path(output).name)
                    self.assertFalse(Path(output).exists())
            self.assertEqual(len(set(output_directories)), 2)
            self.assertFalse(delivery.exists())
            self.assertEqual(list(app.layout.jobs.iterdir()), [])
            self.assertEqual(list(app.layout.runs.iterdir()), [])
            self.assertEqual(list(app.layout.outputs.iterdir()), [])

            export_path = root / "batch-selection.json"
            with self.assertRaisesRegex(
                ValueError,
                "export each batch target as its own selection",
            ):
                app.export_selection(request, str(export_path))
            self.assertFalse(export_path.exists())

            with self.assertRaisesRegex(ValueError, "duplicate target"):
                app.validate_job_request({**request, "target_ids": [target_a, target_a]})
            with self.assertRaisesRegex(ValueError, "more than 32"):
                app.validate_job_request(
                    {
                        **request,
                        "target_id": target_a,
                        "target_ids": [f"target-{index}" for index in range(33)],
                    }
                )
            with self.assertRaisesRegex(ValueError, "must match the first"):
                app.validate_job_request(
                    {
                        **request,
                        "target_id": target_b,
                        "target_ids": [target_a, target_b],
                    }
                )
            app.close()

    def test_materialization_never_reuses_or_deletes_an_existing_output_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_tone(root / "target.wav", frequency=330),
                "target",
                "Collision Mix",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )
            delivery = root / "delivery"
            collision_directory = delivery / "Collision-Mix-master-fixed-render-run"
            collision_directory.mkdir(parents=True)
            existing_output = collision_directory / "Collision-Mix-mastered-limited.wav"
            existing_output.write_bytes(b"irreplaceable existing master")
            request = _job_request(target, [(reference, 1.0, 1.0)])
            request["output_directory"] = str(delivery)

            unsafe_policy = _job_request(target, [(reference, 1.0, 1.0)])
            unsafe_settings = cast(dict[str, object], unsafe_policy["settings"])
            unsafe_edge_cases = cast(dict[str, object], unsafe_settings["edge_cases"])
            unsafe_edge_cases["existing_output"] = "allow"
            with self.assertRaisesRegex(ValueError, "never overwrite"):
                app.validate_job_request(unsafe_policy)

            input_as_directory = _job_request(target, [(reference, 1.0, 1.0)])
            input_as_directory["output_directory"] = str(root / "target.wav")
            with self.assertRaisesRegex(ValueError, "points to a file"):
                app.validate_job_request(input_as_directory)

            with mock.patch(
                "music_mastering_tools.portal_app._new_run_id",
                return_value="fixed-render-run",
            ):
                with self.assertRaises(FileExistsError):
                    app.prepare_job(request, purpose="render")
            self.assertEqual(
                existing_output.read_bytes(),
                b"irreplaceable existing master",
            )
            self.assertEqual(list(app.layout.jobs.iterdir()), [])
            self.assertEqual(list(app.layout.runs.iterdir()), [])
            app.close()

    def test_batch_dry_run_continues_after_failure_and_indexes_every_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            silent_target = self._add(
                app,
                write_silence(root / "silent-target.wav"),
                "target",
                "Silent Mix",
            )
            healthy_target = self._add(
                app,
                write_tone(root / "healthy-target.wav", frequency=330),
                "target",
                "Healthy Mix",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )
            delivery = root / "batch delivery"
            request = _job_request(silent_target, [(reference, 1.0, 1.0)])
            request["target_ids"] = [silent_target, healthy_target]
            request["output_directory"] = str(delivery)

            operation = app.start_job(request, dry_run=True)
            completed = PortalOperationManagerTests._wait_for(
                app.operations,
                operation.operation_id,
            )
            self.assertEqual(completed.state, "succeeded", completed.error)
            result = cast(dict[str, Any], completed.result)
            self.assertEqual(result["kind"], "batch-dry-run")
            self.assertEqual(
                result["batch"]["status"],
                "partial_failure",
            )
            self.assertEqual(result["batch"]["success_count"], 1)
            self.assertEqual(result["batch"]["failure_count"], 1)
            self.assertEqual(
                result["batch"]["failure_policy"],
                "continue-independent-targets",
            )
            self.assertEqual(
                [item["state"] for item in result["targets"]],
                ["failed", "succeeded"],
            )
            self.assertEqual(
                [item["target_id"] for item in result["targets"]],
                [silent_target, healthy_target],
            )
            failed_item, succeeded_item = result["targets"]
            self.assertEqual(failed_item["manifest"]["status"], "failed")
            self.assertEqual(
                failed_item["manifest"]["error"]["code"],
                "preflight_failed",
            )
            self.assertEqual(succeeded_item["manifest"]["status"], "completed")
            self.assertNotEqual(failed_item["run_id"], succeeded_item["run_id"])
            self.assertTrue(Path(failed_item["prepared"]["manifest_path"]).is_file())
            self.assertTrue(Path(succeeded_item["prepared"]["manifest_path"]).is_file())
            self.assertEqual(
                Path(failed_item["prepared"]["output_root"]),
                delivery.resolve(),
            )
            self.assertEqual(
                Path(succeeded_item["prepared"]["output_root"]),
                delivery.resolve(),
            )
            self.assertNotEqual(
                failed_item["prepared"]["output_directory"],
                succeeded_item["prepared"]["output_directory"],
            )

            runs = app.list_runs()
            self.assertEqual(len(runs), 2)
            self.assertEqual({run["status"] for run in runs}, {"completed", "failed"})
            self.assertTrue(
                {
                    "MMT-E-PORTAL-BATCH-TARGET-FAILED",
                    "MMT-I-PORTAL-BATCH-TARGET-COMPLETE",
                }.issubset({event["code"] for event in completed.events})
            )
            app.close()

    def test_background_dry_run_finishes_with_manifest_and_catalog_inventory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_tone(root / "target.wav", frequency=330),
                "target",
                "Target",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )

            operation = app.start_job(
                _job_request(target, [(reference, 1.0, 1.0)]),
                dry_run=True,
            )
            completed = PortalOperationManagerTests._wait_for(
                app.operations,
                operation.operation_id,
            )
            self.assertEqual(completed.state, "succeeded", completed.error)
            result = completed.result
            if result is None:
                self.fail("successful operation did not retain its result")
            manifest_path = Path(result["prepared"]["manifest_path"])
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(result["manifest"]["status"], "completed")
            configuration = json.loads(
                Path(result["prepared"]["configuration_path"]).read_text(encoding="utf-8")
            )
            self.assertTrue(configuration["execution"]["dry_run"])
            self.assertTrue(result["manifest"]["configuration"]["execution"]["dry_run"])
            runs = app.list_runs()
            self.assertEqual(len(runs), 1)
            artifacts = app.list_run_artifacts(runs[0]["run_id"])
            self.assertEqual(
                {item["metadata"]["manifest_role"] for item in artifacts},
                {
                    "target",
                    "reference[0]",
                    "job-config",
                    "catalog-selection",
                    "event-log",
                    "run-manifest",
                },
            )
            app.close()

    def test_failed_preflight_is_indexed_and_large_log_reads_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            target = self._add(
                app,
                write_silence(root / "silent-target.wav"),
                "target",
                "Silent target",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )

            operation = app.start_job(
                _job_request(target, [(reference, 1.0, 1.0)]),
                dry_run=True,
            )
            completed = PortalOperationManagerTests._wait_for(
                app.operations,
                operation.operation_id,
            )
            self.assertEqual(completed.state, "failed")
            runs = app.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "failed")
            manifest = app.load_run_manifest(runs[0]["run_id"])
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["error"]["code"], "preflight_failed")
            self.assertTrue(
                {
                    "job-config",
                    "catalog-selection",
                    "event-log",
                    "run-manifest",
                }.issubset(
                    {
                        item["metadata"]["manifest_role"]
                        for item in app.list_run_artifacts(runs[0]["run_id"])
                    }
                )
            )

            log_path = app.layout.logs / "large.log"
            log_path.write_bytes((b"A" * 300_000) + b"bounded-tail")
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("read_log must not read the whole file"),
            ):
                log = app.read_log("large.log", maximum_bytes=1024)
            self.assertTrue(log["truncated"])
            self.assertLessEqual(len(log["text"].encode("utf-8")), 1024)
            self.assertTrue(log["text"].endswith("bounded-tail"))
            app.close()

    def test_service_result_without_manifest_cleans_unindexed_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = mock.Mock()
            service.run.return_value = object()
            app = PortalApplication(
                root / "workspace",
                service_factory=lambda: service,
            )
            target = self._add(
                app,
                write_tone(root / "target.wav", frequency=440),
                "target",
                "Target",
            )
            reference = self._add(
                app,
                write_tone(root / "reference.wav", frequency=660),
                "reference",
                "Reference",
            )

            operation = app.start_job(
                _job_request(target, [(reference, 1.0, 1.0)]),
                dry_run=True,
            )
            completed = PortalOperationManagerTests._wait_for(
                app.operations,
                operation.operation_id,
            )
            self.assertEqual(completed.state, "failed")
            self.assertIn(
                "without an authoritative manifest",
                cast(dict[str, Any], completed.error)["message"],
            )
            self.assertEqual(app.list_runs(), [])
            for directory in (
                app.layout.jobs,
                app.layout.outputs,
                app.layout.runs,
                app.layout.temporary,
            ):
                with self.subTest(directory=directory):
                    self.assertEqual(list(directory.iterdir()), [])
            app.close()

    def test_catalog_selection_preserves_identical_content_diagnostic_override(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            shared_path = write_tone(root / "shared.wav", frequency=440)
            added = app.add_tracks(
                [str(shared_path)],
                roles=["target", "reference"],
                labels={str(shared_path): "Deliberately shared source"},
            )
            track_id = cast(str, added["results"][0]["track"]["track_id"])
            request = _job_request(track_id, [(track_id, 1.0, 1.0)])

            blocked = app.validate_job_request(request)
            self.assertFalse(blocked["report"]["ok"])
            self.assertTrue(
                any(
                    issue["code"] == "MMT-E-TARGET-REFERENCE-SAME-PATH"
                    for issue in blocked["report"]["issues"]
                )
            )

            settings = cast(dict[str, object], request["settings"])
            audio = cast(dict[str, object], settings["audio"])
            audio["allow_identical_target_and_reference"] = True
            allowed = app.validate_job_request(request)
            self.assertTrue(allowed["report"]["ok"], allowed["report"]["issues"])
            self.assertEqual(
                allowed["selection"]["target"]["track_id"],
                allowed["selection"]["references"][0]["track_id"],
            )
            app.close()

    def test_unavailable_tracks_have_no_preferred_or_set_member_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = PortalApplication(root / "workspace")
            reference_path = write_tone(root / "reference.wav", frequency=550)
            reference_id = self._add(
                app,
                reference_path,
                "reference",
                "Reference that will move",
            )
            reference_set = app.create_reference_set("Needs repair")
            app.replace_reference_set_members(
                reference_set["set_id"],
                (
                    {
                        "track_id": reference_id,
                        "level_weight": 1.0,
                        "frequency_weight": 1.0,
                    },
                ),
            )

            reference_path.unlink()
            verified = app.verify_tracks(reference_id)
            self.assertEqual(verified["states"], {"missing": 1})
            self.assertIsNone(app.get_track(reference_id)["preferred_path"])
            listed_set = app.list_reference_sets()[0]
            self.assertIsNone(listed_set["members"][0]["path"])

            with self.assertRaisesRegex(ValueError, "more than 32"):
                app.replace_reference_set_members(
                    reference_set["set_id"],
                    tuple(
                        {
                            "track_id": reference_id,
                            "level_weight": 1.0,
                            "frequency_weight": 1.0,
                        }
                        for _ in range(33)
                    ),
                )
            app.close()

    @staticmethod
    def _add(
        app: PortalApplication,
        path: Path,
        role: str,
        label: str,
    ) -> str:
        result = app.add_tracks(
            [str(path)],
            roles=[role],
            labels={str(path): label},
        )
        item = result["results"][0]
        if not item["ok"]:
            raise AssertionError(item["error"])
        return cast(str, item["track"]["track_id"])

    @staticmethod
    def _complete_master_version(
        app: PortalApplication,
        request: Mapping[str, Any],
        *,
        frequency: float,
    ) -> str:
        prepared = app.prepare_job(request, purpose="render")
        manifest = RunManifest.create(run_id=prepared.job.execution.job_id)
        manifest.add_input(
            ArtifactManifest.from_file(
                prepared.job.target,
                role="target",
                media_type="audio/wav",
            )
        )
        for index, reference in enumerate(prepared.job.references):
            manifest.add_input(
                ArtifactManifest.from_file(
                    reference.path,
                    role=f"reference[{index}]",
                    media_type="audio/wav",
                )
            )
        for index, output in enumerate(prepared.job.outputs):
            output_path = write_tone(
                Path(output.path),
                frequency=frequency + (index * 33),
            )
            manifest.add_output(
                ArtifactManifest.from_file(
                    output_path,
                    role="mastered-output",
                    media_type="audio/wav",
                    metadata={
                        "mode": output.mode.value,
                        "label": output.label,
                        "requested_subtype": output.subtype,
                    },
                )
            )
        preview_path = write_tone(
            prepared.output_directory / "result-preview.wav",
            frequency=frequency + 180,
            frames=2_048,
        )
        manifest.add_output(
            ArtifactManifest.from_file(
                preview_path,
                role="result-preview",
                media_type="audio/wav",
                metadata={"requested_subtype": "PCM_16"},
            )
        )
        manifest.mark_completed()
        manifest.save(prepared.manifest_path)
        with CatalogStore(app.layout.catalog_database) as catalog:
            finalize_catalog_run(
                catalog,
                prepared.selection,
                prepared.manifest_path,
                configuration_path=prepared.configuration_path,
                selection_path=prepared.selection_path,
            )
        return cast(str, prepared.job.execution.job_id)


if __name__ == "__main__":
    unittest.main()
