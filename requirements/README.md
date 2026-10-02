# Dependency baselines

The project metadata declares `matchering==2.0.6` as the upstream runtime
baseline. The files in this directory make developer installs explicit:

- `base.txt` installs the runtime baseline.
- `dev.txt` adds tests, coverage, linting, typing, and build verification.
- `constraints.txt` prevents unreviewed major-version jumps in Matchering's
  scientific Python stack.

The constraints are compatibility guardrails, not a universal lock file.
Binary scientific packages resolve differently by Python version, operating
system, and CPU architecture. After an installation succeeds,
`scripts/Bootstrap.ps1 -InstallDependencies` writes a timestamped
`pip freeze --all` record to `artifacts/environment/`. Those records are local
artifacts until the team deliberately promotes a platform-specific lock.

No publishing index, token, or upload workflow is configured. The project is
private during this phase.
