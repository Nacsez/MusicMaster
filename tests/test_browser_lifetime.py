"""Deterministic browser presence, refresh, suspension, and operation safety."""

from __future__ import annotations

import math
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from music_mastering_tools.browser_lifetime import BrowserLifetime


class _Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class BrowserLifetimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.lifetime = BrowserLifetime(clock=self.clock)
        self.first, self.second = str(uuid.uuid4()), str(uuid.uuid4())

    def test_startup_deadline_begins_when_server_starts_serving(self) -> None:
        self.clock.now = 200.0
        self.lifetime.start()
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.clock.now = 289.999
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.clock.now = 290.0
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "browser-startup-timeout"
        )
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=True))

    def test_last_close_has_refresh_grace_and_duplicate_close_does_not_extend_it(self) -> None:
        self.assertTrue(self.lifetime.event(self.first, "open")["registered"])
        self.lifetime.event(self.first, "close")
        self.clock.now = 2.0
        self.lifetime.event(self.first, "close")
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.clock.now = 4.0
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "last-browser-document-closed"
        )

    def test_refresh_and_multiple_tabs_keep_process_alive(self) -> None:
        self.lifetime.event(self.first, "open")
        self.lifetime.event(self.second, "open")
        self.lifetime.event(self.first, "close")
        self.clock.now = 10.0
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.lifetime.event(self.second, "close")
        self.clock.now = 13.5
        refreshed = str(uuid.uuid4())
        self.lifetime.event(refreshed, "open")
        self.clock.now = 20.0
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))

    def test_closed_documents_cannot_be_revived_by_reordered_requests(self) -> None:
        self.lifetime.event(self.first, "open")
        lease = self.lifetime.begin_wait(self.first)
        self.assertIsNotNone(lease)
        self.lifetime.event(self.first, "close")
        if lease is not None:
            self.lifetime.finish_wait(lease, delivered=True)
        for event in ("open", "heartbeat"):
            self.assertFalse(self.lifetime.event(self.first, event)["registered"])
        self.assertIsNone(self.lifetime.begin_wait(self.first))
        self.lifetime.event(self.second, "close")  # close arrived before initial open
        self.assertFalse(self.lifetime.event(self.second, "open")["registered"])
        self.clock.now = 4.0
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "last-browser-document-closed"
        )

    def test_crash_expiry_uses_last_successful_presence_then_grace(self) -> None:
        self.lifetime.event(self.first, "open")
        self.clock.now = 179.0
        self.lifetime.event(self.first, "heartbeat")
        self.clock.now = 358.0
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.clock.now = 359.0
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.assertFalse(self.lifetime.event(self.first, "heartbeat")["registered"])
        self.clock.now = 363.0
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "browser-presence-expired"
        )

    def test_longpoll_lease_covers_background_timer_throttling(self) -> None:
        lifetime = BrowserLifetime(clock=self.clock, stale_timeout_seconds=0.1, wait_seconds=25)
        lifetime.event(self.first, "open")
        lease = lifetime.begin_wait(self.first)
        self.assertIsNotNone(lease)
        self.clock.now = 20.0
        self.assertIsNone(lifetime.shutdown_reason(operation_active=False))
        if lease is not None:
            lifetime.finish_wait(lease, delivered=True)
        self.clock.now = 20.05
        self.assertTrue(lifetime.response(self.first)["registered"])
        self.clock.now = 20.2
        self.assertFalse(lifetime.response(self.first)["registered"])

    def test_failed_longpoll_response_does_not_refresh_last_presence(self) -> None:
        lifetime = BrowserLifetime(clock=self.clock, stale_timeout_seconds=10)
        lifetime.event(self.first, "open")
        lease = lifetime.begin_wait(self.first)
        self.clock.now = 8.0
        if lease is not None:
            lifetime.finish_wait(lease, delivered=False)
        self.clock.now = 10.0
        self.assertFalse(lifetime.response(self.first)["registered"])

    def test_active_operation_delays_exit_until_completion_and_new_tab_can_cancel_exit(
        self,
    ) -> None:
        self.lifetime.event(self.first, "open")
        self.lifetime.event(self.first, "close")
        self.clock.now = 10.0
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=True))
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "last-browser-document-closed"
        )
        self.lifetime.event(self.second, "open")
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))

    def test_resume_grants_reconnect_time_without_disabling_regular_crash_expiry(self) -> None:
        self.lifetime.start()
        self.lifetime.event(self.first, "open")
        self.clock.now = 240.0
        self.lifetime.monitor_tick()
        self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        for tick in range(255, 421, 15):
            self.clock.now = float(tick)
            self.lifetime.monitor_tick()
            self.assertIsNone(self.lifetime.shutdown_reason(operation_active=False))
        self.clock.now = 424.0
        self.lifetime.monitor_tick()
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "browser-presence-expired"
        )

    def test_resume_does_not_restore_an_explicitly_closed_document(self) -> None:
        self.lifetime.start()
        self.lifetime.event(self.first, "open")
        self.lifetime.event(self.first, "close")
        self.clock.now = 240.0
        self.lifetime.monitor_tick()
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "last-browser-document-closed"
        )

    def test_close_and_stop_wake_held_waits(self) -> None:
        for action in (
            lambda: self.lifetime.event(self.first, "close"),
            lambda: self.lifetime.stop(),
        ):
            self.lifetime = BrowserLifetime(clock=self.clock)
            self.lifetime.event(self.first, "open")
            lease = self.lifetime.begin_wait(self.first)
            self.assertIsNotNone(lease)
            if lease is not None:
                thread = threading.Thread(target=self.lifetime.wait, args=(lease,), daemon=True)
                thread.start()
                action()
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())

    def test_parallel_documents_and_late_wait_completions_remain_closed(self) -> None:
        def visit(document_id: str) -> None:
            self.lifetime.event(document_id, "open")
            lease = self.lifetime.begin_wait(document_id)
            self.lifetime.event(document_id, "close")
            if lease is not None:
                self.lifetime.finish_wait(lease, delivered=True)
            self.assertFalse(self.lifetime.event(document_id, "heartbeat")["registered"])

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(visit, [str(uuid.uuid4()) for _ in range(40)]))
        self.clock.now = 4.0
        self.assertEqual(
            self.lifetime.shutdown_reason(operation_active=False), "last-browser-document-closed"
        )

    def test_uuid_events_and_timeouts_are_strict(self) -> None:
        for document_id in ("", "not-a-uuid", "{" + self.first + "}"):
            with self.subTest(document_id=document_id), self.assertRaises(ValueError):
                self.lifetime.event(document_id, "open")
        with self.assertRaises(ValueError):
            self.lifetime.event(self.first, "hidden")
        for timeout in (0, -1, math.nan, math.inf, True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                BrowserLifetime(stale_timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
