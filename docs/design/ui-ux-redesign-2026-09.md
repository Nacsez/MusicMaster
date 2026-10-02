# UI/UX redesign — September 2026

Status: **IMPLEMENTED — automated regression and populated headless-browser
evidence are complete; native-picker, real-codec, listening, zoom, and full
assistive-technology operator acceptance remain**

Date: **2026-09-03**

This document defines the user-interface and interaction-design pass for
the private Music Mastering Tools workbench. It records the evidence behind the
change, the target information architecture, the interaction rules that should
guide implementation, and the evidence required before the redesign can be
called complete.

It remains the design and delivery contract, not a substitute for the remaining
human listening and accessibility checks. The three-workspace frontend
structure described here is implemented; current operator behavior is documented in the
[private workbench and catalog guide](../user/workbench-and-catalog.md), and the
[implementation status register](../status/implementation-status.md) remains
the authority for shipped status.

## Context

The mastering, catalog, validation, run, artifact, and recovery services are
working well. The audited frontend exposed those capabilities through seven
top-level tabs, a second four-section job navigator, several wide tables, and a
persistent audition dock. The result is technically complete but operationally
fragmented: users must understand the application's internal domains before
they can complete the musical workflow.

This pass follows the earlier
[GUI workflow pass](../development/gui-workflow-pass-2026-07-27.md). That work
successfully added batch inputs, source-centered master versions, native path
pickers, recoverable version management, and in-application audition. The next
pass must retain those capabilities while making the workflow—not the backend
schema—the organizing principle.

## Audit evidence

The audit covered `index.html`, `app.js`, `app.css`, relevant product and user
documentation, the portal API adapter, and the static frontend regression
tests. The following observations describe the pre-redesign packaged-asset
baseline inspected on 2026-09-03:

- The application has seven top-level workspace tabs: Dashboard, Master Job,
  Music Library, Reference Sets, Runs & Artifacts, Live Events, and Diagnostics
  & Help.
- Master Job adds a second navigation layer with Sources, Outputs & Preview,
  Processing, and Safety & Audit.
- The HTML contains 90 buttons, 53 inputs, 25 selects, 16 tables, 11 fieldsets,
  50 paragraphs, 36 `small` help elements, and 21 disabled controls.
- Persistent chrome includes an application header, command bar, top-level tab
  bar, bottom status bar, and an optional A/B dock. This materially reduces
  usable height on laptop displays.
- At viewport widths of 1460 px and below, the Music Library source inventory
  and selected-source details stack. On a common 1440 px display, the selected
  versions and comparison controls can therefore appear below a long source
  list rather than beside it.
- The source inventory has nine visible columns and a CSS minimum table width
  of 1080 px. Important review actions compete with paths, roles, counts, and
  technical state.
- `app.css` is 4,449 lines with 15 media-query blocks. Several major selectors,
  including the application header, command bar, workspace, job layout, job
  navigation, and audition dock, have multiple generations of definitions and
  late overrides.
- Starting a dry run or render moves the user from Master Job to Dashboard.
  The task display summarizes a batch in prose even though the terminal task
  result contains per-target records and live events include target identity.
- Failed validation always selects Safety & Audit, even when the actionable
  issue concerns a source, output destination, deliverable, or processing
  setting.
- With multiple mastered versions, the source-row action tells the user through
  a toast to choose a version in a separate detail table. The A/B dock itself
  cannot select candidates or swap them.
- A generic Play action replaces whichever audition slot is active. The user
  can therefore disturb a prepared comparison without making an explicit slot
  assignment.
- Reference profiles are managed through a toolbar in Master Job, a separate
  top-level Reference Sets page, and a modal member editor. These are three
  interaction patterns for one concept.
- Pre-redesign static tests encoded the seven-tab structure and several exact DOM
  IDs, class names, function boundaries, and source substrings. Those tests are
  valuable regression evidence, but layout-specific assertions must change
  deliberately with the redesign.

These counts are not quality targets by themselves. They show that the default
interface presents a large portion of the underlying feature model at once and
that another layer of cosmetic compression will not solve the navigation
problem.

## Problem statement

The audited UI asked the operator to navigate among implementation domains:
catalog, reference sets, job configuration, task events, runs, and diagnostics.
The actual operator journey is simpler:

