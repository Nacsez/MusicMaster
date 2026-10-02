from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.errors import ErrorCode, EventSinkError, ManifestError
from music_mastering_tools.events import (
    CompositeEventSink,
    ConsoleEventSink,
    Event,
    EventLevel,
    JobEventEmitter,
    JsonlEventSink,
    MemoryEventSink,
    NullEventSink,
    new_event,
)
from music_mastering_tools.manifest import (
    ArtifactManifest,
    FileFingerprint,
    ManifestStore,
    MetricManifest,
    RunManifest,
    RunStatus,
    fingerprint_file,
)


class EventEdgeTests(unittest.TestCase):
    def test_event_coercion_round_trip_and_context_normalization(self) -> None:
        @dataclass
        class Diagnostic:
            answer: int

        timestamp = datetime(2026, 7, 27, 1, 2, 3)
        event = new_event(
            "MMT-I-NORMALIZED",
            "normalized",
            level="warn",
            job_id="job-1",
            stage="test",
            sequence=4,
            timestamp=timestamp,
            path=Path("private.wav"),
            moment=timestamp,
            values={3, 1},
            payload=b"\x01\xff",
            diagnostic=Diagnostic(42),
            exception=ValueError("example"),
            nan=float("nan"),
        )

        payload = event.to_dict()
        reconstructed = Event.from_dict(payload)

        self.assertEqual(event.level, EventLevel.WARNING)
        self.assertEqual(event.timestamp.tzinfo, UTC)
        self.assertEqual(payload["context"]["path"], "private.wav")
        self.assertEqual(payload["context"]["values"], [1, 3])
        self.assertEqual(
            payload["context"]["payload"],
            {"encoding": "hex", "value": "01ff"},
        )
        self.assertEqual(payload["context"]["diagnostic"], {"answer": 42})
        self.assertEqual(payload["context"]["nan"], "NaN")
        self.assertEqual(reconstructed.to_dict(), payload)

    def test_event_and_level_validation_errors_are_actionable(self) -> None:
        for value in ("unknown", 999, object()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EventLevel.coerce(value)
        for kwargs in (
            {"code": "", "message": "message"},
            {"code": "CODE", "message": ""},
            {"code": "CODE", "message": "message", "job_id": ""},
            {"code": "CODE", "message": "message", "stage": ""},
            {"code": "CODE", "message": "message", "sequence": -1},
            {"code": "CODE", "message": "message", "timestamp": "not-a-date"},
            {"code": "CODE", "message": "message", "context": []},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                Event(**kwargs)
        with self.assertRaisesRegex(ValueError, "ISO-8601"):
            Event.from_dict({"timestamp": "not-a-time"})
        with self.assertRaisesRegex(ValueError, "timestamp"):
            Event.from_dict({"timestamp": 1})
        with self.assertRaisesRegex(ValueError, "context"):
            Event.from_dict(
                {
                    "timestamp": "2026-07-27T00:00:00Z",
                    "code": "CODE",
                    "message": "message",
                    "context": [],
                }
            )

    def test_console_memory_null_and_job_emitters(self) -> None:
        human_stream = io.StringIO()
        human = ConsoleEventSink(
            human_stream,
            min_level=EventLevel.WARNING,
        )
        emitter = JobEventEmitter("job-console", human)
        emitter.info("MMT-I-HIDDEN", "hidden")
        emitter.warning("MMT-W-SHOWN", "shown", answer=42)
        self.assertNotIn("HIDDEN", human_stream.getvalue())
        self.assertIn("MMT-W-SHOWN", human_stream.getvalue())
        self.assertIn('"answer":42', human_stream.getvalue())

        json_stream = io.StringIO()
        json_sink = ConsoleEventSink(json_stream, json_lines=True, flush=False)
        JobEventEmitter("job-json", json_sink).error("MMT-E-JSON", "json")
        self.assertEqual(
            json.loads(json_stream.getvalue())["code"],
            "MMT-E-JSON",
        )

        memory = MemoryEventSink(max_events=2)
        bound = JobEventEmitter.from_sinks("job-memory", memory, NullEventSink())
        bound.debug("MMT-D-ONE", "one")
        bound.info("MMT-I-TWO", "two")
        bound.error("MMT-E-THREE", "three")
        self.assertEqual(
            [event.sequence for event in memory.events],
            [1, 2],
        )
        memory.clear()
        self.assertEqual(memory.events, ())

        with self.assertRaises(ValueError):
            MemoryEventSink(max_events=0)
        with self.assertRaises(TypeError):
            NullEventSink().emit("not an event")
        with self.assertRaises(ValueError):
            JobEventEmitter("", memory)
        with self.assertRaises(TypeError):
            JobEventEmitter("job", object())
        with self.assertRaises(ValueError):
            JobEventEmitter("job", memory, initial_sequence=-1)

    def test_composite_sink_failure_modes_and_context_close(self) -> None:
        class BrokenSink:
            def __init__(self) -> None:
                self.closed = False

            def emit(self, event: Event) -> None:
                raise OSError(f"cannot emit {event.code}")

            def close(self) -> None:
                self.closed = True
                raise OSError("cannot close")

        broken = BrokenSink()
        memory = MemoryEventSink()
        composite = CompositeEventSink([memory, broken])
        event = Event.create("MMT-I-TEST", "test")
        composite.emit(event)
        composite.close()
        self.assertEqual(memory.events, (event,))
        self.assertTrue(broken.closed)
        self.assertEqual(len(composite.failures), 2)
        self.assertEqual(composite.failures[0].sink_type, "BrokenSink")
        self.assertEqual(
            composite.failures[0].to_dict()["exception_type"],
            "OSError",
        )

        strict = CompositeEventSink(broken, strict=True)
        with self.assertRaises(EventSinkError):
            strict.emit(event)
        with self.assertRaises(EventSinkError):
            strict.close()
        with self.assertRaises(TypeError):
            CompositeEventSink([object()])

    def test_jsonl_context_reopen_and_argument_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "events.jsonl"
            sink = JsonlEventSink(
                path,
                append=False,
                sync_on_close=True,
            )
            with sink:
                sink.emit(Event.create("MMT-I-FIRST", "first"))
            sink.emit(Event.create("MMT-I-SECOND", "second"))
            sink.close()

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [row["code"] for row in rows],
                ["MMT-I-FIRST", "MMT-I-SECOND"],
            )
            with self.assertRaises(TypeError):
                sink.emit("not an event")
        with self.assertRaises(ValueError):
            JsonlEventSink("events.jsonl", append=True, exclusive=True)


class ManifestEdgeTests(unittest.TestCase):
    def test_fingerprint_contract_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.bin"
            path.write_bytes(b"stable bytes")
            fingerprint = fingerprint_file(path, chunk_size=3)
            reconstructed = FileFingerprint.from_dict(fingerprint.to_dict())

            self.assertEqual(reconstructed, fingerprint)
            self.assertEqual(fingerprint.digest, fingerprint.sha256)
            self.assertEqual(str(RunStatus.SUCCEEDED), "completed")
            with self.assertRaises(ValueError):
                fingerprint_file(path, chunk_size=0)
            with self.assertRaises(ManifestError) as missing:
                fingerprint_file(path.with_name("missing.bin"))
            self.assertEqual(missing.exception.code, ErrorCode.FINGERPRINT_FAILED)

        valid_digest = "a" * 64
        invalid_cases = (
            {"path": "", "size_bytes": 1, "sha256": valid_digest},
            {"path": "x", "size_bytes": -1, "sha256": valid_digest},
            {
                "path": "x",
                "size_bytes": 1,
                "sha256": valid_digest,
                "modified_ns": -1,
            },
            {"path": "x", "size_bytes": 1, "sha256": "bad"},
            {
                "path": "x",
                "size_bytes": 1,
                "sha256": valid_digest,
                "algorithm": "md5",
            },
        )
        for kwargs in invalid_cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FileFingerprint(**kwargs)

    def test_artifact_and_metric_round_trip_and_validation(self) -> None:
        artifact = ArtifactManifest(
            path="render.wav",
            role="master",
            name="private master",
            media_type="audio/wav",
            metadata={"mode": "limited"},
        )
        metric = MetricManifest(
            "peak",
            0.99,
            unit="linear",
            stage="render",
            recorded_at=datetime(2026, 7, 27),
            metadata={"channel": "all"},
        )
        self.assertEqual(
            ArtifactManifest.from_dict(artifact.to_dict()).to_dict(),
            artifact.to_dict(),
        )
        self.assertEqual(
            MetricManifest.from_dict(metric.to_dict()).to_dict(),
            metric.to_dict(),
        )
        self.assertEqual(metric.recorded_at.tzinfo, UTC)

        for constructor in (
            lambda: ArtifactManifest(path="", role="artifact"),
            lambda: ArtifactManifest(path="x", role="", metadata={}),
            lambda: ArtifactManifest(path="x", metadata=[]),
            lambda: MetricManifest("", 1),
            lambda: MetricManifest("metric", object()),
            lambda: MetricManifest("metric", float("inf")),
            lambda: MetricManifest("metric", 1, recorded_at="bad"),
            lambda: MetricManifest("metric", 1, metadata=[]),
        ):
            with self.subTest(constructor=constructor), self.assertRaises((TypeError, ValueError)):
                constructor()
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            ArtifactManifest.from_dict({"path": "x", "fingerprint": "not-an-object"})
        with self.assertRaisesRegex(ValueError, "metadata"):
            MetricManifest.from_dict({"name": "metric", "value": 1, "metadata": []})

    def test_run_manifest_lifecycle_extensions_and_error_forms(self) -> None:
        manifest = RunManifest.create(
            run_id="run-edge",
            tool_version="test",
            command=("mmt", "run"),
            configuration={"target": "private.wav"},
            environment={"mode": "test"},
        )
        artifact = ArtifactManifest(path="input.wav", role="target")
        metric = MetricManifest("duration", 1.0)
        manifest.add_input(artifact)
        manifest.add_output(artifact)
        manifest.add_metric(metric)
        manifest.add_warning("warning")
        manifest.extensions["private_extension"] = {"enabled": True}
        manifest.mark_running()
        manifest.mark_failed(ValueError("failed"))
        self.assertEqual(manifest.status, RunStatus.FAILED)
        self.assertEqual(manifest.error["exception_type"], "ValueError")
        manifest.mark_failed({"code": "custom", "message": "mapping"})
        self.assertEqual(manifest.error["code"], "custom")
        manifest.mark_failed("string failure")
        self.assertEqual(manifest.error["message"], "string failure")
        manifest.mark_completed()
        self.assertIsNone(manifest.error)
        self.assertIs(manifest.config, manifest.configuration)

        restored = RunManifest.from_dict(manifest.to_dict())
        self.assertEqual(restored.to_dict(), manifest.to_dict())
        self.assertEqual(
            restored.to_dict()["private_extension"],
            {"enabled": True},
        )

        for method, value in (
            (manifest.add_input, metric),
            (manifest.add_output, metric),
            (manifest.add_metric, artifact),
        ):
            with self.subTest(method=method), self.assertRaises(TypeError):
                method(value)
        with self.assertRaises(ValueError):
            manifest.add_warning("")

    def test_manifest_store_load_failures_and_convenience_methods(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "nested" / "manifest.json"
            manifest = RunManifest.create(run_id="run-save")
            self.assertEqual(manifest.save(path), path)
            self.assertEqual(RunManifest.load(path).run_id, "run-save")

            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaises(ManifestError) as invalid_json:
                ManifestStore(malformed).load()
            self.assertEqual(
                invalid_json.exception.code,
                ErrorCode.MANIFEST_READ_FAILED,
            )

            array = root / "array.json"
            array.write_text("[]", encoding="utf-8")
            with self.assertRaises(ManifestError) as invalid_root:
                ManifestStore(array).load()
            self.assertEqual(
                invalid_root.exception.code,
                ErrorCode.MANIFEST_INVALID,
            )

            with self.assertRaises(ManifestError):
                ManifestStore(root / "missing.json").load()
            with self.assertRaises(ManifestError):
                ManifestStore(
                    root / "absent" / "manifest.json",
                    create_parents=False,
                ).save(manifest)

        for indent in (-1, True):
            with self.subTest(indent=indent), self.assertRaises(ValueError):
                ManifestStore("manifest.json", indent=indent)
        with self.assertRaises(TypeError):
            ManifestStore("manifest.json").save(object())


if __name__ == "__main__":
    unittest.main()
