from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

try:
    import pytest

    pytestmark = pytest.mark.integration
except ImportError:
    pytestmark = ()

from music_mastering_tools.engine import EngineRunResult, UpstreamMatcheringEngine
from music_mastering_tools.errors import (
    EventSinkError,
    ManifestError,
    OutputCollisionError,
    PreflightError,
    ProcessingError,
)
from music_mastering_tools.events import JsonlEventSink, MemoryEventSink
from music_mastering_tools.manifest import ManifestStore, RunManifest, RunStatus
from music_mastering_tools.service import MasteringService
from tests.helpers import make_job, write_silence


class MasteringServiceTests(unittest.TestCase):
    def test_dry_run_writes_completed_manifest_and_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            manifest_path = root / "audit" / "manifest.json"
            event_path = root / "audit" / "events.jsonl"
            memory = MemoryEventSink()

            outcome = MasteringService().run(
                job,
                manifest_path=manifest_path,
                event_log_path=event_path,
                sink=memory,
                dry_run=True,
            )

            manifest = RunManifest.load(manifest_path)
            self.assertTrue(outcome.dry_run)
            self.assertEqual(manifest.status, RunStatus.COMPLETED)
            self.assertEqual(len(manifest.inputs), 2)
            self.assertIn("matchering", manifest.dependencies)
            self.assertTrue(event_path.is_file())
            self.assertIn(
                "MMT-I-DRY-RUN-COMPLETED",
                {event.code for event in memory.events},
            )

    def test_audit_collision_is_rejected_before_input_is_modified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            target = Path(job.target)
            before = hashlib.sha256(target.read_bytes()).hexdigest()

            with self.assertRaises(OutputCollisionError):
                MasteringService().run(
                    job,
                    manifest_path=target,
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            after = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(after, before)

    def test_audit_hardlink_collision_is_rejected_without_modifying_input(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            target = Path(job.target)
            manifest_alias = root / "manifest-hardlink.json"
            try:
                os.link(target, manifest_alias)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable on this filesystem: {exc}")
            before = hashlib.sha256(target.read_bytes()).hexdigest()

            with self.assertRaises(OutputCollisionError):
                MasteringService().run(
                    job,
                    manifest_path=manifest_alias,
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)

    def test_configuration_cannot_be_selected_as_render_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = root / "job.json"
            configuration.write_text('{"private": true}\n', encoding="utf-8")
            before = configuration.read_bytes()
            job = make_job(root, output=configuration)

            with self.assertRaises(OutputCollisionError):
                MasteringService().run(
                    job,
                    manifest_path=root / "audit" / "manifest.json",
                    configuration_path=configuration,
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            self.assertEqual(configuration.read_bytes(), before)

    def test_completed_audit_paths_are_never_reused_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            manifest_path = root / "audit" / "manifest.json"
            event_path = root / "audit" / "events.jsonl"
            service = MasteringService()
            service.run(
                job,
                manifest_path=manifest_path,
                event_log_path=event_path,
                dry_run=True,
                sink=MemoryEventSink(),
            )
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (manifest_path, event_path)
            }

            with self.assertRaises(OutputCollisionError):
                service.run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=event_path,
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            self.assertEqual(
                {
                    path: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (manifest_path, event_path)
                },
                before,
            )

    def test_real_render_cannot_bypass_audio_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            manifest_path = root / "audit" / "manifest.json"

            with self.assertRaises(PreflightError):
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    inspect_audio=False,
                    dry_run=False,
                    sink=MemoryEventSink(),
                )

            manifest = RunManifest.load(manifest_path)
            self.assertEqual(manifest.status, RunStatus.FAILED)
            self.assertFalse(manifest.environment["audio_probe_enabled"])

    def test_required_event_log_failure_fails_the_job_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            blocked_parent = root / "not-a-directory"
            blocked_parent.write_text("occupied", encoding="utf-8")
            manifest_path = root / "audit" / "manifest.json"

            with self.assertRaises(EventSinkError):
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=blocked_parent / "events.jsonl",
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            manifest = RunManifest.load(manifest_path)
            self.assertEqual(manifest.status, RunStatus.FAILED)
            self.assertIn("event_sink_failures", manifest.extensions)

    def test_event_log_close_failure_is_journaled_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            manifest_path = root / "audit" / "manifest.json"
            event_path = root / "audit" / "events.jsonl"
            original_close = JsonlEventSink.close
            close_calls = 0

            def fail_first_close(sink: JsonlEventSink) -> None:
                nonlocal close_calls
                close_calls += 1
                original_close(sink)
                if close_calls == 1:
                    raise OSError("injected sync failure")

            with (
                mock.patch.object(JsonlEventSink, "close", new=fail_first_close),
                self.assertRaises(EventSinkError),
            ):
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=event_path,
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            manifest = RunManifest.load(manifest_path)
            rows = [
                json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(manifest.status, RunStatus.FAILED)
            self.assertEqual(
                rows[-1]["code"],
                "MMT-E-AUDIT-LOG-FINALIZE-FAILED",
            )
            event_artifact = next(
                artifact for artifact in manifest.outputs if artifact.role == "event-log"
            )
            self.assertEqual(
                event_artifact.fingerprint.sha256,
                hashlib.sha256(event_path.read_bytes()).hexdigest(),
            )

    def test_manifest_commit_failure_gets_compensating_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            manifest_path = root / "audit" / "manifest.json"
            event_path = root / "audit" / "events.jsonl"
            original_save = ManifestStore.save
            save_calls = 0

            def fail_completed_commit(
                store: ManifestStore,
                manifest: RunManifest,
            ) -> Path:
                nonlocal save_calls
                save_calls += 1
                if save_calls == 3:
                    raise ManifestError("injected final commit failure")
                return original_save(store, manifest)

            with (
                mock.patch.object(
                    ManifestStore,
                    "save",
                    new=fail_completed_commit,
                ),
                self.assertRaises(ManifestError),
            ):
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=event_path,
                    dry_run=True,
                    sink=MemoryEventSink(),
                )

            manifest = RunManifest.load(manifest_path)
            rows = [
                json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(manifest.status, RunStatus.FAILED)
            self.assertEqual(rows[-1]["code"], "MMT-E-AUDIT-COMMIT-FAILED")
            event_artifact = next(
                artifact for artifact in manifest.outputs if artifact.role == "event-log"
            )
            self.assertEqual(
                event_artifact.fingerprint.sha256,
                hashlib.sha256(event_path.read_bytes()).hexdigest(),
            )

    def test_input_change_during_engine_run_blocks_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            manifest_path = root / "audit" / "manifest.json"

            class MutatingEngine:
                capabilities = UpstreamMatcheringEngine().capabilities

                def run(self, active_job, *, log_handler=None):
                    Path(active_job.target).write_bytes(b"changed during render")
                    Path(active_job.outputs[0].path).write_bytes(b"partial render")
                    started = datetime.now(UTC)
                    return EngineRunResult.completed(
                        engine_id=self.capabilities.engine_id,
                        engine_version=self.capabilities.engine_version,
                        output_paths=(active_job.outputs[0].path,),
                        started_at=started,
                        duration_seconds=0.01,
                    )

            with (
                mock.patch(
                    "music_mastering_tools.service.create_engine",
                    return_value=MutatingEngine(),
                ),
                self.assertRaises(ProcessingError),
            ):
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    dry_run=False,
                    sink=MemoryEventSink(),
                )

            manifest = RunManifest.load(manifest_path)
            self.assertEqual(manifest.status, RunStatus.FAILED)
            self.assertIn(
                "changed while the mastering engine was running",
                manifest.error["message"],
            )
            self.assertIn(
                "partial-output",
                {artifact.role for artifact in manifest.outputs},
            )

    def test_preflight_failure_is_persisted_for_automatic_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            silent = write_silence(root / "silent.wav")
            job = make_job(root, target=silent)
            manifest_path = root / "audit" / "manifest.json"

            with self.assertRaises(PreflightError):
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=root / "audit" / "events.jsonl",
                    sink=MemoryEventSink(),
                    dry_run=True,
                )

            manifest = RunManifest.load(manifest_path)
            self.assertEqual(manifest.status, RunStatus.FAILED)
            self.assertEqual(manifest.error["code"], "preflight_failed")
            self.assertFalse(manifest.extensions["validation"]["ok"])


if __name__ == "__main__":
    unittest.main()
