"""Loopback HTTP and security contracts for the graphical portal."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from unittest import mock

from music_mastering_tools.portal import create_portal_server
from music_mastering_tools.portal_app import PortalApplication

from .helpers import write_tone


class PortalHttpTests(unittest.TestCase):
    token = "t" * 48

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.application = PortalApplication(root / "workspace")
        self.server = create_portal_server(
            root / "workspace",
            token=self.token,
            application=self.application,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        self.host, self.port = cast(tuple[str, int], self.server.server_address)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.application.close()
        self.temporary.cleanup()

    def test_index_assets_and_authenticated_bootstrap_have_security_headers(
        self,
    ) -> None:
        status, _, body = self._request("GET", "/")
        self.assertEqual(status, 403)
        self.assertFalse(json.loads(body)["ok"])

        status, headers, body = self._request(
            "GET",
            f"/?token={self.token}",
        )
        self.assertEqual(status, 200)
        self.assertIn("Music Mastering Tools", body)
        self.assertIn(self.token, body)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        media_cookie = next(
            cookie
            for cookie in headers.get_all("Set-Cookie", [])
            if cookie.startswith("MMT-Media-Session=")
        )
        self.assertIn("MMT-Media-Session=", media_cookie)
        self.assertIn("Path=/media/tracks/", media_cookie)
        self.assertIn("HttpOnly", media_cookie)
        self.assertIn("SameSite=Strict", media_cookie)
        self.assertNotIn("Secure", media_cookie)
        self.assertNotIn(self.token, media_cookie)

        for asset in ("app.css", "app.js"):
            with self.subTest(asset=asset):
                status, _, body = self._request("GET", f"/assets/{asset}")
                self.assertEqual(status, 200)
                self.assertTrue(body)

        status, _, body = self._request(
            "GET",
            "/api/bootstrap",
            token=self.token,
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["privacy"]["local_only"])

    def test_access_log_redacts_private_launch_token(self) -> None:
        with self.assertLogs(
            "music_mastering_tools.portal",
            level="INFO",
        ) as captured:
            status, _, _ = self._request(
                "GET",
                f"/?token={self.token}",
            )

        self.assertEqual(status, 200)
        joined = "\n".join(captured.output)
        self.assertNotIn(self.token, joined)
        self.assertIn("[REDACTED]", joined)

    def test_api_rejects_bad_token_origin_host_content_and_duplicate_json(
        self,
    ) -> None:
        status, _, _ = self._request("GET", "/api/tracks")
        self.assertEqual(status, 403)

        status, _, _ = self._request(
            "GET",
            "/api/tracks",
            token=self.token,
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)

        status, _, _ = self._request(
            "GET",
            "/api/tracks",
            token=self.token,
            headers={"Host": "attacker.example"},
        )
        self.assertEqual(status, 403)

        status, _, body = self._request(
            "POST",
            "/api/tracks/verify",
            token=self.token,
            body="{}",
            content_type="text/plain",
        )
        self.assertEqual(status, 400)
        self.assertIn("application/json", json.loads(body)["error"]["message"])

        status, _, body = self._request(
            "POST",
            "/api/tracks/verify",
            token=self.token,
            body='{"track_id":null,"track_id":null}',
        )
        self.assertEqual(status, 400)
        self.assertIn("duplicate JSON field", json.loads(body)["error"]["message"])

    def test_catalog_dialog_and_run_routes_return_structured_json(self) -> None:
        root = Path(self.temporary.name)
        target = write_tone(root / "target.wav", frequency=330)
        with mock.patch(
            "music_mastering_tools.portal.choose_paths",
            return_value=(str(target),),
        ):
            status, _, body = self._json_post(
                "/api/dialog",
                {
                    "mode": "open-audio",
                    "purpose": "audio",
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"]["paths"], [str(target)])

        status, _, body = self._json_post(
            "/api/tracks/add",
            {
                "paths": [str(target)],
                "roles": ["target"],
                "labels": {str(target): "HTTP target"},
            },
        )
        self.assertEqual(status, 200)
        added = json.loads(body)["data"]["results"][0]
        self.assertTrue(added["ok"])
        track_id = added["track"]["track_id"]

        status, _, body = self._request(
            "GET",
            f"/api/tracks/{track_id}",
            token=self.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"]["label"], "HTTP target")

        status, _, body = self._request(
            "GET",
            "/api/tasks/current",
            token=self.token,
        )
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["data"])

    def test_dialog_routes_pass_context_remember_success_and_preserve_cancelled_history(
        self,
    ) -> None:
        root = Path(self.temporary.name).resolve()
        chosen_folder = root / "other computer's music"
        chosen_folder.mkdir()
        with mock.patch(
            "music_mastering_tools.portal.choose_paths", return_value=(str(chosen_folder),)
        ) as choose:
            status, _, _ = self._json_post(
                "/api/dialog", {"mode": "choose-folder", "purpose": "folder"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(
            choose.call_args.kwargs["initial_directory"], self.application.layout.outputs
        )
        with mock.patch("music_mastering_tools.portal.choose_paths", return_value=()) as choose:
            status, _, body = self._json_post(
                "/api/dialog", {"mode": "choose-folder", "purpose": "folder"}
            )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["data"]["cancelled"])
        self.assertEqual(choose.call_args.kwargs["initial_directory"], chosen_folder)
        contextual = root / "album output"
        with mock.patch("music_mastering_tools.portal.choose_paths", return_value=()) as choose:
            status, _, _ = self._json_post(
                "/api/dialog",
                {
                    "mode": "choose-folder",
                    "purpose": "folder",
                    "initial_directory": str(contextual),
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(choose.call_args.kwargs["initial_directory"], contextual)
        self.assertEqual(
            self.application.dialog_initial_directory("choose-folder", "folder"), chosen_folder
        )
        with mock.patch("music_mastering_tools.portal.choose_paths") as choose:
            status, _, _ = self._json_post(
                "/api/dialog",
                {
                    "mode": "choose-folder",
                    "purpose": "folder",
                    "initial_directory": [],
                },
            )
        self.assertEqual(status, 400)
        choose.assert_not_called()

    def test_open_folder_authorizes_workspace_sources_and_user_output_destination(self) -> None:
        root = Path(self.temporary.name).resolve()
        target, _track_id = self._catalog_tone()
        output = root / "delivery outputs"
        output.mkdir()
        self.application.update_preferences(
            {
                **self.application.get_preferences(),
                "default_output_directory": str(output),
            }
        )
        for selected in (self.application.layout.root, output, target):
            with (
                self.subTest(selected=selected),
                mock.patch("music_mastering_tools.portal.show_in_folder") as reveal,
            ):
                status, _, body = self._json_post("/api/open-folder", {"path": str(selected)})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["data"]["path"], str(selected))
                reveal.assert_called_once_with(selected)
        with mock.patch("music_mastering_tools.portal.show_in_folder") as reveal:
            status, _, _ = self._json_post(
                "/api/open-folder", {"path": str(root / "unselected folder")}
            )
        self.assertEqual(status, 403)
        reveal.assert_not_called()

    def test_master_library_and_metadata_routes_are_strict_and_source_centric(
        self,
    ) -> None:
        target, track_id = self._catalog_tone()
        status, _, body = self._request(
            "GET",
            "/api/master-library",
            token=self.token,
        )
        self.assertEqual(status, 200)
        library = json.loads(body)["data"]
        self.assertEqual(library["summary"]["source_count"], 1)
        self.assertEqual(library["sources"][0]["source"]["track_id"], track_id)
        self.assertEqual(library["sources"][0]["status"], "unmastered")
        self.assertEqual(library["sources"][0]["versions"], [])

        status, _, body = self._json_post(
            "/api/tracks/label",
            {
                "track_id": track_id,
                "label": "HTTP renamed source",
            },
        )
        self.assertEqual(status, 200)
        renamed = json.loads(body)["data"]
        self.assertEqual(renamed["label"], "HTTP renamed source")
        self.assertEqual(renamed["preferred_path"], str(target.resolve()))
        self.assertTrue(target.is_file())

        status, _, body = self._json_post(
            "/api/tracks/label",
            {
                "track_id": track_id,
                "label": "bad\nlabel",
                "unexpected": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("exactly", json.loads(body)["error"]["message"])

        status, _, _ = self._json_post(
            "/api/tracks/archive",
            {"track_id": track_id},
        )
        self.assertEqual(status, 200)
        status, _, body = self._request(
            "GET",
            "/api/master-library",
            token=self.token,
        )
        self.assertEqual(json.loads(body)["data"]["summary"]["source_count"], 0)
        status, _, body = self._request(
            "GET",
            "/api/master-library?include_archived=true&include_discarded=true",
            token=self.token,
        )
        self.assertEqual(status, 200)
        archived = json.loads(body)["data"]["sources"]
        self.assertEqual(len(archived), 1)
        self.assertTrue(archived[0]["source"]["archived"])

        status, _, body = self._json_post(
            "/api/master-library/export",
            {
                "destination_directory": "relative",
                "suffix": "-master",
                "items": [],
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("absolute", json.loads(body)["error"]["message"])

        status, _, body = self._json_post(
            "/api/master-library/discard",
            {
                "source_track_id": track_id,
                "version_id": "not-a-run",
            },
        )
        self.assertEqual(status, 422)
        self.assertFalse(json.loads(body)["ok"])

    def test_preferences_routes_round_trip_and_feed_bootstrap_defaults(self) -> None:
        status, _, body = self._request(
            "GET",
            "/api/preferences",
            token=self.token,
        )
        self.assertEqual(status, 200)
        initial = json.loads(body)["data"]
        self.assertEqual(
            initial,
            {
                "kind": "music-mastering-tools/portal-preferences",
                "schema_version": 1,
                "default_output_directory": str(self.application.layout.outputs.resolve()),
            },
        )

        delivery = (Path(self.temporary.name) / "master delivery").resolve()
        updated = {
            "kind": "music-mastering-tools/portal-preferences",
            "schema_version": 1,
            "default_output_directory": str(delivery),
        }
        status, _, body = self._json_post("/api/preferences", updated)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"], updated)

        for path in (
            "/api/preferences",
            "/api/bootstrap",
            "/api/job-defaults",
        ):
            with self.subTest(route=path):
                status, _, body = self._request("GET", path, token=self.token)
                self.assertEqual(status, 200)
                data = json.loads(body)["data"]
                observed = data if path == "/api/preferences" else data["preferences"]
                self.assertEqual(observed, updated)

        status, _, body = self._json_post(
            "/api/preferences",
            {"default_output_directory": str(Path(self.temporary.name) / "other")},
        )
        self.assertEqual(status, 400)
        self.assertIn("fields are invalid", json.loads(body)["error"]["message"])
        status, _, body = self._request(
            "GET",
            "/api/preferences",
            token=self.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"], updated)

    def test_batch_validation_route_preserves_all_targets_and_destination(self) -> None:
        root = Path(self.temporary.name)

        def catalog(path: Path, role: str) -> str:
            result = self.application.add_tracks(
                [str(path)],
                roles=[role],
            )["results"][0]
            self.assertTrue(result["ok"])
            return str(result["track"]["track_id"])

        first_target = catalog(
            write_tone(root / "first-target.wav", frequency=330),
            "target",
        )
        second_target = catalog(
            write_tone(root / "second-target.wav", frequency=440),
            "target",
        )
        reference = catalog(
            write_tone(root / "reference.wav", frequency=660),
            "reference",
        )
        delivery = (root / "batch delivery").resolve()
        request: dict[str, object] = {
            "target_id": first_target,
            "target_ids": [first_target, second_target],
            "output_directory": str(delivery),
            "references": [
                {
                    "track_id": reference,
                    "level_weight": 1.0,
                    "frequency_weight": 1.0,
                }
            ],
            "outputs": {
                "limited": True,
                "normalized": False,
                "raw": False,
                "limited_subtype": "PCM_24",
                "normalized_subtype": "PCM_24",
                "raw_subtype": "FLOAT",
            },
            "preview": {
                "enabled": False,
                "subtype": "PCM_16",
                "duration_seconds": 30.0,
                "analysis_step_seconds": 5.0,
                "fade_seconds": 1.0,
                "fade_coefficient": 8.0,
            },
            "settings": {
                "audio": {},
                "matching": {},
                "limiter": {},
                "detection": {},
                "edge_cases": {},
            },
            "notes": "HTTP batch validation regression",
        }

        status, _, body = self._json_post("/api/jobs/validate", request)
        self.assertEqual(status, 200)
        result = json.loads(body)["data"]
        self.assertEqual(result["kind"], "batch-validation")
        self.assertEqual(
            result["batch"],
            {
                "target_count": 2,
                "valid_count": 2,
                "invalid_count": 0,
                "ok": True,
            },
        )
        self.assertEqual(
            [item["target_id"] for item in result["targets"]],
            [first_target, second_target],
        )
        self.assertTrue(all(item["ok"] for item in result["targets"]))
        self.assertEqual(
            {str(Path(item["prepared"]["output_root"])) for item in result["targets"]},
            {str(delivery)},
        )
        self.assertEqual(
            len({item["prepared"]["output_directory"] for item in result["targets"]}),
            2,
        )
        self.assertFalse(delivery.exists())

    def test_fresh_catalog_is_initialized_before_parallel_dashboard_reads(
        self,
    ) -> None:
        paths = (
            "/api/bootstrap",
            "/api/tracks?include_archived=true",
            "/api/reference-sets",
            "/api/runs",
        ) * 6
        barrier = threading.Barrier(len(paths))

        def request(path: str) -> tuple[int, str]:
            barrier.wait(timeout=5)
            status, _, body = self._request("GET", path, token=self.token)
            return status, body

        with ThreadPoolExecutor(max_workers=len(paths)) as executor:
            results = tuple(executor.map(request, paths))

        self.assertEqual(
            [status for status, _ in results],
            [200] * len(paths),
            [body for status, body in results if status != 200],
        )

    def test_media_stream_requires_scoped_cookie_and_catalog_identifier(
        self,
    ) -> None:
        target, track_id = self._catalog_tone()
        expected = target.read_bytes()
        media_path = f"/media/tracks/{track_id}"

        status, _, _ = self._request_bytes("GET", media_path)
        self.assertEqual(status, 403)

        cookie = self._media_cookie()
        status, headers, body = self._request_bytes(
            "GET",
            media_path,
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "audio/wav")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["Content-Length"], str(len(expected)))
        self.assertEqual(body, expected)

        status, _, _ = self._request_bytes(
            "GET",
            "/api/tracks",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 403, "the media cookie must not authorize APIs")

        status, _, _ = self._request_bytes(
            "GET",
            media_path,
            headers={
                "Cookie": cookie,
                "Origin": "https://attacker.example",
            },
        )
        self.assertEqual(status, 403)

        status, _, _ = self._request_bytes(
            "GET",
            media_path,
            headers={"Cookie": f"{cookie}; {cookie}"},
        )
        self.assertEqual(status, 403, "ambiguous media cookies must be rejected")

        status, _, _ = self._request_bytes(
            "GET",
            f"{media_path}?path=secret.wav",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 400)

        status, _, _ = self._request_bytes(
            "GET",
            f"{media_path}/../../secret.wav",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 400)

        status, _, _ = self._request_bytes(
            "GET",
            f"/media/tracks/trk_sha256_{'0' * 64}",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 404)

    def test_media_stream_supports_full_single_range_suffix_and_head(
        self,
    ) -> None:
        target, track_id = self._catalog_tone(frames=40_000)
        expected = target.read_bytes()
        media_path = f"/media/tracks/{track_id}"
        cookie = self._media_cookie()

        with mock.patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("media streaming must not use Path.read_bytes"),
        ):
            status, headers, body = self._request_bytes(
                "GET",
                media_path,
                headers={"Cookie": cookie},
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Length"], str(len(expected)))
        self.assertEqual(body, expected)

        status, headers, body = self._request_bytes(
            "GET",
            media_path,
            headers={
                "Cookie": cookie,
                "Range": "bytes=10-29",
            },
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], f"bytes 10-29/{len(expected)}")
        self.assertEqual(headers["Content-Length"], "20")
        self.assertEqual(body, expected[10:30])

        status, headers, body = self._request_bytes(
            "GET",
            media_path,
            headers={
                "Cookie": cookie,
                "Range": "bytes=-17",
            },
        )
        self.assertEqual(status, 206)
        self.assertEqual(
            headers["Content-Range"],
            f"bytes {len(expected) - 17}-{len(expected) - 1}/{len(expected)}",
        )
        self.assertEqual(body, expected[-17:])

        status, headers, body = self._request_bytes(
            "GET",
            media_path,
            headers={
                "Cookie": cookie,
                "Range": "bytes=42-",
            },
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, expected[42:])

        status, headers, body = self._request_bytes(
            "HEAD",
            media_path,
            headers={
                "Cookie": cookie,
                "Range": "bytes=4-11",
            },
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], f"bytes 4-11/{len(expected)}")
        self.assertEqual(headers["Content-Length"], "8")
        self.assertEqual(body, b"")

    def test_media_stream_rejects_invalid_and_unsatisfiable_ranges(
        self,
    ) -> None:
        target, track_id = self._catalog_tone()
        size_bytes = target.stat().st_size
        media_path = f"/media/tracks/{track_id}"
        cookie = self._media_cookie()

        for value in (
            "items=0-1",
            "bytes=abc-def",
            "bytes=",
            "bytes=0-1,3-4",
        ):
            with self.subTest(range=value):
                status, headers, _ = self._request_bytes(
                    "GET",
                    media_path,
                    headers={
                        "Cookie": cookie,
                        "Range": value,
                    },
                )
                self.assertEqual(status, 400)
                self.assertEqual(headers["Accept-Ranges"], "bytes")

        for value in (
            f"bytes={size_bytes}-",
            "bytes=9-3",
            "bytes=-0",
        ):
            with self.subTest(range=value):
                status, headers, _ = self._request_bytes(
                    "GET",
                    media_path,
                    headers={
                        "Cookie": cookie,
                        "Range": value,
                    },
                )
                self.assertEqual(status, 416)
                self.assertEqual(headers["Content-Range"], f"bytes */{size_bytes}")

    def test_media_auth_secrets_and_query_values_are_redacted(self) -> None:
        _, track_id = self._catalog_tone()
        cookie = self._media_cookie()
        media_secret = self.server.media_session_token
        with self.assertLogs(
            "music_mastering_tools.portal",
            level="INFO",
        ) as captured:
            status, _, _ = self._request_bytes(
                "GET",
                f"/media/tracks/{track_id}?candidate={media_secret}",
                headers={"Cookie": cookie},
            )

        self.assertEqual(status, 400)
        joined = "\n".join(captured.output)
        self.assertNotIn(self.token, joined)
        self.assertNotIn(media_secret, joined)
        self.assertNotIn("candidate=", joined)
        self.assertIn("?[REDACTED]", joined)

    def _catalog_tone(self, *, frames: int = 8_192) -> tuple[Path, str]:
        target = write_tone(
            Path(self.temporary.name) / f"media-{frames}.wav",
            frequency=330,
            frames=frames,
        )
        result = self.application.add_tracks(
            [str(target)],
            roles=["target"],
        )["results"][0]
        self.assertTrue(result["ok"])
        return target, str(result["track"]["track_id"])

    def _media_cookie(self) -> str:
        status, headers, _ = self._request(
            "GET",
            f"/?token={self.token}",
        )
        self.assertEqual(status, 200)
        media_cookie = next(
            cookie
            for cookie in headers.get_all("Set-Cookie", [])
            if cookie.startswith("MMT-Media-Session=")
        )
        return media_cookie.split(";", 1)[0]

    def _json_post(
        self,
        path: str,
        payload: dict[str, object],
    ) -> tuple[int, http.client.HTTPMessage, str]:
        return self._request(
            "POST",
            path,
            token=self.token,
            body=json.dumps(payload),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: str | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, http.client.HTTPMessage, str]:
        encoded = body.encode("utf-8") if body is not None else None
        status, response_headers, response_body = self._request_bytes(
            method,
            path,
            token=token,
            body=encoded,
            content_type=content_type,
            headers=headers,
        )
        return status, response_headers, response_body.decode("utf-8")

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: bytes | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, http.client.HTTPMessage, bytes]:
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["X-MMT-Token"] = token
        if body is not None:
            request_headers["Content-Type"] = content_type
            request_headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(
                method,
                path,
                body=body,
                headers=request_headers,
            )
            response = connection.getresponse()
            return (
                response.status,
                response.headers,
                response.read(),
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
