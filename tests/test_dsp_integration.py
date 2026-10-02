from __future__ import annotations

import importlib.util
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:
    import pytest

    pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.smoke]
except ImportError:
    pytestmark = ()

from music_mastering_tools.audio_probe import DefaultAudioProbe, WaveAudioProbe
from music_mastering_tools.config import OutputMode, OutputSpec, PreviewConfig
from music_mastering_tools.events import MemoryEventSink
from music_mastering_tools.manifest import RunManifest, RunStatus
from music_mastering_tools.service import MasteringService
from tests.helpers import make_job, write_tone

MATCHERING_DISCOVERABLE = importlib.util.find_spec("matchering") is not None


@unittest.skipUnless(
    MATCHERING_DISCOVERABLE,
    "Matchering 2.0.6 compatibility dependencies are not installed",
)
class UpstreamDspIntegrationTests(unittest.TestCase):
    def test_actual_upstream_render_is_finite_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root, job_id="dsp-smoke")
            job = replace(
                job,
                outputs=(
                    OutputSpec(
                        str(root / "limited.wav"),
                        subtype="PCM_24",
                        mode=OutputMode.LIMITED,
                        label="limited",
                    ),
                    OutputSpec(
                        str(root / "normalized.wav"),
                        subtype="PCM_24",
                        mode=OutputMode.NORMALIZED,
                        label="normalized",
                    ),
                    OutputSpec(
                        str(root / "raw.wav"),
                        subtype="FLOAT",
                        mode=OutputMode.RAW_FLOAT,
                        label="raw",
                    ),
                ),
            )
            manifest_path = root / "audit" / "manifest.json"
            event_path = root / "audit" / "events.jsonl"

            outcome = MasteringService().run(
                job,
                manifest_path=manifest_path,
                event_log_path=event_path,
                sink=MemoryEventSink(),
                dry_run=False,
            )

            output_facts = [DefaultAudioProbe().probe(output.path) for output in job.outputs]
            manifest = RunManifest.load(manifest_path)
            self.assertFalse(outcome.dry_run)
            self.assertEqual(manifest.status, RunStatus.COMPLETED)
            self.assertEqual(
                [facts.subtype for facts in output_facts],
                ["PCM_24", "PCM_24", "FLOAT"],
            )
            for facts in output_facts:
                self.assertEqual(facts.channels, 2)
                self.assertGreater(facts.frames, job.matching.fft_size)
                self.assertEqual(facts.non_finite_samples, 0)
                self.assertGreater(facts.peak, 0.0)
            self.assertLessEqual(output_facts[0].peak, 1.0)
            self.assertLessEqual(output_facts[1].peak, 1.0)
            self.assertEqual(manifest.dependencies["matchering"], "2.0.6")
            self.assertEqual(
                {
                    artifact.metadata.get("mode")
                    for artifact in manifest.outputs
                    if artifact.role == "mastered-output"
                },
                {
                    OutputMode.LIMITED.value,
                    OutputMode.NORMALIZED.value,
                    OutputMode.RAW_FLOAT.value,
                },
            )
            self.assertIn("engine_result", manifest.extensions)

    def test_preview_dimensions_are_cast_to_integer_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = 6 * 44_100
            target = write_tone(
                root / "target.wav",
                frequency=330.0,
                frames=frames,
            )
            reference = write_tone(
                root / "reference.wav",
                frequency=660.0,
                frames=frames,
            )
            job = make_job(
                root,
                target=target,
                reference=reference,
                job_id="preview-regression",
            )
            job = replace(
                job,
                preview=PreviewConfig(
                    enabled=True,
                    target_path=str(root / "target-preview.wav"),
                    result_path=str(root / "result-preview.wav"),
                    duration_seconds=5.5,
                    analysis_step_seconds=2.0,
                    fade_seconds=0.5,
                    fade_coefficient=8.0,
                ),
            )

            MasteringService().run(
                job,
                manifest_path=root / "audit" / "manifest.json",
                event_log_path=root / "audit" / "events.jsonl",
                sink=MemoryEventSink(),
            )

            target_preview = WaveAudioProbe().probe(Path(job.preview.target_path or ""))
            result_preview = WaveAudioProbe().probe(Path(job.preview.result_path or ""))
            expected_frames = int(job.preview.duration_seconds * job.audio.internal_sample_rate)
            self.assertEqual(target_preview.frames, expected_frames)
            self.assertEqual(result_preview.frames, expected_frames)
            self.assertEqual(result_preview.non_finite_samples, 0)

    def test_mono_target_is_explicitly_rendered_as_stereo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(
                root / "mono-target.wav",
                frequency=330.0,
                channels=1,
            )
            reference = write_tone(
                root / "stereo-reference.wav",
                frequency=660.0,
                channels=2,
            )
            job = make_job(
                root,
                target=target,
                reference=reference,
                job_id="mono-upmix-regression",
            )

            outcome = MasteringService().run(
                job,
                manifest_path=root / "audit" / "manifest.json",
                event_log_path=root / "audit" / "events.jsonl",
                sink=MemoryEventSink(),
            )

            facts = DefaultAudioProbe().probe(job.outputs[0].path)
            self.assertEqual(facts.channels, 2)
            self.assertEqual(facts.non_finite_samples, 0)
            self.assertIn(
                "MMT-W-MONO-INPUT",
                {warning.code for warning in outcome.validation.warnings},
            )


if __name__ == "__main__":
    unittest.main()
