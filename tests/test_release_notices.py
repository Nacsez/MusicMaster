"""Keep generated license bundles free of compiled private checkout filenames."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.regression
class ReleaseNoticesTests(unittest.TestCase):
    def test_license_directory_code_is_excluded_and_old_generated_copies_removed(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "release_notice_collector", PROJECT_ROOT / "packaging" / "collect_notices.py"
        )
        assert spec is not None and spec.loader is not None
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            requirements = root / "requirements"
            requirements.mkdir()
            (requirements / "windows-release.txt").write_text("example==1.0\n", encoding="utf-8")
            (requirements / "windows-build.txt").write_text("", encoding="utf-8")
            for name in ("LICENSE", "NOTICE", "LICENSE.txt"):
                (root / name).write_text("License sentinel.\n", encoding="utf-8")
            installed = root / "installed"
            relative_license = Path("example-1.0.dist-info/licenses/LICENSE.txt")
            relative_code = Path("example/licenses/__init__.py")
            relative_cache = Path("example/licenses/__pycache__/__init__.cpython-311.pyc")
            private_marker = b"C:" + b"\\Users\\" + b"AuditProbe\\private-checkout"
            for relative, payload in (
                (relative_license, b"Actual dependency notice.\n"),
                (relative_code, b"# License module implementation\n"),
                (relative_cache, private_marker),
            ):
                source = installed / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(payload)
            output = root / "output"
            old_copy = output / "THIRD-PARTY-NOTICES/example" / relative_cache
            old_copy.parent.mkdir(parents=True)
            old_copy.write_bytes(private_marker)
            info = Message()
            info["License"] = "MIT"
            distribution = SimpleNamespace(
                version="1.0",
                files=[relative_license, relative_code, relative_cache],
                locate_file=lambda entry: installed / entry,
                metadata=info,
            )
            with (
                mock.patch.object(collector, "PROJECT_ROOT", root),
                mock.patch.object(collector.sys, "base_prefix", str(root)),
                mock.patch.object(collector.metadata, "distribution", return_value=distribution),
                mock.patch.object(collector.metadata, "version", return_value="1.0"),
                mock.patch.object(collector, "collect_native_python_support", return_value={}),
            ):
                collector.collect(output)

            self.assertFalse(old_copy.exists())
            self.assertFalse((output / "THIRD-PARTY-NOTICES/example" / relative_code).exists())
            self.assertTrue((installed / relative_cache).exists())
            inventory = json.loads(
                (output / "dependency-inventory.json").read_text(encoding="utf-8")
            )
            notices = inventory["dependencies"][0]["license_files"]
            self.assertEqual(len(notices), 1)
            self.assertEqual((output / notices[0]).read_text(), "Actual dependency notice.\n")
            self.assertNotIn(private_marker.decode(), json.dumps(inventory))
