"""Runnable native weighted-profile engine over the Matchering parity stages."""

from __future__ import annotations

import importlib
import time
from dataclasses import replace
from datetime import UTC, datetime

from ..config import EngineKind, JobConfig
from ..dsp.constants import (
    MAX_WEIGHTED_REFERENCES,
    PROFILE_ALGORITHM_VERSION,
)
from ..errors import (
    CapabilityError,
    DependencyError,
    MusicMasteringError,
    ProcessingError,
)
from .base import (
    EngineCapabilities,
    EngineLogHandler,
    EngineRunResult,
    requested_artifact_paths,
)
from .upstream import (
    _RUN_LOCK,
    PINNED_MATCHERING_VERSION,
    UpstreamMatcheringEngine,
    _config,
    _configure_logging,
    _load_matchering,
    _preview_result,
    _result,
    _verify_version,
)

NATIVE_ENGINE_VERSION = f"0.1.0+{PROFILE_ALGORITHM_VERSION}"


class NativeMasteringEngine:
    """Analyze and blend up to 32 references before rendering the target once."""

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine_id=EngineKind.NATIVE.value,
            engine_version=NATIVE_ENGINE_VERSION,
            implementation_status="experimental-runnable-weighted-profile",
            runnable=True,
            maximum_references=MAX_WEIGHTED_REFERENCES,
            independent_reference_weights=True,
            partial_matching_amount=False,
            ebu_r128_loudness=False,
            spectral_gain_ceiling=True,
            sample_peak_limiter=True,
            true_peak_limiter=False,
            external_limiter=False,
            dither=False,
            previews=True,
            thread_safe=False,
            planned_capabilities=(
                "partial-match-amount",
                "ebu-r128-loudness",
                "true-peak-limiter",
                "external-limiter",
                "dither",
            ),
        )

    def validate_capabilities(self, job: JobConfig) -> None:
        """Accept the weighted parity subset and reject every silent downgrade."""

        if job.execution.engine is not EngineKind.NATIVE:
            raise CapabilityError(
                "The native weighted engine requires execution.engine to select it.",
                details={
                    "engine": EngineKind.NATIVE.value,
                    "requested_engine": job.execution.engine.value,
                },
            )
        if len(job.references) > MAX_WEIGHTED_REFERENCES:
            raise CapabilityError(
                "The native weighted engine reference limit was exceeded.",
                details={
                    "engine": EngineKind.NATIVE.value,
                    "maximum_references": MAX_WEIGHTED_REFERENCES,
                    "requested_references": len(job.references),
                },
            )

        # Reuse every proven Matchering parity constraint except the choices
        # this adapter implements itself: engine identity, N references,
        # independent weights, and the optional post-smoothing EQ ceiling.
        parity_reference = replace(
            job.references[0],
            level_weight=1.0,
            frequency_weight=1.0,
        )
        parity_job = replace(
            job,
            references=(parity_reference,),
            matching=replace(job.matching, max_eq_gain_db=None),
            execution=replace(job.execution, engine=EngineKind.UPSTREAM),
        )
        try:
            UpstreamMatcheringEngine().validate_capabilities(parity_job)
        except CapabilityError as exc:
            raise CapabilityError(
                "The native weighted engine cannot honor one or more requested options.",
                details={
                    "engine": EngineKind.NATIVE.value,
                    "unsupported": list(exc.details.get("unsupported", ())),
                },
            ) from exc

    def run(
        self,
        job: JobConfig,
        *,
        log_handler: EngineLogHandler | None = None,
    ) -> EngineRunResult:
        # Heavy numerical dependencies stay lazy so ``mmt doctor`` can run and
        # diagnose an environment where NumPy/SciPy are absent or broken.
        from ..dsp.profiles import build_reference_inventory
        from ..dsp.weighted_matchering import process_weighted

        self.validate_capabilities(job)
        matchering = _load_matchering()
        defaults = importlib.import_module("matchering.defaults")
        _verify_version(matchering)

        started_at = datetime.now(UTC)
        started = time.monotonic()
        inventory = build_reference_inventory(job)
        result_requests = [_result(matchering, output) for output in job.outputs]
        configured = _config(matchering, defaults, job)
        preview_target = _preview_result(
            matchering,
            job.preview.target_path if job.preview.enabled else None,
            job.preview.subtype,
        )
        preview_result = _preview_result(
            matchering,
            job.preview.result_path if job.preview.enabled else None,
            job.preview.subtype,
        )

        with _RUN_LOCK:
            try:
                _configure_logging(matchering, log_handler)
                outcome = process_weighted(
                    job=job,
                    inventory=inventory,
                    config=configured,
                    results=result_requests,
                    preview_target=preview_target,
                    preview_result=preview_result,
                )
            except (CapabilityError, DependencyError, MusicMasteringError):
                raise
            except Exception as exc:
                raise ProcessingError(
                    f"Native weighted processing failed: {exc}",
                    details={
                        "engine": EngineKind.NATIVE.value,
                        "engine_version": NATIVE_ENGINE_VERSION,
                        "matchering_parity_version": PINNED_MATCHERING_VERSION,
                        "algorithm_version": PROFILE_ALGORITHM_VERSION,
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            finally:
                try:
                    matchering.log()
                except Exception:
                    pass

        return EngineRunResult.completed(
            engine_id=EngineKind.NATIVE.value,
            engine_version=NATIVE_ENGINE_VERSION,
            output_paths=requested_artifact_paths(job),
            started_at=started_at,
            duration_seconds=time.monotonic() - started,
            details=outcome.details,
        )


__all__ = ["NATIVE_ENGINE_VERSION", "NativeMasteringEngine"]
