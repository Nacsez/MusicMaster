"""Contract tests for the Windows dialog boundary without opening any UI."""

from __future__ import annotations

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import music_mastering_tools.native_dialogs as native_dialogs
from music_mastering_tools.native_dialogs import (
    DialogAction,
    JsonPurpose,
    NativeDialogError,
    NativeDialogRequest,
    NativeDialogUnavailableError,
    choose_paths,
    run_dialog,
    show_in_folder,
)


class NativeDialogRequestTests(unittest.TestCase):
    def test_factories_create_strict_valid_requests(self) -> None:
        initial = Path("music")
        self.assertEqual(
            NativeDialogRequest.open_audio(multiple=True, initial_directory=initial),
            NativeDialogRequest(
                DialogAction.OPEN_AUDIO,
                multiple=True,
                initial_directory=initial,
            ),
        )
        self.assertEqual(
            NativeDialogRequest.open_json(JsonPurpose.CATALOG),
            NativeDialogRequest(
                DialogAction.OPEN_JSON,
                json_purpose=JsonPurpose.CATALOG,
            ),
        )
        self.assertEqual(
            NativeDialogRequest.save_json(
                JsonPurpose.SELECTION,
                suggested_name="my-selection.json",
            ),
            NativeDialogRequest(
                DialogAction.SAVE_JSON,
                json_purpose=JsonPurpose.SELECTION,
                suggested_name="my-selection.json",
            ),
        )
        self.assertEqual(
            NativeDialogRequest.choose_folder(),
            NativeDialogRequest(DialogAction.CHOOSE_FOLDER),
        )

    def test_invalid_action_specific_combinations_are_rejected(self) -> None:
        cases = (
            (
                TypeError,
                lambda: NativeDialogRequest("open-audio"),  # type: ignore[arg-type]
            ),
            (
                TypeError,
                lambda: NativeDialogRequest(
                    DialogAction.OPEN_JSON,
                    json_purpose="catalog",  # type: ignore[arg-type]
                ),
            ),
            (
                ValueError,
                lambda: NativeDialogRequest(DialogAction.OPEN_JSON),
            ),
            (
                ValueError,
                lambda: NativeDialogRequest(
                    DialogAction.OPEN_AUDIO,
                    json_purpose=JsonPurpose.CATALOG,
                ),
            ),
            (
                ValueError,
                lambda: NativeDialogRequest(DialogAction.CHOOSE_FOLDER, multiple=True),
            ),
            (
                ValueError,
                lambda: NativeDialogRequest(
                    DialogAction.OPEN_AUDIO,
                    suggested_name="audio.json",
                ),
            ),
            (
                TypeError,
                lambda: NativeDialogRequest(
                    DialogAction.CHOOSE_FOLDER,
                    initial_directory="music",  # type: ignore[arg-type]
                ),
            ),
        )
        for exception_type, operation in cases:
            with self.subTest(exception_type=exception_type):
                with self.assertRaises(exception_type):
                    operation()

    def test_save_name_is_a_plain_json_file_name(self) -> None:
        for value in (
            "",
            " file.json",
            "folder/file.json",
            r"folder\file.json",
            "catalog.txt",
            "bad|name.json",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    NativeDialogRequest.save_json(
                        JsonPurpose.CATALOG,
                        suggested_name=value,
                    )


class NativeDialogProcessTests(unittest.TestCase):
    def invoke(
        self,
        request: NativeDialogRequest,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> tuple[tuple[Path, ...], mock.Mock]:
        completed = subprocess.CompletedProcess(
            args=(),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        with (
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(
                native_dialogs.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            result = run_dialog(request)
        return result, run

    def test_open_audio_many_uses_fixed_script_and_environment_values(self) -> None:
        request = NativeDialogRequest.open_audio(
            multiple=True,
            initial_directory=Path("private music"),
        )
        paths, run = self.invoke(
            request,
            stdout="C:\\Audio\\target.wav\nD:\\References\\reference.flac\n",
        )

        self.assertEqual(
            paths,
            (
                Path("C:\\Audio\\target.wav"),
                Path("D:\\References\\reference.flac"),
            ),
        )
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(command[-1], native_dialogs._OPEN_FILE_SCRIPT)
        self.assertNotIn(str(request.initial_directory), command[-1])
        self.assertTrue(command[0].casefold().endswith("powershell.exe"))
        self.assertEqual(options["env"]["MMT_DIALOG_MULTIPLE"], "1")
        self.assertEqual(options["env"]["MMT_DIALOG_TITLE"], "Open audio tracks")
        self.assertIn("*.wav", options["env"]["MMT_DIALOG_FILTER"])
        self.assertFalse(options["shell"])
        self.assertFalse(options["check"])

    def test_json_open_save_and_folder_choose_use_their_presentations(self) -> None:
        cases = (
            (
                NativeDialogRequest.open_json(JsonPurpose.RUN_MANIFEST),
                "C:\\Runs\\manifest.json\n",
                native_dialogs._OPEN_FILE_SCRIPT,
                "Open mastering run manifest",
                "",
            ),
            (
                NativeDialogRequest.save_json(JsonPurpose.CATALOG),
                "C:\\Exports\\catalog.json\n",
                native_dialogs._SAVE_FILE_SCRIPT,
                "Save catalog JSON",
                "catalog.json",
            ),
            (
                NativeDialogRequest.choose_folder(),
                "C:\\Music\n",
                native_dialogs._CHOOSE_FOLDER_SCRIPT,
                "Choose a folder",
                "",
            ),
        )
        for request, stdout, expected_script, title, suggested_name in cases:
            with self.subTest(action=request.action):
                paths, run = self.invoke(request, stdout=stdout)
                self.assertEqual(len(paths), 1)
                self.assertEqual(run.call_args.args[0][-1], expected_script)
                environment = run.call_args.kwargs["env"]
                self.assertEqual(environment["MMT_DIALOG_TITLE"], title)
                self.assertEqual(
                    environment["MMT_DIALOG_SUGGESTED_NAME"],
                    suggested_name,
                )

    def test_cancellation_is_an_empty_path_tuple(self) -> None:
        paths, _ = self.invoke(
            NativeDialogRequest.open_audio(),
            stdout="\n",
        )
        self.assertEqual(paths, ())

    def test_non_windows_platform_never_starts_a_subprocess(self) -> None:
        with (
            mock.patch.object(native_dialogs.sys, "platform", "linux"),
            mock.patch.object(native_dialogs.subprocess, "run") as run,
            self.assertRaises(NativeDialogUnavailableError),
        ):
            run_dialog(NativeDialogRequest.choose_folder())
        run.assert_not_called()

    def test_process_and_result_contract_failures_are_reported(self) -> None:
        with self.assertRaisesRegex(NativeDialogError, "exit code 9.*WinForms unavailable"):
            self.invoke(
                NativeDialogRequest.open_audio(),
                stderr="WinForms unavailable\n",
                returncode=9,
            )

        with self.assertRaisesRegex(NativeDialogError, "expected at most one"):
            self.invoke(
                NativeDialogRequest.open_json(JsonPurpose.CATALOG),
                stdout="C:\\one.json\nC:\\two.json\n",
            )

        with self.assertRaisesRegex(NativeDialogError, "non-JSON"):
            self.invoke(
                NativeDialogRequest.save_json(JsonPurpose.CATALOG),
                stdout="C:\\catalog.txt\n",
            )

    def test_process_start_failure_is_wrapped(self) -> None:
        with (
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(
                native_dialogs.subprocess,
                "run",
                side_effect=OSError("host missing"),
            ),
            self.assertRaisesRegex(NativeDialogError, "host missing"),
        ):
            run_dialog(NativeDialogRequest.open_audio())

    def test_simple_server_surface_returns_strings_for_canonical_modes(self) -> None:
        completed = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="C:\\Audio\\one.wav\nD:\\Audio\\two.wav\n",
            stderr="",
        )
        with (
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(
                native_dialogs.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            paths = choose_paths("open-audio-many", "audio")

        self.assertEqual(
            paths,
            ("C:\\Audio\\one.wav", "D:\\Audio\\two.wav"),
        )
        self.assertEqual(
            run.call_args.kwargs["env"]["MMT_DIALOG_MULTIPLE"],
            "1",
        )

    def test_adapter_preserves_initial_location_for_each_picker_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initial = Path(temporary)
            for mode, purpose in (
                ("open-audio", "audio"),
                ("open-audio-many", "audio"),
                ("open-json", "catalog"),
                ("save-json", "selection"),
                ("choose-folder", "folder"),
            ):
                with (
                    self.subTest(mode=mode),
                    mock.patch.object(
                        native_dialogs,
                        "run_dialog",
                        return_value=(),
                    ) as run,
                ):
                    choose_paths(mode, purpose, initial_directory=initial)
                    self.assertEqual(run.call_args.args[0].initial_directory, initial)

    def test_picker_uses_nearest_existing_folder_for_files_and_future_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            audio = root / "track.wav"
            audio.touch()
            for requested in (audio, root / "future outputs" / "album"):
                with self.subTest(requested=requested):
                    _, run = self.invoke(
                        NativeDialogRequest.choose_folder(initial_directory=requested)
                    )
                    self.assertEqual(
                        run.call_args.kwargs["env"]["MMT_DIALOG_INITIAL_DIRECTORY"],
                        str(root),
                    )
            self.assertIn("SpecialFolder]::Desktop", native_dialogs._CHOOSE_FOLDER_SCRIPT)

    def test_simple_server_surface_strictly_rejects_invalid_combinations(self) -> None:
        cases = (
            (TypeError, (1, "audio", None)),
            (TypeError, ("open-audio", 1, None)),
            (TypeError, ("save-json", "catalog", 1)),
            (ValueError, ("unknown", "audio", None)),
            (ValueError, ("open-audio", "catalog", None)),
            (ValueError, ("choose-folder", "workspace", None)),
            (ValueError, ("open-json", "unknown", None)),
            (ValueError, ("open-audio", "audio", "name.json")),
            (ValueError, ("save-json", "catalog", "not-json.txt")),
        )
        for exception_type, arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(exception_type):
                    choose_paths(*arguments)  # type: ignore[arg-type]


class ExplorerAdapterTests(unittest.TestCase):
    def test_show_in_folder_selects_without_executing_the_path(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(native_dialogs.subprocess, "Popen") as popen,
            mock.patch.object(native_dialogs, "_open_file_selection") as select,
        ):
            selected = Path(temporary) / "master with spaces, ü.wav"
            selected.touch()
            show_in_folder(selected)
            select.assert_called_once_with(selected.resolve())
            popen.assert_not_called()

    def test_show_in_folder_opens_directory_contents(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="mastering folder ") as temporary,
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(native_dialogs.subprocess, "Popen") as popen,
        ):
            selected = Path(temporary)
            show_in_folder(selected)
            command = popen.call_args.args[0]
            options = popen.call_args.kwargs
        self.assertTrue(command[0].casefold().endswith("explorer.exe"))
        self.assertEqual(command[1], str(selected.resolve()))
        self.assertFalse(options["shell"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)

    def test_show_in_folder_rejects_platform_and_wraps_start_failure(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(native_dialogs.sys, "platform", "linux"),
            mock.patch.object(native_dialogs.subprocess, "Popen") as popen,
            self.assertRaises(NativeDialogUnavailableError),
        ):
            show_in_folder(Path(temporary))
        popen.assert_not_called()

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(
                native_dialogs.subprocess,
                "Popen",
                side_effect=OSError("Explorer missing"),
            ),
            self.assertRaisesRegex(NativeDialogError, "Explorer missing"),
        ):
            show_in_folder(Path(temporary))

    def test_missing_location_reports_error_before_starting_explorer(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(native_dialogs.sys, "platform", "win32"),
            mock.patch.object(native_dialogs.subprocess, "Popen") as popen,
            self.assertRaisesRegex(NativeDialogError, "no longer exists"),
        ):
            show_in_folder(Path(temporary) / "missing.wav")
        popen.assert_not_called()

    def test_shell_selection_uses_unicode_pointers_and_releases_resources(self) -> None:
        shell, ole = mock.Mock(), mock.Mock()
        ole.CoInitializeEx.return_value = 0
        shell.SHOpenFolderAndSelectItems.return_value = 0

        def parse(_name: str, _context: object, output: object, *_args: object) -> int:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = 0x123456789
            return 0

        shell.SHParseDisplayName.side_effect = parse
        selected = Path("Audio ü, spaced") / "master.wav"
        with (
            mock.patch.object(native_dialogs, "_load_shell_libraries", return_value=(shell, ole)),
            mock.patch.object(native_dialogs, "_wait_for_explorer_directory") as wait,
        ):
            native_dialogs._open_file_selection(selected)
        wait.assert_called_once_with(selected.parent)
        self.assertEqual(shell.SHOpenFolderAndSelectItems.call_count, 2)
        self.assertEqual(shell.SHParseDisplayName.call_args.args[0], str(selected))
        args = shell.SHOpenFolderAndSelectItems.call_args.args
        self.assertEqual(args[0].value, 0x123456789)
        self.assertEqual(args[1:], (0, None, 0))
        self.assertEqual(ole.CoTaskMemFree.call_args.args[0].value, 0x123456789)
        ole.CoUninitialize.assert_called_once_with()

    def test_shell_selection_checks_failures_and_balances_com(self) -> None:
        for initialized, parse_result, open_result, expected in (
            (0x80004005, 0, 0, "initialize Windows Shell"),
            (0, 0x80070002, 0, "resolve the Explorer location"),
            (0, 0, 0x80004005, "show the Explorer location"),
            (1, 0, 0, None),
            (0x80010106, 0, 0, None),
        ):
            with self.subTest(initialized=initialized, parse=parse_result, opened=open_result):
                shell, ole = mock.Mock(), mock.Mock()
                ole.CoInitializeEx.return_value = initialized
                shell.SHOpenFolderAndSelectItems.return_value = open_result

                def parse(
                    _name: str,
                    _context: object,
                    output: object,
                    *_args: object,
                    result: int = parse_result,
                ) -> int:
                    if not result:
                        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
                    return result

                shell.SHParseDisplayName.side_effect = parse
                with (
                    mock.patch.object(
                        native_dialogs, "_load_shell_libraries", return_value=(shell, ole)
                    ),
                    mock.patch.object(native_dialogs, "_wait_for_explorer_directory"),
                ):
                    if expected:
                        with self.assertRaisesRegex(NativeDialogError, expected):
                            native_dialogs._open_file_selection(Path("master.wav"))
                    else:
                        native_dialogs._open_file_selection(Path("master.wav"))
                self.assertEqual(ole.CoUninitialize.call_count, initialized in {0, 1})
                self.assertEqual(
                    ole.CoTaskMemFree.call_count, initialized != 0x80004005 and not parse_result
                )

    def test_shell_declarations_keep_pointers_wide_and_hresults_signed(self) -> None:
        with mock.patch.object(native_dialogs.ctypes, "WinDLL", create=True) as loader:
            shell, ole = native_dialogs._load_shell_libraries()
        self.assertEqual(
            loader.call_args_list,
            [
                mock.call("shell32", use_last_error=True),
                mock.call("ole32", use_last_error=True),
            ],
        )
        self.assertEqual(shell.SHOpenFolderAndSelectItems.argtypes[0], ctypes.c_void_p)
        self.assertEqual(shell.SHParseDisplayName.argtypes[2], ctypes.POINTER(ctypes.c_void_p))
        self.assertEqual(shell.SHOpenFolderAndSelectItems.restype, ctypes.c_int32)
        self.assertEqual(ole.CoInitializeEx.restype, ctypes.c_int32)

    def test_readiness_failure_releases_shell_resources_before_reporting(self) -> None:
        shell, ole = mock.Mock(), mock.Mock()
        ole.CoInitializeEx.return_value = 0
        shell.SHOpenFolderAndSelectItems.return_value = 0

        def parse(_name: str, _context: object, output: object, *_args: object) -> int:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
            return 0

        shell.SHParseDisplayName.side_effect = parse
        with (
            mock.patch.object(native_dialogs, "_load_shell_libraries", return_value=(shell, ole)),
            mock.patch.object(
                native_dialogs,
                "_wait_for_explorer_directory",
                side_effect=NativeDialogError("not ready"),
            ),
            self.assertRaisesRegex(NativeDialogError, "not ready"),
        ):
            native_dialogs._open_file_selection(Path("master.wav"))
        ole.CoTaskMemFree.assert_called_once()
        ole.CoUninitialize.assert_called_once_with()
        self.assertEqual(shell.SHOpenFolderAndSelectItems.call_count, 1)

    def test_explorer_readiness_uses_fixed_script_and_bounded_environment_path(self) -> None:
        completed = subprocess.CompletedProcess((), 0, "", "")
        requested = Path("Unicode ü, spaced folder")
        with mock.patch.object(native_dialogs.subprocess, "run", return_value=completed) as run:
            native_dialogs._wait_for_explorer_directory(requested)
        command, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(command[-1], native_dialogs._WAIT_EXPLORER_FOLDER_SCRIPT)
        self.assertNotIn(str(requested), command[-1])
        self.assertEqual(options["env"]["MMT_EXPLORER_DIRECTORY"], str(requested))
        self.assertEqual(options["timeout"], 15)
        self.assertFalse(options["shell"])

    def test_explorer_readiness_process_errors_have_diagnostics(self) -> None:
        for failure in (OSError("host unavailable"), subprocess.TimeoutExpired("host", 15)):
            with (
                self.subTest(failure=failure),
                mock.patch.object(native_dialogs.subprocess, "run", side_effect=failure),
                self.assertRaisesRegex(NativeDialogError, "confirm the Explorer folder view"),
            ):
                native_dialogs._wait_for_explorer_directory(Path("folder"))
        failed = subprocess.CompletedProcess((), 1, "", "view not ready\n")
        with (
            mock.patch.object(native_dialogs.subprocess, "run", return_value=failed),
            self.assertRaisesRegex(NativeDialogError, "view not ready"),
        ):
            native_dialogs._wait_for_explorer_directory(Path("folder"))


if __name__ == "__main__":
    unittest.main()
