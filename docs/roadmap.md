# Phased roadmap

Status: **active evidence-driven sequence**

This roadmap orders risk reduction. Dates are intentionally omitted until the
repository foundation and characterization runtime are measured. A phase exits
on evidence, not elapsed time.

The original private working-version gate remains at the end of Phase 5 as the
longer-term DSP target. On 2026-10-02 the owner explicitly opened preparation
for GitHub sharing and a Windows executable. That scoped milestone can ship
the existing supported capabilities without claiming completion of every
planned DSP extension.

## Current sharing checkpoint — 2026-10-02

Prepare a single Windows 10/11 Intel/AMD x64 EXE with Python/DSP/assets bundled,
persistent independent state per Windows account, reliable native path
navigation/picker locations, and the CADER visual direction. Retain the local
browser interface and its loopback security boundary. Add pinned build tooling,
regression/frozen smoke evidence, project/dependency notices, checksums, a safe
Git source candidate, and Windows CI without automatic publishing.

The build and source candidate are reviewable before any upload. Exact
corresponding dependency sources and an independent clean-Windows operator
pass remain release tasks. The owner has now explicitly authorized source
commit/push to [Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster).
Normal desktop launches also gain last-tab shutdown after a refresh grace
period and active-work completion, with persistent developer modes. See the
[Windows release guide](deployment/windows-release.md) and
[ADR-0007](adr/0007-windows-executable-and-user-workspace.md).

## Current interface checkpoint — 2026-09-03

The operator interface now has three primary workspaces: **Master**,
**Library**, and **Activity**. Master keeps input/reference selection, delivery,
readiness, validation, dry run, and render in one workflow; searchable
multi-select catalog pickers replace repeated one-item additions. Optional
formats and supported advanced settings use progressive disclosure. Library
keeps source/version management and export together. Activity presents History
with Event log and System/Session logs as secondary utilities. The persistent
comparison dock now has direct A/B candidate pickers, one-click swap that
preserves the playhead, and non-destructive Quick Play preview.

Semantic packaged-asset tests protect this information architecture and its
keyboard, focus, contrast, motion, and responsive CSS hooks. The Chromium smoke
harness defaults to Master, accepts a primary workspace or utility hash and
explicit viewport dimensions, and waits for the initialized application-state
sentinel before retaining token-redacted evidence. Interactive operator and
assistive-technology acceptance remains open.

## Historical checkpoint — 2026-07-27

Delivery is intentionally not strictly phase-linear when a well-bounded,
tested operator slice can be added without claiming the surrounding phase is
complete. At that checkpoint, the workspace had:

- the Phase 0 project/tooling/documentation foundation;
- a runnable, audited Phase 2 compatibility slice through pinned Matchering
  2.0.6, while public analyze/plan/render contracts remain incomplete;
- a Phase 4 content-addressed catalog, strict catalog/selection exchange,
  GUI-first localhost portal, terminal fallback, CLI administration,
  double-click Windows launcher, strict persistent delivery-root preferences,
  compact multi-target/reference queues, secure native A/B auditioning, and
  sequential 1–32-target expansion with independent per-target evidence;
- a Phase 5 experimental native weighted-profile slice for one through 32
  references with independent level/frequency weights and guarded EQ; and
- focused automated evidence for those slices.

This checkpoint does **not** move the private completion gate. Characterization
goldens, transactional audio publication, cancellation/resource budgets,
reference-profile cache/reuse, resource-bounded parallel batches,
one-target/many-alternative-reference comparison batches, advanced
loudness/peak/dither/metadata controls, and remaining P0/P1 acceptance
evidence are still open. The
[implementation status register](status/implementation-status.md) remains the
authority.

## Phase 0 — Establish the launchpad

Goal: make the workspace safe to evolve.

Deliverables:

- initialize repository history and record the extracted upstream source/ZIP
  provenance;
- establish a `src/` package, test layout, locked development environment, and
  repeatable commands;
