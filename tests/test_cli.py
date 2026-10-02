from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

try:
    import pytest

    pytestmark: object = pytest.mark.integration
except ImportError:
    pytestmark = ()

from music_mastering_tools.cli import (
    EXIT_PREFLIGHT,
    EXIT_SUCCESS,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_capabilities_json_is_machine_readable(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["capabilities", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(result, EXIT_SUCCESS)
        self.assertEqual(len(payload["engines"]), 2)

    def test_upstream_template_can_be_linted_without_placeholder_audio(
        self,
    ) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(
                [
                    "validate",
                    str(PROJECT_ROOT / "configs" / "upstream-baseline.json"),
                    "--no-input-check",
                    "--no-audio-probe",
                    "--json",
                ]
            )

        self.assertEqual(result, EXIT_SUCCESS)
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_native_target_returns_preflight_exit_code(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(
                [
                    "validate",
                    str(PROJECT_ROOT / "configs" / "native-target.json"),
                    "--no-input-check",
                    "--no-audio-probe",
                ]
            )

        self.assertEqual(result, EXIT_PREFLIGHT)
        self.assertIn("CAPABILITY-UNSUPPORTED", output.getvalue())

    def test_rejected_native_target_has_machine_readable_nested_details(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(
                [
                    "validate",
                    str(PROJECT_ROOT / "configs" / "native-target.json"),
                    "--no-input-check",
                    "--no-audio-probe",
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(result, EXIT_PREFLIGHT)
        self.assertFalse(payload["ok"])
        capability = next(
            issue for issue in payload["issues"] if issue["code"] == "MMT-E-CAPABILITY-UNSUPPORTED"
        )
        unsupported = capability["details"]["unsupported"]
        self.assertIsInstance(unsupported, list)
        self.assertTrue(all(isinstance(item, dict) for item in unsupported))


if __name__ == "__main__":
    unittest.main()
