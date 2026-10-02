# Private workbench and catalog guide

Status: **operator guide for the Windows workbench and executable preparation**

For the packaged build, double-click `MusicMasteringTools.exe`; Python is
bundled and mutable state defaults to the current Windows user's
`%LOCALAPPDATA%\MusicMasteringTools\workspace`. Source-checkout examples below
use `private-workspace/`. Substitute the packaged workspace path when locating
the same catalogs, preferences, logs, and runs. See the
[Windows release guide](../deployment/windows-release.md) for executable
diagnostics, backups, independent-machine acceptance, and moving/relinking audio.

Normal EXE/desktop launches exit after the last workbench tab closes and a short
refresh grace period. Any active mastering operation finishes first; refresh,
another tab, or a hidden/minimized browser retains the session. The source
launcher, `--no-browser`, and `--keep-running` modes remain persistent. See the
[Windows quick start](windows-quick-start.md) for recipient instructions.

This guide covers the GUI-first path from private audio files to one or several
audited masters driven by one or several references. It also explains every
workspace and utility view, what the source-centered music library stores, how
completed masters become versions of an original, how to audition
original/master pairs inside the application, how to export or recoverably
discard selected masters, how output-folder defaults and job overrides work,
how selections are exported and reimported, how weighted references are
combined, and where to find every per-target run artifact.

The graphical workbench is a local browser interface backed by the same Python
application, catalog, validation, and mastering services used by the CLI and
terminal fallback. It does not upload audio, query online metadata, copy source
songs into a managed library, or delete source audio.

## First launch from source

From Windows Explorer, double-click
[`Launch-Music-Mastering-Tools.cmd`](../../Launch-Music-Mastering-Tools.cmd).
The launcher:

1. changes to this repository regardless of Explorer's current directory;
2. creates `private-workspace/logs/` and starts a timestamped transcript;
3. checks `.venv`, Python 3.11, this package, and Matchering 2.0.6;
4. asks for an explicit `YES` before bootstrapping missing base dependencies;
5. runs strict mastering dependency diagnostics; and
6. starts `mmt gui` on `127.0.0.1` using a randomly available port and opens
   its token-bearing private URL in the default browser.

If environment setup or diagnostics fail before portal handoff, the window
stays open and shows the newest `private-workspace/logs/launcher-*.log`; it
contains the environment probe, doctor output, and first error cause. After
handoff, use the live window and newest `portal-*.log` for portal failures
because the launcher transcript has already closed.

The portal also writes `private-workspace/logs/portal-*.log`. Keep the launcher
window open: it owns the portal process and displays the private launch URL if
the browser cannot be opened automatically. During a normal successful launch,
the live console and portal log record only the origin and log path, not the
token-bearing URL. The launcher closes its transcript immediately before
portal handoff. The private URL is printed only when `-NoBrowser` is selected
or automatic browser opening fails, so it remains live-console-only. HTTP
request logs replace the token with `[REDACTED]`.

Useful non-interactive launcher checks from PowerShell are:

```powershell
.\Launch-Music-Mastering-Tools.cmd -CheckOnly -NoPause
.\Launch-Music-Mastering-Tools.cmd -CheckOnly -SkipBootstrap -NoPause
.\Launch-Music-Mastering-Tools.cmd -NoBrowser
.\Launch-Music-Mastering-Tools.cmd -Terminal
```

`-SkipBootstrap` verifies that an already prepared environment works and never
attempts package installation. `-CheckOnly` retains its original behavior and
does not start either interface. `-NoBrowser` starts the portal but leaves URL
opening to the operator. `-Terminal` selects the console workbench explicitly.

You can also start the portal from an activated project environment:

```powershell
mmt gui --workspace .\private-workspace
mmt gui --workspace .\private-workspace --no-browser
```

The `mmt portal` spelling is an alias for `mmt gui`.

## Portal map

The header has three primary workspaces. They follow the operator's normal
sequence instead of mirroring the application's internal services:

| Workspace | Purpose |
|---|---|
| **Master** | Build, check, start, and monitor a batch on one continuous canvas. **Music**, **Delivery**, and **Review & run** are visual landmarks, not separate tabs. |
| **Library** | Keep one row per original source; filter and rename tracks; inspect attached master versions; compare any playable versions; batch-export selected masters; recoverably discard or restore bad takes; and add, verify, relink, archive, or restore source records. |
| **Activity** | Open **History** to inspect runs, artifacts, and manifests. Its local navigation also opens the **Event log** and **System** utility views. |

Less frequent tools remain available without becoming primary workspaces:

| Utility view | How to open it | Purpose |
|---|---|---|
| **Reference profiles** | In **Master**, choose **Manage** beside Saved profile, or open the application menu and choose **Reference sets**. | Create, rename, reorder, independently weight, reuse, and delete named reference sets. **Back to master** returns to the active job. |
| **Event log** | Open **Activity** and choose **Event log**, or use the application menu. | Follow ordered operation events and filter them by text, level, or stage. |
| **System** | Open **Activity** and choose **System**, or choose **System health & logs** from the application menu. | Run the environment doctor, inspect engine capabilities and current limits, review privacy information, and read retained session logs. |

