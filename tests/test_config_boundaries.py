from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.config import (
    AudioConfig,
    DetectionConfig,
    EdgeCasePolicy,
    ExecutionConfig,
    JobConfig,
    LimiterConfig,
    MatchingConfig,
    OutputSpec,
    PreviewConfig,
    ReferenceSpec,
    save_job_config,
)
from tests.helpers import make_job


class ConfigBoundaryTests(unittest.TestCase):
    def test_reference_and_output_boundaries(self) -> None:
        invalid = (
            lambda: ReferenceSpec(""),
            lambda: ReferenceSpec("ref.wav", level_weight=-1),
            lambda: ReferenceSpec("ref.wav", frequency_weight=float("nan")),
            lambda: ReferenceSpec("ref.wav", label=1),
            lambda: OutputSpec(""),
            lambda: OutputSpec("out.wav", subtype=""),
            lambda: OutputSpec("out.wav", mode="unknown"),
            lambda: OutputSpec("out.wav", dither="unknown"),
            lambda: OutputSpec("out.wav", label=1),
        )
        for constructor in invalid:
            with self.subTest(constructor=constructor), self.assertRaises((TypeError, ValueError)):
                constructor()

    def test_audio_and_matching_numeric_domains(self) -> None:
        invalid = (
            lambda: AudioConfig(internal_sample_rate=True),
            lambda: AudioConfig(internal_sample_rate=0),
            lambda: AudioConfig(max_length_seconds=float("inf")),
            lambda: AudioConfig(allow_identical_target_and_reference=1),
            lambda: AudioConfig(temp_directory=""),
            lambda: AudioConfig(metadata_policy="unknown"),
            lambda: MatchingConfig(amount=-0.1),
            lambda: MatchingConfig(amount=1.1),
            lambda: MatchingConfig(max_piece_seconds=0),
            lambda: MatchingConfig(fft_size=12),
            lambda: MatchingConfig(lin_log_oversampling=0),
            lambda: MatchingConfig(rms_correction_steps=-1),
            lambda: MatchingConfig(min_value=0),
            lambda: MatchingConfig(lowess_iterations=-1),
            lambda: MatchingConfig(lowess_delta=-1),
            lambda: MatchingConfig(max_eq_gain_db=0),
        )
        for constructor in invalid:
            with self.subTest(constructor=constructor), self.assertRaises(ValueError):
                constructor()

    def test_limiter_preview_detection_and_execution_domains(self) -> None:
        invalid = (
            lambda: LimiterConfig(threshold_linear=0),
            lambda: LimiterConfig(threshold_linear=1),
            lambda: LimiterConfig(true_peak_oversampling=0),
            lambda: LimiterConfig(attack_ms=0),
            lambda: LimiterConfig(hold_ms=0),
            lambda: LimiterConfig(release_ms=0),
            lambda: LimiterConfig(hold_filter_order=0),
            lambda: LimiterConfig(release_filter_order=0),
            lambda: LimiterConfig(external_command=("ok", "")),
            lambda: LimiterConfig(kind="external"),
            lambda: PreviewConfig(enabled=1),
            lambda: PreviewConfig(enabled=True),
            lambda: PreviewConfig(subtype=""),
            lambda: PreviewConfig(duration_seconds=5, analysis_step_seconds=5),
            lambda: PreviewConfig(fade_coefficient=1),
            lambda: DetectionConfig(silence_peak=-1),
            lambda: DetectionConfig(silence_peak=0.1, near_silence_peak=0.1),
            lambda: DetectionConfig(clipping_peak=0),
            lambda: DetectionConfig(clipping_samples_threshold=-1),
            lambda: DetectionConfig(limited_samples_threshold=0),
            lambda: EdgeCasePolicy(silence="unknown"),
            lambda: EdgeCasePolicy(create_output_directories=1),
            lambda: ExecutionConfig(max_workers=0),
            lambda: ExecutionConfig(job_id=""),
            lambda: ExecutionConfig(manifest_path=""),
            lambda: ExecutionConfig(event_log_path=""),
            lambda: ExecutionConfig(dry_run=1),
        )
        for constructor in invalid:
            with self.subTest(constructor=constructor), self.assertRaises((TypeError, ValueError)):
                constructor()

    def test_job_cross_field_and_collection_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = make_job(Path(temporary))
            invalid = (
                lambda: replace(job, schema_version=True),
                lambda: replace(job, schema_version=2),
                lambda: replace(job, references=[]),
                lambda: replace(job, outputs=[]),
                lambda: replace(job, references=()),
                lambda: replace(job, outputs=()),
                lambda: replace(
                    job,
                    references=(
                        replace(
                            job.references[0],
                            level_weight=0,
                            frequency_weight=0,
                        ),
                    ),
                ),
                lambda: replace(
                    job,
                    matching=replace(
                        job.matching,
                        max_piece_seconds=job.audio.max_length_seconds,
                    ),
                ),
                lambda: replace(job, audio="not audio"),
            )
            for constructor in invalid:
                with (
                    self.subTest(constructor=constructor),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    constructor()

    def test_config_save_creates_parent_and_rejects_non_json_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            path = root / "nested" / "job.json"
            save_job_config(job, path)
            self.assertTrue(path.is_file())
            self.assertIn('"schema_version": 1', path.read_text(encoding="utf-8"))

            invalid = replace(job, notes=None)
            object.__setattr__(invalid, "notes", float("nan"))
            with self.assertRaises((TypeError, ValueError)):
                invalid.to_json()

    def test_direct_job_config_requires_typed_children(self) -> None:
        with self.assertRaises(ValueError):
            JobConfig(
                target="target.wav",
                references=(ReferenceSpec("reference.wav"),),
                outputs=(OutputSpec("output.wav"),),
                matching="not matching",
            )


if __name__ == "__main__":
    unittest.main()
