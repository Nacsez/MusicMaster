# Codebase and process survey record — July 2026

Status: **completed discovery snapshot**

This record documents how the initial conclusions and launchpad direction were
formed. It separates direct local observation, dated runtime observation,
creator/community intent, and design inference so later maintainers can refresh
the evidence without repeating the entire investigation.

Statements about what had or had not yet been implemented belong to this dated
snapshot. The current weighted engine, catalog, workbench, and launcher state
is maintained in the
[implementation status register](../status/implementation-status.md).

## Questions

1. What does the Matchering library actually do?
2. Which public controls and output modes exist?
3. What workflows did the creators and community envision?
4. How does the official web application use the library?
5. Which defects and production gaps should shape a private fork?
6. What process and architecture will let us evolve it without losing sound or
   diagnosability?

## Scope and dates

- Initial source and container survey: 2026-07-26.
- Documentation/scaffold synthesis: 2026-07-27.
- Local source: extracted Matchering 2.0.6 ZIP under the workspace.
- Runtime reference: an active official web-application container observed on
  2026-07-26.
- External intent references: upstream README/examples, official web-app
  source, selected upstream issues, and one community pull request.

This was not a formal security audit, legal opinion, listening test, or
performance benchmark.

## Evidence classes

| Class | Confidence | Use |
|---|---|---|
| Direct source observation | High for the exact extracted files | Baseline API/algorithm/config/dependency facts |
| Direct runtime observation | High for that dated container state | Integration/deployment facts, not future guarantee |
| Creator statement/documentation | Evidence of intent, not delivery | Candidate workflows and architecture |
| Community proposal | Inspiration only | Planned experiments, never upstream capability claim |
| Engineering inference | Must be tested | Risks, target architecture, guardrails |

## Workspace discovery

At initial inspection the workspace contained:

```text
matchering-master.zip
matchering-master/matchering-master/
```

It did not yet contain a root Git repository or private documentation/test
scaffold. The extracted project declared version 2.0.6 in both package metadata
and module metadata, GPL licensing, Python 3.8+, and five numerical/audio
dependencies with lower bounds only.

Representative read-only discovery commands:

```powershell
rg --files
Get-Content .\matchering-master\matchering-master\setup.py
Get-Content .\matchering-master\matchering-master\requirements.txt
Get-Content .\matchering-master\matchering-master\matchering\core.py
Get-Content .\matchering-master\matchering-master\matchering\stages.py
Get-Content .\matchering-master\matchering-master\matchering\defaults.py
Get-Content .\matchering-master\matchering-master\LOG_CODES.md
```

The detailed facts are frozen in
[the baseline record](../baseline/upstream-2.0.6.md).

## Source map

| Area | Evidence file | Finding |
|---|---|---|
| Public exports | `matchering/__init__.py` | `process`, config/result helpers, log, load/check |
| Orchestration | `matchering/core.py` | Whole job takes paths, writes files, returns nothing |
| Stages | `matchering/stages.py` | level → frequency → correction → final branches |
| Level analysis | `stage_helpers/match_levels.py` | piecewise Mid RMS and above-average loud-piece selection |
| Spectral matching | `stage_helpers/match_frequencies.py` | average STFT ratios, log interpolation, LOWESS, Mid/Side FIR |
| Limiter | `limiter/hyrax.py` | sample-domain envelope/brickwall limiting |
| Input | `loader.py`, `checker.py` | SoundFile, FFmpeg fallback, mono duplicate, resample, heuristics |
| Output | `results.py`, `saver.py` | SoundFile subtype selection and direct writes |
| Preview | `preview_creator.py` | loudest-result window with paired fades |
| Logging | `log/*`, `LOG_CODES.md` | global callbacks and numeric stage/warning/error codes |

## Algorithm reconstruction

The source-supported pipeline was reconstructed as:

