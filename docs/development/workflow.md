# Development workflow

Status: **working private baseline; weighted native and GUI-first operator slices implemented**

This workflow keeps DSP changes reproducible, focused, and diagnosable. The
current supported baseline is Windows, PowerShell, and Python 3.11. Linux,
macOS, and other Python minors require an explicit test matrix before support
metadata is broadened.

## Current repository shape

```text
configs/                       # expanded job profiles
docs/                          # product, design, process, policy, status
Launch-Music-Mastering-Tools.cmd # root double-click GUI-first launcher
matchering-master/             # extracted upstream 2.0.6 evidence
private-workspace/             # ignored catalog/jobs/runs/outputs/exports/logs
requirements/                  # runtime/dev constraints
schemas/                       # editor/automation schemas
scripts/                       # bootstrap, quality, test, smoke, fixtures
src/music_mastering_tools/     # private launchpad package
tests/                         # executable unit/integration/regression evidence
```

`artifacts/`, `private-workspace/`, `.mmt/`, generated fixtures, and virtual
environments are ignored runtime state. The original ZIP and extracted
upstream tree are intentionally not ignored because they are provenance
inputs.

## Bootstrap

From PowerShell at the workspace root:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Bootstrap.ps1
```

This creates or reuses `.venv`, verifies Python, and intentionally does **not**
install dependencies. It never deletes an existing environment.

Install the private project, pinned Matchering baseline, and developer tools
when package access is available:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Bootstrap.ps1 `
  -InstallDependencies
```

For a prepared local wheelhouse:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Bootstrap.ps1 `
  -InstallDependencies `
  -Offline `
  -Wheelhouse .\wheelhouse
```

The install records `pip freeze --all` under `artifacts/environment/`. The
constraints are compatibility bounds, not a complete cross-platform lock.
Promote reviewed environment records into platform locks once full
characterization is operational.

The explicit `-ExecutionPolicy Bypass` applies only to that child PowerShell
process. It is required on the supported workstation because its AllSigned
policy blocks unsigned repository scripts; the commands do not change the
machine or user execution policy.

If Git reports "dubious ownership," fix repository ownership or explicitly
approve only this exact path according to local security policy. Do not add a
wildcard safe-directory exception.

## Environment diagnosis

After installation:

```powershell
.\.venv\Scripts\python.exe -m music_mastering_tools doctor
.\.venv\Scripts\python.exe -m music_mastering_tools doctor --strict-mastering
.\.venv\Scripts\python.exe -m music_mastering_tools capabilities
```

The installed console entry point is `mmt`, so the equivalent commands after
environment activation are:

```powershell
mmt doctor
mmt capabilities
```

`doctor` is non-throwing for environmental checks and can emit JSON. The
capability report distinguishes the verified one-reference Matchering
compatibility adapter from the experimental runnable native engine. The latter
accepts one through 32 independently weighted references and an optional EQ
ceiling, while truthfully rejecting the advanced controls it does not yet
implement.

## Deterministic fixtures

The generator uses the standard library and currently specifies ten WAV files
plus a manifest:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_wav_fixtures.py
.\.venv\Scripts\python.exe .\scripts\generate_wav_fixtures.py --check
```

Or run the verified fixture-only wrapper:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-SmokeTest.ps1 `
  -FixturesOnly
```

Generated files are reused only when hashes match. A differing existing file is
never overwritten; move/delete it deliberately after preserving any evidence.
Output paths outside the repository are rejected.

## Job configuration

[`configs/upstream-baseline.json`](../../configs/upstream-baseline.json) is a
fully expanded compatibility job requesting:

- limited PCM24;
- normalized non-limited PCM24;
- raw FLOAT DAW handoff; and
- paired previews.

It expects private inputs under `audio/`; copy the profile and update paths.
Paths resolve relative to the configuration file.

[`configs/weighted-references.json`](../../configs/weighted-references.json) is
a runnable native two-reference job. It demonstrates:

- independent level and frequency weights;
- the optional post-smoothing EQ ceiling;
- limited, normalized, and raw FLOAT output branches; and
- paired previews.

The native engine accepts one through 32 requested references. The GUI portal
and terminal fallback deliberately choose the exact upstream path for one
reference and the weighted native path for two or more.

[`configs/native-target.json`](../../configs/native-target.json) is an
executable specification of planned controls beyond the current native subset.
It intentionally fails capability validation because it requests partial match
amount, EBU R128, true peak, metadata copy, and dither. Schema presence does
not mean an engine can execute an option.

Inspect and preflight:

