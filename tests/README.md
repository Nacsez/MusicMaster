# Test strategy

This directory is the executable quality contract for the private mastering
workbench. The test modules cover configuration, audio preflight, engine
capability boundaries, structured events, manifests, doctor/CLI contracts,
audit fault recovery, catalog/application/localhost-portal contracts, and real
Matchering compatibility renders.

## Tiers

| Tier | Purpose | Normal dependencies | Expected cadence |
| --- | --- | --- | --- |
| `unit` | Pure validation, planning, measurement, and configuration logic | No audio files, network, or subprocesses | Every change |
| `integration` | File adapters, Matchering calls, manifests, and output handling | Generated WAV fixtures and installed DSP dependencies | Every change |
| `regression` | Golden measurements, error codes, edge cases, and tolerance-bounded audio behavior | Versioned expectations plus generated fixtures | Every change and dependency update |
| `smoke` | The shortest complete target + reference + result workflow | Installed package and a writable artifact directory | Every change and deployment |
| `slow` | Long tracks, matrices, performance, and resource characterization | Larger local/generated inputs | Scheduled or explicit |

Tests should carry one primary tier marker. A slow integration test may also
carry `slow` so the default feedback loop can exclude it.

## Commands

From PowerShell at the repository root:

```powershell
.\scripts\Bootstrap.ps1
.\scripts\Bootstrap.ps1 -InstallDependencies
.\scripts\Invoke-Quality.ps1
.\scripts\Invoke-Tests.ps1 -Tier Unit
.\scripts\Invoke-Tests.ps1 -Tier All -Coverage
.\scripts\Invoke-SmokeTest.ps1
.\scripts\Invoke-PortalSmoke.ps1
.\.venv\Scripts\python.exe -m build --wheel
.\.venv\Scripts\python.exe -m build --wheel --no-isolation
```

`Bootstrap.ps1` creates or reuses `.venv` without deleting it. Dependency
installation is explicit because it may require network or a prepared
wheelhouse. Use `-Offline -Wheelhouse <workspace-path>` for a local-only
install.

The test runner writes detailed pytest logs and JUnit XML under
`artifacts/tests/`; coverage XML goes under `artifacts/coverage/`. These are
ignored runtime records, not source. The headless browser smoke writes its
token-redacted DOM/process/browser logs and screenshot under
`artifacts/portal-gui-smoke/`.

## Current behavior contract

The current suite verifies:

1. strict configuration round trips, path resolution, reference weighting, and
   rejection of unknown fields;
2. PCM WAV measurement with no optional dependencies;
3. silence, mono, output/input collision, and configurable policy handling;
4. truthful implemented-versus-planned engine capability rejection;
5. per-job event ordering and atomic, fingerprinted manifests;
6. failure manifests and audit-path data-loss prevention;
7. CLI capability and profile validation;
8. the complete preflight/fingerprint/event/manifest lifecycle;
9. required JSONL failure behavior and compensating close/commit-failure
   journal records;
10. real limited PCM-24, normalized PCM-24, raw FLOAT, paired-preview, and
    mono-to-stereo renders;
11. post-render reopen checks for finite samples, rate, channels, frames,
    preview length, and observed subtype;
12. independently weighted multi-reference analysis/render parity, stable
    order, and duplicate-content behavior;
13. content-addressed catalog, strict exchange, named-set, recovery, and
    run/artifact indexing behavior;
14. GUI-independent application serialization and failure/event projection;
15. local-only no-Electron asset, token/Host/origin/CSP/body/access-log,
    fixed-script native-dialog, CLI-dispatch, and double-click launcher
    contracts; and
16. headless Chrome portal bootstrap, stable application-ready/panel state,
    authenticated clean shutdown, and token-redacted retained text artifacts.

The 2026-07-27 Windows/Python 3.11 gate collected 206 tests: 205 passed, the
case-distinct-path test skipped on the case-insensitive filesystem, and
aggregate branch coverage was 88.79% against an 85% gate. The remaining
characterization backlog includes hard-limiter detection parity,
analyze-plan-render equivalence, transactional audio publication, and golden
RMS/peak/spectral/duration/channel tolerances.
`Invoke-SmokeTest.ps1 -FixturesOnly` validates deterministic fixture
production.

[`Invoke-PortalSmoke.ps1`](../scripts/Invoke-PortalSmoke.ps1) finds an installed
Chrome/Edge browser (or accepts `-BrowserPath`), creates an isolated workspace,
renders the packaged GUI at 1440 × 1000 by default, captures a screenshot and
redacted DOM, and requires orderly API shutdown. It defaults to `master-job`;
use `-InitialTab` for another supported workspace/utility panel and
`-ViewportWidth`/`-ViewportHeight` for a responsive-layout smoke. It does not
exercise native WinForms pickers, an assistive-technology session, or a
GUI-started mastering render.

Both standard isolated wheel construction and
`python -m build --wheel --no-isolation` pass. The built wheel has been checked
for all three packaged frontend assets: `index.html`, `app.css`, and `app.js`.
