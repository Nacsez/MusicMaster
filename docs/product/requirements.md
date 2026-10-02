# Product requirements

Status: **target specification; implementation varies**

This is the normative requirement catalog for the private working version.
Requirements describe intended behavior even when their current status is
`PLANNED`. Delivery status and evidence live in the
[traceability register](../status/implementation-status.md).

## Reading the catalog

- **P0** — required for a safe, parity-capable private core.
- **P1** — required for the private working-version completion gate.
- **P2** — valuable extension that may follow the private completion gate.
- **Acceptance** is observable and must be represented by automated tests
  unless the criterion explicitly calls for listening or document review.

### Core audio behavior

| ID | Pri | Requirement | Acceptance criteria |
|---|---:|---|---|
| CAP-001 | P0 | Accept a distinct target and reference from supported file and in-memory adapters. | The same valid stereo fixture can enter through both adapters; both produce equivalent canonical audio and metadata; identical target/reference input is rejected unless an explicitly documented diagnostic override is enabled. |
| CAP-002 | P0 | Canonicalize supported input to finite floating-point audio with explicit sample rate and channel semantics. | Mono and stereo fixtures at supported rates produce documented canonical shapes; NaN/Inf, empty, unreadable, and unsupported channel layouts receive stable outcomes from the edge policy. |
| CAP-003 | P0 | Analyze representative loud sections and compute loud-section RMS for target and reference. | A deterministic synthetic fixture yields stable selected regions and RMS measurements within test tolerance; the report labels the measure as RMS, not LUFS. |
| CAP-004 | P0 | Match target level toward the reference using the planned gain. | The plan records the gain; render applies that gain; output RMS satisfies the configured tolerance on the characterization corpus without unbounded amplification. |
| CAP-005 | P0 | Derive and apply separate Mid and Side spectral matching filters. | The plan exposes filter identity/coefficients and frequency-response summary; render reproduces the planned result; synthetic Mid-only and Side-bearing fixtures exercise both paths. |
| CAP-006 | P0 | Correct post-filter level using bounded iterative correction. | Correction iteration count and each applied gain are recorded; the loop respects configured bounds; zero/near-zero energy terminates with a policy result instead of division by zero. |
| CAP-007 | P0 | Support optional final limiting and non-limited render paths. | One job can request limited, peak-normalized non-limited, and raw float artifacts; each artifact reports limiter/normalization state and passes finite-sample and peak assertions appropriate to its mode. |
| CAP-008 | P0 | Resample supported inputs to a configured internal rate and render at the declared output rate. | Fixtures at multiple supported rates have correct duration within tolerance, reported source/internal/output rates, and no silent implicit rate change. Non-44.1 kHz modes remain experimental until separately verified. |
| CAP-009 | P0 | Generate time-aligned target/result previews from the selected output region. | Paired previews use identical start/end samples, have bounded fades, handle tracks shorter than the preview, and reject invalid fractional or zero strides during configuration validation. |
| CAP-010 | P0 | Preserve the useful Matchering 2.0.6 configuration surface behind typed validation. | Every inherited option listed in the [baseline](../baseline/upstream-2.0.6.md#configuration-surface) is mapped, documented, range-checked without `assert`, serialized in the manifest, and covered by default plus invalid-boundary tests. |
| CAP-011 | P0 | Encode supported output containers/subtypes without silently changing the selected mode. | PCM16, PCM24, and FLOAT WAV smoke tests reopen with expected rate/channels/subtype; additional SoundFile-supported combinations are capability-checked and either succeed or return a stable unsupported-output error. |
| CAP-012 | P1 | Measure and clearly report sample peaks, RMS, duration, rate, channels, and available spectral summaries before and after processing. | A schema-valid report contains units and method names; measurements recomputed by an independent test helper agree within tolerance. LUFS and true peak are absent or explicitly marked unavailable until implemented. |
| CAP-013 | P1 | Offer EBU R128 integrated loudness as an explicit native analysis/planning option without relabeling parity RMS. | The selected metric and implementation/version are in analysis, plan, and manifest; a standards/reference corpus agrees within reviewed tolerance; RMS parity mode remains independently selectable and unchanged. |
| CAP-014 | P1 | Offer oversampled true-peak measurement and a true-peak-aware output policy. | Intersample-peak fixtures distinguish sample peak from true peak; oversampling/filter method and threshold are recorded; enabled outputs satisfy the declared true-peak tolerance after encoding/reopen; unsupported engines reject the request. |
| CAP-015 | P1 | Make bit-depth reduction and dithering explicit. | `none`, TPDF, and any noise-shaped mode have documented applicability; FLOAT output bypasses dither; integer-output tests verify subtype, deterministic/reproducible seed policy where promised, and bounded statistical/error-shaping properties. |
| CAP-016 | P1 | Apply an explicit metadata policy. | `drop` removes nonessential source tags; `copy-safe` copies only a documented whitelist and never paths/private job data; container-specific reopen tests verify the policy; unsupported combinations reject rather than silently downgrade. |
| CAP-017 | P2 | Support an external limiter as a separately audited post-render adapter. | The adapter consumes an identified raw FLOAT artifact, uses an administrator-approved executable/argument template without a shell, enforces timeout/resource/transaction rules, records tool/version/command policy and measurements, and fails without publishing a partial master. |

### Pipeline contracts and creative controls

| ID | Pri | Requirement | Acceptance criteria |
|---|---:|---|---|
| PIPE-001 | P0 | Expose analysis as a side-effect-bounded operation that returns immutable, versioned reports/profiles. | Analysis does not write final audio; repeated analysis of the same canonical input/config is equivalent; serialized profiles reject incompatible schema versions. |
| PIPE-002 | P0 | Build an explicit, serializable match plan from target analysis, reference profile(s), and policy/configuration. | A plan contains all render-affecting decisions, source/profile identities, algorithm/schema versions, gains, filter references or coefficients, guardrail actions, and warnings; schema round-trip is lossless. |
| PIPE-003 | P0 | Render only from canonical target audio plus an explicit plan. | Render requires no hidden reference audio or global logger; replaying a valid plan in the same declared environment produces equivalent output within tolerance. |
| PIPE-004 | P0 | Remove process-global mutable job state from core processing. | Concurrent jobs with distinct configurations/event sinks have no cross-talk in tests; cancellation and warnings remain correlated to the correct job. |
| PIPE-005 | P1 | Cache reusable reference profiles safely. | Cache keys include reference content hash, analysis configuration, algorithm version, and schema version; cache hit/miss is observable; a changed key component cannot reuse an incompatible profile. |
| PIPE-006 | P1 | Provide a bounded match-amount control. | `0` produces the documented neutral/bypass plan, `1` produces the full plan, intermediate values interpolate by the documented rule, out-of-range values are rejected, and all values appear in the plan/manifest. |
| PIPE-007 | P1 | Build one plan from as many as 32 requested weighted references. | Level and frequency weights are finite, non-negative, normalized independently, and tied to source hashes; each dimension rejects a zero total; duplicate content is inventoried then coalesced by hash; effective profiles are reduced in stable content order using the documented logarithmic-amplitude/geometric rule; order-invariance, duplicate equivalence, one-effective-profile behavior, and single-reference parity are tested. |
| PIPE-008 | P1 | Export inspectable plan/filter data for external study and DAW workflows. | Export includes version, rate, Mid/Side filter data, gains, units, and provenance; import round-trips or fails with a stable compatibility error; no claim is made that every DAW can directly load the format. |

### Workflows and adapters

| ID | Pri | Requirement | Acceptance criteria |
|---|---:|---|---|
| FLOW-001 | P0 | Provide a documented Python API for analyze, plan, render, and one-call convenience processing. | Examples execute in smoke tests; the convenience call produces the same plan/render outcome as the explicit stages under the same configuration. |
| FLOW-002 | P1 | Provide a non-interactive command-line workflow. | A command can run one job from explicit paths/config, emits a job ID and meaningful exit code, supports machine-readable status, and is exercised by an end-to-end smoke test. |
| FLOW-003 | P1 | Process many targets against one immutable reference profile. | The GUI accepts 1–32 distinct targets and 1–32 references; each target receives its own configuration, selection, run ID, manifest, event log, output namespace, and catalog bindings. A target failure follows an explicit continue/stop policy and cannot erase successful siblings. Completion additionally requires analyzing the reference profile once, applying resource-bounded concurrency, and reporting per-item plus aggregate outcomes in batch tests. |
| FLOW-004 | P1 | Process one target separately against many alternative references without artifact collisions. | A batch test creates distinct plans/previews/outputs, preserves reference identity in each manifest, and continues or stops according to an explicit failure policy. This comparison workflow is separate from PIPE-007 combining several references into one profile. |
| FLOW-005 | P1 | Expose a job-oriented application boundary suitable for a local web/worker adapter. | Submit/status/result contracts and operation events are adapter-neutral and tested without a live web server; work is serialized until engine concurrency is proven; cancellation remains unavailable and visibly disabled until ROB-004 is met; core modules do not import HTTP/browser packages. |
| FLOW-006 | P1 | Support a DAW handoff package. | The package contains a FLOAT artifact, plan export, measurements, and a human-readable note that final limiting is not applied; a smoke test validates inventory and audio subtype. |
| FLOW-007 | P1 | Support a GUI-first private localhost browser interface over the job boundary. | The root launcher opens a packaged no-Electron/no-CDN interface on a random `127.0.0.1` port; page/API access is session-token gated; Host/origin/CSP/no-store boundaries are tested; source audio is selected through native dialogs and read in place with no browser upload/copy; dense collections use conventional boxed scrolling tables/queues with sticky headers and stacked detail at laptop widths rather than clipped columns or per-file capsules; the source-centered library automatically prepares A = original and B = master when exactly one playable version exists, exposes an explicit version/deliverable choice otherwise, and switches catalog media without general filesystem serving; job status/events remain responsive during one serialized background operation; shutdown refuses active work; unavailable cancellation and advanced controls are visible but disabled; terminal fallback remains available. |
| FLOW-008 | P1 | Provide a private local track/reference catalog and interactive operator workflow. | From GUI tabs, the operator can add targets/references without copying source audio; distinguish content identity from locations; retain one stable original row while completed mastered-output artifacts become run/version children; search/filter/verify/relink/archive/restore records without deleting source audio; update display labels without renaming files or rewriting history; select preferred versions/deliverables across songs and export fingerprint-verified copies with an editable suffix, exclusive destinations, and per-item results; recoverably discard/restore one version's mastered deliverables through verified quarantine and indexed tombstones while retaining the original and audit chain; create, rename, reorder, independently weight, reuse, and delete named reference sets; export and strictly reimport complete catalogs and one-target content-bound selections, restoring an imported selection's target/reference order/weights to Master Job; idempotently recover/index a preserved committed manifest after catalog finalization failure; choose 1–32 target inputs and 1–32 references; select a persistent default delivery folder or a per-job override with no-reuse per-target subfolders; validate and dry-run before rendering; and inspect/play runs, artifacts, manifests, live/durable events, diagnostics, Session Logs, and capability limits. Catalog mutations/imports/recovery/preferences/version lifecycle are transactional or compensating as applicable, versioned, offline, and covered by model, application, HTTP, native-dialog, integration, and launcher tests. |

### Robustness, safety, and determinism

| ID | Pri | Requirement | Acceptance criteria |
|---|---:|---|---|
| ROB-001 | P0 | Apply the documented edge-case decision table before unsafe DSP operations. | Every P0 row in the [edge-case policy](../policies/edge-cases.md) has an automated outcome test with stable code, severity, remediation, and no unexplained raw traceback. |
| ROB-002 | P0 | Bound spectral ratios, gains, filter energy, and correction iterations. | Configured guardrails appear in the plan; adversarial spectral-null and near-silent fixtures remain finite and bounded or fail before render with a specific policy code. |
| ROB-003 | P0 | Write artifacts transactionally and clean temporary material. | Failure/cancellation injection at each write stage leaves no corrupt file under the final name; temporary files are removed or recorded for recovery; existing files follow explicit overwrite policy. |
| ROB-004 | P1 | Enforce configured resource limits and cooperative cancellation. | Duration, decoded size, working-memory estimate, concurrency, and output budget are checked; cancellation tests stop between bounded stages and emit a terminal event/manifest state. |
| ROB-005 | P0 | Define and test deterministic-equivalence tolerances. | Same-platform reruns produce equal plans and audio hashes when byte determinism is promised, otherwise metric deltas remain within versioned tolerances; cross-version changes require baseline review. |
| ROB-006 | P0 | Represent mono honestly. | Mono duplication is recorded as canonicalization; Side analysis remains zero within tolerance unless an explicit stereo-generation feature exists; reports never label duplication as newly created stereo width. |
| ROB-007 | P1 | Validate configuration through typed exceptions rather than removable assertions. | Running Python with optimization enabled produces the same invalid-config outcomes and codes as normal execution for every boundary fixture. |

### Evidence, observability, security, and governance

| ID | Pri | Requirement | Acceptance criteria |
|---|---:|---|---|
| OBS-001 | P0 | Produce a versioned manifest for every accepted job, including failed and cancelled jobs when a job ID was allocated. | Schema validation covers identity, hashed provenance, config, environment, stage outcomes/timings, measurements, warnings, and terminal state. The artifact inventory includes the target, every requested reference occurrence and its requested/normalized/effective-group weights, owned configuration/selection inputs, every requested output/preview, event log, retained partials, and catalog bindings where used; no required secret or raw audio content is embedded. |
| OBS-002 | P0 | Emit per-job structured events through injected sinks. | Events meet the [event contract](../operations/observability.md#canonical-event-envelope), preserve ordering metadata, correlate stage start/end/failure, isolate concurrent sinks, and tolerate a failing optional sink without corrupting audio. |
| OBS-003 | P1 | Provide an operator-readable diagnostic report derived from authoritative structured data. | A failed fixture report identifies stage, cause chain, stable code, safe context, and remediation without requiring console transcript assembly. |
| SEC-001 | P0 | Keep core processing offline and private by default. | A network-denied integration test completes a local job; dependency/core review finds no telemetry or implicit upload; any remote adapter is opt-in and outside core. |
| SEC-002 | P0 | Treat files, paths, codecs, plans, and manifests as untrusted input at boundaries. | Traversal, symlink/overwrite, oversized input, malformed media, malformed schema, and unsupported codec tests fail within declared resource bounds and do not escape allowed roots. |
| SEC-003 | P1 | Enforce configurable retention and privacy-safe diagnostics. | Success, failure, and cancellation cleanup tests honor policy; logs redact configured roots and user labels by default; reference reuse and long-term retention require explicit settings. |
| SEC-004 | P1 | Keep the private graphical adapter confined to one local desktop session. | The server cannot bind outside `127.0.0.1`; normal launch uses a random port and fresh high-entropy token; APIs require the token and validate loopback/Host/origin; a separate random `HttpOnly`/`SameSite=Strict`, media-path-scoped cookie authorizes only catalog-ID media streaming, never APIs or arbitrary paths; full/single/suffix HTTP Range and invalid-range behavior are tested; CORS is refused; strict JSON bodies are bounded; CSP and defensive response headers disallow remote active content/framing; packaged assets have no Electron/CDN/telemetry; native picker paths are data, never scripts; no audio-upload route exists; normal startup/logging omits the token-bearing URL, launcher transcript closes before portal handoff, and HTTP logs redact both secrets/query values; documentation forbids proxy/LAN/tunnel/container exposure and states that same-user processes remain in the trust boundary. |
| TEST-001 | P0 | Capture upstream behavior with a redistributable characterization corpus before DSP refactoring. | The corpus covers every upstream stage/output branch and named defect class; baseline artifacts include code/dependency identity and reviewed tolerances. |
| TEST-002 | P0 | Maintain automated regression and smoke suites for supported workflows. | The documented fast suite runs on each change; full DSP, format, GUI application/HTTP/assets/native-dialog, batch, and container suites run at the declared gate; a headless supported-browser smoke covers packaged bootstrap and orderly shutdown with token-redacted artifacts; an interactive workstation pass covers real layout, focus, pickers, background work, and a GUI-started render; failures preserve diagnostic artifacts without leaking protected audio. |
| TEST-003 | P1 | Include a documented human listening protocol for intentional sonic changes. | Every intentional DSP change records anonymized comparison IDs, level-matched method, reviewer conclusion, numerical deltas, and approval; listening does not replace automated safety tests. |
| GOV-001 | P0 | Preserve GPL provenance and compatible dependency choices throughout development and sharing. | Upstream license/notices remain intact; exact provenance and dependency-license inventories are reviewable; binary releases include corresponding-source material; preparation and actual publication remain scoped to the owner's instructions. |
| GOV-002 | P0 | Keep requirements, decisions, implementation status, tests, and operator documentation traceable. | Each requirement has an owner milestone, implementation/evidence link or explicit planned state, and status date; documentation checks find no broken internal links or unsupported completion claim. |

## Windows sharing milestone — 2026-10-02

This authorized slice adds deployment acceptance without implying completion
of the older full DSP roadmap.

| ID | Priority | Requirement | Acceptance |
|---|---|---|---|
| DIST-001 | P1 | Run on Windows 10/11 Intel/AMD x64 from a single EXE. | Python/DSP/web assets are bundled; a standard user needs no Python installation or administrator rights; frozen smoke validates, renders, reopens output, serves the portal, and shuts down from an unrelated working directory; a separate clean Windows machine is recorded before claiming cross-machine verification. |
| DIST-002 | P1 | Keep each Windows user's library persistent and independent. | Default workspace is `%LOCALAPPDATA%\MusicMasteringTools\workspace`; changing EXE location or working directory retains that user's catalog/preferences; explicit workspace override is tested; competing instances cannot open the same workspace; audio paths remain user-selected absolute paths. |
| DIST-003 | P1 | Start location pickers at the actual selected location. | Folder dialogs initialize to the chosen destination and file dialogs use existing file/parent locations; remembered/default and per-job paths remain distinct across restarts; missing paths use an explicit valid fallback; Explorer opens the chosen file/folder; spaces and Unicode paths have regression coverage. |
| DIST-004 | P1 | Prepare source and binaries for reviewable sharing. | Safe source allowlist/audit excludes private media/catalogs/logs/secrets and generated artifacts; build pins, project/upstream notices, dependency/native license inventory, hashes, documented build/smoke commands, and corresponding-source instructions accompany the candidate; no CI step publishes a release automatically. |
| DIST-005 | P1 | Match desktop process lifetime to its browser session. | Normal desktop launches exit after the last workbench tab closes and a refresh grace period elapses; active mastering finishes and finalizes audit/catalog data first; refreshing, another tab, or hidden/minimized tabs retain the session; stale/crashed client and absent-first-browser handling are bounded; source GUI/CLI, `--no-browser`, and `--keep-running` stay persistent; lifetime regression and real browser/frozen checks record the behavior without deleting library/media state. |

## Completion rules

A requirement can move to `VERIFIED` only when all acceptance criteria have
evidence and the relevant test tier passes. Partial criteria keep the
requirement `IMPLEMENTED` or `SCAFFOLDED`. Upstream behavior alone is
`UPSTREAM`, even if it appears to satisfy part of a requirement, because the
private project also requires explicit contracts, diagnostics, and regression
evidence.

Any criterion that proves infeasible must be changed here through review. It
must not be silently weakened in a test.
