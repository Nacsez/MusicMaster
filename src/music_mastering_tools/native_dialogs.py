"""Small Windows-native file-dialog boundary for the local portal.

The portal never interpolates request values into PowerShell source.  Each
operation uses one of the fixed scripts below, while titles, filters, and
initial paths travel through child-process environment variables.  This keeps
the desktop integration narrow enough to audit and straightforward to mock in
headless tests.
"""

from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


class NativeDialogError(RuntimeError):
    """A native dialog or Explorer integration operation could not complete."""


class NativeDialogUnavailableError(NativeDialogError):
    """The Windows-only native dialog boundary is unavailable."""


class DialogAction(StrEnum):
    """Supported native dialog operations."""

    OPEN_AUDIO = "open-audio"
    OPEN_JSON = "open-json"
    SAVE_JSON = "save-json"
    CHOOSE_FOLDER = "choose-folder"


class JsonPurpose(StrEnum):
    """Known JSON document purposes exposed by the private application."""

    CATALOG = "catalog"
    SELECTION = "selection"
    JOB_CONFIGURATION = "job-configuration"
    RUN_MANIFEST = "run-manifest"


@dataclass(frozen=True, slots=True)
class NativeDialogRequest:
    """One validated request for a Windows-native picker.

    Every operation returns a tuple of paths.  A user cancellation is the
    empty tuple; successful single-selection operations contain exactly one
    path.
    """

    action: DialogAction
    json_purpose: JsonPurpose | None = None
    multiple: bool = False
    initial_directory: Path | None = None
    suggested_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, DialogAction):
            raise TypeError("action must be a DialogAction")
        if self.json_purpose is not None and not isinstance(self.json_purpose, JsonPurpose):
            raise TypeError("json_purpose must be a JsonPurpose or None")
        if not isinstance(self.multiple, bool):
            raise TypeError("multiple must be a bool")
        if self.initial_directory is not None and not isinstance(self.initial_directory, Path):
            raise TypeError("initial_directory must be a pathlib.Path or None")
        if self.suggested_name is not None:
            _validate_suggested_name(self.suggested_name)

        is_json = self.action in {DialogAction.OPEN_JSON, DialogAction.SAVE_JSON}
        if is_json != (self.json_purpose is not None):
            raise ValueError("json_purpose is required only for JSON dialog actions")
        if self.multiple and self.action is not DialogAction.OPEN_AUDIO:
            raise ValueError("multiple selection is supported only for open-audio")
        if self.suggested_name is not None and self.action is not DialogAction.SAVE_JSON:
            raise ValueError("suggested_name is supported only for save-json")

    @classmethod
    def open_audio(
        cls,
        *,
        multiple: bool = False,
        initial_directory: Path | None = None,
    ) -> NativeDialogRequest:
        """Build a request to choose one or several audio files."""

        return cls(
            DialogAction.OPEN_AUDIO,
            multiple=multiple,
            initial_directory=initial_directory,
        )

    @classmethod
    def open_json(
        cls,
        purpose: JsonPurpose,
        *,
        initial_directory: Path | None = None,
    ) -> NativeDialogRequest:
        """Build a request to choose one JSON document of a known purpose."""

        return cls(
            DialogAction.OPEN_JSON,
            json_purpose=purpose,
            initial_directory=initial_directory,
        )

    @classmethod
    def save_json(
        cls,
        purpose: JsonPurpose,
        *,
        initial_directory: Path | None = None,
        suggested_name: str | None = None,
    ) -> NativeDialogRequest:
        """Build a request to choose a destination for a JSON document."""

        return cls(
            DialogAction.SAVE_JSON,
            json_purpose=purpose,
            initial_directory=initial_directory,
            suggested_name=suggested_name,
        )

    @classmethod
    def choose_folder(
        cls,
        *,
        initial_directory: Path | None = None,
    ) -> NativeDialogRequest:
        """Build a request to choose one folder."""

        return cls(
            DialogAction.CHOOSE_FOLDER,
            initial_directory=initial_directory,
        )


@dataclass(frozen=True, slots=True)
class _JsonPresentation:
    open_title: str
    save_title: str
    default_name: str