The application menu also holds infrequent session and catalog actions such as
New job, refresh, catalog import/export, selection export, opening the private
workspace folder, and safe portal shutdown.

Source queues and libraries use compact table rows rather than a card for every
file. The original list, selected-original details, and mastered-version list
are rectangular boxed sections with their own scroll viewports and sticky
headers. At laptop widths the detail section moves below the full-width source
list instead of squeezing both panes; wide tables scroll horizontally rather
than clipping columns or turning each file into a tall capsule. The interface
supports keyboard focus navigation; use Tab/Shift+Tab between controls, arrow
keys across workspace and run-view navigation, Enter/Space to select a row,
Shift+Space to toggle a
row's preferred master for export, and Escape to close a dialog.

The normal Master canvas shows the required choices first. Additional output
formats and paired previews are collapsed under **Additional formats & paired
preview**; supported processing, safety policies, execution facts, and private
notes are collapsed under **Advanced processing & safety**. Capabilities that
the current engines cannot run are not presented as usable options. Their
status remains explicit in **System** and in validation; the portal never
silently substitutes another behavior.

## Native file and folder dialogs

File actions open Windows WinForms pickers instead of asking for paths in a
terminal:

- audio pickers can select one file or several files;
- purpose-specific JSON pickers handle catalogs, selection snapshots, job
  configurations, and run manifests;
- save dialogs propose a `.json` name and retain overwrite confirmation; and
- folder pickers choose a directory. **Open workspace** opens its contents,
  while **Show in folder** selects the exact existing file in Explorer; neither
  action executes an artifact. A missing file is reported explicitly.

The browser sends only the chosen local paths to the loopback application. It
does not receive or upload audio bytes. The PowerShell/WinForms adapter uses
fixed scripts and passes paths as data; it does not execute user-provided
script text. Cancelling a native dialog leaves the application state unchanged.

Pickers start at the current job output or relink path when one is supplied.
Otherwise they use the last successful directory for that picker purpose,
retained in the workspace's versioned `dialog-locations.json`. Initial defaults
are the workspace root for audio, the preferred delivery folder for folders,
`exports/` for catalogs/selections, `jobs/` for configurations, and `runs/` for
manifests. Cancelled dialogs preserve history; unavailable drives, removed
directories, and malformed history fall back with retained diagnostic detail.
Folder history is separate from the strict delivery-default preferences.

### Local portal security boundary

Each launch chooses an available port and a fresh high-entropy session token.
The initial token-bearing URL authorizes the page; API requests then send the
token in a private header. Requests must arrive through loopback with the
expected Host and same-origin Origin/Referer when present. Cross-origin OPTIONS
requests are refused.

Responses disable caching, framing, MIME sniffing, and referrer leakage. A
Content Security Policy permits only packaged same-origin scripts/styles,
token-nonced bootstrap code, local API connections, and local/data images.
There are no CDN or other external frontend assets, and the application does
not use Electron. JSON POST bodies are strict, reject duplicate keys and
non-finite constants, and are limited to 1 MiB.

Native browser audio elements cannot attach the private API header. The initial
authorized page therefore receives a second random `HttpOnly`,
`SameSite=Strict` cookie scoped only to `/media/tracks/`. That cookie cannot
authorize an API. The media route accepts only one URL-encoded catalog track
identifier, resolves only the catalog's preferred location, rejects query
parameters and nested paths, verifies regular-file size and modification time,
and supports bounded byte-range streaming for seeking. Both session secrets
and arbitrary query values are redacted from request logs.

These controls reduce accidental browser/LAN exposure; they do not turn the
portal into a public or multi-user service. It intentionally has no TLS,
accounts, remote authentication, upload store, rate-limited public API, or
tenant isolation. Do not reuse its server behind another bind address or proxy.

### Session Logs

In **Activity** > **System**, **Session logs** lists retained launcher and portal
`.log` files with their size and modification time. Select one to inspect its
text without leaving the GUI, refresh the list after a failure, or use **Show
in folder** to select it in Explorer. The viewer is confined to plain `.log`
files under `private-workspace/logs/` and displays at most the newest 256 KiB;
it seeks and reads only that bounded tail rather than loading a large log into
memory, and labels the view when older content was truncated.

Normal portal startup records only the origin, and HTTP access logging replaces
the session token with `[REDACTED]`. An explicit `-NoBrowser` launch or failed
automatic browser open must print the private URL so the operator can open it;
the launcher transcript is already closed, so the URL is not retained there.
Treat the live console URL as a session capability until the portal exits.
Session logs can still contain private paths, labels, and exception detail, so
do not publish or commit them.

## Private workspace layout

The GUI portal and terminal fallback share this ignored local layout:

```text
private-workspace/
  portal-preferences.json    # versioned default delivery-folder preference
  dialog-locations.json     # versioned directory history per picker purpose
  catalog/
    catalog.sqlite3          # mutable track, set, run, and artifact index
  jobs/
    <run-id>.json            # immutable job configuration
    <run-id>.selection.json  # reimportable target/reference selection
  runs/
    <run-id>/
      manifest.json          # authoritative per-run record
      events.jsonl           # ordered structured diagnostic events
  outputs/
    <target>-master-<run-id>/ # default masters and optional paired previews
  trash/
    master-versions/         # recoverably discarded master audio + tombstones
  tmp/                       # job-scoped temporary processing files
  exports/                   # default catalog export destination
  logs/                      # launcher transcripts and per-session portal logs
```

