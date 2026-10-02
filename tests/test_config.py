from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.config import JobConfig, load_job_config
from music_mastering_tools.validation import validate_job

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class JobConfigTests(unittest.TestCase):
    def test_upstream_template_round_trips_without_losing_fields(self) -> None:
        source = PROJECT_ROOT / "configs" / "upstream-baseline.json"
        unresolved = load_job_config(source, resolve_paths=False)

        reconstructed = JobConfig.from_dict(json.loads(unresolved.to_json()))

        self.assertEqual(reconstructed, unresolved)
        self.assertIsNone(reconstructed.matching.max_eq_gain_db)
        self.assertEqual(len(reconstructed.outputs), 3)

    def test_relative_paths_resolve_against_config_directory(self) -> None:
        source = PROJECT_ROOT / "configs" / "upstream-baseline.json"
        job = load_job_config(source)

        self.assertEqual(
            Path(job.target),
            (PROJECT_ROOT / "audio" / "target.wav").resolve(),
        )
        self.assertTrue(Path(job.outputs[0].path).is_absolute())

    def test_unknown_fields_are_rejected(self) -> None:
        data = json.loads(
            (PROJECT_ROOT / "configs" / "upstream-baseline.json").read_text(encoding="utf-8")
        )
        data["mystery_switch"] = True

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            JobConfig.from_dict(data)

    def test_reference_weights_are_normalized_independently(self) -> None:
        data = json.loads(
            (PROJECT_ROOT / "configs" / "native-target.json").read_text(encoding="utf-8")
        )
        job = JobConfig.from_dict(data)

        self.assertEqual(job.normalized_level_weights, (0.25, 0.75))
        self.assertEqual(job.normalized_frequency_weights, (0.75, 0.25))

    def test_missing_inputs_can_be_intentionally_skipped_without_probe_errors(
        self,
    ) -> None:
        job = load_job_config(PROJECT_ROOT / "configs" / "upstream-baseline.json")

        report = validate_job(job, require_inputs=False, inspect_audio=True)

        self.assertTrue(report.ok)
        self.assertEqual(report.audio_facts, ())
        self.assertNotIn("MMT-E-AUDIO-PROBE", {issue.code for issue in report.issues})

    def test_duplicate_and_non_finite_json_values_are_rejected(self) -> None:
        for content, message in (
            (
                '{"target":"a","target":"b","references":[],"outputs":[]}',
                "duplicate JSON field",
            ),
            (
                '{"target":NaN,"references":[],"outputs":[]}',
                "non-finite JSON number",
            ),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "job.json"
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_job_config(path)

    def test_fractional_schema_version_and_boolean_numbers_are_rejected(self) -> None:
        data = json.loads(
            (PROJECT_ROOT / "configs" / "upstream-baseline.json").read_text(encoding="utf-8")
        )
        for field, value, message in (
            ("schema_version", 1.0, "schema_version must be an integer"),
            ("matching.amount", True, "matching.amount must be a finite number"),
        ):
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(data))
                if field == "schema_version":
                    candidate["schema_version"] = value
                else:
                    candidate["matching"]["amount"] = value
                with self.assertRaisesRegex(ValueError, message):
                    JobConfig.from_dict(candidate)

    def test_lowess_fraction_rejects_statsmodels_out_of_domain_value(self) -> None:
        data = json.loads(
            (PROJECT_ROOT / "configs" / "upstream-baseline.json").read_text(encoding="utf-8")
        )
        data["matching"]["lowess_fraction"] = 2.0

        with self.assertRaisesRegex(ValueError, "must not exceed 1"):
            JobConfig.from_dict(data)

    def test_hyrax_filter_and_window_domains_are_validated(self) -> None:
        data = json.loads(
            (PROJECT_ROOT / "configs" / "upstream-baseline.json").read_text(encoding="utf-8")
        )
        cases = (
            ("attack_ms", 0.0001, "at least one sample"),
            ("hold_ms", 0.01, "at least three samples"),
            ("attack_filter_coefficient", 0.0, "must be negative"),
            (
                "hold_filter_coefficient",
                22_050.0,
                "internal Nyquist frequency",
            ),
            (
                "release_filter_coefficient",
                100_000_000.0,
                "internal Nyquist frequency",
            ),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(data))
                candidate["limiter"][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    JobConfig.from_dict(candidate)

    def test_extreme_finite_reference_weights_normalize_stably(self) -> None:
        data = json.loads(
            (PROJECT_ROOT / "configs" / "native-target.json").read_text(encoding="utf-8")
        )
        for reference in data["references"]:
            reference["level_weight"] = 1e308
            reference["frequency_weight"] = 1e308

        job = JobConfig.from_dict(data)

        self.assertEqual(job.normalized_level_weights, (0.5, 0.5))
        self.assertEqual(job.normalized_frequency_weights, (0.5, 0.5))


if __name__ == "__main__":
    unittest.main()
