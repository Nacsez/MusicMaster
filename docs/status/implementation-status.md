# Implementation status and traceability

Status date: **2026-10-02**

This is the authority for what the workspace currently delivers. Requirement
text lives in [the catalog](../product/requirements.md); this register prevents
representable options, accepted ADRs, upstream behavior, or passing scaffold
tests from being mistaken for a completed mastering engine.

## Executive status

The workspace is a **functional, tested GUI-first private workbench with a
verified Matchering 2.0.6 compatibility slice and an experimental runnable
weighted native slice**. It is ready for private operator testing, but it is
not yet the completed private-version roadmap.

The owner has opened a scoped **Windows executable and GitHub source publication**
milestone. Supported distribution is Windows 10/11 Intel/AMD x64, one
independent library per Windows account, with a browser interface and a
console-free one-file EXE. This scope supersedes the old prohibition on release
preparation; it does not promote the longer-term DSP backlog to completion.
See the [release guide](../deployment/windows-release.md),
[distribution design](../design/windows-distribution-2026-10.md), and
[CADER reskin record](../design/cader-reskin-2026-10.md).

## Browser lifetime and source publication candidate — 2026-10-02

The owner explicitly authorized commit/push to the public
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster) repository. The source
candidate excludes personal audio, catalogs, logs, workspaces, and generated
executables. CI builds and verifies the application, retaining selected
diagnostic reports; it does not upload executable or workspace archives.

