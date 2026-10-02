# Edge-case policy

Status: **normative target policy; implementation mostly planned**

This policy defines whether difficult inputs are rejected, transformed, warned
about, or accepted. It exists to prevent a numerical accident from becoming an
undocumented sonic choice.

The initial thresholds for "near silence," maximum spectral gain, filter
energy, decoded bytes, and working memory require characterization in Phases 1
and 3. Their values must be typed configuration, recorded in the plan/manifest,
and tested. Implementations must not invent hidden constants.

## Outcome classes

| Outcome | Meaning |
|---|---|
| `REJECT` | Stop before the unsafe stage, publish a stable domain error and safe diagnostic context, and commit no final audio artifact. |
| `NORMALIZE` | Perform a deterministic canonical transform and record it in analysis and the manifest. |
| `WARN` | Continue, but add a structured warning with likely impact and remediation. |
| `CLAMP` | Apply an explicit configured guardrail, record original/bounded values and affected range, and continue only when the bounded operation remains valid. |
| `SUPPORTED` | Process normally and record the relevant facts. |
| `EXPERIMENTAL` | Available only behind explicit opt-in; emit a warning and do not promise regression stability until promoted. |

Raw traceback text may be attached to a private debug artifact, but it is never
the only error result. All accepted jobs receive a job ID and, subject to
retention policy, a terminal manifest.

## Stable error shape

Every rejection has:

- a stable code in the project namespace;
- category (`input`, `configuration`, `analysis`, `planning`, `render`,
  `artifact`, `resource`, `cancelled`, or `internal`);
- failing stage;
- human explanation and remediation;
- safe attributes with units;
- causal code when wrapping a decoder/library failure;
- retryability; and
- a job ID if job acceptance occurred.

The exact code catalog will be versioned with the event schema. Existing
Matchering codes are retained as `legacy_code` where semantically equivalent,
not reused for different meanings.

## Configuration decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| Wrong type, NaN, Inf, or value outside declared range | `REJECT` before input decoding | Test normal and `python -O`; outcome must not depend on `assert`. |
| Fractional duration that converts to a non-integer sample count | `NORMALIZE` using one documented rounding rule, or `REJECT` if exact samples are required | Manifest records requested value and effective integer sample count. Never pass float dimensions/strides to NumPy. |
| Zero/negative window, step, rate, duration, FFT, or limiter time | `REJECT` | Include field path and allowed range. |
| FFT length unsupported by the chosen algorithm | `REJECT` | Power-of-two requirement is explicit for the parity algorithm. |
| Preview longer than track | `NORMALIZE` to full-track preview | Paired target/result timing stays identical. |
| Preview fade longer than allowed fraction | `CLAMP` by documented formula | Record effective fade samples. |
| Unknown configuration field or schema major version | `REJECT` | Do not silently ignore render-affecting options. |
| Non-44.1 kHz internal rate | `EXPERIMENTAL` until separately characterized | Explicit opt-in, separate baseline/tolerance set. |
| Contradictory output flags | `REJECT` | Output mode should be an enum rather than coupled booleans in the private API. |
| Unsafe resource limit disabled | `REJECT` in service/batch adapters; explicit opt-in in trusted local Python use | The selected policy is in the manifest. |

