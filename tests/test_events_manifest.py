from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import pytest

    pytestmark = pytest.mark.unit
except ImportError:
    pytestmark = ()

from music_mastering_tools.errors import ManifestError, MusicMasteringError
from music_mastering_tools.events import (
    CompositeEventSink,
    JobEventEmitter,
    JsonlEventSink,
    MemoryEventSink,
)
from music_mastering_tools.manifest import (
    ArtifactManifest,
    ManifestStore,
    MetricManifest,
    RunManifest,
    RunStatus,
    fingerprint_file,
)


class EventAndManifestTests(unittest.TestCase):
    def test_job_events_are_sequenced_and_persisted_as_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            event_path = Path(temporary) / "events.jsonl"
            memory = MemoryEventSink()
            jsonl = JsonlEventSink(event_path, append=False)
            sinks = CompositeEventSink(memory, jsonl)
            emitter = JobEventEmitter("job-123", sinks)

            emitter.info("MMT-I-FIRST", "first", stage="test")
            emitter.warning("MMT-W-SECOND", "second", answer=42)
            sinks.close()

            self.assertEqual([event.sequence for event in memory.events], [0, 1])
            rows = [
                json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows[1]["context"]["answer"], 42)
            self.assertEqual(rows[1]["job_id"], "job-123")

    def test_manifest_round_trip_preserves_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.bin"
            source.write_bytes(b"deterministic audio stand-in")
            manifest_path = root / "manifest.json"
            fingerprint = fingerprint_file(source)
            manifest = RunManifest.create(run_id="run-1")
            manifest.add_input(ArtifactManifest.from_file(source, role="target"))
            manifest.mark_running()
            manifest.mark_completed()

            ManifestStore(manifest_path).save(manifest)
            loaded = ManifestStore(manifest_path).load()

            self.assertEqual(loaded.status, RunStatus.COMPLETED)
            self.assertEqual(loaded.inputs[0].fingerprint.sha256, fingerprint.sha256)

    def test_no_overwrite_store_rejects_preexisting_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text('{"belongs": "elsewhere"}\n', encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaises(ManifestError):
                ManifestStore(path, overwrite=False).save(RunManifest.create(run_id="new-run"))

            self.assertEqual(path.read_bytes(), before)

    def test_no_overwrite_store_detects_concurrent_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            store = ManifestStore(path, overwrite=False)
            manifest = RunManifest.create(run_id="owned-run")
            store.save(manifest)
            path.write_text('{"changed": true}\n', encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaisesRegex(ManifestError, "changed outside"):
                store.save(manifest)

            self.assertEqual(path.read_bytes(), before)

    def test_exclusive_event_sink_does_not_append_to_existing_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_text('{"belongs": "elsewhere"}\n', encoding="utf-8")
            before = path.read_bytes()
            sink = JsonlEventSink(path, append=False, exclusive=True)

            with self.assertRaises(FileExistsError):
                JobEventEmitter("new-job", sink).info("MMT-I-TEST", "test")

            self.assertEqual(path.read_bytes(), before)

    def test_structured_records_defensively_copy_and_deep_freeze_metadata(
        self,
    ) -> None:
        error_details = {"nested": {"items": ["original"]}}
        artifact_metadata = {"nested": {"items": ["original"]}}
        metric_metadata = {"nested": {"items": ["original"]}}
        configuration = {"nested": {"items": ["original"]}}

        error = MusicMasteringError("failure", details=error_details)
        artifact = ArtifactManifest(path="artifact.bin", metadata=artifact_metadata)
        metric = MetricManifest("peak", 0.5, metadata=metric_metadata)
        manifest = RunManifest.create(configuration=configuration)

        error_details["nested"]["items"].append("caller mutation")
        artifact_metadata["nested"]["items"].append("caller mutation")
        metric_metadata["nested"]["items"].append("caller mutation")
        configuration["nested"]["items"].append("caller mutation")

        self.assertEqual(error.to_dict()["details"]["nested"]["items"], ["original"])
        self.assertEqual(artifact.to_dict()["metadata"]["nested"]["items"], ["original"])
        self.assertEqual(metric.to_dict()["metadata"]["nested"]["items"], ["original"])
        self.assertEqual(
            manifest.to_dict()["configuration"]["nested"]["items"],
            ["original"],
        )
        with self.assertRaises(TypeError):
            artifact.metadata["new"] = True


if __name__ == "__main__":
    unittest.main()
