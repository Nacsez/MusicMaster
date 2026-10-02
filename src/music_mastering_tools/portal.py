"""Localhost-only graphical web portal for Music Mastering Tools."""

from __future__ import annotations

import argparse
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import stat
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .browser_lifetime import BrowserLifetime
from .catalog import CatalogConflictError, CatalogError, CatalogStore
from .errors import MusicMasteringError
from .native_dialogs import choose_paths, show_in_folder
from .portal_app import PortalApplication, PortalBusyError

PORTAL_HOST = "127.0.0.1"
DEFAULT_PORT = 0
MAX_REQUEST_BYTES = 1_048_576
SERVER_NAME = "MusicMasteringToolsPortal/0.1"
MEDIA_ROUTE_PREFIX = "/media/tracks/"
MEDIA_COOKIE_NAME = "MMT-Media-Session"
PAGE_COOKIE_PREFIX = "MMT-Page-Session-"
MEDIA_STREAM_CHUNK_BYTES = 64 * 1024
_CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

_AUDIO_MIME_TYPES: Mapping[str, str] = {
    ".aac": "audio/aac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".wave": "audio/wav",
}


class _MediaRangeError(ValueError):
    """A rejected single-range media request with safe public details."""

    def __init__(
        self,
        message: str,
        *,
        status: HTTPStatus,
        size_bytes: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.size_bytes = size_bytes


class PortalHTTPServer(ThreadingHTTPServer):
    """Threaded loopback server carrying one portal application."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        application: PortalApplication,
        token: str,
        logger: logging.Logger,
        browser_lifetime: BrowserLifetime | None = None,
    ) -> None:
        host, _ = address
        if host != PORTAL_HOST:
            raise ValueError("the private portal may bind only to 127.0.0.1")
        self.application = application
        self.session_token = token
        # Media elements cannot attach the private API header. Give the browser
        # a distinct, per-server secret that is accepted only by /media/.
        self.media_session_token = secrets.token_urlsafe(32)
        # History removes the launch token from URLs. A separate index-only
        # credential lets refresh recover the page without granting API access.
        self.page_session_token = secrets.token_urlsafe(32)
        self.portal_logger = logger
        self.browser_lifetime = browser_lifetime
        self._browser_shutdown_requested = False
        super().__init__(address, PortalRequestHandler)

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        if self.browser_lifetime is not None:
            self.browser_lifetime.start()
        super().serve_forever(poll_interval=poll_interval)

    def service_actions(self) -> None:
        """Check tab lifetime from the serving loop, never from an orphan watchdog."""

        lifetime = self.browser_lifetime
        if lifetime is None or self._browser_shutdown_requested:
            return
        lifetime.monitor_tick()
        reason = lifetime.shutdown_reason(operation_active=self.application.operations.active)
        if reason is not None:
            self._browser_shutdown_requested = True
            self.portal_logger.info("Portal exiting with browser reason=%s", reason)
            threading.Thread(
                target=self.shutdown,
                name="mmt-browser-lifetime-shutdown",
                daemon=True,
            ).start()

    def server_close(self) -> None:
        if self.browser_lifetime is not None:
            self.browser_lifetime.stop()
        super().server_close()

    @property
    def origin(self) -> str:
        host, port = cast(tuple[str, int], self.server_address)
        return f"http://{host}:{port}"

    @property
    def page_cookie_name(self) -> str:
        # Browsers scope cookies by host/path, not port. Distinct cookie names
        # let simultaneous workspace servers refresh independently.
        return f"{PAGE_COOKIE_PREFIX}{cast(tuple[str, int], self.server_address)[1]}"


class PortalRequestHandler(BaseHTTPRequestHandler):
    """Strict JSON and static-asset handler for the private portal."""

    server: PortalHTTPServer
    server_version = SERVER_NAME
    sys_version = ""
    protocol_version = "HTTP/1.1"

    _asset_names: ClassVar[set[str]] = {"app.css", "app.js"}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        request_id = secrets.token_hex(8)
        try:
            self._validate_local_request()
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                launch_tokens = parse_qs(parsed.query, keep_blank_values=True).get("token")
                query_authorized = (
                    launch_tokens is not None
                    and len(launch_tokens) == 1
                    and self._token_matches(launch_tokens[0])
                )
                cookie_authorized = launch_tokens is None and self._cookie_matches(
                    self.server.page_cookie_name,
                    self.server.page_session_token,
                )
                if not query_authorized and not cookie_authorized:
                    self._error(
                        HTTPStatus.FORBIDDEN,
                        "AuthorizationError",
                        "Use the private launch URL created by the application.",
                        request_id,
                    )
                    return
                self._serve_index(issue_page_cookie=query_authorized)
                return
            if parsed.path.startswith("/assets/"):
                self._serve_asset(parsed.path.removeprefix("/assets/"))
                return
            if parsed.path == "/favicon.ico":
                self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                return
            if parsed.path.startswith(MEDIA_ROUTE_PREFIX):
                if parsed.query:
                    raise ValueError("media routes do not accept query parameters")
                self._require_media_cookie()
                self._serve_media_track(
                    unquote(parsed.path.removeprefix(MEDIA_ROUTE_PREFIX)),
                )
                return
            self._require_api_token()
            if not parsed.path.startswith("/api/"):
                raise KeyError("unknown route")
            data = self._dispatch_get(parsed.path, parse_qs(parsed.query))
            self._json(HTTPStatus.OK, {"ok": True, "data": data})
        except _CLIENT_DISCONNECT_ERRORS as exc:
            self._client_disconnected(exc, request_id)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary maps failures
            self._handle_exception(exc, request_id)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Serve metadata for authenticated catalog media without a body."""

        request_id = secrets.token_hex(8)
        try:
            self._validate_local_request()
            parsed = urlsplit(self.path)
            if not parsed.path.startswith(MEDIA_ROUTE_PREFIX):
                raise KeyError("unknown HEAD route")
            if parsed.query:
                raise ValueError("media routes do not accept query parameters")
            self._require_media_cookie()
            self._serve_media_track(
                unquote(parsed.path.removeprefix(MEDIA_ROUTE_PREFIX)),
                head_only=True,
            )
        except _CLIENT_DISCONNECT_ERRORS as exc:
            self._client_disconnected(exc, request_id)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary maps failures
            self._handle_exception(exc, request_id)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        request_id = secrets.token_hex(8)
        try:
            self._validate_local_request()
            self._require_api_token()
            parsed = urlsplit(self.path)
            if not parsed.path.startswith("/api/"):
                raise KeyError("unknown route")
            payload = self._read_json_object()
            if parsed.path == "/api/browser-session/wait":
                self._serve_browser_wait(payload)
                return
            status, data = self._dispatch_post(parsed.path, payload)
            self._json(status, {"ok": True, "data": data})
        except _CLIENT_DISCONNECT_ERRORS as exc:
            self._client_disconnected(exc, request_id)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary maps failures
            # A malformed or rejected POST can leave unread request bytes on a
            # persistent connection. Close it after the response so those bytes
            # can never be interpreted as a second HTTP request.
            self.close_connection = True
            self._handle_exception(exc, request_id)

    def do_OPTIONS(self) -> None:  # noqa: N802 - explicitly refuse CORS
        self._error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "MethodNotAllowed",
            "Cross-origin requests are not supported.",
            secrets.token_hex(8),
        )

    def log_message(self, format: str, *args: object) -> None:
        message = format % args
        for secret in (
            self.server.session_token,
            self.server.media_session_token,
            self.server.page_session_token,
        ):
            message = message.replace(secret, "[REDACTED]")
        # BaseHTTPRequestHandler logs the complete request target. Strip every
        # query string, not just known secrets, so launch and hostile query
        # values can never be retained in the portal log.
        message = re.sub(
            r'("(?:GET|POST|HEAD|OPTIONS) [^?\s"]+)\?[^ \r\n"]*',
            r"\1?[REDACTED]",
            message,
        )
        self.server.portal_logger.info(
            "%s %s",
            self.address_string(),
            message,
        )

    def _dispatch_get(
        self,
        path: str,
        query: Mapping[str, list[str]],
    ) -> Any:
        application = self.server.application
        if path == "/api/bootstrap":
            data = application.bootstrap()
            lifetime = self.server.browser_lifetime
            data["browser_lifetime"] = (
                lifetime.configuration if lifetime is not None else {"enabled": False}
            )
            return data
        if path == "/api/job-defaults":
            return application.job_defaults()
        if path == "/api/preferences":
            return application.get_preferences()
        if path == "/api/capabilities":
            return application.capabilities()
        if path == "/api/diagnostics":
            return application.diagnostics()
        if path == "/api/logs":
            return application.list_logs()
        if path.startswith("/api/logs/"):
            return application.read_log(unquote(path.removeprefix("/api/logs/")))
        if path == "/api/tracks":
            return application.list_tracks(
                role=_first(query, "role"),
                include_archived=_query_bool(query, "include_archived", True),
                query=_first(query, "q"),
            )
        if path == "/api/master-library":
            return application.master_library(
                include_discarded=_query_bool(
                    query,
                    "include_discarded",
                    False,
                ),
                include_archived=_query_bool(
                    query,
                    "include_archived",
                    False,
                ),
            )
        if path.startswith("/api/tracks/"):
            return application.get_track(unquote(path.removeprefix("/api/tracks/")))
        if path == "/api/reference-sets":
            return application.list_reference_sets()
        if path == "/api/runs":
            return application.list_runs()
        if path == "/api/tasks/current":
            current = application.operations.current()
            return current.to_dict() if current is not None else None
        if path == "/api/tasks":
            return [item.to_dict() for item in application.operations.list()]
        if path.startswith("/api/tasks/"):
            operation_id = unquote(path.removeprefix("/api/tasks/"))
            return application.operations.get(operation_id).to_dict()
        if path.startswith("/api/runs/"):
            suffix = path.removeprefix("/api/runs/")
            if suffix.endswith("/artifacts"):
                run_id = unquote(suffix.removesuffix("/artifacts").rstrip("/"))
                return application.list_run_artifacts(run_id)
            if suffix.endswith("/manifest"):
                run_id = unquote(suffix.removesuffix("/manifest").rstrip("/"))
                return application.load_run_manifest(run_id)
        raise KeyError(f"unknown GET route: {path}")

    def _dispatch_post(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> tuple[HTTPStatus, Any]:
        application = self.server.application
        if path == "/api/browser-session":
            if set(payload) != {"document_id", "event"}:
                raise ValueError(
                    "browser session payload must contain exactly document_id and event"
                )
            document_id = _required_string(payload, "document_id")
            event = _required_string(payload, "event")
            if event not in {"open", "heartbeat", "close"}:
                raise ValueError("browser event must be open, heartbeat, or close")
            lifetime = self.server.browser_lifetime
            if lifetime is not None:
                lifetime.monitor_tick()
            data = (
                lifetime.event(document_id, event)
                if lifetime is not None
                else {
                    "enabled": False,
                    "registered": False,
                    "document_id": document_id,
                }
            )
            return HTTPStatus.OK, data
        if path == "/api/dialog":
            mode = _required_string(payload, "mode")
            purpose = _required_string(payload, "purpose")
            paths = choose_paths(
                mode=mode,
                purpose=purpose,
                suggested_name=_optional_string(payload, "suggested_name"),
                initial_directory=application.dialog_initial_directory(
                    mode,
                    purpose,
                    _optional_string(payload, "initial_directory"),
                ),
            )
            application.remember_dialog_location(mode, purpose, paths)
            return HTTPStatus.OK, {
                "cancelled": not paths,
                "paths": list(paths),
            }
        if path == "/api/open-folder":
            selected = _required_string(payload, "path")
            revealable = application.assert_revealable_path(selected)
            show_in_folder(revealable)
            return HTTPStatus.OK, {"path": str(revealable)}
        if path == "/api/preferences":
            return HTTPStatus.OK, application.update_preferences(payload)
        if path == "/api/tracks/add":
            raw_paths = payload.get("paths")
            if not isinstance(raw_paths, list) or not all(
                isinstance(item, str) for item in raw_paths
            ):
                raise ValueError("paths must be a JSON array of strings")
            raw_roles = payload.get("roles")
            if not isinstance(raw_roles, list) or not all(
                isinstance(item, str) for item in raw_roles
            ):
                raise ValueError("roles must be a JSON array of strings")
            raw_labels = payload.get("labels", {})
            if not isinstance(raw_labels, Mapping):
                raise ValueError("labels must be a JSON object")
            labels = {
                str(key): _optional_string_value(value, f"labels.{key}")
                for key, value in raw_labels.items()
            }
            return HTTPStatus.OK, application.add_tracks(
                raw_paths,
                roles=raw_roles,
                labels=labels,
            )
        if path == "/api/tracks/label":
            if set(payload) != {"track_id", "label"}:
                raise ValueError("track label payload must contain exactly track_id and label")
            return HTTPStatus.OK, application.update_track_label(
                _required_string(payload, "track_id"),
                payload["label"],
            )
        if path == "/api/tracks/verify":
            return HTTPStatus.OK, application.verify_tracks(_optional_string(payload, "track_id"))
        if path == "/api/tracks/relink":
            return HTTPStatus.OK, application.relink_track(
                _required_string(payload, "track_id"),
                _required_string(payload, "path"),
                archive_location_id=_optional_string(
                    payload,
                    "archive_location_id",
                ),
            )
        if path in {"/api/tracks/archive", "/api/tracks/restore"}:
            return HTTPStatus.OK, application.set_track_archived(
                _required_string(payload, "track_id"),
                archived=path.endswith("/archive"),
            )
        if path == "/api/catalog/export":
            return HTTPStatus.OK, application.export_catalog(
                _required_string(payload, "path"),
                overwrite=_optional_bool(payload, "overwrite", False),
            )
        if path == "/api/catalog/import":
            return HTTPStatus.OK, application.import_catalog(_required_string(payload, "path"))
        if path == "/api/selections/export":
            job = payload.get("job")
            if not isinstance(job, Mapping):
                raise ValueError("job must be a JSON object")
            return HTTPStatus.OK, application.export_selection(
                cast(Mapping[str, Any], job),
                _required_string(payload, "path"),
                overwrite=_optional_bool(payload, "overwrite", False),
            )
        if path == "/api/master-library/export":
            if set(payload) != {"destination_directory", "suffix", "items"}:
                raise ValueError(
                    "master export payload must contain exactly "
                    "destination_directory, suffix, and items"
                )
            return HTTPStatus.OK, application.export_master_versions(
                payload["destination_directory"],
                payload["suffix"],
                payload["items"],
            )
        if path == "/api/master-library/discard":
            if set(payload) != {"source_track_id", "version_id"}:
                raise ValueError(
                    "master discard payload must contain exactly source_track_id and version_id"
                )
            return HTTPStatus.OK, application.discard_master_version(
                _required_string(payload, "source_track_id"),
                _required_string(payload, "version_id"),
            )
        if path == "/api/master-library/restore":
            if set(payload) != {"source_track_id", "version_id"}:
                raise ValueError(
                    "master restore payload must contain exactly source_track_id and version_id"
                )
            return HTTPStatus.OK, application.restore_master_version(
                _required_string(payload, "source_track_id"),
                _required_string(payload, "version_id"),
            )
        if path == "/api/reference-sets/create":
            return HTTPStatus.OK, application.create_reference_set(
                _required_string(payload, "name")
            )
        if path == "/api/reference-sets/rename":
            return HTTPStatus.OK, application.rename_reference_set(
                _required_string(payload, "set_id"),
                _required_string(payload, "name"),
            )
        if path == "/api/reference-sets/delete":
            return HTTPStatus.OK, application.delete_reference_set(
                _required_string(payload, "set_id")
            )
        if path == "/api/reference-sets/replace-members":
            members = payload.get("members")
            if not isinstance(members, list) or not all(
                isinstance(item, Mapping) for item in members
            ):
                raise ValueError("members must be a JSON array of objects")
            return HTTPStatus.OK, application.replace_reference_set_members(
                _required_string(payload, "set_id"),
                cast(list[Mapping[str, Any]], members),
            )
        if path == "/api/jobs/validate":
            return HTTPStatus.OK, application.validate_job_request(payload)
        if path in {"/api/jobs/dry-run", "/api/jobs/render"}:
            operation = application.start_job(
                payload,
                dry_run=path.endswith("/dry-run"),
            )
            return HTTPStatus.ACCEPTED, operation.to_dict()
        if path == "/api/recovery/run":
            return HTTPStatus.OK, application.recover_run(
                _required_string(payload, "manifest_path"),
                selection_path=_optional_string(payload, "selection_path"),
                configuration_path=_optional_string(
                    payload,
                    "configuration_path",
                ),
            )
        if path == "/api/shutdown":
            if application.operations.active:
                raise PortalBusyError(
                    "mastering is still running; leave the portal open until it finishes"
                )
            threading.Thread(
                target=self.server.shutdown,
                name="mmt-portal-shutdown",
                daemon=True,
            ).start()
            return HTTPStatus.ACCEPTED, {"shutting_down": True}
        raise KeyError(f"unknown POST route: {path}")

    def _serve_browser_wait(self, payload: Mapping[str, Any]) -> None:
        if set(payload) != {"document_id"}:
            raise ValueError("browser wait payload must contain exactly document_id")
        document_id = _required_string(payload, "document_id")
        lifetime = self.server.browser_lifetime
        if lifetime is None:
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "data": {
                        "enabled": False,
                        "registered": False,
                        "document_id": document_id,
                    },
                },
            )
            return
        lifetime.monitor_tick()
        lease = lifetime.begin_wait(document_id)
        if lease is None:
            self._json(HTTPStatus.OK, {"ok": True, "data": lifetime.response(document_id)})
            return
        delivered = False
        try:
            lifetime.wait(lease)
            self._json(HTTPStatus.OK, {"ok": True, "data": lifetime.response(document_id)})
            delivered = True
        finally:
            lifetime.finish_wait(lease, delivered=delivered)

    def _serve_index(self, *, issue_page_cookie: bool = True) -> None:
        source = _asset_bytes("index.html").decode("utf-8")
        nonce = secrets.token_urlsafe(18)
        config = json.dumps(
            {
                "token": self.server.session_token,
                "version": __version__,
                "origin": self.server.origin,
            },
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).replace("</", "<\\/")
        injection = (
            f'<script nonce="{html.escape(nonce, quote=True)}">'
            f"window.__MMT_CONFIG__={config};"
            "</script>"
        )
        if "</head>" not in source:
            raise RuntimeError("portal index is missing its closing head element")
        rendered = source.replace("</head>", f"{injection}</head>", 1).encode("utf-8")
        self._send_bytes(
            HTTPStatus.OK,
            rendered,
            "text/html; charset=utf-8",
            csp_nonce=nonce,
            set_cookies=(
                f"{self.server.page_cookie_name}={self.server.page_session_token}; "
                "Path=/; HttpOnly; SameSite=Strict",
            )
            if issue_page_cookie
            else (),
            additional_headers={
                "Set-Cookie": (
                    f"{MEDIA_COOKIE_NAME}={self.server.media_session_token}; "
                    f"Path={MEDIA_ROUTE_PREFIX}; HttpOnly; SameSite=Strict"
                )
            },
        )

    def _serve_asset(self, name: str) -> None:
        if name not in self._asset_names:
            raise KeyError("unknown static asset")
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._send_bytes(HTTPStatus.OK, _asset_bytes(name), content_type)

    def _serve_media_track(
        self,
        track_id: str,
        *,
        head_only: bool = False,
    ) -> None:
        if not track_id or "/" in track_id or "\\" in track_id:
            raise ValueError("media route requires one catalog track identifier")

        with CatalogStore(self.server.application.layout.catalog_database) as catalog:
            try:
                track = catalog.get_track(track_id)
            except ValueError:
                raise
            except CatalogError as exc:
                raise KeyError("unknown catalog track") from exc
            location = catalog.preferred_location(track_id)

        path = Path(location.path)
        content_type = _audio_content_type(path)
        with path.open("rb") as source:
            facts = os.fstat(source.fileno())
            if not stat.S_ISREG(facts.st_mode):
                raise OSError("catalog audio location is not a regular file")
            if facts.st_size != track.size_bytes:
                raise OSError("catalog audio location changed; verify the catalog")
            if location.modified_ns is not None and facts.st_mtime_ns != location.modified_ns:
                raise OSError("catalog audio location changed; verify the catalog")

            start, end, partial = _media_byte_range(
                self.headers.get_all("Range", []),
                facts.st_size,
            )
            content_length = 0 if end < start else end - start + 1
            additional_headers = {"Accept-Ranges": "bytes"}
            if partial:
                additional_headers["Content-Range"] = f"bytes {start}-{end}/{facts.st_size}"
            self._send_headers(
                HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK,
                content_type,
                content_length,
                additional_headers=additional_headers,
            )
            if head_only or content_length == 0:
                return

            source.seek(start)
            remaining = content_length
            try:
                while remaining:
                    block = source.read(min(remaining, MEDIA_STREAM_CHUNK_BYTES))
                    if not block:
                        self.close_connection = True
                        return
                    self.wfile.write(block)
                    remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                # Switching A/B sources routinely cancels an in-flight browser
                # range request; it is not an application error.
                self.close_connection = True

    def _read_json_object(self) -> Mapping[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise ValueError("POST requests must use application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length is invalid") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must not exceed {MAX_REQUEST_BYTES} bytes")
        document = self.rfile.read(length).decode("utf-8")
        value = json.loads(
            document,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(value, Mapping):
            raise ValueError("request body must be a JSON object")
        return cast(Mapping[str, Any], value)

    def _validate_local_request(self) -> None:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            raise PermissionError("only loopback clients may use this portal")
        expected_port = cast(tuple[str, int], self.server.server_address)[1]
        valid_hosts = {
            f"127.0.0.1:{expected_port}",
            f"localhost:{expected_port}",
        }
        host = self.headers.get("Host", "")
        if host.casefold() not in valid_hosts:
            raise PermissionError("request Host is not the private portal")
        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if value is None:
                continue
            if not any(
                value.casefold().startswith(f"http://{candidate}/")
                or value.casefold() == f"http://{candidate}"
                for candidate in valid_hosts
            ):
                raise PermissionError(f"{header} does not belong to the private portal")

    def _require_api_token(self) -> None:
        if not self._token_matches(self.headers.get("X-MMT-Token", "")):
            raise PermissionError("the private portal token is missing or invalid")

    def _require_media_cookie(self) -> None:
        if not self._cookie_matches(MEDIA_COOKIE_NAME, self.server.media_session_token):
            raise PermissionError("the private media session is missing or invalid")

    def _cookie_matches(self, cookie_name: str, secret: str) -> bool:
        values: list[str] = []
        for raw_cookie in self.headers.get_all("Cookie", []):
            for field in raw_cookie.split(";"):
                name, separator, value = field.strip().partition("=")
                if separator and name == cookie_name:
                    values.append(value)
        return len(values) == 1 and hmac.compare_digest(
            values[0].encode("utf-8"), secret.encode("ascii")
        )

    def _token_matches(self, value: str) -> bool:
        return hmac.compare_digest(value, self.server.session_token)

    def _client_disconnected(self, exc: ConnectionError, request_id: str) -> None:
        self.close_connection = True
        self.server.portal_logger.debug(
            "Portal client disconnected request_id=%s method=%s type=%s errno=%s winerror=%s",
            request_id,
            self.command,
            type(exc).__name__,
            exc.errno,
            getattr(exc, "winerror", None),
        )

    def _handle_exception(self, exc: Exception, request_id: str) -> None:
        if isinstance(exc, _MediaRangeError):
            status = exc.status
        elif isinstance(exc, PermissionError):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, KeyError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, (PortalBusyError, CatalogConflictError, FileExistsError)):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, (ValueError, TypeError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
        elif isinstance(exc, FileNotFoundError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, (MusicMasteringError, CatalogError, OSError)):
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            self.server.portal_logger.exception(
                "Unhandled portal request failure request_id=%s",
                request_id,
            )
        else:
            self.server.portal_logger.warning(
                "Portal request failed request_id=%s type=%s message=%s",
                request_id,
                type(exc).__name__,
                exc,
            )
        details: dict[str, Any] = {}
        code: str | None = None
        retryable = False
        if isinstance(exc, MusicMasteringError):
            error = exc.to_dict()
            details = cast(dict[str, Any], error["details"])
            code = cast(str, error["code"])
            retryable = cast(bool, error["retryable"])
        try:
            self._error(
                status,
                type(exc).__name__,
                str(exc) or type(exc).__name__,
                request_id,
                code=code,
                details=details,
                retryable=retryable,
                additional_headers=_media_range_error_headers(exc),
            )
        except _CLIENT_DISCONNECT_ERRORS as disconnect:
            self._client_disconnected(disconnect, request_id)

    def _error(
        self,
        status: HTTPStatus,
        exception_type: str,
        message: str,
        request_id: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
        additional_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._json(
            status,
            {
                "ok": False,
                "error": {
                    "exception_type": exception_type,
                    "code": code,
                    "message": message,
                    "details": dict(details or {}),
                    "retryable": retryable,
                    "request_id": request_id,
                },
            },
            additional_headers=additional_headers,
        )

    def _json(
        self,
        status: HTTPStatus,
        value: Mapping[str, Any],
        *,
        additional_headers: Mapping[str, str] | None = None,
    ) -> None:
        document = (
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        self._send_bytes(
            status,
            document,
            "application/json; charset=utf-8",
            additional_headers=additional_headers,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        content: bytes,
        content_type: str,
        *,
        csp_nonce: str | None = None,
        additional_headers: Mapping[str, str] | None = None,
        set_cookies: tuple[str, ...] = (),
    ) -> None:
        self._send_headers(
            status,
            content_type,
            len(content),
            csp_nonce=csp_nonce,
            additional_headers=additional_headers,
            set_cookies=set_cookies,
        )
        if content and self.command != "HEAD":
            self.wfile.write(content)

    def _send_headers(
        self,
        status: HTTPStatus,
        content_type: str,
        content_length: int,
        *,
        csp_nonce: str | None = None,
        additional_headers: Mapping[str, str] | None = None,
        set_cookies: tuple[str, ...] = (),
    ) -> None:
        # Validate before sending any bytes; repeated Set-Cookie fields remain
        # separate headers rather than becoming a comma-joined cookie value.
        for name, value in [
            *(additional_headers or {}).items(),
            *(("Set-Cookie", cookie) for cookie in set_cookies),
        ]:
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(
                character in value for character in "\r\n\x00"
            ):
                raise ValueError("response header name or value is invalid")
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        script_policy = "'self'"
        if csp_nonce is not None:
            script_policy += f" 'nonce-{csp_nonce}'"
        self.send_header(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                f"script-src {script_policy}; "
                "style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; font-src 'self'; media-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                "form-action 'self'"
            ),
        )
        for cookie in set_cookies:
            self.send_header("Set-Cookie", cookie)
        for name, value in (additional_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()


def create_portal_server(
    workspace: str | Path,
    *,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    logger: logging.Logger | None = None,
    application: PortalApplication | None = None,
    exit_with_browser: bool = False,
    browser_lifetime: BrowserLifetime | None = None,
) -> PortalHTTPServer:
    """Create, but do not run, a loopback-only portal server."""

    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65_535:
        raise ValueError("port must be an integer from 0 through 65535")
    selected_logger = logger or logging.getLogger("music_mastering_tools.portal")
    selected_application = application or PortalApplication(
        workspace,
        logger=selected_logger,
    )
    selected_token = token or secrets.token_urlsafe(32)
    if len(selected_token) < 32:
        raise ValueError("portal token must contain at least 32 characters")
    return PortalHTTPServer(
        (PORTAL_HOST, port),
        selected_application,
        selected_token,
        selected_logger,
        browser_lifetime
        or (BrowserLifetime(logger=selected_logger) if exit_with_browser else None),
    )


def run_portal(
    workspace: str | Path,
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    ready_file: str | Path | None = None,
    browser_failure: Callable[[str], None] | None = None,
    on_ready: Callable[[Mapping[str, Any]], None] | None = None,
    exit_with_browser: bool = False,
) -> int:
    """Run until explicit shutdown, or last browser close when opted in."""

    layout_root = Path(workspace).resolve()
    log_directory = layout_root / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime_stamp()
    log_path = log_directory / f"portal-{stamp}.log"
    logger = _portal_logger(log_path)
    application: PortalApplication | None = None
    server: PortalHTTPServer | None = None
    try:
        application = PortalApplication(layout_root, logger=logger)
        server = create_portal_server(
            layout_root,
            port=port,
            logger=logger,
            application=application,
            exit_with_browser=exit_with_browser,
        )
        _announce_portal(
            server,
            log_path,
            logger,
            open_browser=open_browser,
            ready_file=ready_file,
            browser_failure=browser_failure,
            on_ready=on_ready,
            exit_with_browser=exit_with_browser,
        )
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        logger.info("Portal interrupted from its launcher console.")
    finally:
        try:
            if server is not None:
                server.server_close()
            if application is not None:
                application.close(wait=True)
        finally:
            logger.info("Portal stopped.")
            _close_logger(logger)
    return 0


def _announce_portal(
    server: PortalHTTPServer,
    log_path: Path,
    logger: logging.Logger,
    *,
    open_browser: bool,
    ready_file: str | Path | None,
    browser_failure: Callable[[str], None] | None,
    on_ready: Callable[[Mapping[str, Any]], None] | None,
    exit_with_browser: bool,
) -> None:
    launch_url = f"{server.origin}/?token={server.session_token}"
    ready = {
        "origin": server.origin,
        "url": launch_url,
        "port": cast(tuple[str, int], server.server_address)[1],
        "log": str(log_path),
        "exit_with_browser": exit_with_browser,
    }
    if ready_file is not None:
        ready_path = Path(ready_file).resolve()
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(
            json.dumps(
                ready,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    if on_ready is not None:
        on_ready(ready)
    logger.info("Private portal ready at %s", server.origin)
    logger.info("Portal log: %s", log_path)
    print(f"Music Mastering Tools portal: {server.origin}", flush=True)
    print(f"Portal log: {log_path}", flush=True)
    if open_browser:
        browser_opened = False
        try:
            browser_opened = webbrowser.open(launch_url, new=1, autoraise=True)
        except Exception as exc:  # noqa: BLE001 - browser adapter fallback boundary
            logger.warning(
                "The default browser launch failed: %s: %s",
                type(exc).__name__,
                exc,
            )
        if not browser_opened:
            logger.warning("The default browser did not acknowledge the launch request.")
            print(f"Private launch URL: {launch_url}", flush=True)
            print("Open the private launch URL in a local browser.", flush=True)
            if browser_failure is not None:
                browser_failure(launch_url)
    else:
        print(f"Private launch URL: {launch_url}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mmt-portal",
        description="Open the private localhost graphical mastering portal.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("private-workspace"),
        help="private mutable workspace (default: private-workspace)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="loopback port; zero chooses an available port (default: 0)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="start the portal without opening the default browser",
    )
    parser.add_argument(
        "--write-ready",
        type=Path,
        help=argparse.SUPPRESS,
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    return run_portal(
        args.workspace,
        port=args.port,
        open_browser=not args.no_browser,
        ready_file=args.write_ready,
    )


def datetime_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _portal_logger(path: Path) -> logging.Logger:
    logger = logging.getLogger(f"music_mastering_tools.portal.{id(path)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(threadName)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _close_logger(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


def _asset_bytes(name: str) -> bytes:
    asset = resources.files("music_mastering_tools").joinpath(
        "web_assets",
        name,
    )
    return asset.read_bytes()


def _audio_content_type(path: Path) -> str:
    explicit = _AUDIO_MIME_TYPES.get(path.suffix.casefold())
    if explicit is not None:
        return explicit
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed if guessed and guessed.startswith("audio/") else "application/octet-stream"


def _media_byte_range(
    raw_headers: list[str],
    size_bytes: int,
) -> tuple[int, int, bool]:
    """Parse one RFC-style byte range without ever materializing file data."""

    if not raw_headers:
        return 0, size_bytes - 1, False
    if len(raw_headers) != 1:
        raise _MediaRangeError(
            "multiple byte ranges are not supported",
            status=HTTPStatus.BAD_REQUEST,
        )

    unit, separator, specification = raw_headers[0].strip().partition("=")
    if not separator or unit.strip().casefold() != "bytes":
        raise _MediaRangeError(
            "Range must use the bytes unit",
            status=HTTPStatus.BAD_REQUEST,
        )
    if "," in specification:
        raise _MediaRangeError(
            "multiple byte ranges are not supported",
            status=HTTPStatus.BAD_REQUEST,
        )
    if specification.count("-") != 1:
        raise _MediaRangeError(
            "Range must contain one start-end byte interval",
            status=HTTPStatus.BAD_REQUEST,
        )

    raw_start, raw_end = (item.strip() for item in specification.split("-", 1))
    if not raw_start and not raw_end:
        raise _MediaRangeError(
            "Range interval is empty",
            status=HTTPStatus.BAD_REQUEST,
        )
    if (raw_start and not raw_start.isascii()) or (raw_end and not raw_end.isascii()):
        raise _MediaRangeError(
            "Range offsets must be decimal integers",
            status=HTTPStatus.BAD_REQUEST,
        )
    if (raw_start and not raw_start.isdigit()) or (raw_end and not raw_end.isdigit()):
        raise _MediaRangeError(
            "Range offsets must be decimal integers",
            status=HTTPStatus.BAD_REQUEST,
        )
    if len(raw_start) > 20 or len(raw_end) > 20:
        raise _MediaRangeError(
            "Range offsets are too large",
            status=HTTPStatus.BAD_REQUEST,
        )

    if not raw_start:
        suffix_length = int(raw_end)
        if suffix_length == 0 or size_bytes == 0:
            raise _MediaRangeError(
                "requested byte range is not satisfiable",
                status=HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                size_bytes=size_bytes,
            )
        return max(size_bytes - suffix_length, 0), size_bytes - 1, True

    start = int(raw_start)
    if start >= size_bytes:
        raise _MediaRangeError(
            "requested byte range is not satisfiable",
            status=HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
            size_bytes=size_bytes,
        )
    end = size_bytes - 1 if not raw_end else int(raw_end)
    if end < start:
        raise _MediaRangeError(
            "requested byte range is not satisfiable",
            status=HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
            size_bytes=size_bytes,
        )
    return start, min(end, size_bytes - 1), True


def _media_range_error_headers(exc: Exception) -> Mapping[str, str] | None:
    if not isinstance(exc, _MediaRangeError):
        return None
    headers = {"Accept-Ranges": "bytes"}
    if exc.status is HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE and exc.size_bytes is not None:
        headers["Content-Range"] = f"bytes */{exc.size_bytes}"
    return headers


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _first(query: Mapping[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


def _query_bool(
    query: Mapping[str, list[str]],
    name: str,
    default: bool,
) -> bool:
    value = _first(query, name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"query parameter {name!r} must be true or false")


def _required_string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(
    payload: Mapping[str, Any],
    name: str,
) -> str | None:
    return _optional_string_value(payload.get(name), name)


def _optional_string_value(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value.strip() or None


def _optional_bool(
    payload: Mapping[str, Any],
    name: str,
    default: bool,
) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


__all__ = [
    "DEFAULT_PORT",
    "MAX_REQUEST_BYTES",
    "PORTAL_HOST",
    "PortalHTTPServer",
    "PortalRequestHandler",
    "build_parser",
    "create_portal_server",
    "main",
    "run_portal",
]


if __name__ == "__main__":
    raise SystemExit(main())
