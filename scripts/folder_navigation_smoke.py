"""Opt-in Windows Explorer acceptance with isolated fixtures and durable evidence.

Run on an interactive Windows desktop after bootstrapping the repository. The
check opens one fixture folder, verifies an actual file selection through Shell
COM, then verifies directory navigation. It closes only newly created Explorer
windows displaying this run's unique fixture path. Existing windows stay open.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from music_mastering_tools.native_dialogs import _windows_system_executable, show_in_folder

_LOGGER = logging.getLogger("music_mastering_tools.folder_navigation_smoke")
_WINDOW_HANDLES_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject Shell.Application
$handles = @(foreach ($window in @($shell.Windows())) { [long]$window.HWND })
[Console]::Out.WriteLine((ConvertTo-Json -InputObject $handles -Compress))
"""
_INSPECT_WINDOW_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject Shell.Application
$existingHandles = @($env:MMT_SMOKE_EXISTING_WINDOWS | ConvertFrom-Json)
$match = $null
$selectedPaths = @()
$result = $null
try {
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        foreach ($window in @($shell.Windows())) {
            try {
                $opened = [string]$window.Document.Folder.Self.Path
                if ([string]::Equals(
                    $opened, $env:MMT_SMOKE_FOLDER, [StringComparison]::OrdinalIgnoreCase)) {
                    $match = $window
                    $selectedItems = $window.Document.SelectedItems()
                    # COM collections require indexed enumeration here; piping
                    # FolderItems can yield the collection instead of its files.
                    $selectedPaths = @(for ($index = 0; $index -lt $selectedItems.Count; $index++) {
                        [string]$selectedItems.Item($index).Path
                    })
                    if ($env:MMT_SMOKE_FILE -eq '' -or
                        $selectedPaths -contains $env:MMT_SMOKE_FILE) { break }
                }
            } catch { }
        }
        if ($null -ne $match -and ($env:MMT_SMOKE_FILE -eq '' -or
            $selectedPaths -contains $env:MMT_SMOKE_FILE)) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $match) { throw 'Explorer did not open the expected test folder.' }
    $folderItems = $match.Document.Folder.Items()
    $result = @{
        directory = [string]$match.Document.Folder.Self.Path
        selected_paths = @($selectedPaths)
        folder_items = @(for ($index = 0; $index -lt $folderItems.Count; $index++) {
            [string]$folderItems.Item($index).Path
        })
        window_handle = [long]$match.HWND
        closed_test_window = $false
    }
    if ($env:MMT_SMOKE_FILE -ne '' -and $selectedPaths -notcontains $env:MMT_SMOKE_FILE) {
        [Console]::Error.WriteLine(($result | ConvertTo-Json -Compress))
        throw 'Explorer opened the folder but did not select the expected test file.'
    }
} finally {
    if ($null -ne $match -and $existingHandles -notcontains [long]$match.HWND) {
        $match.Quit()
        if ($null -ne $result) { $result.closed_test_window = $true }
    }
}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))
"""
_CLOSE_TEST_WINDOW_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject Shell.Application
$existingHandles = @($env:MMT_SMOKE_EXISTING_WINDOWS | ConvertFrom-Json)
foreach ($window in @($shell.Windows())) {
    try {
        $opened = [string]$window.Document.Folder.Self.Path
        if ($existingHandles -notcontains [long]$window.HWND -and
            [string]::Equals(
                $opened, $env:MMT_SMOKE_FOLDER, [StringComparison]::OrdinalIgnoreCase)) {
            $window.Quit()
        }
    } catch { }
}
"""


def _powershell(script: str, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        [
            _windows_system_executable("System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-Command",
            script,
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        shell=False,
        timeout=20,
        check=False,
    )
    _LOGGER.debug(
        "Shell inspection exit=%s stdout=%s stderr=%s",
        completed.returncode,
        completed.stdout.strip(),
        completed.stderr.strip(),
    )
    if completed.returncode:
        raise RuntimeError(f"Explorer inspection failed: {completed.stderr.strip()}")
    return completed.stdout


def _verify_explorer(folder: Path, selected: Path, *, mode: str) -> dict[str, Any]:
    environment = dict(os.environ)
    handles = json.loads(_powershell(_WINDOW_HANDLES_SCRIPT, environment))
    if not isinstance(handles, list) or not all(type(value) is int for value in handles):
        raise ValueError("Shell inspection returned invalid existing window handles")
    environment.update(
        MMT_SMOKE_FOLDER=str(folder),
        MMT_SMOKE_FILE=str(selected) if mode == "file" else "",
        MMT_SMOKE_EXISTING_WINDOWS=json.dumps(handles),
    )
    target = selected if mode == "file" else folder
    _LOGGER.info("Checking real Explorer mode=%s target=%s", mode, target)
    try:
        show_in_folder(target)
        result = json.loads(_powershell(_INSPECT_WINDOW_SCRIPT, environment))
        if not isinstance(result, dict) or Path(result.get("directory", "")) != folder:
            raise ValueError(f"Explorer opened an unexpected directory: {result}")
        if mode == "file" and str(selected) not in result.get("selected_paths", []):
            raise ValueError(f"Explorer did not select the requested file: {result}")
        result["mode"] = mode
        _LOGGER.info(
            "Explorer verified mode=%s directory=%s closed_test_window=%s",
            mode,
            result["directory"],
            result.get("closed_test_window"),
        )
        return result
    finally:
        # Includes timeout/error recovery. Never close a preexisting window or
        # anything whose current folder differs from the unique fixture folder.
        try:
            _powershell(_CLOSE_TEST_WINDOW_SCRIPT, environment)
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            _LOGGER.warning("Could not close this smoke run's Explorer window", exc_info=True)


def main() -> int:
    if sys.platform != "win32":
        print(
            "Folder navigation acceptance requires an interactive Windows desktop.", file=sys.stderr
        )
        return 2
    project_root = Path(__file__).resolve().parents[1]
    expected_artifact_root = project_root / "artifacts" / "folder-navigation-smoke"
    artifact_root = expected_artifact_root.resolve()
    if artifact_root != expected_artifact_root:
        raise PermissionError("folder smoke artifacts path is redirected through a link")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    run_directory = artifact_root / stamp
    run_directory.mkdir(parents=True, exist_ok=False)
    file_log = logging.FileHandler(run_directory / "smoke.log", encoding="utf-8")
    file_log.setLevel(logging.DEBUG)
    console_log = logging.StreamHandler()
    console_log.setLevel(logging.INFO)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_log, console_log],
    )
    folder = run_directory / "Unicode \u00fc, with spaces"
    folder.mkdir()
    selected = folder / "master \u00fc, spaced.wav"
    selected.touch()
    report: dict[str, Any] = {
        "kind": "music-mastering-tools/folder-navigation-smoke",
        "schema_version": 1,
        "passed": False,
        "artifact_directory": str(run_directory),
        "checks": [],
    }
    try:
        report["checks"].append(_verify_explorer(folder, selected, mode="file"))
        report["checks"].append(_verify_explorer(folder, selected, mode="directory"))
        report["passed"] = True
        _LOGGER.info("Folder navigation acceptance passed")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        _LOGGER.exception("Folder navigation acceptance failed")
        return 1
    finally:
        result_path = run_directory / "result.json"
        result_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        _LOGGER.info("Acceptance evidence saved path=%s", result_path)
        logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