_JSON_PRESENTATIONS: dict[JsonPurpose, _JsonPresentation] = {
    JsonPurpose.CATALOG: _JsonPresentation(
        "Open catalog JSON",
        "Save catalog JSON",
        "catalog.json",
    ),
    JsonPurpose.SELECTION: _JsonPresentation(
        "Open track selection JSON",
        "Save track selection JSON",
        "selection.json",
    ),
    JsonPurpose.JOB_CONFIGURATION: _JsonPresentation(
        "Open mastering job configuration",
        "Save mastering job configuration",
        "job.json",
    ),
    JsonPurpose.RUN_MANIFEST: _JsonPresentation(
        "Open mastering run manifest",
        "Save mastering run manifest",
        "manifest.json",
    ),
}

_AUDIO_FILTER = (
    "Audio files (*.wav;*.wave;*.aif;*.aiff;*.flac;*.ogg;*.oga;*.mp3;*.m4a;*.aac;*.wma)"
    "|*.wav;*.wave;*.aif;*.aiff;*.flac;*.ogg;*.oga;*.mp3;*.m4a;*.aac;*.wma"
    "|All files (*.*)|*.*"
)
_JSON_FILTER = "JSON files (*.json)|*.json|All files (*.*)|*.*"

_OPEN_FILE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $dialog = [System.Windows.Forms.OpenFileDialog]::new()
    $dialog.Title = $env:MMT_DIALOG_TITLE
    $dialog.Filter = $env:MMT_DIALOG_FILTER
    $dialog.FilterIndex = 1
    $dialog.Multiselect = ($env:MMT_DIALOG_MULTIPLE -eq '1')
    $dialog.CheckFileExists = $true
    $dialog.CheckPathExists = $true
    $dialog.DereferenceLinks = $true
    $dialog.RestoreDirectory = $true
    if (-not [string]::IsNullOrWhiteSpace($env:MMT_DIALOG_INITIAL_DIRECTORY) -and
        [System.IO.Directory]::Exists($env:MMT_DIALOG_INITIAL_DIRECTORY)) {
        $dialog.InitialDirectory = $env:MMT_DIALOG_INITIAL_DIRECTORY
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        foreach ($selectedPath in $dialog.FileNames) {
            [Console]::Out.WriteLine($selectedPath)
        }
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $dialog) {
        $dialog.Dispose()
    }
}
"""

_SAVE_FILE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $dialog = [System.Windows.Forms.SaveFileDialog]::new()
    $dialog.Title = $env:MMT_DIALOG_TITLE
    $dialog.Filter = $env:MMT_DIALOG_FILTER
    $dialog.FilterIndex = 1
    $dialog.DefaultExt = 'json'
    $dialog.AddExtension = $true
    $dialog.OverwritePrompt = $true
    $dialog.CheckPathExists = $true
    $dialog.RestoreDirectory = $true
    $dialog.FileName = $env:MMT_DIALOG_SUGGESTED_NAME
    if (-not [string]::IsNullOrWhiteSpace($env:MMT_DIALOG_INITIAL_DIRECTORY) -and
        [System.IO.Directory]::Exists($env:MMT_DIALOG_INITIAL_DIRECTORY)) {
        $dialog.InitialDirectory = $env:MMT_DIALOG_INITIAL_DIRECTORY
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Out.WriteLine($dialog.FileName)
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $dialog) {
        $dialog.Dispose()
    }
}
"""