The SQLite catalog is a mutable search index. A committed run manifest is the
authoritative immutable record for that execution. Keep both when preserving
evidence. `outputs/` is only the initial delivery root: a saved default or
one-job override may place the per-target output subfolders elsewhere. Back up
that external delivery root separately from `private-workspace/`. Keep
`trash/master-versions/` with the database and run records while you may still
want to restore a discarded take.

## Prepare the music library

Open **Library**.

- Add target/input tracks for mixes that will be mastered.
- Add reference tracks for finished songs whose level or tonal profile should
  guide a master.
- Add a track as both when either role is legitimate.
- Use **Add audio…** and the native multi-file picker, choose the applicable
  target/reference roles, and add optional private labels.
- Use **Original** to play a source immediately, or **Compare** to prepare its
  original/master pair in the persistent A/B dock.
- Use **Verify source files** or the selected track's **Verify source** action after
  moving or replacing files.

Adding a track streams a SHA-256 fingerprint and audio facts. It does not copy
the file. Identical bytes at multiple paths become one content identity with
multiple locations. A path whose bytes later change is marked `changed`; it
does not silently replace the earlier track identity.

Track IDs begin with `trk_sha256_` and are intentionally content-derived.
Labels, absolute paths, hashes, and audio facts are sensitive private metadata.

### Command-line catalog maintenance

Commands default to `.mmt/catalog/catalog.sqlite3` under the current directory.
To operate on the same catalog as the double-click workbench, pass its database
explicitly:

```powershell
$catalog = ".\private-workspace\catalog\catalog.sqlite3"

.\.venv\Scripts\mmt.exe catalog --database $catalog init
.\.venv\Scripts\mmt.exe catalog --database $catalog add `
  --role target .\audio\mix.wav
.\.venv\Scripts\mmt.exe catalog --database $catalog add `
  --role reference .\audio\reference-a.wav .\audio\reference-b.wav
.\.venv\Scripts\mmt.exe catalog --database $catalog list --include-archived
.\.venv\Scripts\mmt.exe catalog --database $catalog verify --include-archived
```

Use `--json` on list/show/verify commands when another tool will consume the
result.

### Moved, missing, and replaced files

`verify` rehashes known locations and records one of these states:

- `available` — the path still contains the expected bytes;
- `missing` — no file is present;
- `changed` — the path now contains different bytes;
- `unreadable` — the file could not be fingerprinted; or
- `archived` — the catalog record was intentionally hidden.

If a song moved without changing:

1. select it in **Library**;
2. choose **Relink…**;
3. select the byte-identical file at its new location; and
4. review the returned location state.

The equivalent CLI operation is:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog relink `
  <track-id> D:\NewLocation\same-song.wav
```

Relink succeeds only when the new file's size and SHA-256 match the selected
track. It never accepts a merely similar filename.

Archive hides a track from normal selection but retains locations, reference
sets, run history, and artifacts:

Use **Archive original** in the selected track's detail section. Enable
**Archived originals** and use **Restore original** to return it to normal
selection. The equivalent CLI
commands are:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog archive <track-id>
.\.venv\Scripts\mmt.exe catalog --database $catalog restore <track-id>
```

Neither command deletes audio.

## Review and manage mastered versions

Library is source-centered: a non-generated target or reference appears once
as an original row. Rendering another master does not add another peer row.
Instead, a completed run whose immutable catalog selection names that target
becomes a dated version under the original. Failed runs, dry runs, previews,
partial outputs, and audit files remain visible in **Activity** > **History**
but are not counted as mastered versions.

The source row reports `Not mastered`, `Mastered`, a version count, or an
availability warning. Select it to open the version table. A version can contain
limited, normalized/no-limiter, and raw FLOAT deliverables; use its **Output**
chooser to select which one is played, compared, or exported. The limited
deliverable is preferred when available. Selecting an original with an active,
playable master automatically prepares a comparison without starting playback:

- **A** — the original catalog source; and
- **B** — an available mastered deliverable.

For a source-specific comparison, select the desired version and deliverable in
the version table and choose **Compare**. The persistent comparison dock also
has direct **Choose A** and **Choose B** selectors. They group every currently
playable original and master in the available library inventory, so A/B can be
original/master or master/master without returning to a separate version table.
Use the swap button to exchange the two candidates. Choose the A or B button,
or press `1` or `2` when focus is not in an editor, to switch the playing side.
Switching sides and changing a candidate retain the current playhead when the
destination file is long enough.

**Play** is a temporary preview and leaves both prepared comparison slots
unchanged. Use the explicit A or B controls when a track should become part of
the comparison.

The dock's **Raw level** label is literal: audition streams cataloged file bytes
and does not loudness-match, normalize, resample, or transcode the candidates.
A missing original disables original/master preparation and presents the relink
workflow. An unavailable or browser-unsupported master remains exportable and
revealable when its bytes are otherwise valid, but cannot be used by the browser
player.

### Track names and export suffixes

**Edit track name…** updates only the private catalog label used by the GUI and
future export names. It never renames the original file, changes its bytes or
content-derived track ID, or rewrites a committed manifest.

To copy several masters:

1. select the preferred master beside several original rows, or choose exact
   versions/deliverables in one original's version table;
2. edit the common **Export suffix** if needed;
3. choose **Export selected…** and a destination folder;
4. review the item count, destination, suffix, and no-overwrite warning; and
5. inspect the per-item completion report.

The export service reopens and fingerprints each selected catalog artifact,
creates a new copy named from the source's display label plus the suffix, and
verifies the copied hash and size. It never mutates the audited artifact and
never overwrites an existing destination. Items are independent: successful
copies are cleared from the selection, while collisions or copy failures remain
selected so the suffix or destination can be changed and retried.

### Discard and restore a bad take

**Discard…** is a recoverable version-level cleanup, not track archival and not
permanent deletion. After confirmation, it:

- verifies that the selected version belongs to the original and to a
  completed, portal-owned manifest;
- verifies every mastered deliverable against its indexed fingerprint;
- refuses a path shared by another active version or any source/reference;
- moves all mastered deliverables for that version into
  `private-workspace/trash/master-versions/`;
- retains the original, references, paired previews, configuration, selection,
  manifest, event log, and other audit evidence; and
- writes and indexes a tombstone describing every move and the restore request.

Enable **Discarded masters** to show the version and choose **Restore**. Restore
rehashes the quarantined files and moves them to their exact original output
paths. If any destination is occupied, restoration fails without overwriting
it. Keep the private recovery directory, catalog, and run evidence together;
deleting quarantine files outside the application removes the audio needed for
automatic restoration.

## Create a mastering job

Open **Master**. The job is one canvas with three visual stages: **Music**,
**Delivery**, and **Review & run**. They remain in one page rather than dividing
the job across another tab bar.

1. Under **Music** > **Mixes**, choose **Choose from library**. Search the
   available input tracks, select several checkboxes, and add them to the queue
   in one action. The chooser respects the remaining capacity; a job accepts one
   to 32 distinct targets. **Import files** opens the native multi-file picker.
   Reorder, remove, or audition queue rows as needed.
2. Under **Reference sound**, use the same searchable, multi-select library
   chooser, import files, or load a **Saved profile**. A job accepts one to 32
   references. With two or more, reorder or remove rows and edit independent
   level and tone weights. **Balance evenly** resets both dimensions. A lone
   compatibility reference is fixed at effective weights `1`/`1`, so its weight
   fields are disabled.
3. Preview a row immediately without disturbing a prepared pair, or explicitly
   assign it to A or B. The comparison dock stays available across the three
   workspaces and provides direct candidate selectors and a swap action.
   Switching sides preserves the playhead when possible.
4. Under **Delivery**, use **Change** to choose this job's destination.
   **Remember** persists the displayed absolute folder for future jobs and
   launches; **Default** restores that saved value without changing it. The
   visible primary deliverable is a limited PCM-24 WAV.
5. Expand **Additional formats & paired preview** only when the job also needs a
   normalized/no-limiter master, raw floating-point DAW handoff, or aligned
   before/after previews. At least one supported deliverable must remain chosen.
6. Keep **Advanced processing & safety** collapsed for a normal job. Expand it
   to review or change supported audio, matching, limiter, detection, edge-policy,
   evidence, and private-note fields. The server still derives the engine from
   the reference count, and unsupported capabilities fail validation rather
   than being approximated.
7. The **Review & run** rail marks the four prerequisites: mixes, references,
   destination, and a deliverable. Choose **Check setup** to decode and validate
   every queued target against the same reference request. Open **Validation
   details** only when you need errors, warnings, decoded audio facts, engine
   information, or proposed evidence paths. Validation does not create job,
   run, or output scaffolding.
8. Choose **Test run** for an audited dry run. Targets run sequentially. Each
   receives its own immutable configuration, one-target selection, event log,
   manifest, and run ID but does not render audio. Saved dry-run configurations
   truthfully record `execution.dry_run: true`; render configurations record
   `false`.
9. Choose **Render master** or **Render masters** when ready. Each render repeats
   preflight and writes every target into a new exclusive subfolder. The
   interface stays in **Master** while the worker runs; the header and **Review &
   run** rail show the active operation plus compact queued/running/complete/
   failed state for each submitted mix. Partial completion stays visible. When
    at least one final render succeeds, choose **Review masters** to open the
    completed source in Library. Open **Activity** > **Event log** only when
    deeper live detail is useful, then use **History** to inspect durable run
    evidence after completion.

If the portal is reloaded after a job has emitted per-target evidence or
completed successfully, it reconstructs the submitted mixes and their status
from the current task record. Completed renders therefore retain the direct
**Review masters** path instead of depending only on the original browser
session. A job that fails before recording any target identity can still show
its global failure, but cannot reconstruct that target row.

**Reset settings** preserves both music queues and private notes while restoring
the saved destination plus processing, output, preview, and safety controls.
**New job** in the application menu tracks settings-only edits as well as
source/notes changes and asks before clearing the entire form. Neither action
changes catalog tracks or named reference profiles.

The A/B dock is a convenience audition transport, not a level-matched listening
test. It streams the original catalog file and does not normalize, resample, or
transcode it. Browser playback support is narrower than the mastering decoder
boundary on some systems; if the player reports an unsupported source, the
file may still validate and render normally. Use a browser-supported source or
an already-rendered supported preview for in-app comparison.

The workbench selects engines conservatively:

| Selection | Engine | Current boundary |
|---|---|---|
| One reference | `upstream-matchering-2.0.6` | Exact pinned compatibility path; both weights are `1`; optional guarded EQ is unavailable |
| Two through 32 references | `music-mastering-tools-native` | Deterministic weighted Matchering-parity path; level/frequency weights are independent; optional `matching.max_eq_gain_db` is available |

Both current engines use Matchering loud-section RMS, a sample-peak Hyrax
limiter when limited output is requested, metadata policy `drop`, no dither,
full match amount `1`, and `max_workers: 1`. Both reject EBU R128, true-peak,
external limiter, metadata copying, dither, and partial match amount instead of
silently approximating them. Run `mmt capabilities` for the machine-readable
source of truth.

### Output choices

The portal proposes a limited PCM-24 distribution master and can also request:

- normalized/no-limiter PCM-24;
- raw FLOAT without limiter for a DAW handoff; and
- paired target/result PCM-16 previews from the same time region.

Every produced audio file is reopened before the run succeeds. The verifier
checks that it exists, contains frames, has only finite samples, uses the
requested sample rate/channel count/subtype, and has the expected preview
length where applicable.

The initial default is `private-workspace/outputs/`. A **Make default** action
atomically writes the strict
`music-mastering-tools/portal-preferences` schema version 1 document to
`private-workspace/portal-preferences.json`. The preference must contain an
absolute directory; a malformed, duplicate-key, unknown-field, or unsupported
version document is rejected rather than guessed.

Each dry run and render receives unique run-scoped job, run, temporary, and
output paths. Within the selected delivery root, every target receives an
exclusive `<target>-master-<run-id>/` subfolder with readable target-prefixed
filenames. The portal creates that subfolder with no-reuse semantics: a
collision fails safely and existing content is neither opened as a job folder
nor deleted during cleanup. The operator does not type output filenames, so an
existing source file cannot accidentally become a portal destination.
**Validate job** builds and checks the same proposed configuration in memory
without creating job/run/output scaffolding; durable configuration and
selection evidence begins with an accepted dry run or render.

## Background operations, completion, and shutdown

Dry runs and renders execute on one background worker. HTTP requests and the
browser remain responsive, but the application accepts only one mastering
operation at a time because the current engines and inherited process-global
dependencies require serialization. Within a multi-target operation, target
runs also execute in the displayed order rather than in parallel. A second
submission receives a busy response instead of being queued invisibly.

Operation state progresses through `queued`, `running`, and `succeeded` or
`failed`. The portal retains a bounded in-memory view of recent operations and
events while that portal process lives. The durable JSONL event file,
configuration, selection, and committed manifest remain the authorities after
restart.

A submitted dry run or render enters `MasteringService` even when audio-aware
preflight will fail. The service writes the failed manifest/event cause chain,
and the portal indexes that failed run and its evidence for **Activity** >
**History**. If processing fails before any authoritative manifest can be
created, the portal removes its uncommitted job/run/output scaffolding instead
of leaving an invisible attempt behind.

A target failure does not erase completed work or prevent the next independent
target from starting. The batch result reports `succeeded`, `partial_failure`,
or `failed`, plus per-target states and success/failure counts under the
`continue-independent-targets` policy. Every target that reaches the service
has its own manifest and catalog record, including failed preflight evidence
when the service can commit it. Review the summary and failed target rows; a
batch operation returning from its worker is not proof that every target
succeeded.

This coordinator does not yet cache or reuse one analyzed reference profile.
The same immutable reference request and weights are applied to each
independent target job, but current engines may re-read/re-analyze the
references. Bounded parallelism, reference-profile caching, and alternative
one-target/many-reference comparison batches remain planned.

There is no safe cooperative cancellation contract yet:

- closing the last tab never cancels active processing; normal desktop launches
  wait for the operation to finish before automatic exit;
- refresh, another workbench tab, and hidden/minimized tabs retain the session;
- no Cancel action is offered because there is not yet a safe cancellation
  contract;
- keep source/manual sessions running until a terminal operation state; and
- **Shut down portal** in the application menu refuses to stop while an
  operation is active.

When idle, closing the last workbench tab exits a normal desktop launch after
the refresh grace period. **Shut down portal** provides immediate idle exit.
Source GUI/CLI, `--no-browser`, and `--keep-running` sessions remain running
until explicit shutdown. Ctrl+C in the source launcher console is an operator
interrupt, not a supported way to cancel active DSP; allow completion so audit
finalization can finish. See the
[browser lifetime design](../design/browser-session-lifecycle-2026-10.md).

## How several references become one profile

The program never mixes unrelated reference waveforms and never averages
separately limited output masters. Both approaches are phase-, timing-, and
nonlinearity-dependent.

Instead, every effective unique reference is peak-normalized and analyzed
independently. The engine extracts Matchering-style loud-section RMS plus Mid
and Side magnitude spectra. It combines those measurements in logarithmic
amplitude space, creates one smoothed Mid/Side filter pair, and renders the
target once.

For raw non-negative level weights `a[i]` and frequency weights `b[i]`, the
normalizations are independent:

```text
w_level[i] = a[i] / sum(a)
w_frequency[i] = b[i] / sum(b)
```

The combined level measure is a weighted geometric mean:

```text
R = exp(sum(w_level[i] * log(max(R[i], epsilon))))
```

Each Mid/Side spectrum bin uses the same form with `w_frequency`. In decibels,
this is an arithmetic weighted average. Therefore:

- `level_weight` controls loud-section RMS and restoration level;
- `frequency_weight` controls tonal contribution to the Mid/Side profile;
- multiplying every weight in one dimension by the same positive constant
  does not change that dimension's result;
- zero weight removes a reference from that dimension;
- each dimension must have at least one positive total contribution; and
- if only one effective reference contributes to a dimension, its profile is
  used directly without blend-floor rounding.

The floor is the job's positive `matching.min_value` and is recorded with the
algorithm/profile versions.

### Duplicate and ordering rules

- The maximum of 32 applies to requested reference entries, including repeated
  paths/content.
- Requested references remain individually inventoried in their original
  order.
- Equal SHA-256 content is coalesced for analysis and its normalized weights
  are summed.
- Effective content groups are sorted by SHA-256 before numerical reduction,
  so rearranging the request does not change the effective plan.
- A manual job may retain an occurrence with both weights zero for inventory;
  the interactive catalog requires every saved set member to contribute to at
  least one dimension.
- A target with the same content as any reference is rejected unless the
  explicit diagnostic override is enabled. That override is not recommended
  for normal mastering.

Several technically valid references can still make a poor artistic target.
Prefer well-mastered material with compatible genre, arrangement, bass
extension, vocal balance, dynamics, and intended delivery context. Begin with
equal weights, listen to the paired preview, then change one dimension at a
time.

## Named weighted reference sets

In **Master**, build the Reference sound list and choose **Save** beside the
Saved profile controls. Choose **Manage**, or open the application menu and
choose **Reference sets**, to open the **Reference profiles** utility view. It
provides the complete manager:

- create or rename a set;
- replace and reorder its members;
- edit level and frequency weights independently while reviewing normalized
  contributions;
- load the set into the current job; and
- delete the set without deleting any cataloged audio.

Both the editor and application boundary cap a named set at 32 members, so a
set that can be saved can also be loaded into the current mastering engine
selection limit.

The equivalent CLI flow is:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog set create `
  "Balanced electronic references"
