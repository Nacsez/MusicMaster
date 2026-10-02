# Test strategy

Status: **normative strategy; compatibility/weighted/catalog/portal slices shipped, golden corpus planned**

The project treats tests as executable evidence. The primary risks are silent
sonic drift, non-finite or unsafe output, dependency-sensitive numerical
changes, incomplete artifacts, and failures that cannot be diagnosed from the
job record.

## Test objectives

1. Freeze the useful behavior of Matchering 2.0.6 before refactoring.
2. Turn every known defect and edge-policy row into a durable regression.
3. Verify stage contracts independently and as a complete job.
4. Detect sound-changing numerical drift without relying only on byte hashes.
5. Verify that failures and cancellations preserve truthful manifests/events.
6. Keep a fast suite suitable for every change and a complete suite for gates.
7. Minimize dependence on copyrighted audio and manual error transcription.

## Test layers

| Layer | Purpose | Typical evidence |
|---|---|---|
| Model/unit | Validation, hashes, configuration, schema, math helpers | Exact values, typed errors, schema round-trip |
| Property/invariant | Broad numerical and state properties | Finite output, bounds, shape, order-invariance |
| DSP stage | Analysis, filters, correction, limiter, previews | Arrays and measurement deltas |
| Characterization | Capture upstream 2.0.6 behavior | Baseline metrics/artifacts and expected failures |
| Contract | Ports/adapters/event/manifest/cache semantics | Shared adapter suites, schema validation |
| Integration | Decode → analyze → plan → render → commit | Reopened artifacts, events, manifest |
| Workflow | CLI, batch, service boundary, DAW bundle | Exit/status/result and collision/cancellation behavior |
| GUI application | HTTP-independent portal operations and serialized worker | Catalog/job/run results, busy state, events, and structured failures |
| Local portal | Loopback HTTP, browser assets, native dialogs, launcher dispatch | Token/Host/origin/CSP/body contracts, route JSON, fixed subprocess arguments |
| Security/resource | Malformed inputs, paths, budgets, cleanup | Bounded failure and no escaped/corrupt artifact |
| Performance | Time and peak memory trends | Versioned benchmark record, not a correctness substitute |
| Listening | Intentional sonic changes | Level-matched review record |

### GUI-first portal tests

The portal must remain testable without opening a real browser or dialog:

- application-layer tests use temporary workspaces and fake mastering services;
- operation-manager/application tests prove one-worker serialization, busy
  rejection, 1–32-target expansion, continue-after-target-failure aggregate
  results, per-target evidence, strict output preferences, event capture,
  bounded history, success, and structured failure;
- HTTP tests bind only loopback and exercise the token, Host, Origin/Referer,
  CORS refusal, strict JSON/body limit, error mapping, static assets, security
  headers, and catalog-ID-only authenticated HTTP Range media, including
  API/media-secret and query-value redaction;
- asset-contract tests prove the three primary workspaces, separately reached
  utility panels, unified mastering flow, batch picker/readiness rail, direct
  A/B controls, contextual accessible names, guarded dialog keyboard behavior,
  task-target reconstruction, compact Activity run cards, honest initial-load
  failure state, and backend-facing hooks use packaged HTML, CSS, and JavaScript
  with no Electron/CDN/external frontend asset;
- native-dialog tests mock platform and subprocess calls, verify fixed
  PowerShell script bodies and `shell=False`, and prove Explorer selects rather
  than executes a path;
- CLI tests prove lazy `gui`/`portal` dispatch; and
- launcher `-CheckOnly` remains a noninteractive process test.

[`Invoke-PortalSmoke.ps1`](../../scripts/Invoke-PortalSmoke.ps1) starts an
isolated random-port portal, renders it in headless Chrome/Edge at a configurable
viewport, requires the stable application-ready state and requested visible
panel, captures a screenshot and DOM, calls authenticated shutdown, waits for
clean process exit, and redacts the launch token from every retained text
artifact. The default is the `master-job` workspace. `-InitialTab` can exercise
`master-job`, `catalog`, `runs`, `events`, `diagnostics`, or `reference-sets`;
`-ViewportWidth` and `-ViewportHeight` support targeted responsive smoke runs.

The September 2026 redesign gate additionally uses an isolated generated-audio
workspace with several sources, references, deliverables, and two mastered
versions of one source. A dependency-free DevTools audit drives the real
packaged page at 1440 × 1000 and 1280 × 800 and checks direct A/B inventory,
custom-pair preservation, blank-slot clearing, swap/playhead continuity,
keyboard focus across rerendered rows and controls, responsive action
reachability, dialog Enter submission, Activity run-card overflow and toast
containment, completed-task reconstruction on a fresh authenticated page,
browser/runtime errors, token leakage, logs, and graceful shutdown. The final
31-check record is
[`verification-summary.json`](../../artifacts/ux-populated-smoke/20260903T234045034Z/verification-summary.json).
Its workspace and audio live only below the retained test-artifact root; the
operator catalog is never used.