Initial source publication is
[`00ae1d9`](https://github.com/Nacsez/MusicMaster/commit/00ae1d9c1dc197684e49b7cb13adcdea03d59884),
verified against GitHub `main`. The first
[GitHub Windows run](https://github.com/Nacsez/MusicMaster/actions/runs/37077974920)
passed source/privacy and static gates, then exposed five test-fixture path
comparisons: the runner's `RUNNER~1` temporary-directory alias resolves to
`runneradmin`. Application output already used the correct canonical path;
the follow-up test corrections compare resolved locations. No application or
EXE behavior changed. All **five affected tests passed** with a real Windows 8.3
temporary-directory alias; the alias and long path resolved to the same physical
file. Reproduction evidence is
`artifacts/release-audit/windows-short-path-ci/verification-20261002T234005041764Z.json`
and its JUnit report. Final source/remote verification receipts are retained in
`artifacts/release-audit/source-publication.json`.

The second [GitHub Windows run](https://github.com/Nacsez/MusicMaster/actions/runs/37078724499)
passed the full regression suite (**288 passed, 1 skipped**) and static/source
gates. Its populated-browser audit isolated one normal-color focus snapshot:
the field was focused with a solid 3 px outline, but the immediately sampled
outline color was the foreground rather than the CADER pink. Forced colors and
the other **35 browser checks** passed, with no browser/runtime errors. The
follow-up audit establishes page focus and actual keyboard navigation before
sampling the rendered focus state; its exact pink/green/outline assertions stay
in place. Captured CI evidence is retained under
`artifacts/release-audit/github-browser-verification/`. The revised audit passed
all **36 checks** locally; normal focus settled in **18 ms** and forced colors
in **33 ms**. The evidence establishes the corrected test contract without
claiming a confirmed Chromium root cause. No application/CSS/EXE changes were
needed for either CI test correction.

Normal desktop launches now exit after the last workbench tab closes, allowing
four seconds for refresh/reconnection. Separate document leases preserve other
tabs and minimized windows. Active mastering finishes before automatic exit;
the real operation-manager HTTP regression verifies completed publication and
retained catalog/preferences/source bytes. Crash expiry uses the production
180-second lease, and a paused server loop allows reconnect after Windows
sleep/resume. Source GUI/CLI, `--no-browser`, and `--keep-running` stay persistent.

Actual browser qualification exposed token-free refresh returning HTTP 403
after intentional address-bar token removal. A separate per-process, port-named
HttpOnly/SameSite page-session cookie now authenticates query-free index
refreshes. Explicit blank, duplicate, invalid, or stale credentials are rejected.
API and media routes retain their respective authorization requirements.
Expected transport disconnects are handled without duplicate error responses.

Final local verification for this source candidate:

| Gate | Result and retained evidence |
|---|---|
| Full regression and coverage | **288 passed, 1 skipped** (case-insensitive filesystem); **88.42%** against the 85% gate. `artifacts/tests/junit-all-20261002T232203073Z-65852.xml`; `artifacts/coverage/coverage-20261002T232203073Z-65852.xml`. |
| Static quality | Ruff lint/format across **77 files**, strict mypy across **36 source files**, all passed. `artifacts/quality/sharing-final-20261002.log`. |
| Populated browser workflows | **36/36 checks**, no runtime/browser errors or token leaks; actual keyboard focus and settled computed styles. `artifacts/ux-populated-smoke/20261002T235325173Z/verification-summary.json`. |
| Source browser lifecycle | **24/24 checks**, including genuine refresh, canceled navigation, history restoration, delayed old close, multiple tabs, hidden/minimized polling, native window close, and retained library. `artifacts/browser-lifecycle-smoke/20261002T231959149Z/verification-summary.json`. |
| Windows one-file build | CPython **3.11.7**, PyInstaller **6.22.0**, hooks **2026.8**, successful. `artifacts/windows-build/20261002T231957512Z/build.log`. |
| Frozen audio/deployment | **11/11 checks**, actual upstream and weighted mixed-rate renders, original byte preservation, relocated EXE, unrelated cwd, isolated user data, system-only PATH, restart persistence, and token redaction. `artifacts/executable-smoke/20261002T232202883788Z/verification-summary.json`. |
| Frozen browser lifetime | **33/33 checks**: bootloader and runtime child both exited after last tab (**5.18 seconds**), native window close (**4.78 seconds**), and abrupt browser kill (**184.255 seconds**). All session files removed; retained catalog, source, and output preferences; zero runtime exceptions. `artifacts/browser-lifecycle-smoke/20261002T232204318Z/verification-summary.json`. |

The current EXE is **118,591,209 bytes**, SHA-256
`821b3212ab84efb913d6e4a17ff32f94334cb59c38af314c6e2fda69524d68ed`.
The local Windows ZIP includes `START-HERE.txt`, the tested EXE, licenses/notices,
dependency/native inventory, build correspondence, and checksums. Its final
`build-manifest.json` identifies the source commit and **81 authored inputs**;
the archive audit covers **1,122 members** and distribution checksums cover
**86 files**. Publication audit and commit/push receipts live under ignored
`artifacts/release-audit/`; reproduce the source check with
`Invoke-PublicationAudit.ps1 -TrackedOnly`.

| Distribution requirement | Status and evidence |
|---|---|
| DIST-001 | EXE implemented and locally qualified without Python on child PATH; independent clean-Windows acceptance remains unrecorded. |
| DIST-002 | Implemented: isolated per-user default, moved EXE/unrelated cwd, one owner, and catalog/preferences/track persistence across automatic exit/restart. |
| DIST-003 | Implemented: remembered picker contexts, exact Explorer selection/opening, Unicode/space/comma cases; prior native desktop acceptance below remains applicable. |
| DIST-004 | Audited source and reviewable build implemented; complete corresponding dependency-source collection and public binary release remain separate work. |
| DIST-005 | Implemented: deterministic/HTTP operation regressions plus **24 source / 33 frozen** real-browser checks, including production crash timeout. |

See the [recipient quick start](../user/windows-quick-start.md),
[deployment guide](../deployment/windows-release.md), and
[browser lifecycle design](../design/browser-session-lifecycle-2026-10.md).
The following preparation checkpoint records earlier results and build identity.

## Initial Windows sharing preparation checkpoint — 2026-10-02

Delivered a console-free `dist/windows/MusicMasteringTools.exe` for Windows
10/11 Intel/AMD x64. It retains the existing browser workflow and bundles the
Python/mastering runtime. Default mutable state lives in the current user's
`%LOCALAPPDATA%\MusicMasteringTools\workspace`, independently of executable
location or launch working directory. A second desktop launch reopens the
authenticated existing session instead of starting competing mastering work;
reactivation bypasses environment HTTP proxies. Desktop/module logs remain
available in Session Logs, with `--verbose` for additional module detail.

Folder pickers remember successful locations by purpose and honor current job
delivery/relink locations. Explorer opens directory contents and selects files
using Unicode Shell identifiers; a bounded exact-folder readiness check and
one reselection protect first-use selection in newly created run directories.
Missing paths and native failures produce explicit diagnostics. The CADER
palette and sharp geometry retain the three-workspace workflow, purple
references, green active/actions, pink keyboard focus, and system high-contrast
colors. Browser qualification also fixed wrapped Library menus clipping at
laptop widths and obscured active-control labels in forced colors.

Final local acceptance on **2026-10-02**:

| Gate | Result and retained evidence |
|---|---|
| Full application regression + coverage | **256 passed, 1 skipped** (case-insensitive filesystem); **88.10%** aggregate statement/branch coverage against the 85% gate. `artifacts/tests/release-final-20261002.xml` and `artifacts/coverage/release-20261002.xml`. |
| Static quality | Ruff lint/format passed across **72 files**; strict mypy passed across **35 application/build/smoke source files**. `artifacts/quality/release-final-20261002.log`. |
| Populated browser acceptance | **36/36 checks passed**, no runtime exceptions, page errors, or token leaks; A/B swapping/playhead continuity, keyboard rerenders, 1280 × 800, 200% zoom-equivalent reflow, forced colors, and orderly shutdown. `artifacts/ux-populated-smoke/20261002T221418898Z/verification-summary.json`. |
| Actual Explorer acceptance | Fresh Unicode/space/comma filename selected in its exact folder; directory contents opened correctly; only test-created windows closed. `artifacts/folder-navigation-smoke/20261002T221750240134Z-0dd5ff3c/result.json`. |
| Windows one-file build | CPython **3.11.7**, PyInstaller **6.22.0**, hooks **2026.8**; successful final build. `artifacts/windows-build/20261002T223028006Z/build.log`. |
| Actual frozen EXE acceptance | **11/11 checks passed**: relocated EXE with unrelated cwd, isolated current-user data, no Python/FFmpeg on PATH, bundled dependency imports/assets/authentication, second launch, real upstream and weighted native renders including mixed-rate resampling, exact custom destination, unchanged source bytes, catalog/output preference persistence after restart, token redaction, shutdown, and no mutable data beside EXE/in cwd. `artifacts/executable-smoke/20261002T223203872479Z/verification-summary.json`. |
| Final source publication audit | **147 candidate files, 0 findings**. `artifacts/release-audit/publication-audit-20261002T223130107Z-75584.json`. |
| Final distribution audit | **1,122 archive members**, **85 distribution checksums**, and **77 authored build-input hashes** verified; no editable-install URL metadata, private workspace/artifacts, or notice code caches. `artifacts/windows-build/final-distribution-audit.json`. |

The release folder includes project/third-party notices, exact package and
native-library provenance, SHA-256 checksums, and `build-manifest.json` tying
the executable to authored application/build-input hashes. Runtime pins cover
17 distributions; combined runtime/build inventory covers 24 distributions
plus CPython and seven native support components. Editable checkout metadata,
private state, dev-only optional packages, and compiled license-directory
caches are excluded from the artifact. A notice-collection regression protects
the cache exclusion. The build retains optional-hook/TBB diagnostic warnings;
both actual bundled render/resampling workflows passed.

The initial preparation EXE was **118,576,925 bytes**, SHA-256
`5a67f94af4dde423595533143d1481cc54bb495f76ea670b97b941a66dfb5b03`.
`dist/MusicMasteringTools-Windows-x64.zip` contains the tested executable and
distribution notices/inventories/checksums; its checksum is retained alongside
the ZIP. The post-Analysis archive filter and build-manifest audit guard against
PyInstaller hooks reintroducing editable checkout metadata after collection.

Ignored evidence paths above are local development artifacts, not intended for
source publication. Reproduce them with the documented build/smoke scripts.

Preparation added a read-only source publication audit, Windows CI without
automatic publishing, `NOTICE`, and exact upstream provenance. The upstream
ZIP identifies revision `914c9e58939746db1669212645907e7316389d08`; all 80
extracted files match their archived bytes. The local archive/extraction are
preserved and ignored while public documentation uses pinned source links.

Initial Git inspection found no tracked files and no existing commit. Source
was untracked; there was no tracked private audio/catalog history to remove.
At that initial preparation checkpoint no GitHub push or public release upload
had been performed. The source publication candidate above supersedes that scope.

Independent clean-Windows acceptance, actual remote CI results, and collecting
all corresponding dependency sources for a binary publication require separate
evidence and are not claimed by preparation alone.

## Historical implemented baseline — 2026-09-03

Present today:

- Python project/tooling scaffold (private/pre-alpha metadata at this historical checkpoint);
- complete typed job configuration and JSON schema;
- expanded runnable one-reference and weighted-reference profiles plus an
  intentionally capability-rejected future-controls specimen;
- audio metadata/sample preflight with a standard-library WAV fallback;
- stable error types and validation issues;
- immutable per-job events with console, JSONL, memory, and composite sinks;
- file fingerprints, artifact/metric records, run manifests, and atomic
  manifest persistence;
- exclusive, hardlink-aware audit destinations with compensating terminal
  journal records when audit finalization fails;
- environment doctor;
- truthful engine capability contracts;
- serialized adapter code for pinned Matchering 2.0.6, exercised through its
  real `process()` entry point;
- runnable native Matchering-parity processing for one through 32 references,
  with independent level/frequency geometric profiles, SHA-256 duplicate
  coalescing, stable ordering, all three output branches, paired previews, and
  an optional post-smoothing EQ ceiling;
- synchronous audited dry-run/render orchestration;
- post-render reopening and verification of frames, rate, channels, subtype,
  finite samples, and requested preview length;
- content-addressed SQLite catalog for tracks, locations, named weighted
  reference sets, runs, and artifacts, plus a source-centered projection that
  attaches completed mastered-output versions to their original target;
- strict catalog and selection JSON export/reimport;
- CLI for doctor, capabilities, show-config, validate, run, catalog
  administration, the GUI portal, and terminal fallback;
- a GUI-independent portal application layer for complete catalog, named-set,
  1–32-target job preparation/validation, sequential per-target dry-run/render,
  continue-after-target-failure batch summaries, recovery, run/artifact
  inspection, diagnostics, and capability operations;
- a packaged three-workspace browser interface organized around **Master**,
  **Library**, and **Activity**. Master presents one continuous batch flow with
  searchable multi-select input/reference pickers, required delivery controls,
  progressive disclosure for extra formats and supported advanced settings,
  and a sticky readiness/run rail. Library keeps source/version management and
  export together; Activity groups History with Event log and System utilities,
  using compact semantic run cards that avoid desktop horizontal scrolling.
  The persistent audition dock provides direct A/B candidate pickers, one-click
  swap with playhead preservation, and non-destructive Quick Play previews;
  retained task evidence reconstructs per-target terminal state and the direct
  review route after a portal reload;
  reference profiles and path-confined Session logs remain available as
  secondary utilities rather than primary workspaces;
- strict versioned portal preferences for a persistent default delivery folder
  plus a per-job override; each target receives an exclusive run-named output
  subfolder that is never reused or overwritten;
- a loopback-only HTTP adapter using a random default port, per-launch token,
  Host/origin checks, strict bounded JSON, CSP/defensive headers, and a
  separately authenticated catalog-ID-only HTTP Range media route, with no
  remote assets, Electron runtime, arbitrary filesystem route, or audio-upload
  route;
- native Windows audio/JSON/folder dialogs using fixed WinForms scripts and
  safe Explorer selection;
- root double-click launcher with environment repair consent, strict
  diagnostics, pre-handoff transcript closure, GUI default, and `-Terminal`
  fallback;
- a headless Chromium smoke harness that defaults to Master, can enter each
  primary workspace or utility directly, accepts explicit viewport dimensions,
  verifies the initialized application-state sentinel and unified-workflow
  landmarks, and retains token-redacted bootstrap/DOM/screenshot/shutdown
  evidence; semantic asset contracts additionally protect the three-workspace
  navigation, readiness rail, progressive disclosures, and direct A/B controls;
- retained historical Chromium reviews of the former interface at 1400 × 1000
  and 1280 × 800, plus responsive contracts for the redesigned workspace,
  comparison dock, tables, and narrow-layout stacking;
- a successful subprocess operator-acceptance run for two references, all
  three output branches, automatic selection/catalog finalization, ten indexed
  artifacts, and strict selection reimport into a fresh catalog;
- deterministic generator for ten WAV fixtures plus manifest; and
- 227 passing unit/integration/regression/smoke tests and one
  platform-conditional skip, covering engine parity, weighted
  profiles/renders, catalog persistence and adapters, CLI/workbench/portal
  integration, multi-target batches, preferences, authenticated media Range
  streaming, the revised GUI, and the Windows launcher. The most recent
  aggregate branch-coverage run remains the 2026-07-30 87.78% snapshot.

Not demonstrated by that historical checkpoint:

- characterization parity or golden numerical baselines;
- complete public `analyze → plan → render` stage contracts and replayable plan
  schema (the weighted engine currently records an immutable plan summary/hash);
- complete spectral/gain/filter-energy guardrails beyond the native optional
  EQ ceiling and finite-value checks;
- reference-profile caching/reuse, resource-bounded parallel batch execution,
  one-target/many-alternative-reference comparison batches, or match amount;
- cancellation/resource budgets/transactional audio publication;
- interactive native-picker/mastering operator acceptance and a complete
  accessibility audit (application/HTTP/dialog contracts and headless Chromium
  bootstrap/shutdown have evidence);
- EBU R128, true-peak, dither, metadata copying, and external limiter support;
- a supported-platform matrix beyond Windows/Python 3.11; or
- the official container, because no Docker daemon was reachable.

The reviewed local environment contains Python 3.11, Matchering 2.0.6, NumPy,
SciPy, Statsmodels, Resampy, and SoundFile. Its exact `pip freeze --all` record
is retained under ignored `artifacts/environment/`. FFmpeg remains optional
and was not found; the generated WAV evidence does not require it.

## Evidence conventions

- `IMPLEMENTED` means substantive code exists and relevant focused tests may
  pass, but the full requirement acceptance is not yet proven.
- `SCAFFOLDED` means only part of the requirement or its contract/tooling
  exists.
- `UPSTREAM` means behavior is available only by invoking the extracted/pinned
  Matchering engine, without private acceptance evidence.
- No requirement below is `VERIFIED` unless every criterion in the product
  requirement catalog has evidence.

## Shipped scaffold inventory

| Area | Current state | Evidence |
|---|---|---|
| Project metadata | Package `music-mastering-tools` 0.1.0, Windows/Python 3.11 baseline, GPL-3.0-or-later, beta; desktop/portal/CLI entrypoints | [`pyproject.toml`](../../pyproject.toml) |
| Requirements | Matchering pinned to 2.0.6; scientific/dev guardrails plus complete validated Windows runtime/build pins and native provenance | [`requirements/`](../../requirements/), [`native provenance`](../../packaging/third_party/provenance.json) |
| Bootstrap/quality/tests | PowerShell wrappers, regression/coverage, real EXE/browser/Explorer smoke, publication audit and Windows CI; latest dated evidence is in the sharing checkpoint | [`scripts/`](../../scripts/), [`tests/README.md`](../../tests/README.md) |
| Fixtures | Ten deterministic PCM WAVs plus generated manifest; binary outputs ignored | [`generate_wav_fixtures.py`](../../scripts/generate_wav_fixtures.py), [fixture notes](../../tests/fixtures/README.md) |
| Job config | Immutable typed models, strict finite JSON, stable normalized weights, inherited DSP-domain checks, and unknown-field rejection | [`config.py`](../../src/music_mastering_tools/config.py), [`job-config-v1.schema.json`](../../schemas/job-config-v1.schema.json) |
| Profiles | Runnable upstream one-reference and native weighted-reference profiles; a separate native-target specimen deliberately requests unsupported future controls | [`configs/`](../../configs/) |
| Audio probe/preflight | Streaming file facts plus silence/mono/rate/length/capability, symlink, hardlink, and collision checks; policy matrix remains incomplete | [`audio_probe.py`](../../src/music_mastering_tools/audio_probe.py), [`validation.py`](../../src/music_mastering_tools/validation.py) |
| Errors | Stable error codes and typed project exceptions | [`errors.py`](../../src/music_mastering_tools/errors.py) |
| Events | Job-scoped sequence and multiple sinks; schema does not yet fully match the target canonical envelope | [`events.py`](../../src/music_mastering_tools/events.py) |
| Manifest | Deep-copied/frozen records, fingerprints, artifacts, metrics, lifecycle, strict JSON, atomic save/load, and no-overwrite ownership checks | [`manifest.py`](../../src/music_mastering_tools/manifest.py) |
| Doctor | Non-throwing environment/dependency/FFmpeg report | [`doctor.py`](../../src/music_mastering_tools/doctor.py) |
| Engine abstraction | Upstream adapter is verified runnable for one exact reference; experimental native adapter is runnable for one through 32 independently weighted references and rejects unsupported controls | [`engine/`](../../src/music_mastering_tools/engine/), [`dsp/`](../../src/music_mastering_tools/dsp/) |
| Job service | Validation, input-change detection, required audit journal, dry run, engine invocation, reopened output measurements, and compensating audit recovery | [`service.py`](../../src/music_mastering_tools/service.py) |
| Catalog | Local SQLite content identities/locations, roles, metadata labels, archive/restore/relink, weighted sets, strict catalog/selection exchange, immutable run selections, manifest/run/artifact indexing, source-to-master-version projection, and idempotent preserved-run recovery | [`catalog.py`](../../src/music_mastering_tools/catalog.py), [`catalog_integration.py`](../../src/music_mastering_tools/catalog_integration.py) |
| CLI | Doctor/capabilities/config/validate/run plus complete catalog administration, lazy GUI/portal entry point, and terminal fallback, with distinct exit categories | [`cli.py`](../../src/music_mastering_tools/cli.py), [`catalog_cli.py`](../../src/music_mastering_tools/catalog_cli.py) |
| Portal application | HTTP-independent catalog/set/job/recovery/run/diagnostic/log-view services, source/master lineage projection, metadata-only renaming, verified exclusive batch-copy export, recoverable version quarantine/restore with indexed tombstones, strict atomic portal preferences, 1–32-target expansion with per-target validation/evidence and continue-after-failure summaries, one serialized background worker, and bounded operation/event projection | [`portal_app.py`](../../src/music_mastering_tools/portal_app.py) |
| Local GUI/security | Packaged no-Electron three-workspace interface: unified Master batch composer with searchable multi-select queues and readiness rail; Library source/version/export management; Activity history with Event log and System utilities; progressive disclosure; responsive layouts; and a persistent direct-select A/B dock with swap/playhead preservation and non-destructive Quick Play. Reference profiles and path-confined Session logs remain secondary utilities. The loopback-only token-gated HTTP/JSON adapter retains its distinct media-cookie/catalog-ID/Range boundary, token/query-redacted access logs, CSP/defensive headers, fixed-script WinForms pickers, and safe Explorer selection. | [`portal.py`](../../src/music_mastering_tools/portal.py), [`native_dialogs.py`](../../src/music_mastering_tools/native_dialogs.py), [`web_assets/`](../../src/music_mastering_tools/web_assets/) |
| Workbench/launcher | GUI-first catalog-backed 1–32-target/1–32-reference workflow, explicit terminal fallback, and double-click Windows launcher whose diagnostic transcript closes before portal handoff | [`workbench.py`](../../src/music_mastering_tools/workbench.py), [`Launch-Music-Mastering-Tools.cmd`](../../Launch-Music-Mastering-Tools.cmd) |
| Automated tests | The 2026-09-03 non-coverage gate collects 228 tests: 227 pass and one case-distinct-path case skips on the case-insensitive Windows filesystem across model, adapter, integration, regression, DSP, catalog, CLI, workbench, portal, and launcher suites. The most recent aggregate branch-coverage run is the 2026-07-30 87.78% snapshot against an 85% gate. Target batches, preferences, source/master lineage, safe master lifecycle operations, collision safety, authenticated media Range requests, semantic frontend contracts, viewport-configurable GUI smokes, populated multi-version A/B interaction, and responsive Library reviews are covered; no full golden characterization corpus yet | [`tests/`](../../tests/) |
| Upstream evidence | Extracted 2.0.6 tree and ZIP preserved; hashes/provenance manifest still pending | [baseline](../baseline/upstream-2.0.6.md) |

## Requirement traceability

### Core audio behavior

| ID | Status | Current implementation/evidence and gap | Phase |
|---|---|---|---:|
| CAP-001 | SCAFFOLDED | Typed file paths, file probes, and one-target/reference job exist. No in-memory audio adapter or canonical-equivalence test. | 2 |
| CAP-002 | SCAFFOLDED | `AudioFacts`, SoundFile/WAV block probes, non-finite counts, rate/channels exist. No canonical audio buffer contract. | 2 |
| CAP-003 | IMPLEMENTED | The native weighted path performs Matchering-parity loud-piece selection/RMS independently for each effective reference and records combined measurements/profile hashes. A public versioned analysis report and full characterization corpus remain pending. | 1–2 |
| CAP-004 | IMPLEMENTED | Compatibility and native weighted renders apply loud-section level matching; native run details record the initial coefficient and plan identity. A complete public `MatchPlan` contract remains pending. | 1–2 |
| CAP-005 | IMPLEMENTED | The native path builds one smoothed Mid/Side FIR pair from the weighted reference profile and records filter hashes/summaries. Public coefficient export and complete stage characterization remain pending. | 1–2 |
| CAP-006 | SCAFFOLDED | Both paths execute bounded configured RMS correction. Native run details expose final plan measurements, but each correction iteration is not yet a public record. | 1–3 |
| CAP-007 | IMPLEMENTED | One real run creates and reopens limited PCM-24, normalized PCM-24, and raw FLOAT outputs; all are finite and audited. Broader golden characterization is pending. | 2 |
| CAP-008 | IMPLEMENTED | Internal-rate config and upstream resampling mapping exist; post-render rate/frame facts are verified at 44.1 kHz. A multi-rate render matrix remains pending. | 2 |
| CAP-009 | IMPLEMENTED | Paired previews render in both compatibility and weighted paths and reopen at the exact integral frame count with finite output. Native regression compares aligned preview frames; broader alignment characterization remains pending. | 2 |
| CAP-010 | SCAFFOLDED | Inherited options are represented/mapped, finite JSON and LOWESS/FFT/Hyrax domains reject early, and preview dimensions have a real regression. Exhaustive dependency-version boundary characterization remains incomplete. | 2–3 |
| CAP-011 | IMPLEMENTED | Every render is reopened and requested/observed subtype, rate, channels, frames, and finiteness are compared. Current evidence covers WAV PCM-16/24/FLOAT, not a full format matrix. | 2 |
| CAP-012 | SCAFFOLDED | Preflight and post-render reports include duration/rate/channels/peak/RMS/clipping/non-finite facts. Spectral and loudness-delta measurement is absent. | 2 |
| CAP-013 | SCAFFOLDED | EBU R128 is represented in typed config and the native target profile; both current engines truthfully reject it. No measurement/planning implementation. | 5 |
| CAP-014 | SCAFFOLDED | True-peak mode/oversampling are represented; both current engines truthfully provide sample-peak only and reject true-peak requests. | 5 |
| CAP-015 | SCAFFOLDED | Dither modes are represented and raw/integer incompatibility is preflight-tested; no dither DSP exists and upstream accepts only `none`. | 5 |
| CAP-016 | SCAFFOLDED | Metadata `drop`/`copy-safe` are represented; only upstream `drop` is accepted and no reopen/whitelist implementation exists. | 5 |
| CAP-017 | SCAFFOLDED | External limiter command/config is representable and upstream rejection is explicit. Secure approved-tool execution is not implemented. | 5+ |

### Pipeline contracts and creative controls

| ID | Status | Current implementation/evidence and gap | Phase |
|---|---|---|---:|
| PIPE-001 | SCAFFOLDED | The native engine has immutable internal reference-profile records and versioned identities, but no public side-effect-bounded analyze operation or standalone serialized profile schema. | 2 |
| PIPE-002 | SCAFFOLDED | Native result details contain source/profile identities, combination rules, gains, filter summaries, guardrail data, algorithm version, and a deterministic plan hash. A complete importable/replayable `MatchPlan` schema is not yet shipped. | 2 |
| PIPE-003 | PLANNED | Current upstream adapter renders directly from target/reference paths. | 2 |
| PIPE-004 | SCAFFOLDED | Jobs/events are scoped, and upstream runs are locked/reset to contain global logs. A native core without global state does not exist yet. | 2 |
| PIPE-005 | PLANNED | No reference profile/cache or cache key. | 4 |
| PIPE-006 | SCAFFOLDED | `amount` is typed/range-checked; both runnable engines currently require exactly `1.0` and reject partial matching. The interpolation rule/DSP is not implemented. | 5 |
| PIPE-007 | IMPLEMENTED | The native engine builds one plan from up to 32 references using independent normalized level/frequency weights, SHA-256 duplicate coalescing, stable content ordering, geometric profile combination, and direct one-effective-profile behavior. Analytic/order/duplicate/render regressions pass; public plan serialization remains a separate gap. | 5 |
| PIPE-008 | PLANNED | No plan/filter export schema or round-trip. | 5 |

### Workflows and adapters

| ID | Status | Current implementation/evidence and gap | Phase |
|---|---|---|---:|
| FLOW-001 | SCAFFOLDED | Synchronous `MasteringService.run` and CLI exist; explicit analyze/plan/render API and equivalence test do not. | 2 |
| FLOW-002 | IMPLEMENTED | CLI commands, path selection, output rendering, catalog finalization, and exit categories have contract coverage. A real subprocess CLI acceptance job completed two-reference native rendering across limited/normalized/raw outputs and reimported its strict selection into a fresh catalog. | 4 |
| FLOW-003 | SCAFFOLDED | The portal accepts 1–32 distinct targets with one shared 1–32-reference request, validates every target without materializing, then runs targets sequentially under an explicit continue-independent-targets policy. Every target receives a unique run/configuration/selection/manifest/event/artifact namespace, and focused tests prove partial-failure continuation and catalog indexing. One immutable analyzed reference profile is not yet reused, and resource-bounded parallelism is absent, so the full requirement remains incomplete. | 4 |
| FLOW-004 | PLANNED | No coordinator yet renders separate alternatives for one target/reference pair each. The implemented PIPE-007 combined profile is one plan/render and does not satisfy this batch-comparison requirement. | 4 |
| FLOW-005 | SCAFFOLDED | `PortalApplication` provides GUI-independent non-materializing single/batch validation plus prepare/submit/status/result/event operations with one serialized background worker, sequential per-target expansion, aggregate results, and structured failure capture. State is process-local and bounded; persistent coordination, parallelism, and cooperative cancellation do not exist. | 4 |
| FLOW-006 | SCAFFOLDED | Raw FLOAT output can be requested and labeled in config. No bundled plan/measurement/note inventory or smoke. | 4 |
| FLOW-007 | IMPLEMENTED | The GUI-first portal packages three responsive primary workspaces with no Electron/external assets. Master is a unified composer for searchable multi-select inputs/references, required delivery choices, collapsed optional/advanced settings, readiness, validation, dry run, and render. Library combines source/version management and explicit export; Activity combines History with Event log and System utilities. The persistent native A/B dock offers direct candidate selection, swap with playhead preservation, and a non-destructive Quick Play preview. It binds only random-port loopback, token-gates page/APIs, separately authenticates catalog-ID-only media Range requests, checks Host/origin, refuses CORS, emits CSP/defensive headers, uses no CDN/upload/arbitrary-file route, selects paths through fixed-script native dialogs, and exposes serialized background status/events with idle-only shutdown. Application, HTTP, native-dialog, CLI-dispatch, launcher, semantic asset, and viewport-configurable headless Chromium contracts pass; interactive codec/A-B/native-picker/accessibility/operator-render evidence remains pending, and cancellation is visibly unavailable. | 6 |
| FLOW-008 | IMPLEMENTED | The GUI workspaces and terminal/CLI fallback manage content-addressed targets/references, source-centered original/master version lineage, metadata-only source labels, exact version/deliverable choices, cross-song exclusive batch-copy export, recoverable version quarantine/restore, search/locations, original archive/restore/relink, complete named-set editing, strict full-catalog/one-target-selection export/reimport, idempotent preserved-run recovery, capability-aware 1–32-target/1–32-reference jobs, a versioned default/per-job delivery-root workflow, live/durable events, System/Session logs, and catalog-backed run/artifact/manifest audition/inspection. GUI selection import reloads its one target, reference order, and independent weights into Master. Focused tests cover master lineage filtering, label validation, copy collisions/per-item results, quarantine integrity/shared-path refusal/no-overwrite restore, multi-target validation, per-target collision-safe materialization, partial-failure continuation, preferences, media streaming, and revised assets; a real subprocess two-reference run completed all outputs, indexed ten artifacts, and reimported its selection into a fresh catalog. | 4 |

### Robustness, safety, and determinism

| ID | Status | Current implementation/evidence and gap | Phase |
|---|---|---|---:|
| ROB-001 | SCAFFOLDED | Preflight blocks silence, near-silence, path/file-identity collision, invalid length/channels/capabilities and warns on mono/rate/clipping. Hardlink, required-log, config-protection, and input-mutation regressions pass; the complete edge table is not implemented. | 3 |
| ROB-002 | SCAFFOLDED | The native path enforces finite intermediate/output values and an optional post-smoothing `max_eq_gain_db` ceiling. Complete ratio, total gain, filter-energy, and adversarial-corpus bounds remain pending. | 3 |
| ROB-003 | SCAFFOLDED | Manifest and portal catalog/selection/configuration/preferences JSON publication are atomic/no-overwrite, each portal target receives an exclusively created run-named delivery subfolder, an existing subfolder is never reused or removed, uncommitted owned scaffolds are cleaned, required JSONL failures fail the run, and commit/close failures gain compensating terminal events. Upstream audio still writes directly to final paths inside the newly owned subfolder, so multi-artifact publication is not transactional. | 3 |
| ROB-004 | SCAFFOLDED | Maximum duration and engine constraints exist; the portal enforces one background mastering worker, rejects overlap, and refuses explicit shutdown while active. Desktop last-tab exit waits for active work to finish, then releases the process; source/manual modes persist. Memory/output budgets and cooperative cancellation do not exist. | 3 |
| ROB-005 | SCAFFOLDED | Weighted profile identity/decoded samples are invariant to reference order and explicit duplicate coalescing in focused regressions, and the single-reference native path matches upstream decoded samples. Cross-platform tolerances and a golden corpus remain pending. | 1–3 |
| ROB-006 | IMPLEMENTED | Preflight explains mono duplication/zero genuine Side width, and a real mono-target render reopens as finite stereo while retaining the warning. | 2–3 |
| ROB-007 | IMPLEMENTED | Private config uses constructor/cross-field validation instead of `assert`; duplicate/unknown/non-finite JSON, boolean numerics, and DSP-domain boundaries have regression coverage. | 3 |

### Evidence, observability, security, and governance

| ID | Status | Current implementation/evidence and gap | Phase |
|---|---|---|---:|
| OBS-001 | SCAFFOLDED | Each target's versioned `RunManifest` includes its target and every requested reference occurrence, independent weights/effective groups, configuration and one-target selection artifacts, dependency/environment data, observed render facts, output/previews/event fingerprints, native plan details, warnings/error, and catalog provenance. Multi-target submissions retain an aggregate result that links per-target states/run IDs without replacing those manifests. Stage timing, standalone plan schema, retention/redaction, and cancellation remain incomplete. | 2 |
| OBS-002 | SCAFFOLDED | Per-job sequenced deeply immutable events and sinks have success/failure/finalization tests; required JSONL is synced on close and compensating terminal failures preserve manifest agreement. The portal adds a bounded live operation sink/projection and private request/worker logs. Schema version/monotonic offset, comprehensive redaction, and concurrency stress remain. | 2 |
| OBS-003 | SCAFFOLDED | Activity exposes History and raw/summary manifests, with Event log and System/Session logs as adjacent utilities; terminal/CLI views expose the same durable evidence without console transcription. No single derived operator report yet assembles the complete cause/remediation chain. | 4 |
| SEC-001 | IMPLEMENTED | Core/service has no designed telemetry or remote-network dependency. The GUI adapter opens only an intentional loopback listener and packages every asset locally. Required network-denied core integration evidence remains absent. | 3 |
| SEC-002 | SCAFFOLDED | Canonical/symlink/hardlink collision, configuration protection, strict schema/JSON/media preflight, bounded portal JSON, and fixed-script native-dialog process arguments exist. Allowed-root/traversal/junction escape, decode limits, and secure FFmpeg execution remain. | 3 |
| SEC-003 | SCAFFOLDED | Catalog data stays local, source audio is not copied/deleted, archive is reversible, and exports are explicit. No retention sweep, reference-cache consent, or comprehensive redaction test exists. | 4 |
| SEC-004 | IMPLEMENTED | Server construction rejects non-loopback bind, normal launch uses a random port/fresh API token and a separate `HttpOnly`/`SameSite=Strict` media-path cookie, launcher transcripts close before portal handoff, and access logs redact both secrets and query values. Tests cover token/Host/origin/CORS/strict JSON/CSP/headers plus catalog-ID-only full/open/suffix/HEAD Range streaming, invalid/unsatisfiable ranges, cookie scope, and traversal/query rejection; the implementation also checks the preferred location's regular-file type, size, and modification time before streaming. Assets are local with no Electron/upload/arbitrary-file route, and native-dialog tests prove request data is not script text. Headless Chromium retained artifacts are token-redacted. Same-user process trust, no TLS/accounts, browser codec variation, and the no-proxy/LAN boundary are explicit; penetration and interactive-browser evidence remain pending. | 6 |
| TEST-001 | SCAFFOLDED | Ten deterministic WAV fixtures and baseline source exist. No upstream characterization runner, goldens, environment-pinned corpus, or expected-failure catalog. | 1 |
| TEST-002 | IMPLEMENTED | Focused suites cover all three output modes, previews, mono, single-reference parity, weighted analytic/order/duplicate/render behavior, catalog persistence/exchange/integration, multi-target validation/partial-failure/collision handling, strict output preferences, authenticated HTTP Range media, portal assets/native-dialog/CLI contracts, terminal workbench behavior, Windows launcher safety, and headless Chromium bootstrap/shutdown. The configured aggregate branch gate is 85%; interactive A/B codec/operator/accessibility, golden, profile-reuse/parallel-batch, container, and multi-platform suites remain. | 1–6 |
| TEST-003 | PLANNED | Listening protocol is documented but no review corpus/record has been executed. | 5 |
| GOV-001 | SCAFFOLDED | Root/upstream GPL text, GPL metadata, preserved source, private classifier, and constrained dependencies exist. Hash provenance and dependency-license inventory are pending. | 0–5 |
| GOV-002 | SCAFFOLDED | Requirements, ADRs, process docs, and this register exist. Test metadata does not yet carry requirement IDs and no automated documentation/link/status check exists. | 0–5 |

## Test evidence snapshot

The 2026-09-03 full non-coverage gate collected 228 tests: **227 passed and one
case-distinct-path test skipped on the case-insensitive Windows filesystem**.
The redesigned asset slice contributes 14 semantic/security/accessibility
contracts. Isolated Chromium runs at 1440 × 1000 and 1280 × 800 exercised all
three workspaces plus a populated six-source/three-version Library, direct A/B
candidate selection, swap and playhead continuity, responsive version actions,
token-redacted artifacts, and graceful portal shutdown. The normal private
workspace was not used by those browser runs.

The most recent aggregate branch-coverage measurement remains the following
2026-07-30 snapshot; a newer coverage measurement was not taken in this UI
turn.

The 2026-07-30 full local gate collected 224 tests: **223 passed and one
case-distinct-path test skipped on the case-insensitive Windows filesystem**,
with **87.78% aggregate branch coverage** against the configured 85% gate. It
covers the implementation and the then-current seven-tab interface baseline.
The Dashboard/Master Job/Music Library wording in this dated list is retained
as historical evidence; current frontend contracts instead assert the three
primary workspaces, unified Master flow, readiness state, direct A/B controls,
responsive behavior, and initialized application-state sentinel. The snapshot
covers:

- strict configuration JSON, enum/numeric/cross-field boundaries, stable
  extreme-weight normalization, schema/path round trips, and truthful
  per-engine reference limits;
- PCM 8/16/24/32 and SoundFile FLOAT probing, non-finite detection, malformed
  input diagnostics, and fallback behavior;
- silence, near-silence, mono, multichannel, duration, sample-rate, clipping,
  lossy-extension, and configurable policy findings;
- canonical, symlink, hardlink, duplicate-output, config/output, and immutable
  audit-path collision protection;
- truthful current/planned engine capabilities and actionable unsupported
  constraints;
- deeply immutable events/errors/artifact metadata, required JSONL behavior,
  manifest ownership, concurrent mutation detection, and injected close/commit
  recovery;
- environment doctor dependency/version/FFmpeg boundaries and all CLI
  commands/exit categories/path-selection contracts;
- real `matchering.process()` renders for limited PCM-24, normalized PCM-24,
  raw FLOAT, paired previews, and a mono-to-stereo target path;
- analytic independent geometric level/frequency blending, direct
  one-effective-profile behavior, SHA-256 duplicate coalescing, stable
  order-invariant reduction, one-reference decoded-sample parity, finite
  two-reference outputs, and aligned weighted previews;
- catalog identity/location/archive/relink, weighted sets, strict/tamper-aware
  selection and full-catalog import/export, manifest integration, CLI adapter,
  and run/artifact indexing;
- GUI portal application/HTTP/native-dialog contracts, terminal-fallback
  workbench menu/job/catalog behavior, and root Windows launcher
  environment/diagnostic safety;
- strict atomic output-folder preferences, 1–32-target validation and
  expansion, exclusive per-target delivery subfolders, aggregate
  continue-after-failure results, and per-target manifest/catalog retention;
- catalog-ID-only authenticated media serving with full/open/suffix byte
  ranges, HEAD, invalid/unsatisfiable range handling, cookie-scope and
  traversal/query rejection, plus secret/query log redaction;
- packaged HTML/CSS/JavaScript asset contracts, Session Logs behavior,
  token-redacted HTTP logging, and 1440 × 1000 headless Chromium Dashboard and
  direct Master Job bootstraps, initialized DOM/screenshots, authenticated
  shutdown, and clean portal exit;
- populated 1400 × 1000 and 1280 × 800 Music Library browser reviews with long
  labels and histories, automatic one-version A/B assignment, explicit
  two-version choice, responsive detail stacking, and retained screenshots;
- an ignored local operator-acceptance run from
  `artifacts/operator-acceptance/job-fixed.json`: two-reference native
  limited/normalized/raw rendering, zero non-finite samples, automatic
  selection/catalog finalization, manifest plan SHA-256, ten indexed
  artifacts, and strict selection reimport into a fresh catalog;
- a catalog-ingestion regression for the recursive tuple-metadata
  normalization defect exposed by the first acceptance attempt;
- post-render reopen checks for finite samples, sample rate, channel count,
  frame count, preview length, and observed subtype; and
- an 85% configured aggregate branch-coverage gate.

Ruff lint and format checks are clean across 60 files, and strict mypy is clean
across 28 source files. All nine explicit audio smoke tests pass. Wheel
construction passes both with `--no-isolation` in the prepared environment and
with a standard isolated build; the resulting wheel contains all three
packaged GUI assets.

The platform-conditional skip is the case-distinct path regression on the
case-insensitive Windows filesystem. The gate reports one inherited Matchering
2.0.6 `scipy.ndimage.filters` deprecation warning during DSP tests.

## Known status mismatches to resolve

These are intentional visibility items, not hidden defects:

- The target event envelope names fields such as schema version and monotonic
  offset that the current event model does not yet fully expose.
- The job schema represents planned native controls by design; capability
  validation, not schema presence, is the implementation truth.
- The native weighted path is experimental and still uses pinned Matchering
  stage implementations/global locking internally; it is not yet the
  independent public analyze/plan/render architecture.
- Catalog/selection export v1 stores resolved private paths. A restored catalog
  must be verified and byte-identical moved files explicitly relinked; there
  is no root-remapping import option yet.
- Upstream audio files are reopened and verified but still written directly to
  final names; transactional multi-artifact publication remains a required
  milestone.
- Manifest ownership is checked before atomic replacement, but there is not yet
  a job-lifecycle OS lock or parent-directory `fsync`; a competing process can
  still race the final check on filesystems that permit it.
- Portal operation history/events are bounded process-memory projections. A
  browser refresh does not cancel work, a portal restart loses that projection,
  and the durable per-target JSONL/manifest/catalog evidence must be used
  afterward. A multi-target result is an aggregate summary rather than a
  durable batch manifest. Within one portal process, a reloaded page reconstructs
  target rows from retained result/prepared/event evidence; if an operation
  fails before recording any target identity, only its global failure can be
  reconstructed.
- The portal token protects a loopback desktop session, not against another
  process running as the same user. The adapter has no supported non-loopback,
  proxy, tunnel, container-publish, multi-user, or TLS mode.
- Cooperative cancellation is not implemented. The control is disabled,
  overlapping work is rejected, batch targets run sequentially, and portal
  shutdown refuses an active worker.
- A/B playback uses the installed browser's native codec support and streams
  catalog media without transcoding or loudness matching. Direct A/B pickers
  and swap preserve the current playhead; Quick Play temporarily previews a
  candidate without changing either saved comparison assignment. A valid
  mastering input may therefore still be unavailable in the audition dock.
- Windows/Python 3.11 is the only currently supported and tested private
  baseline. Other platforms and Python minors require explicit matrix
  evidence before metadata expansion.

## Next evidence to collect

The highest-value next steps are:

1. stage, reopen, and atomically publish engine outputs instead of writing
   directly to final names;
2. build the upstream characterization runner and lock initial numerical
   metrics/goldens;
3. promote the internal reference-profile/plan evidence into versioned public
   `AudioAnalysis`, `ReferenceProfile`, and replayable `MatchPlan` contracts;
4. add cancellation and resource/output budgets with fault injection;
5. add a lifecycle lock and durable directory synchronization around manifest
   publication;
6. perform a supported-workstation interactive
   browser/WinForms/accessibility/operator acceptance pass, including real A/B
   playback, output-default/override selection, a partial-failure target
   batch, and a GUI-started render (the three-workspace semantic contracts and
   viewport-configurable headless bootstrap/shutdown smoke are available);
7. add explicit requirement/pytest-tier metadata and documentation link/status
   checks;
8. extend the successful subprocess acceptance evidence with the documented
   listening protocol on rights-cleared private material;
9. exercise the retained official container when its runtime is available; and
10. add Windows/Python update and eventual POSIX platform matrices before
   broadening support metadata.

## Update rule

Change this file in the same change set as implementation/tests. Include the
date and exact evidence. Never promote a requirement from `IMPLEMENTED` to
`VERIFIED` on code inspection alone.
