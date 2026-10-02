# Security and privacy

Status: **normative private-use policy; initial localhost portal controls implemented, full gate pending**

The workbench processes valuable, often unreleased audio. Local/private
processing remains the default while Windows executable and GitHub sharing
preparation is active. All media, paths, serialized plans, caches, and job metadata
are untrusted at their boundaries.

## Default operating posture

- Core analysis/planning/rendering performs no network calls.
- File processing is local and limited to configured input/artifact/temp roots.
- The GUI adapter binds only to `127.0.0.1` on a per-launch port and requires a
  fresh session token; it is not authorized for LAN, tunnel, proxy, container
  publication or access by other users over a network. Sharing the executable
  gives each recipient their own local application and per-user workspace.
- Inputs, temporary conversions, outputs, profiles, manifests, events, and
  debug artifacts have explicit retention.
- The local catalog references source audio in place; catalog actions never
  copy or delete source songs.
- Complete catalog/selection export is explicit and produces a private
  document containing resolved paths, labels, sizes, and full hashes.
- Reference reuse and long-term caching are opt-in and visible in the job
  policy.
- Telemetry, crash uploads, remote fonts/assets, and update checks are off
  unless separately designed and authorized.
- The GUI ships repository-owned HTML, CSS, and JavaScript; it has no Electron
  runtime, CDN, or external frontend asset.