```text
compose → check → run → monitor → review
```

The redesign must make that journey continuous. Catalog maintenance,
reference-profile reuse, audit evidence, and diagnostics should appear in
context when the journey needs them, without hiding the application's truthful
audio and safety semantics.

## Goals

1. Let an operator compose, check, start, and monitor a normal batch from one
   Master workspace.
2. Make the required choices—inputs, reference profile, deliverable, and
   destination—visually primary.
3. Move uncommon processing, safety, provenance, and capability detail behind
   intentional progressive disclosure.
4. Make original/master and master/master comparison direct, visible, and easy
   to change without losing the playhead.
5. Keep source-centered master lineage, no-overwrite behavior, recoverable
   lifecycle operations, and complete audit evidence intact.
6. Give background progress and failure detail a persistent, contextual home
   rather than requiring navigation among Dashboard, Events, and Runs.
7. Reduce visible prose and use hierarchy, placement, state, and concise labels
   to teach the workflow.
8. Replace accumulated CSS overrides with a coherent, testable layout system.
9. Preserve keyboard access, visible focus, clear state announcements, and
   readable layouts across the supported workstation sizes.
10. Update process documentation and regression coverage as first-class parts
    of the implementation.

## Non-goals

This redesign does not authorize changes to mastering algorithms, catalog
identity, run manifests, or output safety policy. It also does not claim or
simulate capabilities the backend does not provide, including:

- loudness-matched, resampled, or transcoded browser audition;
- waveform or spectral-image generation;
- cooperative cancellation;
- parallel batch execution;
- persistent unfinished job drafts across portal restarts;
- reference-analysis caching that is not already implemented;
- one-target/many-alternative-reference rendering as one batch operation; or
- remote, multi-user, cloud, upload, or public-network operation.

New backend work may later support some of these features, but it must receive
its own contract, requirements, tests, and documentation. The frontend must not
imply support in advance.

## Design principles

### Workflow before feature inventory

Primary navigation represents user goals. Backend concepts appear within the
goal they support.

### Essentials first

The default Master view exposes only what a normal render needs. Advanced
controls remain available and technically precise, but they do not dominate the
initial composition surface.

### Musical identity before technical identity

Track name, version, deliverable, duration, and availability lead. Paths,
content IDs, hashes, run IDs, and full provenance remain accessible in details
and History.

### One concept, one primary control

There is one primary place to run a job, one place to choose A/B candidates,
one saved-profile manager, and one persistent activity entry point. Duplicate
commands may exist only when they are true shortcuts and do not compete for
attention.

### Explicit state

Selection, comparison assignment, export selection, validation state, and
operation state must look different. Generic row highlighting or checkboxes
must not carry several meanings at once.

### Truthful progressive disclosure

Moving technical detail behind a disclosure is allowed; removing the ability to
inspect it is not. Unsupported capabilities belong in a compact capability or
help view, not as disabled controls in the main workflow.

## Target information architecture

The top-level application has three workspaces.

| Workspace | Primary purpose | Includes |
|---|---|---|
| **Master** | Compose, check, start, and monitor current work | Inputs, processing order, reference profile, saved profiles, delivery, destination, advanced settings, validation, dry run, render, and current per-target progress |
| **Library** | Manage and review music | Add/search/filter sources, selected-source maintenance, mastered versions, A/B review, review queue, export selection, discard/restore, verify/relink, and archive/restore |
| **Activity** | Inspect work and troubleshoot | History, run artifacts, manifest summary/raw data, manifest recovery, the current session's event log, diagnostics, capabilities, and retained logs |

Dashboard is removed as a destination. Its useful current-operation summary
moves into the compact header state and Master readiness rail.

Quick reference-profile selection and saving move into Master. The complete
**Reference profiles** manager opens as a utility view from that panel or the
application menu while Master remains the active primary context.

Runs & Artifacts and Live Events move under **Activity** as **History** and
**Event log**. The current event stream remains a session projection; durable
JSONL artifacts remain authoritative after restart.

Diagnostics & Help becomes **System**, a utility view under Activity and a
direct application-menu destination. Environment health may be visible as a
compact status, but capability tables, logs, local paths, keyboard help, and
current limits are not primary navigation destinations.

