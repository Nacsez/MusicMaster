from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.audio_probe import WaveAudioProbe
from music_mastering_tools.config import EdgeCasePolicy, OutputMode, PolicyAction
from music_mastering_tools.validation import validate_job
from tests.helpers import make_job, write_silence, write_tone


class AudioValidationTests(unittest.TestCase):
    def test_wave_probe_measures_pcm_without_optional_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_tone(Path(temporary) / "tone.wav", amplitude=0.5)

            facts = WaveAudioProbe().probe(path)

            self.assertEqual(facts.backend, "wave")
            self.assertEqual(facts.sample_rate, 44_100)
            self.assertEqual(facts.channels, 2)
            self.assertGreater(facts.peak, 0.49)
            self.assertLessEqual(facts.peak, 0.5)
            self.assertEqual(facts.non_finite_samples, 0)

    def test_silence_is_blocking_before_upstream_log10_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            silent = write_silence(root / "silent.wav")
            job = make_job(root, target=silent)

            report = validate_job(job, probe=WaveAudioProbe())

            codes = {issue.code for issue in report.errors}
            self.assertIn("MMT-E-SILENCE", codes)

    def test_near_silence_is_blocking_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            near_silent = write_tone(root / "near-silent.wav", amplitude=1.0 / 32_767)
            job = make_job(root, target=near_silent)

            report = validate_job(job, probe=WaveAudioProbe())

            self.assertIn(
                "MMT-E-NEAR-SILENCE",
                {issue.code for issue in report.errors},
            )

    def test_mono_policy_can_warn_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mono = write_tone(root / "mono.wav", channels=1)
            job = make_job(root, target=mono)

            report = validate_job(job, probe=WaveAudioProbe())

            self.assertTrue(report.ok)
            self.assertIn("MMT-W-MONO-INPUT", {issue.code for issue in report.warnings})

    def test_output_cannot_overwrite_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=220.0)
            job = make_job(root, target=target, output=target)

            report = validate_job(job, inspect_audio=False)

            self.assertIn(
                "MMT-E-OUTPUT-OVERWRITES-INPUT",
                {issue.code for issue in report.errors},
            )

    def test_output_hardlink_cannot_overwrite_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=220.0)
            alias = root / "target-hardlink.wav"
            try:
                os.link(target, alias)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable on this filesystem: {exc}")
            job = make_job(root, target=target, output=alias)

            report = validate_job(job, inspect_audio=False)

            self.assertIn(
                "MMT-E-OUTPUT-OVERWRITES-INPUT",
                {issue.code for issue in report.errors},
            )

    def test_target_and_reference_hardlinks_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.wav", frequency=220.0)
            alias = root / "reference-hardlink.wav"
            try:
                os.link(target, alias)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable on this filesystem: {exc}")
            job = make_job(root, target=target, reference=alias)

            report = validate_job(job, inspect_audio=False)

            self.assertIn(
                "MMT-E-TARGET-REFERENCE-SAME-PATH",
                {issue.code for issue in report.errors},
            )

    def test_case_distinct_paths_remain_distinct_on_case_sensitive_filesystems(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upper = write_tone(root / "Target.wav", frequency=220.0)
            lower = root / "target.wav"
            if lower.exists():
                self.skipTest("filesystem is case-insensitive")
            job = make_job(root, target=upper, output=lower)

            report = validate_job(job, inspect_audio=False)

            self.assertNotIn(
                "MMT-E-OUTPUT-OVERWRITES-INPUT",
                {issue.code for issue in report.errors},
            )

    def test_raw_mode_rejects_integer_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            job = replace(
                job,
                outputs=(
                    replace(
                        job.outputs[0],
                        mode=OutputMode.RAW_FLOAT,
                        subtype="PCM_24",
                    ),
                ),
            )

            report = validate_job(job, inspect_audio=False)

            self.assertIn(
                "MMT-E-RAW-FLOAT-INTEGER-SUBTYPE",
                {issue.code for issue in report.errors},
            )

    def test_policy_allow_suppresses_mono_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mono = write_tone(root / "mono.wav", channels=1)
            job = make_job(root, target=mono)
            job = replace(
                job,
                edge_cases=replace(EdgeCasePolicy(), mono_input=PolicyAction.ALLOW),
            )

            report = validate_job(job, probe=WaveAudioProbe())

            self.assertNotIn("MMT-W-MONO-INPUT", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()
