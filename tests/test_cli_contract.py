from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import music_mastering_tools.cli as cli_module
from music_mastering_tools.cli import (
    EXIT_CAPABILITY,
    EXIT_ENVIRONMENT,
    EXIT_PREFLIGHT,
    EXIT_PROCESSING,
    EXIT_SUCCESS,
    EXIT_USAGE_OR_CONFIG,
    main,
)
from music_mastering_tools.config import ExecutionConfig, save_job_config
from music_mastering_tools.doctor import DoctorCheck, DoctorReport, DoctorStatus
from music_mastering_tools.errors import (
    CapabilityError,
    DependencyError,
    PreflightError,
    ProcessingError,
)
from music_mastering_tools.events import NullEventSink
from music_mastering_tools.manifest import RunManifest
from music_mastering_tools.service import RunOutcome
from music_mastering_tools.validation import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from tests.helpers import make_job

cli_runtime: Any = cli_module


def _completed_outcome(
    manifest: Path,
    *,
    event_log: Path | None,
    job_id: str = "cli-job",
) -> RunOutcome:
    run_manifest = RunManifest.create(run_id=job_id)
    run_manifest.mark_completed()
    run_manifest.save(manifest)
    return RunOutcome(
        job_id=job_id,
        dry_run=True,
        manifest_path=str(manifest),
        event_log_path=str(event_log) if event_log is not None else None,
        validation=ValidationReport(()),
        engine_result=None,
    )