The three workspaces are application destinations, not one semantic tab set.
Navigation should therefore use links or buttons with an `aria-current`
indicator. Tabs remain appropriate for closely related views of one selected
run, such as Artifacts, Manifest Overview, and Raw Manifest.

## Application shell

The target desktop shell is:

```text
┌ Music Mastering Tools ─────── Master | Library | Activity ─── Status ┐
├─────────────────────────────────────────────────────────────────────────┤
│ active workspace                                                        │
│                                                                         │
│                                              contextual readiness/activity│
├─────────────────────────────────────────────────────────────────────────┤
│ A/B transport, only while comparison candidates are populated          │
└─────────────────────────────────────────────────────────────────────────┘
```

Rules:

- Combine the current header, command bar, tab bar, and status bar into one
  compact application header plus workspace content.
- Put import, backup, open-workspace, and shutdown commands in one application
  menu.
- Keep health and current-operation state visible through concise indicators.
- Do not reserve space for the comparison transport until it is populated.
- Preserve source/job state while navigating among workspaces during the
  portal session.
- Warn before an action intentionally clears a non-empty job draft.

## Unified Master workflow

### 1. Compose music

Inputs and Reference Profile are the first visible region. At wide workstation
sizes they appear side by side; at narrower sizes they stack while retaining
their state and order.

#### Inputs

- Label the queue as **Inputs** and its ordering as **Processing order**.
- Open a searchable, multi-select catalog chooser rather than requiring one
  dropdown selection and one Add action for every existing track.
- Include **Add files…** in the same chooser. The existing native picker and
  catalog fingerprinting flow remain authoritative.
- Show compact rows with track name, essential format/duration state, audition,
  and ordering/removal actions.
- Preserve accessible move earlier/later controls even if pointer users also
  receive drag handles.
- Show unavailable or archived tracks as actionable exceptions rather than
  allowing them to remain silently stale in the queue.

#### Reference profile

- Use the same searchable multi-select pattern for cataloged references.
- Put **Saved profile** selection and **Manage profiles** in this panel.
- Loading a profile replaces the current reference list only after confirmation
  when the list is non-empty, retaining the current safety behavior.
- When one reference is present, show the derived Matchering compatibility path
  and fixed effective weights without rendering editable weight controls.
- When two or more references are present, show level and tonal contribution
  controls with normalized shares. Keep the precise level/frequency terminology
  available through labels or tooltips.
- **Equal weights** and **Save profile** remain contextual actions in this
  panel.
- Do not label Inputs and References as A and B; those labels belong exclusively
  to audition comparison.

### 2. Choose delivery

The next always-visible row contains:

- the normal limited PCM-24 master preset;
- an **Additional outputs…** disclosure for normalized, raw floating-point, and
  paired preview options; and
- the required destination path with Choose, Use default, and Make default
  behavior.

The destination remains visible because it is structurally required. The
exclusive per-target subfolder and no-overwrite rule should be summarized at
confirmation time and in contextual help rather than repeated under every
control.

### 3. Adjust advanced settings when needed

Processing and Safety move into an **Advanced settings** drawer or disclosure
with clear sections:

- Sound and analysis
- Limiter
- Input policies
- Detection thresholds
- Audit notes

Supported controls retain their exact values and names. Values that differ from
defaults receive a compact changed-state indicator. Reset Defaults lives here
and continues to preserve selected music and notes according to the documented
contract.

Planned output controls, planned workflow cards, managed internal paths, and
read-only statements such as generated job ID do not remain as form controls.
Their truthful information belongs in capability help, validation results, or
History.

### 4. Check and run from one readiness rail

A sticky rail or compact bottom action region summarizes:

- input count and availability;
- reference count/profile and derived engine;
- selected deliverables;
- destination;
- whether settings differ from defaults; and
- validation state: not checked, stale, checking, passed, or blocked.

It owns the only primary job actions:

- **Check setup**
- **Audited dry run**
- **Render** or **Render batch**

Render remains protected by the backend's authoritative audio-aware preflight.
The UI may recommend Check Setup or a dry run, but it must not imply that a
previous client-side status replaces render-time validation.