.\.venv\Scripts\mmt.exe catalog --database $catalog set list --json
.\.venv\Scripts\mmt.exe catalog --database $catalog set add `
  <set-id> <reference-a-track-id> --ordinal 0 `
  --level-weight 1 --frequency-weight 2
.\.venv\Scripts\mmt.exe catalog --database $catalog set add `
  <set-id> <reference-b-track-id> --ordinal 1 `
  --level-weight 2 --frequency-weight 1
.\.venv\Scripts\mmt.exe catalog --database $catalog set show <set-id> --json
```

`set add` updates an existing member. `set remove` removes one member without
removing its track from the catalog or deleting audio.

## Export and reimport

There are two related JSON documents.

### Complete catalog export

A complete export contains tracks, all known locations and states, labels and
audio facts, named reference sets, indexed runs, and run artifacts. Open the
application menu and choose **Export catalog** to use the native save dialog.
**Import catalog** accepts a full catalog or content-bound selection JSON and
reports the imported kind.

The equivalent CLI flow is:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog export `
  .\private-workspace\exports\catalog.json
.\.venv\Scripts\mmt.exe catalog --database $catalog import `
  .\private-workspace\exports\catalog.json
.\.venv\Scripts\mmt.exe catalog --database $catalog verify --include-archived
```

