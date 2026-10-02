"""Exercise the relocated frozen EXE using isolated per-user data and real DSP.

Only the test driver uses Python. The child has no Python, venv, repository or
FFmpeg on PATH. All readiness tokens stay in ignored test artifacts and are
checked for absence from program logs. Failures preserve every diagnostic file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import time
import wave
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def tone(path: Path, rate: int, frequency: float) -> None:
    """Synthetic redistributable stereo program material with transient variation."""
    payload = bytearray()
    for frame in range(rate * 4):
        t = frame / rate
        envelope = 0.55 + 0.35 * math.sin(2 * math.pi * 1.7 * t) ** 2
        values = [
            int(
                32767
                * envelope
                * (
                    0.23 * math.sin(2 * math.pi * frequency * (1 + channel * 0.1) * t)
                    + 0.08 * math.sin(2 * math.pi * frequency * 3.2 * t)
                    + 0.04 * math.sin(2 * math.pi * 55 * t)
                )
            )
            for channel in range(2)
        ]
        payload.extend(struct.pack("<hh", *values))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload)


def run(executable: Path, artifacts: Path) -> None:
    run_directory = artifacts / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_directory.mkdir(parents=True)
    distribution = run_directory / "Relocated application ü, spaces"
    distribution.mkdir()
    copied = distribution / "MusicMasteringTools.exe"
    shutil.copyfile(executable, copied)
    unrelated = run_directory / "unrelated working directory"
    unrelated.mkdir()
    app_data = run_directory / "Isolated user ü, data"
    workspace = app_data / "MusicMasteringTools" / "workspace"
    session = workspace / ".desktop-session.json"
    environment = {key.upper(): value for key, value in os.environ.items()}
    environment["LOCALAPPDATA"] = str(app_data)
    environment["PATH"] = os.pathsep.join(
        [
            str(Path(environment["SYSTEMROOT"]) / "System32"),
            environment["SYSTEMROOT"],
        ]
    )
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    checks: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    origin = ""
    token = ""
    session_tokens: list[str] = []
    opener = build_opener(ProxyHandler({}))

    def stop_tree(child: subprocess.Popen[bytes]) -> None:
        """Stop only this harness's launcher and its frozen runtime descendants."""
        if child.poll() is not None:
            return
        stopped = subprocess.run(
            [
                str(Path(environment["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                "/PID",
                str(child.pid),
                "/T",
                "/F",
            ],
            capture_output=True,
            check=False,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        (run_directory / f"forced-cleanup-{child.pid}.txt").write_bytes(
            stopped.stdout + stopped.stderr
        )
        child.wait(timeout=15)

    def launch(arguments: Sequence[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(copied), *arguments],
            cwd=unrelated,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def run_command(arguments: Sequence[str]) -> int:
        child = launch(arguments)
        try:
            return child.wait(timeout=120)
        except subprocess.TimeoutExpired:
            stop_tree(child)
            raise

    def api(route: str, payload: dict[str, Any] | None = None) -> Any:
        request = Request(
            origin + route,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"X-MMT-Token": token, "Content-Type": "application/json", "Origin": origin},
        )
        try:
            with opener.open(request, timeout=30) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise AssertionError(f"{route}: {exc.code} {exc.read().decode('utf-8')}") from exc
        if not result["ok"]:
            raise AssertionError(result)
        return result["data"]

    def start() -> subprocess.Popen[bytes]:
        child = launch(["--no-browser"])
        deadline = time.monotonic() + 120
        while not session.exists():
            if child.poll() is not None:
                raise AssertionError(f"EXE exited before readiness, code {child.returncode}")
            if time.monotonic() >= deadline:
                stop_tree(child)
                raise AssertionError("EXE startup exceeded 120 seconds")
            time.sleep(0.1)
        return child

    def read_session() -> None:
        nonlocal origin, token
        ready = json.loads(session.read_text(encoding="utf-8"))
        origin = ready["origin"]
        token = parse_qs(urlsplit(ready["url"]).query)["token"][0]
        session_tokens.append(token)

    try:
        diagnostics = run_directory / "frozen-diagnostics.json"
        checked = run_command(
            ["--check-only", "--no-browser", "--diagnostics-output", str(diagnostics)]
        )
        assert checked == 0, f"Frozen dependency check failed: {checked}"
        assert json.loads(diagnostics.read_text(encoding="utf-8"))["healthy"]
        checks.append("All bundled audio dependency imports pass without Python or FFmpeg on PATH")
        process = start()
        read_session()
        bootstrap = api("/api/bootstrap")
        assert Path(bootstrap["workspace"]["root"]) == workspace
        assert bootstrap["catalog"]["track_count"] == 0
        catalog_id = bootstrap["catalog"]["catalog_id"]
        checks.append(
            "Relocated EXE and unrelated cwd create a fresh library under current LOCALAPPDATA"
        )
        with opener.open(origin + "/assets/app.css", timeout=10) as response:
            assert b"#76ff53" in response.read().lower()
        with opener.open(origin + "/?token=" + token, timeout=10) as response:
            assert b"Music Mastering Tools" in response.read()
        checks.append("Packaged CADER assets and authenticated page load")
        try:
            opener.open(origin + "/api/bootstrap", timeout=10)
        except HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("Unauthenticated API request accepted")
        checks.append("Loopback API rejects unauthenticated access")
        duplicate = run_command(["--no-browser"])
        assert duplicate == 0
        checks.append(
            "Second EXE activates existing library instead of starting a competing server"
        )
        source = run_directory / "Original mix ü, 44k.wav"
        reference = run_directory / "Reference 48k.wav"
        other = run_directory / "Reference 32k.wav"
        for path, rate, frequency in (
            (source, 44100, 330),
            (reference, 48000, 660),
            (other, 32000, 880),
        ):
            tone(path, rate, frequency)
        fingerprints = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (source, reference, other)
        }
        tracks = api(
            "/api/tracks/add",
            {"paths": [str(source), str(reference), str(other)], "roles": ["target", "reference"]},
        )
        # Track registration responses contain per-item data; query portable catalog IDs.
        del tracks
        all_tracks = api("/api/tracks")
        by_path = {
            location["path"]: item["track_id"]
            for item in all_tracks
            for location in item["locations"]
        }
        target_id = by_path[str(source)]
        reference_ids = [by_path[str(reference)], by_path[str(other)]]
        destination = run_directory / "Deliveries ü, custom path"
        destination.mkdir()
        preferences = api("/api/preferences")
        preferences["default_output_directory"] = str(destination)
        api("/api/preferences", preferences)
        for ids in ([reference_ids[0]], reference_ids):
            request = {
                "target_ids": [target_id],
                "references": [
                    {"track_id": identifier, "level_weight": 1.0, "frequency_weight": 1.0}
                    for identifier in ids
                ],
                "outputs": {
                    "limited": True,
                    "normalized": False,
                    "raw": False,
                    "limited_subtype": "PCM_24",
                },
            }
            validated = api("/api/jobs/validate", request)
            assert validated["report"]["ok"], validated
            operation = api("/api/jobs/render", request)
            deadline = time.monotonic() + 240
            while True:
                operation = api("/api/tasks/" + operation["operation_id"])
                if operation["state"] in {"succeeded", "failed"}:
                    break
                assert time.monotonic() < deadline, "Frozen DSP exceeded 240 seconds"
                time.sleep(0.25)
            assert operation["state"] == "succeeded", operation
            checks.append(
                f"Actual {'upstream' if len(ids) == 1 else 'weighted native'} render "
                "including mixed-rate resampling"
            )
        masters = list(destination.rglob("*.wav"))
        assert len(masters) == 2, masters
        for master in masters:
            with wave.open(str(master), "rb") as audio:
                assert audio.getsampwidth() == 3 and audio.getnchannels() == 2
                assert audio.getnframes() > 0
        assert fingerprints == {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in fingerprints
        }
        checks.append(
            "Two PCM24 stereo deliveries go to exact custom folder; all source bytes preserved"
        )
        api("/api/shutdown", {})
        assert process.wait(timeout=30) == 0
        assert not session.exists()
        process = start()
        read_session()
        bootstrap = api("/api/bootstrap")
        assert bootstrap["catalog"]["catalog_id"] == catalog_id
        assert api("/api/preferences")["default_output_directory"] == str(destination)
        assert len(api("/api/runs")) == 2
        checks.append("Restart retains catalog identity, delivery preference and both audited runs")
        for log in (workspace / "logs").glob("*.log"):
            contents = log.read_text(encoding="utf-8")
            assert all(secret not in contents for secret in session_tokens), (
                "Session token leaked into log"
            )
        checks.append("Program logs redact session tokens")
        api("/api/shutdown", {})
        assert process.wait(timeout=30) == 0
        process = None
        assert list(distribution.iterdir()) == [copied]
        assert not list(unrelated.iterdir())
        checks.append("No catalogs, logs or output are written beside the EXE or in cwd")
        (run_directory / "verification-summary.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "checks": checks,
                    "executable_sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Executable smoke passed: {len(checks)} checks. Evidence: {run_directory}")
    except Exception as exc:
        (run_directory / "verification-summary.json").write_text(
            json.dumps(
                {
                    "passed": False,
                    "checks": checks,
                    "error": str(exc),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Executable smoke failed; evidence: {run_directory}")
        raise
    finally:
        if process is not None and process.poll() is None:
            try:
                api("/api/shutdown", {})
                process.wait(timeout=15)
            except Exception:
                stop_tree(process)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    run(args.executable.resolve(), args.artifacts.resolve())
