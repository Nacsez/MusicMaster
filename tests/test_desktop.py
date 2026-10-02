"""Regression coverage for deployment state, process ownership and diagnostics."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from music_mastering_tools.desktop import (
    WorkspaceInUseError,
    WorkspaceLease,
    _reopen_existing,
    default_workspace,
    main,
)
from music_mastering_tools.doctor import DoctorCheck, DoctorReport, DoctorStatus
from music_mastering_tools.portal import create_portal_server


class DesktopTests(unittest.TestCase):
    def test_default_workspace_uses_current_user_without_cwd_or_executable_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "User A"
            second = Path(temporary) / "User B"
            for base in (first, second):
                self.assertEqual(
                    default_workspace({"LOCALAPPDATA": str(base)}),
                    base / "MusicMasteringTools" / "workspace",
                )
            with self.assertRaisesRegex(ValueError, "absolute"):
                default_workspace({"LOCALAPPDATA": "relative-directory"})

    @unittest.skipUnless(os.name == "nt", "Windows executable lease")
    def test_workspace_lease_prevents_second_owner_and_allows_relaunch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with WorkspaceLease(root):
                with self.assertRaises(WorkspaceInUseError):
                    with WorkspaceLease(root):
                        self.fail("second launcher acquired the same workspace")
                with WorkspaceLease(root / "different-library"):
                    pass
            with WorkspaceLease(root):
                pass

    def test_check_only_writes_machine_readable_failures_without_opening_portal(self) -> None:
        report = DoctorReport((DoctorCheck("soundfile", DoctorStatus.FAIL, "DLL missing", True),))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "checks.json"
            with (
                mock.patch("music_mastering_tools.desktop.run_doctor", return_value=report),
                mock.patch("music_mastering_tools.desktop.run_portal") as portal,
                mock.patch("music_mastering_tools.desktop._notify") as notify,
            ):
                result = main(
                    [
                        "--workspace",
                        str(root),
                        "--check-only",
                        "--no-browser",
                        "--diagnostics-output",
                        str(destination),
                    ]
                )
            self.assertEqual(result, 1)
            self.assertFalse(json.loads(destination.read_text(encoding="utf-8"))["healthy"])
            portal.assert_not_called()
            notify.assert_not_called()
            self.assertIn("DLL missing", next((root / "logs").glob("desktop-*.log")).read_text())

    @unittest.skipUnless(os.name == "nt", "Windows executable launcher")
    def test_startup_errors_are_retained_and_session_file_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / ".desktop-session.json"
            session.write_text('{"url": "obsolete"}', encoding="utf-8")
            with mock.patch(
                "music_mastering_tools.desktop.run_portal", side_effect=RuntimeError("missing DLL")
            ):
                self.assertEqual(main(["--workspace", str(root), "--no-browser"]), 1)
            self.assertFalse(session.exists())
            text = next((root / "logs").glob("desktop-*.log")).read_text()
            self.assertIn("RuntimeError: missing DLL", text)
            with WorkspaceLease(root):
                pass

    def test_reopen_rejects_external_session_urls_without_network_or_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for url in ("https://example.com/?token=secret", "http://user@127.0.0.1:9000/?token=s"):
                (root / ".desktop-session.json").write_text(json.dumps({"url": url}))
                with (
                    mock.patch("music_mastering_tools.desktop._loopback_open") as request,
                    mock.patch("music_mastering_tools.desktop.webbrowser.open") as browser,
                ):
                    self.assertFalse(_reopen_existing(root, open_browser=True))
                    request.assert_not_called()
                    browser.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows executable launcher")
    def test_browser_lifetime_defaults_and_manual_server_overrides(self) -> None:
        """Desktop launches own tabs; diagnostic/manual launches stay available."""
        cases = (
            ([], True, True),
            (["--no-browser"], False, False),
            (["--keep-running"], False, True),
            (["--no-browser", "--exit-with-browser"], True, False),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for arguments, track_tabs, open_browser in cases:
                with (
                    self.subTest(arguments=arguments),
                    mock.patch(
                        "music_mastering_tools.desktop.run_portal", return_value=0
                    ) as portal,
                ):
                    self.assertEqual(main(["--workspace", str(root), *arguments]), 0)
                    self.assertEqual(portal.call_args.kwargs["exit_with_browser"], track_tabs)
                    self.assertEqual(portal.call_args.kwargs["open_browser"], open_browser)
                    self.assertFalse((root / ".desktop-session.json").exists())

    def test_reopen_authenticates_existing_workspace_before_browser_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            url = "http://127.0.0.1:9000/?token=test-secret"
            (root / ".desktop-session.json").write_text(json.dumps({"url": url}))
            with (
                mock.patch("music_mastering_tools.desktop._loopback_open") as request,
                mock.patch(
                    "music_mastering_tools.desktop.json.load",
                    return_value={"data": {"workspace": {"root": str(root)}}},
                ),
                mock.patch(
                    "music_mastering_tools.desktop.webbrowser.open", return_value=True
                ) as browser,
            ):
                self.assertTrue(_reopen_existing(root, open_browser=True))
                self.assertEqual(request.call_args.args[0].get_header("X-mmt-token"), "test-secret")
                browser.assert_called_once_with(url, new=1, autoraise=True)

    def test_reopen_connects_to_loopback_even_when_an_environment_proxy_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            server = create_portal_server(root)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            (root / ".desktop-session.json").write_text(
                json.dumps({"url": f"{server.origin}/?token={server.session_token}"}),
                encoding="utf-8",
            )
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "HTTP_PROXY": "http://127.0.0.1:1",
                        "http_proxy": "http://127.0.0.1:1",
                        "NO_PROXY": "",
                        "no_proxy": "",
                    },
                ):
                    self.assertTrue(_reopen_existing(root, open_browser=False))
            finally:
                server.shutdown()
                worker.join(timeout=5)
                server.server_close()
                server.application.close(wait=True)


if __name__ == "__main__":
    unittest.main()
