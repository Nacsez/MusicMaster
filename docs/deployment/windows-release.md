# Windows executable and GitHub preparation

Scope date: **2026-10-02**. The owner authorized Windows executable work and
committing/pushing the audited project source to
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster), using the SSH remote
`git@github.com:Nacsez/MusicMaster.git`. Build/CI does not upload a binary
release automatically. Actual push and verification results belong in the
[implementation register](../status/implementation-status.md).

For a recipient's first launch, use the
[Windows quick start](../user/windows-quick-start.md).

Supported target: **Windows 10/11, Intel/AMD x64**, one library per Windows user.
The current application remains a local browser workbench. Its EXE bundles
Python and the mastering runtime; recipients do not install Python or use
administrator rights.

## Run the executable

1. Copy `MusicMasteringTools.exe` to a convenient local folder and double-click
   it. The default browser opens the workbench.
2. Add your own tracks/references using the native picker. Source audio remains
   at the chosen absolute paths.
3. Choose a delivery folder in Master. **Remember** retains it for later
   launches; a per-job override applies only to that job. Each target gets a
   new run-named subfolder.
4. Close the last workbench tab to exit the desktop application. A short grace
   period protects refresh/reconnection; any active mastering operation
   finishes before exit. Another open workbench tab keeps the session alive.
   **Shut down portal** is still available for immediate idle shutdown.

The default state directory is:

```text
%LOCALAPPDATA%\MusicMasteringTools\workspace
```

It holds the current user's catalog, preferences, jobs, audit records, recovery
area, and diagnostic logs. Moving/replacing the EXE does not change this state.
Another Windows account gets its own workspace. The workspace lock prevents
two copies from competing for the same library.

If startup fails, inspect the retained workspace logs. Diagnostic startup and
an isolated test launch are available from PowerShell:

```powershell
.\MusicMasteringTools.exe --check-only --no-browser --diagnostics-output .\diagnostics.json
.\MusicMasteringTools.exe --workspace C:\MMT-Test\workspace --verbose
```

`--no-browser --write-ready PATH` starts the portal without opening a browser
and retains a private bootstrap document containing the launch URL. The normal
windowed EXE has no terminal console. `--no-browser` keeps the server running
until explicit shutdown; `--keep-running` also opts out of automatic last-tab
exit for a normal browser launch. Keep the bootstrap document private when
using manual access or an automated smoke harness.
Keep ready documents and logs private: they can contain local paths and session
access information. Logs in the normal portal redact session tokens.

The installed browser determines which audio codecs can be auditioned. The
package does not include an FFmpeg executable; formats requiring the upstream
FFmpeg fallback need a separately available decoder. WAV mastering is exercised
by the frozen executable smoke test. The first build is unsigned.

## Build and verify from source

Build on Windows x64 with the validated Python **3.11.7** interpreter. This is a Windows build, not a
cross-platform executable. From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check `
  -r requirements/windows-release.txt -r requirements/windows-build.txt `
  -e '.[dev,packaging]'
.\.venv\Scripts\python.exe scripts/generate_wav_fixtures.py
.\scripts\Invoke-Quality.ps1
.\scripts\Invoke-Tests.ps1 -Tier All -Coverage
.\scripts\Invoke-PopulatedPortalSmoke.ps1
.\scripts\Invoke-PublicationAudit.ps1
.\scripts\Build-Windows.ps1
.\scripts\Invoke-ExecutableSmoke.ps1 `
  -ExecutablePath dist\windows\MusicMasteringTools.exe
.\scripts\Invoke-BrowserLifecycleSmoke.ps1 `
  -ExecutablePath dist\windows\MusicMasteringTools.exe -IncludeCrashFallback
Compress-Archive -LiteralPath dist\windows `
  -DestinationPath dist\MusicMasteringTools-Windows-x64.zip -Force
```

`Build-Windows.ps1 -InstallBuildTools` installs the separately pinned packaging
tools when required. The spec is `packaging/MusicMasteringTools.spec`; the
entrypoint is `packaging/desktop_entry.py`. Runtime pins live in
`requirements/windows-release.txt` and build pins in
`requirements/windows-build.txt`.

