# Architecture decision records

ADRs record durable decisions and their consequences. `Accepted` means the
project intends to follow the decision; it does not mean implementation is
complete. Delivery evidence remains in the
[implementation status register](../status/implementation-status.md).

| ADR | Status | Decision |
|---|---|---|
| [0001](0001-private-gpl-compatible-development.md) | Accepted; sharing restriction superseded by 0007 | Preserve GPL-compatible provenance; historical private-only preparation boundary |
| [0002](0002-characterization-first.md) | Accepted | Characterize upstream behavior before refactoring DSP |
| [0003](0003-analysis-plan-render.md) | Accepted | Separate analysis, planning, and rendering with versioned contracts |
| [0004](0004-per-job-structured-events.md) | Accepted | Use injected per-job structured events instead of global logging state |
| [0005](0005-weighted-reference-profiles.md) | Accepted | Combine multiple references as deterministic weighted loudness and Mid/Side spectral profiles |
| [0006](0006-private-content-addressed-catalog.md) | Accepted | Keep a local content-addressed track/artifact catalog alongside immutable run manifests |
| [0007](0007-windows-executable-and-user-workspace.md) | Accepted | Package a Windows x64 one-file browser workbench with persistent per-user state and active sharing preparation |

## ADR states

- `Proposed`: under review and not yet constraining work.
- `Accepted`: current decision.
- `Superseded`: replaced; links to the replacement.
- `Deprecated`: retained for history but no longer recommended.
- `Rejected`: considered and not adopted.

## Creating or changing a decision

Create an ADR when a choice changes a durable boundary, compatibility promise,
security/licensing posture, algorithm meaning, or cross-cutting development
process. Small implementation details belong in code and tests.

An ADR contains context, decision, consequences, alternatives, and verification.
Do not rewrite accepted history to hide an old choice. Add a superseding ADR
and update this index, requirements, architecture, tests, and status.
