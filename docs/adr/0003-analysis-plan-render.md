# ADR-0003: Separate analyze, plan, and render

- Status: Accepted
- Date: 2026-07-27
- Decision owners: application and DSP maintainers
- Requirements: PIPE-001 through PIPE-008, FLOW-001, ROB-005, OBS-001

## Context

Upstream `process()` loads files, analyzes target/reference, computes matching
filters, renders variants, writes files, creates previews, and logs through
global callbacks in one orchestration path. It returns no analysis, plan,
measurements, filter, audio array, or report.

That shape makes it difficult to reuse a reference, explain a render, compare
several references, cache expensive analysis, replay a decision, or test stages
without filesystem work.

## Decision

The private core will expose three explicit, versioned operations:

1. `analyze(canonical_audio, analysis_config)` returns immutable analysis or a
   reusable reference profile;
2. `build_plan(target_analysis, reference_profiles, planning_config)` returns a
   complete immutable match plan; and
3. `render(canonical_target, match_plan, output_requests, render_context)`
   returns measurements and transactional artifact records.

The plan contains every render-affecting decision: source/profile identity,
gains, Mid/Side filters, guardrails, limiter/normalization modes, preview
policy, rates, creative controls, and algorithm/schema versions. Render does
not access reference audio or global configuration.

A one-call convenience facade may compose the operations but cannot create a
second hidden behavior.

## Consequences

### Positive

- One reference profile can safely serve many targets.
- Plans can be inspected, compared, exported, and replayed.
- Batch, CLI, service, and UI adapters share one core.
- Analysis, creative policy, and render correctness get focused tests.
- Cache keys and compatibility rules become explicit.
- The manifest can explain what happened rather than only record filenames.

### Costs and constraints

- More versioned models and validation are required.
- Filter-array serialization/hash conventions must be designed.
- Canonical audio lifetime and memory ownership need explicit handling.
- Plans may be larger than a conventional preset.
- Migration must prove equivalence rather than moving code mechanically.

## Alternatives considered

### Keep one `process()` function and add callback hooks

Rejected as the core design. Hooks do not make decisions replayable and retain
hidden coupling. A convenience wrapper remains acceptable over explicit stages.

### Analyze only the reference

Rejected. Planning also depends on target loudness/spectra and guardrail
conditions; target analysis must be explicit.

### Persist arbitrary Python objects with pickle

Rejected at trust boundaries. Pickle is unsafe for untrusted input and weak as
a long-term compatibility contract. Schemas use data-only formats and explicit
binary array encoding.

### Make every adapter call raw DSP helpers

Rejected. It duplicates policy and makes manifests, cancellation, and error
semantics inconsistent.

## Verification

- Each stage has an independent contract suite.
- Plan serialization round-trips and incompatible major versions fail closed.
- Convenience processing equals explicit composition.
- Render tests prove it needs no reference path/audio and no global logger.
- Cache keys include every analysis-affecting identity component.
- Replaying a plan satisfies the declared determinism tier.
- The dependency rule `adapters → application → domain/DSP` is checked.

## Revisit triggers

- plan size or canonical-audio memory makes supported workflows impractical;
- real-time processing becomes an approved product requirement;
- multi-stage streaming requires a different replay contract; or
- a second algorithm cannot fit the versioned plan abstraction.
