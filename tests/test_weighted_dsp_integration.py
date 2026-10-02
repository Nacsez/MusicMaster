from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    import pytest

    pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.smoke]
except ImportError:
    pytestmark = ()

from music_mastering_tools.audio_probe import DefaultAudioProbe
from music_mastering_tools.config import (
    EngineKind,
    OutputMode,
    OutputSpec,
    PreviewConfig,
    ReferenceSpec,
)
from music_mastering_tools.events import MemoryEventSink
from music_mastering_tools.manifest import RunManifest, RunStatus
from music_mastering_tools.service import MasteringService
from tests.helpers import make_job, write_tone

MATCHERING_DISCOVERABLE = importlib.util.find_spec("matchering") is not None


@unittest.skipUnless(
    MATCHERING_DISCOVERABLE,
    "Matchering 2.0.6 parity dependencies are not installed",
)
class WeightedNativeDspIntegrationTests(unittest.TestCase):
    def test_one_reference_native_raw_path_matches_upstream_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=340.0)
            reference = write_tone(root / "reference.wav", frequency=740.0)
            base = make_job(root, target=target, reference=reference)

            def render(*, engine: EngineKind, name: str) -> np.ndarray:
                job = replace(
                    base,
                    outputs=(
                        OutputSpec(
                            str(root / f"{name}.wav"),
                            subtype="FLOAT",
                            mode=OutputMode.RAW_FLOAT,
                        ),
                    ),
                    execution=replace(
                        base.execution,
                        engine=engine,
                        job_id=name,
                    ),
                )
                MasteringService().run(
                    job,
                    manifest_path=root / name / "manifest.json",
                    event_log_path=root / name / "events.jsonl",
                    sink=MemoryEventSink(),
                )
                audio, _sample_rate = sf.read(
                    job.outputs[0].path,
                    always_2d=True,
                )
                return np.asarray(audio)

            upstream_audio = render(
                engine=EngineKind.UPSTREAM,
                name="upstream-single",
            )
            native_audio = render(
                engine=EngineKind.NATIVE,
                name="native-single",
            )

            np.testing.assert_array_equal(upstream_audio, native_audio)

    def test_two_reference_render_is_finite_and_fully_inventoried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=330.0)
            first_reference = write_tone(root / "reference-a.wav", frequency=550.0)
            second_reference = write_tone(root / "reference-b.wav", frequency=990.0)
            job = make_job(
                root,
                target=target,
                reference=first_reference,
                job_id="weighted-finite",
            )
            job = replace(
                job,
                references=(
                    ReferenceSpec(
                        str(first_reference),
                        level_weight=1.0,
                        frequency_weight=3.0,
                        label="tone A",
                    ),
                    ReferenceSpec(
                        str(second_reference),
                        level_weight=3.0,
                        frequency_weight=1.0,
                        label="tone B",
                    ),
                ),
                outputs=(
                    OutputSpec(
                        str(root / "limited.wav"),
                        subtype="PCM_24",
                        mode=OutputMode.LIMITED,
                    ),
                    OutputSpec(
                        str(root / "normalized.wav"),
                        subtype="PCM_24",
                        mode=OutputMode.NORMALIZED,
                    ),
                    OutputSpec(
                        str(root / "raw.wav"),
                        subtype="FLOAT",
                        mode=OutputMode.RAW_FLOAT,
                    ),
                ),
                matching=replace(job.matching, max_eq_gain_db=18.0),
                execution=replace(
                    job.execution,
                    engine=EngineKind.NATIVE,
                ),
            )
            manifest_path = root / "audit" / "manifest.json"

            MasteringService().run(
                job,
                manifest_path=manifest_path,
                event_log_path=root / "audit" / "events.jsonl",
                sink=MemoryEventSink(),
            )

            manifest = RunManifest.load(manifest_path)
            self.assertEqual(manifest.status, RunStatus.COMPLETED)
            for output in job.outputs:
                facts = DefaultAudioProbe().probe(output.path)
                self.assertEqual(facts.channels, 2)
                self.assertEqual(facts.non_finite_samples, 0)
                self.assertGreater(facts.peak, 0)

            reference_artifacts = [
                artifact for artifact in manifest.inputs if artifact.role.startswith("reference[")
            ]
            self.assertEqual(len(reference_artifacts), 2)
            self.assertEqual(
                [artifact.metadata["label"] for artifact in reference_artifacts],
                ["tone A", "tone B"],
            )
            self.assertEqual(
                [artifact.metadata["normalized_level_weight"] for artifact in reference_artifacts],
                [0.25, 0.75],
            )
            engine_details = manifest.extensions["engine_result"]["details"]
            self.assertEqual(
                engine_details["algorithm_version"],
                "mmt-weighted-reference-profile-v1",
            )
            self.assertEqual(len(engine_details["reference_profiles"]), 2)
            self.assertIn("sha256", engine_details["plan"])

    def test_reference_order_does_not_change_rendered_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=310.0)
            first_reference = ReferenceSpec(
                str(write_tone(root / "reference-a.wav", frequency=470.0)),
                level_weight=1.0,
                frequency_weight=2.0,
            )
            second_reference = ReferenceSpec(
                str(write_tone(root / "reference-b.wav", frequency=1250.0)),
                level_weight=3.0,
                frequency_weight=1.0,
            )
            base = make_job(
                root,
                target=target,
                reference=Path(first_reference.path),
                job_id="weighted-forward",
            )

            def render(
                references: tuple[ReferenceSpec, ...],
                *,
                name: str,
            ) -> tuple[np.ndarray, str]:
                job = replace(
                    base,
                    references=references,
                    outputs=(
                        OutputSpec(
                            str(root / f"{name}.wav"),
                            subtype="FLOAT",
                            mode=OutputMode.RAW_FLOAT,
                        ),
                    ),
                    matching=replace(base.matching, max_eq_gain_db=18.0),
                    execution=replace(
                        base.execution,
                        engine=EngineKind.NATIVE,
                        job_id=name,
                    ),
                )
                manifest_path = root / name / "manifest.json"
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=root / name / "events.jsonl",
                    sink=MemoryEventSink(),
                )
                manifest = RunManifest.load(manifest_path)
                plan_sha256 = manifest.extensions["engine_result"]["details"]["plan"]["sha256"]
                audio, _sample_rate = sf.read(
                    job.outputs[0].path,
                    always_2d=True,
                )
                return np.asarray(audio), plan_sha256

            forward_audio, forward_plan = render(
                (first_reference, second_reference),
                name="forward",
            )
            reverse_audio, reverse_plan = render(
                (second_reference, first_reference),
                name="reverse",
            )

            self.assertEqual(forward_plan, reverse_plan)
            np.testing.assert_array_equal(forward_audio, reverse_audio)

    def test_duplicate_content_matches_explicitly_coalesced_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=350.0)
            first = write_tone(root / "reference-a.wav", frequency=610.0)
            duplicate = root / "reference-a-copy.wav"
            shutil.copyfile(first, duplicate)
            second = write_tone(root / "reference-b.wav", frequency=1070.0)
            base = make_job(root, target=target, reference=first)

            def render(
                references: tuple[ReferenceSpec, ...],
                *,
                name: str,
            ) -> tuple[np.ndarray, RunManifest]:
                job = replace(
                    base,
                    references=references,
                    outputs=(
                        OutputSpec(
                            str(root / f"{name}.wav"),
                            subtype="FLOAT",
                            mode=OutputMode.RAW_FLOAT,
                        ),
                    ),
                    matching=replace(base.matching, max_eq_gain_db=18.0),
                    execution=replace(
                        base.execution,
                        engine=EngineKind.NATIVE,
                        job_id=name,
                    ),
                )
                manifest_path = root / name / "manifest.json"
                MasteringService().run(
                    job,
                    manifest_path=manifest_path,
                    event_log_path=root / name / "events.jsonl",
                    sink=MemoryEventSink(),
                )
                audio, _sample_rate = sf.read(
                    job.outputs[0].path,
                    always_2d=True,
                )
                return np.asarray(audio), RunManifest.load(manifest_path)

            duplicated_audio, duplicated_manifest = render(
                (
                    ReferenceSpec(
                        str(first),
                        level_weight=1.0,
                        frequency_weight=1.0,
                    ),
                    ReferenceSpec(
                        str(duplicate),
                        level_weight=3.0,
                        frequency_weight=3.0,
                    ),
                    ReferenceSpec(
                        str(second),
                        level_weight=4.0,
                        frequency_weight=4.0,
                    ),
                ),
                name="duplicated",
            )
            coalesced_audio, coalesced_manifest = render(
                (
                    ReferenceSpec(
                        str(first),
                        level_weight=4.0,
                        frequency_weight=4.0,
                    ),
                    ReferenceSpec(
                        str(second),
                        level_weight=4.0,
                        frequency_weight=4.0,
                    ),
                ),
                name="coalesced",
            )

            np.testing.assert_array_equal(duplicated_audio, coalesced_audio)
            duplicated_details = duplicated_manifest.extensions["engine_result"]["details"]
            coalesced_details = coalesced_manifest.extensions["engine_result"]["details"]
            self.assertEqual(
                duplicated_details["plan"]["sha256"],
                coalesced_details["plan"]["sha256"],
            )
            self.assertEqual(
                duplicated_details["reference_inventory"]["duplicate_group_count"],
                1,
            )
            self.assertEqual(len(duplicated_manifest.inputs), 4)

    def test_weighted_previews_are_aligned_and_finite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = 6 * 44_100
            target = write_tone(
                root / "target.wav",
                frequency=320.0,
                frames=frames,
            )
            first = write_tone(
                root / "reference-a.wav",
                frequency=520.0,
                frames=frames,
            )
            second = write_tone(
                root / "reference-b.wav",
                frequency=920.0,
                frames=frames,
            )
            job = make_job(root, target=target, reference=first)
            job = replace(
                job,
                references=(
                    ReferenceSpec(str(first)),
                    ReferenceSpec(str(second)),
                ),
                outputs=(
                    OutputSpec(
                        str(root / "raw.wav"),
                        subtype="FLOAT",
                        mode=OutputMode.RAW_FLOAT,
                    ),
                ),
                matching=replace(job.matching, max_eq_gain_db=18.0),
                preview=PreviewConfig(
                    enabled=True,
                    target_path=str(root / "target-preview.wav"),
                    result_path=str(root / "result-preview.wav"),
                    duration_seconds=5.5,
                    analysis_step_seconds=2.0,
                    fade_seconds=0.5,
                    fade_coefficient=8.0,
                ),
                execution=replace(
                    job.execution,
                    engine=EngineKind.NATIVE,
                    job_id="weighted-preview",
                ),
            )

            MasteringService().run(
                job,
                manifest_path=root / "audit" / "manifest.json",
                event_log_path=root / "audit" / "events.jsonl",
                sink=MemoryEventSink(),
            )

            expected_frames = int(job.preview.duration_seconds * job.audio.internal_sample_rate)
            target_preview = DefaultAudioProbe().probe(job.preview.target_path or "")
            result_preview = DefaultAudioProbe().probe(job.preview.result_path or "")
            self.assertEqual(target_preview.frames, expected_frames)
            self.assertEqual(result_preview.frames, expected_frames)
            self.assertEqual(target_preview.frames, result_preview.frames)
            self.assertEqual(result_preview.non_finite_samples, 0)


if __name__ == "__main__":
    unittest.main()
