from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import music_mastering_tools.doctor as doctor_module
from music_mastering_tools.doctor import (
    DependencySpec,
    Doctor,
    DoctorCheck,
    DoctorReport,
    DoctorStatus,
    doctor,
    run_doctor,
)

doctor_runtime: Any = doctor_module


def _required_check(report: DoctorReport, name: str) -> DoctorCheck:
    check = report.get(name)
    if check is None:
        raise AssertionError(f"doctor report did not contain {name!r}")
    return check


class DoctorValueContractTests(unittest.TestCase):
    def test_dependency_spec_rejects_blank_identifiers(self) -> None:
        invalid_values: tuple[dict[str, object], ...] = (
            {"module": ""},
            {"module": "valid", "distribution": ""},
            {"module": "valid", "expected_version": ""},
        )

        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                DependencySpec(**cast(Any, values))

        dependency = DependencySpec("package.module", distribution="distribution-name")
        self.assertEqual(dependency.distribution_name, "distribution-name")
        self.assertEqual(DependencySpec("module-only").distribution_name, "module-only")
        self.assertEqual(str(DoctorStatus.WARNING), "warning")

    def test_check_serialization_is_json_ready_and_defensively_copied(self) -> None:
        original_details: dict[str, object] = {
            "path": Path("audio.wav"),
            "status": DoctorStatus.PASS,
            "nested": (Path("one"), {"two": Path("two")}),
            "unknown": object(),
        }
        check = DoctorCheck(
            "dependency",
            cast(DoctorStatus, "warning"),
            "version metadata is unavailable",
            details=original_details,
            remediation="Install a versioned distribution.",
        )
        original_details["new"] = "caller mutation"

        serialized = check.to_dict()
        self.assertEqual(check.status, DoctorStatus.WARNING)
        self.assertTrue(check.ok)
        self.assertEqual(serialized["details"]["path"], "audio.wav")
        self.assertEqual(serialized["details"]["status"], "pass")
        self.assertEqual(serialized["details"]["nested"], ["one", {"two": "two"}])
        self.assertIn("object at", serialized["details"]["unknown"])
        self.assertNotIn("new", check.details)

    def test_check_rejects_invalid_public_fields(self) -> None:
        invalid_calls: tuple[tuple[object, object, object], ...] = (
            ("", DoctorStatus.PASS, "summary"),
            ("name", DoctorStatus.PASS, ""),
            ("name", DoctorStatus.PASS, "summary"),
        )

        with self.assertRaises(ValueError):
            DoctorCheck(
                cast(str, invalid_calls[0][0]),
                cast(DoctorStatus, invalid_calls[0][1]),
                cast(str, invalid_calls[0][2]),
            )
        with self.assertRaises(ValueError):
            DoctorCheck(
                cast(str, invalid_calls[1][0]),
                cast(DoctorStatus, invalid_calls[1][1]),
                cast(str, invalid_calls[1][2]),
            )
        with self.assertRaises(TypeError):
            DoctorCheck(
                "name",
                DoctorStatus.PASS,
                "summary",
                details=cast(Any, invalid_calls[2]),
            )

    def test_report_normalizes_time_and_renders_actionable_results(self) -> None:
        warning = DoctorCheck(
            "optional",
            DoctorStatus.WARNING,
            "not available",
            version="1.0",
            remediation="Install it.",
        )
        failure = DoctorCheck(
            "required",
            DoctorStatus.FAIL,
            "not available",
            required=True,
            remediation="Install it now.",
        )
        report = DoctorReport(
            (warning, failure),
            generated_at=datetime(2026, 1, 2, 3, 4, 5),
        )

        self.assertEqual(report.generated_at.tzinfo, UTC)
        self.assertFalse(report.healthy)
        self.assertTrue(report.has_warnings)
        self.assertEqual(report.exit_code, 1)
        self.assertIs(report.get("required"), failure)
        self.assertIsNone(report.get("absent"))
        self.assertIn("[WARNING] optional (1.0)", report.render_text())
        self.assertIn("Overall: action required", report.render_text())
        payload = json.loads(report.to_json(indent=0))
        self.assertTrue(payload["generated_at"].endswith("Z"))
        self.assertEqual(payload["checks"][1]["required"], True)

        offset_report = DoctorReport(
            (DoctorCheck("python", DoctorStatus.PASS, "available"),),
            generated_at=datetime(
                2026,
                1,
                1,
                tzinfo=timezone(timedelta(hours=-5)),
            ),
        )
        self.assertEqual(offset_report.generated_at.hour, 5)
        self.assertTrue(offset_report.healthy)
        self.assertFalse(offset_report.has_warnings)
        self.assertEqual(offset_report.exit_code, 0)
        self.assertIn("Overall: healthy", offset_report.render_text())

        with self.assertRaises(TypeError):
            DoctorReport((), generated_at=cast(datetime, "not-a-datetime"))


