# Upstream Matchering 2.0.6 baseline

Status: **observed baseline facts, not private implementation completion**

This record freezes what the workspace started from. It is evidence for
characterization and migration, not an endorsement of every behavior.

## Provenance in this workspace

| Item | Observed value |
|---|---|
| Extracted source | [`matchering-master/matchering-master/`](https://github.com/sergree/matchering/tree/914c9e58939746db1669212645907e7316389d08) |
| Original archive | [`matchering-master.zip`](upstream-provenance.json) |
| Package | `matchering` |
| Declared version | `2.0.6` |
| Declared Python | `>=3.8`; classifiers list 3.8–3.10 |
| Declared license | GPLv3; source headers say GPL version 3 or later |
| Upstream URL | Recorded in [`setup.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/setup.py) |
| Workspace state at survey | ZIP extraction; no root Git history was present |

Version evidence appears in both
[`setup.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/setup.py) and
[`matchering/__init__.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/__init__.py).
The complete upstream license text is
[`LICENSE`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/LICENSE).

On 2026-10-02, the ZIP comment identified the exact revision above and all 80
extracted files matched their archived bytes. Their hashes and the original ZIP
SHA-256 are recorded in [upstream-provenance.json](upstream-provenance.json).
The original archive/extraction are preserved locally and ignored for GitHub
preparation; the source links in this document resolve to that pinned revision.

## What is actually shipped upstream

The root Python API exports:

- `process(target, reference, results, config, preview_target, preview_result)`;
- `Config` and nested `LimiterConfig` behavior;
- `Result`, `pcm16`, and `pcm24`;
- `log(...)` for global callbacks;
- lower-level `load(...)`, `check(...)`, and version metadata.

See [`matchering/__init__.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/__init__.py)
and [`core.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/core.py).

`process()` accepts file paths, writes requested files, and returns `None`.
In-memory staged analysis, serializable plans, returned measurements, job
manifests, structured per-job events, batch coordination, and reference caches
are not present.

### Processing path

The observed source path is:

1. load target with SoundFile, falling back to an FFmpeg subprocess for
   unrecognized formats;
2. validate length/channels, duplicate mono, resample to the internal rate, and
   warn heuristically about clipped/limited target audio;
3. repeat loading/validation for the reference;
4. reject numerically equal target/reference unless `allow_equality`;
5. normalize the reference when it is below the peak threshold;
6. convert both inputs from Left/Right to Mid/Side;
7. split them into pieces and select pieces whose Mid RMS is at or above the
   average piece RMS;
8. match target loud-section RMS to reference loud-section RMS;
9. average STFT magnitudes over loud pieces separately for Mid and Side;
10. divide reference spectra by floored target spectra, smooth the ratio using
    logarithmic interpolation and LOWESS, and form windowed FIR filters;
11. FFT-convolve target Mid and Side with their respective FIRs and reconstruct
    Left/Right;
12. perform configured iterative RMS correction using clipped result Mid;
13. optionally create raw non-limited, peak-normalized non-limited, and Hyrax
    limited branches;
14. encode requested results; and
15. optionally select the loudest result window and write paired target/result
    previews.

Primary evidence:

- orchestration:
  [`core.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/core.py);
- stages:
  [`stages.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/stages.py);
- loudness:
  [`match_levels.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/stage_helpers/match_levels.py);
- spectra and FIR:
  [`match_frequencies.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/stage_helpers/match_frequencies.py);
- limiter:
  [`hyrax.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/limiter/hyrax.py);
- previews:
  [`preview_creator.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/preview_creator.py).

### Claim boundaries

- "Loudness" is loud-section RMS, not LUFS/EBU R128.
- Peak handling uses sample values; no oversampled true-peak measurement is
  implemented.
- Stereo matching is separate Mid/Side static filtering; there is no explicit
  stereo-width metric or stereo synthesis.
- Mono is duplicated to two equal channels, so its Side signal remains zero.
- The engine is whole-file/offline and holds multiple large arrays; it is not a
  streaming or real-time processor.
- The algorithm is deterministic numerical DSP, not machine learning.

## Configuration surface

The following constructor options are present in
[`defaults.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/defaults.py).
Several values stated in seconds are immediately converted to samples and then
stored under the same attribute name.

| Setting | Upstream default | Meaning/observation |
|---|---:|---|
| `internal_sample_rate` | `44100` | Non-44.1 kHz logs an untested warning only through debug output. |
| `max_length` | `900` s | Per-input maximum duration. |
| `max_piece_size` | `15` s | Maximum analysis piece size; stored as samples. |
| `threshold` | `(32767-61)/32768` | Peak/limiter threshold, approximately 0.9981. |
| `min_value` | `1e-6` | Numerical floor used in normalization, RMS, and spectral division. |
| `fft_size` | `4096` | Power-of-two STFT/FIR length. |
| `lin_log_oversampling` | `4` | Density of the logarithmic smoothing grid. |
| `rms_correction_steps` | `4` | Number of post-filter correction passes. |
| `clipping_samples_threshold` | `8` | Heuristic repeated-peak threshold. |
| `limited_samples_threshold` | `128` | Larger repeated-peak threshold used to infer limiting. |
| `allow_equality` | `False` | Allows equal target/reference when true. |
| `lowess_frac` | `0.0375` | LOWESS smoothing fraction. |
| `lowess_it` | `0` | LOWESS robustifying iterations. |
| `lowess_delta` | `0.001` | LOWESS delta. |
| `preview_size` | `30` s | Maximum preview duration; stored as samples. |
| `preview_analysis_step` | `5` s | Preview scan stride; stored as samples. |
| `preview_fade_size` | `1` s | Fade duration; stored as samples. |
| `preview_fade_coefficient` | `8` | Caps fade relative to selected preview length. |
| `temp_folder` | `None` | FFmpeg conversion location; inferred from result paths otherwise. |
| `limiter` | shared default `LimiterConfig()` | Limiter parameter object; mutable-default design should not be copied. |

`LimiterConfig` exposes:

| Setting | Default |
|---|---:|
| `attack` | `1` ms |
| `hold` | `1` ms |
| `release` | `3000` ms |
| `attack_filter_coefficient` | `-2` |
| `hold_filter_order` | `1` |
| `hold_filter_coefficient` | `7` |
| `release_filter_order` | `1` |
| `release_filter_coefficient` | `800` |

Validation uses `assert` extensively. Assertions disappear under `python -O`,
so the private implementation must use typed, stable validation errors.

## Result modes

[`results.py`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/matchering/results.py)
constructs a `Result` with:

- path;
- SoundFile subtype;
- `use_limiter`; and
- `normalize` for the non-limited path.

The effective modes are:

| Mode | Flags | Behavior |
|---|---|---|
| Limited | `use_limiter=True` | Hyrax limiter, then reference-derived final amplitude coefficient |
| Normalized, no limiter | `use_limiter=False`, `normalize=True` | Peak-normalized non-limited array |
| Raw float-capable, no limiter | `use_limiter=False`, `normalize=False` | Unnormalized matched array; subtype is caller-selected |

`pcm16()` and `pcm24()` are convenience constructors. Other combinations are
available if SoundFile reports that the extension/subtype pair is supported.
No explicit dither or metadata-preservation layer is visible in the source.

## Logging baseline

Upstream provides global mutable callbacks for warning, info, and debug, plus
an option to prefix info/warning text with four-digit codes. The catalog is in
[`LOG_CODES.md`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/LOG_CODES.md).

- 2xxx: information/stage states;
- 3xxx: target warnings;
- 4xxx: processing errors.

The stable codes are useful compatibility evidence. Global handlers are unsafe
for independent concurrent jobs and carry unstructured strings, so the target
architecture preserves legacy code mapping inside per-job structured events
rather than preserving global state.

## Dependency baseline

[`requirements.txt`](https://github.com/sergree/matchering/blob/914c9e58939746db1669212645907e7316389d08/requirements.txt)
has only lower bounds and no lock:

- NumPy `>=1.23.4`;
- SciPy `>=1.9.2`;
- SoundFile `>=0.11.0`;
- Resampy `>=0.4.2`;
- Statsmodels `>=0.13.2`.

`libsndfile` is a native runtime dependency through SoundFile. FFmpeg is
optional for fallback decoding. Version drift can change numerical behavior,
so characterization records must use a lock and runtime inventory.

## Known risks and defects to capture

These are baseline findings, not yet fixes:

- no upstream automated test suite was found;
- silence can reach logarithmic diagnostic formatting with zero and fail even
  when a no-op logger is configured;
- fractional preview durations/steps become floating-point NumPy stride
  dimensions and can fail;
- no explicit NaN/Inf validation precedes DSP;
- spectral ratios have a floor on the target denominator but no explicit
  maximum boost or FIR-energy bound;
- `process()` returns no plan, measurements, arrays, or report;
- multiple full-length arrays increase memory pressure;
- output writes are not visibly transactional;
- FFmpeg fallback has no explicit timeout/resource limit and its temporary file
  cleanup is not protected by a `finally` block;
- global log handlers can cross-talk between concurrent jobs;
- non-44.1 kHz operation is explicitly described by the source as not properly
  tested;
- mono duplication cannot create Side information; and
- format support depends on local libsndfile/FFmpeg capabilities.

Each becomes a fixture or policy test in
[the test strategy](../quality/test-strategy.md).

## Official web application observation

The official application is an integration reference, not source to deploy
unchanged. During the 2026-07-26 survey, the active container named `mgw-app`
was observed with:

- Matchering 2.0.6 installed;
- Python 3.10.8;
- sampled key DSP files matching the extracted source;
- a Django application, Redis queue, one RQ worker, and SQLite/data-volume
  persistence;
- output workflows for 44.1 kHz stereo PCM16/PCM24 WAV and paired 30-second
  FLAC previews;
- once-per-second browser polling of numeric stage codes; and
- workflow affordances to retain either the reference for many targets or the
  target for trying several references.

One existing log sample showed a roughly 231.6-second job completing in about
17 seconds after queueing. It is an anecdote, not a benchmark.

The same inspection found development-oriented deployment characteristics:
no application authentication/TLS, Django debug/development serving, root
container execution, all-interface binding, no declared resource limits, and
cleanup coupled to later session creation. Those observations make the app a
valuable workflow study but not a production/private-service base image.

Container facts are a dated snapshot. Re-run and record runtime inspection
before relying on them; the container was not reachable from the documentation
authoring session on 2026-07-27.

## Creator-envisioned uses

The upstream README and examples present:

- a Python library embedded in other applications;
- a separate CLI;
- a containerized browser application;
- album consistency from a shared reference;
- experimentation with target/reference choices;
- desktop integration (UVR5);
- a ComfyUI node; and
- AI-mastering/startup backends.

The raw non-limited result supports a DAW/external-limiter workflow. Broader
ideas found during the survey—weighted references, native wrappers, or stem
workflows—remain inspirations rather than shipped upstream capabilities. The
private requirements do not claim upstream authorship or completion for them.

## Baseline preservation rules

- Do not edit the only extracted copy without first preserving hashes and
  provenance.
- Do not label an upstream behavior `VERIFIED` until the private acceptance
  criteria pass.
- Keep expected upstream failures visible; do not delete fixtures after fixing
  them privately.
- Changes in dependency versions are part of the behavioral baseline.
- Preserve GPL notices and license terms in copied or modified source.
