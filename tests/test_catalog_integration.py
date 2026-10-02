"""Integration contracts between jobs, run manifests, and the catalog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from music_mastering_tools.audio_probe import AudioProbeUnavailable
from music_mastering_tools.catalog import (
    CatalogConflictError,
    CatalogRole,
    CatalogStore,
)
from music_mastering_tools.catalog_integration import (
    finalize_catalog_run,
    register_job_selection,
    selection_with_weights,
)
from music_mastering_tools.config import JobConfig, OutputSpec, ReferenceSpec
from music_mastering_tools.manifest import ArtifactManifest, RunManifest

from .helpers import write_tone


def _job(
    target: Path,
    references: tuple[ReferenceSpec, ...],
    output: Path,
) -> JobConfig:
    return JobConfig(
        target=str(target),
        references=references,
        outputs=(OutputSpec(str(output)),),
    )


class CatalogIntegrationTests(unittest.TestCase):
    def test_register_job_selection_coalesces_content_and_sums_independent_weights(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.bin"
            reference = root / "reference.bin"
            target.write_bytes(b"target")
            reference.write_bytes(b"reference")
            job = _job(
                target,
                (
                    ReferenceSpec(
                        str(reference),
                        level_weight=0.25,
                        frequency_weight=0.75,
                    ),
                    ReferenceSpec(
                        str(reference),
                        level_weight=0.75,
                        frequency_weight=0.25,
                    ),
                ),
                root / "output.wav",
            )

            with CatalogStore(root / "catalog.sqlite3") as catalog:
                selection = register_job_selection(
                    catalog,
                    job,
                    inspect_audio=False,
                )
                self.assertEqual(len(selection.references), 1)
                self.assertEqual(selection.references[0].level_weight, 1.0)
                self.assertEqual(selection.references[0].frequency_weight, 1.0)
                self.assertEqual(
                    len(catalog.list_tracks(role=CatalogRole.TARGET)),
                    1,
                )
                self.assertEqual(
                    len(catalog.list_tracks(role=CatalogRole.REFERENCE)),
                    1,
                )
                self.assertIsNone(catalog.get_track(selection.target.track_id).audio_facts)

    def test_register_job_selection_probes_audio_and_selection_weight_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=330)
            reference_a = write_tone(root / "reference-a.wav", frequency=550)
            reference_b = write_tone(root / "reference-b.wav", frequency=770)
            job = _job(
                target,
                (
                    ReferenceSpec(str(reference_a), label="Reference A"),
                    ReferenceSpec(str(reference_b), label="Reference B"),
                ),
                root / "output.wav",
            )
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                selection = register_job_selection(catalog, job)
                target_record = catalog.get_track(selection.target.track_id)
                self.assertIsNotNone(target_record.audio_facts)
                if target_record.audio_facts is None:
                    self.fail("audio facts unexpectedly absent")
                self.assertEqual(target_record.audio_facts.sample_rate, 44_100)
                self.assertEqual(target_record.audio_facts.channels, 2)

                weighted = selection_with_weights(
                    selection,
                    ((0.8, 0.2), (0.2, 0.8)),
                )
                self.assertEqual(
                    tuple(item.level_weight for item in weighted.references),
                    (0.8, 0.2),
                )
                self.assertEqual(
                    tuple(item.frequency_weight for item in weighted.references),
                    (0.2, 0.8),
                )
                self.assertEqual(
                    tuple(item.track.label for item in weighted.references),
                    ("Reference A", "Reference B"),
                )
                self.assertNotEqual(weighted.selection_id, selection.selection_id)
                self.assertEqual(
                    selection_with_weights(
                        selection,
                        ((value, value) for value in (1.0, 1.0)),
                    ),
                    selection,
                )

                with self.assertRaisesRegex(ValueError, "one .* pair"):
                    selection_with_weights(selection, ((1.0, 1.0),))
                with self.assertRaisesRegex(ValueError, "positive weight"):
                    selection_with_weights(
                        selection,
                        ((0.0, 0.0), (0.0, 0.0)),
                    )

    def test_finalize_augments_manifest_registers_output_and_indexes_inventory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=330)
            reference = write_tone(root / "reference.wav", frequency=660)
            output = write_tone(root / "master.wav", frequency=440)
            event_log = root / "events.jsonl"
            event_log.write_text("{}\n", encoding="utf-8")
            configuration = root / "job.json"
            configuration.write_text('{"schema_version": 1}\n', encoding="utf-8")
            manifest_path = root / "manifest.json"

            job = _job(
                target,
                (ReferenceSpec(str(reference)),),
                output,
            )
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                selection = register_job_selection(catalog, job)
                selection_path = root / "selection.json"
                selection_path.write_text(selection.to_json(), encoding="utf-8")

                manifest = RunManifest.create(run_id="catalog-finalize")
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
                        metadata={"effective_group_source_indices": [0]},
                    )
                )
                manifest.add_output(
                    ArtifactManifest.from_file(
                        output,
                        role="mastered-output",
                        media_type="audio/wav",
                    )
                )
                manifest.add_output(
                    ArtifactManifest.from_file(
                        event_log,
                        role="event-log",
                        media_type="application/x-ndjson",
                    )
                )
                manifest.mark_completed()
                manifest.save(manifest_path)

                finalized = finalize_catalog_run(
                    catalog,
                    selection,
                    manifest_path,
                    configuration_path=configuration,
                    selection_path=selection_path,
                )
                catalog_extension = finalized.extensions["catalog"]
                self.assertEqual(
                    catalog_extension["selection_id"],
                    selection.selection_id,
                )
                self.assertEqual(catalog_extension["catalog_id"], catalog.catalog_id)
                self.assertEqual(
                    catalog_extension["database"],
                    str(catalog.path),
                )
                self.assertTrue(catalog_extension["reimportable"])

                persisted = RunManifest.load(manifest_path)
                input_roles = [artifact.role for artifact in persisted.inputs]
                self.assertEqual(input_roles.count("job-config"), 1)
                self.assertEqual(input_roles.count("catalog-selection"), 1)
                indexed_run = catalog.get_run("catalog-finalize")
                self.assertEqual(indexed_run.status, "completed")
                self.assertEqual(indexed_run.selection_id, selection.selection_id)
                indexed_artifacts = catalog.list_run_artifacts("catalog-finalize")
                self.assertEqual(len(indexed_artifacts), 7)
                self.assertEqual(
                    {item.metadata["manifest_role"] for item in indexed_artifacts},
                    {
                        "target",
                        "reference[0]",
                        "job-config",
                        "catalog-selection",
                        "mastered-output",
                        "event-log",
                        "run-manifest",
                    },
                )
                indexed_reference = next(
                    item
                    for item in indexed_artifacts
                    if item.metadata["manifest_role"] == "reference[0]"
                )
                self.assertEqual(
                    indexed_reference.metadata["effective_group_source_indices"],
                    [0],
                )
                generated = catalog.list_tracks(role=CatalogRole.GENERATED_OUTPUT)
                self.assertEqual(len(generated), 1)
                self.assertIsNotNone(generated[0].audio_facts)

                finalize_catalog_run(
                    catalog,
                    selection,
                    manifest_path,
                    configuration_path=configuration,
                    selection_path=selection_path,
                )
                persisted_again = RunManifest.load(manifest_path)
                second_roles = [artifact.role for artifact in persisted_again.inputs]
                self.assertEqual(second_roles.count("job-config"), 1)
                self.assertEqual(second_roles.count("catalog-selection"), 1)
                self.assertEqual(
                    len(catalog.list_run_artifacts("catalog-finalize")),
                    7,
                )

    def test_finalize_without_optional_files_tolerates_unprobeable_audio_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.bin"
            reference = root / "reference.bin"
            output = root / "declared-audio.bin"
            target.write_bytes(b"target")
            reference.write_bytes(b"reference")
            output.write_bytes(b"not decodable audio")
            manifest_path = root / "manifest.json"
            job = _job(
                target,
                (ReferenceSpec(str(reference)),),
                output,
            )

            with CatalogStore(root / "catalog.sqlite3") as catalog:
                selection = register_job_selection(
                    catalog,
                    job,
                    inspect_audio=False,
                )
                manifest = RunManifest.create(run_id="unprobeable-output")
                manifest.add_input(
                    ArtifactManifest.from_file(
                        target,
                        role="target",
                        media_type="application/octet-stream",
                    )
                )
                manifest.add_input(
                    ArtifactManifest.from_file(
                        reference,
                        role="reference[0]",
                        media_type="application/octet-stream",
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

                with patch(
                    "music_mastering_tools.catalog_integration._audio_facts",
                    side_effect=AudioProbeUnavailable("unsupported"),
                ):
                    finalized = finalize_catalog_run(
                        catalog,
                        selection,
                        manifest_path,
                    )
                self.assertNotIn("job-config", [item.role for item in finalized.inputs])
                generated = catalog.list_tracks(role=CatalogRole.GENERATED_OUTPUT)
                self.assertEqual(len(generated), 1)
                self.assertIsNone(generated[0].audio_facts)
                self.assertEqual(
                    len(catalog.list_run_artifacts("unprobeable-output")),
                    4,
                )

    def test_finalize_rolls_back_output_registration_when_ingestion_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=330)
            reference = write_tone(root / "reference.wav", frequency=660)
            output = write_tone(root / "master.wav", frequency=440)
            manifest_path = root / "manifest.json"
            job = _job(
                target,
                (ReferenceSpec(str(reference)),),
                output,
            )

            with CatalogStore(root / "catalog.sqlite3") as catalog:
                selection = register_job_selection(catalog, job)
                manifest = RunManifest.create(run_id="atomic-finalize")
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

                with (
                    patch.object(
                        CatalogStore,
                        "ingest_manifest",
                        side_effect=CatalogConflictError("injected ingestion failure"),
                    ),
                    self.assertRaisesRegex(
                        CatalogConflictError,
                        "injected ingestion failure",
                    ),
                ):
                    finalize_catalog_run(catalog, selection, manifest_path)

                self.assertEqual(
                    catalog.list_tracks(role=CatalogRole.GENERATED_OUTPUT),
                    (),
                )
                self.assertEqual(catalog.list_runs(), ())


if __name__ == "__main__":
    unittest.main()