```text
decode + validate + resample + mono duplicate
  → normalize reference
  → split target/reference into Mid and Side
  → select above-average-RMS Mid pieces
  → initial RMS level match
  → derive smoothed target/reference Mid and Side spectral ratios
  → window ratios into FIR filters
  → FFT convolution and Left/Right reconstruction
  → iterative RMS correction
  → raw / normalized / Hyrax-limited branches
  → encode outputs and paired preview
```

Important terminology corrections:

- loud-section RMS is not LUFS;
- sample peak is not oversampled true peak;
- separate Mid/Side filtering is not explicit stereo-width measurement; and
- deterministic numerical DSP is not an AI/ML model.

## Capability inventory

Directly observed upstream capabilities:

- one target and one reference per `process()` call;
- mono and stereo input, with mono duplicated;
- resampling to configurable internal rate (only 44.1 kHz described as properly
  tested in source);
- level and Mid/Side frequency-response matching;
- iterative level correction;
- optional Hyrax-limited, normalized non-limited, and raw non-limited results;
- SoundFile-supported output/subtype pairs and PCM16/PCM24 shortcuts;
- aligned target/result previews; and
- separate info/warning/debug callbacks with stable legacy codes.

Not observed upstream:

- staged analyze/plan/render API;
- returned audio, measurements, plan, filter, or manifest;
- multiple weighted references;
- match amount;
- reference cache;
- batch coordinator;
- LUFS or true-peak conformance;
- explicit dither/metadata preservation;
- streaming/real-time operation;
- bounded spectral boost/filter energy; or
- automated tests.

## Official application runtime observation

On 2026-07-26, the active `mgw-app` container was inspected read-only. It
showed Matchering 2.0.6 on Python 3.10.8 and sampled DSP files matching the
local extraction. The application used Django, Redis, one RQ worker, SQLite,
and a data volume. It demonstrated:

- queue/status integration through numeric stage codes;
- PCM16/PCM24 WAV masters at 44.1 kHz stereo;
- paired 30-second FLAC previews;
- retaining one reference for multiple targets; and
- retaining one target to audition multiple references.

One historical job log showed approximately 231.6 seconds of audio completing
about 17 seconds after queueing. It was recorded only as a sanity observation,
not a reproducible benchmark.

The observed container also had development/server risks: no application
authentication or TLS, debug/development serving, root execution,
all-interface binding, no resource limits, and cleanup dependent on later
session creation. This informed the decision to reuse workflow ideas, not the
deployment itself.

The Docker endpoint was unavailable from the documentation-authoring session
on 2026-07-27, so the runtime facts above remain a dated snapshot rather than a
second independently repeated observation.

## Creator and community intent record

