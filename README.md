# Music Mastering Tools

A local, auditable reference-guided mastering workbench built around
Matchering 2.0.6.

Source repository: [Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster).

The workbench now supports both an exact one-reference compatibility path and
a deterministic native profile made from multiple references. It also provides
a content-addressed track catalog, reusable weighted reference sets,
reimportable JSON inventories, per-run manifests/events, a compact multi-target
GUI queue, in-application A/B auditioning, operator-selected delivery folders,
and a root launcher for a GUI-first interactive workflow.

## Current capability boundary

| Area | Status |
|---|---|
| 1–32 targets + one reference | Verified Matchering 2.0.6 compatibility adapter, run separately for each target |
| 1–32 targets + 2–32 references | Experimental runnable native profile engine with independent level/frequency weights, duplicate coalescing, deterministic ordering, optional guarded EQ, and the same weighted reference request applied in a separate run per target |
| Limited, normalized/no-limiter, raw FLOAT, and paired previews | Implemented and reopened after rendering for format, shape, duration, and finite-sample checks |
| Source-centered music library and named weighted sets | Implemented locally in SQLite; each original remains one row while completed masters are attached as versions, and source audio is referenced rather than copied |
| Reimport and complete run visibility | Versioned catalog/selection JSON, fingerprinted manifests, JSONL events, and searchable run/artifact inventory |
| GUI-first private workbench | Localhost browser portal organized around Master, Library, and Activity; includes searchable batch selection, a readiness-led run flow, native Windows file/folder pickers, source/master version management, direct A/B selection and swapping, recoverable take cleanup, batch master export, Session Logs, and serialized background mastering |
| Multi-target coordinator | 1–32 distinct targets run sequentially with independent manifests/artifacts and an explicit continue-after-target-failure policy |
| Output destination control | Versioned persistent default plus per-job override; every target receives an exclusive run-named subfolder and existing content is never reused or overwritten |
| Double-click launcher and terminal fallback | Implemented for the supported Windows/Python 3.11 baseline; GUI is the default and `-Terminal` retains the console workflow |
| Match amount, EBU R128, true peak, dither, metadata copy, external limiter | Represented but capability-rejected by both current engines |
| Reference-profile cache/reuse, bounded parallelism, cancellation, resource budgets | Planned |
| Windows 10/11 x64 executable and GitHub preparation | Active sharing milestone; console-free EXE with per-user state, pinned build, inventory/notices, and executable smoke workflow |

The authoritative detail is the
[implementation status register](docs/status/implementation-status.md).

## Run the Windows executable

Double-click `MusicMasteringTools.exe` from the Windows release candidate. It
opens the workbench in your default browser and needs no Python installation or
administrator rights. Each Windows user's library, preferences, recovery
records, and logs live in `%LOCALAPPDATA%\MusicMasteringTools\workspace`.
Source audio stays at your selected paths, and moving or replacing the EXE
preserves your library. Closing the last workbench tab exits the desktop
application after a short grace period; an active render finishes first.
Refreshing a tab or keeping another workbench tab open retains the session.
Use **Shut down portal** for an immediate idle exit, or launch with
`--keep-running` to retain the server after closing the browser. `--no-browser`
also keeps it running for manual/developer workflows.

