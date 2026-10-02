"""Build an isolated, populated portal workspace for the UI smoke audit.

This helper intentionally refuses paths outside artifacts/ux-populated-smoke so
the developer's normal private workspace cannot be touched by the smoke run.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

# Running a helper by path puts this artifact directory (rather than the repo
# root) on sys.path. Add the root explicitly so the existing test WAV helper is
# reused instead of carrying another audio-fixture implementation here.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.helpers import write_tone  # noqa: E402

from music_mastering_tools.catalog import CatalogStore  # noqa: E402
from music_mastering_tools.catalog_integration import finalize_catalog_run  # noqa: E402
from music_mastering_tools.manifest import ArtifactManifest, RunManifest  # noqa: E402
from music_mastering_tools.portal_app import PortalApplication  # noqa: E402


def job_request(target_id: str, references: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "target_id": target_id,
        "references": [
            {
                "track_id": track_id,
                "level_weight": level_weight,
                "frequency_weight": frequency_weight,
            }
            for track_id, level_weight, frequency_weight in references
        ],
        "outputs": {
            "limited": True,
            "normalized": True,
            "raw": True,
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
        "notes": "Populated isolated browser smoke fixture",
    }


def add_track(app: PortalApplication, path: Path, role: str, label: str) -> str:
    result = app.add_tracks(
        [str(path)],
        roles=[role],
        labels={str(path): label},
    )
    item = result["results"][0]
    if not item["ok"]:
        raise RuntimeError(cast(Mapping[str, Any], item["error"])["message"])
    return cast(str, cast(Mapping[str, Any], item["track"])["track_id"])


def complete_version(
    app: PortalApplication,
    request: Mapping[str, Any],
    *,
    base_frequency: float,
) -> str:
    prepared = app.prepare_job(request, purpose="render")
    run_id = prepared.job.execution.job_id
    if run_id is None:
        raise RuntimeError("prepared browser fixture has no materialized run ID")
    manifest = RunManifest.create(run_id=run_id)
    manifest.add_input(
        ArtifactManifest.from_file(
            prepared.job.target,
            role="target",
            media_type="audio/wav",
        )
    )
    for index, reference in enumerate(prepared.job.references):
        manifest.add_input(
            ArtifactManifest.from_file(
                reference.path,
                role=f"reference[{index}]",
                media_type="audio/wav",
            )
        )
    for index, output in enumerate(prepared.job.outputs):
        output_path = write_tone(
            Path(output.path),
            frequency=base_frequency + (index * 37.0),
            frames=44_100,
        )
        manifest.add_output(
            ArtifactManifest.from_file(
                output_path,
                role="mastered-output",
                media_type="audio/wav",
                metadata={
                    "mode": output.mode.value,
                    "label": output.label,
                    "requested_subtype": output.subtype,
                },
            )
        )
    manifest.mark_completed()
    manifest.save(prepared.manifest_path)
    with CatalogStore(app.layout.catalog_database) as catalog:
        finalize_catalog_run(
            catalog,
            prepared.selection,
            prepared.manifest_path,
            configuration_path=prepared.configuration_path,
            selection_path=prepared.selection_path,
        )
    return run_id


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: populate_portal_smoke.py RUN_DIRECTORY")

    project_root = Path.cwd().resolve()
    allowed_root = (project_root / "artifacts" / "ux-populated-smoke").resolve()
    run_root = Path(sys.argv[1]).resolve()
    if not run_root.is_relative_to(allowed_root) or run_root == allowed_root:
        raise SystemExit(f"refusing run directory outside {allowed_root}")
    run_root.mkdir(parents=True, exist_ok=False)
    fixtures = run_root / "fixtures"
    workspace = run_root / "workspace"
    fixtures.mkdir()

    app = PortalApplication(workspace)
    try:
        target_specs = (
            ("Neon Current — Mix 01", 220.0),
            ("Glass Skyline — Mix 07", 277.18),
            ("Afterimage — Vocal Up", 329.63),
        )
        reference_specs = (
            ("Reference — Warm & Wide", 523.25),
            ("Reference — Tight Low End", 659.25),
            ("Reference — Open Top", 783.99),
        )
        target_ids = [
            add_track(
                app,
                write_tone(
                    fixtures / f"target-{index + 1}.wav", frequency=frequency, frames=44_100
                ),
                "target",
                label,
            )
            for index, (label, frequency) in enumerate(target_specs)
        ]
        reference_ids = [
            add_track(
                app,
                write_tone(
                    fixtures / f"reference-{index + 1}.wav",
                    frequency=frequency,
                    frames=44_100,
                ),
                "reference",
                label,
            )
            for index, (label, frequency) in enumerate(reference_specs)
        ]

        version_ids = [
            complete_version(
                app,
                job_request(target_ids[0], [(reference_ids[0], 1.0, 1.0)]),
                base_frequency=410.0,
            ),
            complete_version(
                app,
                job_request(
                    target_ids[0],
                    [
                        (reference_ids[1], 0.65, 0.35),
                        (reference_ids[2], 0.35, 0.65),
                    ],
                ),
                base_frequency=485.0,
            ),
            complete_version(
                app,
                job_request(target_ids[1], [(reference_ids[2], 1.0, 1.0)]),
                base_frequency=575.0,
            ),
        ]
        app.create_reference_set("Release references")
        library = app.master_library()
        tracks = app.list_tracks(include_archived=True)
        report = {
            "workspace": str(workspace),
            "target_ids": target_ids,
            "reference_ids": reference_ids,
            "version_ids": version_ids,
            "track_count": len(tracks),
            "library_summary": library["summary"],
            "source_versions": {
                source["source"]["label"]: len(source["versions"]) for source in library["sources"]
            },
        }
        (run_root / "fixture-report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
