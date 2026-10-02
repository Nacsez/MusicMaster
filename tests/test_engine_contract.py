from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.config import (
    DitherMode,
    EngineKind,
    LimiterKind,
    MatchingConfig,
    PreviewConfig,
    ReferenceSpec,
)
from music_mastering_tools.engine import (
    EngineRunResult,
    NativeMasteringEngine,
    UpstreamMatcheringEngine,
)
from music_mastering_tools.errors import CapabilityError
from tests.helpers import make_job


class EngineContractTests(unittest.TestCase):
    def test_upstream_capabilities_match_adapter_constraints(self) -> None:
        capabilities = UpstreamMatcheringEngine().capabilities

        self.assertEqual(capabilities.engine_version, "2.0.6")
        self.assertEqual(capabilities.maximum_references, 1)
        self.assertFalse(capabilities.true_peak_limiter)
        self.assertFalse(capabilities.thread_safe)
        self.assertTrue(capabilities.runnable)

    def test_upstream_rejects_multiple_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            second = ReferenceSpec(str(root / "second.wav"))
            job = replace(job, references=job.references + (second,))

            with self.assertRaises(CapabilityError):
                UpstreamMatcheringEngine().validate_capabilities(job)

    def test_upstream_rejects_unimplemented_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = make_job(Path(temporary))
            job = replace(
                job,
                matching=replace(MatchingConfig(), amount=0.5),
                outputs=(replace(job.outputs[0], dither=DitherMode.TPDF),),
            )

            with self.assertRaisesRegex(CapabilityError, "matching.amount"):
                UpstreamMatcheringEngine().validate_capabilities(job)

    def test_native_engine_truthfully_runs_the_weighted_parity_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            second = ReferenceSpec(
                str(root / "second.wav"),
                level_weight=3.0,
                frequency_weight=1.0,
            )
            job = replace(
                job,
                references=(
                    replace(
                        job.references[0],
                        level_weight=1.0,
                        frequency_weight=3.0,
                    ),
                    second,
                ),
                execution=replace(job.execution, engine=EngineKind.NATIVE),
            )

            NativeMasteringEngine().validate_capabilities(job)

            capabilities = NativeMasteringEngine().capabilities
            self.assertTrue(capabilities.runnable)
            self.assertEqual(capabilities.maximum_references, 32)
            self.assertTrue(capabilities.independent_reference_weights)
            self.assertTrue(capabilities.spectral_gain_ceiling)
            self.assertFalse(capabilities.true_peak_limiter)
            self.assertIn("true-peak-limiter", capabilities.planned_capabilities)

    def test_native_engine_rejects_more_than_32_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            references = tuple(
                ReferenceSpec(str(root / f"reference-{index}.wav")) for index in range(33)
            )
            job = replace(
                job,
                references=references,
                execution=replace(job.execution, engine=EngineKind.NATIVE),
            )

            with self.assertRaisesRegex(CapabilityError, "limit"):
                NativeMasteringEngine().validate_capabilities(job)

    def test_native_engine_rejects_a_job_selecting_another_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = make_job(Path(temporary))

            with self.assertRaisesRegex(CapabilityError, "requires execution.engine"):
                NativeMasteringEngine().validate_capabilities(job)

    def test_upstream_rejects_limited_output_when_limiter_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = make_job(Path(temporary))
            job = replace(
                job,
                limiter=replace(job.limiter, kind=LimiterKind.NONE),
            )

            with self.assertRaisesRegex(CapabilityError, "limiter.kind"):
                UpstreamMatcheringEngine().validate_capabilities(job)

    def test_upstream_rejects_fractional_preview_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root)
            job = replace(
                job,
                preview=PreviewConfig(
                    enabled=True,
                    target_path=str(root / "target-preview.wav"),
                    duration_seconds=5.50001,
                ),
            )

            with self.assertRaisesRegex(CapabilityError, "integer sample count"):
                UpstreamMatcheringEngine().validate_capabilities(job)

    def test_engine_result_details_are_deeply_immutable_and_json_ready(self) -> None:
        original: dict[str, object] = {
            "nested": {"items": ["original"]},
            "finite": 1.5,
        }
        result = EngineRunResult.completed(
            engine_id="test",
            engine_version="1",
            output_paths=("output.wav",),
            started_at=datetime.now(UTC),
            duration_seconds=0.1,
            details=original,
        )
        nested = original["nested"]
        self.assertIsInstance(nested, dict)
        nested["items"].append("caller mutation")
        original["new"] = "caller mutation"

        serialized = result.to_dict()

        self.assertEqual(
            serialized["details"],
            {
                "nested": {"items": ["original"]},
                "finite": 1.5,
            },
        )
        self.assertNotIn("new", result.details)
        with self.assertRaises(TypeError):
            result.details["new"] = "blocked"

        with self.assertRaises(TypeError):
            EngineRunResult.completed(
                engine_id="test",
                engine_version="1",
                output_paths=(),
                started_at=datetime.now(UTC),
                duration_seconds=0,
                details=["not", "a", "mapping"],
            )
        for invalid_details in (
            {"value": float("nan")},
            {"value": object()},
        ):
            with (
                self.subTest(invalid_details=invalid_details),
                self.assertRaises((TypeError, ValueError)),
            ):
                EngineRunResult.completed(
                    engine_id="test",
                    engine_version="1",
                    output_paths=(),
                    started_at=datetime.now(UTC),
                    duration_seconds=0,
                    details=invalid_details,
                )

    def test_diagnostic_cli_import_does_not_eagerly_load_numerical_dsp(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import music_mastering_tools.cli; "
                    "assert 'numpy' not in sys.modules; "
                    "assert 'scipy' not in sys.modules"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