The packaging gate builds a wheel both in the standard isolated environment
and with `--no-isolation` against the prepared environment, then verifies that
`index.html`, `app.css`, and `app.js` are present in the wheel.

The headless browser smoke and automated adapter tests do not replace an
interactive keyboard/accessibility review, native WinForms picker exercise, a
full responsive-layout matrix, or a mastering render started from the GUI.
Those remain explicit supported-workstation evidence.

## Fixture strategy

### Generated, redistributable corpus

Use deterministic generators with a recorded seed, sample rate, duration,
amplitude, phase, and channel construction:

- silence and near-silence at threshold neighbors;
- impulse at beginning/middle/end;
- single and multiple sine tones, sweeps, and filtered noise;
- Mid-only mono-equivalent and Side-dominant/opposite-polarity stereo;
- independent stereo tones/noise;
- DC offset and constant signals;
- clipped and repeated-peak/limited-looking envelopes;
- reference energy in target spectral nulls;
- very short and limit-boundary durations;
- multiple sample rates, PCM depths, and float;
- NaN/Inf arrays for the in-memory boundary; and
- malformed/truncated/unsupported file fixtures that are safe to store.

Generated fixture identity includes generator version and parameter JSON. Tests
must not depend on random state outside the fixture seed.

### Private listening corpus

Real music used for listening or private regression must have recorded rights
and must not be committed unless redistribution is permitted. Store only an
opaque corpus item ID, local content hash, rights note, and expected role in
versioned metadata. Automated tests skip cleanly when optional private media is
absent.

### No production/user audio in test artifacts

Never promote uploaded or personal audio into a fixture without explicit
authorization and rights review. Failure bundles use generated or redacted
content.

## Characterization-first protocol

ADR-0002 defines the decision. For every upstream case:

1. pin Python and dependency versions;
2. record upstream source/archive hashes and configuration;
3. run through the upstream public API when possible;
4. capture structured observations externally, since upstream returns `None`;
5. reopen output and calculate independent measurements;
6. store pass/failure, legacy log codes, exception identity, timings, output
   hashes, and metrics;
7. classify the result as desired parity, tolerated numerical variance, known
   upstream defect, or intentional future divergence; and
8. review and version the baseline.

Known defects remain executable as `expected-upstream-failure` cases. The
private engine's corresponding test expects the policy-correct result, not the
upstream exception.

## Required characterization matrix

At minimum, cross these dimensions without attempting a combinatorial
explosion:

| Dimension | Required representatives |
|---|---|
| Channels | mono target, mono reference, stereo pair, >2-channel rejection |
| Rates | 44.1 kHz, 48 kHz resample, one high rate, invalid rate through array adapter |
| Duration | just below minimum, minimum boundary, shorter than preview, normal, maximum boundary, over maximum |
| Content | normal synthetic, silence, near-silence, clipped, limited heuristic, DC, spectral null, anti-phase |
| Relationship | distinct, byte-identical, numerically equal after decode |
| References | one-reference compatibility, one-reference native parity, two or more independent weights, zero-in-one-dimension, duplicate content, reordered content, and 33-reference rejection |
| Output | PCM16 limited, PCM24 limited, FLOAT raw, normalized no-limiter, paired preview |
| Config | defaults, zero corrections, non-default FFT/smoothing, fractional preview, invalid limiter bounds |
| Failure | decode, write, disk-budget simulation, cancellation, event sink, manifest commit |

Pairwise generation may cover combinations, but every named row in the
[edge-case policy](../policies/edge-cases.md) needs a direct owned test.

## Oracle hierarchy

No single metric proves audio correctness. Use, in order:

1. schema and state invariants;
2. finite samples, shape, duration, channel, rate, subtype, and bounds;
3. plan equivalence and recorded guardrail decisions;
4. independent time/spectral measurements;
5. sample or perceptual delta against a reviewed baseline;
6. listening review for intentional audible changes.

### Core numerical metrics

- maximum absolute sample delta and RMS sample delta;
- output RMS and selected loud-section RMS delta;
- sample peak and crest-factor delta;
- Mid and Side energy deltas;
- log-frequency response delta over defined bands;
- filter coefficient/response and energy delta;
- duration/sample-count delta;
- optional correlation/null residual; and
- once implemented, separately named LUFS and oversampled true-peak metrics.

Tolerances are versioned data with a rationale, not literals scattered across
tests. Use absolute and relative tolerances appropriate to near-zero values.

### Byte hashes

