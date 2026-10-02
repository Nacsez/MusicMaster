# GUI workflow pass — 2026-07-27

Status: **implemented private-workbench slice with automated evidence; operator
acceptance remains open**

## Trigger and goals

The first GUI operator review found that the feature-complete portal exposed
too much information at once, rendered file collections as oversized cards,
accepted several references but only one input, required leaving the
application for quick comparisons, and did not expose a delivery-folder
workflow.

This pass treated those findings as one workflow problem:

1. make the default view quieter and file collections denser;
2. make target and reference selection structurally consistent;
3. keep quick source/result comparison inside the workbench;
4. make delivery ownership explicit without allowing overwrite; and
5. retain truthful per-target audit evidence when one queued input fails.

## Decisions and boundaries

- Dashboard guidance is collapsed and the primary work surfaces use compact,
  scrolling tables/queues with shared interaction patterns.
- Master Job accepts 1–32 distinct inputs and 1–32 weighted references.
- A multi-target request expands sequentially into independent target jobs.
  Every target owns its run ID, configuration, one-target selection, manifest,
  event log, catalog bindings, and output subfolder. The explicit policy is to
  continue later independent targets after one fails.
- This is not yet reference-profile caching: engines may re-read and re-analyze
  the same reference request for each target. Parallel resource scheduling and
  one-target/many-alternative-reference comparison batches remain planned.
- The persistent delivery preference is a strict, versioned, atomically
  replaced document. A job can override it. Each target gets an exclusively
  created `<target>-master-<run-id>/` child; an existing child is never reused,
  overwritten, or removed as cleanup.
- The A/B dock uses the browser's native audio element and original catalog
  bytes. Its separate random cookie is `HttpOnly`, `SameSite=Strict`, and
  media-path-scoped; the route accepts only catalog IDs and supports bounded
  HTTP Range streaming. It is not a general file server or API credential.
- Audition playback does not transcode, resample, loudness-match, or replace a
  controlled listening protocol. Browser codec support is an explicit limit.

## Evidence and follow-up

The completed gate has 216 passing tests, one case-insensitive-filesystem skip,
88.97% branch coverage, clean Ruff lint/format across 60 files, and clean mypy
across 28 source files. Focused tests cover batch validation/continuation,
output-folder preferences and collisions, media authentication/ranges, and GUI
asset contracts. Headless Chromium smokes passed at 1440 × 1000 for both the
Dashboard and `Invoke-PortalSmoke.ps1 -InitialTab master-job`.

The next operator pass should exercise real A/B playback across available
codecs, switch slots at matched playhead positions, save/reload a default
delivery folder, override it for one job, render several targets, force one
target preflight failure, and inspect every resulting manifest/artifact record.
Accessibility, zoom/responsive behavior, cooperative cancellation,
reference-profile reuse, and bounded parallelism remain separate work.

## Follow-up source/version pass — 2026-07-30

The next operator review found that generic manual A/B slots still made it hard
to find an original after rendering, generated outputs looked like unrelated
catalog rows, and the remaining split-pane/card geometry compressed library
columns at the operator's display resolution. It also identified a missing
post-render workflow: review several takes, rename presentation labels, export
a selected batch with a useful suffix, and remove a bad take without damaging
its evidence.

The implemented interaction model is now source-to-version:

1. Music Library renders one stable top-level row per non-generated source.
2. A completed render is attached to the selected target as a run/version.
   Only available audio artifacts whose exact manifest role is
   `mastered-output` qualify; dry runs, previews, partial outputs, failed runs,
   and audit artifacts cannot inflate the master count.
3. One playable version prepares A = original and B = preferred master
   automatically without starting playback. Several versions remain explicit
   rows, and a version with several deliverables has an output chooser.
4. Cross-song checkboxes select preferred masters for export. Version-level
   checkboxes can override that choice. Export uses a native destination
   picker, a common editable suffix, exclusive copies, post-copy fingerprint
   verification, and a per-item result so one collision does not conceal
   successful siblings.
5. Track renaming updates catalog display metadata only. It does not rename
   audio or rewrite immutable identities/manifests.
6. Discard is keyed by source plus run/version, not by content-derived output
   track ID. It quarantines all mastered deliverables for that version, writes
   an indexed tombstone, and preserves source audio plus run/audit evidence.
   Restore is compare-and-swap-like: it verifies quarantine bytes and refuses
   any occupied original destination.