- add baseline schemas/contracts for jobs, plans, manifests, and events without
  claiming DSP completion;
- add lint, type, test, documentation-link, and license checks;
- preserve the upstream GPL license and notices; and
- adopt the requirements, policies, ADRs, and traceability register in this
  documentation.

Exit evidence:

- a clean bootstrap from documented commands;
- fast test and static-check commands pass;
- all scaffolded models clearly reject unsupported execution;
- repository status distinguishes upstream extraction from private code; and
- requirements have initial status and milestone ownership.

## Phase 1 — Characterize Matchering 2.0.6

Goal: turn upstream behavior into measured evidence before touching DSP.

Deliverables:

- deterministic synthetic audio-fixture generator;
- normal stereo, mono, resampling, short, silence, near-silence, spectral-null,
  clipped, limited, equality, preview, and output-mode cases;
- a baseline runner isolated from the future private engine;
- measurements and golden plan-equivalent observations for each stage;
- dependency/runtime snapshot and tolerance rationale;
- named regression tests for the discovered silence and fractional-preview
  failures; and
- a level-matched listening protocol with a small, legally usable private
  corpus.

Exit evidence:

- `TEST-001` is `VERIFIED`;
- every branch of the upstream public processing path is exercised;
- known defects are reproducible and tagged `expected-upstream-failure`;
- baseline artifacts include hashes, environment identity, and review date;
- no unexplained non-finite outputs occur in the accepted normal corpus.

## Phase 2 — Establish parity through analyze → plan → render

Goal: express the existing algorithm through explicit contracts without
deliberately changing its sound.

Deliverables:

- typed configuration and domain errors;
- file and array ingest into canonical audio;
- `AudioAnalysis` and reusable `ReferenceProfile`;
- versioned `MatchPlan` containing gains and Mid/Side filters;
- render paths for raw float, normalized, and limited output;
- paired previews;
- transactional file artifacts;
- per-job events and manifest generation; and
- a compatibility facade for one-call processing.

Exit evidence:

- P0 CAP, PIPE-001 through PIPE-004, OBS-001, and OBS-002 requirements are
  verified;
- characterization deltas are within reviewed tolerances;
- plan serialization round-trips and replay works;
- concurrent jobs demonstrate no configuration/log cross-talk;
- normal, failed, and cancelled job manifests validate against schemas.

## Phase 3 — Harden edge cases and resources

Goal: make failure safe, bounded, and diagnosable.

Deliverables:

- implement every P0 edge-case policy row;
- spectral ratio, gain, filter-energy, and correction guardrails;
- optimized-mode-safe configuration validation;
- decoded size, duration, memory, output, and concurrency limits;
- cooperative cancellation;
- secure FFmpeg fallback and codec capability reporting;
- atomic cleanup and overwrite policy; and
- property, fuzz-like malformed-input, fault-injection, and resource tests.

Exit evidence:

- `ROB-001` through `ROB-003`, `ROB-005` through `ROB-007`, `SEC-001`, and
  `SEC-002` are verified;
- adversarial fixtures remain bounded or fail before render;
- no test failure leaves a corrupt final artifact;
- diagnostic reports identify a stable root cause for every policy failure.

## Phase 4 — Productive private workflows

Goal: make the reliable engine convenient for daily work.

Deliverables:

- non-interactive CLI with JSON status;
- private content-addressed track/location/run/artifact catalog;
- named independently weighted reference sets;
- strict complete-catalog and target/reference-selection export/reimport;
- GUI-first catalog-backed localhost portal, explicit console fallback, and
  root Windows launcher;
- one-reference/many-target and one-target/many-reference batches;
- immutable reference cache with compatibility keys;
- job submit/status/cancel/result application contracts;
- DAW handoff bundle;
- operator runbook and troubleshooting guide; and
- retention/redaction controls.

Current delivered slice:

- catalog, exchange, CLI adapter/integration, workbench, and launcher behavior
  plus portal application/HTTP/native-dialog behavior have focused automated
  tests; and
