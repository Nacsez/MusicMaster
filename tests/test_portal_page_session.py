"""Refresh authorization, cookie boundaries, and browser disconnect regressions."""

from __future__ import annotations

import http.client
import secrets
import tempfile
import threading
import unittest
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Any, cast
from unittest import mock

from music_mastering_tools.browser_lifetime import BrowserLifetime
from music_mastering_tools.portal import (
    MEDIA_COOKIE_NAME,
    PortalHTTPServer,
    PortalRequestHandler,
    create_portal_server,
)
from music_mastering_tools.portal_app import PortalApplication

from .helpers import write_tone


class PortalPageSessionTests(unittest.TestCase):
    token = "page-session-test-token-" + "x" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.application = PortalApplication(self.root / "workspace")
        self.server = create_portal_server(
            self.application.layout.root, token=self.token, application=self.application
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.application.close(wait=True)
        self.temporary.cleanup()

    def request(
        self,
        path: str,
        *,
        server: PortalHTTPServer | None = None,
        cookies: tuple[str, ...] = (),
        method: str = "GET",
        api_token: str | None = None,
    ) -> tuple[int, http.client.HTTPMessage, bytes]:
        selected = server or self.server
        host, port = cast(tuple[str, int], selected.server_address)
        connection = http.client.HTTPConnection(host, port, timeout=3)
        try:
            # putheader deliberately preserves multiple Cookie headers for the
            # ambiguity regression; request(headers=dict) would collapse them.
            connection.putrequest(method, path)
            for cookie in cookies:
                connection.putheader("Cookie", cookie)
            if api_token is not None:
                connection.putheader("X-MMT-Token", api_token)
            if method == "POST":
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", "2")
            connection.endheaders(b"{}" if method == "POST" else None)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def launch_cookies(self) -> tuple[str, str]:
        status, headers, _ = self.request(f"/?token={self.token}")
        self.assertEqual(status, 200)
        cookies = headers.get_all("Set-Cookie", [])
        self.assertEqual(len(cookies), 2)
        return cookies[0].split(";", 1)[0], cookies[1].split(";", 1)[0]

    def test_launch_issues_separate_port_named_cookie_and_tokenless_refresh_recovers_page(
        self,
    ) -> None:
        status, headers, body = self.request(f"/?token={self.token}")
        self.assertEqual(status, 200)
        self.assertIn(self.token.encode(), body)
        cookies = headers.get_all("Set-Cookie", [])
        self.assertEqual(len(cookies), 2)
        page_cookie, media_cookie = cookies
        self.assertTrue(page_cookie.startswith(f"{self.server.page_cookie_name}="))
        self.assertTrue(media_cookie.startswith(f"{MEDIA_COOKIE_NAME}="))
        self.assertTrue(self.server.page_cookie_name.endswith(str(self.server.server_address[1])))
        for attribute in ("Path=/", "HttpOnly", "SameSite=Strict"):
            self.assertIn(attribute, page_cookie)
        self.assertEqual(
            page_cookie.split(";", 1)[0],
            f"{self.server.page_cookie_name}={self.server.page_session_token}",
        )
        self.assertEqual(
            len({self.token, self.server.page_session_token, self.server.media_session_token}), 3
        )
        self.assertNotIn(self.token, page_cookie)
        self.assertNotIn(self.server.media_session_token, page_cookie)

        status, refreshed_headers, refreshed_body = self.request(
            "/", cookies=(page_cookie.split(";", 1)[0],)
        )
        self.assertEqual(status, 200)
        self.assertIn(b"window.__MMT_CONFIG__", refreshed_body)
        self.assertIn(self.token.encode(), refreshed_body)
        self.assertEqual(refreshed_headers.get_all("Set-Cookie", []), [media_cookie])

    def test_index_rejects_wrong_ambiguous_and_stale_page_credentials(self) -> None:
        page_cookie, _ = self.launch_cookies()
        name = self.server.page_cookie_name
        secret = self.server.page_session_token
        for cookies in (
            (),
            (f"{name}=incorrect",),
            (f"{name}=caf\N{LATIN SMALL LETTER E WITH ACUTE}",),
            (f"MMT-Page-Session-1={secret}",),
            (f"{page_cookie}; {page_cookie}",),
            (page_cookie, page_cookie),
        ):
            with self.subTest(cookies=cookies):
                status, headers, _ = self.request("/", cookies=cookies)
                self.assertEqual(status, 403)
                self.assertIsNone(headers.get("Set-Cookie"))
        self.server.page_session_token = secrets.token_urlsafe(32)
        self.assertEqual(self.request("/", cookies=(page_cookie,))[0], 403)

    def test_explicit_blank_or_duplicate_launch_tokens_never_fall_back_to_page_cookie(
        self,
    ) -> None:
        page_cookie, _ = self.launch_cookies()
        for query in (
            "token=",
            "token",
            "token=wrong",
            f"token={self.token}&token=wrong",
            f"token=wrong&token={self.token}",
            f"token=&token={self.token}",
            f"token={self.token}&token={self.token}",
        ):
            with self.subTest(query=query):
                status, headers, _ = self.request(f"/?{query}", cookies=(page_cookie,))
                self.assertEqual(status, 403)
                self.assertIsNone(headers.get("Set-Cookie"))
        self.assertEqual(self.request("/?view=library", cookies=(page_cookie,))[0], 200)

    def test_cookie_names_keep_two_live_workspaces_independently_refreshable(self) -> None:
        page_cookie, _ = self.launch_cookies()
        second_application = PortalApplication(self.root / "second-workspace")
        second_token = "second-workspace-token-" + "y" * 40
        second_server = create_portal_server(
            second_application.layout.root,
            token=second_token,
            application=second_application,
        )
        second_thread = threading.Thread(
            target=second_server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        second_thread.start()
        try:
            self.assertNotEqual(self.server.page_cookie_name, second_server.page_cookie_name)
            self.assertNotEqual(self.server.page_session_token, second_server.page_session_token)
            status, headers, _ = self.request(f"/?token={second_token}", server=second_server)
            self.assertEqual(status, 200)
            second_cookie = headers.get_all("Set-Cookie", [])[0].split(";", 1)[0]
            combined = f"{page_cookie}; {second_cookie}"
            for server, token in ((self.server, self.token), (second_server, second_token)):
                status, _, body = self.request("/", server=server, cookies=(combined,))
                self.assertEqual(status, 200)
                self.assertIn(token.encode(), body)
            self.assertEqual(self.request("/", cookies=(second_cookie,))[0], 403)
            self.assertEqual(
                self.request("/", server=second_server, cookies=(page_cookie,))[0], 403
            )
        finally:
            second_server.shutdown()
            second_thread.join(timeout=2)
            second_server.server_close()
            second_application.close(wait=True)

    def test_page_media_and_api_credentials_are_accepted_only_by_their_routes(self) -> None:
        page_cookie, media_cookie = self.launch_cookies()
        target = write_tone(self.root / "target.wav", frequency=330)
        added = self.application.add_tracks([str(target)], roles=["target"], labels={})
        track_id = added["results"][0]["track"]["track_id"]
        media_path = f"/media/tracks/{track_id}"
        self.assertEqual(self.request("/api/bootstrap", cookies=(page_cookie,))[0], 403)
        self.assertEqual(
            self.request(
                "/api/browser-session", method="POST", cookies=(f"{page_cookie}; {media_cookie}",)
            )[0],
            403,
        )
        self.assertEqual(self.request(media_path, cookies=(page_cookie,))[0], 403)
        self.assertEqual(self.request(media_path, cookies=(media_cookie,))[0], 200)
        self.assertEqual(self.request("/", cookies=(media_cookie,))[0], 403)
        self.assertEqual(self.request("/", api_token=self.token)[0], 403)
        self.assertEqual(self.request("/api/bootstrap", api_token=self.token)[0], 200)

    def test_page_secret_and_all_launch_query_values_are_redacted_from_access_logs(self) -> None:
        secrets_to_check = (
            self.token,
            self.server.media_session_token,
            self.server.page_session_token,
        )
        with self.assertLogs("music_mastering_tools.portal", level="INFO") as captured:
            status, _, _ = self.request(f"/?token=wrong&candidate={self.server.page_session_token}")
        self.assertEqual(status, 403)
        joined = "\n".join(captured.output)
        for secret in secrets_to_check:
            self.assertNotIn(secret, joined)
        self.assertNotIn("candidate=", joined)
        self.assertIn("?[REDACTED]", joined)


class PortalDisconnectTests(unittest.TestCase):
    def handler(self, lifetime: BrowserLifetime | None = None) -> PortalRequestHandler:
        handler = PortalRequestHandler.__new__(PortalRequestHandler)
        handler.server = cast(
            PortalHTTPServer,
            mock.Mock(browser_lifetime=lifetime, portal_logger=mock.Mock()),
        )
        handler.command = "POST"
        handler.path = "/api/browser-session/wait"
        handler.close_connection = False
        return handler

    def test_aborted_wait_drops_lease_without_renewal_or_second_error_response(self) -> None:
        for error_type in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            with self.subTest(error_type=error_type.__name__):
                clock = mock.Mock(return_value=0.0)
                lifetime = BrowserLifetime(clock=clock, stale_timeout_seconds=10, wait_seconds=5)
                document_id = str(uuid.uuid4())
                lifetime.event(document_id, "open")
                handler = self.handler(lifetime)

                def wait(_lease: Any, current_clock: mock.Mock = clock) -> None:
                    current_clock.return_value = 4.0

                with (
                    mock.patch.object(handler, "_validate_local_request"),
                    mock.patch.object(handler, "_require_api_token"),
                    mock.patch.object(
                        handler,
                        "_read_json_object",
                        return_value={
                            "document_id": document_id,
                        },
                    ),
                    mock.patch.object(lifetime, "wait", side_effect=wait),
                    mock.patch.object(
                        lifetime, "finish_wait", wraps=lifetime.finish_wait
                    ) as finish,
                    mock.patch.object(handler, "_json", side_effect=error_type(10053, "closed")),
                    mock.patch.object(handler, "_handle_exception") as error,
                ):
                    handler.do_POST()
                self.assertTrue(handler.close_connection)
                error.assert_not_called()
                self.assertFalse(finish.call_args.kwargs["delivered"])
                handler.server.portal_logger.debug.assert_called_once()  # type: ignore[attr-defined]
                clock.return_value = 10.1
                self.assertFalse(lifetime.response(document_id)["registered"])

    def test_disconnect_while_sending_error_is_logged_once_without_another_response(self) -> None:
        handler = self.handler()
        with mock.patch.object(
            handler, "_error", side_effect=BrokenPipeError(32, "closed")
        ) as send:
            handler._handle_exception(ValueError("bad input"), "request-123")
        send.assert_called_once()
        self.assertTrue(handler.close_connection)
        handler.server.portal_logger.debug.assert_called_once()  # type: ignore[attr-defined]
        handler.server.portal_logger.exception.assert_not_called()  # type: ignore[attr-defined]

    def test_invalid_response_headers_are_rejected_before_any_response_is_sent(self) -> None:
        handler = self.handler()
        for extra, cookies in (
            ({"X-Test\r\nInjected": "value"}, ()),
            ({"X-Test": "value\r\nInjected: bad"}, ()),
            ({"X-Test": "value\x00bad"}, ()),
            ({}, ("Session=secret\r\nInjected: bad",)),
            ({}, ("Session=secret\x00bad",)),
        ):
            with self.subTest(extra=extra, cookies=cookies):
                with mock.patch.object(handler, "send_response") as send:
                    with self.assertRaisesRegex(ValueError, "header name or value"):
                        handler._send_headers(
                            HTTPStatus.OK,
                            "text/plain",
                            0,
                            additional_headers=extra,
                            set_cookies=cookies,
                        )
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