Import is strict and transactional: duplicate JSON keys, unknown required
structure, unsupported versions, invalid references, non-finite values, and
conflicting local identities fail rather than partially merging. A full
catalog import preserves named sets and run/artifact history. Run `verify`
after restoring on another disk or machine because v1 exports retain resolved
private paths.

The portal asks before replacing an existing export. CLI exports refuse to
overwrite unless `--force` is explicitly supplied. Portal catalog, selection,
and prepared-configuration JSON use a temporary sibling plus atomic
replacement after overwrite policy is resolved; an interrupted write does not
truncate the previous approved export.

### Selection snapshot

A selection contains exactly one target, one or more ordered references, content
identities, paths, labels, sizes, and independent weights. Portal-created jobs
automatically write one per target under `private-workspace/jobs/`. Use
**Save setup…** in **Master**, or **Export current selection** in the
application menu, to save the current selection before running when the input
queue contains exactly one target.
Portable selection schema version 1 intentionally cannot collapse a
multi-target queue into one document; remove all but one input and export each
target separately, or export the complete catalog to retain every track.

To export one explicitly:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog selection `
  --target <target-track-id> `
  --reference <reference-a-track-id> `
  --reference <reference-b-track-id> `
  --output .\private-workspace\exports\selection.json
```

Use `--reference-set <set-id>` instead of repeated `--reference` arguments to
preserve a named set's weights.

Choose **Import catalog** in the application menu, or:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog import `
  .\private-workspace\exports\selection.json