When validation returns issues, group or route them to Music, Delivery, Sound,
or Safety. A Fix action focuses the relevant control or opens the relevant
advanced section. Do not send every failure to Safety & Audit.

Any job edit invalidates the previous check result visibly. Validation remains
non-materializing: it must not be described as having created job, selection,
run, event, or output artifacts.

### 5. Monitor without leaving Master

Submitting a dry run or render must not navigate away from the job. The
readiness rail becomes a live operation view:

- overall queued/running/terminal state;
- current stage;
- current target and position in the batch when events provide it;
- one row per input with waiting, running, succeeded, or failed state;
- success/failure counts when terminal;
- concise error and remediation access; and
- links to Activity or the resulting History records.

Live per-target state can be projected from the submitted job targets and
structured task events. Terminal task results already contain per-target run
and error records. This presentation change does not require a new endpoint.

There is no Cancel action until a cooperative cancellation contract exists.
The absence should be explained once in contextual help, not represented by a
large disabled planned-feature card.

### 6. Continue to review

After a render reaches a terminal state, refresh Library and History and offer
**Review masters**. The action opens Library focused on the targets from that
operation. Partial failure must preserve successful results and identify failed
targets individually.

## Library model

Library retains the correct source-centered domain model: one stable original
row with zero or more run/version children. Failed runs, dry runs, previews,
partials, and audit files remain in History and do not become master versions.

### Layout

At normal laptop and desktop widths, Library uses two panes:

```text
┌ Search / filters / Add audio ───────────────────────────────────────────┐
├ Sources ─────────────────────┬ Selected source ─────────────────────────┤
│ title, state, versions       │ primary actions                          │
│ compact scrollable rows      │ A/B comparison                           │
│                              │ mastered versions                        │
│                              │ technical details disclosure             │
└──────────────────────────────┴──────────────────────────────────────────┘
```

The source pane should show musical identity and review state rather than nine
always-visible columns. Recommended visible data is name, availability/status,
roles, active master count/latest state, and duration. Path, full track ID,
known locations, hashes, and other provenance move into the selected-source
details disclosure.

Do not stack the detail pane merely because the viewport is 1440 px wide.
Reduce source-table columns and use a deliberately narrower source pane so the
selected versions remain visible beside it. Stack only when two panes can no
longer provide readable controls.

### Actions

Selected-source actions use priority and grouping:

- Primary: Add to Batch, Use as Reference, Compare.
- Secondary **More** menu: Rename, Verify, Relink, Archive, or Restore.
- Version row: choose for comparison, choose deliverable, play, select for
  export, and More for Show, Discard, or Restore.

Archive and version discard remain distinct operations. Destructive-looking
actions retain explicit confirmation and wording about retained source audio
and audit evidence.

### Export selection

Enter an explicit **Select masters** mode before showing cross-song export
checkboxes. This prevents export selection from competing with row selection
in the normal review view.

The export tray shows selected count, suffix, clear, and Export. Exact
version/deliverable selection remains possible. Existing no-overwrite,
fingerprint verification, independent per-item results, and retry retention
remain unchanged.

## A/B and group review

The comparison surface owns its candidate choices. It is not only a transport.

### Candidate model

For a selected source, each slot chooser groups:

- Original
- Each active playable master version
- Each playable deliverable within a version

Defaults are A = Original and B = the latest/preferred playable master. When
several masters exist, changing B is one direct chooser action; the user does
not need a toast, a separate row-selection step, and another Compare action.

### Required controls

- Explicit A candidate chooser
- Explicit B candidate chooser
- Swap A/B
- A/B active toggle
- Play/pause and seek through the native audio control or equivalent accessible
  transport
- Previous/next reviewed version
- Clear comparison
- Visible assignment markers on version candidates
- A concise **Raw level** or equivalent indicator explaining that audition is
  not loudness-matched or transcoded

Switching candidates and A/B slots should preserve the current playhead when
the destination file is long enough, retaining the useful current behavior.
Swapping should keep the currently audible item audible while its slot label
changes, avoiding an unnecessary audio jump.

Quick Play must not silently overwrite an established A/B slot. It should be a
separate preview state or require an explicit slot assignment that is
immediately visible.

