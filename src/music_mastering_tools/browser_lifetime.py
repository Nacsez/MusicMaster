"""Thread-safe, clock-injectable lifetime of browser documents using a portal.

Document presence is ephemeral process state. Closing the last document never
deletes workspace state, and an active mastering operation delays process exit.
Long-poll leases keep background documents alive without depending on timers.
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BrowserWaitLease:
    """One bounded presence request belonging to a particular document."""

    document_id: str
    ticket: int
    deadline: float


@dataclass(slots=True)
class _Document:
    last_seen: float
    waits: dict[int, float] = field(default_factory=dict)


class BrowserLifetime:
    """Track authenticated document presence and decide when exit is safe."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
        close_grace_seconds: float = 4.0,
        stale_timeout_seconds: float = 180.0,
        startup_timeout_seconds: float = 90.0,
        wait_seconds: float = 25.0,
    ) -> None:
        for name, value in (
            ("close_grace_seconds", close_grace_seconds),
            ("stale_timeout_seconds", stale_timeout_seconds),
            ("startup_timeout_seconds", startup_timeout_seconds),
            ("wait_seconds", wait_seconds),
        ):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        self._clock = clock
        self.logger = logger or logging.getLogger(__name__)
        self.close_grace_seconds = close_grace_seconds
        self.stale_timeout_seconds = stale_timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.wait_seconds = wait_seconds
        self._condition = threading.Condition(threading.RLock())
        self._documents: dict[str, _Document] = {}
        # Tombstones prevent close/open reordering or an in-flight heartbeat
        # from reviving a document after its page has gone away.
        self._closed_documents: set[str] = set()
        self._started_at = clock()
        self._has_seen_document = False
        self._empty_since: float | None = None
        self._empty_cause = "browser-presence-expired"
        self._next_ticket = 0
        self._stopped = False
        self._deferred_reason: str | None = None
        self._last_monitor_tick: float | None = None

    @property
    def configuration(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "close_grace_seconds": self.close_grace_seconds,
            "heartbeat_interval_seconds": 15,
            "stale_timeout_seconds": self.stale_timeout_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "wait_seconds": self.wait_seconds,
        }

    def start(self) -> None:
        """Begin the no-client deadline when the HTTP server starts serving."""

        with self._condition:
            self._started_at = self._clock()
            self._last_monitor_tick = self._started_at
            self.logger.info(
                "Browser lifetime started startup_timeout_seconds=%s close_grace_seconds=%s "
                "stale_timeout_seconds=%s wait_seconds=%s",
                self.startup_timeout_seconds,
                self.close_grace_seconds,
                self.stale_timeout_seconds,
                self.wait_seconds,
            )

    def monitor_tick(self) -> None:
        """Give open documents a reconnect window after machine suspension.

        Normal expiration still uses exact monotonic deadlines. This separate
        monitor hook recognizes a paused server loop before evaluating expiry;
        explicit document closes keep their original grace deadline.
        """

        with self._condition:
            now = self._clock()
            previous = self._last_monitor_tick
            self._last_monitor_tick = now
            if previous is not None and now - previous >= 60.0:
                for document in self._documents.values():
                    document.last_seen = now
                if not self._has_seen_document:
                    self._started_at = now
                self.logger.info(
                    "Browser lifetime monitor resumed after pause gap_seconds=%.3f "
                    "live_documents=%s reconnect_seconds=%s",
                    now - previous,
                    len(self._documents),
                    self.stale_timeout_seconds,
                )

    def event(self, document_id: str, event: str) -> dict[str, Any]:
        document_id = _document_id(document_id)
        if event not in {"open", "heartbeat", "close"}:
            raise ValueError("browser event must be open, heartbeat, or close")
        with self._condition:
            now = self._clock()
            self._expire(now)
            if event == "close":
                self._documents.pop(document_id, None)
                self._closed_documents.add(document_id)
                self._has_seen_document = True
                self._mark_empty(now, "last-browser-document-closed")
                self._condition.notify_all()
                self.logger.info(
                    "Browser document closed document_id=%s live_documents=%s",
                    document_id,
                    len(self._documents),
                )
            elif not self._stopped and document_id not in self._closed_documents:
                document = self._documents.get(document_id)
                if event == "open":
                    self._documents[document_id] = document or _Document(now)
                    self._has_seen_document = True
                if document_id in self._documents:
                    self._documents[document_id].last_seen = now
                    self._empty_since = None
                    self._deferred_reason = None
                    self.logger.log(
                        logging.INFO if event == "open" else logging.DEBUG,
                        "Browser document presence event=%s document_id=%s live_documents=%s",
                        event,
                        document_id,
                        len(self._documents),
                    )
            return self._response(document_id)

    def begin_wait(self, document_id: str) -> BrowserWaitLease | None:
        document_id = _document_id(document_id)
        with self._condition:
            now = self._clock()
            self._expire(now)
            document = self._documents.get(document_id)
            if document is None or self._stopped:
                return None
            self._next_ticket += 1
            deadline = now + self.wait_seconds
            # A held request is a lease; the safety margin allows its response
            # to finish before a very short configured stale interval elapses.
            document.waits[self._next_ticket] = deadline + 5.0
            document.last_seen = now
            self.logger.debug(
                "Browser presence wait started document_id=%s ticket=%s",
                document_id,
                self._next_ticket,
            )
            return BrowserWaitLease(document_id, self._next_ticket, deadline)

    def wait(self, lease: BrowserWaitLease) -> None:
        """Hold an HTTP request until its renewal deadline or document close."""

        with self._condition:
            while not self._stopped and lease.document_id in self._documents:
                remaining = lease.deadline - self._clock()
                if remaining <= 0:
                    return
                self._condition.wait(timeout=min(remaining, 0.5))

    def finish_wait(self, lease: BrowserWaitLease, *, delivered: bool) -> None:
        """Renew only a still-open document after the response is delivered."""

        with self._condition:
            document = self._documents.get(lease.document_id)
            if document is None:
                return
            document.waits.pop(lease.ticket, None)
            if delivered and not self._stopped:
                document.last_seen = self._clock()
            self.logger.debug(
                "Browser presence wait completed document_id=%s ticket=%s delivered=%s",
                lease.document_id,
                lease.ticket,
                delivered,
            )

    def response(self, document_id: str) -> dict[str, Any]:
        document_id = _document_id(document_id)
        with self._condition:
            self._expire(self._clock())
            return self._response(document_id)

    def shutdown_reason(self, *, operation_active: bool) -> str | None:
        with self._condition:
            now = self._clock()
            self._expire(now)
            if self._stopped or self._documents:
                return None
            if not self._has_seen_document:
                reason = (
                    "browser-startup-timeout"
                    if (now - self._started_at >= self.startup_timeout_seconds)
                    else None
                )
            else:
                reason = (
                    self._empty_cause
                    if (
                        self._empty_since is not None
                        and now - self._empty_since >= self.close_grace_seconds
                    )
                    else None
                )
            if reason is not None and operation_active:
                if self._deferred_reason != reason:
                    self.logger.info(
                        "Browser lifetime exit deferred reason=%s active_operation=true", reason
                    )
                    self._deferred_reason = reason
                return None
            return reason

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def _expire(self, now: float) -> None:
        for document_id, document in tuple(self._documents.items()):
            leased = any(deadline > now for deadline in document.waits.values())
            if not leased and now - document.last_seen >= self.stale_timeout_seconds:
                del self._documents[document_id]
                self._closed_documents.add(document_id)
                self.logger.info(
                    "Browser document expired document_id=%s idle_seconds=%.3f",
                    document_id,
                    now - document.last_seen,
                )
        self._mark_empty(now, "browser-presence-expired")

    def _mark_empty(self, now: float, cause: str) -> None:
        if self._has_seen_document and not self._documents and self._empty_since is None:
            self._empty_since = now
            self._empty_cause = cause
            self.logger.info(
                "Browser lifetime has no live documents cause=%s grace_seconds=%s",
                cause,
                self.close_grace_seconds,
            )

    def _response(self, document_id: str) -> dict[str, Any]:
        return {
            "enabled": True,
            "document_id": document_id,
            "registered": not self._stopped and document_id in self._documents,
        }


def _document_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("document_id must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("document_id must be a UUID string") from exc
    if str(parsed) != value.lower():
        raise ValueError("document_id must be a canonical UUID string")
    return str(parsed)
