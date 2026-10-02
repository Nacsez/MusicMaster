"""Collect installed runtime license texts and a path-free distribution inventory.

This records the exact dependency wheels used by the build. It does not claim
that license texts replace corresponding source when publishing a GPL binary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pyexpat
import shutil
import sqlite3
import ssl
import sys
import zlib
from importlib import metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def collect_native_python_support(notices: Path) -> dict[str, object]:
    """Retain exact native notices beyond those in CPython's LICENSE.txt."""

    assets = PROJECT_ROOT / "packaging" / "third_party"
    provenance = json.loads((assets / "provenance.json").read_text(encoding="utf-8"))
    if sys.version.split()[0] != provenance["python_version"]:
        raise RuntimeError(
            "Native notice baseline requires CPython 3.11.7; refresh provenance first"
        )
    observed = {
        "openssl": ssl.OPENSSL_VERSION,
        "sqlite": sqlite3.sqlite_version,
        "zlib": zlib.ZLIB_RUNTIME_VERSION,
        "expat": pyexpat.EXPAT_VERSION,
    }
    if observed != provenance["runtime_observations"]:
        raise RuntimeError("Native Python library versions differ from retained notice provenance")
    for entry in provenance["retained_upstream_texts"]:
        asset = assets / entry["path"]
        if hashlib.sha256(asset.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"Retained upstream license changed: {entry['path']}")
    target = notices / "native-python-support"
    shutil.copytree(assets, target, dirs_exist_ok=True)
    components = []
    for entry in provenance["components"]:
        component = dict(entry)
        component["license_files"] = (
            ["THIRD-PARTY-NOTICES/python/LICENSE.txt"]
            if entry["name"] in {"bzip2", "libffi"}
            else [
                f"THIRD-PARTY-NOTICES/native-python-support/{path}"
                for path in entry["notice_files"]
            ]
        )
        components.append(component)
    return {
        "python_version": provenance["python_version"],
        "runtime_observations": observed,
        "components": components,
        "provenance_file": "THIRD-PARTY-NOTICES/native-python-support/provenance.json",
    }


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    notices = destination / "THIRD-PARTY-NOTICES"
    notices.mkdir(exist_ok=True)
    requirements = PROJECT_ROOT / "requirements" / "windows-release.txt"
    inventory: list[dict[str, object]] = []
    runtime_lines = requirements.read_text(encoding="utf-8").splitlines()
    build_lines = (
        (PROJECT_ROOT / "requirements" / "windows-build.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    for line in runtime_lines + build_lines:
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        if any(item["name"] == name for item in inventory):
            continue
        distribution = metadata.distribution(name)
        if distribution.version != expected:
            raise RuntimeError(
                f"Release dependency mismatch: {name} {distribution.version} != {expected}"
            )
        copied = []
        native_files = []
        for entry in distribution.files or ():
            path = Path(str(entry))
            # All inventory paths must be portable and remain under their package.
            if path.is_absolute() or ".." in path.parts:
                continue
            source = Path(str(distribution.locate_file(entry)))
            if not source.is_file():
                continue
            is_license = any(
                marker in path.name.casefold()
                for marker in ("license", "licence", "copying", "notice", "copyright")
            ) or "licenses" in [part.casefold() for part in path.parts]
            if is_license and path.suffix.casefold() not in {
                ".py",
                ".pyc",
                ".pyo",
                ".dll",
                ".pyd",
                ".exe",
            }:
                target = notices / name / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                copied.append(target.relative_to(destination).as_posix())
            elif is_license:
                # A Python module named packaging.licenses is code, not a notice.
                # Remove only this known generated copy from an older collection;
                # compiled code may retain private checkout filenames.
                prior_generated_copy = notices / name / path
                if prior_generated_copy.is_file():
                    prior_generated_copy.unlink()
            if path.suffix.casefold() in {".dll", ".pyd"}:
                native_files.append(
                    {
                        "path": path.as_posix(),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                )
        if not copied:
            raise RuntimeError(f"No license text found for runtime dependency: {name}")
        info = distribution.metadata
        inventory.append(
            {
                "name": name,
                "version": distribution.version,
                "purpose": "runtime" if line in runtime_lines else "build or bundled support",
                "license": next(
                    iter(info.get_all("License-Expression") or info.get_all("License") or []), None
                ),
                "homepage": next(iter(info.get_all("Home-page") or []), None),
                "project_urls": info.get_all("Project-URL") or [],
                "license_files": copied,
                "native_files": native_files,
                "requires_dist": info.get_all("Requires-Dist") or [],
            }
        )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Python license is unavailable: {python_license}")
    python_target = notices / "python" / "LICENSE.txt"
    python_target.parent.mkdir(exist_ok=True)
    shutil.copyfile(python_license, python_target)
    native_support = collect_native_python_support(notices)
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(PROJECT_ROOT / name, destination / name)
    (destination / "dependency-inventory.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "application": {
                    "name": "Music Mastering Tools",
                    "version": metadata.version("music-mastering-tools"),
                },
                "python": {
                    "version": sys.version.split()[0],
                    "license_file": "THIRD-PARTY-NOTICES/python/LICENSE.txt",
                },
                "dependencies": inventory,
                "native_python_support": native_support,
                "build_tools": {
                    name: metadata.version(name)
                    for name in ("pyinstaller", "pyinstaller-hooks-contrib")
                },
                "source_collection": (
                    "Corresponding dependency source must accompany any public binary release; "
                    "this inventory records installed wheels and notices only."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Collected notices and inventory for {len(inventory)} runtime/build distributions.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    collect(parser.parse_args().destination.resolve())