The list architecture also moved away from nested capsules. Original and
version inventories are conventional tables inside rectangular boxed
sections. The scroll viewport, not the surrounding page, owns wide-table
overflow and sticky headers. At 1460 px and below, source and detail sections
stack vertically so neither pane is forced into compressed columns. Narrow
layouts preserve table semantics and horizontal scrolling rather than
transforming each file into a tall card.

Static GUI regression coverage now protects the source-only primary list,
automatic A/B hooks, multi-version/output choices, cross-song export controls,
rename and recoverable lifecycle routes, the responsive boxed-list selectors,
and removal of the former per-track manual A/B IDs. Application and HTTP tests
own lineage filtering, label validation, exclusive batch copies, per-item
failures, quarantine/restore integrity, shared-path refusal, strict request
shapes, and no-overwrite recovery behavior.

The next operator pass should exercise this model with actual music: render
several sources and references, create two versions of one song, compare at
matched playhead positions, export a cross-song selection with a custom suffix,
force a destination collision, discard and restore a bad take, relink a moved
original, and check the layout at the operator's normal zoom and resolution.

## Unified-workflow pass — 2026-09-03

Status: **frontend structure implemented; regression evidence and operator
acceptance remain in progress**

This pass implements the structural direction recorded in the
[September 2026 UI/UX redesign brief](../design/ui-ux-redesign-2026-09.md).
The goal is to make the musical sequence—not the backend service boundaries—the
organizing model while preserving all catalog, execution, evidence, recovery,
and privacy contracts.

### Implemented interaction model

1. The seven peer tabs are consolidated into three primary workspaces:
   **Master**, **Library**, and **Activity**. Named reference-set management is
   available as the **Reference profiles** utility under Master. **History**,
   **Event log**, and **System** are grouped under Activity; the application
   menu also links directly to the two less frequent utility views.
2. Master is one continuous canvas with **Music**, **Delivery**, and **Review &
   run** landmarks. Mix and reference queues share the same visual structure.
   Searchable library dialogs support multi-selection, so a batch no longer
   requires reopening a single-item catalog picker for every target or
   reference.
3. The limited PCM-24 master and destination are the visible default delivery
   choices. Normalized and raw deliverables plus paired previews are grouped in
   a collapsed **Additional formats & paired preview** disclosure. Supported
   processing, safety, evidence, and note fields are grouped in collapsed
   **Advanced processing & safety**. Unsupported capabilities remain truthful
   through validation and the System capability view; they are not simulated.
4. The Review & run rail exposes the four actionable prerequisites—mixes,
   references, destination, and deliverable—and keeps check, test-run, and
   render actions together. Starting an operation no longer redirects to a
   dashboard: Master stays active while the global task state and local run
   summary report the worker stage. Activity remains available for detailed
   event and durable run inspection.
5. Library retains the source-to-version model while moving maintenance and
   batch-export controls behind contextual actions and an export disclosure.
   Selecting a source with an available master prepares an original/master pair
   without autoplay.
6. The persistent comparison dock now exposes direct A and B selectors over
   playable originals and mastered deliverables, an explicit swap action, and
   `1`/`2` side-selection shortcuts. Candidate or side changes preserve the
   playhead when possible. The dock continues to identify playback as raw level:
   it does not loudness-match, normalize, resample, or transcode audio.

### Preserved boundaries

- Mastering remains one serialized background operation, with 1–32 targets
  expanded into sequential independent runs and 1–32 references applied as the
  shared request. One target failure does not erase successful siblings.
- One reference still selects the pinned Matchering 2.0.6 compatibility route;
  two through 32 select the native deterministic weighted route.
- Validation remains non-materializing. Every accepted dry run or render owns
  its configuration, selection, event log, manifest, run ID, and exclusive
  no-overwrite destination; each render performs its own preflight.
- There is still no safe cooperative cancellation contract. The portal remains
  loopback-only, media access remains catalog-ID-scoped, and source bytes remain
  in place unless the operator explicitly chooses another supported workflow.
- This was a frontend information-architecture change over existing portal
  endpoints. It did not broaden backend capabilities or change the documented
  catalog, audit, privacy, or recovery semantics.

### Remaining gate

The redesign brief remains **IN PROGRESS** until the revised static asset tests,
responsive and keyboard/focus smokes, real-audio A/B checks, multi-target
execution checks, and operator acceptance are recorded. In particular, verify
the single-canvas layout at the operator's normal viewport and zoom, exercise
multi-select capacity/error handling, switch and swap candidates during
playback, confirm Master remains selected through terminal task state, and
inspect the same operation through Activity > Event log and History.