### Review queue

A client-session **Review queue** supports comparing groups of mastered
versions across sources. Adding a version to this queue is distinct from
selecting it for export. Previous/next moves through the queue while the other
slot may remain anchored.

The initial review queue is in-memory only. It must not be described as a saved
playlist or persistent catalog entity. The existing master-library response and
media route are sufficient; no backend mutation is required.

### Playback boundary

The browser player continues to request catalog track IDs through the
media-scoped route. It does not receive arbitrary file paths or API credentials.
The UI must remain honest that browser codec support may be narrower than the
mastering decoder and that playback does not normalize, resample, transcode, or
replace a controlled listening protocol.

## Reference profiles

Saved profiles are selected, loaded, and created from the Master reference
panel. Its **Manage** action opens the Reference profiles utility view without
adding a fourth primary workspace. The manager contains:

- searchable named-profile list;
- selected profile membership and normalized level/frequency contributions;
- Use in Job;
- create from current references;
- rename;
- edit complete ordered membership; and
- delete profile definition.

The existing atomic replace-members behavior remains. Deleting a profile does
not delete cataloged audio or alter prior runs. A modal may still be used for
focused member editing. Returning to Master must preserve the current draft.

## Activity and utilities

### Activity

Activity is the third primary workspace. Its local navigation groups:

- **History** for durable run, artifact, manifest, and recovery evidence;
- **Event log** for filtered current-session events and selected-event detail;
  and
- **System** for environment health, capabilities, limits, privacy, and logs.

The compact task state in the application header remains visible while moving
between workspaces. Master remains the primary place to monitor a submitted
job; Activity supplies deeper event and retained evidence when requested.

Clearing the in-browser event view continues to leave JSONL evidence intact.
The Event log must make that distinction concise and visible at the Clear
action.

Activity does not pretend to provide historical live events after portal
restart. Durable event logs remain run artifacts unless a future backend
endpoint explicitly provides parsed historical events.

### System utility

System contains:

- environment doctor and refresh;
- engine capability matrix;
- session logs and bounded log viewer;
- portal identity and private paths;
- keyboard help;
- supported behavior and current limitations; and
- workspace-folder access.

Healthy status should be quiet. Failures or warnings receive a visible header
indicator and a direct route to the failed check. Long capability explanations
belong here or in documentation, not in default job forms.

## Content design

Use prose only where it changes a decision or prevents a harmful
misunderstanding.

- One clear title per region; avoid repeating an eyebrow, heading, subtitle,
  card title, and helper paragraph for the same concept.
- Prefer state labels such as **3 inputs**, **2 references**, **Ready**, or
  **Needs destination** over instructional paragraphs.
- Put definitions and limits in tooltips or disclosures adjacent to the term.
- Keep confirmation dialogs specific about scope, retained evidence, and
  no-overwrite/recovery behavior.
- Use empty states to offer the next action, not to restate the entire workflow.
- Hide technical IDs and paths until requested, while preserving copyable full
  values in Details and History.
- Do not rely on an ephemeral toast as the only instruction for a required next
  step.

## Accessibility rules

The redesign must retain or improve the current keyboard and focus behavior.

- Primary workspace navigation exposes current location and has a logical tab
  order.
- Every pointer interaction has a keyboard equivalent.
- Multi-select choosers expose selection count, selected state, and clear
  labels to assistive technology.
- Reordering supports accessible Move Earlier and Move Later actions even when
  drag-and-drop is available.
- Dialogs and modal sheets trap focus, close predictably, and return focus to
  their opener. Non-modal drawers do not steal focus unexpectedly.
- Opening a validation issue's Fix action moves focus to the relevant control
  and announces the destination.
- Operation state, validation result, errors, and A/B active state use
  appropriately scoped live announcements without repeatedly reading the full
  event stream.
- State is not conveyed by color alone; text or icon shape accompanies it.
- Focus indicators remain clearly visible against every surface.
- Controls meet a comfortable workstation target size and expand for narrow or
  touch-oriented layouts.
- Truncated text has an accessible full value through a title, details view, or
  explicit copy action.
- Reduced-motion preferences disable nonessential transitions.
- Table semantics remain intact where the content is genuinely tabular.

## Responsive and viewport rules

