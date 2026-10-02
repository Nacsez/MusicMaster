# Windows distribution design — October 2026

Date: **2026-10-02**

Scope: Windows 10/11 x64, one library per Windows account, one-file executable,
existing browser workbench, reliable selected folder locations, and the CADER
visual direction. This is a delivery slice, not completion of every longer-term
DSP requirement in the roadmap.

## Runtime ownership

| Material | Owner and location |
|---|---|
| Executable and bundled read-only assets | Release EXE; its location may change |
| Catalog, preferences, jobs, logs, recovery records | Current user's `%LOCALAPPDATA%\MusicMasteringTools\workspace` |
| Original audio | User-selected absolute path; referenced in place |
| Delivery folder | Remembered default or per-job absolute override |
| Rendered audio | Exclusive run-named child of the chosen delivery folder |
| Extracted bundled libraries | PyInstaller's temporary runtime directory |
| Developer/test evidence | Ignored `artifacts/` in the source checkout |

The desktop entrypoint resolves state before opening the portal and retains
startup diagnostics there. A workspace lock makes concurrent access explicit.
Moving the EXE or changing its launch working directory must not change catalog
or output destinations. Paths containing spaces and Unicode are ordinary data.

Normal desktop launches exit when the last workbench tab leaves, after a short
refresh grace period and completion of any active operation. Refresh, another
open tab, and hidden/minimized browser windows retain the session. The catalog
and preferences persist after process exit. Developer/persistent modes opt out;
see the [browser lifetime design](browser-session-lifecycle-2026-10.md).

## Folder-location behavior

Pickers begin at the current job output/relink location when supplied, then
the last successful location for that picker purpose, then a workspace default.
Directory history persists in versioned `dialog-locations.json`, separate from
delivery preferences. Defaults are workspace root for audio, preferred output
for folders, exports for catalogs/selections, jobs for configurations, and runs
for manifests. Cancelling preserves history; deleted/unavailable/malformed
locations use valid fallbacks with diagnostic detail.

Explorer file selection uses the Unicode Windows Shell API with the exact
catalog/artifact path; directories open their contents, and missing files fail
explicitly. This avoids the former argument parsing that dropped file reveals
into Documents when paths contained spaces.

When Explorer creates a fresh directory view, the backend waits up to ten
seconds for that exact view and repeats selection so first-use file highlighting
does not depend on shell initialization timing. The opt-in
`scripts/Invoke-FolderNavigationSmoke.ps1` verifies the real folder and selected
file on an interactive desktop and retains evidence in
`artifacts/folder-navigation-smoke/`.

Remembered state belongs to the user's persistent workspace, not the checkout,
EXE directory, or current working directory. The delivery default and job
override remain distinct, and each target receives a new exclusive child.

## Package and source boundary

The Windows build is controlled by `packaging/MusicMasteringTools.spec` and
`scripts/Build-Windows.ps1`. Runtime versions are pinned in
`requirements/windows-release.txt`; packaging tools are pinned in
`requirements/windows-build.txt`. The validated build interpreter is CPython
3.11.7 x64; native support notices/source links are pinned in
`packaging/third_party/provenance.json`. The spec includes the browser assets and
licenses alongside required Python and native libraries.

`dist/windows/` contains the EXE, `LICENSE`, `NOTICE`, dependency notices,
`dependency-inventory.json`, and `SHA256SUMS.txt`. Build and smoke logs belong in
ignored `artifacts/`. Neither directory enters the Git source history.

`build-manifest.json` records the executable hash and authored code, assets,
schemas, config, requirements, and build-script hashes. This identifies the
candidate even before an initial source commit; a Git revision alone does not
establish that a working tree was clean.

The GitHub source candidate consists of authored source, tests, deterministic
fixture generator, safe example configs, schemas, documentation, requirements,
packaging material, and workflows. Private audio, catalogs, manifests, logs,
exports, credentials, assistant settings, and duplicated upstream extraction
remain local. Recorded upstream hashes and links preserve provenance.

## Verification plan and evidence limits

| Gate | Purpose |
|---|---|
| Static checks and full regression suite | Existing mastering/catalog/security behavior remains covered |
| Path and desktop regression tests | Workspace, concurrency, and picker destinations are deterministic |
| Browser smoke at desktop and laptop widths | Restyled interface initializes and retains accessible controls |
| Frozen executable smoke | Bundled DSP imports, WAV validation/render/reopen, HTTP/assets, shutdown work outside checkout |
| Browser lifetime smoke | Actual tab close, refresh, multiple/hidden tabs, active-render completion and crashed-browser expiry |
| Publication audit | Source candidate excludes common private artifacts and credential patterns |
| Clean Windows 10/11 x64 operator pass | No Python/admin prerequisite; real dialogs, browser/audio, persistence, relink work |

Local smoke proves the packaged runtime works on the build workstation. It does
not prove every supported Windows/browser combination or a separate clean
machine. The [implementation register](../status/implementation-status.md) and
[release guide](../deployment/windows-release.md) distinguish passed evidence
from remaining operator checks.

## Maintenance process

1. Change runtime paths or package contents with focused regression coverage.
2. Run full quality/tests, then rebuild and exercise the actual EXE.
3. Retain timestamped logs, inventories, and checksums for the candidate.
4. Update user instructions and the implementation register with exact results.
5. Audit the Git candidate and prepare exact corresponding sources before
   uploading a binary release. Source commit/push to
   [Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster) is now explicitly
   authorized. CI uploads selected diagnostics and retains no binary candidate
   while the complete corresponding-source distribution remains unfinished.

The UI reskin follows the CADER workspace evidence reviewed for this task. Its
implemented palette/layout details and actual browser acceptance evidence are
recorded with the frontend change rather than inferred from this packaging ADR.
