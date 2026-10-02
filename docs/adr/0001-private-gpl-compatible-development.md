# ADR-0001: Private, GPL-compatible development

- Status: Accepted; sharing-preparation restriction superseded by ADR-0007
- Date: 2026-07-27
- Decision owners: project owner and maintainers
- Requirements: GOV-001, GOV-002, SEC-001

On 2026-10-02 the owner authorized Windows executable and GitHub preparation,
then explicitly authorized source commit/push to
[Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster).
[ADR-0007](0007-windows-executable-and-user-workspace.md) replaces the historic
prohibition on packaging/sharing preparation and source publication below.
License, attribution, and
provenance duties remain in force. The original decision is retained as history.

## Context

The workspace begins from Matchering 2.0.6. Its metadata declares GPLv3, and
its source headers grant GPL version 3 or later. The owner wants to complete a
working private version before considering public release. At the same time,
development choices made now can make later GPL-compliant open-source release
easy, difficult, or impossible.

Private use does not require pretending the upstream license is permissive or
removing provenance. Distribution can occur through more routes than a public
Git repository, including binaries, containers, or copies sent to another
party.

## Decision

We will:

1. keep development and use within the authorized private group;
2. prohibit publication or conveyance until an explicit public-release gate is
   opened and approved;
3. preserve upstream license text, notices, authorship, source/archive
   identity, and modification history;
4. treat modifications and tightly integrated new work as needing a
   GPL-compatible combined-work path;
5. evaluate dependency, asset, media, and tool licenses before adoption;
6. keep source, build/test material, schemas, and dependency records suitable
   for future corresponding-source assembly; and
7. use the standard applicable GNU GPL text rather than a custom "GPL-like"
   license if public release is later approved.

The detailed operational rules are in
[the licensing policy](../policies/licensing.md).

## Consequences

### Positive

- The owner retains the choice not to release the private modifications.
- Provenance and build material will not need to be reconstructed at the end.
- Incompatible dependencies and assets are found early.
- A future release can be evaluated from an explicit source/binary inventory.

### Costs and constraints

- License review is part of dependency and asset changes.
- Sending a copy outside the group cannot be treated casually.
- Some proprietary or source-available components will be unavailable for the
  integrated application.
- Public release still needs legal, security, branding, packaging, and
  corresponding-source review; this ADR does not pre-approve it.

## Alternatives considered

### Publish immediately

Rejected. The owner explicitly wants a complete private working version first,
and the system is not yet hardened.

### Ignore licensing until release

Rejected. Missing provenance and incompatible dependencies are expensive and
sometimes impossible to repair retroactively.

### Reimplement from scratch under an unrelated proprietary license

Rejected for current scope. It would discard the useful baseline, would require
a careful clean-room process to make the licensing claim credible, and does
not advance the requested GPL-compatible path.

### Use AGPL by default

Not selected. The inherited baseline is GPL rather than AGPL. A license change
for wholly new separable work would require ownership and compatibility
analysis and is not necessary for the private milestone.

## Verification

- Upstream license and notices remain present.
- A provenance record identifies version/source hashes.
- Dependency/license inventories are reviewed at milestones.
- Packaging/upload workflows remain absent or hard-disabled during the private
  phase.
- The status register never labels a public release active without an owner
  decision and completed checklist.

## Revisit triggers

- sharing code or binaries outside the authorized private group;
- adding a dependency or asset with non-obvious terms;
- introducing a hosted service for people outside the group;
- accepting external contributors;
- changing the package/component boundary; or
- opening the public-release phase.