The desktop application should use the viewport rather than an indefinitely
long document:

- The shell occupies one viewport height where practical.
- Workspace grids use `min-height: 0`; each long inventory owns its scroll
  region and sticky header.
- Required job actions remain visible while source queues scroll.
- The A/B transport never covers the final rows or action region; content
  reserves space only while it is open.
- Wide tables do not impose minimum widths on their surrounding panes.
- At wide sizes, Master shows input/reference composition plus readiness, and
  Library shows source/detail panes.
- At intermediate sizes, queues may stack and readiness may become a sticky
  bottom region or drawer. Selecting a library source must bring its detail into
  view immediately.
- At narrow sizes, use one content column, full-width sheets, and intentional
  omission of secondary columns. Preserve horizontal scrolling only for data
  that cannot truthfully be reduced.

Required visual regression viewports include at least 1280 × 800, 1440 × 900,
1440 × 1000, and a narrow layout. An operator pass must also cover the normal
workstation zoom and 125% zoom.

## Backend and security constraints

The redesign should reuse the existing API rather than create a parallel UI
model.

| UI capability | Existing authority |
|---|---|
| Catalog chooser and Library | `GET /api/tracks`, `GET /api/master-library` |
| Add and maintain sources | track add, label, verify, relink, archive, and restore routes |
| Job defaults and destination preference | job-defaults and preferences routes |
| Saved profiles | reference-set list/create/rename/replace/delete routes |
| Check, dry run, render | job validation, dry-run, and render routes |
| Per-operation monitoring | current task, task events, and terminal batch target results |
| Run evidence | run list, artifact inventory, and manifest routes |
| Master export/lifecycle | master-library export, discard, and restore routes |
| A/B media | catalog-ID media route with the scoped media cookie |
| Desktop integration | native dialog and open-folder routes |
| Environment support | diagnostics, capabilities, and bounded log routes |

Implementation must retain these boundaries:

- Browser code constructs DOM safely and does not insert untrusted HTML.
- Assets remain packaged and local; no CDN, telemetry, or remote metadata is
  added.
- API requests retain the session token header and same-origin protections.
- Audio remains referenced in place; adding a track fingerprints it and does
  not upload or copy it into a managed media store.
- A master version remains a completed run's exact `mastered-output` lineage,
  not any generated audio file.
- Multi-target requests remain independent sequential target jobs sharing one
  immutable reference request. One failure does not erase successful siblings.
- Output folders remain exclusive, collision-safe per-target children. Existing
  files are never silently reused or overwritten.
- Validation remains a preview and does not materialize run scaffolding.
- Version discard remains recoverable quarantine with retained source and audit
  evidence; source archival remains a different operation.
- Shutdown remains refused while mastering is active, and browser close does
  not cancel DSP.

## Frontend implementation guidance

The lowest-risk restructuring keeps existing control IDs for request-bearing
form fields while changing their containers and visual order. In particular,
`buildJobRequest()` and `applyDefaults()` already encode the supported backend
shape and should not be rewritten merely to support layout.

Areas requiring deliberate refactoring include:

- replace seven-tab activation with three-workspace navigation;
- replace one-at-a-time source dropdowns with a client-side multi-select
  chooser over loaded catalog data;
- split comparison assignment from transient playback;
- let the comparison dock select candidates and swap slots;
- render terminal and live per-target task state instead of only a summary
  paragraph;
- keep the submitted job visible instead of redirecting to Dashboard;
- fold reference-set management, events, and diagnostics into contextual
  surfaces; and
- separate job draft, library view, comparison, review queue, and activity
  state enough to avoid unnecessary full-panel rerenders.

`app.css` should be replaced or reduced coherently rather than extended with a
fourth override layer. The target stylesheet should have one token source, one
definition for each major layout primitive, content-driven breakpoints, and
shared compact row, drawer, state, and action patterns.

## Delivery process

### Phase 1 — characterize and protect

- Capture screenshots and interaction recordings of the current Master,
  Library, multi-version comparison, running batch, partial failure, and History
  flows.
- Record baseline behavior for job request construction, queue persistence,
  A/B playhead preservation, export selection, discard/restore, and task
  polling.