```

Selection import rehashes every declared file and accepts it only when path,
size, track ID, and SHA-256 agree. In the GUI it registers the tracks, loads
that one target plus ordered references and independent weights into
**Master**, replacing the music queues after confirmation. CLI import registers
the same catalog identities but has no interactive form to populate. A
selection is not a substitute for the separately saved job configuration:
processing controls, outputs, destination, previews, edge policies, and notes
are not restored from it. Import the full catalog when named sets and
historical runs must also be restored.

## Complete run and artifact visibility

For every accepted portal, terminal, or CLI target job, inspect these layers:

1. **Job configuration** — the exact requested paths, controls, output modes,
   engine, policies, and weights.
2. **Selection JSON** — content-bound target/reference list and deterministic
   selection ID, suitable for strict reimport.
3. **Run manifest** — terminal status, configuration snapshot, environment,
   input/output fingerprints, measurements, warnings/errors, and engine
   details.
4. **JSONL event log** — ordered lifecycle and diagnostic events for the cause
   chain. Portal and terminal jobs always request it; CLI jobs do unless
   `--no-event-log` is explicitly selected.
5. **Catalog index** — cross-run lookup of selection, manifest, every
   fingerprinted input/output/audit artifact, and current location state.
6. **Audio artifacts** — limited, normalized, raw, and preview files actually
   requested by the job.

A multi-target portal submission expands into one complete set of these layers
per target. The parent batch result is an aggregate navigation/diagnostic
summary, not a replacement for the individual authoritative manifests.

Every requested reference occurrence is present in the manifest with:

- original index, path, label, size, and SHA-256;
- requested and independently normalized weights;
- effective content-group SHA-256 and member indices;
- summed effective group weights; and
- level/frequency effectiveness flags.

Native weighted runs additionally record algorithm version, requested and
effective reference inventories, per-reference profile/filter hashes,
geometric-combination rules, epsilon, direct-profile decisions, combined
measurements, guardrail information, and a deterministic processing-plan
SHA-256.

Use **Activity** > **History** to select a run, inspect its compact artifact
table, play or assign cataloged audio artifacts to comparison slots, view a
manifest summary or raw JSON, and ask Explorer to select an artifact without
executing it. **Event log** is a responsive projection of operation events; the
durable JSONL file remains the evidence source after the portal exits.

The equivalent CLI queries are:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog runs --json
.\.venv\Scripts\mmt.exe catalog --database $catalog artifacts `
  <run-id> --json
```

The artifact record's `metadata.manifest_role` preserves the exact manifest
role (`target`, `reference[n]`, `job-config`, `catalog-selection`,
`mastered-output`, preview, event log, or run manifest) even when the catalog's
higher-level role groups several artifacts together.

When diagnosing a failure, read the manifest first, then follow the correlated
events up to the earliest failed stage. Do not infer success solely from the
presence of an output file.

## Run a hand-authored configuration

Use the fully expanded examples:

- [`upstream-baseline.json`](../../configs/upstream-baseline.json) — runnable
  one-reference compatibility job;
- [`weighted-references.json`](../../configs/weighted-references.json) —
  runnable two-reference native profile; and
- [`native-target.json`](../../configs/native-target.json) — intentionally
  rejected specification of future advanced controls.

Copy an example before editing it. Relative paths resolve from the job file.

```powershell
.\.venv\Scripts\mmt.exe show-config .\configs\my-job.json
.\.venv\Scripts\mmt.exe validate .\configs\my-job.json
.\.venv\Scripts\mmt.exe run .\configs\my-job.json --dry-run `
  --catalog .\private-workspace\catalog\catalog.sqlite3
.\.venv\Scripts\mmt.exe run .\configs\my-job.json --verbose `
  --catalog .\private-workspace\catalog\catalog.sqlite3
```

CLI runs write their selection beside the chosen/default manifest. With no
`--catalog`, `mmt run` uses `CONFIG_DIR/.mmt/catalog/catalog.sqlite3`, which is
separate from the interactive workbench catalog unless the config itself is
under that workspace.

The job schema is
[`schemas/job-config-v1.schema.json`](../../schemas/job-config-v1.schema.json).
Schema validation checks structure and current reference-count/weight rules.
`mmt validate` remains required because engine capability and decoded-audio
checks depend on cross-field and runtime facts.

## Control the workflow as a Python library

The top-level package exports the typed job models, service, catalog, selection,
and manifest-integration boundary. This compact example builds a two-reference
job without loading JSON and records the same configuration/selection/manifest
provenance as the CLI:

```python
from pathlib import Path
from uuid import uuid4

from music_mastering_tools import (
    CatalogStore,
    EngineKind,
    ExecutionConfig,
    JobConfig,
    MasteringService,
    OutputMode,
    OutputSpec,
    ReferenceSpec,
    finalize_catalog_run,
    register_job_selection,
    save_job_config,
    validate_job,
)