| Idea | Evidence/attribution | Interpretation |
|---|---|---|
| Python library embedded in other applications | Upstream README describes connecting it to the Python world | Central intended implementation |
| CLI | Upstream README links separate CLI projects | Shipped externally |
| Containerized/local web application | Upstream README and official app | Shipped workflow reference |
| Album consistency and experimentation | Upstream README use cases | Explicit intended user workflows |
| Desktop and ComfyUI integration | Upstream README links UVR5 and ComfyUI node | Shipped third-party integrations |
| AI-mastering/startup backend | Upstream README recommends Docker to startups | Intended, but surveyed deployment needs hardening |
| DAW/VST hybrid through raw output/external limiter | [Upstream issue #15](https://github.com/sergree/matchering/issues/15#issuecomment-587424311) and advanced-results example | Creator-endorsed integration direction |
| Native GUI/Flutter wrapper and Rust rewrite discussion | [Upstream issue #32](https://github.com/sergree/matchering/issues/32#issuecomment-857523655) | Feasible wrapper discussed; Rust prototype not delivered |
| Multi-stem workflow | [Upstream issue #66](https://github.com/sergree/matchering/issues/66#issuecomment-2439977587) | Saved future idea, not shipped roadmap commitment |
| Weighted multiple references | [Community pull request #82](https://github.com/sergree/matchering/pull/82) | Promising community proposal, not merged baseline |

No planned private feature is described as shipped by the original creators
unless the evidence supports that statement.

## Edge and production findings

Source reading and a small synthetic smoke investigation identified:

- exact silence can fail in logarithmic debug formatting;
- fractional preview values can reach NumPy stride shapes as floats;
- no pre-DSP non-finite sample policy;
- unbounded reference/target spectral ratio above the denominator floor;
- global mutable log callbacks;
- assertion-based configuration validation;
- multiple whole-track arrays and no resource estimator;
- non-transactional-looking audio saves;
- FFmpeg fallback without visible timeout/finally cleanup;
- no test suite or locked numerical environment; and
- deployment hazards in the official web container.

These became named requirements and policy cases rather than informal TODOs:
[requirements](../product/requirements.md),
[edge-case policy](../policies/edge-cases.md), and
[test strategy](../quality/test-strategy.md).

## Architecture/process synthesis

The evidence led to four accepted decisions:

1. preserve GPL-compatible provenance while remaining private;
2. characterize before DSP refactoring;
3. separate analyze, plan, and render; and
4. inject per-job structured events.

The first launchpad scaffold then introduced:

- project metadata and constrained requirements;
- PowerShell bootstrap/quality/test/smoke wrappers;
- deterministic generated WAV fixtures;
- typed job configuration and expanded example profiles;
- config schema and truthful engine capability declarations;
- stable errors, audio preflight probes, validation, manifests, and per-job
  events;
- a serialized adapter around pinned Matchering 2.0.6;
- a deliberately non-runnable native-engine placeholder;
- synchronous audited service orchestration; and
- a diagnostic/validation/run CLI.

These are not all verified features. The
[implementation status register](../status/implementation-status.md) records
which are implemented, scaffolded, upstream-only, or planned.

## Validation performed during scaffold creation

Reported local checks on Windows with Python 3.11 and PowerShell 5 included:

- TOML, Python, and PowerShell syntax checks;
- bootstrap environment creation/reuse without dependency install;
- deterministic fixture creation, idempotence, read-only verification, and
  workspace-escape refusal;
- independent SHA-256 and WAV metadata checks for all ten generated WAV files;
- fixture-only smoke wrapper;
- focused success/failure round-trips for events, errors, manifests, and doctor
  models; and
- 25 standard-library regression/smoke tests covering configuration, audio
  preflight, capability truthfulness, events/manifests, dry-run service
  orchestration, and CLI behavior.

Not yet established at the survey close:

- full package/DSP smoke with installed dependencies;
- end-to-end CLI render;
- Ruff/mypy results in an installed development environment;
- characterization/golden corpus;
- code coverage;
- supported-platform matrix; or
- production/private local service deployment.

This distinction is essential: syntax/manual checks support `IMPLEMENTED` or
`SCAFFOLDED`, not `VERIFIED` product requirements.

## Limitations

- Source history before the ZIP snapshot was not embedded in the initial
  workspace.
- Runtime-container observations may change and were not captured as an
  automated evidence bundle.
- The quick synthetic smoke was not a listening evaluation.
- External issue/PR statements can be edited or superseded.
- License summary is not legal advice.
- Performance and memory behavior were not benchmarked systematically.

## Refresh procedure

When updating this survey:

1. never overwrite this dated record; add a new dated survey or appendix;
2. hash the current ZIP/extracted source and record Git/dependency identity;
3. rerun static source map and public API/config extraction;
4. regenerate and execute the full characterization matrix;
5. inspect the exact container/image digest with read-only commands;
6. capture configuration, user, ports, mounts, limits, server mode, queue,
   retention, and installed versions;
7. review upstream releases/issues/PRs and distinguish creator/community;
8. compare new findings to requirements, ADRs, policies, and roadmap;
9. update the status register with evidence dates; and
10. preserve raw machine-readable results under a private, bounded evidence
    location.