- Identify static tests that intentionally change and preserve security/API
  assertions that must not weaken.

### Phase 2 — shell and Master workspace

- Implement three-workspace navigation and the compact application shell.
- Build the unified composer, delivery row, advanced settings surface, and
  readiness rail.
- Implement the searchable multi-select catalog chooser.
- Keep task progress in Master and add per-target projection.

### Phase 3 — Library and comparison

- Reduce the source inventory and implement the stable two-pane review layout.
- Separate normal row selection, comparison assignment, review queue, and
  export mode.
- Implement direct A/B candidate selection, swap, version stepping, explicit
  assignment, and raw-level disclosure.

### Phase 4 — contextual secondary tools

- Put quick saved-profile use in Master and its complete manager in the
  Reference profiles utility.
- Group History, Event log, and System beneath Activity.
- Keep diagnostics, logs, limits, and keyboard help in System.
- Retain run evidence and recovery in History.

### Phase 5 — harden and document

- Replace layout-specific static tests with behavior-oriented contracts.
- Run lint, type, application, HTTP, asset, native-dialog, and GUI smoke suites.
- Complete keyboard, zoom, responsive, real-codec playback, batch partial
  failure, export collision, discard/restore, and relink operator passes.
- Update the user guide, implementation status, screenshots, and this document
  with final evidence.

Each phase should remain reviewable and runnable. Temporary compatibility code
must be identified for removal rather than becoming another permanent style or
navigation layer.

## Acceptance criteria

The redesign is not complete until all applicable criteria have implementation
and test or operator evidence.

| ID | Criterion |
|---|---|
| UX-001 | The application exposes exactly three primary workspaces: Master, Library, and Activity. History, Event log, System, and Reference profiles are subordinate views rather than additional primary workspaces. |
| UX-002 | A user can select existing inputs and references, set delivery and destination, check, dry-run, render, and monitor the operation without leaving Master. |
| UX-003 | Several eligible catalog tracks can be added to an input or reference queue through one searchable multi-select interaction. |
| UX-004 | The normal Master view presents required choices before advanced processing and safety controls, and no planned capability is rendered as a disabled primary-workflow control. |
| UX-005 | There is one primary location for Check Setup, Audited Dry Run, and Render actions. |
| UX-006 | Any edit after validation visibly marks the result stale. Validation issues route to the correct Music, Delivery, Sound, or Safety context. |
| UX-007 | Starting work preserves the visible job context and shows overall plus per-target progress/result, including partial failure. |
| UX-008 | Render completion provides a direct Review Masters path focused on the submitted targets. |
| UX-009 | At 1280 × 800, required Master actions remain reachable without scrolling past advanced configuration, and long queues scroll within their region. |
| UX-010 | At 1440 px width, the selected Library source and its versions remain visible beside the source list rather than being forced below it. |
| UX-011 | The normal Library source inventory prioritizes musical identity and review state; full paths, hashes, and technical IDs are available through Details or History. |
| UX-012 | Export selection is an explicit mode and is visually distinct from source selection, comparison assignment, and the review queue. |
| UX-013 | For a source with several masters, the user can change the B version/deliverable in one direct chooser action. |
| UX-014 | The comparison surface supports explicit A and B candidate selection, Swap, active-slot switching, and visible slot assignment without requiring navigation through source details. |
| UX-015 | A/B candidate switching preserves the playhead when possible, and Quick Play cannot silently replace a prepared comparison slot. |
| UX-016 | The player states that audition is raw-level/browser-decoded and does not claim loudness matching, resampling, or transcoding. |
| UX-017 | Saved profiles can be created, edited, loaded, renamed, and deleted without becoming a fourth primary workspace or losing the current Master draft. |
| UX-018 | Current structured events and per-target operation detail are reachable from every workspace; clearing the view cannot imply deletion of durable evidence. |
| UX-019 | Environment failure is visible from the shell and opens directly to the failed diagnostic; healthy detailed diagnostics remain quiet. |
| UX-020 | All primary workflows are operable by keyboard with visible focus and appropriate state announcements. |
| UX-021 | Responsive and zoom smoke tests show no clipped primary actions, obscured final rows, or inaccessible comparison transport. |
| UX-022 | Existing API, local-only asset, authenticated request, catalog identity, source-centered lineage, no-overwrite, and recoverable lifecycle contracts continue to pass. |
| UX-023 | The user guide, implementation status, regression evidence, and this design record reflect the shipped behavior before the work is marked complete. |