```powershell
mmt show-config .\configs\upstream-baseline.json
mmt validate .\configs\upstream-baseline.json
mmt validate .\configs\upstream-baseline.json --json
mmt run .\configs\upstream-baseline.json --dry-run
mmt show-config .\configs\weighted-references.json
mmt validate .\configs\weighted-references.json --no-input-check --no-audio-probe
```

Only run a real render after validation, input review, and dependencies:

```powershell
mmt run .\configs\upstream-baseline.json --verbose
```

Jobs write a manifest and JSONL event log under a job path unless overridden.
Every CLI run also writes a content-bound selection, registers its sources in a
local catalog, and indexes the committed manifest/artifacts. The compatibility
and current native parity engines serialize in-process runs because upstream
logging/DSP dependencies are global. Do not infer thread safety from a
successful single run.

## GUI-first portal, terminal fallback, and catalog

Double-click `Launch-Music-Mastering-Tools.cmd` from Explorer. It uses a
process-scoped execution-policy bypass, validates/repairs the private
environment with consent, runs strict diagnostics, starts `mmt gui` on a random
`127.0.0.1` port, and opens the token-gated portal. Launcher transcripts and
per-session portal logs are under `private-workspace/logs/`.

The frontend is repository-owned HTML, CSS, and JavaScript with no Electron
runtime, CDN, or external frontend assets. Its primary navigation is limited to
**Master**, **Library**, and **Activity**. Master is one continuous batch
composer: searchable multi-select catalog pickers feed the input/reference
queues, required delivery stays in the main flow, optional formats and
supported advanced settings use progressive disclosure, and a readiness rail
keeps validation/dry-run/render state visible. Library consolidates
source/version management and export. Activity consolidates History with
**Event log** and **System** utilities; System includes the path-confined
**Session logs** viewer. Reference profiles remain a secondary Master utility.

The persistent comparison dock uses direct A/B candidate selectors across
available originals and mastered deliverables. Swap exchanges the assignments
without resetting the current playhead; Quick Play uses separate transient
preview state and leaves the selected pair unchanged.

Useful adapter commands are:

```powershell
.\Launch-Music-Mastering-Tools.cmd -CheckOnly -NoPause
.\Launch-Music-Mastering-Tools.cmd -NoBrowser
.\Launch-Music-Mastering-Tools.cmd -Terminal
mmt gui --workspace .\private-workspace --no-browser
```

`-CheckOnly` does not instantiate an interface. `-Terminal` is the supported
console fallback. `-NoBrowser` is useful for launch diagnostics and automated
HTTP testing; the private token-bearing URL is printed to the live launcher
window. Before portal handoff, the launcher closes its transcript. Normal
startup output/logging contains only the origin, portal HTTP request logs
replace API/media secrets and query values with `[REDACTED]`, and a fallback URL printed for
`-NoBrowser` or browser-open failure is not persisted by the launcher.

The workbench catalog is
`private-workspace/catalog/catalog.sqlite3`. Command-line catalog actions
default to a different `.mmt/catalog/catalog.sqlite3` unless `--database` is
provided:

```powershell
$catalog = ".\private-workspace\catalog\catalog.sqlite3"
mmt catalog --database $catalog list --include-archived
mmt catalog --database $catalog verify --include-archived
mmt catalog --database $catalog runs --json
```

The complete operator procedure, weighting math, export/reimport behavior,
workspace layout, and artifact inspection commands are in the
[workbench/catalog guide](../user/workbench-and-catalog.md).

Portal implementation changes must keep four boundaries independently
testable:

1. browser assets render controls and translate operator intent;
2. the loopback HTTP adapter validates host/origin/token/JSON and maps errors;
3. `PortalApplication` performs catalog/job operations without importing HTTP
   or browser code; and
4. native dialogs use fixed WinForms scripts and return paths without opening
   media.

Never run DSP on an HTTP request thread. The current portal uses one background
worker and rejects overlapping mastering operations. Tests must not claim
cancel behavior: no cooperative cancellation contract exists. Closing the last
normal desktop tab requests exit after active work finishes; refresh/multiple
tabs retain the session. Manual portal shutdown is allowed only when idle.
Source GUI/CLI, `--no-browser`, and `--keep-running` remain persistent. See the
[lifetime design](../design/browser-session-lifecycle-2026-10.md).