- the operator can launch into a three-workspace GUI, use searchable
  multi-select pickers to queue 1–32 inputs and 1–32 references, manage the
  catalog and weighted reference profiles, choose and persist a delivery root,
  review a live readiness rail, progressively reveal optional formats and
  supported advanced controls, validate all targets, and run sequential
  dry-run/render batches with continue-after-target-failure;
- Library provides source/version lifecycle, direct auditioning, and explicit
  export, while Activity groups History with Event log and System utilities for
  manifest/artifact inspection, indexing recovery, and diagnostics; and
- the persistent comparison dock directly selects any available original or
  mastered deliverable for A/B, swaps assignments without losing the playhead,
  and Quick Plays a candidate without overwriting the chosen pair.

Remaining exit evidence:

- FLOW-002 through FLOW-006, FLOW-008, and PIPE-005 are verified;
- one analyzed reference profile is safely reused across a batch, bounded
  concurrency/resource policy and cancellation are tested, and alternative
  comparison batches are implemented;
- reference cache invalidation and corruption tests pass;
- a fresh operator can run and diagnose a job from documentation.

## Phase 5 — Controlled creative and output expansion

Goal: add intended extensions without losing reproducibility or safety.

Deliverables:

- bounded match-amount control;
- weighted multi-reference planning;
- plan/filter export with stable compatibility behavior;
- measured default guardrails tuned on the corpus;
- separately selectable EBU R128 analysis/planning and oversampled true-peak
  measurement/output policy;
- explicit TPDF/noise-shaped dither behavior for integer outputs;
- safe metadata drop/copy policy; and
- comparative reports and preview organization; and
- focused listening and regression records for changed sonic behavior.

Current delivered slice:

- independently weighted reference profiles combine loudness and Mid/Side
  spectra in logarithmic amplitude space;
- SHA-256 duplicate coalescing and stable effective ordering make the plan
  invariant to request order;
- one through 32 references, all three output branches, paired previews, and
  an optional EQ ceiling run through the native Matchering-parity path; and
- analytic, parity, duplicate, order, preview, and multi-output regressions
  cover that slice.

Match amount, public plan/filter exchange, EBU R128, true peak, dither,
metadata copying, comprehensive guardrails, and listening acceptance remain
open.

Exit evidence:

- PIPE-006 through PIPE-008, CAP-013 through CAP-016, and TEST-003 are verified;
- zero/full/intermediate match behavior is documented and tested;
- weighted planning is order-invariant and single-reference compatible;
- intentional sonic deltas have both numerical and listening approval.

### Private working-version completion gate

The project is a complete private working version only when:

- all P0 requirements are `VERIFIED`;
- all P1 requirements are `VERIFIED`;
- supported-platform install, full regression, and end-to-end smoke suites pass;
- the security/privacy and licensing reviews have no unresolved P0 findings;
- manifests can reproduce the configuration and explain failures;
- documentation and traceability checks pass; and
- a documented recovery procedure exists for caches and job artifacts.

## Phase 6 — Private localhost GUI hardening

Goal: make the now-required GUI-first workflow safe, accessible, and proven on
the supported workstation without turning it into a public service.

Current delivered slice:

- the root launcher defaults to `mmt gui`; `-Terminal` preserves the console
  fallback and `-CheckOnly` remains noninteractive;
- a packaged responsive browser interface exposes three primary workspaces:
  Master, Library, and Activity. Reference profiles, Event log, and System are
  discoverable secondary utilities instead of competing top-level tabs;
- Master accepts 1–32 inputs and 1–32 references through searchable
  multi-select catalog pickers, keeps required delivery controls in the main
  flow, collapses optional formats and supported advanced settings, and uses a
  sticky readiness/run rail to expose missing prerequisites and active work;
- the persistent native A/B dock directly selects originals or any available
  mastered deliverable, swaps A/B while preserving the playhead, and uses a
  non-destructive Quick Play state for transient previews;
