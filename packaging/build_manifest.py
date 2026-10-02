"""Tie a Windows executable to its exact authored application/build inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def audit_archive(executable: Path) -> int:
    names = [name.replace("\\", "/") for name in CArchiveReader(str(executable)).toc]
    forbidden = [
        name
        for name in names
        if name.endswith("direct_url.json")
        or name.startswith(("private-workspace/", "artifacts/"))
        or (name.startswith("THIRD-PARTY-NOTICES/") and name.endswith((".py", ".pyc", ".pyo")))
    ]
    if forbidden:
        raise RuntimeError(f"Private/generated metadata entered the executable: {forbidden}")
    return len(names)


def write_manifest(executable: Path, destination: Path) -> None:
    archive_members = audit_archive(executable)
    root = Path(__file__).resolve().parents[1]
    inputs: list[dict[str, str]] = []
    paths = [root / name for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE")]
    for directory in ("src", "packaging", "requirements", "scripts", "schemas", "configs"):
        paths.extend(
            path
            for path in (root / directory).rglob("*")
            if path.is_file()
            and path.suffix not in {".pyc", ".pyo"}
            and "__pycache__" not in path.parts
            and ".egg-info" not in str(path)
        )
    for path in sorted(paths):
        inputs.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    revision = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "git_revision": revision.stdout.strip() if revision.returncode == 0 else None,
                "python": platform.python_version(),
                "architecture": platform.machine(),
                "executable": executable.name,
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "audited_archive_members": archive_members,
                "authored_build_inputs": inputs,
                "dependency_inventory": "dependency-inventory.json",
                "source_note": (
                    "Input hashes identify this build even before the initial Git commit. "
                    "Git revision alone does not assert a clean working tree. "
                    "Dependency source collection remains separate."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Recorded {len(inputs)} authored build-input hashes and executable identity.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    write_manifest(args.executable.resolve(), args.destination.resolve())