## Input and decode decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| Missing, unreadable, or permission-denied path | `REJECT` | Distinguish missing from decoder failure without disclosing disallowed absolute roots. |
| Path outside allowed root, traversal, or unsafe symlink | `REJECT` | Test relative traversal, junction/symlink escape, and output/input aliasing. |
| Unsupported container/subtype/codec | `REJECT` with detected capabilities | Do not claim a format is supported solely from its extension. |
| SoundFile failure with permitted FFmpeg fallback | `NORMALIZE` through isolated conversion, otherwise `REJECT` | Enforce timeout/decoded-size limits and cleanup on every exception. |
| Empty stream, zero frames, truncated stream, or no audio stream | `REJECT` | Distinguish parse failure from valid but too-short audio. |
| Duration below algorithm minimum | `REJECT` | Report required and observed frames/duration; minimum accounts for FFT and filters. |
| Duration/decoded bytes above configured limit | `REJECT` before large allocation when metadata is trustworthy, otherwise during bounded decode | Report which budget failed. |
| Zero/invalid sample rate | `REJECT` | No guessed default. |
| Mono | `NORMALIZE` by duplicate-channel parity mode and `WARN` about zero Side information | Later explicit stereo-generation features require a separate plan operation. |
| Stereo | `SUPPORTED` | Channel order is recorded. |
| More than two channels or ambiguous layout | `REJECT` by default | A future explicit downmix adapter may be added with declared matrix and tests. |
| Integer PCM | `NORMALIZE` to canonical float with known scaling | Reopened test fixture must agree with independent decoding. |
| Floating input containing NaN or Inf | `REJECT` | Report counts only, not sample content. |
| Denormal/subnormal samples | `NORMALIZE` to zero only if the configured canonicalization policy says so; otherwise `SUPPORTED` | Policy and count are recorded. |
| Lossy source detected or inferred | Target: `WARN`; reference: `WARN`/info according to UI policy | Continue when decoder output is otherwise valid; label detection as heuristic. |
| Target and reference content hashes equal | `REJECT` by default | Diagnostic override is allowed only for characterization and recorded prominently. |
| Arrays numerically equal after canonicalization but file hashes differ | `REJECT` by default | Record equality method/tolerance. |
| Zero or more than 32 requested targets | `REJECT` at the portal/application boundary | No empty batch or silent truncation; legacy `target_id` must agree with the first `target_ids` entry when both are present. |
| Duplicate target content in one batch | `REJECT` before validation/materialization | A content-addressed target must not produce two ambiguous sibling runs. |

## Audio-content decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| Exact digital silence in target | `REJECT` before logarithms/division | Explain that no meaningful level/spectral plan can be built. |
| Exact digital silence in reference | `REJECT` | A silent reference cannot define a mastering target. |
| Near-silent target | `REJECT` by default | Threshold is defined in measurement config with units; optional diagnostic analysis may continue without render. |
| Near-silent reference | `REJECT` | Prevent extreme downward/upward ratios with meaningless content. |
| One silent channel | `WARN` and continue only when Mid analysis and guardrails are valid | Record per-channel energy; do not mislabel stereo width. |
| Constant/DC-only signal | `REJECT` for mastering; allow diagnostic analysis | Tests verify no invalid interpolation/filter result. |
| Target sample clipping heuristic triggered | `WARN` | Preserve legacy code 3001 mapping when applicable. |
| Target limiting heuristic triggered | `WARN` | Preserve legacy code 3002 mapping and label the detection as heuristic. |
| Reference already above peak threshold | `SUPPORTED` with its normalization decision recorded | No silent clipping during canonicalization. |
| Strong DC offset | `WARN` initially | Do not automatically high-pass without an explicit planned operation and characterization. |
| Opposite-polarity channels / Mid cancellation | `WARN` or `REJECT` if level analysis is invalid | Side-bearing tests must remain finite. |
| Mono duplicated to stereo | `NORMALIZE` plus provenance flag | Side filter may be calculated, but applying it to zero Side cannot create width. |

