# Observability and logging

Status: **accepted design; initial per-job events/manifests are implemented**

The workbench must explain both its signal-processing decisions and its failure
chain. ADR-0004 replaces upstream process-global string callbacks with
per-job structured events while preserving useful legacy code mappings.

## Observability goals

- A job can be diagnosed from its manifest and event stream.
- Concurrent jobs never mix events or configuration.
- Stage progress is stable enough for CLI, batch, and UI adapters.
- Debug detail is available when enabled without making normal logs unusable.
- Metrics and logs use precise audio terms and units.
- Sensitive paths, user labels, and audio content are private by default.
- Event emission cannot silently change DSP results.

## Evidence products

| Product | Purpose | Durability |
|---|---|---|
| Event stream | Ordered lifecycle, progress, decisions, warnings, and failures | Configurable; terminal/warning/error summary copied to manifest |
| Job manifest | Authoritative provenance, configuration, plan, measurements, artifacts, and terminal state | Durable according to job retention |
| Catalog index | Mutable cross-run lookup of tracks/locations, selections, manifests, and every fingerprinted input/output/audit artifact | Local SQLite plus explicit versioned JSON export |
| Portal operation projection | Current/recent operation state, stage, bounded event view, result, and failure detail for the GUI | In memory for one portal process; not authoritative after restart |
| Portal session log | Token-redacted loopback requests, request IDs, worker/thread context, startup/shutdown, and adapter failures; viewable in the GUI Session Logs panel | Private timestamped file under `private-workspace/logs/` |
| Diagnostic report | Human-readable projection of manifest/events with cause and remediation | Generated on demand or at failure |

Console text is an adapter rendering of events, not the source of truth. The
catalog is a convenience/recovery index; the committed manifest remains
authoritative for one run.

For weighted jobs, the manifest inventories every requested reference
occurrence even when equal content is coalesced for analysis. It records raw
and independently normalized weights, effective content-group identity and
membership, dimension-specific effectiveness, algorithm/profile version,
blend rule/floor, filter/profile/plan hashes, and output identity. Catalog
artifact JSON retains the exact manifest role in `metadata.manifest_role`.

## Canonical event envelope

Every event contains:

| Field | Required | Meaning |
|---|---:|---|
| `schema_version` | yes | Event contract version |
| `event_id` | yes | Unique opaque event identity |
| `sequence` | yes | Monotonic per-job ordering number |
| `timestamp` | yes | UTC RFC 3339 wall-clock time |
| `monotonic_offset_ms` | yes | Duration-safe offset since job acceptance |
| `job_id` | yes | Correlation identity |
| `parent_job_id` | no | Batch correlation |
| `stage` | yes | Stable stage name |
| `severity` | yes | `trace`, `debug`, `info`, `warning`, or `error` |
| `code` | yes | Stable project event/error code |
| `name` | yes | Stable machine-readable event name |
| `message` | yes | Concise safe human text |
| `attributes` | yes | Typed safe context with units in field names/schema |
| `legacy_code` | no | Matchering 2xxx/3xxx/4xxx mapping when equivalent |
| `cause_code` | no | Stable wrapped cause |
| `retryable` | no | Whether unchanged request may reasonably be retried |

Example, illustrative until a normative schema file ships:

```json
{
  "schema_version": "1.0",
  "event_id": "01J...",
  "sequence": 17,
  "timestamp": "2026-07-27T15:04:05.123Z",
  "monotonic_offset_ms": 842,
  "job_id": "job_01J...",
  "stage": "analysis.target",
  "severity": "warning",
  "code": "MWB.INPUT.TARGET_CLIPPING_HEURISTIC",
  "name": "target_clipping_heuristic_detected",
  "message": "Repeated full-scale samples suggest clipping in the target.",
  "attributes": {
    "peak_linear": 1.0,
    "repeated_peak_samples": 24,
    "threshold_samples": 8
  },
  "legacy_code": 3001,
  "retryable": false
}
```

The code format is namespaced text. Code meanings are immutable within a major
event schema. A new meaning gets a new code.

## Stage vocabulary

Initial stable stages:

```text
job
ingest.target
ingest.reference
analysis.target
analysis.reference
planning
render.match
render.correct
render.limit
render.preview
artifact.encode
artifact.commit
manifest.commit
cleanup
```

Adapters may add `queue`, `batch`, `cli`, or `service`, but they do not rename
core stages.

Each stage emits:

- `stage_started`;
- zero or more progress/decision/debug events; and
- exactly one of `stage_succeeded`, `stage_failed`, or `stage_cancelled`.

Terminal job events are `job_succeeded`, `job_succeeded_with_warnings`,
`job_failed`, or `job_cancelled`. The event stream and manifest must agree.

## Progress

Progress is stage-based, not a fabricated smooth percentage. An adapter may
map known stage weights to an estimate, but the event states remain:

- queued/accepted;
- current stage;
- completed/total work units when the stage has meaningful units; and
- terminal outcome.

Long numerical operations should report bounded milestones only when doing so
does not materially alter performance. Progress event frequency is rate-limited.

### GUI operation projection

The portal translates one submitted dry run or render into an operation record
with `queued`, `running`, `succeeded`, or `failed` worker state, current stage,
timestamps, bounded events, and a structured result/error. A single background
worker serializes mastering; a second submission fails busy. A request with
several targets expands sequentially into independent target runs. Its result
also reports aggregate `succeeded`, `partial_failure`, or `failed` status,
success/failure counts, the `continue-independent-targets` policy, and each
target's state/run ID/error. The worker can return normally with an aggregate
partial failure, so operator projections must display the aggregate rather than
infer all-target success from worker completion.

The current process keeps at most 50 operation summaries and 4,000 events per
operation. Those bounds protect the UI, not the durable audit record.

The **Activity** workspace projects operation History, while its **Event log**
utility polls the bounded event stream. Master also projects the active task in
its readiness/run rail, alongside client-derived prerequisites for inputs,
references, destination, and output. A browser refresh or close does not cancel
work. After portal restart, use the configuration, selection, JSONL event file,
manifest, and catalog inventory rather than expecting the in-memory operation
list to return.

Portal request logs include a generated request ID and exception type/message.
Unexpected failures include a private traceback in the portal operation/log
for diagnosis. API/media session secrets, token-bearing launch URLs, and query
values must never be copied into log messages, manifests, or catalog metadata.

### Session logs projection

The Activity workspace's **System** utility lists launcher and portal `.log`
files from the private workspace. Selecting a log fetches no more than its
newest 256 KiB and labels a truncated tail. The server seeks directly to that
tail instead of reading an unbounded file. The application rejects
nested/non-`.log` names and confines reads to `private-workspace/logs/`;
**Show in folder** uses the same workspace-owned reveal boundary.

A normal GUI launch prints and logs only the local origin. The launcher closes
its transcript before portal handoff, so a private URL printed for
`--no-browser` or automatic-browser failure remains in the live console rather
than a retained launcher log. Portal access logging replaces the API and
media-session secrets with `[REDACTED]` and strips query values. The headless
GUI smoke accepts an explicit workspace/utility and viewport, waits for the
`data-app-state="ready"` sentinel, verifies the unified-workflow landmarks, and
then scrubs its ready document, rendered DOM, stdout, stderr, and browser log
before retaining them.
This secret handling does not make the remaining paths, labels, or exception
detail in Session logs public-safe.

## Event sinks

The application injects a job-scoped `EventSink`:

- in-memory collector for tests and embedding;
- newline-delimited JSON file for durable private diagnosis;
- human console renderer;
- composite/fan-out sink;
- the portal operation sink used for a bounded live projection; and
- future queue/service publisher.

A sink has explicit required/optional semantics. Failure of an optional verbose
sink emits through a fallback and may continue; failure of the required audit
sink prevents successful artifact commit. Sink failures never call DSP with
different numerical settings.

No process-global handler controls core job events.

## Verbosity

| Level | Intended content |
|---|---|
| `info` | lifecycle, selected modes, artifacts, major safe decisions |
| `warning` | questionable input, guardrail action, optional artifact failure |
| `error` | terminal/operation failure and cause code |
| `debug` | stage parameters, aggregate measurements, cache decisions, timings |
| `trace` | high-volume iteration/bin/chunk summaries for focused diagnosis |

