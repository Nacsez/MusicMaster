"""Exercise publication guards against an isolated repository, never operator data."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.regression
@unittest.skipUnless(os.name == "nt" and shutil.which("git"), "Windows and Git are required")
class PublicationAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="mmt-publication-")
        self.root = Path(self.temporary.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("_Common.ps1", "Invoke-PublicationAudit.ps1"):
            shutil.copyfile(PROJECT_ROOT / "scripts" / name, scripts / name)
        shutil.copyfile(PROJECT_ROOT / ".gitignore", self.root / ".gitignore")
        (self.root / "README.md").write_text("Public source candidate.\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
            timeout=20,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_audit(self) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.root / "scripts" / "Invoke-PublicationAudit.ps1"),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        reports = sorted((self.root / "artifacts" / "release-audit").glob("*.json"))
        self.assertEqual(len(reports), 1, completed.stdout + completed.stderr)
        return completed, json.loads(reports[0].read_text(encoding="utf-8"))

    def test_safe_source_excludes_ignored_operator_workspace(self) -> None:
        private = self.root / "private-workspace"
        private.mkdir()
        song = private / "operator-song.wav"
        song.write_bytes(b"private sentinel")

        completed, report = self.run_audit()

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(report["finding_count"], 0)
        self.assertEqual(report["candidate_count"], 4)
        self.assertEqual(song.read_bytes(), b"private sentinel")

    def test_private_candidates_and_forced_tracked_secret_fail(self) -> None:
        configs = self.root / "configs"
        configs.mkdir()
        samples = {
            self.root / "unexpected.txt": "outside allowlist\n",
            configs / "private-audio.wav": "private media\n",
            configs / "personal.txt": "C:" + "\\Users\\" + "AuditProbe\\song.wav\n",
            configs / "key.txt": "-" * 5 + "BEGIN " + "PRIVATE KEY" + "-" * 5,
            self.root / ".env": "PASSWORD=harmless-placeholder\n",
        }
        for path, contents in samples.items():
            path.write_text(contents, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "--force", ".env"],
            check=True,
            capture_output=True,
            timeout=20,
        )

        completed, report = self.run_audit()

        self.assertNotEqual(completed.returncode, 0)
        findings = report["findings"]
        self.assertIsInstance(findings, list)
        rules = {finding["rule"] for finding in findings}
        self.assertEqual(
            rules,
            {
                "outside-source-allowlist",
                "private-media-or-generated-binary",
                "personal-windows-path",
                "credential-pattern",
                "private-runtime-or-configuration",
            },
        )
        self.assertTrue(any(finding["path"] == ".env" for finding in findings))
        self.assertTrue(all(path.exists() for path in samples))