## Analysis and planning decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| No loud piece selected | `REJECT` | Never take RMS of an empty selection. Include selection threshold summary. |
| Non-finite intermediate measurement | `REJECT` immediately | Event identifies operation and aggregate counts, not arrays. |
| Target spectral bin at/below floor | Use declared numerical floor, then apply maximum ratio guardrail | Record affected-bin count and frequency ranges. |
| Reference spectral bin at/below floor | Permit attenuation only within configured bound | Avoid unexplained zeroing/ringing. |
| Spectral ratio exceeds configured boost/cut | `CLAMP` and `WARN`, or `REJECT` under strict policy | Original extrema and bounds are in plan. |
| Smoothed response becomes non-finite or negative where invalid | `REJECT` | Must occur before FIR application. |
| FIR energy, coefficient peak, or predicted gain exceeds bound | `REJECT` by default; optional `CLAMP` only with a defined normalization rule | Adversarial spectral-null fixture is mandatory. |
| RMS gain/correction exceeds bound | `CLAMP` and `WARN`, or `REJECT` in strict mode | Bound and action are planning config. |
| Correction fails to converge/improve | Stop at iteration limit and `WARN` if output is safe; otherwise `REJECT` | Record per-iteration measurements and termination reason. |
| Multi-reference zero total level or frequency weight | `REJECT` that dimension before DSP | Totals are independent; negative/non-finite weights also reject. |
| More than 32 requested references | `REJECT` during capability validation | Requested occurrences count before duplicate coalescing. |
| One effective reference in a dimension | Use that profile directly | Avoid blend-floor rounding and preserve single-profile behavior. |
| One requested reference has both weights zero | Inventory but do not analyze in a hand-authored job; reject as a saved catalog-set member | Another reference must keep both independent totals positive. |
| Duplicate references in weighted set | Coalesce by content hash and sum weights, then record | Result must equal explicitly coalesced request. |
| Incompatible cached profile or plan schema | `REJECT` and recompute profile when source is available | Never guess compatibility. |
| Match amount outside declared range | `REJECT` | Exact endpoints and interpolation space are documented/tested. |

## Render and limiter decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| Non-finite samples after any render operation | `REJECT`, abort artifacts | Stage/operation identifies first observed failure. |
| Raw float output exceeds full scale | `SUPPORTED` only for explicitly requested raw mode and reported prominently | No integer encoding of out-of-range data without explicit policy. |
| Normalized output has zero peak | `REJECT` earlier as silence; defensive render must remain finite | Never divide by zero. |
| Limited output exceeds configured sample-peak tolerance | `REJECT` artifact publication | True-peak compliance is not implied. |
| Limiter not needed | `SUPPORTED`; record bypass | Plan still states limiter mode/request. |
| Limiter parameters unstable at selected rate | `REJECT` during plan validation | Test minimum attack/hold sample counts. |
| Output duration/channel count differs unexpectedly | `REJECT` artifact publication | Expected convolution/resampling length tolerance is explicit. |
| Encoder clips or changes subtype | `REJECT` or `WARN` only for an explicitly lossy mode | Reopen and inspect required smoke outputs. |