- Library consolidates source/version lifecycle, comparison, and explicit
  export; Activity consolidates History with Event log and System utilities;
- each batch target is expanded sequentially into an independent
  configuration, selection, run, manifest, event log, catalog record, and
  exclusive run-named output subfolder; one target's failure does not erase
  siblings or prevent later targets;
- System includes a path-confined Session logs viewer for retained launcher and
  portal diagnostics;
- `PortalApplication` isolates browser/HTTP concerns from catalog/mastering
  behavior and serializes long-running jobs on one background worker;
- the server is constructor-confined to random-port `127.0.0.1`, uses a fresh
  session token, Host/origin checks, strict bounded JSON, CSP/defensive headers,
  no CORS/CDN/upload/Electron runtime, and local-only assets;
- fixed-script WinForms pickers select audio/JSON/folders by path, while
  Explorer integration selects artifacts without executing them;
- launcher transcripts close before portal handoff, normal startup logs only
  the origin, and HTTP request logs redact the session token; and
- semantic asset contracts protect the three-workspace navigation, unified
  Master flow, progressive disclosures, readiness rail, direct A/B controls,
  accessible names/focus hooks, and responsive layout; and
- the headless Chromium smoke defaults to Master, supports direct primary or
  utility startup, accepts explicit viewport dimensions, verifies the ready
  sentinel and workflow landmarks, and retains token-redacted
  bootstrap/DOM/screenshot/shutdown evidence.

Current limitations are explicit: there is no cooperative cancellation, active
work blocks shutdown, batch targets are serialized and currently re-read their
references, operation projections are process-local, and native A/B playback
depends on browser codec support because the media route does not transcode.
The portal is not authorized for LAN/proxy/tunnel/container/public or
multi-user use.

Remaining exit evidence:

- an interactive supported-browser run through catalog import, one- and
  multi-target/multi-reference validation/dry-run/render, A/B comparison,
  delivery-root default/override, partial target failure, event display,
  artifact opening, recovery, and native dialogs (semantic asset and headless
  viewport-smoke coverage does not replace this operator exercise);
- keyboard, focus, responsive-layout, screen-reader, contrast, and zoom review;
- native WinForms picker and browser-failure/`--no-browser` exercises;
- cancellation/resource budgets and corresponding truthful GUI state;
- bounded retention/redaction review for portal logs, browser-visible
  tracebacks, operation history, and private live-console fallback URLs
  (HTTP and retained smoke artifacts already redact the token); and
- the full `FLOW-007` and `SEC-004` acceptance evidence.

## Phase 7 — Sharing preparation, opened 2026-10-02

The owner's current instructions authorize Windows work and source commit/push
to [Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster). A
successful private gate is not required before preparing this narrower
distribution of existing supported capabilities. The current phase includes:

- product naming and trademark review;
- exact GPLv3-or-later source and corresponding-source packaging;
- dependency and media-license audit;
- a threat model for the distributed local desktop application;
- security response and vulnerability disclosure process;
- release artifacts, SBOM, checksums, reproducible-build evidence;
- public documentation and support boundaries; and
- resolution of actual outstanding component/license questions for the chosen
  desktop distribution model.

Public hosted services, multi-user servers, and production container deployment
remain outside this desktop milestone.

See the [licensing policy](policies/licensing.md). Complete the source audit,
commit, push, and remote verification for this authorized publication. Build/CI
does not publish a binary release automatically; corresponding dependency-source
collection and independent-machine qualification remain separate release work.

## Cross-phase rules

- New DSP work cannot skip characterization.
- A roadmap checkbox never changes a requirement to `VERIFIED`; evidence does.
- Defects get a regression test before or with the fix.
- Status and documentation change in the same change set as behavior.
- Temporary debug logging is encouraged, but production-capable sinks must
  obey privacy and volume policies.
- Performance work must preserve measured behavior or document an intentional
  algorithm change.
