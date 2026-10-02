"""Regressions for persistent picker locations and authorized Explorer targets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from music_mastering_tools.portal_app import PORTAL_DIALOG_LOCATIONS_FILENAME, PortalApplication


class FolderNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.application = PortalApplication(self.root / "workspace")

    def tearDown(self) -> None:
        self.application.close()
        self.temporary.cleanup()

    def test_first_pick_uses_workspace_and_preferences_for_each_purpose(self) -> None:
        app = self.application
        expected = (
            ("open-audio", "audio", app.layout.root),
            ("choose-folder", "folder", app.layout.outputs),
            ("save-json", "catalog", app.layout.exports),
            ("open-json", "selection", app.layout.exports),
            ("open-json", "job-configuration", app.layout.jobs),
            ("open-json", "run-manifest", app.layout.runs),
        )
        for mode, purpose, directory in expected:
            with self.subTest(purpose=purpose):
                self.assertEqual(app.dialog_initial_directory(mode, purpose), directory)
        delivery = self.root / "other user's delivery folder"
        delivery.mkdir()
        preferences = {**app.get_preferences(), "default_output_directory": str(delivery)}
        app.update_preferences(preferences)
        self.assertEqual(app.dialog_initial_directory("choose-folder", "folder"), delivery)
        self.assertEqual(app.assert_revealable_path(str(delivery)), delivery)
        with self.assertRaises(PermissionError):
            app.assert_revealable_path(str(self.root / "unselected"))

    def test_locations_persist_across_restart_and_stay_scoped_by_purpose(self) -> None:
        app = self.application
        audio_folder = self.root / "Audio ü, with spaces"
        audio_folder.mkdir()
        catalog_folder = self.root / "Catalog exports"
        catalog_folder.mkdir()
        app.remember_dialog_location("open-audio-many", "audio", [str(audio_folder / "track.wav")])
        app.remember_dialog_location("save-json", "catalog", [str(catalog_folder / "catalog.json")])
        reopened = PortalApplication(app.layout.root)
        try:
            self.assertEqual(reopened.dialog_initial_directory("open-audio", "audio"), audio_folder)
            self.assertEqual(
                reopened.dialog_initial_directory("open-json", "catalog"), catalog_folder
            )
            self.assertEqual(
                reopened.dialog_initial_directory("open-json", "selection"), app.layout.exports
            )
            self.assertEqual(reopened.get_preferences(), app.get_preferences())
        finally:
            reopened.close()

    def test_current_context_wins_and_cancellation_does_not_replace_history(self) -> None:
        app = self.application
        remembered = self.root / "delivery"
        remembered.mkdir()
        current = self.root / "current album" / "future masters"
        app.remember_dialog_location("choose-folder", "folder", [str(remembered)])
        app.remember_dialog_location("choose-folder", "folder", [])
        self.assertEqual(app.dialog_initial_directory("choose-folder", "folder"), remembered)
        self.assertEqual(
            app.dialog_initial_directory("choose-folder", "folder", str(current)), current
        )
        for mode, purpose in (("unknown", "folder"), ("choose-folder", "audio")):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                app.dialog_initial_directory(mode, purpose)

    def test_unavailable_or_corrupt_history_falls_back_and_logs_diagnostic(self) -> None:
        app = self.application
        removed = self.root / "old drive" / "music"
        app.remember_dialog_location("open-audio", "audio", [str(removed / "target.wav")])
        with self.assertLogs(app.logger, level="INFO") as logs:
            self.assertEqual(app.dialog_initial_directory("open-audio", "audio"), app.layout.root)
        self.assertIn("remembered location unavailable", "\n".join(logs.output))
        history = app.layout.root / PORTAL_DIALOG_LOCATIONS_FILENAME
        for document in (
            "not json",
            json.dumps({"schema_version": True, "locations": {}}),
            json.dumps({"schema_version": 1, "locations": {"unknown": str(self.root)}}),
            json.dumps({"schema_version": 1, "locations": {"audio": "relative"}}),
            json.dumps(
                {"schema_version": 1, "locations": {"audio": str(self.root / "bad\x00path")}}
            ),
        ):
            with self.subTest(document=document):
                history.write_text(document, encoding="utf-8")
                with self.assertLogs(app.logger, level="WARNING"):
                    self.assertEqual(
                        app.dialog_initial_directory("open-audio", "audio"), app.layout.root
                    )

    def test_optional_history_write_failure_does_not_fail_the_selection(self) -> None:
        app = self.application
        with (
            mock.patch(
                "music_mastering_tools.portal_app._write_text_file",
                side_effect=OSError("disk full"),
            ),
            self.assertLogs(app.logger, level="WARNING") as logs,
        ):
            app.remember_dialog_location("open-audio", "audio", [str(self.root / "target.wav")])
        self.assertIn("disk full", "\n".join(logs.output))

    def test_history_path_cannot_follow_a_link_outside_workspace(self) -> None:
        app = self.application
        outside = self.root / "other.json"
        outside.write_text("{}", encoding="utf-8")
        with (
            mock.patch.object(Path, "resolve", return_value=outside),
            self.assertRaises(PermissionError),
        ):
            app.dialog_initial_directory("open-audio", "audio")


if __name__ == "__main__":
    unittest.main()