Production-capable builds keep debug/trace code paths but disable them by
default. Never log sample arrays. Per-bin values belong in a bounded diagnostic
artifact, not individual log lines.

## DSP decision logging

At debug or in the manifest, capture:

- requested/effective sample rates and frame counts;
- channel canonicalization;
- analysis pieces and selected-piece count/duration;
- named RMS/peak/spectral measurements with units;
- initial and each corrective gain in linear and derived dB form;
- Mid/Side filter hashes, length, response extrema, and energy;
- spectral/gain guardrail count and frequency ranges;
- limiter requested/applied/bypassed and threshold;
- preview start/end/fade frames; and
- output encoder/container/subtype and reopen verification.

Never evaluate unsafe formatting merely to produce a disabled log message.
Measurements such as `20*log10(x)` use safe functions with explicit zero
representation (`-inf` in data or a defined text label) and cannot crash the
job.

## Metrics

Metrics are aggregate operational signals, not a second source of job truth:

- jobs by terminal state and stable code;
- stage duration distributions;
- decoded frames/bytes and output bytes;
- peak working-memory estimate/observation;
- cache hit/miss/incompatible/corrupt;
- guardrail intervention counts;
- output mode/format counts;
- cancellation latency; and
- cleanup failures/stale transactions.

Labels must be low-cardinality. Never label metrics with job ID, path, filename,
hash, reference title, or user-entered text.

## Redaction

Default event data excludes:

- absolute input/output/temp paths;
- raw filenames and user labels unless explicitly permitted;
- audio samples, spectra, or filter arrays;
- secrets, tokens, headers, cookies, and connection strings;
- full command lines that may contain private paths; and
- full content hashes in human console output.

The GUI deliberately displays selected local paths, content identities, and
raw manifest JSON to the local operator. That is a private diagnostic view, not
a relaxation of export/log redaction requirements. Screenshots and browser
history can therefore contain sensitive project metadata.

Use role-based labels (`target`, `reference[0]`), opaque artifact IDs, shortened
hashes for display, and path-root aliases. The manifest may store full content
hashes when local policy permits because reproducibility depends on them; this
is explicitly declared in retention/privacy settings.

Exception messages from libraries are untrusted text. Store a sanitized summary
in normal events and the original traceback only in a permission-restricted
private debug artifact under bounded retention.

## Legacy Matchering mapping

Upstream codes remain useful during migration:

- 2003–2010 map to job/stage lifecycle;
- 2101, 2201–2203 map to canonicalization/input observations;
- 3001–3004 map to target warnings;
- 4001–4202 map to input/validation/internal failures.

The private event keeps a `legacy_code` when the semantics genuinely match.
New conditions—non-finite input, guardrail rejection, cancellation, artifact
transaction failure—receive project-native codes. A numeric legacy code is
never reassigned.

## Diagnostic report

A failure report presents:

1. terminal state, job ID, software/algorithm identity;
2. failed stage and stable code;
3. cause chain;
4. safe observed/requested values;
5. likely remediation;
6. warnings before failure;
7. artifact/cleanup state; and
8. exact local reproduction command or manifest reference when safe.

Successful reports summarize plan decisions and before/after measurements.
Reports are generated from structured records to avoid divergence.

## Retention and rotation

- Per-job event files and debug artifacts have size and age limits.
- A terminal manifest records whether events were truncated/rotated.
- Errors and warnings are summarized before verbose events may be dropped.
- Cleanup is triggered by job finalization and an independent scheduled sweep;
  it is never dependent only on another user creating a job.
- A debug retention override is explicit, time-bounded, and recorded.

See [security and privacy](../policies/security-and-privacy.md).

## Test obligations

- concurrent sinks/configurations show no cross-talk;
- event sequences are monotonic and stage terminals are complete;
- failed optional and required sinks follow their different policies;
- debug disabled/enabled produces numerically identical output;
- silence/non-finite formatting cannot crash emission;
- redaction catches absolute roots, filenames, and synthetic secret patterns;
- event/manifest terminal states agree;
- rate limiting bounds trace/progress volume; and
- schema compatibility tests reject unknown major versions.
