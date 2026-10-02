# ADR-0007: Windows executable with a per-user browser workbench

- Status: Accepted
- Date: 2026-10-02
- Decision owners: project owner and maintainers
- Supersedes: ADR-0001's prohibition on release preparation; GPL provenance remains in force

## Context

The owner wants to finish the existing application, prepare it for their GitHub
account, and distribute a simple executable to other Windows users. Supported
systems are Windows 10/11 on Intel/AMD x64. Each Windows user has one independent
library. The existing Python application already supplies its interface through
a local browser and reads audio from the user's selected paths.

The developer checkout and its private workspace cannot be runtime dependencies
of a shared executable. Native pickers must begin at the selected folder or
existing file location, and remembered locations must survive restarts without
falling back silently to Documents.

## Decision

Package the current application as a console-free PyInstaller one-file
`MusicMasteringTools.exe`. Retain its local browser interface and loopback-only
security boundary. Bundle Python, the DSP dependencies, web assets, and notices.
Build on Windows x64 using the validated Python 3.11.7 and pinned release environment.

Default mutable state to `%LOCALAPPDATA%\MusicMasteringTools\workspace`.
`--workspace` selects another absolute workspace for testing or an explicit
operator workflow. Source tracks and selected delivery destinations remain
absolute paths chosen by that user; the executable folder and extraction
directory hold no mutable library state. A workspace lock prevents two running
copies from opening the same catalog concurrently.

Keep the source launcher and CLI for development. Do not add Electron or Tauri
to this delivery. Electron would add a second runtime and distribution surface.
Tauri would add a native host and WebView integration to an application that
already has tested browser/HTTP boundaries. Neither addresses the current path
bug by itself. A future browser-codec or native-window requirement can reopen
this decision with measured evidence.

Preparing builds, CI, documentation, and a publication-safe source tree is
authorized by the owner's 2026-10-02 request. The follow-up explicitly
authorizes source commit/push to `git@github.com:Nacsez/MusicMaster.git`.
Build/CI does not upload binary releases automatically. The release process retains the GPL
license, notices, exact dependency inventory, and corresponding-source work
described in the [release guide](../deployment/windows-release.md).

## Consequences

- Recipients run one EXE without installing Python or using administrator rights.
- Replacing or moving the EXE preserves the current Windows user's library.
- Different Windows accounts receive independent state by default.
- Moving to another computer requires moving audio and relinking its catalog
  locations; catalogs do not make source audio portable automatically.
- A one-file executable extracts bundled files into a temporary runtime folder
  during launch. Initial launch can take longer than later application actions.
- The installed browser determines audition codec support. The DSP path remains
  independent of browser playback support.
- Normal desktop launches exit after the last workbench tab closes and a short
  refresh grace period elapses. An active operation finishes first; refreshing
  or keeping another tab open retains the session. `--keep-running`,
  `--no-browser`, and the source GUI/CLI remain explicitly persistent.
- The manual shutdown action still refuses active mastering. Browser-managed
  desktop lifetime is recorded in the
  [lifecycle design](../design/browser-session-lifecycle-2026-10.md).
- The first package is unsigned; signing and installer/update infrastructure are
  separate release decisions, not dependencies of this local executable.

## Verification

The implementation register records actual results; this ADR is not acceptance
evidence. Required automated checks cover workspace resolution, frozen launch,
single-instance handling, picker initial paths, persisted delivery paths,
packaged assets/dependencies, and actual WAV rendering from the executable.

The executable smoke test must use an isolated workspace and a working directory
unrelated to the checkout. A clean Windows computer without Python is the
remaining independent deployment acceptance check. See the
[distribution design](../design/windows-distribution-2026-10.md).