The source launcher's older library remains in `private-workspace/`; the EXE
uses its separate per-user default. See
[continue an existing library](docs/deployment/windows-release.md#continue-an-existing-checkout-library)
if tracks from source testing appear missing. The catalog and songs are
preserved; choose the old workspace or relink moved files.

Build instructions, diagnostics, backup/relink guidance, GitHub preparation,
and remaining independent-machine checks are in the
[Windows release guide](docs/deployment/windows-release.md). A concise
[Windows quick start](docs/user/windows-quick-start.md) is available for
recipients. Actual verification
results are recorded in the
[implementation register](docs/status/implementation-status.md).

## Start from a source checkout

On Windows, double-click
[`Launch-Music-Mastering-Tools.cmd`](Launch-Music-Mastering-Tools.cmd) in the
repository root.

The launcher always uses this repository, checks the supported environment,
runs strict mastering diagnostics, starts a private server on
`127.0.0.1` using a randomly available port, and opens the graphical portal in
the default browser. If `.venv` is missing or unusable, it asks before
installing the base dependencies; type `YES` only when package installation is
available. Launcher and portal diagnostics are retained under
`private-workspace/logs/`.

The portal is a local application, not a hosted site. Its API requires a
per-launch random token, all browser assets ship in this repository, and it
has no upload endpoint, CDN, telemetry, or remote metadata lookup. Native
Windows dialogs select files by path; source audio is fingerprinted and read
in place rather than copied through the browser. The lightweight frontend is
packaged HTML, CSS, and JavaScript; there is no Electron runtime or external
frontend asset.

On a normal successful launch, the console and portal log show only the local
origin and log path; they do not print the token-bearing URL. That private URL
is printed only for `-NoBrowser` or when automatic browser opening fails.
The launcher closes its transcript before handing control to the portal, and
portal HTTP logs replace any request token with `[REDACTED]`, so fallback URLs
shown in the live console are not retained in either log.

Quick start:

1. In **Library**, use **Add audio…** to select input and reference
   tracks with the native Windows picker. Originals remain the primary rows;
   completed master versions and their deliverables appear beneath the
   corresponding original.
2. In **Master**, use the searchable **Choose from library** controls to add one
   to 32 mixes and one to 32 references in batches, or load a saved reference
   profile. For several references, edit the independent level and tone weights.
3. Choose the delivery folder for this job. Use **Remember** to persist it for
   later sessions, or **Default** to restore the saved destination.
4. Keep the standard limited master or expand the optional delivery and
   advanced disclosures for normalized, raw FLOAT, paired-preview, processing,
   or safety settings. Each target receives an exclusive run-named subfolder.
5. Follow the **Review & run** readiness rail, then use **Check setup** and
   **Test run**. Start the final render from the same rail; the current setup
   stays visible in Master while per-mix state updates. When successful outputs
   exist, **Review masters** opens them in Library. Use **Activity > Event log**
   only for deeper live detail and **Activity > History** for durable evidence.
6. In **Library**, select an original to prepare A = original and B = the
   preferred playable master. The dock's direct A and B selectors can choose
   any playable original or mastered deliverable; **Swap** exchanges them while
   A/B switching preserves the playhead when possible. Quick Play uses a
   temporary preview and does not overwrite the prepared pair.
7. Select preferred versions, choose an export suffix, and copy a verified
   batch to another folder. **Discard** moves one complete mastered version
   into the private recovery area; **Restore** returns it to its original paths.
8. For the EXE, close the final workbench tab to exit; active mastering finishes
   before shutdown. Refreshing or closing one of several tabs retains the
   session. The source launcher, `--no-browser`, and `--keep-running` sessions
   stay running until an explicit idle **Shut down portal** action.

Only one dry run or render operation is accepted at a time. Targets within a
multi-target operation run sequentially. A failed target is retained as its
own audited result and does not prevent later independent targets from
running. The portal remains responsive; safe cancellation is not implemented,
and the shutdown action refuses to stop an active operation.

The audition dock streams cataloged original or master bytes through a
catalog-ID-only, authenticated local HTTP Range route. Selecting an original
with a playable master prepares the preferred comparison automatically; direct
candidate selectors and **Swap** make multi-version and cross-song comparisons
explicit, and switching between A and B preserves the current playhead when
possible. Quick Play is a separate temporary preview. The dock does not
upload, copy, loudness-match, or transcode audio. Playback therefore depends on
the formats/codecs supported by the installed browser; a file can remain valid
for mastering even when that browser cannot preview it.

Library renaming changes only private display metadata, never the source
filename, bytes, hash, or historical manifest. Batch export creates
fingerprint-verified copies with fail-on-collision semantics and reports each
item independently. Recoverable discard moves only the selected completed
version's mastered deliverables; it retains the original, references,
manifest, event log, previews, and a tombstone needed for restoration.

For troubleshooting or accessibility fallback:

```powershell
.\Launch-Music-Mastering-Tools.cmd -Terminal
```

`-CheckOnly` still validates the environment without starting either
interface. `-NoBrowser` starts the portal and prints its private launch URL in
the launcher window.

See the [private workbench and catalog guide](docs/user/workbench-and-catalog.md)
for catalog maintenance, reimport, weighted-profile math, artifact locations,
and recovery behavior.

## Command-line setup

PowerShell from the repository root:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Bootstrap.ps1 `
  -InstallDependencies -DependencySet Dev
.\.venv\Scripts\mmt.exe doctor --strict-mastering
.\.venv\Scripts\mmt.exe capabilities
.\.venv\Scripts\python.exe .\scripts\generate_wav_fixtures.py
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier All
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Tests.ps1 -Tier All -Coverage
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-Quality.ps1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\Invoke-PortalSmoke.ps1
```

The 2026-09-03 Windows/Python 3.11 gate collected 228 tests: 227 passed and
one case-insensitive-filesystem test skipped. Ruff lint/format checks passed
across 60 files, mypy passed across 28 source files, and all 9 explicit audio
smoke tests passed. Isolated populated 1440 × 1000 and 1280 × 800 Chromium
reviews exercised the three-workspace flow, multi-version direct A/B selection,
swap/playhead continuity, responsive source and version controls, token
redaction, and orderly shutdown without modifying the operator catalog. The
most recent aggregate coverage run remains the 2026-07-30 snapshot: 87.78%
against the 85% gate. Both the offline/prepared-environment
`--no-isolation` wheel build and the standard isolated wheel build passed; the
wheel contains all three packaged GUI assets.

For one reference, copy
[`configs/upstream-baseline.json`](configs/upstream-baseline.json). For several,
copy [`configs/weighted-references.json`](configs/weighted-references.json).
Update the private input/output paths, then:

```powershell
.\.venv\Scripts\mmt.exe validate .\configs\my-job.json
.\.venv\Scripts\mmt.exe run .\configs\my-job.json --dry-run
.\.venv\Scripts\mmt.exe run .\configs\my-job.json
```

Relative audio/artifact paths are resolved from the job file. A dry run still
decodes, probes, fingerprints, and records inputs; it simply does not render
audio. Every CLI run registers its target and references in a private catalog,
writes a reimportable selection beside the manifest, and indexes the completed
manifest and artifacts. The default CLI catalog is
`CONFIG_DIR/.mmt/catalog/catalog.sqlite3`; use `--catalog` to choose another.

Useful catalog commands:

```powershell
$catalog = ".\private-workspace\catalog\catalog.sqlite3"
.\.venv\Scripts\mmt.exe catalog --database $catalog init
.\.venv\Scripts\mmt.exe catalog --database $catalog add `
  --role reference .\audio\reference.wav
.\.venv\Scripts\mmt.exe catalog --database $catalog list --include-archived
.\.venv\Scripts\mmt.exe catalog --database $catalog verify --include-archived
.\.venv\Scripts\mmt.exe catalog --database $catalog export `
  .\private-workspace\exports\catalog.json
.\.venv\Scripts\mmt.exe catalog --database $catalog import `
  .\private-workspace\exports\catalog.json
```

Run `mmt catalog --help` for track relocation, archive/restore, named set,
selection, run, and artifact commands.

## Python API

```python
from pathlib import Path

from music_mastering_tools import (
    MasteringService,
    load_job_config,
    validate_job,
)

job = load_job_config("configs/my-job.json")
report = validate_job(job)
if not report.ok:
    raise RuntimeError(report.to_dict())

outcome = MasteringService().run(
    job,
    manifest_path=Path("artifacts/runs/example/manifest.json"),
    event_log_path=Path("artifacts/runs/example/events.jsonl"),
)
print(outcome.manifest_path)
```

For a typed multi-reference job with programmatic catalog registration,
selection export, rendering, and final artifact indexing, see
[Control the workflow as a Python library](docs/user/workbench-and-catalog.md#control-the-workflow-as-a-python-library).

The schema expresses current reference-count/weight rules and also retains
planned advanced controls. Always run capability-aware validation; adapters
never silently downgrade a request.

## Repository map

- `src/music_mastering_tools/` — Python package, GUI portal/application
  layers, CLI, policies, and engine adapters
- `configs/` — runnable one- and multi-reference profiles plus an intentionally
  capability-rejected future-controls specimen
- `schemas/` — JSON job contract for editors and automation
- `tests/` — unit, integration, regression, and smoke cases
- `scripts/` — bootstrap, quality, test, audio/GUI/EXE smoke, Windows build,
  publication audit, and fixture
  workflows
- `packaging/` — desktop executable entrypoint, PyInstaller spec, and notices
  inventory tooling
- `.github/workflows/` — Windows quality, regression, build, and EXE verification;
  selected diagnostic artifacts, no binary uploads
- `docs/` — requirements, architecture, roadmap, edge policy, ADRs, and
  traceability
- `private-workspace/` — ignored mutable catalog, jobs, runs, default outputs,
  versioned portal preferences, exports, temporary files, and launcher logs;
  operator-selected delivery folders may live elsewhere
- `matchering-master/` and `matchering-master.zip` — locally preserved, ignored
  upstream evidence; published provenance records identify its exact revision
- `artifacts/` — ignored local logs, manifests, renders, test reports, and
  environment freezes

Start with the [workbench/catalog guide](docs/user/workbench-and-catalog.md) or
the [documentation index](docs/README.md). Developers should then read the
[architecture](docs/architecture/overview.md), [test
strategy](docs/quality/test-strategy.md), and [roadmap](docs/roadmap.md).

## Privacy and licensing

The owner authorized source commit and push to
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster), in addition to the
Windows executable work. The Windows workflow builds and verifies a candidate
and uploads selected diagnostics; the tested EXE bundle remains local while
its complete corresponding-source distribution is prepared. Do not
commit private audio, catalogs, manifests, paths, logs, secrets, or generated
renders. The release guide covers the source candidate and exact corresponding
source needed alongside a binary release. Actual publication evidence is recorded in
the implementation register.

The combined project is kept GPL-compatible because its compatibility engine
depends on GPL-covered Matchering. The standard GPLv3 text is preserved in
[LICENSE](LICENSE), and the operational policy is in
[docs/policies/licensing.md](docs/policies/licensing.md). This is engineering
documentation, not legal advice.
