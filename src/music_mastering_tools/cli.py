"""Command-line launchpad for diagnostics, validation, and audited runs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .catalog import CatalogError, CatalogStore
from .catalog_cli import add_catalog_parser, run_catalog_command
from .catalog_integration import finalize_catalog_run, register_job_selection
from .config import EngineKind, JobConfig, load_job_config
from .doctor import DependencySpec, run_doctor
from .engine import create_engine
from .errors import (
    CapabilityError,
    ConfigError,
    DependencyError,
    ErrorCode,
    ManifestError,
    MusicMasteringError,
    PreflightError,
)
from .events import ConsoleEventSink, EventLevel, EventSink, NullEventSink
from .service import MasteringService
from .validation import ValidationReport, validate_job

EXIT_SUCCESS = 0
EXIT_ENVIRONMENT = 1
EXIT_USAGE_OR_CONFIG = 2
EXIT_PREFLIGHT = 3
EXIT_CAPABILITY = 4
EXIT_PROCESSING = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mmt",
        description=("Private, auditable reference-guided music mastering launchpad."),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=_version_text(),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="inspect Python, FFmpeg, and audio dependencies")
    doctor.add_argument("--json", action="store_true", help="emit JSON")
    doctor.add_argument(
        "--strict-mastering",
        action="store_true",
        help="treat the pinned compatibility engine dependencies as required",
    )
    doctor.add_argument(
        "--no-import-probe",
        action="store_true",
        help="discover packages without importing their native components",
    )

    capabilities = commands.add_parser(
        "capabilities",
        help="show truthful implemented/planned engine capability declarations",
    )
    capabilities.add_argument("--json", action="store_true", help="emit JSON")

    validate = commands.add_parser("validate", help="validate a versioned job without rendering")
    validate.add_argument("config", type=Path)
    validate.add_argument("--json", action="store_true", help="emit JSON")
    validate.add_argument(
        "--no-input-check",
        action="store_true",
        help="allow absent placeholder input paths",
    )
    validate.add_argument(
        "--no-audio-probe",
        action="store_true",
        help="skip decoded audio measurements",
    )

    show = commands.add_parser(
        "show-config",
        help="load, validate structurally, resolve paths, and print a job",
    )
    show.add_argument("config", type=Path)

    run = commands.add_parser("run", help="preflight and execute an audited mastering job")
    run.add_argument("config", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--manifest", type=Path)
    run.add_argument(
        "--catalog",
        type=Path,
        help="private catalog database (default: CONFIG_DIR/.mmt/catalog/catalog.sqlite3)",
    )
    event_group = run.add_mutually_exclusive_group()
    event_group.add_argument("--event-log", type=Path)
    event_group.add_argument("--no-event-log", action="store_true")
    run.add_argument(
        "--no-audio-probe",
        action="store_true",
        help="skip decoded audio measurements during preflight",
    )
    run.add_argument(
        "--json-events",
        action="store_true",
        help="write console progress as JSON Lines",
    )
    run.add_argument("-q", "--quiet", action="store_true")
    run.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="include diagnostic/debug events",
    )

    add_catalog_parser(commands)

    portal = commands.add_parser(
        "gui",
        aliases=["portal"],
        help="open the private localhost graphical mastering portal",
    )
    portal.add_argument(
        "--workspace",
        type=Path,
        default=Path("private-workspace"),
        help="private mutable workspace (default: ./private-workspace)",
    )
    portal.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port; zero chooses an available port (default: 0)",
    )
    portal.add_argument(
        "--no-browser",
        action="store_true",
        help="start without opening the default browser",
    )
    portal.add_argument("--write-ready", type=Path, help=argparse.SUPPRESS)

    workbench = commands.add_parser(
        "workbench",
        help="open the terminal fallback workbench",
    )
    workbench.add_argument(
        "--workspace",
        type=Path,
        default=Path("private-workspace"),
        help="private mutable workspace (default: ./private-workspace)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_arguments)
    try:
        if args.command == "doctor":
            return _doctor_command(args)
        if args.command == "capabilities":
            return _capabilities_command(args)
        if args.command == "validate":
            return _validate_command(args)
        if args.command == "show-config":
            job = _load(args.config)
            sys.stdout.write(job.to_json())
            return EXIT_SUCCESS
        if args.command == "run":
            return _run_command(args, raw_arguments)
        if args.command == "catalog":
            return run_catalog_command(args)
        if args.command in {"gui", "portal"}:
            from .portal import run_portal

            return run_portal(
                args.workspace,
                port=args.port,
                open_browser=not args.no_browser,
                ready_file=args.write_ready,
            )
        if args.command == "workbench":
            from .workbench import run_workbench

            return run_workbench(args.workspace)
        raise AssertionError(f"unhandled command: {args.command}")
    except CatalogError as exc:
        _error("catalog", exc)
        return EXIT_USAGE_OR_CONFIG
    except (OSError, json.JSONDecodeError, ValueError, ConfigError) as exc:
        _error("configuration", exc)
        return EXIT_USAGE_OR_CONFIG
    except PreflightError as exc:
        _error("preflight", exc)
        return EXIT_PREFLIGHT
    except DependencyError as exc:
        _error("environment", exc)
        return EXIT_ENVIRONMENT
    except CapabilityError as exc:
        _error("capability", exc)
        return EXIT_CAPABILITY
    except MusicMasteringError as exc:
        _error("processing", exc)
        return EXIT_PROCESSING


def _doctor_command(args: argparse.Namespace) -> int:
    dependencies = None
    include_optional = True
    if args.strict_mastering:
        include_optional = False
        dependencies = tuple(
            DependencySpec(
                module,
                distribution=distribution,
                purpose=purpose,
                required=True,
                expected_version=expected_version,
            )
            for module, distribution, purpose, expected_version in (
                (
                    "matchering",
                    "matchering",
                    "pinned Matchering 2.0.6 compatibility processing",
                    "2.0.6",
                ),
                ("numpy", "numpy", "numerical DSP", None),
                ("scipy", "scipy", "signal processing", None),
                ("statsmodels", "statsmodels", "LOWESS spectral smoothing", None),
                ("resampy", "resampy", "sample-rate conversion", None),
                ("soundfile", "soundfile", "audio decoding and encoding", None),
            )
        )
    report = run_doctor(
        dependencies=dependencies,
        include_optional_dependencies=include_optional,
        probe_imports=not args.no_import_probe,
    )
    sys.stdout.write(report.to_json() if args.json else report.render_text() + "\n")
    return report.exit_code


def _capabilities_command(args: argparse.Namespace) -> int:
    rows = [
        create_engine(kind).capabilities.to_dict()
        for kind in (EngineKind.UPSTREAM, EngineKind.NATIVE)
    ]
    if args.json:
        sys.stdout.write(json.dumps({"engines": rows}, indent=2, sort_keys=True) + "\n")
        return EXIT_SUCCESS
    for row in rows:
        sys.stdout.write(
            f"{row['engine_id']} {row['engine_version']} [{row['implementation_status']}]\n"
        )
        for name, value in row.items():
            if name in {"engine_id", "engine_version", "implementation_status"}:
                continue
            sys.stdout.write(f"  {name}: {str(value).lower()}\n")
    return EXIT_SUCCESS


def _validate_command(args: argparse.Namespace) -> int:
    job = _load(args.config)
    report = validate_job(
        job,
        inspect_audio=not args.no_audio_probe,
        require_inputs=not args.no_input_check,
    )
    _write_validation(report, json_output=args.json)
    return EXIT_SUCCESS if report.ok else EXIT_PREFLIGHT


def _run_command(
    args: argparse.Namespace,
    raw_arguments: Sequence[str],
) -> int:
    config_path = args.config.resolve()
    job = _load(config_path)
    if job.execution.job_id is None:
        job = replace(
            job,
            execution=replace(job.execution, job_id=uuid.uuid4().hex),
        )

    manifest = (
        args.manifest.resolve()
        if args.manifest is not None
        else (
            Path(job.execution.manifest_path)
            if job.execution.manifest_path
            else config_path.parent
            / ".mmt-runs"
            / (job.execution.job_id or "run")
            / "manifest.json"
        )
    )
    if args.no_event_log:
        event_log = None
    elif args.event_log is not None:
        event_log = args.event_log.resolve()
    elif job.execution.event_log_path:
        event_log = Path(job.execution.event_log_path)
    else:
        event_log = manifest.with_name("events.jsonl")

    sink: EventSink
    if args.quiet:
        sink = NullEventSink()
    else:
        sink = ConsoleEventSink(
            min_level=EventLevel.DEBUG if args.verbose else EventLevel.INFO,
            json_lines=args.json_events,
        )

    catalog_path = (
        args.catalog.resolve()
        if args.catalog is not None
        else config_path.parent / ".mmt" / "catalog" / "catalog.sqlite3"
    )
    selection_path = manifest.with_name("selection.json")
    _validate_catalog_destinations(
        job,
        config_path=config_path,
        catalog_path=catalog_path,
        manifest_path=manifest,
        event_log_path=event_log,
        selection_path=selection_path,
    )

    with CatalogStore(catalog_path) as catalog:
        selection = register_job_selection(
            catalog,
            job,
            inspect_audio=not args.no_audio_probe,
        )
        selection_path.parent.mkdir(parents=True, exist_ok=True)
        with selection_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(selection.to_json())
        try:
            outcome = MasteringService().run(
                job,
                manifest_path=manifest,
                event_log_path=event_log,
                configuration_path=config_path,
                sink=sink,
                dry_run=True if args.dry_run else None,
                command=("mmt", *raw_arguments),
                inspect_audio=not args.no_audio_probe,
            )
        except MusicMasteringError:
            if manifest.is_file():
                try:
                    finalize_catalog_run(
                        catalog,
                        selection,
                        manifest,
                        configuration_path=config_path,
                        selection_path=selection_path,
                    )
                except (CatalogError, MusicMasteringError, OSError, ValueError) as catalog_error:
                    sys.stderr.write(
                        f"Catalog recovery also failed: {type(catalog_error).__name__}: "
                        f"{catalog_error}\n"
                    )
            sys.stderr.write(f"Manifest: {manifest}\n")
            if event_log is not None:
                sys.stderr.write(f"Event log: {event_log}\n")
            raise
        if manifest.is_file():
            try:
                finalize_catalog_run(
                    catalog,
                    selection,
                    manifest,
                    configuration_path=config_path,
                    selection_path=selection_path,
                )
            except (CatalogError, MusicMasteringError, OSError, ValueError):
                sys.stderr.write(
                    "Mastering completed, but catalog finalization failed; "
                    "the render and audit files were preserved.\n"
                    f"Manifest: {manifest}\n"
                    f"Selection: {selection_path}\n"
                )
                if event_log is not None:
                    sys.stderr.write(f"Event log: {event_log}\n")
                raise
        else:
            sys.stderr.write(
                "Audit failure: the mastering service reported success without "
                "committing its authoritative manifest.\n"
                f"Expected manifest: {manifest}\n"
                f"Selection: {selection_path}\n"
            )
            raise ManifestError(
                "A successful mastering run did not create its authoritative manifest.",
                code=ErrorCode.MANIFEST_READ_FAILED,
                details={
                    "manifest_path": str(manifest),
                    "selection_path": str(selection_path),
                    "job_id": outcome.job_id,
                },
            )

    sys.stdout.write(
        f"{'Dry run' if outcome.dry_run else 'Run'} completed: "
        f"{outcome.job_id}\n"
        f"Manifest: {outcome.manifest_path}\n"
    )
    if outcome.event_log_path is not None:
        sys.stdout.write(f"Event log: {outcome.event_log_path}\n")
    return EXIT_SUCCESS


def _validate_catalog_destinations(
    job: JobConfig,
    *,
    config_path: Path,
    catalog_path: Path,
    manifest_path: Path,
    event_log_path: Path | None,
    selection_path: Path,
) -> None:
    job_artifacts = [
        Path(job.target),
        *(Path(item.path) for item in job.references),
        *(Path(item.path) for item in job.outputs),
        *(
            Path(value)
            for value in (job.preview.target_path, job.preview.result_path)
            if value is not None
        ),
        config_path,
        manifest_path,
    ]
    if event_log_path is not None:
        job_artifacts.append(event_log_path)
    protected = [*job_artifacts, selection_path]
    catalog_files = (
        catalog_path,
        Path(f"{catalog_path}-wal"),
        Path(f"{catalog_path}-shm"),
    )
    for catalog_file in catalog_files:
        for other in protected:
            if _same_path_or_file(catalog_file, other):
                raise ValueError(f"catalog storage collides with a job artifact: {catalog_file}")
    for other in job_artifacts:
        if _same_path_or_file(selection_path, other):
            raise ValueError(
                f"catalog selection path collides with a job artifact: {selection_path}"
            )
    if selection_path.exists():
        raise ValueError(
            f"catalog selection artifact already exists; choose a new manifest path: "
            f"{selection_path}"
        )


def _same_path_or_file(first: Path, second: Path) -> bool:
    if os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve())):
        return True
    try:
        return first.exists() and second.exists() and os.path.samefile(first, second)
    except OSError:
        return False


def _load(path: Path) -> JobConfig:
    try:
        return load_job_config(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(
            f"Could not load job configuration '{path}': {exc}",
            details={"path": str(path), "exception_type": type(exc).__name__},
        ) from exc


def _write_validation(report: ValidationReport, *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
        return
    status = "PASS" if report.ok else "FAIL"
    sys.stdout.write(
        f"Validation {status}: {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s), "
        f"{len(report.audio_facts)} audio file(s) inspected.\n"
    )
    for issue in report.issues:
        location = f" [{issue.path}]" if issue.path else ""
        sys.stdout.write(
            f"- {issue.severity.value.upper()} {issue.code}{location}: {issue.message}\n"
        )
        if issue.remediation:
            sys.stdout.write(f"  Remedy: {issue.remediation}\n")


def _error(category: str, error: BaseException) -> None:
    if isinstance(error, MusicMasteringError):
        rendered = str(error)
    else:
        rendered = f"{type(error).__name__}: {error}"
    sys.stderr.write(f"{category.capitalize()} error: {rendered}\n")


def _version_text() -> str:
    from . import __version__

    return f"music-mastering-tools {__version__}"


__all__ = ["build_parser", "main"]