_CHOOSE_FOLDER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
    $dialog.Description = $env:MMT_DIALOG_TITLE
    $dialog.RootFolder = [System.Environment+SpecialFolder]::Desktop
    $dialog.ShowNewFolderButton = $true
    if (-not [string]::IsNullOrWhiteSpace($env:MMT_DIALOG_INITIAL_DIRECTORY) -and
        [System.IO.Directory]::Exists($env:MMT_DIALOG_INITIAL_DIRECTORY)) {
        $dialog.SelectedPath = $env:MMT_DIALOG_INITIAL_DIRECTORY
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Out.WriteLine($dialog.SelectedPath)
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $dialog) {
        $dialog.Dispose()
    }
}
"""

_WAIT_EXPLORER_FOLDER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $shell = New-Object -ComObject Shell.Application
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        foreach ($window in @($shell.Windows())) {
            try {
                $opened = [string]$window.Document.Folder.Self.Path
                if ([string]::Equals(
                    $opened, $env:MMT_EXPLORER_DIRECTORY, [StringComparison]::OrdinalIgnoreCase)) {
                    exit 0
                }
            } catch { }
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Explorer did not expose the requested folder view within 10 seconds.'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def run_dialog(request: NativeDialogRequest) -> tuple[Path, ...]:
    """Show one validated native dialog and return selected paths.

    The empty tuple means that the operator cancelled the dialog.  Failures to
    start PowerShell or construct WinForms are raised as
    :class:`NativeDialogError`.
    """

    if not isinstance(request, NativeDialogRequest):
        raise TypeError("request must be a NativeDialogRequest")
    _require_windows()
    script = _script_for(request.action)
    environment = _dialog_environment(request)
    _LOGGER.debug(
        "Opening native picker action=%s initial_directory=%s multiple=%s",
        request.action.value,
        environment["MMT_DIALOG_INITIAL_DIRECTORY"],
        request.multiple,
    )
    command = (
        _windows_system_executable(
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        ),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Sta",
        "-Command",
        script,
    )
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except OSError as exc:
        raise NativeDialogError(f"Could not start the Windows dialog host: {exc}") from exc
    if completed.returncode != 0:
        detail = _one_line(completed.stderr) or "PowerShell returned no diagnostic detail"
        raise NativeDialogError(
            f"Windows dialog host failed with exit code {completed.returncode}: {detail}"
        )
    paths = _parse_selected_paths(completed.stdout, request)
    _LOGGER.debug(
        "Native picker completed action=%s selected_count=%s", request.action.value, len(paths)
    )
    return paths


def choose_paths(
    mode: str,
    purpose: str,
    suggested_name: str | None = None,
    *,
    initial_directory: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Stable string-oriented adapter used by the local portal server.

    Supported ``(mode, purpose)`` combinations are:

    - ``("open-audio", "audio")``;
    - ``("open-audio-many", "audio")``;
    - ``("open-json", JSON_PURPOSE)``;
    - ``("save-json", JSON_PURPOSE)``; and
    - ``("choose-folder", "folder")``.

    JSON purposes are the values declared by :class:`JsonPurpose`.  A
    suggested name is accepted only for ``save-json`` and must be a plain
    ``.json`` file name.
    """

    if not isinstance(mode, str):
        raise TypeError("mode must be a string")
    if not isinstance(purpose, str):
        raise TypeError("purpose must be a string")
    if suggested_name is not None and not isinstance(suggested_name, str):
        raise TypeError("suggested_name must be a string or None")
    initial_path = Path(initial_directory) if initial_directory is not None else None
    if mode == "open-audio":
        _require_simple_purpose(mode, purpose, "audio")
        _reject_simple_suggested_name(mode, suggested_name)
        request = NativeDialogRequest.open_audio(initial_directory=initial_path)
    elif mode == "open-audio-many":
        _require_simple_purpose(mode, purpose, "audio")
        _reject_simple_suggested_name(mode, suggested_name)
        request = NativeDialogRequest.open_audio(multiple=True, initial_directory=initial_path)
    elif mode in {"open-json", "save-json"}:
        try:
            json_purpose = JsonPurpose(purpose)
        except ValueError as exc:
            expected = ", ".join(item.value for item in JsonPurpose)
            raise ValueError(f"purpose for {mode!r} must be one of: {expected}") from exc
        if mode == "open-json":
            _reject_simple_suggested_name(mode, suggested_name)
            request = NativeDialogRequest.open_json(json_purpose, initial_directory=initial_path)
        else:
            request = NativeDialogRequest.save_json(
                json_purpose,
                suggested_name=suggested_name,
                initial_directory=initial_path,
            )
    elif mode == "choose-folder":
        _require_simple_purpose(mode, purpose, "folder")
        _reject_simple_suggested_name(mode, suggested_name)
        request = NativeDialogRequest.choose_folder(initial_directory=initial_path)
    else:
        raise ValueError(
            "mode must be one of: open-audio, open-audio-many, open-json, save-json, choose-folder"
        )
    return tuple(str(path) for path in run_dialog(request))


def show_in_folder(path: str | os.PathLike[str]) -> None:
    """Open a directory, or select an existing file in its containing folder.

    Files use the Windows Shell API rather than Explorer's unusual command
    line grammar. Quoting a combined ``/select,<path with spaces>`` argument
    can make Explorer silently fall back to Documents instead of that path.
    """

    _require_windows()
    selected = Path(path).expanduser().resolve()
    if not selected.exists():
        raise NativeDialogError(f"The requested location no longer exists: {selected}")
    if selected.is_file():
        _open_file_selection(selected)
        _LOGGER.info("Explorer selected file path=%s", selected)
        return
    if not selected.is_dir():
        raise NativeDialogError(f"The requested location is not a file or directory: {selected}")
    command = (
        _windows_system_executable("explorer.exe"),
        str(selected),
    )
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise NativeDialogError(f"Could not start Windows Explorer: {exc}") from exc
    _LOGGER.info("Explorer opened directory path=%s", selected)


