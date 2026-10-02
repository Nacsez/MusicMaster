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

from music_mastering_tools.audio_probe import AudioFacts, AudioProbeUnavailable
from music_mastering_tools.config import (
    EdgeCasePolicy,
    OutputMode,
    OutputSpec,
    PolicyAction,
)
from music_mastering_tools.validation import validate_job
from tests.helpers import make_job, write_tone


class _FactsProbe:
    def __init__(self, facts: dict[str, AudioFacts]) -> None:
        self.facts = facts

    def probe(self, path: str | Path) -> AudioFacts:
        return self.facts[str(Path(path))]


class _FailingProbe:
    def probe(self, path: str | Path) -> AudioFacts:
        raise AudioProbeUnavailable(f"cannot decode {path}")


def _facts(
    path: str,
    *,
    channels: int = 2,
    sample_rate: int = 44_100,
    frames: int = 8_192,
    peak: float = 0.5,
    clipping_samples: int = 0,
    non_finite_samples: int = 0,
) -> AudioFacts:
    return AudioFacts(
        path=path,
        backend="test",
        format="WAV",
        subtype="PCM_16",
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        duration_seconds=frames / sample_rate,
        peak=peak,
        rms=peak / 2,
        clipping_samples=clipping_samples,
        non_finite_samples=non_finite_samples,
    )


class ValidationEdgeTests(unittest.TestCase):
    def test_missing_nonfile_duplicate_and_parent_path_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            directory_input = root / "directory-input"
            directory_input.mkdir()
            missing = root / "missing.wav"
            output = root / "absent-parent" / "output.wav"
            job = replace(
                job,
                target=str(directory_input),
                references=(replace(job.references[0], path=str(missing)),),
                outputs=(
                    OutputSpec(str(output)),
                    OutputSpec(str(output)),
                ),
                edge_cases=replace(
                    job.edge_cases,
                    create_output_directories=False,
                ),
            )

            report = validate_job(job, inspect_audio=False)
            codes = {issue.code for issue in report.errors}

            self.assertIn("MMT-E-INPUT-NOT-FILE", codes)
            self.assertIn("MMT-E-INPUT-MISSING", codes)
            self.assertIn("MMT-E-DUPLICATE-OUTPUT", codes)
            self.assertIn("MMT-E-OUTPUT-DIRECTORY-MISSING", codes)

    def test_existing_output_policy_warning_and_allow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "existing.wav"
            output.write_bytes(b"existing")
            job = make_job(root, output=output)

            warning_job = replace(
                job,
                edge_cases=replace(
                    job.edge_cases,
                    existing_output=PolicyAction.WARN,
                ),
            )
            warning_report = validate_job(warning_job, inspect_audio=False)
            self.assertIn(
                "MMT-W-OUTPUT-EXISTS",
                {issue.code for issue in warning_report.warnings},
            )

            allow_job = replace(
                job,
                edge_cases=replace(
                    job.edge_cases,
                    existing_output=PolicyAction.ALLOW,
                ),
            )
            allow_report = validate_job(allow_job, inspect_audio=False)
            self.assertNotIn(
                "MMT-OUTPUT-EXISTS",
                " ".join(issue.code for issue in allow_report.issues),
            )

    def test_audio_fact_policy_matrix_reports_each_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_tone(root / "target.mp3")
            reference = write_tone(root / "reference.wav")
            job = make_job(root, target=target, reference=reference)
            job = replace(
                job,
                audio=replace(
                    job.audio,
                    internal_sample_rate=48_000,
                    max_length_seconds=1.0,
                ),
                matching=replace(job.matching, max_piece_seconds=0.5),
                edge_cases=replace(
                    EdgeCasePolicy(),
                    more_than_two_channels=PolicyAction.WARN,
                    lossy_input=PolicyAction.WARN,
                    sample_rate_conversion=PolicyAction.WARN,
                ),
            )
            probe = _FactsProbe(
                {
                    str(target): _facts(
                        str(target),
                        channels=6,
                        sample_rate=44_100,
                        frames=88_200,
                        peak=1.0,
                        clipping_samples=20,
                        non_finite_samples=2,
                    ),
                    str(reference): _facts(
                        str(reference),
                        sample_rate=44_100,
                        frames=100,
                    ),
                }
            )

            report = validate_job(job, probe=probe)
            codes = {issue.code for issue in report.issues}

            self.assertIn("MMT-E-NONFINITE-AUDIO", codes)
            self.assertIn("MMT-W-MULTICHANNEL-INPUT", codes)
            self.assertIn("MMT-W-LOSSY-INPUT", codes)
            self.assertIn("MMT-W-SAMPLE-RATE-CONVERSION", codes)
            self.assertIn("MMT-E-TRACK-TOO-LONG", codes)
            self.assertIn("MMT-E-TRACK-TOO-SHORT", codes)
            self.assertIn("MMT-W-TARGET-CLIPPING", codes)
            self.assertIn("MMT-W-CUSTOM-SAMPLE-RATE", codes)

    def test_probe_failure_and_output_semantic_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            job = replace(
                job,
                outputs=(
                    replace(
                        job.outputs[0],
                        mode=OutputMode.NORMALIZED,
                        dither="tpdf",
                    ),
                ),
            )

            report = validate_job(job, probe=_FailingProbe())
            codes = {issue.code for issue in report.errors}

            self.assertIn("MMT-E-AUDIO-PROBE", codes)
            self.assertIn("MMT-E-CAPABILITY-UNSUPPORTED", codes)
            capability = next(
                issue for issue in report.errors if issue.code == "MMT-E-CAPABILITY-UNSUPPORTED"
            )
            self.assertIn("outputs[0].dither", capability.message)


if __name__ == "__main__":
    unittest.main()
