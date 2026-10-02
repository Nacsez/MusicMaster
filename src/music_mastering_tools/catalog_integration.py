"""Application-layer bridge between immutable run manifests and the catalog."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .audio_probe import AudioProbeUnavailable, DefaultAudioProbe
from .catalog import AudioFacts as CatalogAudioFacts
from .catalog import CatalogRole, CatalogSelection, CatalogStore
from .config import JobConfig
from .manifest import (
    ArtifactManifest,
    ManifestStore,
    RunManifest,
)


@dataclass(frozen=True, slots=True)
class CatalogRecoveryResult:
    """Resolved selection and finalized manifest produced by run recovery."""

    manifest: RunManifest
    selection: CatalogSelection
    manifest_path: Path
    selection_path: Path | None
    configuration_path: Path | None


def register_job_selection(
    catalog: CatalogStore,
    job: JobConfig,
    *,
    inspect_audio: bool = True,
) -> CatalogSelection:
    """Catalog every selected source and return a content-bound selection.

    Repeated reference content is coalesced in the portable selection by
    summing its requested weights. The full job configuration and run manifest
    retain every requested occurrence.
    """

    target = catalog.add_track(
        job.target,
        roles=(CatalogRole.TARGET,),
        audio_facts=_audio_facts(job.target) if inspect_audio else None,
    )
    grouped: dict[str, dict[str, object]] = {}
    labels: dict[str, str | None] = {}
    order: list[str] = []
    for reference in job.references:
        track = catalog.add_track(
            reference.path,
            roles=(CatalogRole.REFERENCE,),
            audio_facts=_audio_facts(reference.path) if inspect_audio else None,
        )
        if track.track_id not in grouped:
            order.append(track.track_id)
            labels[track.track_id] = reference.label
            grouped[track.track_id] = {
                "level": [],
                "frequency": [],
            }
        elif labels[track.track_id] is None and reference.label is not None:
            labels[track.track_id] = reference.label
        level_values = grouped[track.track_id]["level"]
        frequency_values = grouped[track.track_id]["frequency"]
        if not isinstance(level_values, list) or not isinstance(frequency_values, list):
            raise AssertionError("catalog weight grouping was corrupted")
        level_values.append(float(reference.level_weight))
        frequency_values.append(float(reference.frequency_weight))

    base = catalog.build_selection(target.track_id, reference_ids=order)
    weighted_references = tuple(
        replace(
            reference,
            track=replace(
                reference.track,
                label=labels[reference.track.track_id],
            ),
            level_weight=math.fsum(_float_values(grouped[reference.track.track_id]["level"])),
            frequency_weight=math.fsum(
                _float_values(grouped[reference.track.track_id]["frequency"])
            ),
        )
        for reference in base.references
    )
    return CatalogSelection(target=base.target, references=weighted_references)


def selection_with_weights(
    selection: CatalogSelection,
    weights: Iterable[tuple[float, float]],
) -> CatalogSelection:
    """Return a selection with explicit independent weights in displayed order."""

    selected_weights = tuple(weights)
    if len(selected_weights) != len(selection.references):
        raise ValueError("one level/frequency weight pair is required per reference")
    references = tuple(
        replace(
            reference,
            level_weight=level,
            frequency_weight=frequency,
        )
        for reference, (level, frequency) in zip(
            selection.references,
            selected_weights,
            strict=True,
        )
    )
    return CatalogSelection(selection.target, references)


def finalize_catalog_run(
    catalog: CatalogStore,
    selection: CatalogSelection,
    manifest_path: str | Path,
    *,
    configuration_path: str | Path | None = None,
    selection_path: str | Path | None = None,
) -> RunManifest:
    """Attach catalog provenance to a manifest and index its complete inventory."""

    destination = Path(manifest_path).resolve()
    manifest = RunManifest.load(destination)
    _append_owned_input(
        manifest,
        configuration_path,
        role="job-config",
        media_type="application/json",
    )
    _append_owned_input(
        manifest,
        selection_path,
        role="catalog-selection",
        media_type="application/json",
    )
    manifest.extensions["catalog"] = {
        "catalog_id": catalog.catalog_id,
        "schema_version": 1,
        "revision_at_index_start": catalog.revision,
        "database": str(catalog.path),
        "selection": selection.to_dict(),
        "selection_id": selection.selection_id,
        "reimportable": True,
    }
    ManifestStore(destination, overwrite=True).save(manifest)
    with catalog.transaction():
        for artifact in manifest.outputs:
            if artifact.media_type is None or not artifact.media_type.startswith("audio/"):
                continue
            catalog.add_track(
                artifact.path,
                roles=(CatalogRole.GENERATED_OUTPUT,),
                audio_facts=_optional_audio_facts(artifact.path),
            )
        catalog.ingest_manifest(destination, selection=selection)
    return manifest


def recover_catalog_run(
    catalog: CatalogStore,
    manifest_path: str | Path,
    *,
    selection_path: str | Path | None = None,
    configuration_path: str | Path | None = None,
) -> CatalogRecoveryResult:
    """Idempotently recover a preserved run into a catalog without rerendering.

    Explicit selection and configuration paths take precedence. Otherwise the
    function discovers owned inputs already listed by the manifest. A selection
    embedded by :func:`finalize_catalog_run` is the final fallback.
    """

    destination = Path(manifest_path).resolve()
    manifest = RunManifest.load(destination)
    resolved_selection = (
        Path(selection_path).resolve()
        if selection_path is not None
        else _manifest_input_path(manifest, "catalog-selection")
    )
    resolved_configuration = (
        Path(configuration_path).resolve()
        if configuration_path is not None
        else _manifest_input_path(manifest, "job-config")
    )
    if resolved_selection is not None:
        selection = CatalogSelection.from_json(resolved_selection.read_text(encoding="utf-8"))
    else:
        selection = _embedded_catalog_selection(manifest)

    with catalog.transaction():
        catalog.import_selection(selection.to_json())
        finalized = finalize_catalog_run(
            catalog,
            selection,
            destination,
            configuration_path=resolved_configuration,
            selection_path=resolved_selection,
        )
    return CatalogRecoveryResult(
        manifest=finalized,
        selection=selection,
        manifest_path=destination,
        selection_path=resolved_selection,
        configuration_path=resolved_configuration,
    )


def _manifest_input_path(manifest: RunManifest, role: str) -> Path | None:
    matches = [Path(item.path).resolve() for item in manifest.inputs if item.role == role]
    if len(matches) > 1:
        raise ValueError(f"manifest contains more than one {role!r} artifact")
    return matches[0] if matches else None


def _embedded_catalog_selection(manifest: RunManifest) -> CatalogSelection:
    catalog_extension = manifest.extensions.get("catalog")
    if not isinstance(catalog_extension, Mapping):
        raise ValueError("manifest has no embedded catalog selection; provide selection_path")
    selection = catalog_extension.get("selection")
    if not isinstance(selection, Mapping):
        raise ValueError("manifest has no embedded catalog selection; provide selection_path")
    return CatalogSelection.from_json(json.dumps(selection, sort_keys=True, allow_nan=False))


def _append_owned_input(
    manifest: RunManifest,
    path: str | Path | None,
    *,
    role: str,
    media_type: str,
) -> None:
    if path is None:
        return
    candidate = Path(path).resolve()
    if any(Path(item.path).resolve() == candidate for item in manifest.inputs):
        return
    manifest.add_input(
        ArtifactManifest.from_file(
            candidate,
            role=role,
            media_type=media_type,
        )
    )


def _audio_facts(path: str | Path) -> CatalogAudioFacts:
    measured = DefaultAudioProbe().probe(path)
    return CatalogAudioFacts(
        sample_rate=measured.sample_rate,
        channels=measured.channels,
        frames=measured.frames,
        duration_seconds=measured.duration_seconds,
        format=measured.format,
        subtype=measured.subtype,
    )


def _optional_audio_facts(path: str | Path) -> CatalogAudioFacts | None:
    try:
        return _audio_facts(path)
    except AudioProbeUnavailable:
        return None


def _float_values(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or not all(isinstance(item, float) for item in value):
        raise TypeError("catalog weight values must be floating-point numbers")
    return tuple(value)


__all__ = [
    "CatalogRecoveryResult",
    "finalize_catalog_run",
    "recover_catalog_run",
    "register_job_selection",
    "selection_with_weights",
]
