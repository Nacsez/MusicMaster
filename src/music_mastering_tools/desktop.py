"""Portable Windows launcher with stable per-user state and retained diagnostics.

The executable is read-only application code. Catalogs, preferences, logs and
deliveries belong to the current user, never the executable's extraction folder.
An OS-held workspace lock prevents two launchers from running mastering at once.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import webbrowser
from collections.abc import Mapping
from datetime import UTC, datetime
from http.client import HTTPResponse
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from . import __version__
from .doctor import DEFAULT_OPTIONAL_DEPENDENCIES, DependencySpec, run_doctor
from .portal import run_portal

APP_DIRECTORY = "MusicMasteringTools"


def default_workspace(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve user data independently of cwd, installation, and frozen assets."""

    values = os.environ if environment is None else environment
    app_data = values.get("LOCALAPPDATA")
    root = Path(app_data) if app_data else Path.home() / "AppData" / "Local"
    if not root.is_absolute():
        raise ValueError("LOCALAPPDATA must be an absolute directory")
    return root / APP_DIRECTORY / "workspace"


class WorkspaceInUseError(RuntimeError):
    """The operating system reports an existing owner of this workspace."""


class WorkspaceLease:
    """An automatically released file lock; stale files are harmless after exit."""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace / ".desktop.lock"
        self.handle: BinaryIO | None = None

    def __enter__(self) -> WorkspaceLease:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise WorkspaceInUseError(f"Workspace already open: {self.path.parent}") from exc
        self.handle = handle
        return self

    def __exit__(self, *args: object) -> None:
        # Closing the handle releases the OS lock even on unexpected termination.
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def _notify(message: str, *, error: bool = False) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(
            0, message, "Music Mastering Tools", 0x10 if error else 0x40
        )
    else:
        print(message, file=sys.stderr, flush=True)


def _reopen_existing(workspace: Path, *, open_browser: bool) -> bool:
    """Only activate a live, authenticated loopback session for this workspace."""

    try:
        session = json.loads((workspace / ".desktop-session.json").read_text(encoding="utf-8"))
        url = session["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path != "/"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            return False
        token = parse_qs(parsed.query)["token"][0]
        request = Request(
            f"http://127.0.0.1:{parsed.port}/api/bootstrap",
            headers={"X-MMT-Token": token},
        )
        with _loopback_open(request, timeout=3) as response:
            bootstrap = json.load(response)
        if Path(bootstrap["data"]["workspace"]["root"]).resolve() != workspace:
            return False
        if open_browser and not webbrowser.open(url, new=1, autoraise=True):
            _notify(f"The workbench is already running. Open this URL in a browser:\n\n{url}")
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _loopback_open(request: Request, *, timeout: float) -> HTTPResponse:
    # Environment proxies must never receive the local session credential.
    return cast(HTTPResponse, build_opener(ProxyHandler({})).open(request, timeout=timeout))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Music Mastering Tools for Windows")
    parser.add_argument("--workspace", type=Path, help="override the per-user library directory")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    lifetime = parser.add_mutually_exclusive_group()
    lifetime.add_argument(
        "--keep-running",
        action="store_true",
        help="keep the server running after browser tabs close (manual shutdown)",
    )
    lifetime.add_argument(
        "--exit-with-browser",
        action="store_true",
        help="track browser tabs even when --no-browser is used",
    )
    parser.add_argument("--verbose", action="store_true", help="retain additional startup detail")
    parser.add_argument(
        "--check-only", action="store_true", help="check bundled audio dependencies"
    )
    parser.add_argument("--diagnostics-output", type=Path, help="write check results as JSON")
    parser.add_argument("--write-ready", type=Path, help=argparse.SUPPRESS)
    return parser


def _write_readiness(path: Path, ready: Mapping[str, object]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    logger = logging.getLogger(f"music_mastering_tools.desktop.{os.getpid()}")
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    logger.propagate = False
    package_logger = logging.getLogger("music_mastering_tools")
    previous_package_level = package_logger.level
    package_handler: logging.FileHandler | None = None
    log_path: Path | None = None
    try:
        workspace = (args.workspace or default_workspace()).resolve()
        log_directory = workspace / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        log_path = log_directory / f"desktop-{stamp}-{os.getpid()}.log"
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        # Native Shell and other module diagnostics are otherwise discarded in
        # a console-free launch. Retain them in the same discoverable session log.
        package_logger.setLevel(logger.level)
        package_logger.addHandler(handler)
        package_handler = handler
        logger.info(
            "Desktop startup version=%s frozen=%s executable=%s workspace=%s cwd=%s",
            __version__,
            bool(getattr(sys, "frozen", False)),
            sys.executable,
            workspace,
            Path.cwd(),
        )
        # Windowed PyInstaller sets standard streams to None. Libraries may print;
        # keep those calls valid without putting a console in the user's workflow.
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        dependencies = tuple(
            DependencySpec(
                item.module,
                distribution=item.distribution,
                purpose=item.purpose,
                required=True,
                expected_version=item.expected_version,
            )
            for item in DEFAULT_OPTIONAL_DEPENDENCIES
        )
        if args.check_only:
            report = run_doctor(dependencies=dependencies, include_optional_dependencies=False)
            result = report.to_dict()
            if args.diagnostics_output is not None:
                destination = args.diagnostics_output.resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            logger.info("Dependency check: %s", json.dumps(result, sort_keys=True))
            if not args.no_browser:
                _notify(
                    f"Dependency checks {'passed' if report.healthy else 'failed'}.\n\n"
                    f"Log: {log_path}",
                    error=not report.healthy,
                )
            return report.exit_code

        try:
            with WorkspaceLease(workspace):
                session_path = workspace / ".desktop-session.json"
                # Never reuse a token left by a crashed process that owned the old lock.
                session_path.unlink(missing_ok=True)
                try:
                    exit_with_browser = args.exit_with_browser or (
                        not args.no_browser and not args.keep_running
                    )
                    logger.info(
                        "Browser lifetime enabled=%s open_browser=%s",
                        exit_with_browser,
                        not args.no_browser,
                    )
                    return run_portal(
                        workspace,
                        open_browser=not args.no_browser,
                        exit_with_browser=exit_with_browser,
                        ready_file=session_path,
                        on_ready=(
                            (lambda ready: _write_readiness(args.write_ready, ready))
                            if args.write_ready is not None
                            else None
                        ),
                        browser_failure=lambda url: _notify(
                            f"Open this URL in a local browser:\n\n{url}\n\nLog: {log_path}"
                        ),
                    )
                finally:
                    session_path.unlink(missing_ok=True)
        except WorkspaceInUseError:
            if _reopen_existing(workspace, open_browser=not args.no_browser):
                logger.info("Activated the existing workspace session.")
                return 0
            logger.warning("Workspace is locked; existing portal is still starting or unavailable.")
            if not args.no_browser:
                _notify(
                    "This library is already open or starting. Wait a few seconds and try again."
                )
            return 2
    except Exception:  # noqa: BLE001 - retain all startup failures for a windowed launch
        logger.exception("Desktop startup failed.")
        if not args.no_browser:
            _notify(
                f"The workbench could not start.\n\nLog: {log_path or 'unavailable'}", error=True
            )
        return 1
    finally:
        if package_handler is not None:
            package_logger.removeHandler(package_handler)
            package_logger.setLevel(previous_package_level)
        for log_handler in tuple(logger.handlers):
            log_handler.close()
            logger.removeHandler(log_handler)


if __name__ == "__main__":
    raise SystemExit(main())
