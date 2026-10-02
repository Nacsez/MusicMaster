# ADR-0006: Use a private content-addressed track and artifact catalog

- Status: Accepted
- Date: 2026-07-27
- Decision owners: application, operations, and security maintainers
- Requirements: FLOW-002, FLOW-004, FLOW-008, OBS-001, OBS-003, SEC-001 through
  SEC-003, GOV-002

## Context

Paths alone are not durable track identities. A song can move, the same bytes
can exist at several paths, and a file can be replaced in place. Operators need
to select prior targets and references, retain named weighted reference sets,
re-import lists, and inspect which inputs and outputs participated in each run.
The catalog must remain private, local, dependency-light, and recoverable.

Run manifests remain immutable per-run evidence. The catalog is a mutable index
across runs and must not replace those manifests.

## Decision

The private workbench uses a local SQLite catalog. SQLite is part of Python's
standard library and provides transactions, schema migrations, foreign keys,
indexes, and safe concurrent readers without a service dependency.

A track identity is derived from its full SHA-256 content digest. A separate
location identity is derived from its normalized canonical path. Therefore:

- identical bytes at several paths produce one track with several locations;
- moving a file can add or relink a location without changing track identity;
- changed bytes at a known path never silently mutate the prior identity;
- missing locations remain in history; and
- generated outputs can become cataloged tracks without losing their producing
  run.

The catalog stores audio facts, roles, labels/tags, archive state, locations,
named weighted reference sets, run records, and run-artifact records. It does
not copy source audio by default and performs no network metadata lookup.

Every catalog-backed job emits a versioned JSON selection snapshot containing
the target, all requested references, content identities, paths, independent
weights, and a deterministic selection identity. Selection and catalog exports
use strict JSON and can be imported again. Portable exports may make paths
relative to an explicit root; private absolute-path exports are clearly
identified.

The run manifest is authoritative for one execution. It inventories target,
every reference, configuration/selection inputs, outputs, previews, event logs,
and retained partial artifacts. It records the catalog identity, revision,
selection identity, and bindings. After final manifest commit, the mutable
catalog indexes the manifest and its artifact inventory. A manifest cannot
contain its own final hash without recursion; that hash belongs in the catalog
or a companion receipt.

Catalog data lives under an ignored private-workspace or `.mmt` directory.
Archive/restore is the normal management operation; deleting audio is never a
catalog command.

## Consequences

### Positive

- Prior targets, references, and weighted sets can be reused without editing
  JSON by hand.
- Every run has both immutable evidence and a searchable cross-run index.
- Duplicate files and moved paths have explicit behavior.
- A future desktop or browser UI can reuse the same catalog/application API.
- Catalog operation remains offline and dependency-free.

### Costs and constraints

- Database schema migration, backup, locking, and corruption recovery need
  tests and operator documentation.
- Absolute paths, labels, and full hashes are sensitive private data.
- A content hash proves byte identity, not ownership, quality, or permission to
  redistribute a recording.
- Mutable catalog updates and immutable manifest commits require an explicit
  recovery path when only one succeeds.
- Cataloging a large library requires streaming hashes and may take time.

## Alternatives considered

### One JSON file as the live database

Rejected for the primary mutable catalog. Whole-file rewrites, concurrent
management, indexes, and migrations become fragile as the library grows. JSON
remains the interchange and selection format.

### Store only paths

Rejected. Replacement-in-place, aliases, hardlinks, moves, and duplicate
content cannot be handled truthfully.

### Copy all audio into a managed library

Rejected as the default. It multiplies private storage, raises rights and
retention concerns, and is unnecessary for local indexing.

### Use an external media database or cloud service

Rejected for the private baseline. It adds deployment, privacy, availability,
and network dependencies.

## Verification

- Schema creation and migration are transactional; foreign keys and bounded
  busy timeout are enabled.
- Same-content paths coalesce; changed, missing, hardlink, relink, archive, and
  restore cases have regression tests.
- Reference sets preserve member identity, order, and independent finite
  weights.
- Strict export/import round-trips and tamper/version failures are tested.
- A catalog-backed smoke run links its selection, manifest, all inputs, and all
  outputs.
- Private catalog/workspace files are ignored by source control.
- No catalog operation deletes an audio file or contacts a network service.

## Implementation note

The local SQLite catalog, CLI command tree, strict full-catalog and selection
exchange, manifest integration, named weighted sets, GUI-first loopback portal,
terminal fallback, and root Windows launcher implement the initial private
slice of this decision. Catalog/selection v1 currently retains resolved paths
and requires explicit verification/relink after files move. Automated
retention sweeps, portable root remapping, and full interactive-browser
operator acceptance beyond the passing headless bootstrap/shutdown smoke
remain future work.
