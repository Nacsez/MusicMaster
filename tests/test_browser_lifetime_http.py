"""Real authenticated HTTP presence, long-poll release, and durable workspace state."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from unittest import mock

from music_mastering_tools.browser_lifetime import BrowserLifetime
from music_mastering_tools.portal import create_portal_server
from music_mastering_tools.portal_app import PortalApplication

from .helpers import write_tone


class BrowserLifetimeHttpTests(unittest.TestCase):
    token = "browser-lifetime-test-token-" + "x" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.application = PortalApplication(self.root / "workspace")
        self.lifetime = BrowserLifetime(close_grace_seconds=0.1, wait_seconds=0.04)
        self.server = create_portal_server(
            self.application.layout.root,
            token=self.token,
            application=self.application,
            browser_lifetime=self.lifetime,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.host, self.port = cast(tuple[str, int], self.server.server_address)
        self.document_id = str(uuid.uuid4())

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.application.close(wait=True)
        self.temporary.cleanup()

    def request(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = token,
    ) -> tuple[int, dict[str, Any]]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-MMT-Token"] = token
        method = "GET" if payload is None else "POST"
        try:
            connection.request(
                method,
                path,
                body=json.dumps(payload) if payload is not None else None,
                headers=headers,
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def presence(self, event: str, document_id: str | None = None) -> tuple[int, dict[str, Any]]:
        return self.request(
            "/api/browser-session",
            {
                "document_id": document_id or self.document_id,
                "event": event,
            },
        )

    def test_bootstrap_describes_opt_in_and_both_presence_routes_require_authentication(
        self,
    ) -> None:
        status, response = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        config = response["data"]["browser_lifetime"]
        self.assertTrue(config["enabled"])
        self.assertEqual(config["stale_timeout_seconds"], 180)
        self.assertEqual(config["heartbeat_interval_seconds"], 15)
        for path, payload in (
            ("/api/browser-session", {"document_id": self.document_id, "event": "open"}),
            ("/api/browser-session/wait", {"document_id": self.document_id}),
        ):
            with self.subTest(path=path):
                status, _response = self.request(path, payload, token=None)
                self.assertEqual(status, 403)
        self.assertFalse(self.lifetime.response(self.document_id)["registered"])

    def test_payloads_are_strict_and_wait_does_not_register_unknown_documents(self) -> None:
        for path, payload in (
            ("/api/browser-session", {"document_id": "not-a-uuid", "event": "open"}),
            ("/api/browser-session", {"document_id": self.document_id, "event": "hidden"}),
            (
                "/api/browser-session",
                {"document_id": self.document_id, "event": "open", "extra": 1},
            ),
            ("/api/browser-session/wait", {"document_id": self.document_id, "extra": 1}),
        ):
            with self.subTest(payload=payload):
                status, _response = self.request(path, payload)
                self.assertEqual(status, 400)
        status, response = self.request(
            "/api/browser-session/wait", {"document_id": self.document_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(response["data"]["registered"])

    def test_presence_wait_returns_live_document_and_close_tombstones_late_requests(self) -> None:
        self.assertTrue(self.presence("open")[1]["data"]["registered"])
        status, response = self.request(
            "/api/browser-session/wait", {"document_id": self.document_id}
        )
        self.assertEqual(status, 200)
        self.assertTrue(response["data"]["registered"])
        self.presence("close")
        for event in ("heartbeat", "open"):
            self.assertFalse(self.presence(event)[1]["data"]["registered"])
        status, response = self.request(
            "/api/browser-session/wait", {"document_id": self.document_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(response["data"]["registered"])

    def test_close_releases_held_longpoll_without_renewing_the_closed_document(self) -> None:
        self.lifetime.wait_seconds = 25.0
        self.presence("open")
        waiting = threading.Event()
        begin_wait = self.lifetime.begin_wait

        def begin(document_id: str) -> Any:
            lease = begin_wait(document_id)
            waiting.set()
            return lease

        with (
            mock.patch.object(self.lifetime, "begin_wait", side_effect=begin),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(
                self.request, "/api/browser-session/wait", {"document_id": self.document_id}
            )
            self.assertTrue(waiting.wait(timeout=2))
            self.presence("close")
            status, response = future.result(timeout=2)
        self.assertEqual(status, 200)
        self.assertFalse(response["data"]["registered"])
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def test_last_document_exit_preserves_catalog_and_preferences_across_restart(self) -> None:
        target = write_tone(self.root / "target.wav", frequency=330)
        added = self.application.add_tracks([str(target)], roles=["target"], labels={})
        self.assertTrue(added["results"][0]["ok"])
        previous = self.application.bootstrap()["catalog"]
        delivery = self.root / "delivery"
        delivery.mkdir()
        preferences = {
            **self.application.get_preferences(),
            "default_output_directory": str(delivery),
        }
        self.application.update_preferences(preferences)
        source_bytes = target.read_bytes()
        self.presence("open")
        self.presence("close")
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        reopened = PortalApplication(self.application.layout.root)
        try:
            self.assertEqual(reopened.bootstrap()["catalog"]["catalog_id"], previous["catalog_id"])
            self.assertEqual(reopened.bootstrap()["catalog"]["track_count"], 1)
            self.assertEqual(reopened.get_preferences(), preferences)
            self.assertEqual(target.read_bytes(), source_bytes)
        finally:
            reopened.close(wait=True)

    def test_second_document_keeps_server_running_until_it_also_closes(self) -> None:
        second = str(uuid.uuid4())
        self.presence("open")
        self.presence("open", second)
        self.presence("close")
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        status, response = self.request("/api/browser-session/wait", {"document_id": second})
        self.assertEqual(status, 200)
        self.assertTrue(response["data"]["registered"])
        self.assertTrue(self.thread.is_alive())
        self.presence("close", second)
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def test_disabled_lifetime_reports_opt_out_without_shutting_down(self) -> None:
        self.server.browser_lifetime = None
        status, response = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["browser_lifetime"], {"enabled": False})
        for event in ("open", "heartbeat", "close"):
            self.assertFalse(self.presence(event)[1]["data"]["enabled"])
        status, response = self.request(
            "/api/browser-session/wait", {"document_id": self.document_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(response["data"]["enabled"])
        self.assertTrue(self.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
