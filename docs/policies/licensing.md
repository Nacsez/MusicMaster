# GPL provenance and distribution policy

Status: **active Windows/GitHub preparation; updated 2026-10-02**

The combined application uses GPL-3.0-or-later. Matchering 2.0.6 declares GPLv3
in package metadata; its inherited headers grant GNU GPL version 3 or later.
Preserve [LICENSE](../../LICENSE), attribution/modifications in
[NOTICE](../../NOTICE), and the exact
[upstream provenance record](../baseline/upstream-provenance.json).

## Current scope

The owner's 2026-10-02 request authorizes Windows executable work; the follow-up
explicitly authorizes source commit/push to
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster).
[ADR-0007](../adr/0007-windows-executable-and-user-workspace.md)
supersedes ADR-0001's previous prohibition on packaging/release preparation.
The work includes reviewable builds, audits, CI, documentation, and the
authorized source publication. It does not publish a binary release
automatically. Follow the
[release guide](../deployment/windows-release.md) for the actual candidate
process and remaining source/publication work.

## Provenance and contributions

Retain inherited copyright, license, and warranty notices. Identify derived
material and its changes. The weighted pipeline derives from Matchering's
processing stages; NOTICE records its upstream authorship and July 2026 changes.
Preserve preferred source, schemas, tests, and build/install scripts needed to
modify and rebuild the application. Record externally sourced patches/assets,
their permissions, and compatible terms for integrated contributions.

The original ZIP/extraction remains intact locally. Its archive comment
identifies revision `914c9e58939746db1669212645907e7316389d08`; all 80 extracted
files match their archived bytes. Publication uses recorded hashes and pinned
source links instead of duplicating the extraction in the authored source tree.

## Dependency and asset inventory

Record exact versions, sources, license grants, retained license text, use,
and required notice/source actions for bundled Python/native components.
Distinguish development-only tools from bundled runtime components.

The Windows build produces `dependency-inventory.json` and
`THIRD-PARTY-NOTICES/` from installed distributions. These can include native
BLAS, compiler-runtime, and audio-codec notices. Review actual bundled contents;
a top-level Python package license does not necessarily describe every DLL.

PyInstaller's bootloader exception permits bundling without forcing a new
license onto the application; dependencies retain their terms. See
[its official license documentation](https://www.pyinstaller.org/en/stable/license.html).
Resolve actual license incompatibilities before bundling new material.

UI resources ship as repository-owned HTML/CSS/JavaScript with no remote
fonts/CDN. Audio fixtures are generated deterministically. Operator music,
reference tracks, artwork, private renders, and local catalogs are not assets
of the release candidate.

## Corresponding source and binary releases

Source releases retain license, notices, modification records, and build
material. GPLv3 section 1 defines corresponding source; section 6 governs
object-code conveyance. Read the retained [LICENSE](../../LICENSE).

For a download release, provide an exact corresponding-source download beside
the binary. Include the application revision, necessary required dependency
and native-library source, build/install scripts, dependency identities, and
interfaces needed to modify/rebuild that executable. Original upstream alone
or a patch without the complete applicable source is insufficient.

The inventory/notices collector does not collect every dependency source
archive. Assemble those exact sources, retain each component's terms/notices,
and record a rebuild from that bundle before publishing a binary candidate.
The release guide records this remaining work.

## Audio and local desktop use

Audio output does not automatically acquire the program's GPL merely because
the program produced it. GPLv3 section 2 applies when the output's content
itself constitutes a covered work. Operators retain responsibility for their
input/reference/output media rights.

This milestone distributes a local desktop application. It does not authorize
a hosted service, audio uploads, multi-user server, tunnel, or proxy exposure.
Recipients have their own workspace; the executable carries no owner's library.

## Candidate record

Retain the application revision, build commands, upstream provenance,
modification notices, dependency/native license/source inventory, source and
binary checksums/correspondence, verification evidence, asset inventory, actual
unresolved license questions, and scope of the owner's publication instruction.

Do not remove upstream notices, mislabel GPL as permissive, offer only original
upstream source for modified binaries, or bundle private songs/logs/manifests/keys.
