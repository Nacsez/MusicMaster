# ADR-0004: Per-job structured events

- Status: Accepted
- Date: 2026-07-27
- Decision owners: application and operations maintainers
- Requirements: PIPE-004, OBS-001 through OBS-003, SEC-003

## Context

Matchering 2.0.6 exposes process-global mutable callbacks for info, warning,
and debug messages. Information and warnings can include numeric codes, but
payloads are strings. Concurrent jobs can overwrite handlers or mix output,
and operators must reconstruct a cause chain from console text.

The private project prioritizes verbose diagnostics that can be enabled when
needed, automated artifact-based diagnosis, privacy, and trustworthy batch/job
status.

## Decision

Core and application operations receive an injected, job-scoped event
emitter/sink. Events use a versioned structured envelope with:

- job and optional parent/batch identity;
- monotonic per-job sequence and timing;
- stable stage, severity, code, and machine name;
- safe human message and typed attributes;
- optional legacy Matchering code; and
- cause/retry semantics for failures.

Stage lifecycle and terminal job events follow a defined state machine.
Console and JSONL logging are adapters. The terminal manifest summarizes
authoritative events and state. No global mutable handler controls job logging.

The canonical envelope and redaction rules are in
[observability and logging](../operations/observability.md).

## Consequences

### Positive

- Concurrent job diagnostics remain isolated.
- CLI, batch, and UI progress share stable semantics.
- Failures can be triaged automatically from artifacts.
- Operators can enable debug/trace without changing DSP code.
- Legacy 2xxx–4xxx codes can remain discoverable during migration.
- Privacy rules can be tested on structured fields.

### Costs and constraints

- Event schemas and code catalogs require compatibility discipline.
- Sink backpressure/failure semantics must be explicit.
- High-volume trace events require rate and retention limits.
- Library exception text must be sanitized before normal event storage.
- A manifest and an event stream must be kept consistent.

## Alternatives considered

### Retain global callbacks and add locks

Rejected. Locks serialize mutation but do not provide structural data,
per-job configuration, or durable causality.

### Use Python logging records directly as the domain contract

Rejected. Python logging is a useful adapter but does not define manifest,
job-state, stable-code, or cross-process schema semantics.

### Write only a final report

Rejected. A crash or cancellation may occur before report generation, and
stage progress is needed for operations.

### Store every numerical detail as events

Rejected. It creates volume/privacy problems. Large arrays and dense diagnostic
data use bounded, content-addressed artifacts referenced by summary events.

## Verification

- Two concurrent jobs with different sinks/configs exhibit no cross-talk.
- Event sequences and stage terminal invariants are tested.
- Event-sink failure follows required/optional policy without altering audio.
- Debug on/off yields numerically identical output.
- Redaction tests cover paths, labels, secrets, and exception text.
- Event and manifest terminal state/codes agree.
- Legacy code mappings are explicit and never repurposed.

## Revisit triggers

- events cross a public multi-tenant or distributed boundary;
- a durable queue imposes delivery/ordering semantics not covered here;
- trace volume makes the current envelope operationally unsuitable; or
- regulatory/audit requirements change required retention or integrity.
