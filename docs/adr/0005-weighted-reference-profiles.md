# ADR-0005: Combine references as weighted analysis profiles

- Status: Accepted
- Date: 2026-07-27
- Decision owners: application and DSP maintainers
- Requirements: CAP-003 through CAP-007, PIPE-002, PIPE-007, ROB-002,
  ROB-005, OBS-001

## Context

A user may want either one reference track or several tracks that collectively
describe the intended tonal balance and level. Unrelated songs cannot safely be
averaged as waveforms: their timing, phase, arrangement, and duration differ,
so sample-by-sample mixing creates cancellation and a result that is not a
meaningful mastering target. Rendering once per reference and averaging the
outputs is also unsuitable because normalization and limiting are nonlinear.

Matchering 2.0.6 analyzes one reference. It peak-normalizes that reference,
selects representative loud pieces, derives loud-section RMS and Mid/Side
magnitude spectra, calculates a spectral ratio, and then renders the target.
The existing compatibility adapter has verified one-reference output and must
not change silently.

## Decision

The exact Matchering adapter remains the default for one-reference
compatibility jobs. A separately identified weighted-profile engine implements
the parity-capable multi-reference path.

Each selected reference is loaded, checked, resampled, peak-normalized, and
analyzed independently. Duplicate content is identified by SHA-256, coalesced
for analysis, and still retained in the requested-source inventory. Effective
profiles are sorted by content identity before numerical reduction.

Level and frequency weights are independent, finite, non-negative, and
normalized separately. The combined loud-section RMS is the weighted geometric
mean:

```text
R = exp(sum(w_level[i] * log(max(R[i], epsilon))))
```

The peak-normalization restoration coefficient uses the same level rule.
Mid and Side magnitude spectra are combined independently in the logarithmic
amplitude domain:

```text
P[channel, bin] =
    exp(sum(w_frequency[i] * log(max(P[i, channel, bin], epsilon))))
```

This is equivalent to an arithmetic mean in decibels and prevents one
reference from dominating merely because its linear magnitude is larger. The
combined spectrum is divided by the target spectrum; smoothing and FIR
construction occur once; the target is convolved and level-corrected once.

References with zero weight in one dimension do not affect that dimension. A
reference with both weights zero remains inventoried but is not analyzed. When
only one effective profile contributes to a dimension, that profile is
returned directly so the blend layer adds no flooring or rounding. The first
implementation accepts at most 32 selected references and processes them
sequentially.

Every run records:

- algorithm and profile-schema versions;
- every requested path, label, content hash, and raw/normalized weight;
- duplicate and zero-weight disposition;
- the stable effective profile identity;
- combination domain and epsilon;
- combined measurements; and
- render/output identity.

## Consequences

### Positive

- One and many references have explicit, reproducible semantics.
- Reference order cannot change the effective plan.
- Duplicate files cannot receive accidental extra analysis work.
- Level and tonal emphasis can be adjusted independently.
- Existing exact one-reference compatibility output remains unchanged.
- The approach can evolve into reusable cached profiles and explicit plans.

### Costs and constraints

- Multi-reference output is a new algorithm, not an upstream compatibility
  claim.
- Analysis cost and memory grow with reference count until profile caching is
  implemented.
- Log-domain floors require explicit versioning and mono/zero-Side tests.
- A weighted average is a technical target, not a substitute for listening
  approval or genre-aware reference selection.
- Match amount, LUFS, true peak, dither, and other advanced native controls
  remain separate capability milestones.

## Alternatives considered

### Mix the reference waveforms

Rejected. Unaligned musical material phase-cancels and creates a synthetic
waveform whose level and spectrum depend on arbitrary timing.

### Concatenate reference tracks into one file

Rejected as the canonical rule. Track duration and loud-section selection would
implicitly determine weight, separate level/frequency weights would be
impossible, and profile identity would be obscure.

### Render once per reference and average the masters

Rejected. Limiting and normalization are nonlinear, so output averaging is not
equivalent to averaging a mastering target and may violate peak guarantees.

### Change the existing upstream adapter

Rejected. It would change a verified compatibility promise and make regression
interpretation ambiguous.

## Verification

- Analytic unit tests cover geometric blends, zeros, duplicate coalescing,
  weight-scale invariance, and one-effective-profile behavior.
- Permuting the selected references produces the same profile identity and
  equivalent output.
- Single-reference compatibility continues through the unchanged upstream
  adapter.
- Real two-reference renders reopen as finite stereo in every supported output
  mode, including paired previews.
- Manifests inventory every requested source and explain the effective blend.
- More than 32 references and every unsupported native option fail before DSP.

## Implementation note

The experimental `music-mastering-tools-native` engine now implements this
decision for one through 32 requested references, including limited,
normalized, raw FLOAT, and paired-preview branches. The interactive workbench
application layer, exposed through the GUI-first local portal and terminal
fallback, selects this engine for two or more references and retains the
unchanged upstream adapter for the normal one-reference compatibility
workflow.

This delivered slice does not imply completion of separate planned controls
such as partial match amount, EBU R128, true peak, dither, metadata copying,
external limiting, profile caching, or public plan/profile serialization.