class CliDiagnosticContractTests(unittest.TestCase):
    def test_doctor_strict_json_builds_required_mastering_dependency_set(self) -> None:
        report = DoctorReport(
            (
                DoctorCheck("python", DoctorStatus.PASS, "ready", required=True),
                DoctorCheck("ffmpeg", DoctorStatus.WARNING, "optional"),
            )
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(cli_module, "run_doctor", return_value=report) as run,
            contextlib.redirect_stdout(stdout),
        ):
            result = main(
                [
                    "doctor",
                    "--strict-mastering",
                    "--no-import-probe",
                    "--json",
                ]
            )

        self.assertEqual(result, EXIT_SUCCESS)
        self.assertTrue(json.loads(stdout.getvalue())["healthy"])
        dependencies = run.call_args.kwargs["dependencies"]
        self.assertEqual(len(dependencies), 6)
        self.assertTrue(all(dependency.required for dependency in dependencies))
        self.assertEqual(dependencies[0].module, "matchering")
        self.assertEqual(dependencies[0].expected_version, "2.0.6")
        self.assertFalse(run.call_args.kwargs["include_optional_dependencies"])
        self.assertFalse(run.call_args.kwargs["probe_imports"])

    def test_doctor_default_text_preserves_report_exit_status(self) -> None:
        report = DoctorReport(
            (
                DoctorCheck(
                    "python",
                    DoctorStatus.FAIL,
                    "too old",
                    required=True,
                    remediation="Upgrade Python.",
                ),
            )
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(cli_module, "run_doctor", return_value=report) as run,
            contextlib.redirect_stdout(stdout),
        ):
            result = main(["doctor"])

        self.assertEqual(result, EXIT_ENVIRONMENT)
        self.assertIn("Overall: action required", stdout.getvalue())
        self.assertIsNone(run.call_args.kwargs["dependencies"])
        self.assertTrue(run.call_args.kwargs["include_optional_dependencies"])
        self.assertTrue(run.call_args.kwargs["probe_imports"])

    def test_capabilities_text_and_implicit_process_arguments_are_supported(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch.object(
                cli_runtime.sys,
                "argv",
                ["mmt", "capabilities"],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            result = main()

        self.assertEqual(result, EXIT_SUCCESS)
        rendered = stdout.getvalue()
        self.assertIn("matchering", rendered)
        self.assertIn("[verified-runnable]", rendered)
        self.assertIn("planned_capabilities", rendered)

    def test_show_config_prints_normalized_resolved_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            save_job_config(make_job(root), config)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["show-config", str(config)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, EXIT_SUCCESS)
        self.assertTrue(Path(payload["target"]).is_absolute())
        self.assertEqual(payload["schema_version"], 1)

    def test_text_validation_includes_issue_path_and_remediation(self) -> None:
        issue = ValidationIssue(
            "MMT-E-TEST",
            ValidationSeverity.ERROR,
            "A deterministic validation failure.",
            path="target.wav",
            remediation="Replace the target.",
        )
        report = ValidationReport((issue,))
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            save_job_config(make_job(root), config)
            with (
                mock.patch.object(cli_module, "validate_job", return_value=report),
                contextlib.redirect_stdout(stdout),
            ):
                result = main(["validate", str(config)])

        self.assertEqual(result, EXIT_PREFLIGHT)
        self.assertIn("Validation FAIL: 1 error(s)", stdout.getvalue())
        self.assertIn("MMT-E-TEST [target.wav]", stdout.getvalue())
        self.assertIn("Remedy: Replace the target.", stdout.getvalue())


class CliRunContractTests(unittest.TestCase):
    def test_run_respects_explicit_paths_quiet_mode_and_disabled_event_log(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            manifest = root / "audit" / "explicit-manifest.json"
            save_job_config(make_job(root, job_id="explicit-job"), config)
            outcome = _completed_outcome(manifest, event_log=None, job_id="explicit-job")
            stdout = io.StringIO()

            with (
                mock.patch.object(
                    cli_runtime.MasteringService,
                    "run",
                    return_value=outcome,
                ) as service_run,
                contextlib.redirect_stdout(stdout),
            ):
                result = main(
                    [
                        "run",
                        str(config),
                        "--dry-run",
                        "--manifest",
                        str(manifest),
                        "--no-event-log",
                        "--no-audio-probe",
                        "--quiet",
                    ]
                )

        self.assertEqual(result, EXIT_SUCCESS)
        self.assertIn("Dry run completed: explicit-job", stdout.getvalue())
        self.assertNotIn("Event log:", stdout.getvalue())
        call = service_run.call_args
        self.assertEqual(call.kwargs["manifest_path"], manifest.resolve())
        self.assertIsNone(call.kwargs["event_log_path"])
        self.assertIsInstance(call.kwargs["sink"], NullEventSink)
        self.assertTrue(call.kwargs["dry_run"])
        self.assertFalse(call.kwargs["inspect_audio"])
        self.assertEqual(
            call.kwargs["command"],
            (
                "mmt",
                "run",
                str(config),
                "--dry-run",
                "--manifest",
                str(manifest),
                "--no-event-log",
                "--no-audio-probe",
                "--quiet",
            ),
        )

    def test_run_generates_job_id_and_default_audit_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            job = replace(
                make_job(root),
                execution=ExecutionConfig(job_id=None),
            )
            save_job_config(job, config)
            manifest = root / ".mmt-runs" / "generated-id" / "manifest.json"
            event_log = manifest.with_name("events.jsonl")
            outcome = _completed_outcome(
                manifest,
                event_log=event_log,
                job_id="generated-id",
            )
            # Windows temp roots can use an 8.3 alias; the CLI canonicalizes
            # both existing manifest files and future event-log destinations.
            expected_manifest = manifest.resolve()
            expected_event_log = event_log.resolve()
            stdout = io.StringIO()

            with (
                mock.patch.object(
                    cli_runtime.uuid,
                    "uuid4",
                    return_value=SimpleNamespace(hex="generated-id"),
                ),
                mock.patch.object(
                    cli_runtime.MasteringService,
                    "run",
                    return_value=outcome,
                ) as service_run,
                contextlib.redirect_stdout(stdout),
            ):
                result = main(
                    [
                        "run",
                        str(config),
                        "--dry-run",
                        "--verbose",
                        "--json-events",
                    ]
                )

        self.assertEqual(result, EXIT_SUCCESS)
        call = service_run.call_args
        active_job = call.args[0]
        self.assertEqual(active_job.execution.job_id, "generated-id")
        self.assertEqual(call.kwargs["manifest_path"], expected_manifest)
        self.assertEqual(call.kwargs["event_log_path"], expected_event_log)
        self.assertIn(f"Event log: {event_log}", stdout.getvalue())

    def test_run_uses_paths_embedded_in_resolved_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            embedded_manifest = root / "records" / "manifest.json"
            embedded_events = root / "records" / "events.jsonl"
            job = replace(
                make_job(root, job_id="embedded-job"),
                execution=ExecutionConfig(
                    job_id="embedded-job",
                    manifest_path="records/manifest.json",
                    event_log_path="records/events.jsonl",
                    dry_run=True,
                ),
            )
            save_job_config(job, config)
            outcome = _completed_outcome(
                embedded_manifest,
                event_log=embedded_events,
                job_id="embedded-job",
            )
            expected_manifest = embedded_manifest.resolve()
            expected_event_log = embedded_events.resolve()

            with mock.patch.object(
                cli_runtime.MasteringService,
                "run",
                return_value=outcome,
            ) as service_run:
                result = main(["run", str(config), "--quiet"])

        self.assertEqual(result, EXIT_SUCCESS)
        self.assertEqual(service_run.call_args.kwargs["manifest_path"], expected_manifest)
        self.assertEqual(service_run.call_args.kwargs["event_log_path"], expected_event_log)
        self.assertIsNone(service_run.call_args.kwargs["dry_run"])


class CliErrorContractTests(unittest.TestCase):
    def test_configuration_and_non_domain_errors_use_config_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "broken.json"
            config.write_text("{not-json", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["show-config", str(config)])

        self.assertEqual(result, EXIT_USAGE_OR_CONFIG)
        self.assertIn("Configuration error: [config_invalid]", stderr.getvalue())
        self.assertIn("Expecting property name", stderr.getvalue())

        stderr = io.StringIO()
        with (
            mock.patch.object(
                cli_module,
                "run_doctor",
                side_effect=OSError("diagnostic I/O failed"),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            result = main(["doctor"])

        self.assertEqual(result, EXIT_USAGE_OR_CONFIG)
        self.assertIn("OSError: diagnostic I/O failed", stderr.getvalue())

    def test_domain_failures_map_to_stable_exit_codes(self) -> None:
        cases = (
            (
                "preflight",
                ["validate"],
                "validate_job",
                PreflightError("unsafe input"),
                EXIT_PREFLIGHT,
            ),
            (
                "environment",
                ["doctor"],
                "run_doctor",
                DependencyError("missing package"),
                EXIT_ENVIRONMENT,
            ),
            (
                "capability",
                ["capabilities"],
                "create_engine",
                CapabilityError("unsupported mode"),
                EXIT_CAPABILITY,
            ),
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            save_job_config(make_job(root), config)
            for category, arguments, dependency_name, failure, expected in cases:
                invocation = [*arguments, str(config)] if arguments[0] == "validate" else arguments
                stderr = io.StringIO()
                with (
                    self.subTest(category=category),
                    mock.patch.object(
                        cli_module,
                        dependency_name,
                        side_effect=failure,
                    ),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = main(invocation)

                self.assertEqual(result, expected)
                self.assertIn(f"{category.capitalize()} error:", stderr.getvalue())

    def test_processing_failure_prints_audit_locations_before_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            manifest = root / "audit" / "manifest.json"
            events = root / "audit" / "events.jsonl"
            save_job_config(make_job(root), config)
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    cli_runtime.MasteringService,
                    "run",
                    side_effect=ProcessingError("engine failed"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                result = main(
                    [
                        "run",
                        str(config),
                        "--manifest",
                        str(manifest),
                        "--event-log",
                        str(events),
                        "--quiet",
                    ]
                )

        self.assertEqual(result, EXIT_PROCESSING)
        self.assertIn(f"Manifest: {manifest.resolve()}", stderr.getvalue())
        self.assertIn(f"Event log: {events.resolve()}", stderr.getvalue())
        self.assertIn("Processing error: [processing_failed]", stderr.getvalue())

    def test_catalog_selection_is_never_overwritten_or_shared_with_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            save_job_config(make_job(root), config)
            audit = root / "audit"
            audit.mkdir()

            cases = (
                (audit / "selection.json", "collides with a job artifact"),
                (audit / "manifest.json", "already exists"),
            )
            for manifest, expected in cases:
                selection = manifest.with_name("selection.json")
                if manifest.name != "selection.json":
                    selection.write_text("preserve me", encoding="utf-8")
                stderr = io.StringIO()
                with (
                    self.subTest(manifest=manifest.name),
                    mock.patch.object(cli_runtime.MasteringService, "run") as service_run,
                    contextlib.redirect_stderr(stderr),
                ):
                    result = main(
                        [
                            "run",
                            str(config),
                            "--manifest",
                            str(manifest),
                            "--no-audio-probe",
                            "--quiet",
                        ]
                    )

                self.assertEqual(result, EXIT_USAGE_OR_CONFIG)
                self.assertIn(expected, stderr.getvalue())
                service_run.assert_not_called()
                if selection.is_file():
                    self.assertEqual(selection.read_text(encoding="utf-8"), "preserve me")

    def test_catalog_finalization_failure_preserves_and_reports_completed_render(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            manifest = root / "audit" / "manifest.json"
            events = root / "audit" / "events.jsonl"
            save_job_config(make_job(root), config)
            outcome = _completed_outcome(manifest, event_log=events)
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    cli_runtime.MasteringService,
                    "run",
                    return_value=outcome,
                ),
                mock.patch.object(
                    cli_runtime,
                    "finalize_catalog_run",
                    side_effect=cli_runtime.CatalogError("index unavailable"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                result = main(
                    [
                        "run",
                        str(config),
                        "--manifest",
                        str(manifest),
                        "--event-log",
                        str(events),
                        "--no-audio-probe",
                        "--quiet",
                    ]
                )

        self.assertEqual(result, EXIT_USAGE_OR_CONFIG)
        rendered = stderr.getvalue()
        self.assertIn("Mastering completed, but catalog finalization failed", rendered)
        self.assertIn(f"Manifest: {manifest.resolve()}", rendered)
        self.assertIn(f"Selection: {manifest.with_name('selection.json').resolve()}", rendered)
        self.assertIn(f"Event log: {events.resolve()}", rendered)
        self.assertIn("Catalog error: CatalogError: index unavailable", rendered)

    def test_success_without_authoritative_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "job.json"
            manifest = root / "missing" / "manifest.json"
            save_job_config(make_job(root), config)
            outcome = RunOutcome(
                job_id="missing-manifest",
                dry_run=True,
                manifest_path=str(manifest),
                event_log_path=None,
                validation=ValidationReport(()),
                engine_result=None,
            )
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    cli_runtime.MasteringService,
                    "run",
                    return_value=outcome,
                ),
                contextlib.redirect_stderr(stderr),
            ):
                result = main(
                    [
                        "run",
                        str(config),
                        "--manifest",
                        str(manifest),
                        "--no-event-log",
                        "--no-audio-probe",
                        "--quiet",
                    ]
                )

        self.assertEqual(result, EXIT_PROCESSING)
        rendered = stderr.getvalue()
        self.assertIn("Audit failure:", rendered)
        self.assertIn(f"Expected manifest: {manifest.resolve()}", rendered)
        self.assertIn("manifest_read_failed", rendered)


if __name__ == "__main__":
    unittest.main()