- Structured job logs contain no raw audio and should redact paths/user labels
  by default. The current portal-log exception is documented under
  [Redaction](#redaction).

## Protected assets

- unreleased target and reference audio;
- derived masters and previews;
- reusable reference profiles, spectra, and filter plans;
- file paths, track titles, hashes, and workflow history;
- the SQLite catalog, selection JSON, named reference sets, location states,
  run/artifact index, and catalog exports;
- job manifests, events, tracebacks, and performance data;
- service credentials, API keys, cookies, queue/database secrets; and
- private source, design decisions, test media, and release plans.

Content hashes are sensitive identifiers: they can reveal that two jobs used
the same private file. Store full hashes only where reproducibility requires
them and avoid displaying/exporting them by default.

## Threat model

### In scope

- malformed or adversarial audio exploiting decoders or resource use;
- path traversal, symlink/junction escape, output overwrite, and temporary-file
  races;
- command injection or uncontrolled FFmpeg behavior;
- denial of service through duration, channels, rate, decoded size, memory,
  concurrency, output volume, or log volume;
- accidental disclosure through logs, manifests, debug artifacts, caches, and
  previews;
- one job reading another job's files or events;
- compromised dependencies and unpinned numerical/native libraries;
- stale partial artifacts after crash/cancellation;
- accidental network exposure of a development web server; and
- token/referrer/browser-history disclosure from the local portal;
- a malicious local page attempting DNS-rebinding, cross-origin, framing, or
  forged API requests; and
- distribution of incomplete or license-incompatible source/binaries.

### Outside the desktop milestone: hosted network/multi-user operation

- hostile internet-scale traffic and account takeover;
- tenant isolation and billing;
- public abuse prevention, malware scanning, and legal takedown handling;
- distributed object-store/queue security;
- high-availability disaster recovery; and
- formal compliance certification.

Being out of scope means "do not expose the system to it," not "the risk is
accepted."

## Trust boundaries

```text
untrusted file/array/request
  → boundary validation and budgets
    → canonical private audio
      → analysis/plan/render core
        → transactional artifact writer
          → allowed private artifact root
```

Serialized profiles/plans/manifests re-entering the process cross the same
untrusted boundary. A valid JSON document is not necessarily a compatible or
safe plan.

The implemented GUI adds this boundary:

```text
local browser + native Windows picker
  → loopback/Host/origin/session-token/JSON validation
    → GUI-independent PortalApplication
      → catalog and mastering services

browser native audio element
  → loopback/Host/origin/media-cookie/catalog-ID validation
    → preferred catalog media location
```

The browser and another same-user process are not a strong isolation boundary.
Possession of the per-launch URL/token grants local portal capability for that
session.

## File and path controls

- Resolve and validate existing input and the explicitly selected absolute
  delivery root. The portal may use an operator-selected root outside its
  private workspace, but it creates one exclusive run-named child per target
  and never treats an existing child as owned.
- Reject traversal and links/reparse points that escape the root.
- Compare resolved file identities so input cannot also be output.
- Generate opaque server-side artifact names; treat original filenames only as
  redacted labels.
- Default collision policy is `fail`. Versioning and replacement are explicit.
- Create temporary files with exclusive, restrictive permissions and
  unpredictable names under a job-specific directory.
- Write output to a temporary sibling or transaction area, flush/close, reopen
  and verify, then atomically publish when supported.
- On failure/cancellation, clean job temporary material. An independent sweep
  handles crash leftovers.
- Never construct a shell command from media paths.

Windows junction/reparse behavior and POSIX symlink behavior both need tests on
their supported platforms.

## Media decoding

SoundFile/libsndfile is the preferred supported decoder boundary. Optional
FFmpeg fallback:

- is capability-detected and version-recorded;
- receives an argument array, never a shell string;
- runs with a timeout and constrained working directory/environment;
- has decoded-byte, frame, channel, rate, and duration budgets;
- writes only to its job-specific temporary area;
- captures bounded stderr in a private diagnostic artifact;
- cleans up in a guaranteed finalization path; and
- returns a stable sanitized domain error.

If secure process isolation/resource limiting is unavailable on a platform,
fallback decoding remains disabled there until the limitation is documented
and accepted for trusted local use.

An external limiter follows the same process boundary and additionally uses an
administrator-owned allowlist/template. A job configuration cannot supply an
arbitrary executable or free-form shell command. The adapter hashes and records
the approved tool, bounds stdout/stderr, measures input/output, and commits the
result transactionally.

## Resource controls

The job policy sets:

- maximum compressed/input bytes;
- maximum decoded frames, duration, rate, and channels;
- working-memory estimate and process/batch concurrency;
- maximum output/previews and encoded bytes;
- per-stage/job wall-clock budgets where safely enforceable;
- event/debug artifact size and rate;
- cache quota and item age; and
- temporary/final retention.

Check metadata before allocation, then enforce actual decoded/allocated values.
Do not trust container headers alone. Resource violations use distinct codes and
must not be reported as generic decode failures.

## Isolation and concurrency

- All mutable job state is per-job.
- Job directories are opaque and not derived from user names.
- Adapters authorize artifact/status access by job ownership when more than one
  user can exist.
- Caches are content-addressed but access-controlled; a cache hit must not
  reveal another job's filename or metadata.
- Shared stores use atomic operations/locks and explicit collision behavior.
- Workers should run as an unprivileged identity with only required roots.
- Containers use read-only application layers, writable job volumes, dropped
  capabilities, memory/CPU/process limits, health checks, and pinned images
  before any non-local deployment.

The current portal serializes mastering through one background worker and
rejects another submission while active. HTTP request handling may be
concurrent, but it does not make either engine concurrent. The browser cannot
cancel the worker, and shutdown is refused until the worker is idle.

The surveyed official container's root/development/all-interface posture is not
an acceptable deployment configuration.

## Network and service adapters

Core mastering remains network-independent. The GUI intentionally opens one
local HTTP listener with these implemented constraints:

- constructor-enforced `127.0.0.1` binding and a random available default port;
- a fresh high-entropy token for the initial page and `X-MMT-Token` on APIs;
- constant-time token comparison;
- loopback client plus exact Host and same-origin Origin/Referer validation;
- no CORS support and explicit OPTIONS refusal;
- strict JSON objects, duplicate/non-finite rejection, and a 1 MiB body limit;
- packaged same-origin assets only, no Electron/CDN/external frontend runtime
  and no arbitrary filesystem serving;
- CSP with a per-response bootstrap nonce, `connect-src 'self'`, and no
  objects, frames, foreign forms, or base-URI override;
- no-store/referrer/frame/MIME/permissions response headers;
- a separate per-process `HttpOnly`, `SameSite=Strict`, `Path=/` page cookie
  accepted directly only at the index route, to refresh after the interface
  removes the launch token from its URL; its name includes the server port;
- a separate random `HttpOnly`, `SameSite=Strict` cookie scoped only to
  `/media/tracks/`, because native browser audio elements cannot send the
  private API header;
- a media handler that accepts only one catalog track ID, resolves only the
  preferred catalog location, rejects query/nested paths, checks regular-file
  size/modification time, and supports bounded-chunk full or single-byte-range
  responses without a transcoder;
- no browser audio upload or server-side copy into a managed media store; and
- fixed PowerShell/WinForms picker scripts whose selected paths are data, plus
  native Windows Shell file selection and directory opening.

Page-cookie refresh returns the authenticated HTML containing the API bootstrap
token and establishes the playback cookie, so possession of this page
credential grants the same local session capability indirectly. The page
cookie itself does not replace the API header or media-cookie checks; missing,
invalid, or duplicated refresh credentials are refused. An explicitly invalid
launch token does not fall back to cookie authorization.

Port-qualified page-cookie names avoid collisions between simultaneous
workspace servers. They do not create browser-enforced port isolation:
cookies are shared across ports on the same host, as specified in
[RFC 6265 section 8.5](https://www.rfc-editor.org/rfc/rfc6265.html#section-8.5).
The existing same-user desktop trust boundary therefore remains applicable.

Normal startup output and the portal log contain only the local origin, not the
token-bearing launch URL. The launcher closes its diagnostic transcript before
handing off to the portal. If `--no-browser` is explicit or automatic browser
opening fails, the private URL is therefore shown only in the live console.
Portal HTTP access logging replaces the API, page, and media secrets with
`[REDACTED]` and strips request query values.

This is a private desktop adapter, not a production web deployment. It has no
TLS, account authentication, persistent account sessions, multi-user
ownership, public rate limiting, reverse-proxy contract, or tenant isolation.
The page and media cookies are per-process refresh/playback capabilities,
and APIs still require their session header. A same-user process that obtains
a relevant credential is inside the current trust boundary. Internet,
LAN, tunnel, proxy, or container-port exposure requires a new adapter, revised
threat model, and security review; changing the bind address is intentionally
unsupported.

## Privacy and retention

### Data classes

| Class | Examples | Default |
|---|---|---|
| Source media | target, reference, temporary decode | Do not copy when local paths can be read; delete temp at terminal state |
| Derived media | master, raw output, previews | Retain in requested private output root |
| Reusable profile | reference analysis/spectrum | Off unless explicitly enabled; quota/expiry required |
| Catalog/index | SQLite track/location/set/run/artifact data and JSON exports | Retain only in ignored private workspace; export explicitly; archive does not delete source audio |
| Job record | manifest, normal events | Retain for configured private diagnostic window |
| Debug evidence | traceback, bounded decoder stderr, trace events | Off by default; short explicit retention |
| Portal session capability | token-bearing launch URL, API header, page-refresh cookie, and media-path cookie | Valid only for the current server process; never manifest/catalog/export/portal-log content; cookies are `HttpOnly` and `SameSite=Strict`, the page cookie has a port-qualified name and the media cookie is scoped to the media path; launcher transcript closes before handoff and fallback URL is live-console-only; close the portal to invalidate |
| Secrets | service/database credentials | Never in manifest/logs; use secret provider/environment boundary |

Cleanup runs on success, failure, and cancellation plus an independent sweep.
A retention failure emits an alert/event and remains visible until remediated.

### Redaction

Normal events and reports use opaque IDs and role labels. Path-root aliases
replace absolute roots. User labels are optional. Full hashes and original
names are excluded from console output and remote metrics.

When exporting a diagnostic bundle, generate an inventory preview so the
operator can see what will leave the machine. Bundle export is a separate
explicit action.

Current implementation boundary: the local GUI intentionally displays selected
absolute paths, hashes, raw manifests, and private failure detail to its
operator, and portal logs may contain paths/exception text useful for root-cause
analysis. Comprehensive redaction and bounded retention are not yet complete.
Treat browser history, screenshots, `portal-*.log`, launcher transcripts, and
operation tracebacks as private artifacts; none is ready for public issue
attachments.

The three-workspace redesign does not widen the service boundary: searchable
input/reference selection resolves existing catalog IDs through authenticated
APIs, and direct A/B selection, assignment swap, playhead preservation, and
Quick Play all reuse the catalog-ID-only media route. Quick Play is preview
state; it does not export, rewrite, discard, or replace either selected A/B
assignment.

The Activity workspace's **System** utility confines its **Session logs** viewer
to plain `.log` files in the private workspace and returns at most a 256 KiB
tail. HTTP request tokens are replaced with `[REDACTED]`.
`Invoke-PortalSmoke.ps1` accepts an explicit workspace/utility and viewport,
waits for initialized UI state, and scrubs the launch token from its retained
ready document, rendered DOM, stdout, stderr, and browser log. These focused
controls do not redact private paths or exception content.

## Secrets

- No secrets in repository files, command lines, manifests, sample configs, or
  container images.
- Validate that required secrets exist without logging their values.
- Redact common token/key/connection-string patterns in exception text.
- Rotate a secret if it appears in any retained event or artifact.
- Local-only core and CLI operation should require no secret.
- The portal's generated token is an ephemeral local capability. Do not share
  its URL, preserve it in screenshots, or paste it into issue reports.

## Dependency and supply-chain controls

- Lock Python and native dependency versions for each supported environment.
- Hash-check downloaded artifacts where tooling supports it.
- Record license, source, version, and reason for each direct dependency.
- Generate an SBOM for milestone/release artifacts.
- Scan vulnerabilities, then review exploitability rather than blindly
  suppressing findings.
- Pin container base images by digest for reproducible milestones.
- Treat libsndfile and FFmpeg updates as security and numerical-behavior changes;
  run decode and full regression suites.
- Do not execute code from untrusted plan/manifest formats; use data-only
  schemas.

## Incident and diagnostic procedure

For suspected disclosure or compromise:

1. stop affected adapters/workers without deleting evidence;
2. record time, version, job/artifact IDs, and observed scope;
3. restrict access to affected roots and rotate exposed secrets;
4. preserve permission-restricted manifests/events/checksums, not unnecessary
   source audio copies;
5. identify the trust-boundary failure and affected retention windows;
6. add a reproducible safe test and fix;
7. run security plus regression gates; and
8. document cleanup and any changed threat assumptions.

## Security completion evidence

The private gate requires:

- traversal/symlink/collision tests on supported platforms;
- malformed and oversized media tests;
- network-denied core smoke test;
- loopback-only bind, token, Host/origin, CORS refusal, CSP/header, strict body,
  and native-dialog command-injection tests;
- a supported Chromium bootstrap/shutdown smoke whose retained text artifacts
  contain no launch token;
- FFmpeg timeout/cleanup tests if fallback is enabled;
- concurrent-job isolation and cache privacy tests;
- secret/path redaction tests;
- success/failure/cancellation retention tests;
- atomic artifact fault injection;
- locked dependency and vulnerability/license inventory; and
- documented permission model for all writable roots.