| Output | Purpose |
|---|---|
| `dist/windows/MusicMasteringTools.exe` | Single application executable |
| `dist/MusicMasteringTools-Windows-x64.zip` | Local tested bundle with EXE, notices, inventories and checksums |
| `dist/windows/START-HERE.txt` | Recipient quick start shipped beside the EXE |
| `dist/windows/LICENSE` and `NOTICE` | Project license and upstream attribution |
| `dist/windows/THIRD-PARTY-NOTICES/` | Retained installed dependency/license files |
| `dist/windows/dependency-inventory.json` | Exact package/version and license inventory |
| `dist/windows/build-manifest.json` | Executable hash and exact authored build-input hashes |
| `dist/windows/SHA256SUMS.txt` | Candidate content hashes |
| `artifacts/windows-build/` | Timestamped build diagnostics |
| `artifacts/executable-smoke/` | Isolated executable acceptance diagnostics |
| `artifacts/browser-lifecycle-smoke/` | Actual browser close/refresh/multiple-tab and crash-fallback acceptance |
| `artifacts/release-audit/` | Read-only source publication audit reports |

The [Windows workflow](../../.github/workflows/windows.yml) repeats quality,
regression, populated browser checks, publication audit, build, executable
audio smoke, and browser-lifetime acceptance on `windows-2022` with Python
3.11.7 x64. It uploads selected diagnostic logs, JSON/XML summaries, and
generated-fixture screenshots. It retains no executable, audio, workspace, or
dependency archive as a public CI artifact while the complete corresponding
source distribution remains unfinished. The owner retains the tested bundle
locally. A successful remote CI run is only claimed once it has actually run.

Populated browser verification uses Node.js 22+ and installed Chrome or Edge
only during development/testing. Recipients need neither Node nor Python.
The browser-lifetime harness tests actual tab/window close, refresh, multiple
tabs, minimizing, canceled navigation, history-restored visits, and library
persistence. `-IncludeCrashFallback` also waits for the real crashed-browser
lease expiry. `tests/test_browser_lifetime_operation.py` separately uses the
real HTTP server and operation manager with a blocked worker to verify durable
completion before automatic exit. Frozen audio smoke exercises real mastering
DSP. See the
[lifecycle design](../design/browser-session-lifecycle-2026-10.md) for timing
and persistent-session options.

`packaging/third_party/provenance.json` records the exact CPython 3.11.7 native
library baseline and official license/source links. Missing OpenSSL, Expat,
zlib, and XZ/liblzma notice texts are retained under `packaging/third_party/`;
bzip2/libffi notices come from the interpreter's copied `LICENSE.txt`, and
SQLite's public-domain source is recorded. The collector rejects a different
interpreter/native version or modified upstream notice asset. A Python update
requires refreshing this evidence and rebuilding/smoking the executable.

## Continue an existing checkout library

An empty initial EXE library does not mean the source-workbench tracks were
deleted. The earlier catalog, reference sets, job history, and preferences
remain in the checkout's ignored `private-workspace/`; the EXE defaults to a
separate persistent library under the current user's Local AppData. Source
audio remains at its original paths.

To use that existing catalog, shut down its current portal, then launch from
the repository root:

```powershell
.\dist\windows\MusicMasteringTools.exe --workspace (Resolve-Path .\private-workspace).Path
```

This uses the existing catalog, paths, and preferences in place. The normal
double-click launch still uses the persistent per-user default. To make an old
library available from another folder, retain the explicit `--workspace` path
in a shortcut. To move a full workspace, shut down first, back it up together
with required audio/output folders, and verify/relink catalog locations after
the move. Use Library's relink action when an original path no longer exists;
the catalog references audio rather than storing its bytes.

Do not copy the owner's workspace into the EXE or GitHub repository. Recipients
start with their own library and select their own media.

## GitHub source candidate

Publishable source consists of:

- `src/`, `tests/`, `scripts/`, `packaging/`, `requirements/`, `schemas/`, and
  safe example `configs/`;
- `docs/`, `.github/`, `README.md`, `pyproject.toml`, the source launcher,
  `.editorconfig`, `.gitattributes`, `.gitignore`, `LICENSE`, and `NOTICE`.

