# Vision and scope

Status: **approved direction; working compatibility, weighted, catalog, and
GUI-first workbench slices**

## Product vision

Build a private, reproducible reference-mastering workbench that preserves the
useful deterministic behavior of Matchering 2.0.6 while making every important
decision inspectable, configurable, testable, and safe at the edges.

The workbench should help its operator answer four questions for every render:

1. What did the system observe in the target and reference?
2. What processing plan did those observations produce?
3. Exactly how was the output rendered?
4. What evidence shows that the result is valid and reproducible?

The workbench runs locally for one desktop user. On 2026-10-02 the owner opened
a Windows executable and GitHub preparation milestone: Windows 10/11 x64,
independent state per Windows account, reliable selected paths, and the CADER
visual direction. See the [distribution design](../design/windows-distribution-2026-10.md).
This milestone does not turn the portal into a public hosted service or claim
that every longer-term DSP requirement is complete.

## Primary users

- A producer or engineer mastering one target against one reference.
- An experimenter comparing one target against several references.
- An album operator applying one reference or reference profile to many
  targets.
- A developer inspecting or extending the DSP pipeline.
- An operator diagnosing a failed or questionable job from artifacts and logs
  without asking a user to reproduce the error manually.

## Product principles

### Reproducibility before convenience

Inputs, configuration, code/dependency identity, plans, events, measurements,
and artifacts must form an auditable job record. Convenience adapters may
submit work, but they do not own DSP policy.

### Characterize before changing sound

The existing algorithm is the behavioral baseline. We first capture its normal
and pathological behavior, then refactor behind measurements. Deliberate sonic
changes require new expectations, listening evidence, and a recorded decision.

### Explicit stages and bounded controls

Analysis, planning, and rendering are separate contracts. Creative controls
such as match amount or weighted references must be explicit in the plan and
must have safe, documented ranges.

### Honest audio semantics

The system must not claim true stereo creation from duplicated mono, LUFS
matching when it uses RMS, or true-peak protection when it only controls sample
peaks. Reports use technically precise names.

### Failure should be diagnosable

Errors have stable machine codes, correlation identifiers, contextual
attributes, and remediation guidance. Sensitive paths or audio content are not
logged by default.

### Local and private by default

Core processing makes no network calls. Retention, reference reuse, and any
remote adapter are explicit choices.

## In scope for the private working version

- One-target/one-reference mastering with parity to the useful upstream flow:
  loading, validation, resampling, loud-section selection, RMS level matching,
  Mid/Side spectral matching, level correction, optional limiting, output
  encoding, and paired previews.
- Limited, peak-normalized, and raw floating-point output paths.
- Complete, validated exposure of the inherited Matchering configuration
  surface, with unsafe or ambiguous settings made explicit.
- An `analyze → plan → render` Python API with serializable reports and plans.
- File and in-memory adapters, a command-line workflow, and a job-oriented
  service boundary suitable for a private local UI or worker.
- A private content-addressed catalog for targets, references, locations,
  reusable weighted sets, strict selection/catalog reimport, runs, and
  artifacts, plus a GUI-first localhost workbench, explicit terminal fallback,
  and root launcher.
- Per-job manifests, structured events, timings, measurements, warnings, and
  artifact inventory.
- Batch workflows: one reference to many targets and one target to many
  references.
- Reference-analysis caching keyed by content and analysis configuration.
- Safe creative controls: match amount, bounded spectral correction, and
  weighted multi-reference planning.
- Optional native EBU R128 loudness, oversampled true-peak policy, explicit
  dithering, and safe metadata handling, each separately named and verified
  rather than implied by the compatibility engine.
- DAW handoff through raw float output and documented plan/filter export.
- Automated unit, property, characterization, regression, smoke, integration,
  and resource tests.
- Explicit behavior for silence, near-silence, mono, malformed data,
  non-finite samples, unsupported channel layouts, unusual sample rates,
  pathological ratios, partial writes, cancellation, and resource exhaustion.
- Private GPL-compatible provenance and dependency hygiene.

The requirements in [requirements.md](requirements.md) define acceptance.

## Out of scope for the private completion gate

- A public hosted service or public multi-tenant deployment.
- Publication to PyPI, an app store, or container registry. GitHub source and
  Windows binary preparation now belong to the authorized sharing milestone.
- A legal or branding decision for a public product.
- Real-time, low-latency plug-in processing.
- A native VST/AU/AAX implementation.
- A Rust rewrite or a new DSP engine merely for language preference.
- Machine-learning genre recognition or generative mastering.
- Automated stem mixing. The architecture should not make it impossible, but
  it is a separate future product problem.
- Claims of broadcast compliance beyond the explicitly implemented and tested
  EBU R128/true-peak measurements; meeting a number is not blanket delivery
  compliance.

## Intended workflows

### Reference master

An operator submits a target and reference, reviews the analysis and proposed
plan, renders selected output variants, and receives the audio plus a complete
job report.

### Album consistency

An operator analyzes one approved reference once, reuses the immutable profile
across multiple targets, and compares both per-track and collection-level
measurements.

### Reference audition

An operator analyzes one target against several references, renders consistently
named previews or masters, and compares plans and outcomes without overwriting
earlier artifacts.

### Catalog reuse and recovery

An operator catalogs private targets/references without copying them, saves a
weighted reference set, exports the versioned catalog or one content-bound
selection, reimports it later, verifies/relinks moved byte-identical files, and
retains complete run/artifact history without deleting source audio.

### DAW handoff

An operator exports an unclipped 32-bit floating-point result, measurements,
and the matching plan for final limiting or further processing in a DAW.

### Failure investigation

An operator opens the job manifest and event stream, finds the failed stage and
stable error code, inspects non-sensitive measurements and dependency identity,
and can reproduce the job locally when the source material is available.

## Success criteria

The private working version is complete only when:

- all P0 requirements and the private-gate P1 requirements are `VERIFIED`;
- the normal-input characterization corpus has no unexplained regressions;
- every supported output path passes smoke tests;
- edge-case outcomes match the policy and never emit an unexplained traceback
  as the sole diagnostic;
- repeated jobs with identical inputs and environment produce equivalent plans
  and audio within documented tolerances;
- each job produces a schema-valid manifest and correlated structured events;
- batch and cancellation tests leave no corrupt final artifacts;
- operator documentation can reproduce setup, processing, and diagnosis on the
  supported environments; and
- the licensing checklist confirms provenance and GPL-compatible dependencies.

Listening review remains necessary. Passing numerical tests means the system is
consistent with its specification, not that every reference is artistically
appropriate.

## Non-goals and claim boundaries

The workbench is a reference-transfer tool, not an autonomous mastering
engineer. It should explain that results depend strongly on reference choice.
It must call the inherited loudness calculation "loud-section RMS" unless a
separate LUFS implementation is used. It must distinguish sample peak from
oversampled true peak and derived Mid/Side filtering from explicit stereo image
generation.
