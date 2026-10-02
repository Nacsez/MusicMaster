from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import pytest

    pytestmark = pytest.mark.smoke
except ImportError:
    pytestmark = ()

from music_mastering_tools.events import MemoryEventSink
from music_mastering_tools.manifest import RunManifest, RunStatus
from music_mastering_tools.service import MasteringService
from tests.helpers import make_job


class LaunchpadSmokeTests(unittest.TestCase):
    def test_nominal_job_reaches_audited_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = make_job(root, job_id="smoke-job")
            destination = root / "run" / "manifest.json"

            outcome = MasteringService().run(
                job,
                manifest_path=destination,
                event_log_path=root / "run" / "events.jsonl",
                sink=MemoryEventSink(),
                dry_run=True,
            )

            self.assertEqual(outcome.job_id, "smoke-job")
            self.assertEqual(RunManifest.load(destination).status, RunStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
