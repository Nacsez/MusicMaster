# ADR-0002: Characterize before DSP refactoring

- Status: Accepted
- Date: 2026-07-27
- Decision owners: DSP and quality maintainers
- Requirements: TEST-001, TEST-002, ROB-005, GOV-002

## Context

Matchering 2.0.6 contains a compact but numerically coupled whole-file DSP
pipeline and no discovered automated test suite. Dependency versions have only
lower bounds. Refactoring stage boundaries, changing array layout, updating
SciPy/NumPy, or adding guardrails can change output subtly even when code looks
equivalent.

The baseline also has known undesirable behavior, including failures around
silence and fractional preview values. "Preserve everything" is therefore as
wrong as "rewrite freely": desired parity and intentional divergence must be
distinguished.

## Decision

Before changing an upstream DSP behavior, we will create executable
characterization evidence for it:

1. generate or identify a legally usable fixture;
2. record exact source, dependency, configuration, and environment identity;
3. run the upstream public path;
4. independently measure outputs and record logs/failure;
5. classify the behavior as desired parity, tolerated numerical variance,
   known defect, or intentional change candidate;
6. set reviewed tolerances and preserve the baseline record; and
7. add the private-engine acceptance/regression test before or with the change.

Known upstream defects remain as expected-failure characterization cases while
the private implementation test requires the corrected policy result.

## Consequences

### Positive

- Sonic drift becomes visible and reviewable.
- Refactors can be separated from intentional algorithm changes.
- Dependency updates produce measurable reports.
- Edge-case fixes remain durable.
- Performance optimization has a correctness reference.

### Costs and constraints

- Initial feature delivery waits for fixture and harness work.
- Golden data needs curation, review, and storage policy.
- Numerical tolerances require judgment and may vary by supported environment.
- Listening review is still needed for intentional sonic changes.

## Alternatives considered

### Unit-test only the rewritten implementation

Rejected. It proves internal consistency but not whether useful baseline
behavior was lost.

### Require byte-identical audio everywhere

Rejected as the only oracle. Scientific and codec dependencies may create
innocent low-level differences. Exact hashes are used where proven; otherwise
versioned numerical/perceptual tolerances apply.

### Use only commercial songs and listening

Rejected. Rights, reproducibility, runtime, and localization make this
insufficient. A generated corpus covers mechanics; private music supplements
it for listening.

### Fix known bugs first, then capture behavior

Rejected. It loses the evidence that identifies the original failure and risks
changing neighboring valid behavior unnoticed.

## Verification

- Each DSP change cites a characterization case and requirement ID.
- Phase 1 matrix in the [test strategy](../quality/test-strategy.md) is covered.
- Baseline updates include automated deltas and review rationale.
- Dependency-lock changes run the full corpus.
- Expected upstream defects and corrected private behavior both remain visible.

## Revisit triggers

- upstream code is fully retired and all supported behavior has private
  regression ownership;
- a new DSP engine becomes a deliberate alternative rather than parity path; or
- supported platforms make existing tolerances invalid.