Use hashes for identity and exact-replay tests. Do not make byte equality the
sole DSP oracle across operating systems or dependency versions until proven
stable. Container/codec headers may differ even when decoded samples agree.

## Invariants and properties

Every accepted render must verify:

- all samples and measurements are finite;
- expected frame/channel/rate contracts hold;
- plan and artifact identities match the manifest;
- requested bounds and guardrails hold;
- terminal event and manifest state agree;
- no output is published before commit;
- `match_amount=0` and `1` follow their specified endpoints;
- weighted-reference results are order-invariant after canonical sorting;
- duplicate-reference results equal an explicitly coalesced request while the
  manifest still inventories every requested occurrence;
- catalog selections/exports reject tampering, import atomically, and retain
  exact source/artifact identity;
- duplicated mono has zero Side within tolerance before any explicit stereo
  operation;
- analysis/profile serialization round-trips; and
- replay either satisfies exact or declared numerical-equivalence tier.

Use property-based generation for finite arrays, valid configurations, weight
sets, names/paths, schema round-trips, and fault boundaries. Bound generated
array sizes so the fast suite remains predictable.

## Regression rules

- Every fixed defect gets a test that fails for the pre-fix reason.
- The test name or metadata references a requirement and, where applicable,
  edge-policy row or issue ID.
- Never update a golden artifact merely to make a test green.
- Baseline updates include a generated delta report, reason, algorithm/schema
  impact, and reviewer approval.
- Dependency-lock updates run the full numerical comparison suite.
- Intentional sonic differences require the listening protocol as well as
  automated safety tests.

## Listening protocol

Listening is mandatory for a deliberate DSP change and optional for pure
adapter/documentation work.

1. Use material with documented private-use rights.
2. Render old/new paths from identical canonical input and plan intent.
3. Level-match comparisons independently so loudness preference does not
   dominate.
4. Randomize labels and order where practical.
5. Check full mix plus selected stress regions on at least one trusted system
   and headphones.
6. Record only opaque corpus IDs, environment, numerical deltas, result
   preference/concern, and reviewer decision.
7. Link the review record to the algorithm-version change.

This is an engineering guard, not a scientific claim of universal preference.

## Test tiers and gates

### Fast change gate

Runs on every behavioral change:

- unit/model tests;
- small DSP stage tests;
- schemas/contracts;
- deterministic generated smoke job;
- documentation/internal-link check; and
- lint/type/license-header checks established by tooling.

Target duration should be measured and kept short enough for routine use.

### Full private gate

Runs before merging DSP/dependency/format changes and before private milestone
tags:

- complete characterization and regression corpus;
- all supported output/container tests;
- batch, concurrency, cancellation, and fault injection;
- cache compatibility;
- CLI and job-boundary end-to-end tests;
- GUI application/HTTP/native-dialog, headless-browser smoke, and interactive
  operator acceptance;
- security/path/retention tests;
- supported Python/platform matrix;
- performance and peak-memory comparison; and
- listening review when required.

### Optional public gate

Inactive until a public-release decision. It adds clean-environment install,
package/container reproducibility, SBOM/license scan, vulnerability scan,
public-server security tests, and corresponding-source verification.

## Failure artifacts

On test failure, preserve:

- test/requirement ID;
- seed and generated-fixture parameters;
- safe job manifest and structured event stream;
- code/dependency identity;
- metric delta report;
- stack trace in a private debug artifact; and
- generated audio snippets only when small and policy-permitted.

Do not require a person to copy console errors manually. CI or the local test
runner should point directly to the retained evidence directory. Retention is
bounded and cleanup documented. HTTP/session tokens must be redacted from
retained request, browser, DOM, readiness, stdout, and stderr artifacts.

## Performance tests

Measure wall time and peak resident memory for representative durations and
stereo/rate cases. Include stage timings. A historical container observation
is not a baseline. Establish measurements on identified hardware after the
private runner exists.

Performance regression thresholds should initially alert rather than fail until
variance is understood. Resource-bound violations always fail.

## Test ownership and traceability

Test metadata uses stable requirement IDs such as `CAP-005` and `ROB-002`.
The [status register](../status/implementation-status.md) links requirement
groups to test locations and evidence. A new P0/P1 requirement is incomplete
until its test owner and intended tier are named.

## Anti-patterns

- Treating "the file was written" as DSP correctness.
- Comparing lossy files sample-for-sample.
- Using only full songs, which makes failures slow and hard to localize.
- Hiding dependency updates inside golden-baseline changes.
- Suppressing a non-finite value to make a render finish.
- Asserting LUFS or true-peak compliance using RMS/sample-peak measurements.
- Allowing optional private corpus absence to skip all core coverage.
- Committing user audio, absolute source paths, or secrets in failure bundles.