class DoctorRuntimeContractTests(unittest.TestCase):
    def test_invalid_runtime_arguments_are_rejected_before_probing(self) -> None:
        invalid_minimums: tuple[object, ...] = (
            [3, 10],
            (3,),
            (3, True),
            (-1, 0),
        )
        for minimum in invalid_minimums:
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                run_doctor(
                    minimum_python=cast(tuple[int, int], minimum),
                    include_optional_dependencies=False,
                )

        for timeout in (0, -1, True, "fast"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                run_doctor(
                    command_timeout_seconds=cast(float, timeout),
                    include_optional_dependencies=False,
                )

    def test_missing_ffmpeg_and_old_python_are_reported_without_throwing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "not-installed-ffmpeg"
            with mock.patch.object(doctor_runtime.shutil, "which", return_value=None):
                report = run_doctor(
                    minimum_python=(99, 0),
                    ffmpeg_binary=missing,
                    include_optional_dependencies=False,
                )

        self.assertEqual(_required_check(report, "python").status, DoctorStatus.FAIL)
        self.assertIn("older than required", _required_check(report, "python").summary)
        self.assertEqual(
            _required_check(report, "ffmpeg").status,
            DoctorStatus.WARNING,
        )
        self.assertEqual(report.exit_code, 1)

    def test_ffmpeg_path_probe_reports_success_version_and_invocation(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ffmpeg version 7.1 Copyright\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "ffmpeg.exe"
            executable.touch()
            with (
                mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
                mock.patch.object(
                    doctor_runtime.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                report = run_doctor(
                    ffmpeg_binary=executable,
                    include_optional_dependencies=False,
                    command_timeout_seconds=2.5,
                )

        ffmpeg = _required_check(report, "ffmpeg")
        self.assertEqual(ffmpeg.status, DoctorStatus.PASS)
        self.assertEqual(ffmpeg.version, "7.1")
        self.assertEqual(ffmpeg.details["path"], str(executable.resolve()))
        self.assertEqual(run.call_args.args[0], [str(executable.resolve()), "-version"])
        self.assertEqual(run.call_args.kwargs["timeout"], 2.5)

    def test_ffmpeg_execution_failures_and_nonzero_exit_are_warnings(self) -> None:
        with (
            mock.patch.object(
                doctor_runtime.shutil,
                "which",
                return_value="C:/tools/ffmpeg.exe",
            ),
            mock.patch.object(
                doctor_runtime.subprocess,
                "run",
                side_effect=OSError("loader failure"),
            ),
        ):
            execution_failure = run_doctor(include_optional_dependencies=False)

        execution_check = _required_check(execution_failure, "ffmpeg")
        self.assertIn("could not be executed", execution_check.summary)
        self.assertEqual(
            execution_check.details["exception_type"],
            "OSError",
        )

        for banner in ("unexpected banner", ""):
            with (
                self.subTest(banner=banner),
                mock.patch.object(
                    doctor_runtime.shutil,
                    "which",
                    return_value="C:/tools/ffmpeg.exe",
                ),
                mock.patch.object(
                    doctor_runtime.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        args=[],
                        returncode=7,
                        stdout=banner,
                    ),
                ),
            ):
                nonzero = run_doctor(include_optional_dependencies=False)

            nonzero_check = _required_check(nonzero, "ffmpeg")
            self.assertEqual(nonzero_check.status, DoctorStatus.WARNING)
            self.assertIn("code 7", nonzero_check.summary)
            self.assertIsNone(nonzero_check.version)

    def test_invalid_duplicate_and_missing_dependency_specs_are_diagnostic(self) -> None:
        required = DependencySpec(
            "missing",
            purpose="required feature",
            required=True,
        )
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(
                doctor_runtime.importlib.util,
                "find_spec",
                return_value=None,
            ) as find_spec,
        ):
            report = run_doctor(
                dependencies=("invalid", required, required),
                include_optional_dependencies=False,
            )

        invalid = _required_check(report, "python-dependency")
        missing = _required_check(report, "python:missing")
        self.assertEqual(invalid.status, DoctorStatus.WARNING)
        self.assertIn("type str", invalid.summary)
        self.assertEqual(missing.status, DoctorStatus.FAIL)
        self.assertIn("required 'missing'", missing.remediation or "")
        find_spec.assert_called_once_with("missing")

    def test_required_dependency_overrides_duplicate_optional_default(self) -> None:
        required_matchering = DependencySpec(
            "matchering",
            required=True,
            expected_version="2.0.6",
        )
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(doctor_runtime.importlib.util, "find_spec", return_value=None),
        ):
            report = run_doctor(
                dependencies=(required_matchering,),
                include_optional_dependencies=True,
            )

        matchering_checks = [check for check in report.checks if check.name == "python:matchering"]
        self.assertEqual(len(matchering_checks), 1)
        self.assertTrue(matchering_checks[0].required)
        self.assertEqual(matchering_checks[0].status, DoctorStatus.FAIL)

    def test_dependency_discovery_and_import_failures_preserve_requiredness(self) -> None:
        dependency = DependencySpec("broken", purpose="DSP", required=True)
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(
                doctor_runtime.importlib.util,
                "find_spec",
                side_effect=ValueError("bad module name"),
            ),
        ):
            discovery = run_doctor(
                dependencies=(dependency,),
                include_optional_dependencies=False,
            )
        discovery_check = _required_check(discovery, "python:broken")
        self.assertEqual(discovery_check.status, DoctorStatus.FAIL)
        self.assertIn("discovery failed", discovery_check.summary)

        found = SimpleNamespace(origin="/virtual/broken.py")
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(doctor_runtime.importlib.util, "find_spec", return_value=found),
            mock.patch.object(
                doctor_runtime.importlib.metadata,
                "version",
                return_value="1.2.3",
            ),
            mock.patch.object(
                doctor_runtime.importlib,
                "import_module",
                side_effect=RuntimeError("native loader failed"),
            ),
        ):
            imported = run_doctor(
                dependencies=(dependency,),
                include_optional_dependencies=False,
            )

        failed = _required_check(imported, "python:broken")
        self.assertEqual(failed.status, DoctorStatus.FAIL)
        self.assertEqual(failed.version, "1.2.3")
        self.assertIn("RuntimeError", failed.summary)
        self.assertEqual(failed.details["origin"], "/virtual/broken.py")

    def test_dependency_version_sources_and_soundfile_native_version(self) -> None:
        found = SimpleNamespace(origin="/virtual/soundfile.py")
        loaded = SimpleNamespace(
            __version__="0.13.1",
            __libsndfile_version__="1.2.2",
        )
        dependency = DependencySpec("soundfile", purpose="audio I/O")
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(doctor_runtime.importlib.util, "find_spec", return_value=found),
            mock.patch.object(
                doctor_runtime.importlib.metadata,
                "version",
                side_effect=doctor_runtime.importlib.metadata.PackageNotFoundError("soundfile"),
            ),
            mock.patch.object(
                doctor_runtime.importlib,
                "import_module",
                return_value=loaded,
            ),
        ):
            report = run_doctor(
                dependencies=(dependency,),
                include_optional_dependencies=False,
            )

        check = _required_check(report, "python:soundfile")
        self.assertEqual(check.status, DoctorStatus.PASS)
        self.assertEqual(check.version, "0.13.1")
        self.assertEqual(check.details["libsndfile_version"], "1.2.2")

    def test_unknown_or_mismatched_versions_have_actionable_status(self) -> None:
        found = SimpleNamespace(origin="/virtual/module.py")
        unknown = DependencySpec("unknown", purpose="analysis")
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(doctor_runtime.importlib.util, "find_spec", return_value=found),
            mock.patch.object(
                doctor_runtime.importlib.metadata,
                "version",
                side_effect=RuntimeError("metadata database unavailable"),
            ),
        ):
            unknown_report = run_doctor(
                dependencies=(unknown,),
                include_optional_dependencies=False,
                probe_imports=False,
            )

        unknown_check = _required_check(unknown_report, "python:unknown")
        self.assertEqual(unknown_check.status, DoctorStatus.WARNING)
        self.assertIn("version could not be determined", unknown_check.summary)
        self.assertFalse(unknown_check.details["import_probed"])

        dependencies = (
            DependencySpec("optional", expected_version="2.0"),
            DependencySpec("required", required=True, expected_version="2.0"),
        )
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(doctor_runtime.importlib.util, "find_spec", return_value=found),
            mock.patch.object(
                doctor_runtime.importlib.metadata,
                "version",
                return_value="1.0",
            ),
        ):
            mismatch_report = run_doctor(
                dependencies=dependencies,
                include_optional_dependencies=False,
                probe_imports=False,
            )

        optional_check = _required_check(mismatch_report, "python:optional")
        required_check = _required_check(mismatch_report, "python:required")
        self.assertEqual(optional_check.status, DoctorStatus.WARNING)
        self.assertEqual(required_check.status, DoctorStatus.FAIL)
        self.assertIn(
            "Install required==2.0",
            required_check.remediation or "",
        )

    def test_diagnostic_boundaries_catch_environment_failures_but_not_interrupts(
        self,
    ) -> None:
        with (
            mock.patch.object(
                doctor_runtime.platform,
                "python_version",
                side_effect=RuntimeError("platform unavailable"),
            ),
            mock.patch.object(
                doctor_runtime.shutil,
                "which",
                side_effect=SystemExit("probe exited"),
            ),
        ):
            report = run_doctor(include_optional_dependencies=False)

        python_check = _required_check(report, "python")
        ffmpeg_check = _required_check(report, "ffmpeg")
        self.assertEqual(python_check.status, DoctorStatus.FAIL)
        self.assertIn("RuntimeError", python_check.summary)
        self.assertEqual(ffmpeg_check.status, DoctorStatus.WARNING)
        self.assertIn("SystemExit", ffmpeg_check.summary)

        dependency = DependencySpec("interrupt")
        with (
            mock.patch.object(doctor_runtime.shutil, "which", return_value=None),
            mock.patch.object(
                doctor_runtime.importlib.util,
                "find_spec",
                return_value=SimpleNamespace(origin="/virtual/interrupt.py"),
            ),
            mock.patch.object(
                doctor_runtime.importlib.metadata,
                "version",
                return_value="1",
            ),
            mock.patch.object(
                doctor_runtime.importlib,
                "import_module",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_doctor(
                dependencies=(dependency,),
                include_optional_dependencies=False,
            )

    def test_function_and_object_entry_points_delegate_configuration(self) -> None:
        expected = DoctorReport((DoctorCheck("python", DoctorStatus.PASS, "ready"),))
        with mock.patch.object(
            doctor_module,
            "run_doctor",
            return_value=expected,
        ) as run:
            self.assertIs(
                doctor(
                    include_optional_dependencies=False,
                    ffmpeg_binary="custom-ffmpeg",
                ),
                expected,
            )
            configured = Doctor(
                minimum_python=(3, 12),
                ffmpeg_binary="configured-ffmpeg",
                dependencies=(DependencySpec("engine"),),
                include_optional_dependencies=False,
                probe_imports=False,
                command_timeout_seconds=9.0,
            )
            self.assertIs(configured.run(), expected)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args.kwargs,
            {
                "minimum_python": (3, 12),
                "ffmpeg_binary": "configured-ffmpeg",
                "dependencies": (DependencySpec("engine"),),
                "include_optional_dependencies": False,
                "probe_imports": False,
                "command_timeout_seconds": 9.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
