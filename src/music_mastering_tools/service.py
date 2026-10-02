"""Audited orchestration around validation, engines, events, and manifests."""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from .audio_probe import AudioProbeUnavailable, DefaultAudioProbe
from .config import JobConfig
from .engine import EngineRunResult, create_engine
from .engine.base import EngineLogRecord, requested_artifact_paths
from .errors import (
    EventSinkError,
    MusicMasteringError,
    OutputCollisionError,
    PreflightError,
    ProcessingError,
)
from .events import (
    CompositeEventSink,
    ConsoleEventSink,
    Event,
    EventLevel,
    EventSink,
    JobEventEmitter,
    JsonlEventSink,
)
from .manifest import (
    ArtifactManifest,
    FileFingerprint,
    ManifestStore,
    MetricManifest,
    RunManifest,
    fingerprint_file,
)
from .validation import ValidationReport, validate_job


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Completed orchestration result returned to Python and CLI callers."""

    job_id: str
    dry_run: bool
    manifest_path: str
    event_log_path: str | None
    validation: ValidationReport
    engine_result: EngineRunResult | None


class MasteringService:
    """Execute one job with durable diagnostics and no hidden downgrades."""

    def run(
        self,
        job: JobConfig,
        *,
        manifest_path: str | Path,
        event_log_path: str | Path | None = None,
        configuration_path: str | Path | None = None,
        sink: EventSink | None = None,
        dry_run: bool | None = None,
        command: Sequence[str] = (),
        inspect_audio: bool = True,
    ) -> RunOutcome:
        job_id = job.execution.job_id or uuid.uuid4().hex
        effective_dry_run = job.execution.dry_run if dry_run is None else dry_run
        manifest_destination = Path(manifest_path).resolve()
        event_destination = Path(event_log_path).resolve() if event_log_path is not None else None
        _validate_audit_destinations(
            job,
            manifest_destination,
            event_destination,
            (Path(configuration_path).resolve() if configuration_path is not None else None),
        )

        primary_sink = sink or ConsoleEventSink()
        optional_sinks = CompositeEventSink(primary_sink)
        sinks: list[EventSink] = [optional_sinks]
        jsonl_sink: JsonlEventSink | None = None
        if event_destination is not None:
            jsonl_sink = JsonlEventSink(
                event_destination,
                append=False,
                exclusive=True,
                sync_on_close=True,
            )
            sinks.append(jsonl_sink)
        # A caller-supplied console/UI sink is diagnostic and may degrade
        # without aborting audio. An explicitly requested owned JSONL log is a
        # required audit artifact and therefore fails the job if it cannot
        # accept an event.
        composite = CompositeEventSink(
            *sinks,
            strict=event_destination is not None,
        )
        emitter = JobEventEmitter(job_id, composite)
        store = ManifestStore(manifest_destination, overwrite=False)
        manifest = RunManifest.create(
            run_id=job_id,
            tool_version=_tool_version(),
            command=command,
            configuration=job.to_dict(),
            environment={
                "dry_run": effective_dry_run,
                "audio_probe_enabled": inspect_audio,
            },
        )
        manifest.dependencies.update(_dependency_versions())
        manifest.notes = job.notes
        manifest.extensions["engine_capabilities"] = create_engine(
            job.execution.engine
        ).capabilities.to_dict()
        if event_destination is not None:
            manifest.extensions["event_log_path"] = str(event_destination)

        validation = ValidationReport(())
        engine_result: EngineRunResult | None = None
        failure: MusicMasteringError | None = None
        render_started = False
        preexisting_outputs: dict[Path, FileFingerprint] = {}
        store.save(manifest)
        try:
            emitter.info(
                "MMT-I-JOB-CREATED",
                "Mastering job created.",
                stage="lifecycle",
                dry_run=effective_dry_run,
                manifest_path=str(manifest_destination),
            )
            if not inspect_audio and not effective_dry_run:
                raise PreflightError(
                    "Audio probing cannot be disabled for a real render.",
                    details={
                        "inspect_audio": False,
                        "dry_run": False,
                        "unsafe_bypass_prevented": True,
                    },
                )
            if not inspect_audio:
                dry_run_warning = (
                    "Decoded audio measurements were explicitly skipped for this dry run."
                )
                manifest.add_warning(dry_run_warning)
                emitter.warning(
                    "MMT-W-AUDIO-PROBE-SKIPPED",
                    dry_run_warning,
                    stage="preflight",
                )
            emitter.info(
                "MMT-I-PREFLIGHT-STARTED",
                "Preflight validation started.",
                stage="preflight",
            )
            validation = validate_job(job, inspect_audio=inspect_audio)
            manifest.extensions["validation"] = validation.to_dict()
            for validation_warning in validation.warnings:
                manifest.add_warning(f"{validation_warning.code}: {validation_warning.message}")

            if not validation.ok:
                raise PreflightError(
                    f"Preflight found {len(validation.errors)} blocking issue(s).",
                    details={"validation": validation.to_dict()},
                )

            emitter.info(
                "MMT-I-PREFLIGHT-PASSED",
                "Preflight validation passed.",
                stage="preflight",
                warning_count=len(validation.warnings),
            )
            _add_input_artifacts(job, manifest, emitter)
            _reject_duplicate_input_content(job, manifest)
            _create_output_directories(job)
            manifest.mark_running()
            store.save(manifest)

            if effective_dry_run:
                manifest.extensions["dry_run"] = {
                    "performed": True,
                    "rendered_audio": False,
                }
                emitter.info(
                    "MMT-I-DRY-RUN-COMPLETED",
                    "Dry run completed without rendering audio.",
                    stage="lifecycle",
                )
            else:
                engine = create_engine(job.execution.engine)
                preexisting_outputs = _snapshot_existing_outputs(job)
                emitter.info(
                    "MMT-I-ENGINE-STARTED",
                    "Audio rendering started.",
                    stage="engine",
                    engine=engine.capabilities.engine_id,
                    engine_version=engine.capabilities.engine_version,
                )
                render_started = True
                engine_result = engine.run(
                    job,
                    log_handler=lambda record: _forward_engine_log(emitter, record),
                )
                _verify_inputs_unchanged(manifest)
                observed_outputs = _probe_rendered_audio(job)
                manifest.extensions["post_render_audio"] = list(observed_outputs.values())
                manifest.extensions["engine_result"] = engine_result.to_dict()
                manifest.add_metric(
                    MetricManifest(
                        "render_duration",
                        engine_result.duration_seconds,
                        unit="seconds",
                        stage="engine",
                    )
                )
                _add_output_artifacts(job, manifest, observed_outputs)
                emitter.info(
                    "MMT-I-ENGINE-COMPLETED",
                    "Audio rendering completed.",
                    stage="engine",
                    output_count=len(engine_result.output_paths),
                    duration_seconds=engine_result.duration_seconds,
                )

            emitter.info(
                "MMT-I-JOB-COMMITTING",
                "Processing completed; committing the authoritative run manifest.",
                stage="lifecycle",
                dry_run=effective_dry_run,
            )
        except MusicMasteringError as exc:
            failure = exc
        except Exception as exc:
            failure = ProcessingError(
                f"Unexpected orchestration failure: {exc}",
                details={"exception_type": type(exc).__name__},
            )

        if failure is None:
            try:
                emitter.info(
                    "MMT-I-JOB-COMPLETED",
                    "Mastering job processing completed successfully.",
                    stage="lifecycle",
                    dry_run=effective_dry_run,
                )
            except MusicMasteringError as exc:
                failure = exc

        if failure is not None:
            try:
                emitter.error(
                    "MMT-E-JOB-FAILED",
                    failure.message,
                    stage="lifecycle",
                    error=failure.to_dict(),
                )
            except MusicMasteringError as event_failure:
                manifest.add_warning(f"Could not write the terminal failure event: {event_failure}")
            finally:
                if render_started:
                    _record_partial_outputs(
                        job,
                        manifest,
                        preexisting_outputs,
                    )
                manifest.mark_failed(failure)

        # No more events are emitted after this point, so the event log can be
        # fingerprinted without immediately invalidating its digest.
        if jsonl_sink is not None:
            try:
                jsonl_sink.close()
            except Exception as exc:
                close_failure = EventSinkError(
                    f"Could not durably close the required event log: {exc}",
                    details={
                        "path": str(event_destination),
                        "exception_type": type(exc).__name__,
                    },
                )
                manifest.extensions.setdefault(
                    "audit_finalization_failures",
                    [],
                ).append(close_failure.to_dict())
                if failure is None:
                    failure = close_failure
                    manifest.mark_failed(close_failure)
                else:
                    manifest.add_warning(str(close_failure))
                _append_audit_failure_event(
                    emitter,
                    primary_sink,
                    jsonl_sink,
                    close_failure,
                    code="MMT-E-AUDIT-LOG-FINALIZE-FAILED",
                    message="Could not durably finalize the required event log.",
                )
        sink_failures = (*optional_sinks.failures, *composite.failures)
        if sink_failures:
            failures = [item.to_dict() for item in sink_failures]
            manifest.extensions["event_sink_failures"] = failures
            manifest.add_warning(
                f"{len(failures)} event sink operation(s) failed; see event_sink_failures."
            )
        if event_destination is not None and event_destination.is_file():
            try:
                _refresh_event_log_artifact(manifest, event_destination)
            except MusicMasteringError as exc:
                if failure is None:
                    failure = exc
                    manifest.mark_failed(exc)
                else:
                    manifest.add_warning(f"Event log fingerprint failed: {exc}")

        if failure is None:
            manifest.mark_completed()
        try:
            store.save(manifest)
        except MusicMasteringError as commit_failure:
            _append_audit_failure_event(
                emitter,
                primary_sink,
                jsonl_sink,
                commit_failure,
                code="MMT-E-AUDIT-COMMIT-FAILED",
                message="Could not commit the authoritative run manifest.",
            )
            if event_destination is not None and event_destination.is_file():
                try:
                    _refresh_event_log_artifact(manifest, event_destination)
                except MusicMasteringError as fingerprint_failure:
                    manifest.add_warning(
                        f"Event log recovery fingerprint failed: {fingerprint_failure}"
                    )
            manifest.extensions.setdefault("audit_commit_failures", []).append(
                commit_failure.to_dict()
            )
            if failure is None:
                failure = commit_failure
                manifest.mark_failed(commit_failure)
            else:
                manifest.add_warning(f"Final manifest commit also failed: {commit_failure}")
            try:
                store.save(manifest)
            except MusicMasteringError as recovery_failure:
                _emit_audit_commit_failure(
                    primary_sink,
                    job_id,
                    recovery_failure,
                    recovery=True,
                )
            if failure is commit_failure:
                raise
            raise failure from commit_failure

        if failure is not None:
            raise failure

        return RunOutcome(
            job_id=job_id,
            dry_run=effective_dry_run,
            manifest_path=str(manifest_destination),
            event_log_path=(str(event_destination) if event_destination is not None else None),
            validation=validation,
            engine_result=engine_result,
        )


def _add_input_artifacts(job: JobConfig, manifest: RunManifest, emitter: JobEventEmitter) -> None:
    emitter.debug(
        "MMT-D-FINGERPRINT-INPUT",
        "Fingerprinting input artifact.",
        stage="preflight",
        role="target",
        path=job.target,
    )
    manifest.add_input(
        ArtifactManifest.from_file(
            job.target,
            role="target",
            media_type=_media_type(Path(job.target)),
        )
    )

    fingerprinted_references: list[ArtifactManifest] = []
    for index, reference in enumerate(job.references):
        role = f"reference[{index}]"
        emitter.debug(
            "MMT-D-FINGERPRINT-INPUT",
            "Fingerprinting input artifact.",
            stage="preflight",
            role=role,
            path=reference.path,
        )
        fingerprinted_references.append(
            ArtifactManifest.from_file(
                reference.path,
                role=role,
                name=(
                    reference.label
                    if reference.label is not None and reference.label.strip()
                    else None
                ),
                media_type=_media_type(Path(reference.path)),
            )
        )

    level_weights = job.normalized_level_weights
    frequency_weights = job.normalized_frequency_weights
    indices_by_digest: dict[str, list[int]] = {}
    for index, artifact in enumerate(fingerprinted_references):
        if artifact.fingerprint is None:
            continue
        indices_by_digest.setdefault(artifact.fingerprint.sha256, []).append(index)

    for index, (reference, artifact) in enumerate(
        zip(job.references, fingerprinted_references, strict=True)
    ):
        fingerprint = artifact.fingerprint
        digest = fingerprint.sha256 if fingerprint is not None else ""
        group_indices = tuple(indices_by_digest.get(digest, (index,)))
        manifest.add_input(
            ArtifactManifest(
                path=artifact.path,
                role=artifact.role,
                name=artifact.name,
                fingerprint=fingerprint,
                media_type=artifact.media_type,
                metadata={
                    "reference_index": index,
                    "label": reference.label,
                    "requested_level_weight": reference.level_weight,
                    "requested_frequency_weight": reference.frequency_weight,
                    "normalized_level_weight": level_weights[index],
                    "normalized_frequency_weight": frequency_weights[index],
                    "effective_group_sha256": digest,
                    "effective_group_source_indices": group_indices,
                    "effective_group_level_weight": math.fsum(
                        level_weights[group_index] for group_index in group_indices
                    ),
                    "effective_group_frequency_weight": math.fsum(
                        frequency_weights[group_index] for group_index in group_indices
                    ),
                    "effective_for_level": level_weights[index] > 0,
                    "effective_for_frequency": frequency_weights[index] > 0,
                    "effective": (level_weights[index] > 0 or frequency_weights[index] > 0),
                },
            )
        )


def _reject_duplicate_input_content(job: JobConfig, manifest: RunManifest) -> None:
    if job.audio.allow_identical_target_and_reference or not manifest.inputs:
        return
    target_digest = manifest.inputs[0].fingerprint
    if target_digest is None:
        return
    duplicates = [
        artifact.path
        for artifact in manifest.inputs[1:]
        if artifact.fingerprint is not None and artifact.fingerprint.sha256 == target_digest.sha256
    ]
    if duplicates:
        raise PreflightError(
            "Target and reference have identical file content.",
            details={
                "target": manifest.inputs[0].path,
                "identical_references": duplicates,
                "sha256": target_digest.sha256,
            },
        )


def _verify_inputs_unchanged(manifest: RunManifest) -> None:
    """Fail a run whose input bytes changed after preflight fingerprinting."""

    for artifact in manifest.inputs:
        previous = artifact.fingerprint
        if previous is None:
            continue
        current = fingerprint_file(artifact.path)
        if current.sha256 != previous.sha256 or current.size_bytes != previous.size_bytes:
            raise ProcessingError(
                "An input changed while the mastering engine was running.",
                details={
                    "path": artifact.path,
                    "role": artifact.role,
                    "before_sha256": previous.sha256,
                    "after_sha256": current.sha256,
                    "before_size_bytes": previous.size_bytes,
                    "after_size_bytes": current.size_bytes,
                    "run_completion_blocked": True,
                    "rendered_outputs_treated_as_partial": True,
                },
            )


def _create_output_directories(job: JobConfig) -> None:
    if not job.edge_cases.create_output_directories:
        return
    for path in requested_artifact_paths(job):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    if job.audio.temp_directory is not None:
        Path(job.audio.temp_directory).mkdir(parents=True, exist_ok=True)


def _validate_audit_destinations(
    job: JobConfig,
    manifest_path: Path,
    event_log_path: Path | None,
    configuration_path: Path | None,
) -> None:
    protected: list[tuple[Path, str]] = [
        (Path(job.target).resolve(), "target"),
        *[
            (Path(reference.path).resolve(), f"reference[{index}]")
            for index, reference in enumerate(job.references)
        ],
        *[(Path(path).resolve(), "render-output") for path in requested_artifact_paths(job)],
    ]
    audit_paths = [(manifest_path.resolve(), "manifest")]
    if event_log_path is not None:
        audit_paths.append((event_log_path.resolve(), "event-log"))
    if len(audit_paths) == 2 and _same_path_or_file(
        audit_paths[0][0],
        audit_paths[1][0],
    ):
        raise OutputCollisionError(
            "Manifest and event log paths must be distinct.",
            details={"paths": [str(path) for path, _ in audit_paths]},
        )
    if configuration_path is not None:
        resolved_configuration = configuration_path.resolve()
        for protected_path, protected_role in protected:
            if _same_path_or_file(resolved_configuration, protected_path):
                raise OutputCollisionError(
                    "The configuration path collides with an audio input or output.",
                    details={
                        "path": str(resolved_configuration),
                        "configuration_role": "configuration",
                        "audio_role": protected_role,
                        "audio_path": str(protected_path),
                    },
                )
        protected.append((resolved_configuration, "configuration"))
    for path, role in audit_paths:
        collision = next(
            (
                (protected_path, protected_role)
                for protected_path, protected_role in protected
                if _same_path_or_file(path, protected_path)
            ),
            None,
        )
        if collision is not None:
            protected_path, protected_role = collision
            raise OutputCollisionError(
                f"The {role} path collides with a protected project path.",
                details={
                    "path": str(path),
                    "audit_role": role,
                    "protected_role": protected_role,
                    "protected_path": str(protected_path),
                },
            )
        if path.exists():
            raise OutputCollisionError(
                f"The {role} path already exists; audit records are never overwritten.",
                details={"path": str(path), "audit_role": role},
            )


def _same_path_or_file(first: Path, second: Path) -> bool:
    """Compare lexical paths and existing filesystem identities.

    Resolving symlinks catches ordinary aliases. ``samefile`` additionally
    catches hardlinks, which is essential before any output or audit write.
    """

    if os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve())):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _probe_rendered_audio(
    job: JobConfig,
) -> dict[str, dict[str, object]]:
    requests: list[tuple[str, str, str, int | None]] = [
        (output.path, "mastered-output", output.subtype, None) for output in job.outputs
    ]
    if job.preview.enabled:
        requests.extend(
            (path, role, job.preview.subtype, expected_frames)
            for path, role, expected_frames in (
                (
                    job.preview.target_path,
                    "target-preview",
                    int(job.preview.duration_seconds * job.audio.internal_sample_rate),
                ),
                (
                    job.preview.result_path,
                    "result-preview",
                    int(job.preview.duration_seconds * job.audio.internal_sample_rate),
                ),
            )
            if path is not None
        )

    probe = DefaultAudioProbe(clipping_peak=job.detection.clipping_peak)
    observed: dict[str, dict[str, object]] = {}
    for path, role, requested_subtype, expected_frames in requests:
        candidate = Path(path)
        if not candidate.is_file():
            raise ProcessingError(
                "The engine reported success without creating every requested artifact.",
                details={"missing_output": str(candidate), "role": role},
            )
        try:
            facts = probe.probe(candidate)
        except AudioProbeUnavailable as exc:
            raise ProcessingError(
                "A rendered artifact could not be reopened for verification.",
                details={
                    "path": str(candidate),
                    "role": role,
                    "reason": str(exc),
                },
            ) from exc

        violations: list[str] = []
        if facts.frames <= 0:
            violations.append("artifact contains no audio frames")
        if facts.non_finite_samples:
            violations.append(f"artifact contains {facts.non_finite_samples} non-finite samples")
        if facts.sample_rate != job.audio.internal_sample_rate:
            violations.append(
                f"sample rate is {facts.sample_rate}, expected {job.audio.internal_sample_rate}"
            )
        if facts.channels != 2:
            violations.append(f"channel count is {facts.channels}, expected stereo output")
        if facts.subtype is None or facts.subtype.casefold() != requested_subtype.casefold():
            violations.append(f"subtype is {facts.subtype!r}, expected {requested_subtype!r}")
        if expected_frames is not None and facts.frames != expected_frames:
            violations.append(f"preview has {facts.frames} frames, expected {expected_frames}")
        if violations:
            raise ProcessingError(
                "A rendered artifact failed post-render verification.",
                details={
                    "path": str(candidate),
                    "role": role,
                    "violations": violations,
                },
            )

        observed[str(candidate.resolve())] = {
            "path": str(candidate.resolve()),
            "role": role,
            "backend": facts.backend,
            "format": facts.format,
            "requested_subtype": requested_subtype,
            "observed_subtype": facts.subtype,
            "sample_rate": facts.sample_rate,
            "channels": facts.channels,
            "frames": facts.frames,
            "duration_seconds": facts.duration_seconds,
            "peak": facts.peak,
            "rms": facts.rms,
            "clipping_samples": facts.clipping_samples,
            "non_finite_samples": facts.non_finite_samples,
        }
    return observed


def _add_output_artifacts(
    job: JobConfig,
    manifest: RunManifest,
    observed: dict[str, dict[str, object]],
) -> None:
    artifacts: list[tuple[str, str, dict[str, object]]] = [
        (
            output.path,
            "mastered-output",
            {
                "mode": output.mode.value,
                "requested_subtype": output.subtype,
                "dither": output.dither.value,
                "label": output.label,
            },
        )
        for output in job.outputs
    ]
    if job.preview.enabled:
        artifacts.extend(
            (
                path,
                role,
                {"requested_subtype": job.preview.subtype},
            )
            for path, role in (
                (job.preview.target_path, "target-preview"),
                (job.preview.result_path, "result-preview"),
            )
            if path is not None
        )
    for path, role, metadata_fields in artifacts:
        source = Path(path)
        measured = observed.get(str(source.resolve()), {})
        manifest.add_output(
            ArtifactManifest.from_file(
                source,
                role=role,
                media_type=_media_type(source),
                metadata={**metadata_fields, **measured},
            )
        )


def _snapshot_existing_outputs(
    job: JobConfig,
) -> dict[Path, FileFingerprint]:
    snapshots: dict[Path, FileFingerprint] = {}
    for path in requested_artifact_paths(job):
        candidate = Path(path)
        if candidate.is_file():
            snapshots[candidate.resolve()] = fingerprint_file(candidate)
    return snapshots


def _record_partial_outputs(
    job: JobConfig,
    manifest: RunManifest,
    preexisting: dict[Path, FileFingerprint],
) -> None:
    already_recorded = {Path(artifact.path).resolve() for artifact in manifest.outputs}
    for path in requested_artifact_paths(job):
        candidate = Path(path)
        if not candidate.is_file() or candidate.resolve() in already_recorded:
            continue
        try:
            current = fingerprint_file(candidate)
            previous = preexisting.get(candidate.resolve())
            if previous is not None and previous.sha256 == current.sha256:
                continue
            manifest.add_output(
                ArtifactManifest(
                    path=current.path,
                    role="partial-output",
                    fingerprint=current,
                    media_type=_media_type(candidate),
                    metadata={
                        "retained_for_diagnostics": True,
                        "replaced_preexisting": previous is not None,
                        "previous_sha256": (previous.sha256 if previous is not None else None),
                    },
                )
            )
            manifest.add_warning(f"Partial output retained for diagnostics: {candidate}")
        except MusicMasteringError as exc:
            manifest.add_warning(f"Partial output fingerprint failed: {exc}")


def _forward_engine_log(emitter: JobEventEmitter, record: EngineLogRecord) -> None:
    code = (
        f"UPSTREAM-{record.code}" if record.code is not None else f"UPSTREAM-{record.level.upper()}"
    )
    emitter.emit(
        code,
        record.message,
        level=EventLevel.coerce(record.level),
        stage="engine",
        upstream_code=record.code,
    )


def _append_audit_failure_event(
    emitter: JobEventEmitter,
    primary_sink: EventSink,
    jsonl_sink: JsonlEventSink | None,
    failure: MusicMasteringError,
    *,
    code: str,
    message: str,
) -> None:
    """Best-effort compensating journal event after an audit-stage failure."""

    try:
        emitter.error(
            code,
            message,
            stage="audit",
            error=failure.to_dict(),
        )
    except MusicMasteringError:
        # The composite delivers to the optional primary sink before the
        # required JSONL sink, so the operator normally still sees this event.
        pass
    if jsonl_sink is not None:
        try:
            jsonl_sink.close()
        except Exception as close_error:
            _emit_audit_commit_failure(
                primary_sink,
                emitter.job_id,
                EventSinkError(
                    f"Could not sync the compensating audit event: {close_error}",
                    details={"exception_type": type(close_error).__name__},
                ),
                recovery=True,
            )


def _refresh_event_log_artifact(
    manifest: RunManifest,
    event_path: Path,
) -> None:
    manifest.outputs[:] = [
        artifact for artifact in manifest.outputs if artifact.role != "event-log"
    ]
    manifest.add_output(
        ArtifactManifest.from_file(
            event_path,
            role="event-log",
            media_type="application/x-ndjson",
        )
    )


def _emit_audit_commit_failure(
    sink: EventSink,
    job_id: str,
    failure: MusicMasteringError,
    *,
    recovery: bool = False,
) -> None:
    """Best-effort out-of-band notice when the authoritative commit fails."""

    try:
        sink.emit(
            Event.create(
                ("MMT-E-AUDIT-RECOVERY-FAILED" if recovery else "MMT-E-AUDIT-COMMIT-FAILED"),
                (
                    "Could not persist the failed audit state."
                    if recovery
                    else "Could not commit the authoritative run manifest."
                ),
                level=EventLevel.ERROR,
                job_id=job_id,
                stage="audit",
                error=failure.to_dict(),
            )
        )
    except Exception:
        # The original manifest failure remains authoritative; a diagnostic
        # sink must never mask it.
        pass


def _tool_version() -> str:
    try:
        return metadata.version("music-mastering-tools")
    except metadata.PackageNotFoundError:
        return "0.1.0-dev"


def _dependency_versions() -> dict[str, str | None]:
    """Record exact installed compatibility-stack versions in each manifest."""

    versions: dict[str, str | None] = {}
    for distribution in (
        "matchering",
        "numpy",
        "scipy",
        "statsmodels",
        "resampy",
        "soundfile",
    ):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def _media_type(path: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".wave": "audio/wav",
        ".flac": "audio/flac",
        ".aif": "audio/aiff",
        ".aiff": "audio/aiff",
        ".mp3": "audio/mpeg",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
    }.get(path.suffix.casefold(), "application/octet-stream")


__all__ = ["MasteringService", "RunOutcome"]