## Implementation evidence — 2026-09-03

The delivered frontend now has three primary workspaces, one continuous Master
canvas, searchable multi-select source pickers, required-before-advanced
progressive disclosure, source-of-truth readiness checks, inline per-target task
state, terminal partial-failure reporting, and a direct **Review masters** path.
Library uses a stable source/detail workspace and compact responsive version
cards. The persistent audition dock exposes every playable library item and
master deliverable directly in A and B, preserves custom cross-track pairs,
keeps Quick Play separate, swaps without moving the audible media or playhead,
and rejects stale rapid-load callbacks. Activity presents the narrower run
inventory as compact semantic-table cards rather than forcing desktop
horizontal scrolling. Dialogs support guarded Enter submission, destructive
actions begin on a safe focus target, and contextual control names plus focus
restoration survive dynamic list rerenders.

The current mastering operation is not only a browser-memory association:
submitted targets are reconstructed from retained task results, prepared-job
data, or per-target events after a reload, preserving terminal target state and
the direct **Review masters** route whenever that evidence is available.

Automated evidence includes 14 packaged-asset contracts, the complete 228-test
application gate (227 pass, one platform skip), and isolated populated Chromium
interaction runs at 1440 × 1000 and 1280 × 800. The browser audit exercises a
source with two master versions, direct candidate choice, swap/playhead
continuity, keyboard focus through DOM rerenders, responsive action reachability,
dialog Enter behavior, Activity overflow/toast containment, completed-task
reconstruction on a fresh page, runtime-error capture, token redaction, and
graceful shutdown. The final 31-check record is retained at
[`verification-summary.json`](../../artifacts/ux-populated-smoke/20260903T234045034Z/verification-summary.json).
CSS and JavaScript stay local and the portal/backend API contract did not
change.

The remaining acceptance work is deliberately physical/operator-facing:
Windows native-picker use, playback through the operator's installed codecs,
real mastering duration and partial-failure observation, 200% zoom, screen
reader behavior, and the documented listening protocol. These are not treated
as frontend implementation blockers, but they remain release evidence gaps in
the implementation status register.

## Required test updates

The former static asset suite explicitly required seven linked workspace tabs.
It now asserts the three semantic primary destinations and subordinate utility
views; no compatibility-only navigation was retained to satisfy the old test.

Tests should continue to enforce:

- no duplicate IDs;
- all literal JavaScript selectors resolve;
- safe DOM construction with no untrusted HTML insertion or dynamic
  evaluation;
- local packaged assets only;
- authenticated JSON requests and every supported API family;
- exactly one audio element using the catalog media route;
- batch target arrays, output destination, preference, and no-overwrite
  contracts;
- source-centered master/version lineage;
- strict export, discard, restore, and recovery behavior; and
- responsive scroll ownership and visible focus.

New behavior-level tests should cover the acceptance criteria instead of
depending on function source substrings or the previous container class names.
Headless smoke evidence should be supplemented by an interactive workstation
pass because native pickers, actual codecs, focus return, audio continuity,
zoom, and long-running DSP cannot be fully established through static asset
inspection.

## Resolved implementation decisions

1. Compact per-target progress stays inline in Master; Activity holds the deep
   event and durable evidence views.
2. Advanced processing and safety use an inline disclosure so required controls
   remain above it and reading order stays predictable.
3. Quick Play uses a separate temporary preview state and cannot mutate A or B.
4. Master source queues stack at full working width. Library keeps its
   source/detail split at ordinary desktop widths and turns version rows into
   responsive cards inside the detail pane.
5. Direct candidate selectors were preferred over previous/next buttons: they
   support arbitrary cross-track comparison while keeping the dock compact.

One presentation question remains for operator acceptance: whether the `1`/`2`
A/B shortcuts need an additional visible keyboard-help treatment beyond their
accessible shortcut metadata.

Any choice that alters backend semantics, persisted data, audio processing, or
security boundaries is not an implementation detail and requires separate
design review.
