# Mastering Workbench documentation

This directory is the control center for evolving the Matchering 2.0.6
baseline, the runnable weighted native slice, and the private catalog/workbench
into a dependable mastering system. Windows executable and GitHub sharing
preparation is active under the owner's 2026-10-02 request.

The documentation deliberately separates three kinds of statement:

- **Baseline fact** — behavior observed in the extracted upstream 2.0.6
  source or the surveyed official web application.
- **Shipped behavior or scaffold** — implementation present in this workspace;
  its exact acceptance/evidence level is stated in the status register.
- **Planned requirement** — behavior the private implementation must deliver
  and verify before it is called complete.

An accepted design decision is not proof that its code has been implemented.
The [implementation status register](status/implementation-status.md) is the
authority for current delivery state.

## Start here

| Need | Document |
|---|---|
| Use the Windows executable for the first time | [Windows quick start](user/windows-quick-start.md) |
| Run/build a Windows EXE, prepare GitHub source, and check deployment evidence | [Windows release guide](deployment/windows-release.md) |
| Understand desktop packaging, per-user paths, and deployment verification | [Windows distribution design](design/windows-distribution-2026-10.md) |
| Understand automatic exit, refresh/multiple tabs, and active-render safety | [Browser-managed desktop lifetime](design/browser-session-lifecycle-2026-10.md) |
| Understand the CADER palette and visual changes | [CADER reskin — October 2026](design/cader-reskin-2026-10.md) |
| Launch the workbench, manage original/master versions, compare A/B, batch-export or recover a take, use one or several references, and inspect artifacts | [Private workbench and catalog guide](user/workbench-and-catalog.md) |
| Control jobs, weighted references, catalog selections, and manifests through Python | [Python library example](user/workbench-and-catalog.md#control-the-workflow-as-a-python-library) |
| Understand the intended product and boundaries | [Vision and scope](product/vision-and-scope.md) |
| See testable product requirements | [Requirements](product/requirements.md) |
| Understand the target system shape | [Architecture](architecture/overview.md) |
| Understand the implemented workflow-led frontend redesign and remaining operator checks | [UI/UX redesign — September 2026](design/ui-ux-redesign-2026-09.md) |
| See the intended delivery order | [Phased roadmap](roadmap.md) |
| Understand what upstream actually supplies | [Upstream 2.0.6 baseline](baseline/upstream-2.0.6.md) |
| See what exists today | [Implementation status and traceability](status/implementation-status.md) |

## Engineering handbook

- [Edge-case policy](policies/edge-cases.md)
- [Test strategy](quality/test-strategy.md)
- [Observability and logging](operations/observability.md)
- [Security and privacy](policies/security-and-privacy.md)
- [GPL provenance and distribution policy](policies/licensing.md)
- [Windows executable and GitHub preparation](deployment/windows-release.md)
- [Development workflow](development/workflow.md)
- [UI/UX redesign — September 2026](design/ui-ux-redesign-2026-09.md)
- [GUI workflow pass record (2026-07-27; source/version follow-up 2026-07-30)](development/gui-workflow-pass-2026-07-27.md)
- [Configuration examples](../configs/README.md)
- [Job schema notes](../schemas/README.md)
- [Codebase and process survey record](research/process-survey-2026-07.md)

## Architecture decisions

The [ADR index](adr/README.md) records decisions that are durable enough to
constrain implementation:

1. [Private, GPL-compatible development](adr/0001-private-gpl-compatible-development.md)
2. [Characterization before DSP refactoring](adr/0002-characterization-first.md)
3. [Analyze → plan → render separation](adr/0003-analysis-plan-render.md)
4. [Per-job structured events](adr/0004-per-job-structured-events.md)
5. [Weighted reference profiles](adr/0005-weighted-reference-profiles.md)
6. [Private content-addressed catalog](adr/0006-private-content-addressed-catalog.md)
7. [Windows executable and per-user workspace](adr/0007-windows-executable-and-user-workspace.md)

## Status vocabulary

| Status | Meaning |
|---|---|
| `UPSTREAM` | Present in the vendored/extracted upstream baseline only; it is not yet accepted as hardened private behavior. |
| `SCAFFOLDED` | A contract, file layout, or test/tooling hook exists; functional completion is not implied. |
| `IMPLEMENTED` | The private implementation performs the behavior, but all required evidence may not yet exist. |
| `VERIFIED` | The implementation passes the documented acceptance and regression evidence. |
| `PLANNED` | Approved scope with no meaningful implementation yet. |
| `DEFERRED` | Intentionally outside the current private-version completion target. |
| `BLOCKED` | Progress requires an identified decision or external condition. |

Only `VERIFIED` means a requirement is complete. Status must cite a file, test,
artifact, or dated observation. See the
[development workflow](development/workflow.md#definition-of-done).

## Documentation maintenance

Every behavior-changing change must update, in the same change set:

1. the applicable requirement or acceptance criterion;
2. architecture or ADRs when system boundaries change;
3. tests and evidence;
4. the status/traceability register; and
5. user or operator instructions affected by the change.

Documentation is treated as a first-class build artifact. Broken relative
links, stale status claims, and requirements without test ownership are
defects.
