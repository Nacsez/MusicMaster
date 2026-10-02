"""Last-tab HTTP shutdown must wait for a real serialized worker to publish."""

from __future__ import annotations

import http.client
import json
import logging
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from music_mastering_tools.browser_lifetime import BrowserLifetime
from music_mastering_tools.portal import create_portal_server
from music_mastering_tools.portal_app import OperationEventSink, PortalApplication

from .helpers import write_tone


@pytest.mark.integration
@pytest.mark.regression
class BrowserLifetimeOperationTests(unittest.TestCase):
    def test_last_tab_close_waits_for_worker_publication_and_preserves_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = write_tone(root / "Original mix ü, test.wav")
            original_bytes = source.read_bytes()
            delivery = root / "Delivery ü, remembered"
            delivery.mkdir()
            completed = delivery / "completed.txt"
            logger = logging.getLogger("music_mastering_tools.tests.active_browser_close")
            application = PortalApplication(workspace, logger=logger)
            registered = application.add_tracks([str(source)], roles=["target"])
            self.assertEqual(registered["success_count"], 1)
            catalog_id = application.bootstrap()["catalog"]["catalog_id"]
            preferences = application.get_preferences()
            preferences["default_output_directory"] = str(delivery)
            application.update_preferences(preferences)
            lifetime = BrowserLifetime(logger=logger, close_grace_seconds=0.04)
            server = create_portal_server(
                workspace, application=application, browser_lifetime=lifetime, logger=logger
            )
            host, port = cast(tuple[str, int], server.server_address)
            document_id = str(uuid.uuid4())
            entered = threading.Event()
            release = threading.Event()

            def worker(_sink: OperationEventSink) -> dict[str, Any]:
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("Test did not release its operation")
                completed.write_text(
                    "Worker finished and published its output.\n", encoding="utf-8"
                )
                return {"published": str(completed)}

            def browser_event(event: str) -> None:
                connection = http.client.HTTPConnection(host, port, timeout=3)
                try:
                    connection.request(
                        "POST",
                        "/api/browser-session",
                        json.dumps({"document_id": document_id, "event": event}),
                        headers={
                            "X-MMT-Token": server.session_token,
                            "Origin": server.origin,
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200, response.read())
                finally:
                    connection.close()

            serving = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
            )
            try:
                with self.assertLogs(logger, level="INFO") as captured:
                    serving.start()
                    browser_event("open")
                    operation = application.operations.start("render", worker)
                    self.assertTrue(entered.wait(timeout=2))
                    browser_event("close")
                    serving.join(timeout=0.2)  # Longer than the configured close grace.
                    self.assertTrue(serving.is_alive(), "Server stopped during publication")
                    self.assertTrue(application.operations.active)
                    self.assertFalse(completed.exists())
                    release.set()
                    serving.join(timeout=3)
                    self.assertFalse(serving.is_alive(), "Server survived completed last-tab exit")
                    result = application.operations.get(operation.operation_id)
                    self.assertEqual(result.state, "succeeded")
                    self.assertEqual(result.result, {"published": str(completed)})
                messages = "\n".join(captured.output)
                self.assertIn("exit deferred", messages)
                self.assertIn("last-browser-document-closed", messages)
            finally:
                release.set()
                server.shutdown()
                serving.join(timeout=3)
                server.server_close()
                application.close(wait=True)

            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertIn("Worker finished", completed.read_text(encoding="utf-8"))
            reopened = PortalApplication(workspace)
            try:
                self.assertEqual(reopened.bootstrap()["catalog"]["catalog_id"], catalog_id)
                self.assertEqual(reopened.bootstrap()["catalog"]["track_count"], 1)
                self.assertEqual(
                    reopened.get_preferences()["default_output_directory"], str(delivery)
                )
            finally:
                reopened.close(wait=True)