## Artifact and preview decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| Output parent missing | Create only within allowed artifact root when policy permits; otherwise `REJECT` | Parent creation is explicit and race-safe. |
| Destination already exists | Apply requested `fail`, `version`, or `replace` policy | Default is `fail`; replacement is atomic and explicitly authorized. |
| Input and output resolve to same file | `REJECT` | Compare resolved identities, not strings alone. |
| Disk full, short write, encoder failure, or permission change | `REJECT`, abort temporary artifact | Final path must be absent or retain its prior complete content. |
| Preview track shorter than requested duration | Use whole track with bounded fade | Target/result frame ranges are identical. |
| Preview window computation yields no windows | Fall back to whole available valid range | Emit decision; never call `argmax` on empty input. |
| Optional preview fails | Follow request policy | It may produce `succeeded_with_warnings` only when marked optional before job start. |
| Artifact hash/reopen verification fails | `REJECT` commit | Preserve safe diagnostics and temporary evidence only under debug retention. |
| Cataloged source path is missing or changed | Mark location state; do not mutate the original content identity | `verify` rehashes; `relink` accepts only byte-identical content. |
| Selection reimport path/hash/size disagree | `REJECT` the entire import | No partial registration; use a verified current selection/export. |
| Complete catalog import conflicts with local identities | `REJECT` and roll back the import transaction | Existing catalog/run history remains intact. |
| Catalog archive requested | Hide the record and retain history; never delete audio | Restore re-verifies known locations. |
| Selected delivery root resolves to a file or is not absolute | `REJECT` before materialization | The persistent preference and per-job override use the same strict boundary. |
| Per-target run-named output subfolder already exists | `REJECT` without reuse or cleanup of that folder | Only paths proven created by the current preparation call may be removed on failure. |
| Master-library export destination already contains a computed filename | Fail only that copy and report it; never overwrite or mutate the audited source artifact | Successful siblings remain complete and fingerprint-verified; failed selections remain available for retry with another suffix/destination. |
| Master-library export suffix contains separators, controls, or platform-reserved filename characters | `REJECT` in the GUI before the request; sanitize again at the application boundary | Display the effective suffix in the result; destination ownership is still exclusive. |
| Discard requested for one completed mastered version | Verify source/run lineage and every mastered deliverable, then move only that version's mastered outputs to private quarantine and index a recovery tombstone | Preserve the original, references, manifest, events, previews, configuration, selection, and other evidence. Never implement version discard as content-ID track archival. |
| A mastered output path is shared by another active version or a source/reference | `REJECT` discard before moving any file | Content-derived track IDs and physical paths can be shared; version lifecycle is keyed by source plus run/artifact lineage. |
| Master-version restore destination is occupied or quarantine bytes changed | `REJECT` without overwrite or partial catalog transition | Restore requires every quarantined deliverable and its original path; retain tombstone/recovery evidence for diagnosis. |
| Catalog media is valid for mastering but unsupported by the browser codec | Disable/fail that audition with an actionable UI error; do not reject the mastering job | The local media route streams original bytes and never silently transcodes or loudness-matches. |

## Resource, concurrency, and lifecycle decisions

| Case | Required outcome | Notes and minimum test |
|---|---|---|
| Working-memory estimate exceeds budget | `REJECT` before allocation or queue until capacity is available | Batch coordinator records decision. |
| Runtime memory allocation fails | `REJECT` with resource code; cleanup | Do not broadly catch and relabel unrelated errors as OOM. |
| Job cancelled before render | `cancelled` terminal state | No final audio. |
| Job cancelled during a non-interruptible numerical call | Complete the bounded call, check token, then cancel | Document maximum cancellation latency from measured stages. |
| Event sink fails | Continue only if sink is optional and a fallback diagnostic path exists | Required audit sink failure fails the job before final commit. |
| Manifest commit fails | Job cannot be `succeeded` | Audio temporary artifacts are not published as final. |
| Concurrent jobs target same output | Exactly one succeeds under `fail`, or both get unique names under `version` | No interleaved bytes. |
| One target in a multi-target portal operation fails | Record/index its independent evidence when available, report aggregate partial/failed status, and continue later independent targets | Current portal policy is explicitly `continue-independent-targets`; no successful sibling is rolled back. |
| Process crashes mid-job | Recovery identifies stale transaction and applies retention policy | Never infer success from a partial audio file. |
| Unexpected internal exception | `REJECT` with stable internal-error wrapper and preserved cause for private debugging | Code 4201 may be recorded as upstream legacy mapping where applicable. |

## Warning promotion

Strict mode may promote selected warnings to rejection. The promoted set is
part of `JobPolicy` and the manifest. At minimum, service/batch operators can
promote lossy target, clipped target, limiter-detected target, mono input, and
guardrail-clamp warnings.

Warning suppression affects display only. A warning that influenced a plan or
policy remains in structured events and the manifest.

## Policy-change process

Changing an outcome class or a default threshold can change sound and safety.
Such a change requires:

1. a requirement/ADR review when the semantic change is durable;
2. old and new characterization results;
3. regression tests for both the triggering case and neighboring valid cases;
4. plan/manifest schema or algorithm-version impact analysis;
5. listening review when audio changes; and
6. status and operator-documentation updates.