run_id = f"private-{uuid4().hex}"
workspace = Path("private-workspace").resolve()
run_dir = workspace / "runs" / run_id
run_dir.mkdir(parents=True, exist_ok=False)

job = JobConfig(
    target=str(Path("audio/target.wav").resolve()),
    references=(
        ReferenceSpec(
            str(Path("audio/reference-a.wav").resolve()),
            level_weight=1.0,
            frequency_weight=2.0,
            label="tone emphasis",
        ),
        ReferenceSpec(
            str(Path("audio/reference-b.wav").resolve()),
            level_weight=2.0,
            frequency_weight=1.0,
            label="level emphasis",
        ),
    ),
    outputs=(
        OutputSpec(
            str(workspace / "outputs" / run_id / "mastered-limited-24.wav"),
            subtype="PCM_24",
            mode=OutputMode.LIMITED,
        ),
    ),
    execution=ExecutionConfig(
        engine=EngineKind.NATIVE,
        job_id=run_id,
    ),
)

report = validate_job(job)
if not report.ok:
    raise RuntimeError(report.to_dict())

config_path = workspace / "jobs" / f"{run_id}.json"
selection_path = workspace / "jobs" / f"{run_id}.selection.json"
config_path.parent.mkdir(parents=True, exist_ok=True)
save_job_config(job, config_path)

with CatalogStore(workspace / "catalog" / "catalog.sqlite3") as catalog:
    selection = register_job_selection(catalog, job)
    selection_path.write_text(selection.to_json(), encoding="utf-8", newline="\n")

    outcome = MasteringService().run(
        job,
        manifest_path=run_dir / "manifest.json",
        event_log_path=run_dir / "events.jsonl",
        configuration_path=config_path,
    )
    finalize_catalog_run(
        catalog,
        selection,
        outcome.manifest_path,
        configuration_path=config_path,
        selection_path=selection_path,
    )
```

`JobConfig` and its nested values are validated immutable dataclasses.
`register_job_selection` fingerprints/catalogs every source and returns a
content-bound `CatalogSelection`. `MasteringService.run` owns preflight,
events, DSP, reopen verification, and the initial manifest.
`finalize_catalog_run` adds catalog/selection provenance and indexes every
manifest artifact. An embedding application should use the CLI/portal
pattern of attempting catalog finalization whenever a failure manifest exists,
not only on the success path shown above.

## Backup and recovery

- Back up the source audio separately; the catalog does not contain audio.
- Preserve `private-workspace/jobs/`, `runs/`, and `exports/catalog.json` with
  the SQLite database when exact run history matters.
- Preserve `private-workspace/trash/master-versions/` while any discarded
  version may need to be restored.
- Ensure the application has exited before copying the SQLite database. Use
  idle **Shut down portal**, or close the last normal desktop tab and let active
  work finish; source launchers return after explicit shutdown. A complete JSON export is the
  preferred interchange/restore artifact.
- After restoring files, import the catalog and run `verify`.
- Use `relink` only for byte-identical moved files.
- Never edit a committed run manifest to make a failed run look successful.
- Existing manifests, event logs, selections, and output paths are protected
  from silent overwrite; use a new run ID/path for another attempt.

### Recover a rendered run whose catalog finalization failed

If rendering committed a manifest but catalog finalization failed, do not
rerender or edit the evidence. In **Activity** > **History**, choose **Recover
manifest…**, select the preserved manifest, and supply its selection/config
only if the portal cannot auto-detect them. Review the recovered run and
artifact count.

The equivalent CLI reconciliation is:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog recover-run `
  .\private-workspace\runs\<run-id>\manifest.json
```

An already augmented CLI/portal/terminal manifest contains the embedded catalog
selection and normally needs no other arguments. For an earlier manifest that
was preserved before catalog augmentation, supply the owned files explicitly:

```powershell
.\.venv\Scripts\mmt.exe catalog --database $catalog recover-run `
  .\private-workspace\runs\<run-id>\manifest.json `
  --selection .\private-workspace\jobs\<run-id>.selection.json `
  --configuration .\private-workspace\jobs\<run-id>.json
```

Recovery strictly parses and rehashes the selection, verifies every manifest
artifact before indexing, attaches the catalog provenance, and reports the
run/selection IDs plus indexed artifact count. Repeating it for the same
unchanged evidence replaces that run's index transactionally; it does not
rewrite audio.

## Privacy and licensing boundary

The portal, source catalog, paths, hashes, logs, manifests, selections, audio,
and renders remain private. The server listens only on loopback and uses a
per-launch token, but other software running as the same desktop user remains
inside the local trust boundary. Do not share the launch URL or expose its port
through a proxy, firewall rule, tunnel, container mapping, or LAN bind. Do not
commit or distribute private artifacts.

The project preserves GPLv3-or-later compatibility, the standard GPL text, and
upstream attribution. The owner's 2026-10-02 request opened Windows executable
work and the follow-up authorized source commit/push to
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster).
See the [release guide](../deployment/windows-release.md)
and [GPL policy](../policies/licensing.md) for the reviewable source/build
candidate and exact corresponding-source work before a binary publication.
Completing a local run does not upload or share the operator's library.