def _load_shell_libraries() -> tuple[Any, Any]:
    """Declare pointer-sized Shell/COM functions before crossing the ABI."""

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    hresult = ctypes.c_int32
    pointer = ctypes.c_void_p
    uint = ctypes.c_uint32
    shell.SHParseDisplayName.argtypes = [
        ctypes.c_wchar_p,
        pointer,
        ctypes.POINTER(pointer),
        uint,
        ctypes.POINTER(uint),
    ]
    shell.SHParseDisplayName.restype = hresult
    shell.SHOpenFolderAndSelectItems.argtypes = [pointer, uint, ctypes.POINTER(pointer), uint]
    shell.SHOpenFolderAndSelectItems.restype = hresult
    ole.CoInitializeEx.argtypes = [pointer, uint]
    ole.CoInitializeEx.restype = hresult
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [pointer]
    ole.CoTaskMemFree.restype = None
    return shell, ole


def _open_file_selection(selected: Path) -> None:
    """Use a Unicode PIDL to reveal a file; always release COM allocations."""

    try:
        shell, ole = _load_shell_libraries()
    except OSError as exc:
        raise NativeDialogError(f"Could not load Windows Explorer integration: {exc}") from exc
    initialized = int(ole.CoInitializeEx(None, 0x2))  # COINIT_APARTMENTTHREADED
    # An already initialized MTA remains usable. Only successful calls own a
    # matching CoUninitialize; RPC_E_CHANGED_MODE must not be balanced.
    changed_mode = (initialized & 0xFFFFFFFF) == 0x80010106
    if not changed_mode:
        _check_hresult(initialized, "initialize Windows Shell", selected)
    item_id = ctypes.c_void_p()
    attributes = ctypes.c_uint32()
    try:
        result = shell.SHParseDisplayName(
            str(selected), None, ctypes.byref(item_id), 0, ctypes.byref(attributes)
        )
        _check_hresult(int(result), "resolve the Explorer location", selected)
        if not item_id.value:
            raise NativeDialogError(f"Windows Shell returned no location identifier: {selected}")
        # cidl=0 means the absolute PIDL identifies the file itself: Windows
        # opens its parent directory and selects the file without executing it.
        result = shell.SHOpenFolderAndSelectItems(item_id, 0, None, 0)
        _check_hresult(int(result), "show the Explorer location", selected)
        # On first use of a new folder, Explorer can load the correct directory
        # while dropping the initial file selection. Wait for that exact view
        # to exist, then repeat the native selection without guessing a delay.
        _wait_for_explorer_directory(selected.parent)
        result = shell.SHOpenFolderAndSelectItems(item_id, 0, None, 0)
        _check_hresult(int(result), "select the file in the Explorer folder", selected)
    finally:
        if item_id.value:
            ole.CoTaskMemFree(item_id)
        if not changed_mode:
            ole.CoUninitialize()


