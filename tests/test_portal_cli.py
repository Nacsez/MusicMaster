"""CLI dispatch contracts for the graphical portal."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from music_mastering_tools.cli import EXIT_SUCCESS, build_parser, main
from music_mastering_tools.portal import run_portal


class PortalCliTests(unittest.TestCase):
    def test_gui_and_portal_alias_parse_and_dispatch_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "private"
            for command in ("gui", "portal"):
                with self.subTest(command=command):
                    parsed = build_parser().parse_args(
                        [
                            command,
                            "--workspace",
                            str(workspace),
                            "--port",
                            "7654",
                            "--no-browser",
                        ]
                    )
                    self.assertEqual(parsed.command, command)
                    self.assertEqual(parsed.workspace, workspace)
                    self.assertEqual(parsed.port, 7654)
                    self.assertTrue(parsed.no_browser)

                    with mock.patch(
                        "music_mastering_tools.portal.run_portal",
                        return_value=EXIT_SUCCESS,
                    ) as run:
                        result = main(
                            [
                                command,
                                "--workspace",
                                str(workspace),
                                "--port",
                                "7654",
                                "--no-browser",
                            ]
                        )
                    self.assertEqual(result, EXIT_SUCCESS)
                    run.assert_called_once_with(
                        workspace,
                        port=7654,
                        open_browser=False,
                        ready_file=None,
                    )

    def test_browser_launch_failure_falls_back_to_private_console_url(self) -> None:
        server = mock.Mock()
        server.origin = "http://127.0.0.1:7654"
        server.server_address = ("127.0.0.1", 7654)
        server.session_token = "s" * 48
        server.serve_forever.side_effect = KeyboardInterrupt
        application = mock.Mock()
        logger = mock.Mock()
        output = io.StringIO()
        fallback = mock.Mock()
        readiness = mock.Mock()

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch(
                "music_mastering_tools.portal.PortalApplication",
                return_value=application,
            ),
            mock.patch(
                "music_mastering_tools.portal.create_portal_server",
                return_value=server,
            ),
            mock.patch(
                "music_mastering_tools.portal._portal_logger",
                return_value=logger,
            ),
            mock.patch(
                "music_mastering_tools.portal._close_logger",
            ),
            mock.patch(
                "music_mastering_tools.portal.webbrowser.open",
                side_effect=RuntimeError("browser unavailable"),
            ),
            redirect_stdout(output),
        ):
            result = run_portal(
                Path(temporary), open_browser=True, browser_failure=fallback, on_ready=readiness
            )

        self.assertEqual(result, EXIT_SUCCESS)
        fallback.assert_called_once_with(f"{server.origin}/?token={server.session_token}")
        self.assertEqual(readiness.call_args.args[0]["port"], 7654)
        self.assertEqual(readiness.call_args.args[0]["url"], fallback.call_args.args[0])
        self.assertIn(
            f"Private launch URL: {server.origin}/?token={server.session_token}",
            output.getvalue(),
        )
        server.serve_forever.assert_called_once_with(poll_interval=0.25)
        server.server_close.assert_called_once_with()
        application.close.assert_called_once_with(wait=True)

    def test_readiness_failure_closes_portal_resources_before_serving(self) -> None:
        application, server, logger = mock.Mock(), mock.Mock(), mock.Mock()
        server.origin = "http://127.0.0.1:7654"
        server.server_address = ("127.0.0.1", 7654)
        server.session_token = "s" * 48
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("music_mastering_tools.portal.PortalApplication", return_value=application),
            mock.patch("music_mastering_tools.portal.create_portal_server", return_value=server),
            mock.patch("music_mastering_tools.portal._portal_logger", return_value=logger),
            mock.patch("music_mastering_tools.portal._close_logger") as close_logger,
            self.assertRaisesRegex(OSError, "ready callback failed"),
        ):
            run_portal(
                Path(temporary),
                open_browser=False,
                exit_with_browser=True,
                on_ready=mock.Mock(side_effect=OSError("ready callback failed")),
            )
        server.serve_forever.assert_not_called()
        server.server_close.assert_called_once_with()
        application.close.assert_called_once_with(wait=True)
        close_logger.assert_called_once_with(logger)


if __name__ == "__main__":
    unittest.main()
