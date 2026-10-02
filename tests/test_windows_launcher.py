"""Focused contract checks for the Windows double-click launch layer."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_LAUNCHER = PROJECT_ROOT / "Launch-Music-Mastering-Tools.cmd"
PRIVATE_LOG_DIRECTORY = PROJECT_ROOT / "private-workspace" / "logs"


@unittest.skipUnless(os.name == "nt", "the supported launcher is Windows-specific")
class WindowsLauncherTests(unittest.TestCase):
    def test_root_launcher_check_only_anchors_to_project_and_runs_doctor(self) -> None:
        before = (
            set(PRIVATE_LOG_DIRECTORY.glob("launcher-*.log"))
            if PRIVATE_LOG_DIRECTORY.exists()
            else set()
        )
        command_processor = os.environ.get("ComSpec", "cmd.exe")

        with tempfile.TemporaryDirectory() as foreign_directory:
            foreign_path = Path(foreign_directory)
            relay = foreign_path / "invoke-launcher.cmd"
            relay.write_text(
                "@echo off\r\n"
                f'call "{ROOT_LAUNCHER}" -CheckOnly -SkipBootstrap -NoPause\r\n'
                "exit /b %ERRORLEVEL%\r\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [command_processor, "/d", "/c", relay.name],
                cwd=foreign_directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn(f"Project root: {PROJECT_ROOT}", output)
        self.assertIn("Strict mastering diagnostics passed.", output)
        self.assertIn(
            "Check-only launch verification completed successfully",
            output,
        )

        after = set(PRIVATE_LOG_DIRECTORY.glob("launcher-*.log"))
        created = after - before
        self.assertEqual(len(created), 1, output)
        transcript = next(iter(created)).read_text(encoding="utf-8")
        self.assertIn("Music Mastering Tools launcher started.", transcript)
        self.assertIn("Strict mastering diagnostics passed.", transcript)

    def test_private_workspace_and_launcher_contract_are_explicit(self) -> None:
        ignore_text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        launcher_text = ROOT_LAUNCHER.read_text(encoding="utf-8")
        script_text = (PROJECT_ROOT / "scripts" / "Launch-Workbench.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("private-workspace/", ignore_text)
        self.assertIn('cd /d "%~dp0"', launcher_text)
        self.assertIn("%*", launcher_text)
        for switch in (
            "$CheckOnly",
            "$SkipBootstrap",
            "$Terminal",
            "$NoBrowser",
            "$NoPause",
        ):
            self.assertIn(switch, script_text)
        self.assertIn('"gui"', script_text)
        self.assertIn('"workbench"', script_text)
        self.assertIn("localhost graphical portal", script_text)
        self.assertIn(
            "private fallback launch URL cannot be retained",
            script_text,
        )
        self.assertLess(
            script_text.index("private fallback launch URL cannot be retained"),
            script_text.index("& $projectPython @portalArguments"),
        )


if __name__ == "__main__":
    unittest.main()