def _wait_for_explorer_directory(directory: Path) -> None:
    environment = dict(os.environ, MMT_EXPLORER_DIRECTORY=str(directory))
    command = (
        _windows_system_executable("System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Sta",
        "-Command",
        _WAIT_EXPLORER_FOLDER_SCRIPT,
    )
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeDialogError(f"Could not confirm the Explorer folder view: {exc}") from exc
    if completed.returncode:
        raise NativeDialogError(
            f"Could not confirm the Explorer folder view: {_one_line(completed.stderr)}"
        )
    _LOGGER.debug("Explorer folder view ready path=%s", directory)


def _check_hresult(result: int, operation: str, path: Path) -> None:
    if result & 0x80000000:
        raise NativeDialogError(
            f"Could not {operation} (HRESULT 0x{result & 0xFFFFFFFF:08X}): {path}"
        )


def _script_for(action: DialogAction) -> str:
    if action in {DialogAction.OPEN_AUDIO, DialogAction.OPEN_JSON}:
        return _OPEN_FILE_SCRIPT
    if action is DialogAction.SAVE_JSON:
        return _SAVE_FILE_SCRIPT
    if action is DialogAction.CHOOSE_FOLDER:
        return _CHOOSE_FOLDER_SCRIPT
    raise AssertionError(f"unhandled dialog action: {action}")


def _dialog_environment(request: NativeDialogRequest) -> dict[str, str]:
    environment = dict(os.environ)
    environment["MMT_DIALOG_INITIAL_DIRECTORY"] = (
        str(_existing_initial_directory(request.initial_directory))
        if request.initial_directory is not None
        else ""
    )
    environment["MMT_DIALOG_MULTIPLE"] = "1" if request.multiple else "0"
    if request.action is DialogAction.OPEN_AUDIO:
        environment["MMT_DIALOG_TITLE"] = (
            "Open audio tracks" if request.multiple else "Open audio track"
        )
        environment["MMT_DIALOG_FILTER"] = _AUDIO_FILTER
        environment["MMT_DIALOG_SUGGESTED_NAME"] = ""
        return environment
    if request.action is DialogAction.CHOOSE_FOLDER:
        environment["MMT_DIALOG_TITLE"] = "Choose a folder"
        environment["MMT_DIALOG_FILTER"] = ""
        environment["MMT_DIALOG_SUGGESTED_NAME"] = ""
        return environment

    purpose = request.json_purpose
    if purpose is None:
        raise AssertionError("validated JSON dialog request has no purpose")
    presentation = _JSON_PRESENTATIONS[purpose]
    environment["MMT_DIALOG_TITLE"] = (
        presentation.open_title
        if request.action is DialogAction.OPEN_JSON
        else presentation.save_title
    )
    environment["MMT_DIALOG_FILTER"] = _JSON_FILTER
    environment["MMT_DIALOG_SUGGESTED_NAME"] = (
        request.suggested_name or presentation.default_name
        if request.action is DialogAction.SAVE_JSON
        else ""
    )
    return environment


def _existing_initial_directory(requested: Path) -> Path:
    """Start at the selected directory or its nearest existing ancestor."""

    directory = requested.expanduser().resolve()
    original = directory
    while not directory.is_dir() and directory.parent != directory:
        directory = directory.parent
    if directory != original:
        _LOGGER.debug(
            "Picker initial location resolved requested=%s directory=%s", original, directory
        )
    return directory


def _parse_selected_paths(
    output: str,
    request: NativeDialogRequest,
) -> tuple[Path, ...]:
    paths = tuple(
        Path(line.strip().lstrip("\ufeff"))
        for line in output.splitlines()
        if line.strip().lstrip("\ufeff")
    )
    if not paths:
        return ()
    if request.action is not DialogAction.OPEN_AUDIO or not request.multiple:
        if len(paths) != 1:
            raise NativeDialogError(
                f"{request.action.value} returned {len(paths)} paths; expected at most one"
            )
    if request.action in {DialogAction.OPEN_JSON, DialogAction.SAVE_JSON} and any(
        path.suffix.casefold() != ".json" for path in paths
    ):
        raise NativeDialogError(f"{request.action.value} returned a non-JSON path")
    return paths


def _validate_suggested_name(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("suggested_name must be a string or None")
    if not value.strip():
        raise ValueError("suggested_name must be non-empty")
    if value != value.strip():
        raise ValueError("suggested_name cannot begin or end with whitespace")
    if any(character in value for character in '<>:"/\\|?*'):
        raise ValueError("suggested_name must be a plain Windows file name")
    if any(ord(character) < 32 for character in value):
        raise ValueError("suggested_name cannot contain control characters")
    if not value.casefold().endswith(".json"):
        raise ValueError("suggested_name must use the .json extension")


def _require_windows() -> None:
    if sys.platform != "win32":
        raise NativeDialogUnavailableError(
            "Native file dialogs are available only on the Windows desktop."
        )


def _windows_system_executable(*parts: str) -> str:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    if not ntpath.isabs(system_root):
        system_root = r"C:\Windows"
    return ntpath.normpath(ntpath.join(system_root, *parts))


def _one_line(value: str) -> str:
    return " ".join(value.split())[:1000]


def _require_simple_purpose(mode: str, actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"purpose for {mode!r} must be {expected!r}")


def _reject_simple_suggested_name(mode: str, suggested_name: str | None) -> None:
    if suggested_name is not None:
        raise ValueError(f"suggested_name is supported only for 'save-json', not {mode!r}")


__all__ = [
    "DialogAction",
    "JsonPurpose",
    "NativeDialogError",
    "NativeDialogRequest",
    "NativeDialogUnavailableError",
    "choose_paths",
    "run_dialog",
    "show_in_folder",
]