`.gitignore` excludes mutable workspaces, generated audio, logs, build output,
credentials, assistant/cloud settings, and the duplicated upstream archive and
extraction. The originals are retained locally. Their exact identities are in
[`upstream-provenance.json`](../baseline/upstream-provenance.json), and
[`NOTICE`](../../NOTICE) preserves the inherited copyright and modification
record.

`Invoke-PublicationAudit.ps1` checks the candidate using an explicit source
allowlist, prohibited media/database/binary extensions, private runtime paths,
credential patterns, and personal Windows user paths. `-TrackedOnly` checks
the committed candidate; the default also checks nonignored untracked files.
The audit does not stage, commit, remove, or upload anything. Pattern matching
is a practical check, not proof that arbitrary sensitive content is absent.

The 2026-10-02 initial audit found no tracked source files or existing commits
and no tracked private audio/catalog history to remove. Source content was
untracked. The owner now explicitly authorized source commit/push to the SSH
remote above. Repeat the audit for the staged candidate and inspect its diff
before committing. Retain the commit ID and push/remote result in the
implementation register. Never add a personal catalog, run manifest,
ready document, session log, or source song to a support issue.

## License and exact corresponding source

The combined application is GPL-3.0-or-later. Preserve `LICENSE`, `NOTICE`, the
dependency notices, and build/install material. Binary distribution requires
the exact corresponding source for the conveyed build, including required
dependency source; an application-only GitHub archive or original upstream
source alone is insufficient. The definition and permitted distribution
methods are in GPLv3 sections 1 and 6 of the retained
[`LICENSE`](../../LICENSE).

For a download release, prepare these together:

1. The tested EXE and notices/checksums from the chosen build.
2. An archive of the exact application source revision, including packaging,
   requirements, schemas, and build instructions.
3. The corresponding source for bundled required dependencies and native
   libraries, identified by the dependency inventory and their upstream
   source/version records. Preserve each component's license and notices.
4. A source-to-binary record giving the source revision, environment inventory,
   build commands, and checksums. Put an explicit corresponding-source download
   link next to the binary download and test a rebuild from that source.

The build's inventory/notices collection does not itself collect every
dependency source archive. That collection remains separate work before a
public binary release; a source push alone does not collect these archives.
PyInstaller's bootloader exception
allows bundling without changing the application's license; dependencies still
retain their own terms. See its
[official license documentation](https://www.pyinstaller.org/en/stable/license.html).

## Independent Windows acceptance

Native Explorer acceptance is available as an opt-in smoke on an interactive
Windows desktop:

```powershell
.\scripts\Invoke-FolderNavigationSmoke.ps1
```

The harness creates a unique folder/file containing Unicode, spaces, and a
comma; reads the actual Explorer folder and selected item through Windows Shell
COM; verifies file reveal and directory opening; and closes only its own new
test windows. It retains `result.json` and diagnostics under
`artifacts/folder-navigation-smoke/<run-id>/`. It is not a headless CI check.
The 2026-10-02 real desktop pass is retained at
`artifacts/folder-navigation-smoke/20261002T221750240134Z-0dd5ff3c/result.json`.

This exercise exposed first-use Explorer readiness: a freshly opened directory
could initially drop the file selection. The backend now waits up to ten
seconds for the exact directory view, then repeats native selection. The
accepted smoke checks the actual selected path after that readiness step.

Before describing the build as verified on other computers, run it on a clean
Windows 10/11 x64 machine without Python or developer tools:

1. Launch as a standard user from a folder unrelated to this checkout.
2. Add WAV input/reference files with spaces and Unicode in their paths.
3. Choose an external delivery folder; reopen its picker and confirm the
   initial location. Render, audition, and open the exact output in Explorer.
4. Shut down, restart, and verify catalog and remembered delivery destination.
5. Move the EXE to another folder and repeat; the same user's catalog persists.
6. Start a second copy against the same workspace and check the one-instance
   behavior, then confirm the original can still render and shut down.
7. Sign in as another Windows user; its initial library is independent.
8. Move a cataloged source and use relink/verification to restore its location.
9. Retain diagnostic logs and record OS/browser/build identity and result in
   the [implementation register](../status/implementation-status.md).

Workspace backups should be made after shutting down the portal, together with
any audio/delivery folders needed for recovery. Copying only the EXE carries no
library or songs. On another computer, catalog paths must be verified/relinked
to the destination user's actual files.