If DSP completed but catalog finalization did not, preserve the manifest,
selection, and configuration and use `mmt catalog --database <path>
recover-run <manifest>`. This re-verifies and indexes existing evidence; it
does not rerender audio. See the
[recovery procedure](../user/workbench-and-catalog.md#recover-a-rendered-run-whose-catalog-finalization-failed).

## Quality and tests

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Quality.ps1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier Unit
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier Integration
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier Regression
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier Smoke
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier All -Coverage
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-SmokeTest.ps1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-PortalSmoke.ps1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-PortalSmoke.ps1 `
  -InitialTab catalog -ViewportWidth 1280 -ViewportHeight 800
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-PortalSmoke.ps1 `
  -InitialTab runs -ViewportWidth 1024 -ViewportHeight 768
.\.venv\Scripts\python.exe -m build --wheel
.\.venv\Scripts\python.exe -m build --wheel --no-isolation
```

The current suite covers configuration and inherited DSP domains,
probes/policies, engine capabilities, events/manifests and injected audit
failures, doctor/CLI contracts, dry-run orchestration, real Matchering renders,
all three output modes, paired previews, mono-to-stereo behavior, weighted
profile math/parity/order/duplicates, catalog models/adapters/integration,
multi-target expansion/partial failure, output preferences, authenticated media
Range handling, portal application/HTTP/native-dialog/asset/CLI dispatch,
terminal workbench behavior, launcher safety, and secret/query-redacted access
logging. Frontend asset tests assert the three primary workspaces, unified
Master flow, searchable multi-select hooks, readiness state, progressive
disclosures, direct A/B selection/swap/Quick Play behavior, accessible names,
and responsive/focus/motion/contrast CSS. The Chromium smoke accepts explicit
workspace and viewport parameters and requires the initialized
`data-app-state="ready"` sentinel plus workflow landmarks. The configured
aggregate branch gate is 85%. After the editable install performed by
`Bootstrap.ps1 -InstallDependencies`, tests can also be run through
standard-library discovery:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The full PowerShell smoke wrapper imports Matchering and therefore needs the
DSP dependencies. Registered integration/regression tests prove real limited,
normalized, raw FLOAT, paired-preview, mono-target, and weighted-reference
renders.

The historical 2026-07-27 Windows/Python 3.11 evidence is retained below. Its
Dashboard/Master Job labels describe the interface at that date, not the
current three-workspace information architecture:

- 217 collected tests: 216 passed and the case-distinct-path test skipped on
  the case-insensitive filesystem, with 88.97% aggregate branch coverage;
- Ruff lint and format clean across 60 files, plus mypy clean across 28 source
  files;
- 9/9 explicit audio smoke tests passed;
- `Invoke-PortalSmoke.ps1` passed in headless Chromium at 1440 × 1000 for its
  default Dashboard and `-InitialTab master-job`, proving packaged-asset
  bootstrap, initialized Session Logs/private-workspace UI, screenshot/DOM
  capture, API shutdown, clean process exit, and token redaction in retained
  text artifacts; and
- wheel construction passed both with `--no-isolation` in the prepared
  environment and with the standard isolated build; inspection found all three
  packaged GUI assets in the wheel.

The headless GUI smoke does not select native files or run mastering and is not
an accessibility/operator acceptance pass. The remaining evidence gap includes
those interactive checks, full characterization/golden parity, and
transactional multi-artifact publication; see the
[status register](../status/implementation-status.md).

Test artifacts go to:

- `artifacts/tests/` for detailed logs and JUnit XML;
- `artifacts/coverage/` for coverage data/XML;
- `artifacts/smoke/` for generated smoke fixtures;
- `artifacts/portal-gui-smoke/` for token-redacted DOM/browser/process logs and
  the GUI screenshot; and
- job-specific manifest/event locations for processing diagnostics.

Do not commit runtime artifacts or private source audio.

## Change workflow

### 1. Frame the change

- Identify requirement IDs and current status.
- State whether the change is parity, defect correction, intentional sonic
  change, adapter/tooling, or documentation.
- Identify edge-policy and security/licensing impact.
- Write/update an ADR before coding when a durable boundary changes.

### 2. Establish evidence

- Add or select deterministic fixtures.
- For upstream DSP behavior, run and record characterization first.
- Add a regression that fails for the intended pre-change reason.
- Define numerical tolerance and listening need before inspecting a desired
  result.

### 3. Implement narrowly

- Keep adapters outside domain/DSP.
- Pass configuration and events explicitly.
- Do not silently fall back from requested native/planned features to the
  upstream adapter.
- Preserve private audio and unrelated workspace changes.
- Add detailed debug events around cause/effect boundaries, with redaction.

### 4. Verify

- Run the smallest relevant tier during iteration.
- Run fast quality/unit/smoke gates before review.
- Run full regression for DSP, dependency, format, schema, limiter, resampling,
  cache, or concurrency changes.
- Inspect generated manifest/events and artifacts, not only exit code.
- Conduct and record level-matched listening for intentional sonic changes.

### 5. Document and trace

In the same change set:

- update requirement acceptance when semantics changed;
- update architecture/ADR if boundaries changed;
- link implementation and tests in the status register;
- update config examples/operator instructions; and
- record algorithm/schema compatibility impact.

## Definition of ready

Work is ready to implement when:

- scope and non-goals are explicit;
- requirement IDs and acceptance criteria exist;
- baseline/fixture and expected outcome are identified;
- security, privacy, licensing, and migration impact are considered;
- dependencies and external authority are known; and
- no unresolved choice would materially change the implementation.

## Definition of done

A behavior is done only when:

- implementation satisfies all acceptance criteria;
- automated tests include normal, boundary, failure, and regression coverage;
- relevant supported-environment gates pass;
- events/manifests explain success and failure;
- output artifacts are reopened and independently checked where applicable;
- no unexplained characterization delta remains;
- listening approval exists when sound intentionally changed;
- documentation, examples, schemas, status, and ADRs are current;
- dependency/license/provenance records are current; and
- the requirement is moved to `VERIFIED` with dated evidence.

Code present without complete evidence is `IMPLEMENTED`, not done.

## Defect workflow

1. Preserve the original failure manifest/events and safe fixture parameters.
2. Reduce to generated media when possible.
3. Add a named regression tied to requirement/edge-policy ID.
4. Diagnose the first incorrect state, not only the final exception.
5. Fix without suppressing non-finite or resource errors.
6. Run neighboring normal cases and full affected stage tests.
7. Document the cause/effect chain and any new event/error code.

This minimizes the need for a user to manually find and transcribe errors.

## Dependency updates

Dependency changes are behavior changes for this numerical/native stack:

1. update declared bounds/lock candidate;
2. record license and provenance;
3. create a fresh environment record;
4. run import/doctor, format/decode, full characterization/regression, and
   performance comparison;
5. review numerical deltas rather than overwriting goldens;
6. update supported-environment documentation; and
7. commit the lock/evidence decision together.

## Upstream sync

- Preserve the original 2.0.6 evidence unchanged.
- Fetch/import a later upstream version into a separately identified location.
- Record source revision, license, and file hashes.
- Compare public API, log codes, algorithm, dependencies, and issues.
- Run both baselines over the characterization matrix.
- Adopt changes intentionally into the private engine/adapter; never replace
  the baseline silently.

## Review checklist

- [ ] Requirement and status are accurate.
- [ ] No planned option is reported as implemented.
- [ ] No global mutable job state was introduced.
- [ ] Failure and cancellation paths were tested.
- [ ] Logs are useful at debug level and safe at normal level.
- [ ] Units and audio method names are precise.
- [ ] Artifacts are transactional and collision policy explicit.
- [ ] Every requested target/reference/output/audit artifact remains visible in
      the manifest and catalog with its exact role.
- [ ] Catalog migrations/imports are transactional; source audio is never
      copied or deleted as a side effect of catalog management.
- [ ] Portal changes preserve loopback-only bind, session-token, Host/origin,
      CSP/no-store, strict body, no-CDN, and no-upload boundaries.
- [ ] Frontend changes preserve the three primary workspace semantics, keep
      utility panels out of the primary tab set, derive readiness from every
      required prerequisite, and retain direct A/B swap/playhead plus
      non-destructive Quick Play contracts across responsive viewports.
- [ ] Native dialog values remain data passed to fixed scripts; selected media
      is never executed.
- [ ] Background UI behavior remains serialized and does not claim
      cancellation or allow shutdown during active mastering.
- [ ] Weighted-reference changes preserve independent normalization,
      duplicate coalescing, stable ordering, and the 32-request limit.
- [ ] Private audio, paths, hashes, and secrets are absent from commits.
- [ ] License/provenance/dependency records are current.
- [ ] Windows packaging and its frozen smoke pass when distribution behavior
      changes; no workflow publishes a release automatically.

## Milestones and publication

Windows executable work and source commit/push to
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster) are authorized under
the owner's 2026-10-02 instructions. Follow the [release guide](../deployment/windows-release.md)
for pinned builds, frozen smoke, source allowlist auditing, license inventories,
and exact corresponding source. Build output remains local; CI uploads selected
diagnostics and contains no binary upload or automatic release-publishing step.
Record the
source commit, push result, and remote branch identity. Public binary-release
upload remains separate from this authorized source push.
